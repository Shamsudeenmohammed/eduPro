"""
portal/models.py

Admissions portal for eduPro.

Key changes from v1:
- AdmissionStatus choices extended with WITHDRAWN.
- AdmissionApplication.approve() now calls
  EduProUser.objects.create_approved_user() to provision the account,
  then links it back via application.user.
- AdmissionApplication.reject() marks status and records actor + timestamp.
- All state transitions are guarded to prevent double-approval.
- Only admissions officers / admin may approve; enforced at the view layer
  via responsibility_required(ADMISSIONS_OFFICER) or admin_required.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ─────────────────────────────────────────────────────────────────────────────
# ORIGINAL MODELS — preserved exactly from portal/models.py v1
# Required by: home, about, news_list, news_detail, contact, admin_contacts
# ─────────────────────────────────────────────────────────────────────────────

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class WebsitePage(TimeStampedModel):
    """CMS-style static pages for the public website."""
    slug             = models.SlugField(unique=True)
    title            = models.CharField(max_length=200)
    content          = models.TextField()
    meta_description = models.CharField(max_length=300, blank=True)
    is_published     = models.BooleanField(default=True)
    order            = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title


class PublicAnnouncement(TimeStampedModel):
    """News / announcements shown on the public homepage."""
    title        = models.CharField(max_length=200)
    summary      = models.TextField(max_length=500)
    content      = models.TextField(blank=True)
    image        = models.ImageField(upload_to="portal/news/", blank=True, null=True)
    is_featured  = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]

    def __str__(self):
        return self.title


class ContactMessage(TimeStampedModel):
    """Contact form submissions."""
    name       = models.CharField(max_length=150)
    email      = models.EmailField()
    phone      = models.CharField(max_length=30, blank=True)
    subject    = models.CharField(max_length=200)
    message    = models.TextField()
    is_read    = models.BooleanField(default=False)
    replied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} — {self.subject}"


# ─────────────────────────────────────────────────────────────────────────────
# CHOICES
# ─────────────────────────────────────────────────────────────────────────────

class AdmissionStatus(models.TextChoices):
    PENDING    = "pending",    _("Pending Review")
    REVIEW     = "review",     _("Under Review")       # original value
    REVIEWING  = "reviewing",  _("Under Review")       # new alias
    ACCEPTED   = "accepted",   _("Accepted")           # original value
    APPROVED   = "approved",   _("Approved")           # new alias
    REJECTED   = "rejected",   _("Rejected")
    WAITLIST   = "waitlist",   _("Waitlisted")         # original value
    WITHDRAWN  = "withdrawn",  _("Withdrawn by Applicant")


class ApplicationType(models.TextChoices):
    UNDERGRADUATE = "undergraduate", _("Undergraduate")
    POSTGRADUATE  = "postgraduate",  _("Postgraduate")
    DIPLOMA       = "diploma",       _("Diploma")
    TRANSFER      = "transfer",      _("Transfer")
    EXCHANGE      = "exchange",      _("Exchange / Visiting")


class Gender(models.TextChoices):
    MALE        = "male",        _("Male")
    FEMALE      = "female",      _("Female")
    NON_BINARY  = "non_binary",  _("Non-binary")
    PREFER_NOT  = "prefer_not",  _("Prefer not to say")


# ─────────────────────────────────────────────────────────────────────────────
# ADMISSION CYCLE
# ─────────────────────────────────────────────────────────────────────────────

class AdmissionCycle(models.Model):
    """
    Defines the active intake window.  Only one cycle should be is_active=True
    at a time (enforced in save()).
    """
    name            = models.CharField(_("cycle name"), max_length=100, unique=True)
    academic_year   = models.CharField(_("academic year"), max_length=20)
    start_date      = models.DateField(_("application open date"))
    end_date        = models.DateField(_("application close date"))
    is_active       = models.BooleanField(_("active"), default=False)
    max_applications = models.PositiveIntegerField(
        _("max applications"), default=0,
        help_text=_("0 = unlimited"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = _("admission cycle")
        verbose_name_plural = _("admission cycles")
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"

    def save(self, *args, **kwargs):
        if self.is_active:
            AdmissionCycle.objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()

    @property
    def is_open(self):
        today = timezone.now().date()
        return self.is_active and self.start_date <= today <= self.end_date


# ─────────────────────────────────────────────────────────────────────────────
# ADMISSION APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

class AdmissionApplication(models.Model):
    """
    A prospective student's application for admission.

    Onboarding flow:
        1. Applicant fills in the public application form (no login needed).
        2. Application sits with status=PENDING.
        3. Admissions officer / admin reviews → sets REVIEWING.
        4. On APPROVED:
               - approve() provisions an EduProUser (is_active=True) and a
                 StudentProfile.
               - application.user is linked to the new account.
               - Applicant can now log in with the temp password.
        5. On REJECTED:
               - No user account is created / existing account is deactivated.
    """

    # ── Cycle + program intent ────────────────────────────────────────────────
    cycle = models.ForeignKey(
        AdmissionCycle,
        on_delete=models.PROTECT,
        related_name="applications",
        verbose_name=_("admission cycle"),
    )
    program_applied = models.ForeignKey(
        "academics.Program",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="applications",
        verbose_name=_("program applied for"),
    )
    application_type = models.CharField(
        _("application type"),
        max_length=20,
        choices=ApplicationType.choices,
        default=ApplicationType.UNDERGRADUATE,
    )

    # ── Personal details ──────────────────────────────────────────────────────
    first_name    = models.CharField(_("first name"),    max_length=150)
    last_name     = models.CharField(_("last name"),     max_length=150)
    other_names   = models.CharField(_("other names"),   max_length=150, blank=True)
    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True)
    gender        = models.CharField(
        _("gender"), max_length=20,
        choices=Gender.choices, blank=True,
    )
    nationality   = models.CharField(_("nationality"),   max_length=100, blank=True)
    email         = models.EmailField(_("email address"), unique=False)
    phone         = models.CharField(_("phone number"),  max_length=30, blank=True)
    address       = models.TextField(_("residential address"), blank=True)

    # ── Academic background ───────────────────────────────────────────────────
    previous_school      = models.CharField(_("previous school / institution"), max_length=200, blank=True)
    qualification        = models.CharField(_("highest qualification"),         max_length=100, blank=True)
    year_of_completion   = models.PositiveSmallIntegerField(
        _("year of completion"), null=True, blank=True,
        validators=[MinValueValidator(1950), MaxValueValidator(2100)],
    )
    aggregate_score      = models.DecimalField(
        _("aggregate / GPA"), max_digits=5, decimal_places=2,
        null=True, blank=True,
    )

    # ── Supporting documents ──────────────────────────────────────────────────
    transcript = models.FileField(
        _("transcript / results slip"),
        upload_to="admissions/transcripts/%Y/%m/",
        null=True, blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
    )
    id_document = models.FileField(
        _("ID / passport"),
        upload_to="admissions/ids/%Y/%m/",
        null=True, blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
    )
    passport_photo = models.ImageField(
        _("passport photograph"),
        upload_to="admissions/photos/%Y/%m/",
        null=True, blank=True,
    )
    personal_statement = models.TextField(_("personal statement"), blank=True)

    # ── Original fields kept for legacy AdmissionForm compatibility ───────────
    # The original AdmissionApplication had these plain fields. They are
    # preserved so the legacy portal:admission_apply view keeps working.
    qualifications = models.TextField(_("qualifications"), blank=True)
    documents      = models.FileField(
        _("documents"),
        upload_to="portal/admissions/",
        blank=True, null=True,
    )
    notes = models.TextField(_("notes"), blank=True)

    # ── Status & workflow ─────────────────────────────────────────────────────
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=AdmissionStatus.choices,
        default=AdmissionStatus.PENDING,
        db_index=True,
    )
    reference_number = models.CharField(
        _("reference number"), max_length=30, unique=True, blank=True,
    )

    # ── Review audit ──────────────────────────────────────────────────────────
    reviewed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="reviewed_applications",
        verbose_name=_("reviewed by"),
    )
    reviewed_at  = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    review_notes = models.TextField(_("review notes"), blank=True)

    # ── Approval audit ────────────────────────────────────────────────────────
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="approved_applications",
        verbose_name=_("approved by"),
    )
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)

    # ── Rejection audit ───────────────────────────────────────────────────────
    rejected_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="rejected_applications",
        verbose_name=_("rejected by"),
    )
    rejected_at     = models.DateTimeField(_("rejected at"), null=True, blank=True)
    rejection_reason = models.TextField(_("rejection reason"), blank=True)

    # ── Provisioned user account (set after approval) ─────────────────────────
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="admission_application",
        verbose_name=_("provisioned user account"),
        help_text=_("Created automatically on approval."),
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at = models.DateTimeField(_("applied at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name        = _("admission application")
        verbose_name_plural = _("admission applications")
        ordering = ["-created_at"]
        indexes  = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self):
        return (
            f"{self.get_full_name()} — "
            f"{self.program_applied.code if self.program_applied else 'No Program'} "
            f"[{self.reference_number or 'No Ref'}] "
            f"({self.get_status_display()})"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_full_name(self):
        parts = [self.first_name, self.other_names, self.last_name]
        return " ".join(p for p in parts if p).strip()

    def save(self, *args, **kwargs):
        if not self.reference_number:
            self.reference_number = self._generate_ref()
        super().save(*args, **kwargs)

    def _generate_ref(self):
        import uuid
        year  = timezone.now().year
        short = str(uuid.uuid4()).replace("-", "").upper()[:8]
        return f"APP-{year}-{short}"

    # ── State transitions ─────────────────────────────────────────────────────

    def mark_reviewing(self, actor):
        """Admissions officer starts reviewing the application."""
        if self.status not in (AdmissionStatus.PENDING,):
            raise ValidationError(
                _("Only PENDING applications can be moved to REVIEWING.")
            )
        self.status      = AdmissionStatus.REVIEWING
        self.reviewed_by = actor
        self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

    @transaction.atomic
    def approve(self, actor, temp_password: str = None):
        """
        Approve the application and provision the EduProUser account.

        Steps (all in one DB transaction):
          1. Validate current status is PENDING or REVIEWING.
          2. Check no user account already provisioned.
          3. Create EduProUser (is_active=True, role=STUDENT).
          4. Create/link StudentProfile.
          5. Update application: status=APPROVED, user=<new user>.

        Args:
            actor:         The admin/admissions officer performing the approval.
            temp_password: If provided, used as the account password.
                           If None, a random secure password is set (user must
                           reset via email).

        Returns:
            The newly provisioned EduProUser instance.
        """
        if self.status not in (AdmissionStatus.PENDING, AdmissionStatus.REVIEWING):
            raise ValidationError(
                _("Only PENDING or REVIEWING applications can be approved. "
                  "Current status: %(s)s.") % {"s": self.get_status_display()}
            )
        if self.user_id:
            raise ValidationError(
                _("A user account has already been provisioned for this application.")
            )

        from django.contrib.auth import get_user_model
        from academics.models import StudentProfile

        User = get_user_model()

        # ── 1. Provision user ────────────────────────────────────────────────
        if User.objects.filter(email=self.email).exists():
            # Edge-case: email already in system (e.g. returning applicant).
            # Link to existing user and re-activate.
            new_user = User.objects.get(email=self.email)
            new_user.is_active   = True
            new_user.approved_by = actor
            new_user.approved_at = timezone.now()
            new_user.save(update_fields=["is_active", "approved_by", "approved_at"])
        else:
            new_user = User.objects.create_approved_user(
                email      = self.email,
                password   = temp_password,   # None → unusable pw → must reset
                first_name = self.first_name,
                last_name  = self.last_name,
                role       = "student",
            )
            new_user.approved_by = actor
            new_user.approved_at = timezone.now()
            new_user.save(update_fields=["approved_by", "approved_at"])

        # ── 2. Create / update StudentProfile ────────────────────────────────
        profile, _ = StudentProfile.all_objects.get_or_create(student=new_user)
        if self.program_applied:
            profile.program        = self.program_applied
            profile.admission_date = timezone.now().date()
            profile.save(update_fields=["program", "admission_date"])

        # ── 3. Update application record ──────────────────────────────────────
        self.status      = AdmissionStatus.APPROVED
        self.user        = new_user
        self.approved_by = actor
        self.approved_at = timezone.now()
        self.save(update_fields=[
            "status", "user", "approved_by", "approved_at", "updated_at"
        ])

        return new_user

    def reject(self, actor, reason: str = ""):
        """Reject the application. No user account is created."""
        if self.status in (AdmissionStatus.APPROVED, AdmissionStatus.WITHDRAWN):
            raise ValidationError(
                _("Cannot reject an already %(s)s application.")
                % {"s": self.get_status_display()}
            )
        self.status          = AdmissionStatus.REJECTED
        self.rejected_by     = actor
        self.rejected_at     = timezone.now()
        self.rejection_reason = reason
        self.save(update_fields=[
            "status", "rejected_by", "rejected_at", "rejection_reason", "updated_at"
        ])

    def withdraw(self):
        """Applicant withdraws their own application."""
        if self.status in (AdmissionStatus.APPROVED, AdmissionStatus.REJECTED):
            raise ValidationError(
                _("Cannot withdraw an already %(s)s application.")
                % {"s": self.get_status_display()}
            )
        self.status = AdmissionStatus.WITHDRAWN
        self.save(update_fields=["status", "updated_at"])


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT REQUEST (optional supporting model)
# ─────────────────────────────────────────────────────────────────────────────

class DocumentRequest(models.Model):
    """
    Admissions officer can request additional documents from an applicant.
    Sent via email notification; applicant re-uploads through the portal.
    """

    class RequestStatus(models.TextChoices):
        PENDING   = "pending",   _("Pending")
        FULFILLED = "fulfilled", _("Fulfilled")
        WAIVED    = "waived",    _("Waived")

    application    = models.ForeignKey(
        AdmissionApplication,
        on_delete=models.CASCADE,
        related_name="document_requests",
        verbose_name=_("application"),
    )
    document_name  = models.CharField(_("document required"), max_length=200)
    instructions   = models.TextField(_("instructions to applicant"), blank=True)
    status         = models.CharField(
        _("status"), max_length=20,
        choices=RequestStatus.choices, default=RequestStatus.PENDING,
    )
    requested_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="document_requests_made",
        verbose_name=_("requested by"),
    )
    requested_at   = models.DateTimeField(_("requested at"), default=timezone.now)
    fulfilled_at   = models.DateTimeField(_("fulfilled at"), null=True, blank=True)
    uploaded_file  = models.FileField(
        _("uploaded document"),
        upload_to="admissions/requested_docs/%Y/%m/",
        null=True, blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "doc", "docx"])],
    )

    class Meta:
        verbose_name        = _("document request")
        verbose_name_plural = _("document requests")
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.document_name} — {self.application.get_full_name()}"

    def mark_fulfilled(self):
        self.status       = self.RequestStatus.FULFILLED
        self.fulfilled_at = timezone.now()
        self.save(update_fields=["status", "fulfilled_at"])
