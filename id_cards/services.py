"""
id_cards/services.py

Service layer for the ID card lifecycle.

All complicated operations (photo workflow, card generation, batching, PDF/QR
preparation, replacement, verification, expiry, readiness reporting) live here
instead of inside the views.  Views stay thin.

Authoritative sources (never duplicated here):
  * accounts.EduProUser                 — student identity
  * academics.StudentProfile            — program / student number
  * academics.Institution/Faculty/Department/Program
"""

import datetime
import logging
import re

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import StaffResponsibility
from academics.models import Department, Institution, StudentProfile

from .models import (
    AuditAction,
    IDCard,
    IDCardAuditLog,
    IDCardBatch,
    IDCardSettings,
    IDCardTemplate,
    PassportPhoto,
    PhotoStatus,
    CardStatus,
    BatchStatus,
    ReplacementRequest,
    ReplacementStatus,
    generate_verification_token,
)

logger = logging.getLogger("eduPro.id_cards")

User = get_user_model()

# Values used only internally by the service layer — never rendered on a card.
SYSTEM_ACTOR_HINT = "system"


# ─────────────────────────────────────────────────────────────────────────────
# PREFLIGHT CODES
# ─────────────────────────────────────────────────────────────────────────────

PREFLIGHT_READY                = "ready"
PREFLIGHT_PHOTO_MISSING        = "photo_missing"
PREFLIGHT_PHOTO_PENDING        = "photo_pending"
PREFLIGHT_PHOTO_REJECTED       = "photo_rejected"
PREFLIGHT_MISSING_DATA         = "missing_student_data"
PREFLIGHT_NO_TEMPLATE          = "no_template"
PREFLIGHT_EXISTING_ACTIVE_CARD = "existing_active_card"
PREFLIGHT_INVALID_STATE        = "invalid_state"
PREFLIGHT_OTHER_ERROR          = "other_error"

PREFLIGHT_LABELS = {
    PREFLIGHT_READY:                "READY",
    PREFLIGHT_PHOTO_MISSING:        "PHOTO MISSING",
    PREFLIGHT_PHOTO_PENDING:        "PHOTO PENDING",
    PREFLIGHT_PHOTO_REJECTED:       "PHOTO REJECTED",
    PREFLIGHT_MISSING_DATA:         "MISSING STUDENT DATA",
    PREFLIGHT_NO_TEMPLATE:          "NO TEMPLATE",
    PREFLIGHT_EXISTING_ACTIVE_CARD: "EXISTING ACTIVE CARD",
    PREFLIGHT_INVALID_STATE:        "INVALID STATE",
    PREFLIGHT_OTHER_ERROR:          "OTHER ERROR",
}


class CardGenerationError(Exception):
    def __init__(self, code, message, reasons=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reasons = reasons or []


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def student_institution(student):
    """Resolve the institution a student belongs to (via profile → programme)."""
    try:
        profile = student.academic_profile
    except StudentProfile.DoesNotExist:
        return None
    if not profile or not profile.program:
        return None
    return profile.program.department.institution


def scoped_students(user, institution=None):
    """
    QuerySet of students the given user may manage for ID cards.

    * Admin / ID-card officer  → every student in (optionally) an institution.
    * HOD                     → students of their own department(s) only.
    """
    qs = User.objects.filter(role="student")
    if institution is not None:
        qs = qs.filter(
            academic_profile__program__department__institution=institution,
        )
    if user.is_admin or user.has_responsibility(StaffResponsibility.ID_CARD_OFFICER):
        return qs
    if user.is_hod:
        depts = user.get_hod_departments()
        return qs.filter(academic_profile__program__department__in=depts)
    return qs.none()


def is_id_card_admin(user):
    """Templates / settings are reserved for admins and ID-card officers."""
    if not user or user.is_anonymous:
        return False
    if user.is_admin:
        return True
    return user.has_responsibility(StaffResponsibility.ID_CARD_OFFICER)


def is_id_card_staff(user):
    """Admin, ID-card officer, or (scoped) HOD."""
    if not user or user.is_anonymous:
        return False
    if user.is_admin or user.has_responsibility(StaffResponsibility.ID_CARD_OFFICER):
        return True
    return user.is_hod


def card_fields_for(student):
    """Read-only snapshot of the stable identity used on a card (no level!)."""
    try:
        profile = student.academic_profile
    except StudentProfile.DoesNotExist:
        profile = None
    program = profile.program if profile else None
    department = program.department if program else None
    faculty = department.faculty if department else None
    return {
        "full_name": student.get_full_name(),
        "student_number": (profile.student_number if profile else "") or "",
        "programme": program.name if program else "",
        "programme_code": program.code if program else "",
        "department": department.name if department else "",
        "department_code": department.code if department else "",
        "faculty": faculty.name if faculty else "",
    }


def verification_url(token, request=None):
    """Absolute verification URL for the given token (used inside QR codes)."""
    from django.urls import reverse
    path = reverse("id_cards:verify_card", args=[token])
    if request is not None:
        return request.build_absolute_uri(path)
    from django.conf import settings as dj_settings
    base = getattr(dj_settings, "SITE_URL", "").rstrip("/")
    if base:
        return f"{base}{path}"
    return path


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT
# ─────────────────────────────────────────────────────────────────────────────

def log(actor, action, *, card=None, batch=None, template=None, photo=None,
        description=""):
    """Append an entry to the ID card audit trail (append-only)."""
    return IDCardAuditLog.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        action=action,
        card=card,
        batch=batch,
        template=template,
        photo=photo,
        description=description,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CARD NUMBER & TOKEN GENERATION
# ─────────────────────────────────────────────────────────────────────────────

_CARD_PATTERN = re.compile(r"^(?P<prefix>.+)-(?P<year>\d{4})-(?P<seq>\d+)$")


def _next_card_number(settings_obj):
    """
    Produce the next unique card number, e.g. ``CARD-2026-000001``.

    Uniqueness is additionally enforced by the database unique constraint; on
    a race the caller retries.
    """
    year = timezone.now().year
    prefix = (settings_obj.card_number_prefix or "CARD").strip()
    max_seq = 0
    pattern = re.compile(re.escape(f"{prefix}-{year}-") + r"(\d+)$")
    for card_number in (
        IDCard.objects.filter(card_number__startswith=f"{prefix}-{year}-")
        .values_list("card_number", flat=True)
    ):
        match = pattern.search(card_number)
        if match:
            max_seq = max(max_seq, int(match.group(1)))
    return f"{prefix}-{year}-{max_seq + 1:06d}"


def _new_token(settings_obj):
    length = settings_obj.verification_token_length
    return generate_verification_token(length)


# ─────────────────────────────────────────────────────────────────────────────
# PHOTO WORKFLOW
# ─────────────────────────────────────────────────────────────────────────────

def upload_photo(student, uploaded_file, actor=None):
    """
    Validate, normalize, store and (if approval is disabled) auto-approve a
    passport photograph.  Returns the PassportPhoto record.

    Only one current photo exists per student (OneToOne); a re-upload
    overwrites the same row and resets it to a fresh approval cycle.  The
    audit trail records every upload.
    """
    from .validators import normalize_photo, PhotoValidationError

    try:
        buffer = normalize_photo(uploaded_file)
    except PhotoValidationError as exc:
        logger.warning("Photo rejected for %s: %s", student.email, str(exc))
        raise

    from django.core.files.base import ContentFile

    with transaction.atomic():
        # One current photo per student (OneToOne).  Re-upload overwrites the
        # same row — cards that reference it keep their FK, and the audit log
        # records the replacement of the image bytes.
        name = f"passport_{student.pk}_{timezone.now().strftime('%Y%m%d%H%M%S')}.jpg"
        photo, created = PassportPhoto.objects.get_or_create(
            student=student,
            defaults={"status": PhotoStatus.PENDING_APPROVAL},
        )
        photo.image.save(name, ContentFile(buffer.getvalue()), save=False)
        photo.status = PhotoStatus.PENDING_APPROVAL
        photo.submitted_at = timezone.now()
        photo.reviewed_by = None
        photo.reviewed_at = None
        photo.rejection_reason = ""
        photo.save(update_fields=[
            "image", "status", "submitted_at",
            "reviewed_by", "reviewed_at", "rejection_reason", "updated_at",
        ])

    settings_obj = IDCardSettings.get_for(student_institution(student) or _default_institution())
    if not settings_obj.require_photo_approval:
        approve_photo(photo, actor or student)

    log(actor or student, AuditAction.PHOTO_UPLOADED, photo=photo,
        description=f"Passport photo uploaded by {actor.get_full_name() if actor else 'student'}.")
    return photo


def _default_institution():
    return Institution.objects.first()


def approve_photo(photo, actor):
    """Mark the photo as approved for printing."""
    if photo.status == PhotoStatus.APPROVED:
        return photo
    photo.status = PhotoStatus.APPROVED
    photo.reviewed_by = actor
    photo.reviewed_at = timezone.now()
    photo.rejection_reason = ""
    photo.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at",
    ])
    log(actor, AuditAction.PHOTO_APPROVED, photo=photo,
        description=f"Photo approved by {actor.get_full_name() if actor else 'System'}.")
    return photo


def reject_photo(photo, actor, reason):
    """Reject a photo with a mandatory reason the student can action."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A rejection reason is required.")
    photo.status = PhotoStatus.REJECTED
    photo.reviewed_by = actor
    photo.reviewed_at = timezone.now()
    photo.rejection_reason = reason
    photo.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at",
    ])
    log(actor, AuditAction.PHOTO_REJECTED, photo=photo,
        description=f"Photo rejected by {actor.get_full_name() if actor else 'System'}: {reason}")
    return photo


# ─────────────────────────────────────────────────────────────────────────────
# PREFLIGHT
# ─────────────────────────────────────────────────────────────────────────────

def preflight(student, *, template=None, institution=None):
    """
    Validate that a student is eligible for a card right now.

    Returns ``(code, [reasons])``.  Only ``PREFLIGHT_READY`` may proceed.

    Note: academic level is deliberately NOT part of any check — level changes
    never affect card eligibility.
    """
    reasons = []
    institution = institution or student_institution(student)

    if institution is None:
        return PREFLIGHT_MISSING_DATA, reasons + [
            "Student is not attached to an institution (no programme/department).",
        ]

    try:
        profile = student.academic_profile
    except StudentProfile.DoesNotExist:
        profile = None

    if profile is None or not profile.program:
        return PREFLIGHT_MISSING_DATA, reasons + [
            "No academic profile / programme assigned.",
        ]
    if not profile.student_number:
        return PREFLIGHT_MISSING_DATA, reasons + ["Student ID number is missing."]
    if not profile.is_active:
        return PREFLIGHT_INVALID_STATE, reasons + [
            "Student profile is inactive.",
        ]

    photo = getattr(student, "id_photo", None)
    if photo is None:
        return PREFLIGHT_PHOTO_MISSING, reasons + [
            "No passport photo uploaded.",
        ]
    if photo.status == PhotoStatus.PENDING_APPROVAL:
        return PREFLIGHT_PHOTO_PENDING, reasons + [
            "Passport photo is pending approval.",
        ]
    if photo.status == PhotoStatus.REJECTED:
        return PREFLIGHT_PHOTO_REJECTED, reasons + [
            "Passport photo was rejected: " + (photo.rejection_reason or "no reason given."),
        ]

    if IDCard.objects.filter(
        student=student, status__in=CardStatus.LIVE_STATUSES,
    ).exists():
        return PREFLIGHT_EXISTING_ACTIVE_CARD, reasons + [
            "Student already has a live card.",
        ]

    tpl = template or IDCardTemplate.default_for(institution)
    if tpl is None:
        return PREFLIGHT_NO_TEMPLATE, reasons + [
            "No active card template configured for the institution.",
        ]

    return PREFLIGHT_READY, reasons


def is_ready(student, **kwargs):
    code, _ = preflight(student, **kwargs)
    return code == PREFLIGHT_READY


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE CARD GENERATION
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def generate_card_for_student(student, actor=None, *, template=None,
                              batch=None, expiry_date=None, replace_reason=""):
    """
    Generate one ID card for a student (inside a transaction).

    Raises :class:`CardGenerationError` when preflight fails.  Card numbers
    and verification tokens are unique; races retried with fresh values.
    """
    institution = student_institution(student)
    if institution is None:
        raise CardGenerationError(
            PREFLIGHT_MISSING_DATA, "Student has no institution.",
        )
    code, reasons = preflight(student, template=template, institution=institution)
    if code != PREFLIGHT_READY:
        raise CardGenerationError(code, PREFLIGHT_LABELS.get(code, code), reasons)

    tpl = template or IDCardTemplate.default_for(institution)
    if tpl is None:
        raise CardGenerationError(PREFLIGHT_NO_TEMPLATE, "No active template.", reasons)

    settings_obj = IDCardSettings.get_for(institution)
    photo = student.id_photo
    issue = timezone.localdate()
    if expiry_date is None:
        years = tpl.effective_validity_years
        expiry_date = issue + datetime.timedelta(days=365 * years)

    attempts = 0
    while attempts < 5:
        attempts += 1
        card_number = _next_card_number(settings_obj)
        token = _new_token(settings_obj)
        try:
            card = IDCard.objects.create(
                student=student,
                institution=institution,
                template=tpl,
                photo=photo,
                batch=batch,
                card_number=card_number,
                verification_token=token,
                status=CardStatus.GENERATED,
                issue_date=issue,
                expiry_date=expiry_date,
                generated_at=timezone.now(),
                replacement_reason=replace_reason,
            )
            log(actor, AuditAction.CARD_GENERATED, card=card, batch=batch,
                description=f"Card {card.card_number} generated for {student.get_full_name()}.")
            return card
        except IntegrityError:
            # Number/token collision or live-card race → retry with a new token;
            # a repeated race on the same student is handled by the caller.
            logger.warning("Generation collision (attempt %s) for %s",
                           attempts, student.email)

    raise CardGenerationError(
        PREFLIGHT_OTHER_ERROR, "Could not allocate a unique card number/token."
    )


# ─────────────────────────────────────────────────────────────────────────────
# BULK GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def generate_batch(students, actor=None, *, template=None, notes="", filters=None):
    """
    Generate cards for many students in one batch.

    One bad record never aborts the run — it is reported as a skip with its
    reason.  Returns the created IDCardBatch.
    """
    students = list(students)
    institution = None
    if students:
        institution = student_institution(students[0])

    generated = 0
    skipped = 0
    skips = {}

    with transaction.atomic():
        batch = IDCardBatch.objects.create(
            institution=institution or _default_institution(),
            batch_number=_next_batch_number(),
            generated_by=actor,
            generated_at=timezone.now(),
            status=BatchStatus.GENERATED,
            total_selected=len(students),
            generated_count=0,
            skipped_count=0,
            filters=filters or {},
        )

        for student in students:
            try:
                with transaction.atomic():
                    generate_card_for_student(
                        student, actor, template=template, batch=batch,
                    )
                generated += 1
            except CardGenerationError as exc:
                skipped += 1
                skips.setdefault(exc.code, {"label": PREFLIGHT_LABELS.get(exc.code, exc.code), "students": []})
                skips[exc.code]["students"].append(
                    f"{student.get_full_name()} <{getattr(student, 'email', '')}>"
                )
            except Exception:  # noqa: BLE001 — never kill a whole batch
                logger.exception("Unexpected error generating card for %s", student.email)
                skipped += 1
                code = PREFLIGHT_OTHER_ERROR
                skips.setdefault(code, {"label": PREFLIGHT_LABELS[code], "students": []})
                skips[code]["students"].append(
                    f"{student.get_full_name()} <{getattr(student, 'email', '')}>"
                )

        batch.generated_count = generated
        batch.skipped_count = skipped
        batch.skips = skips
        batch.save(update_fields=["generated_count", "skipped_count", "skips", "updated_at"])

    log(actor, AuditAction.BATCH_GENERATED, batch=batch,
        description=(
            f"Batch {batch.batch_number}: {generated} generated, {skipped} skipped."
        ))
    return batch


def _next_batch_number():
    year = timezone.now().year
    prefix = f"IDB-{year}-"
    max_seq = 0
    for bn in (
        IDCardBatch.objects.filter(batch_number__startswith=prefix)
        .values_list("batch_number", flat=True)
    ):
        match = re.search(r"(\d+)$", bn)
        if match:
            max_seq = max(max_seq, int(match.group(1)))
    return f"{prefix}{max_seq + 1:06d}"


# ─────────────────────────────────────────────────────────────────────────────
# PRINTING / ISSUANCE
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def mark_printed(cards, actor=None):
    """Mark cards as PRINTED."""
    cards = list(cards)
    for card in cards:
        if card.status not in CardStatus.LIVE_STATUSES:
            continue
        card.status = CardStatus.PRINTED
        card.printed_at = card.printed_at or timezone.now()
        card.printed_by = actor
        card.save(update_fields=["status", "printed_at", "printed_by", "updated_at"])
        log(actor, AuditAction.CARD_PRINTED, card=card,
            description=f"Card {card.card_number} marked printed.")
    batches = {c.batch_id for c in cards if c.batch_id}
    if batches:
        IDCardBatch.objects.filter(pk__in=batches).update(status=BatchStatus.PRINTED)
    return len(cards)


@transaction.atomic
def issue_card(card, actor=None, issued_by_override=None):
    """
    Activate a card: ISSUED → ACTIVE.  Live cards are (re)issued without
    invalidating the duplicate guard (only one ACTIVE card per student).
    """
    if not card.is_active_or_issued:
        raise CardGenerationError(
            PREFLIGHT_INVALID_STATE,
            f"Card {card.card_number} is {card.get_status_display()} and cannot be issued.",
        )
    card.status = CardStatus.ACTIVE
    card.issue_date = card.issue_date or timezone.localdate()
    card.issued_at = timezone.now()
    card.issued_by = actor
    card.save(update_fields=["status", "issue_date", "issued_at", "issued_by", "updated_at"])
    if card.batch_id:
        IDCardBatch.objects.filter(pk=card.batch_id).update(status="issued")
    log(actor, AuditAction.CARD_ISSUED, card=card,
        description=f"Card {card.card_number} issued to {card.student.get_full_name()}.")
    return card


@transaction.atomic
def cancel_card(card, actor=None, reason=""):
    if card.status == CardStatus.CANCELLED:
        return card
    card.status = CardStatus.CANCELLED
    card.cancel_reason = (reason or "").strip()
    card.save(update_fields=["status", "cancel_reason", "updated_at"])
    log(actor, AuditAction.CARD_CANCELLED, card=card,
        description=f"Card {card.card_number} cancelled. {card.cancel_reason}")
    return card


@transaction.atomic
def mark_lost(card, actor=None):
    card.status = CardStatus.LOST
    card.save(update_fields=["status", "updated_at"])
    log(actor, AuditAction.CARD_MARKED_LOST, card=card,
        description=f"Card {card.card_number} reported lost.")
    return card


@transaction.atomic
def mark_damaged(card, actor=None):
    card.status = CardStatus.DAMAGED
    card.save(update_fields=["status", "updated_at"])
    log(actor, AuditAction.CARD_MARKED_DAMAGED, card=card,
        description=f"Card {card.card_number} reported damaged.")
    return card


# ─────────────────────────────────────────────────────────────────────────────
# REPLACEMENT
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_replacement_request(student, card, reason, details="", actor=None):
    """Student/staff files a replacement request for the current card."""
    if card.student_id != student.pk:
        raise ValueError("Card does not belong to this student.")
    settings_obj = IDCardSettings.get_for(student_institution(student) or _default_institution())
    if not settings_obj.allow_student_replacement_requests and not is_id_card_staff(actor):
        raise CardGenerationError(
            PREFLIGHT_INVALID_STATE, "Replacement requests are not enabled.",
        )
    req = ReplacementRequest.objects.create(
        institution=card.institution,
        student=student,
        card=card,
        reason=reason,
        details=details,
        status=ReplacementStatus.PENDING,
        requested_by=actor,
        requested_at=timezone.now(),
    )
    log(actor or student, AuditAction.REPLACEMENT_REQUESTED, card=card,
        description=(
            f"Replacement requested ({req.get_reason_display()}) for "
            f"{card.card_number}."
        ))
    return req


@transaction.atomic
def approve_replacement_request(request_obj, actor=None):
    """
    Approve a replacement request: generate a new card and invalidate the old.
    The old card immediately fails QR verification.
    """
    if request_obj.status != ReplacementStatus.PENDING:
        raise CardGenerationError(
            PREFLIGHT_INVALID_STATE,
            f"Request is {request_obj.get_status_display()}, not pending.",
        )

    old = request_obj.card
    # Any previous live card of this student (incl. the one being replaced)
    # must already be non-live or will be replaced now.
    old.status = CardStatus.REPLACED
    old.save(update_fields=["status", "updated_at"])

    new = generate_card_for_student(
        request_obj.student,
        actor,
        replace_reason=request_obj.reason,
    )
    # Link both directions (database-safe because status guards allow it).
    old.replaced_by = new
    old.save(update_fields=["replaced_by", "updated_at"])

    request_obj.status = ReplacementStatus.COMPLETED
    request_obj.reviewed_by = actor
    request_obj.reviewed_at = timezone.now()
    request_obj.replacement_card = new
    request_obj.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "replacement_card", "updated_at",
    ])

    log(actor, AuditAction.CARD_REPLACED, card=new,
        description=f"Card {old.card_number} replaced by {new.card_number}.")
    log(actor, AuditAction.REPLACEMENT_APPROVED, card=new,
        description=f"Replacement request approved; new card {new.card_number}.")
    return new


@transaction.atomic
def reject_replacement_request(request_obj, actor=None, reason=""):
    if request_obj.status != ReplacementStatus.PENDING:
        raise CardGenerationError(
            PREFLIGHT_INVALID_STATE, "Only pending requests can be rejected.",
        )
    request_obj.status = ReplacementStatus.REJECTED
    request_obj.reviewed_by = actor
    request_obj.reviewed_at = timezone.now()
    request_obj.rejection_reason = (reason or "").strip()
    request_obj.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at",
    ])
    log(actor, AuditAction.REPLACEMENT_REJECTED, card=request_obj.card,
        description=f"Replacement rejected: {request_obj.rejection_reason}")
    return request_obj


def replace_card_direct(card, actor=None, reason="other", notes=""):
    """
    Staff-initiated replacement of a live card.  Returns the new card.
    The old card becomes REPLACED and fails QR verification immediately.
    """
    req = create_replacement_request(
        card.student, card, reason, details=notes, actor=actor,
    )
    return approve_replacement_request(req, actor)


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRY
# ─────────────────────────────────────────────────────────────────────────────

def process_expirations():
    """
    Move ACTIVE cards past their expiry date to EXPIRED.

    Returns the number of cards expired.  Automatic replacement only occurs
    when the institution enables auto-renewal.
    """
    today = timezone.localdate()
    expired_ids = IDCard.objects.filter(
        status=CardStatus.ACTIVE,
        expiry_date__isnull=False,
        expiry_date__lt=today,
    ).values_list("pk", flat=True)

    count = 0
    for pk in expired_ids:
        with transaction.atomic():
            card = IDCard.objects.select_for_update().get(pk=pk)
            if card.status != CardStatus.ACTIVE:
                continue
            card.status = CardStatus.EXPIRED
            card.save(update_fields=["status", "updated_at"])
            log(None, AuditAction.CARD_EXPIRED, card=card,
                description=f"Card {card.card_number} expired on {card.expiry_date}.")

            settings_obj = IDCardSettings.get_for(card.institution)
            if settings_obj.auto_renewal_enabled:
                try:
                    replace_card_direct(card, reason="expired",
                                        notes="Automatic renewal on expiry.")
                except CardGenerationError as exc:
                    logger.warning("Auto-renewal failed for %s: %s",
                                   card.card_number, exc)
        count += 1
    return count


# ─────────────────────────────────────────────────────────────────────────────
# VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class VerificationResult:
    def __init__(self, found=False, valid=False, reason="", payload=None, card=None):
        self.found = found
        self.valid = valid
        self.reason = reason
        self.payload = payload or {}
        self.card = card


def verify_token(token):
    """
    Public QR verification.

    * UNKNOWN token  → not found
    * REPLACED card  → invalid (replaced by a newer card)
    * EXPIRED card   → invalid
    * CANCELLED etc. → invalid
    * ACTIVE card within validity → valid
    Public payload is limited to stable identity fields (never private data).
    """
    if not token:
        return VerificationResult(found=False, reason="No verification code provided.")
    try:
        card = IDCard.objects.select_related(
            "student", "institution", "student__academic_profile",
            "student__academic_profile__program",
            "student__academic_profile__program__department",
        ).get(verification_token=token)
    except IDCard.DoesNotExist:
        return VerificationResult(found=False, reason="Card not found.")

    result = VerificationResult(found=True, card=card)
    if card.replaced_by_id:
        result.valid = False
        result.reason = "REPLACED"
    elif card.status != CardStatus.ACTIVE:
        result.valid = False
        result.reason = card.get_status_display().upper()
    elif card.expiry_date and card.expiry_date < timezone.localdate():
        result.valid = False
        result.reason = "EXPIRED"
    else:
        result.valid = True
        result.reason = "VALID"
    result.payload = card.verification_payload()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# READINESS / REPORTING
# ─────────────────────────────────────────────────────────────────────────────

def readiness_stats(students):
    """
    Compute READINESS statistics for a queryset of students.

    Returns a dict of counts (see the dashboard template).
    """
    students = list(students)
    student_ids = [s.pk for s in students] or [0]

    photo_rows = PassportPhoto.objects.filter(student_id__in=student_ids)
    photo_student_ids = set(photo_rows.values_list("student_id", flat=True))
    pending_ids = set(
        photo_rows.filter(status=PhotoStatus.PENDING_APPROVAL)
        .values_list("student_id", flat=True)
    )
    rejected_ids = set(
        photo_rows.filter(status=PhotoStatus.REJECTED)
        .values_list("student_id", flat=True)
    )
    approved_ids = set(
        photo_rows.filter(status=PhotoStatus.APPROVED)
        .values_list("student_id", flat=True)
    )

    live_card_ids = set(
        IDCard.objects.filter(
            student_id__in=student_ids, status__in=CardStatus.LIVE_STATUSES,
        ).values_list("student_id", flat=True)
    )
    printed_count = IDCard.objects.filter(
        student_id__in=student_ids, printed_at__isnull=False,
    ).count()
    issued_count = IDCard.objects.filter(
        student_id__in=student_ids, issued_at__isnull=False,
    ).count()
    expired_count = IDCard.objects.filter(
        student_id__in=student_ids, status=CardStatus.EXPIRED,
    ).count()
    lost_count = IDCard.objects.filter(
        student_id__in=student_ids, status=CardStatus.LOST,
    ).count()
    damaged_count = IDCard.objects.filter(
        student_id__in=student_ids, status=CardStatus.DAMAGED,
    ).count()

    profile_complete_ids = set(
        StudentProfile.objects.filter(
            student_id__in=student_ids, is_active=True,
            program__isnull=False,
        ).exclude(student_number="").values_list("student_id", flat=True)
    )

    ready_ids = (
        profile_complete_ids & approved_ids
    ) - live_card_ids
    printed_n = printed_count
    issued_n = issued_count

    missing_ids = set(students[i].pk for i in range(len(students))) - photo_student_ids
    replacement_required = (
        expired_count + lost_count + damaged_count
    )

    return {
        "total_students": len(students),
        "profiles_complete": len(profile_complete_ids),
        "photos_uploaded": len(photo_student_ids),
        "photos_pending": len(pending_ids),
        "photos_approved": len(approved_ids),
        "photos_rejected": len(rejected_ids),
        "photos_missing": len(missing_ids),
        "ready_for_generation": len(ready_ids),
        "already_generated": len(live_card_ids),
        "printed": printed_n,
        "issued": issued_n,
        "expired": expired_count,
        "replacement_required": replacement_required,
        "missing_ids": missing_ids,
        "ready_ids": ready_ids,
        "pending_ids": pending_ids,
        "rejected_ids": rejected_ids,
        "approved_ids": approved_ids,
        "live_card_ids": live_card_ids,
    }


def readiness_students(students, flag):
    """
    Return the students matching a readiness flag (for drill-down).
    ``students`` may be a QuerySet or list.
    """
    students = list(students)
    ids = [s.pk for s in students] or [0]
    qs = User.objects.filter(pk__in=ids)

    if flag == "photos_missing":
        has_photo = set(PassportPhoto.objects.filter(student_id__in=ids)
                        .values_list("student_id", flat=True))
        return qs.exclude(pk__in=has_photo)
    if flag == "photos_pending":
        ids2 = set(PassportPhoto.objects.filter(
            student_id__in=ids, status=PhotoStatus.PENDING_APPROVAL,
        ).values_list("student_id", flat=True))
        return qs.filter(pk__in=ids2)
    if flag == "photos_rejected":
        ids2 = set(PassportPhoto.objects.filter(
            student_id__in=ids, status=PhotoStatus.REJECTED,
        ).values_list("student_id", flat=True))
        return qs.filter(pk__in=ids2)
    if flag == "photos_approved":
        ids2 = set(PassportPhoto.objects.filter(
            student_id__in=ids, status=PhotoStatus.APPROVED,
        ).values_list("student_id", flat=True))
        return qs.filter(pk__in=ids2)
    if flag == "ready":
        ready_ids = readiness_stats(students)["ready_ids"]
        return qs.filter(pk__in=ready_ids)
    if flag == "generated":
        ids2 = set(IDCard.objects.filter(
            student_id__in=ids, status__in=CardStatus.LIVE_STATUSES,
        ).values_list("student_id", flat=True))
        return qs.filter(pk__in=ids2)
    if flag in ("expired", "replacement_required"):
        ids2 = set(IDCard.objects.filter(
            student_id__in=ids, status=CardStatus.EXPIRED,
        ).values_list("student_id", flat=True))
        ids3 = set(IDCard.objects.filter(
            student_id__in=ids, status__in=(CardStatus.LOST, CardStatus.DAMAGED),
        ).values_list("student_id", flat=True))
        return qs.filter(pk__in=ids2 | ids3)
    if flag == "printed":
        ids2 = set(IDCard.objects.filter(
            student_id__in=ids, printed_at__isnull=False,
        ).values_list("student_id", flat=True))
        return qs.filter(pk__in=ids2)
    if flag == "issued":
        ids2 = set(IDCard.objects.filter(
            student_id__in=ids, issued_at__isnull=False,
        ).values_list("student_id", flat=True))
        return qs.filter(pk__in=ids2)
    return qs.none()