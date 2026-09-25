"""
id_cards/models.py

Student ID Card lifecycle models for eduPro.

Design rules:
  * This module NEVER duplicates students, departments, programmes, faculties,
    institutions or academic sessions — everything is read from the existing
    authoritative models in ``academics`` / ``accounts``.
  * The physical card intentionally carries NO academic level.  A level change
    must never invalidate or regenerate a card.
  * Only card-lifecycle data lives here: photos, templates, cards, batches,
    replacement requests and the ID-card audit trail.
"""

import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ─────────────────────────────────────────────────────────────────────────────
# CHOICES
# ─────────────────────────────────────────────────────────────────────────────

class PhotoStatus(models.TextChoices):
    PENDING_APPROVAL = "pending",   _("Pending approval")
    APPROVED         = "approved",  _("Approved")
    REJECTED         = "rejected",  _("Rejected")


class TemplateStatus(models.TextChoices):
    DRAFT    = "draft",     _("Draft")
    ACTIVE   = "active",    _("Active")
    ARCHIVED = "archived",  _("Archived")


class CardStatus(models.TextChoices):
    DRAFT           = "draft",            _("Draft")
    PHOTO_REQUIRED  = "photo_required",   _("Photo required")
    PHOTO_PENDING   = "photo_pending",    _("Photo pending")
    READY           = "ready",            _("Ready")
    GENERATED       = "generated",        _("Generated")
    PRINTED         = "printed",          _("Printed")
    ISSUED          = "issued",           _("Issued")
    ACTIVE          = "active",           _("Active")
    EXPIRED         = "expired",          _("Expired")
    LOST            = "lost",             _("Lost")
    DAMAGED         = "damaged",          _("Damaged")
    REPLACED        = "replaced",         _("Replaced")
    CANCELLED       = "cancelled",        _("Cancelled")


# Status sets are assigned AFTER the enum: TextChoices would otherwise treat a
# tuple attribute inside the class body as an (value, label) enum member.
CardStatus.LIVE_STATUSES = (
    CardStatus.READY, CardStatus.GENERATED, CardStatus.PRINTED,
    CardStatus.ISSUED, CardStatus.ACTIVE,
)
CardStatus.ISSUABLE = (
    CardStatus.GENERATED, CardStatus.PRINTED, CardStatus.ISSUED, CardStatus.ACTIVE,
)


class BatchStatus(models.TextChoices):
    GENERATED = "generated",  _("Generated")
    PRINTED   = "printed",    _("Printed")
    ISSUED    = "issued",     _("Issued")
    ARCHIVED  = "archived",   _("Archived")


class ReplacementReason(models.TextChoices):
    LOST            = "lost",              _("Lost")
    DAMAGED         = "damaged",           _("Damaged")
    EXPIRED         = "expired",           _("Expired")
    NAME_CHANGE     = "name_change",       _("Name change")
    STUDENT_ID_CHANGE = "student_id_change", _("Student ID change")
    PROGRAMME_CHANGE  = "programme_change",  _("Programme / department change")
    SECURITY         = "security",         _("Security issue")
    OTHER            = "other",            _("Other")


class ReplacementStatus(models.TextChoices):
    PENDING   = "pending",   _("Pending")
    APPROVED  = "approved",  _("Approved")
    REJECTED  = "rejected",  _("Rejected")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class AuditAction(models.TextChoices):
    PHOTO_UPLOADED      = "photo_uploaded",      _("Photo uploaded")
    PHOTO_APPROVED      = "photo_approved",      _("Photo approved")
    PHOTO_REJECTED      = "photo_rejected",      _("Photo rejected")
    CARD_GENERATED      = "card_generated",      _("Card generated")
    CARD_PRINTED        = "card_printed",        _("Card printed")
    CARD_ISSUED         = "card_issued",         _("Card issued")
    CARD_REPLACED       = "card_replaced",       _("Card replaced")
    CARD_MARKED_LOST    = "card_marked_lost",    _("Card marked lost")
    CARD_MARKED_DAMAGED = "card_marked_damaged", _("Card marked damaged")
    CARD_CANCELLED      = "card_cancelled",      _("Card cancelled")
    CARD_EXPIRED        = "card_expired",        _("Card expired")
    CARD_VERIFIED       = "card_verified",       _("Card verified (public)")
    TEMPLATE_CHANGED    = "template_changed",    _("Template changed")
    TEMPLATE_CREATED    = "template_created",    _("Template created")
    TEMPLATE_ACTIVATED  = "template_activated",  _("Template activated")
    TEMPLATE_ARCHIVED   = "template_archived",   _("Template archived")
    TEMPLATE_DUPLICATED = "template_duplicated", _("Template duplicated")
    BATCH_GENERATED     = "batch_generated",     _("Batch generated")
    BATCH_DOWNLOADED    = "batch_downloaded",    _("Batch downloaded")
    BATCH_ARCHIVED      = "batch_archived",      _("Batch archived")
    REPLACEMENT_REQUESTED = "replacement_requested", _("Replacement requested")
    REPLACEMENT_APPROVED  = "replacement_approved",  _("Replacement approved")
    REPLACEMENT_REJECTED  = "replacement_rejected",  _("Replacement rejected")
    SETTINGS_UPDATED      = "settings_updated",      _("Settings updated")


# ─────────────────────────────────────────────────────────────────────────────
# MIXINS
# ─────────────────────────────────────────────────────────────────────────────

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


# ─────────────────────────────────────────────────────────────────────────────
# FRONT / BACK SUPPORTED CARD FIELDS
# ─────────────────────────────────────────────────────────────────────────────

FRONT_FIELD_CHOICES = [
    ("institution_logo",      _("Institution logo")),
    ("institution_name",      _("Institution name")),
    ("institution_short_name",_("Institution short name")),
    ("photo",                 _("Passport photograph")),
    ("full_name",             _("Student full name")),
    ("student_number",        _("Student ID / index number")),
    ("programme",             _("Programme")),
    ("department",            _("Department")),
    ("faculty",               _("Faculty")),
    ("card_number",           _("Card number")),
    ("issue_date",            _("Issue date")),
    ("expiry_date",           _("Expiry date")),
    ("qr",                    _("QR verification code")),
    ("custom_text",           _("Institution-defined text")),
    ("authorized_signature",  _("Authorized signature")),
]

BACK_FIELD_CHOICES = [
    ("watermark",             _("Institution watermark")),
    ("address",               _("Institution address")),
    ("telephone",             _("Telephone")),
    ("email",                 _("Email")),
    ("website",               _("Website")),
    ("return_instructions",   _("If found, please return to...")),
    ("student_signature",     _("Student signature area")),
    ("authorized_signature",  _("Authorized signature")),
    ("terms",                 _("Card terms and conditions")),
    ("qr",                    _("QR verification code")),
    ("custom_text",           _("Institution-defined text")),
]

DEFAULT_FRONT_FIELDS = [
    "institution_logo",
    "institution_name",
    "photo",
    "full_name",
    "student_number",
    "programme",
    "department",
    "card_number",
    "issue_date",
    "expiry_date",
    "qr",
]

DEFAULT_BACK_FIELDS = [
    "watermark",
    "address",
    "return_instructions",
    "email",
    "website",
    "student_signature",
    "authorized_signature",
    "terms",
    "qr",
]

TEMPLATE_DEFAULT_TERMS = (
    "This card remains the property of the institution. "
    "It is not transferable. Report loss or damage immediately. "
    "This card is invalid once expired, cancelled or replaced."
)

TEMPLATE_DEFAULT_RETURN = "If found, please return to the institution registry or any campus security office."


# ─────────────────────────────────────────────────────────────────────────────
# PASSPORT PHOTO
# ─────────────────────────────────────────────────────────────────────────────

class PassportPhoto(TimeStampedModel):
    """
    One required passport photograph per student.

    A student with no ``PassportPhoto`` row is treated as NOT_UPLOADED; the
    row is created on first upload with ``PENDING_APPROVAL``.

    Exactly one current photo exists per student (OneToOne); a re-upload
    overwrites the same row and restarts the approval cycle.  The card already
    generated keeps its ``IDCard.photo`` reference, and every change is
    recorded in the ID-card audit trail.
    """

    student = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="id_photo", verbose_name=_("student"),
        limit_choices_to={"role": "student"},
    )
    image = models.ImageField(
        _("passport photograph"),
        upload_to="id_cards/photos/%Y/%m/",
        help_text=_("Recent passport photograph (JPG/PNG)."),
    )
    status = models.CharField(
        _("status"), max_length=12,
        choices=PhotoStatus.choices, default=PhotoStatus.PENDING_APPROVAL,
        db_index=True,
    )
    submitted_at = models.DateTimeField(_("submitted at"), default=timezone.now)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="id_photo_reviews",
        verbose_name=_("reviewed by"),
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    rejection_reason = models.TextField(_("rejection reason"), blank=True)

    class Meta:
        verbose_name = _("passport photo")
        verbose_name_plural = _("passport photos")
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"Photo — {self.student.get_full_name()} ({self.get_status_display()})"

    @property
    def is_approved(self):
        return self.status == PhotoStatus.APPROVED

    @property
    def is_pending(self):
        return self.status == PhotoStatus.PENDING_APPROVAL

    @property
    def is_rejected(self):
        return self.status == PhotoStatus.REJECTED


# ─────────────────────────────────────────────────────────────────────────────
# ID CARD SETTINGS  (per institution)
# ─────────────────────────────────────────────────────────────────────────────

class IDCardSettings(TimeStampedModel):
    """
    Institution-scoped ID card policy.

    Consumed by the service layer; nothing here duplicates student data.
    """

    institution = models.OneToOneField(
        "academics.Institution", on_delete=models.CASCADE,
        related_name="id_card_settings", verbose_name=_("institution"),
    )
    validity_years = models.PositiveSmallIntegerField(
        _("card validity (years)"), default=4,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    card_number_prefix = models.CharField(
        _("card number prefix"), max_length=12, default="CARD",
        help_text=_("e.g. CARD → CARD-2026-000001"),
    )
    verification_token_length = models.PositiveSmallIntegerField(
        _("verification token length"), default=32,
        validators=[MinValueValidator(16), MaxValueValidator(64)],
    )
    auto_renewal_enabled = models.BooleanField(
        _("automatic renewal on expiry"), default=False,
        help_text=_("When enabled, an expired card may be auto-replaced."),
    )
    require_photo_approval = models.BooleanField(
        _("photo approval required"), default=True,
        help_text=_("When unchecked, uploaded photos are treated as approved."),
    )
    allow_student_replacement_requests = models.BooleanField(
        _("students may request replacement"), default=True,
    )
    verification_requires_login = models.BooleanField(
        _("verification requires login"), default=False,
        help_text=_("Public QR verification is open unless this is enabled."),
    )

    class Meta:
        verbose_name = _("ID card setting")
        verbose_name_plural = _("ID card settings")

    def __str__(self):
        return f"ID card settings — {self.institution}"

    @classmethod
    def get_for(cls, institution):
        obj, _ = cls.objects.get_or_create(institution=institution)
        return obj


# ─────────────────────────────────────────────────────────────────────────────
# ID CARD TEMPLATE
# ─────────────────────────────────────────────────────────────────────────────

class IDCardTemplate(TimeStampedModel):
    """
    Configurable front/back card design scoped to an institution.

    Layout is intentionally data-driven: the institution picks which stable
    fields appear on the front and back from the supported field catalogues.
    Archive (never delete) templates still referenced by historical cards.
    """

    institution = models.ForeignKey(
        "academics.Institution", on_delete=models.CASCADE,
        related_name="id_card_templates", verbose_name=_("institution"),
    )
    name = models.CharField(_("name"), max_length=120)
    status = models.CharField(
        _("status"), max_length=12,
        choices=TemplateStatus.choices, default=TemplateStatus.DRAFT,
        db_index=True,
    )
    is_default = models.BooleanField(
        _("default template"), default=False,
        help_text=_("Used for generation when no template is chosen explicitly."),
    )

    # ── Physical dimensions (millimetres) ───────────────────────────────────
    card_width_mm = models.DecimalField(
        _("card width (mm)"), max_digits=5, decimal_places=2, default=85.60,
    )
    card_height_mm = models.DecimalField(
        _("card height (mm)"), max_digits=5, decimal_places=2, default=53.98,
    )

    # ── Colours & imagery ───────────────────────────────────────────────────
    primary_color = models.CharField(
        _("primary colour"), max_length=7, default="#1d4ed8",
        help_text=_("Hex colour, e.g. #1d4ed8."),
    )
    secondary_color = models.CharField(
        _("secondary colour"), max_length=7, default="#0f172a",
    )
    text_color = models.CharField(_("text colour"), max_length=7, default="#ffffff")
    background = models.ImageField(
        _("card background image"), upload_to="id_cards/templates/backgrounds/",
        blank=True, null=True,
    )
    watermark = models.ImageField(
        _("watermark image"), upload_to="id_cards/templates/watermarks/",
        blank=True, null=True,
    )
    logo = models.ImageField(
        _("logo override"), upload_to="id_cards/templates/logos/",
        blank=True, null=True,
        help_text=_("Leave blank to use the institution logo."),
    )

    # ── Content configuration ───────────────────────────────────────────────
    front_fields = models.JSONField(_("front fields"), default=list)
    back_fields = models.JSONField(_("back fields"), default=list)
    custom_front_text = models.TextField(_("front institution-defined text"), blank=True)
    custom_back_text = models.TextField(_("back institution-defined text"), blank=True)
    return_instructions = models.TextField(_("return instructions"), blank=True)
    card_terms = models.TextField(_("card terms"), blank=True)
    authorized_signature_name = models.CharField(
        _("authorized signature name"), max_length=160, blank=True,
        help_text=_("Title / name printed under the authorized signature."),
    )
    show_signature_line = models.BooleanField(_("show signature line"), default=True)

    # ── Validity display ────────────────────────────────────────────────────
    validity_years = models.PositiveSmallIntegerField(
        _("validity (years)"), default=4,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text=_("Leave 0 to fall back to institution settings."),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="id_card_templates_created",
        verbose_name=_("created by"),
    )
    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True)

    class Meta:
        verbose_name = _("ID card template")
        verbose_name_plural = _("ID card templates")
        ordering = ["institution__name", "name"]

    def __str__(self):
        return f"{self.name} — {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if not self.front_fields:
            self.front_fields = DEFAULT_FRONT_FIELDS
        if not self.back_fields:
            self.back_fields = DEFAULT_BACK_FIELDS
        if self.is_default:
            IDCardTemplate.objects.filter(
                institution=self.institution,
            ).exclude(pk=self.pk).update(is_default=False)
        if self.status == TemplateStatus.ARCHIVED:
            self.is_default = False
        super().save(*args, **kwargs)

    @property
    def effective_validity_years(self):
        if self.validity_years:
            return self.validity_years
        return IDCardSettings.get_for(self.institution).validity_years

    def logo_url(self):
        if self.logo:
            return self.logo
        return self.institution.logo

    @classmethod
    def default_for(cls, institution):
        template = (
            cls.objects.filter(
                institution=institution, status=TemplateStatus.ACTIVE, is_default=True,
            ).first()
            or cls.objects.filter(
                institution=institution, status=TemplateStatus.ACTIVE,
            ).order_by("pk").first()
        )
        return template


# ─────────────────────────────────────────────────────────────────────────────
# ID CARD BATCH
# ─────────────────────────────────────────────────────────────────────────────

class IDCardBatch(TimeStampedModel):
    """
    One generation/print run.

    Batch records are never deleted — they are archived when no longer needed.
    """

    institution = models.ForeignKey(
        "academics.Institution", on_delete=models.CASCADE,
        related_name="id_card_batches", verbose_name=_("institution"),
    )
    batch_number = models.CharField(_("batch number"), max_length=24, unique=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="id_card_batches_generated",
        verbose_name=_("generated by"),
    )
    generated_at = models.DateTimeField(_("generated at"), default=timezone.now)
    status = models.CharField(
        _("status"), max_length=12,
        choices=BatchStatus.choices, default=BatchStatus.GENERATED,
        db_index=True,
    )
    total_selected = models.PositiveIntegerField(_("students selected"), default=0)
    generated_count = models.PositiveIntegerField(_("cards generated"), default=0)
    skipped_count = models.PositiveIntegerField(_("cards skipped"), default=0)
    skips = models.JSONField(_("skips detail"), default=dict, blank=True)
    filters = models.JSONField(_("applied filters"), default=dict, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True)

    class Meta:
        verbose_name = _("ID card batch")
        verbose_name_plural = _("ID card batches")
        ordering = ["-generated_at"]

    def __str__(self):
        return self.batch_number


# ─────────────────────────────────────────────────────────────────────────────
# ID CARD
# ─────────────────────────────────────────────────────────────────────────────

class IDCard(TimeStampedModel):
    """
    One physical/digital identity card.

    Card numbers and verification tokens are globally unique and enforced at
    the database level.  A student may hold at most one *live* card at a time
    (partial unique constraint).  Replaced/expired/cancelled cards are kept
    for history and pass QR verification with a non-valid result.
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="id_cards", verbose_name=_("student"),
        limit_choices_to={"role": "student"},
    )
    institution = models.ForeignKey(
        "academics.Institution", on_delete=models.CASCADE,
        related_name="id_cards", verbose_name=_("institution"),
    )
    template = models.ForeignKey(
        IDCardTemplate, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cards", verbose_name=_("template"),
    )
    photo = models.ForeignKey(
        PassportPhoto, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cards",
        verbose_name=_("photo snapshot"),
        help_text=_("The approved photo used when the card was generated."),
    )
    batch = models.ForeignKey(
        IDCardBatch, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cards", verbose_name=_("batch"),
    )

    card_number = models.CharField(_("card number"), max_length=32, unique=True)
    verification_token = models.CharField(
        _("verification token"), max_length=128, unique=True,
        help_text=_("Secure token embedded in the QR code — never a student ID."),
    )

    status = models.CharField(
        _("status"), max_length=20,
        choices=CardStatus.choices, default=CardStatus.READY,
        db_index=True,
    )

    issue_date = models.DateField(_("issue date"), null=True, blank=True)
    expiry_date = models.DateField(_("expiry date"), null=True, blank=True)

    generated_at = models.DateTimeField(_("generated at"), null=True, blank=True)

    printed_at = models.DateTimeField(_("printed at"), null=True, blank=True)
    printed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="id_cards_printed",
        verbose_name=_("printed by"),
    )
    issued_at = models.DateTimeField(_("issued at"), null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="id_cards_issued",
        verbose_name=_("issued by"),
    )

    # ── Replacement linkage ─────────────────────────────────────────────────
    replaced_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="replacement_of_cards",
        verbose_name=_("replaced by"),
        help_text=_("Set on the old card when a replacement card is issued."),
    )
    replacement_reason = models.CharField(
        _("replacement reason"), max_length=30, blank=True,
        choices=ReplacementReason.choices,
    )
    cancel_reason = models.CharField(_("cancel reason"), max_length=200, blank=True)

    class Meta:
        verbose_name = _("ID card")
        verbose_name_plural = _("ID cards")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=[
                    CardStatus.DRAFT, CardStatus.PHOTO_REQUIRED, CardStatus.PHOTO_PENDING,
                    CardStatus.READY, CardStatus.GENERATED, CardStatus.PRINTED,
                    CardStatus.ISSUED, CardStatus.ACTIVE, CardStatus.EXPIRED,
                    CardStatus.LOST, CardStatus.DAMAGED, CardStatus.REPLACED,
                    CardStatus.CANCELLED,
                ]),
                name="id_cards_status_valid",
            ),
            models.UniqueConstraint(
                fields=["student"],
                condition=models.Q(status__in=CardStatus.LIVE_STATUSES),
                name="uniq_live_card_per_student",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["expiry_date"]),
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["student", "status"]),
        ]

    def __str__(self):
        return f"{self.card_number} — {self.student.get_full_name()} ({self.get_status_display()})"

    # ── Lifecycle helpers ───────────────────────────────────────────────────

    @property
    def is_live(self):
        return self.status in CardStatus.LIVE_STATUSES

    @property
    def is_active_or_issued(self):
        return self.status in CardStatus.ISSUABLE

    @property
    def is_verified(self):
        """True when the card is currently valid for QR verification."""
        if self.status != CardStatus.ACTIVE:
            return False
        if self.expiry_date and self.expiry_date < timezone.localdate():
            return False
        if self.replaced_by_id:
            return False
        return True

    def verification_payload(self):
        """Non-sensitive data exposed through the public verification page."""
        profile = getattr(self.student, "academic_profile", None)
        return {
            "valid": self.is_verified,
            "status": self.get_status_display(),
            "student_name": self.student.get_full_name(),
            "student_number": (profile.student_number if profile else ""),
            "programme": (profile.program.name if profile and profile.program else ""),
            "department": (
                profile.program.department.name
                if profile and profile.program and profile.program.department else ""
            ),
            "card_number": self.card_number,
            "institution": self.institution.name,
            "issue_date": str(self.issue_date) if self.issue_date else "",
            "expiry_date": str(self.expiry_date) if self.expiry_date else "",
            "replaced": bool(self.replaced_by_id),
        }


# ─────────────────────────────────────────────────────────────────────────────
# REPLACEMENT REQUEST
# ─────────────────────────────────────────────────────────────────────────────

class ReplacementRequest(TimeStampedModel):
    """
    Student-initiated (or staff-initiated) replacement request.

    Approval creates a new replacement card and invalidates the old one.
    """

    institution = models.ForeignKey(
        "academics.Institution", on_delete=models.CASCADE,
        related_name="id_replacement_requests", verbose_name=_("institution"),
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="id_replacement_requests", verbose_name=_("student"),
        limit_choices_to={"role": "student"},
    )
    card = models.ForeignKey(
        IDCard, on_delete=models.CASCADE,
        related_name="replacement_requests", verbose_name=_("card to replace"),
    )
    reason = models.CharField(
        _("reason"), max_length=30, choices=ReplacementReason.choices,
    )
    details = models.TextField(_("details"), blank=True)
    status = models.CharField(
        _("status"), max_length=12,
        choices=ReplacementStatus.choices, default=ReplacementStatus.PENDING,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="id_replacements_requested",
        verbose_name=_("requested by"),
    )
    requested_at = models.DateTimeField(_("requested at"), default=timezone.now)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="id_replacement_reviews",
        verbose_name=_("reviewed by"),
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    rejection_reason = models.TextField(_("rejection reason"), blank=True)
    replacement_card = models.ForeignKey(
        IDCard, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_via_request",
        verbose_name=_("replacement card"),
    )

    class Meta:
        verbose_name = _("replacement request")
        verbose_name_plural = _("replacement requests")
        ordering = ["-requested_at"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return (
            f"Replacement {self.get_reason_display()} — "
            f"{self.student.get_full_name()} ({self.get_status_display()})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ID CARD AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────

class IDCardAuditLog(models.Model):
    """
    Immutable audit trail for the ID card lifecycle.

    Ordinary users (including students) can never modify these records; they
    are only appended to by the service layer.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="id_card_audit_logs",
        verbose_name=_("actor"),
    )
    action = models.CharField(
        _("action"), max_length=40, choices=AuditAction.choices, db_index=True,
    )
    card = models.ForeignKey(
        IDCard, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_logs", verbose_name=_("card"),
    )
    batch = models.ForeignKey(
        IDCardBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_logs", verbose_name=_("batch"),
    )
    template = models.ForeignKey(
        IDCardTemplate, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_logs", verbose_name=_("template"),
    )
    photo = models.ForeignKey(
        PassportPhoto, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_logs", verbose_name=_("photo"),
    )
    description = models.TextField(_("description"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("ID card audit log")
        verbose_name_plural = _("ID card audit logs")
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["card", "-created_at"]),
        ]

    def __str__(self):
        who = self.actor.get_full_name() if self.actor else "System"
        return f"{who} — {self.get_action_display()}"


def generate_verification_token(length=32):
    """Cryptographically secure verification token (URL-safe)."""
    return secrets.token_urlsafe(max(length, 16))[:length]