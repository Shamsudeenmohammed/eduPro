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
from django.db.utils import OperationalError, ProgrammingError
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
    """
    Workflow status of an application.

    IMPORTANT: this is the *workflow* only. The admission outcome lives in
    ``AdmissionApplication.decision`` (see :class:`AdmissionDecision`) so a
    workflow state and an academic outcome can never contradict each other.

    Legacy values (PENDING / REVIEW / REVIEWING / ACCEPTED / APPROVED) are
    preserved because they are already stored in the database and referenced
    by existing templates and views. :mod:`portal.workflow` normalises them.
    """

    # ── New, explicit workflow stages ────────────────────────────────────────
    DRAFT               = "draft",                _("Draft")
    SUBMITTED           = "submitted",            _("Submitted")
    PAYMENT_PENDING     = "payment_pending",      _("Awaiting Payment")
    PAYMENT_CONFIRMED   = "payment_confirmed",    _("Payment Confirmed")
    UNDER_REVIEW        = "under_review",         _("Under Review")
    NEEDS_CORRECTION    = "needs_correction",     _("Needs Correction")
    RESUBMITTED         = "resubmitted",          _("Resubmitted")
    SHORTLISTED         = "shortlisted",          _("Shortlisted")
    INTERVIEW_REQUIRED  = "interview_required",   _("Interview Required")
    INTERVIEW_COMPLETED = "interview_completed",  _("Interview Completed")
    DECISION_PENDING    = "decision_pending",     _("Awaiting Decision")
    COMPLETED           = "completed",            _("Completed")
    CONVERTED           = "converted",            _("Converted to Student")

    # ── Legacy values (kept: existing rows + templates depend on them) ───────
    PENDING    = "pending",    _("Pending Review")
    REVIEW     = "review",     _("Under Review")
    REVIEWING  = "reviewing",  _("Under Review")
    ACCEPTED   = "accepted",   _("Accepted")
    APPROVED   = "approved",   _("Approved")
    REJECTED   = "rejected",   _("Rejected")
    WAITLIST   = "waitlist",   _("Waitlisted")
    WITHDRAWN  = "withdrawn",  _("Withdrawn by Applicant")

    @classmethod
    def workflow_values(cls):
        """Every value that represents an in-flight workflow state."""
        return [c.value for c in cls if c.value not in _TERMINAL_STATUS_VALUES]


#: Workflow values that close an application and permit no further movement.
_TERMINAL_STATUS_VALUES = {
    AdmissionStatus.WITHDRAWN,
    AdmissionStatus.REJECTED,
    AdmissionStatus.CONVERTED,
}


class AdmissionDecision(models.TextChoices):
    """
    The academic outcome of an application — deliberately separate from
    :class:`AdmissionStatus` so "under review" and "accepted" can coexist.
    """

    PENDING               = "pending",                _("Pending")
    ACCEPTED              = "accepted",               _("Accepted")
    CONDITIONALLY_ACCEPTED = "conditionally_accepted", _("Conditionally Accepted")
    REJECTED              = "rejected",               _("Rejected")
    WAITLISTED            = "waitlisted",             _("Waitlisted")


class ApplicationTypeCode(models.TextChoices):
    """
    Stable machine codes for application types.

    The ``code`` values are stored verbatim in
    ``AdmissionApplication.application_type`` and must never change: they are
    already persisted and referenced by templates. Human-facing labels,
    ordering and configurable requirements live in the
    :class:`ApplicationType` *model* (created lazily by
    :meth:`ApplicationType.for_code`), so new types can be added from the
    admin without a code change.
    """

    UNDERGRADUATE     = "undergraduate",     _("Undergraduate")
    POSTGRADUATE      = "postgraduate",      _("Postgraduate")
    DIPLOMA           = "diploma",           _("Diploma")
    CERTIFICATE       = "certificate",       _("Certificate")
    MATURE            = "mature",            _("Mature Applicant")
    TRANSFER          = "transfer",          _("Transfer Applicant")
    INTERNATIONAL     = "international",     _("International Applicant")
    DISTANCE_LEARNING = "distance_learning", _("Distance Learning")
    # Legacy code kept for backward compatibility with existing rows.
    EXCHANGE          = "exchange",          _("Exchange / Visiting")


#: Backwards-compatible alias — the enum used to be called ``ApplicationType``
#: before the identically-named model was introduced. Kept so any external
#: import keeps working.
ApplicationType_Choices = ApplicationTypeCode.choices


class Gender(models.TextChoices):
    MALE        = "male",        _("Male")
    FEMALE      = "female",      _("Female")
    NON_BINARY  = "non_binary",  _("Non-binary")
    PREFER_NOT  = "prefer_not",  _("Prefer not to say")


# ─────────────────────────────────────────────────────────────────────────────
# APPLICATION TYPE (configurable) + REQUIREMENTS
# ─────────────────────────────────────────────────────────────────────────────

class ApplicationType(TimeStampedModel):
    """
    A configurable application type (Undergraduate, Postgraduate, …).

    The stored code is the *stable* identifier already persisted in
    ``AdmissionApplication.application_type``; this model adds the parts that
    must be configurable without a code change: display label, ordering,
    whether it is currently offered, and which requirements apply.

    Types are created lazily by :meth:`for_code` and can equally be created by
    an administrator, which is how the engine stays extensible for Diploma,
    Certificate, Mature, Transfer, International, Distance Learning, etc.
    """

    code = models.SlugField(
        _("code"), max_length=40, unique=True,
        help_text=_("Stable machine code, e.g. 'undergraduate'."),
    )
    label = models.CharField(_("label"), max_length=100)
    description = models.TextField(_("description"), blank=True)

    is_active = models.BooleanField(
        _("offered"), default=True,
        help_text=_("Untick to stop new applications of this type without "
                    "affecting applications already in the system."),
    )
    requires_program = models.BooleanField(
        _("requires programme selection"), default=True,
        help_text=_("If unticked, applicants of this type pick a programme "
                    "later instead of at application time."),
    )
    requires_payment = models.BooleanField(
        _("requires application fee"), default=True,
    )
    order = models.PositiveSmallIntegerField(_("display order"), default=100)

    class Meta:
        verbose_name        = _("application type")
        verbose_name_plural = _("application types")
        ordering            = ["order", "label"]

    def __str__(self):
        return self.label or self.code

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().lower()
        if not self.label:
            self.label = self.code.replace("_", " ").title()
        super().save(*args, **kwargs)

    @classmethod
    def for_code(cls, code):
        """
        Return the :class:`ApplicationType` for ``code``, creating it on first
        use so the engine works on a database that predates this model and
        before any data migration has been run.
        """
        if not code:
            return None
        code = str(code).strip().lower()
        obj, _created = cls.objects.get_or_create(
            code=code,
            defaults={"label": code.replace("_", " ").title()},
        )
        return obj

    @classmethod
    def seed_defaults(cls):
        """
        Ensure the standard types exist. Idempotent — safe to call repeatedly.
        Returns the list of codes that were created.
        """
        created = []
        for order, member in enumerate(ApplicationTypeCode, start=1):
            _, made = cls.objects.get_or_create(
                code=member.value,
                defaults={
                    "label": member.label,
                    "order": order * 10,
                    "description": "",
                },
            )
            if made:
                created.append(member.value)
        return created


class RequirementKind(models.TextChoices):
    FIELD    = "field",    _("Information field")
    DOCUMENT = "document", _("Supporting document")


class ApplicationRequirement(TimeStampedModel):
    """
    A single configurable requirement for an application.

    This is the extension point that keeps undergraduate / postgraduate rules
    out of the views (requirement 11 & 14). A requirement is either:

    * ``FIELD``    — an information field that must be completed. ``code``
                     is the name of the field on :class:`AdmissionApplication`
                     (or a synthetic key handled by the checklist service).
    * ``DOCUMENT`` — a document that must be uploaded, of ``document_type``.

    Requirements are resolved most-specific-first: programme-specific
    overrides application-type-wide, so a cycle can tighten rules for one
    programme without affecting the rest of the faculty.
    """

    application_type = models.ForeignKey(
        ApplicationType, on_delete=models.CASCADE,
        related_name="requirements", verbose_name=_("application type"),
    )
    program = models.ForeignKey(
        "academics.Program", on_delete=models.CASCADE,
        null=True, blank=True, related_name="application_requirements",
        verbose_name=_("programme (optional)"),
        help_text=_("Leave empty to apply to every programme of this type."),
    )

    kind = models.CharField(
        _("requirement kind"), max_length=10,
        choices=RequirementKind.choices, default=RequirementKind.DOCUMENT,
    )
    code = models.SlugField(
        _("code"), max_length=60,
        help_text=_("For fields: the model field name. For documents: the document type code."),
    )
    label = models.CharField(_("label"), max_length=150)
    help_text = models.CharField(_("help text"), max_length=250, blank=True)
    is_required = models.BooleanField(_("required"), default=True)
    order = models.PositiveSmallIntegerField(_("order"), default=100)

    class Meta:
        verbose_name        = _("application requirement")
        verbose_name_plural = _("application requirements")
        ordering            = ["application_type", "kind", "order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["application_type", "program", "kind", "code"],
                name="uniq_requirement_per_type_program",
            ),
        ]

    def __str__(self):
        scope = self.program.code if self.program else "all programmes"
        return f"{self.label} ({self.application_type.code} / {scope})"

    @property
    def document_type(self):
        """The :class:`ApplicationDocumentType` code this requirement demands."""
        return self.code if self.kind == RequirementKind.DOCUMENT else None

    @property
    def field_name(self):
        return self.code if self.kind == RequirementKind.FIELD else None

    @classmethod
    def resolve_for(cls, application_type_code, program=None):
        """
        Effective requirements for a type (and optionally a programme).

        Programme-specific rows replace the type-wide row with the same
        ``kind``/``code`` so a requirement can be dropped for one programme by
        creating a non-required override row.
        """
        atype = ApplicationType.for_code(application_type_code)
        if atype is None:
            return []
        qs = cls.objects.filter(application_type=atype).select_related("program")
        effective = {}
        for req in qs.filter(program__isnull=True):
            effective[(req.kind, req.code)] = req
        for req in qs.filter(program=program) if program else []:
            effective[(req.kind, req.code)] = req
        return sorted(effective.values(), key=lambda r: (r.kind, r.order, r.code))


# ─────────────────────────────────────────────────────────────────────────────
# ADMISSION CYCLE
# ─────────────────────────────────────────────────────────────────────────────

class CycleStatus(models.TextChoices):
    DRAFT    = "draft",    _("Draft")
    OPEN     = "open",     _("Open")
    CLOSED   = "closed",   _("Closed")
    ARCHIVED = "archived", _("Archived")


class AdmissionCycle(models.Model):
    """
    Defines the active intake window.  Only one cycle should be is_active=True
    at a time (enforced in save()).

    Upgraded (all additions are optional, so existing rows keep working):
      * academic_session  — links to the EXISTING academics.AcademicSession
                            instead of relying on a free-text academic_year.
      * application_type  — the configurable :class:`ApplicationType` this
                            cycle recruits for.
      * application_fee   — the fee payable for this cycle.
      * status            — replaces the bare is_active boolean for opening and
                            closing applications without a code change.
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

    # ── Added: richer configuration (optional, backward compatible) ──────────
    academic_session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.PROTECT,
        null=True, blank=True, related_name="admission_cycles",
        verbose_name=_("academic session"),
        help_text=_("The existing academic session applicants will be admitted into."),
    )
    application_type = models.ForeignKey(
        ApplicationType, on_delete=models.PROTECT,
        null=True, blank=True, related_name="cycles",
        verbose_name=_("application type"),
    )
    application_fee = models.DecimalField(
        _("application fee"), max_digits=10, decimal_places=2,
        default=0,
        help_text=_("0 = no application fee for this cycle."),
    )
    status = models.CharField(
        _("status"), max_length=10,
        choices=CycleStatus.choices, default=CycleStatus.DRAFT,
        help_text=_("Only OPEN cycles accept new applications."),
    )
    payment_required_to_progress = models.BooleanField(
        _("payment required before review"), default=True,
        help_text=_("When ticked, an application only moves past payment "
                    "confirmation once the application fee is verified."),
    )

    class Meta:
        verbose_name        = _("admission cycle")
        verbose_name_plural = _("admission cycles")
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"

    def save(self, *args, **kwargs):
        if self.is_active:
            AdmissionCycle.objects.exclude(pk=self.pk).update(is_active=False)
        if self.is_active and self.status == CycleStatus.DRAFT:
            # Legacy rows set is_active=True directly; treat that as "open".
            self.status = CycleStatus.OPEN
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()

    @classmethod
    def get_open_for(cls, application_type_code=None):
        """
        The cycle that should receive a new application of this type.

        Prefers an open cycle matching the requested application type, and
        falls back to the single active cycle so existing single-type
        institutions keep working.
        """
        today = timezone.now().date()
        qs = cls.objects.filter(
            is_active=True,
            status=CycleStatus.OPEN,
            start_date__lte=today,
            end_date__gte=today,
        )
        if application_type_code:
            typed = qs.filter(
                application_type__code=application_type_code
            ).order_by("-start_date").first()
            if typed:
                return typed
        return qs.order_by("-start_date").first()

    @property
    def is_open(self):
        today = timezone.now().date()
        if not self.is_active:
            return False
        if self.status in (CycleStatus.CLOSED, CycleStatus.ARCHIVED):
            return False
        return self.start_date <= today <= self.end_date

    @property
    def accepts_applications(self):
        """Alias used by views so intent is explicit at the call site."""
        return self.is_open

    @property
    def requires_payment(self):
        return bool(self.application_fee) and self.application_fee > 0

    def admission_summary(self):
        types = self.accepted_types()
        if not types:
            return "All types"
        if len(types) == 1:
            return types[0].label
        return f"{types[0].label} +{len(types) - 1} more"

    # ── Accepted application types ───────────────────────────────────────────
    # `application_type` above can only name one type, but a real intake
    # usually recruits several at once ("undergraduate" + "mature"). The
    # accepted set therefore lives in its own join table, and the FK is kept
    # as the *primary* type so the admin filters and every row created before
    # the join table existed keep working untouched.
    #
    # Reading rules, in order:
    #   1. join rows exist      -> exactly those types are accepted
    #   2. no join rows, FK set -> the single referenced type
    #   3. neither              -> the cycle accepts any type
    # `join table missing` is a real state here: like every other table in this
    # engine it is created from the models rather than from a migration file, so
    # it may not exist yet on a database that predates it. Callers must go
    # through `accepted_types()`, which degrades to the old single-type reading
    # instead of raising.

    def accepted_types(self):
        """
        The :class:`ApplicationType` rows this cycle accepts, in display order.

        Returns an empty list when the cycle is unrestricted, which callers
        treat as "any type". Ordering follows ``ApplicationType.order`` so the
        list is stable no matter what order the choices were ticked in.
        """
        try:
            links = list(self.application_type_links.all())
        except (OperationalError, ProgrammingError):
            # The join table has not been created on this database yet.
            links = []
        if links:
            return sorted(
                (link.application_type for link in links),
                key=lambda t: (t.order, t.code),
            )
        return [self.application_type] if self.application_type else []

    def accepts_type(self, application_type):
        """True when *application_type* is one this cycle accepts."""
        if application_type is None:
            return True
        accepted = self.accepted_types()
        if not accepted:
            return True
        return any(t.pk == application_type.pk for t in accepted)

    def set_accepted_types(self, application_types):
        """
        Replace the accepted set and keep the primary-type FK in step.

        The FK is set to the first type in display order, so "the cycle's type"
        stays deterministic and the admin list filters keep grouping sensibly.
        """
        types = sorted(
            {t.pk: t for t in (application_types or [])}.values(),
            key=lambda t: (t.order, t.code),
        )
        self.application_type = types[0] if types else None
        if self.pk:
            self.application_type_links.all().delete()
            self.application_type_links.bulk_create([
                CycleApplicationType(cycle=self, application_type=t)
                for t in types
            ])
        return types

    @property
    def primary_application_type(self):
        """The type used for admin filtering; falls back to the first accepted."""
        if self.application_type:
            return self.application_type
        accepted = self.accepted_types()
        return accepted[0] if accepted else None



class CycleApplicationType(models.Model):
    """
    Links an :class:`AdmissionCycle` to one of the :class:`ApplicationType`
    rows it accepts, so a cycle can recruit for several types at once.
    """

    cycle = models.ForeignKey(
        AdmissionCycle, on_delete=models.CASCADE,
        related_name="application_type_links", verbose_name=_("admission cycle"),
    )
    application_type = models.ForeignKey(
        ApplicationType, on_delete=models.CASCADE,
        related_name="cycle_links", verbose_name=_("application type"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = _("cycle application type")
        verbose_name_plural = _("cycle application types")
        ordering            = ["application_type__order", "application_type__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["cycle", "application_type"],
                name="uniq_cycle_application_type",
            ),
        ]

    def __str__(self):
        return f"{self.cycle_id}: {self.application_type.code}"



class ProgramApplicationType(models.Model):
    """
    Which :class:`ApplicationType` rows a particular ``academics.Program``
    accepts.

    A programme is not a catch-all: a Higher National Diploma programme should
    not offer postgraduate entry, and a Master's programme should not offer a
    certificate. ``academics.Program.program_type`` already uses the same
    vocabulary as :class:`ApplicationTypeCode`, so that field is the default
    answer and this table is the explicit override — add rows when a programme
    accepts something beyond (or instead of) its own type.

    A programme with no rows here accepts exactly the type that matches its
    ``program_type``. See :meth:`academics.Program.available_application_types`
    for the full reading, including the fallback when types are not seeded.
    """

    program = models.ForeignKey(
        "academics.Program", on_delete=models.CASCADE,
        related_name="application_type_links", verbose_name=_("programme"),
    )
    application_type = models.ForeignKey(
        ApplicationType, on_delete=models.CASCADE,
        related_name="program_links", verbose_name=_("application type"),
    )
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = _("programme application type")
        verbose_name_plural = _("programme application types")
        ordering            = ["application_type__order", "application_type__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "application_type"],
                name="uniq_program_application_type",
            ),
        ]

    def __str__(self):
        return f"{self.program_id}: {self.application_type.code}"


#: How a programme's own ``program_type`` maps onto an application type.
#:
#: The two enumerations mostly overlap, but not quite: doctoral entry is
#: postgraduate entry, and a professional programme is diploma-level. Without
#: this a doctorate programme would fall through to "accepts everything".
PROGRAM_TYPE_TO_APPLICATION_TYPE = {
    "undergraduate": "undergraduate",
    "postgraduate":  "postgraduate",
    "doctorate":     "postgraduate",
    "diploma":       "diploma",
    "certificate":   "certificate",
    "professional":  "diploma",
}


def program_available_types(program):
    """
    The application types *program* accepts, as a list of
    :class:`ApplicationType`.

    Reading order:

    1. explicit :class:`ProgramApplicationType` rows -> exactly those;
    2. otherwise the type matching ``program.program_type`` (see
       :data:`PROGRAM_TYPE_TO_APPLICATION_TYPE`), so a diploma programme does
       not offer postgraduate and a postgraduate programme does not offer
       diploma;
    3. if no type row matches — the catalogue has not been seeded, or the
       programme has a ``program_type`` with no equivalent application type —
       fall back to every active type, so the dropdown is never empty just
       because the catalogue has not been populated.

    An inactive programme is treated as accepting everything, because the
    callers filter on ``is_active`` themselves and an over-narrow list here
    would hide types from an admin editing the programme.
    """
    active = ApplicationType.objects.filter(is_active=True).order_by("order", "code")
    if program is None or not program.is_active:
        return list(active)

    overrides = [
        link.application_type
        for link in program.application_type_links.select_related("application_type")
        if link.is_active and link.application_type.is_active
    ]
    if overrides:
        return sorted(overrides, key=lambda t: (t.order, t.code))

    code = PROGRAM_TYPE_TO_APPLICATION_TYPE.get(program.program_type)
    own = [t for t in active if t.code == code] if code else []
    return own if own else list(active)



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
        choices=ApplicationTypeCode.choices,
        default=ApplicationTypeCode.UNDERGRADUATE,
    )
    application_type_ref = models.ForeignKey(
        ApplicationType, on_delete=models.PROTECT,
        null=True, blank=True, related_name="applications",
        verbose_name=_("application type (configured)"),
        help_text=_("Auto-resolved from the application type code on save. "
                    "Links the application to its configurable requirements."),
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
    # NOTE: the *workflow* status only. The academic outcome is `decision`
    # below — keeping them apart prevents contradictory states such as
    # "Rejected" while the decision says "Accepted".
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

    # ── Admission decision (separate from workflow status) ────────────────────
    decision = models.CharField(
        _("admission decision"),
        max_length=25,
        choices=AdmissionDecision.choices,
        default=AdmissionDecision.PENDING,
        db_index=True,
        help_text=_("The academic outcome. Independent of the workflow status above."),
    )
    decision_made_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="admission_decisions_made",
        verbose_name=_("decision made by"),
    )
    decision_at = models.DateTimeField(_("decision made at"), null=True, blank=True)
    decision_notes = models.TextField(
        _("decision notes"), blank=True,
        help_text=_("Internal remarks. Conditions attached to an offer belong on the offer."),
    )

    # ── Submission / correction tracking ──────────────────────────────────────
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    last_correction_note = models.TextField(
        _("last correction request"), blank=True,
        help_text=_("Most recent reason staff asked the applicant to correct something."),
    )
    correction_requested_at = models.DateTimeField(
        _("correction requested at"), null=True, blank=True,
    )
    interview_at = models.DateTimeField(_("interview scheduled at"), null=True, blank=True)
    interview_remarks = models.TextField(_("interview remarks"), blank=True)

    # ── Conversion to student (requirement 7: applicant is NOT a student) ─────
    converted_at = models.DateTimeField(_("converted to student at"), null=True, blank=True)
    converted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="applications_converted",
        verbose_name=_("converted by"),
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

    @property
    def type_config(self):
        """The configurable :class:`ApplicationType` for this application."""
        if self.application_type_ref_id:
            return self.application_type_ref
        return ApplicationType.for_code(self.application_type)

    @property
    def department(self):
        """
        Department reached through the EXISTING academic structure
        (Program → Department → Faculty). Never duplicated on the application.
        """
        return self.program_applied.department if self.program_applied_id else None

    @property
    def faculty(self):
        dept = self.department
        return dept.faculty if dept else None

    @property
    def academic_session(self):
        """Session this application targets, via the cycle."""
        return self.cycle.academic_session if self.cycle_id else None

    @property
    def is_converted(self):
        return bool(self.converted_at) or self.status == AdmissionStatus.CONVERTED

    @property
    def is_draft(self):
        return self.status == AdmissionStatus.DRAFT

    @property
    def is_submitted(self):
        return self.status not in (
            AdmissionStatus.DRAFT, AdmissionStatus.PENDING, AdmissionStatus.REVIEW,
        )

    @property
    def is_terminal(self):
        return self.status in _TERMINAL_STATUS_VALUES

    @property
    def is_admitted(self):
        return self.decision in (
            AdmissionDecision.ACCEPTED,
            AdmissionDecision.CONDITIONALLY_ACCEPTED,
        )

    @property
    def is_withdrawn(self):
        """True once the applicant has pulled out (or legacy status says so)."""
        return self.status == AdmissionStatus.WITHDRAWN

    def get_program_name(self):
        """
        Human label for the applied programme, tolerating an unset choice.

        A draft may not have chosen a programme yet, so templates use this
        instead of dereferencing ``program_applied.name`` directly.
        """
        if self.program_applied_id and self.program_applied:
            return self.program_applied.name
        return "Programme not yet selected"

    @property
    def is_rejected(self):
        return self.decision == AdmissionDecision.REJECTED

    @property
    def application_fee(self):
        """
        The fee due for this application, inherited from its cycle.

        Always returned as ``Decimal``. A ``DecimalField`` can still hold a
        ``str`` when a value was assigned programmatically (a cycle built in
        code, or a form that has not been through ``full_clean``), and every
        consumer here does arithmetic or comparison against it, so coercing
        once here keeps the money comparisons total.
        """
        from decimal import Decimal, InvalidOperation

        if not self.cycle_id:
            return Decimal("0")
        fee = self.cycle.application_fee
        if fee in (None, ""):
            return Decimal("0")
        try:
            return Decimal(fee)
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    @property
    def fee_outstanding(self):
        return self.payment_balance > 0

    @property
    def payment_balance(self):
        """
        Amount still payable. Deliberately derived from *verified* payments
        only — opening a payment page must never mark the fee as paid.
        """
        from decimal import Decimal
        due = self.application_fee or Decimal("0")
        paid = self.paid_amount
        balance = due - paid
        return balance if balance > 0 else Decimal("0")

    @property
    def paid_amount(self):
        """
        Amount actually received, counting **successful payments only**.

        Pending, failed, cancelled and refunded rows are deliberately ignored:
        only ``ApplicationPaymentStatus.SUCCESSFUL`` is set after an explicit
        verification step, so this is the only value that can unlock a paid
        fee. Summing every row here would let a pending or failed payment mark
        the fee as settled and move the application into review.
        """
        from decimal import Decimal
        return sum(
            (payment.amount for payment in self.payments.all()
             if payment.status == ApplicationPaymentStatus.SUCCESSFUL),
            Decimal("0"),
        )

    @property
    def is_fee_paid(self):
        fee = self.application_fee
        if not fee:
            return True
        return self.paid_amount >= fee

    @property
    def editable_by_applicant(self):
        """
        Server-side guard: an applicant may only change an application while it
        is still a draft or has been sent back for correction.
        """
        from .workflow import ApplicationWorkflow
        return ApplicationWorkflow.is_applicant_editable(self)

    def checklist(self):
        """
        Full requirement checklist with per-item state and a completion
        percentage. See :mod:`portal.services` for the implementation.
        """
        from .services import ChecklistService
        return ChecklistService.build(self)

    @property
    def completion_percent(self):
        return self.checklist()["percent"]

    @property
    def missing_requirements(self):
        return self.checklist()["missing"]

    def save(self, *args, **kwargs):
        if not self.reference_number:
            self.reference_number = self._generate_ref()
        if not self.application_type_ref_id and self.application_type:
            # Keep the stored code and the configurable type record in sync
            # without ever renaming or retyping the existing column.
            resolved = ApplicationType.for_code(self.application_type)
            if resolved is not None:
                self.application_type_ref = resolved
        super().save(*args, **kwargs)

    def _generate_ref(self):
        import uuid
        year  = timezone.now().year
        short = str(uuid.uuid4()).replace("-", "").upper()[:8]
        return f"APP-{year}-{short}"

    # ── State transitions ─────────────────────────────────────────────────────

    def mark_reviewing(self, actor):
        """
        Admissions officer starts reviewing the application.

        Delegates to :class:`portal.workflow.ApplicationWorkflow` so the
        transition graph, the payment gate and the audit trail are applied
        exactly as they are for every other status change.
        """
        from .workflow import ApplicationWorkflow
        return ApplicationWorkflow.mark_reviewing(self, actor)


    @transaction.atomic
    def approve(self, actor, temp_password: str = None):
        """
        Backward-compatible wrapper: accept the application and issue an offer.

        .. deprecated::
            This method used to provision an active account *and* a
            StudentProfile with a student number at approval time. That made an
            applicant a student without an offer ever being issued or accepted,
            which contradicts requirement 7 (applicant is not a student until
            they accept). It now delegates to the unified engine:

                decision = Accepted  ->  AdmissionOffer issued

        The student record is created later by
        :meth:`portal.services.StudentConversionService.convert`, once the
        applicant has accepted the offer.

        The return value is unchanged (the applicant's ``EduProUser``) so any
        existing caller keeps working.
        """
        from .services import DecisionService, OfferService

        if self.status not in (
            AdmissionStatus.PENDING, AdmissionStatus.REVIEWING,
            AdmissionStatus.UNDER_REVIEW, AdmissionStatus.SHORTLISTED,
            AdmissionStatus.INTERVIEW_COMPLETED, AdmissionStatus.DECISION_PENDING,
            AdmissionStatus.SUBMITTED, AdmissionStatus.PAYMENT_CONFIRMED,
        ):
            raise ValidationError(
                _("Only PENDING or REVIEWING applications can be approved. "
                  "Current status: %(s)s.") % {"s": self.get_status_display()}
            )

        # Ensure an applicant account exists (legacy rows may predate it).
        if self.user_id is None:
            from django.contrib.auth import get_user_model
            UserModel = get_user_model()
            user = UserModel.objects.filter(email__iexact=self.email).first()
            if user is None:
                user = UserModel.objects.create_user(
                    email=self.email,
                    password=temp_password or UserModel.DEFAULT_PASSWORD,
                    first_name=self.first_name,
                    last_name=self.last_name,
                    role="student",
                    is_active=True,
                )
            self.user = user
            self.save(update_fields=["user"])

        DecisionService.record(
            self, AdmissionDecision.ACCEPTED, actor=actor,
            notes="Accepted via application.approve().",
        )
        OfferService.issue(self, actor=actor)
        return self.user

    def reject(self, actor, reason: str = ""):
        """
        Reject the application.

        Routed through the decision service so the decision field and the
        workflow status can never disagree, and the applicant is notified.
        """
        from .services import DecisionService
        DecisionService.record(
            self, AdmissionDecision.REJECTED, actor=actor, notes=reason,
        )
        self.rejection_reason = reason
        self.rejected_by = actor if (actor and actor.pk) else None
        self.rejected_at = timezone.now()
        self.save(update_fields=[
            "rejection_reason", "rejected_by", "rejected_at", "updated_at",
        ])
        return self

    def withdraw(self, actor=None):
        """Applicant withdraws their own application."""
        from .workflow import ApplicationWorkflow
        ApplicationWorkflow.withdraw(self, actor=actor)
        return self



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


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT MANAGEMENT (requirement 12)
# ─────────────────────────────────────────────────────────────────────────────

class ApplicationDocumentType(models.TextChoices):
    """
    Standard document slots. These are *codes* only — what an application is
    actually obliged to supply is decided by
    :class:`ApplicationRequirement`, so a programme can demand documents that
    are not listed here without any code change.
    """

    TRANSCRIPT          = "transcript",          _("Academic Transcript / Results Slip")
    ID_DOCUMENT         = "id_document",         _("ID / Passport")
    PASSPORT_PHOTO      = "passport_photo",      _("Passport Photograph")
    BIRTH_CERTIFICATE   = "birth_certificate",   _("Birth Certificate")
    WA_SSCE_SLIP        = "wa_ssce_slip",        _("WASSCE / SSSCE Slip")
    DEGREE_CERTIFICATE  = "degree_certificate",  _("Degree Certificate")
    CV                  = "cv",                  _("Curriculum Vitae")
    REFERENCE           = "reference",           _("Reference Letter")
    STATEMENT_OF_PURPOSE = "statement_of_purpose", _("Statement of Purpose")
    RESEARCH_INTEREST   = "research_interest",   _("Research Interest")
    OTHER               = "other",               _("Other Supporting Document")


class DocumentVerificationStatus(models.TextChoices):
    PENDING             = "pending",             _("Pending Verification")
    VERIFIED            = "verified",            _("Verified")
    REJECTED            = "rejected",            _("Rejected")
    REQUIRES_REPLACEMENT = "requires_replacement", _("Requires Replacement")


#: Hard ceiling enforced server-side on every applicant upload (requirement 22).
APPLICATION_DOCUMENT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
APPLICATION_DOCUMENT_EXTENSIONS = ["pdf", "jpg", "jpeg", "png"]


class ApplicationDocument(TimeStampedModel):
    """
    A single uploaded document belonging to an application, with its own
    verification lifecycle.

    A verified document is never silently mutated: replacing it resets the
    status to ``REQUIRES_REPLACEMENT``/``PENDING`` and clears the verifier, so
    staff always know the evidence they signed off is no longer current.
    """

    application = models.ForeignKey(
        AdmissionApplication, on_delete=models.CASCADE,
        related_name="uploaded_documents", verbose_name=_("application"),
    )
    document_type = models.CharField(
        _("document type"), max_length=40,
        choices=ApplicationDocumentType.choices,
        default=ApplicationDocumentType.OTHER,
    )
    label = models.CharField(_("label"), max_length=150, blank=True)
    file = models.FileField(
        _("file"), upload_to="admissions/applications/%Y/%m/",
        validators=[FileExtensionValidator(APPLICATION_DOCUMENT_EXTENSIONS)],
    )
    status = models.CharField(
        _("verification status"), max_length=25,
        choices=DocumentVerificationStatus.choices,
        default=DocumentVerificationStatus.PENDING,
        db_index=True,
    )
    uploaded_at = models.DateTimeField(_("uploaded at"), default=timezone.now)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="admission_documents_verified",
        verbose_name=_("verified by"),
    )
    verified_at = models.DateTimeField(_("verified at"), null=True, blank=True)
    rejection_reason = models.TextField(_("rejection reason"), blank=True)
    version = models.PositiveSmallIntegerField(
        _("version"), default=1,
        help_text=_("Increments each time the applicant supplies a replacement."),
    )

    class Meta:
        verbose_name        = _("application document")
        verbose_name_plural = _("application documents")
        ordering            = ["document_type", "-uploaded_at"]
        indexes = [models.Index(fields=["application", "document_type"])]

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.application.reference_number}"

    @property
    def name(self):
        return self.label or self.get_document_type_display()

    @property
    def is_verified(self):
        return self.status == DocumentVerificationStatus.VERIFIED

    @property
    def size_bytes(self):
        try:
            return self.file.size
        except (OSError, ValueError):
            return 0

    def mark_verified(self, actor):
        self.status     = DocumentVerificationStatus.VERIFIED
        self.verified_by = actor
        self.verified_at = timezone.now()
        self.rejection_reason = ""
        self.save(update_fields=[
            "status", "verified_by", "verified_at", "rejection_reason", "updated_at",
        ])

    def mark_rejected(self, actor, reason):
        self.status     = DocumentVerificationStatus.REJECTED
        self.verified_by = actor
        self.verified_at = timezone.now()
        self.rejection_reason = reason
        self.save(update_fields=[
            "status", "verified_by", "verified_at", "rejection_reason", "updated_at",
        ])

    def require_replacement(self, reason=""):
        """
        Applicant is being asked to supply a new copy. Any previous sign-off is
        revoked so staff can never act on a superseded document.
        """
        self.status     = DocumentVerificationStatus.REQUIRES_REPLACEMENT
        self.verified_by = None
        self.verified_at = None
        self.rejection_reason = reason
        self.save(update_fields=[
            "status", "verified_by", "verified_at", "rejection_reason", "updated_at",
        ])


# ─────────────────────────────────────────────────────────────────────────────
# APPLICATION PAYMENT (requirement 15)
# ─────────────────────────────────────────────────────────────────────────────

class ApplicationPaymentStatus(models.TextChoices):
    PENDING    = "pending",    _("Pending")
    SUCCESSFUL = "successful", _("Successful")
    FAILED     = "failed",     _("Failed")
    CANCELLED  = "cancelled",  _("Cancelled")
    REFUNDED   = "refunded",   _("Refunded")


class ApplicationPayment(TimeStampedModel):
    """
    Application-fee payment, kept separate from the tuition ledger in
    ``finance`` on purpose: an application fee is owed by an *applicant*, not
    by a student, and must be payable before any student record exists.

    A payment is only ever ``SUCCESSFUL`` after an explicit verification step
    (manual confirmation by finance, or a verified provider callback). Merely
    initialising a payment never marks the fee as paid.
    """

    application = models.ForeignKey(
        AdmissionApplication, on_delete=models.CASCADE,
        related_name="payments", verbose_name=_("application"),
    )
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)
    payment_reference = models.CharField(
        _("payment reference"), max_length=100, unique=True,
        help_text=_("Our reference for this payment attempt."),
    )
    provider = models.CharField(
        _("provider"), max_length=30, blank=True,
        help_text=_("e.g. paystack, cash, bank transfer, mobile money."),
    )
    transaction_id = models.CharField(
        _("provider transaction id"), max_length=120, blank=True, db_index=True,
    )
    status = models.CharField(
        _("status"), max_length=12,
        choices=ApplicationPaymentStatus.choices,
        default=ApplicationPaymentStatus.PENDING,
        db_index=True,
    )
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="application_payments_verified",
        verbose_name=_("verified by"),
    )
    verified_at = models.DateTimeField(_("verified at"), null=True, blank=True)
    failure_reason = models.TextField(_("failure reason"), blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name        = _("application payment")
        verbose_name_plural = _("application payments")
        ordering            = ["-created_at"]
        indexes = [models.Index(fields=["application", "status"])]

    def __str__(self):
        return f"{self.payment_reference} — {self.amount} ({self.get_status_display()})"

    @property
    def is_successful(self):
        return self.status == ApplicationPaymentStatus.SUCCESSFUL

    @staticmethod
    def generate_reference(application):
        """Unique, human-quotable reference, e.g. APP-PAY-2026-AB12CD34."""
        import uuid
        year = timezone.now().year
        return f"APP-PAY-{year}-{uuid.uuid4().hex[:8].upper()}"

    def mark_successful(self, actor=None, transaction_id=""):
        self.status = ApplicationPaymentStatus.SUCCESSFUL
        self.paid_at = timezone.now()
        self.verified_by = actor
        self.verified_at = timezone.now()
        if transaction_id:
            self.transaction_id = transaction_id
        self.failure_reason = ""
        self.save(update_fields=[
            "status", "paid_at", "verified_by", "verified_at",
            "transaction_id", "failure_reason", "updated_at",
        ])

    def mark_failed(self, reason=""):
        self.status = ApplicationPaymentStatus.FAILED
        self.failure_reason = reason
        self.save(update_fields=["status", "failure_reason", "updated_at"])


# ─────────────────────────────────────────────────────────────────────────────
# ADMISSION OFFER (requirement 8)
# ─────────────────────────────────────────────────────────────────────────────

class OfferStatus(models.TextChoices):
    ISSUED   = "issued",   _("Awaiting Response")
    ACCEPTED = "accepted", _("Accepted")
    DECLINED = "declined", _("Declined")
    EXPIRED  = "expired",  _("Expired")
    REVOKED  = "revoked",  _("Revoked")


class AdmissionOffer(models.Model):
    """
    A formal offer of admission issued to an applicant.

    An offer is the ONLY route to a student record. The applicant must accept
    it before :class:`~portal.services.StudentConversionService` will run, so
    "admitted" and "enrolled as a student" can never happen implicitly.
    """

    offer_number = models.CharField(
        _("offer number"), max_length=40, unique=True, blank=True,
    )
    application = models.ForeignKey(
        AdmissionApplication, on_delete=models.PROTECT,
        related_name="offers", verbose_name=_("application"),
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="admission_offers", verbose_name=_("applicant"),
    )
    program = models.ForeignKey(
        "academics.Program", on_delete=models.PROTECT,
        related_name="admission_offers", verbose_name=_("programme"),
    )
    academic_session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.PROTECT,
        related_name="admission_offers", verbose_name=_("academic session"),
    )
    admission_type = models.CharField(
        _("admission type"), max_length=30, default="full_time",
        help_text=_("e.g. full_time, part_time."),
    )
    issue_date = models.DateField(_("issue date"), default=timezone.localdate)
    expiry_date = models.DateField(_("expiry date"))
    status = models.CharField(
        _("status"), max_length=10,
        choices=OfferStatus.choices, default=OfferStatus.ISSUED,
        db_index=True,
    )
    acceptance_date = models.DateField(_("acceptance date"), null=True, blank=True)
    conditions = models.TextField(
        _("conditions"), blank=True,
        help_text=_("Conditions the applicant must satisfy, e.g. for a conditional offer."),
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="admission_offers_issued",
        verbose_name=_("issued by"),
    )
    responded_at = models.DateTimeField(_("responded at"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = _("admission offer")
        verbose_name_plural = _("admission offers")
        ordering            = ["-issue_date"]
        indexes = [models.Index(fields=["application", "status"])]

    def __str__(self):
        return f"{self.offer_number} — {self.applicant} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.offer_number:
            self.offer_number = self._generate_number()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_number():
        import uuid
        year = timezone.now().year
        return f"OFF-{year}-{uuid.uuid4().hex[:8].upper()}"

    @property
    def is_accepted(self):
        return self.status == OfferStatus.ACCEPTED

    @property
    def is_open(self):
        return self.status == OfferStatus.ISSUED and self.expiry_date >= timezone.localdate()

    @property
    def is_expired(self):
        return self.status == OfferStatus.ISSUED and self.expiry_date < timezone.localdate()

    @property
    def is_converted(self):
        return bool(self.application.converted_at)

    def mark_accepted(self):
        self.status = OfferStatus.ACCEPTED
        self.acceptance_date = timezone.localdate()
        self.responded_at = timezone.now()
        self.save(update_fields=[
            "status", "acceptance_date", "responded_at", "updated_at",
        ])

    def mark_declined(self):
        self.status = OfferStatus.DECLINED
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "responded_at", "updated_at"])

    def mark_expired(self):
        self.status = OfferStatus.EXPIRED
        self.save(update_fields=["status", "updated_at"])

    def mark_revoked(self):
        self.status = OfferStatus.REVOKED
        self.save(update_fields=["status", "updated_at"])


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT TRAIL (requirement 19)
# ─────────────────────────────────────────────────────────────────────────────

class AuditAction(models.TextChoices):
    CREATED            = "created",            _("Application created")
    UPDATED            = "updated",            _("Details updated")
    SUBMITTED          = "submitted",          _("Submitted")
    STATUS_CHANGED     = "status_changed",     _("Status changed")
    PAYMENT_RECORDED   = "payment_recorded",   _("Payment recorded")
    PAYMENT_CONFIRMED  = "payment_confirmed",  _("Payment confirmed")
    DOCUMENT_UPLOADED  = "document_uploaded",  _("Document uploaded")
    DOCUMENT_VERIFIED  = "document_verified",  _("Document verified")
    DOCUMENT_REJECTED  = "document_rejected",  _("Document rejected")
    CORRECTION_REQUESTED = "correction_requested", _("Correction requested")
    DECISION_RECORDED  = "decision_recorded",  _("Admission decision recorded")
    OFFER_ISSUED       = "offer_issued",       _("Offer issued")
    OFFER_ACCEPTED     = "offer_accepted",     _("Offer accepted")
    OFFER_DECLINED     = "offer_declined",     _("Offer declined")
    OFFER_EXPIRED      = "offer_expired",      _("Offer expired")
    CONVERTED          = "converted",          _("Converted to student")
    WITHDRAWN          = "withdrawn",          _("Withdrawn by applicant")


class ApplicationAuditLog(models.Model):
    """
    Append-only record of everything sensitive that happened to an
    application. Staff-only: the applicant-facing templates never render it.
    """

    application = models.ForeignKey(
        AdmissionApplication, on_delete=models.CASCADE,
        related_name="audit_logs", verbose_name=_("application"),
    )
    action = models.CharField(
        _("action"), max_length=30, choices=AuditAction.choices,
        db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="admission_audit_entries",
        verbose_name=_("actor"),
    )
    from_status = models.CharField(_("from status"), max_length=20, blank=True)
    to_status   = models.CharField(_("to status"), max_length=20, blank=True)
    remark = models.TextField(_("remark"), blank=True)
    # Applicants must never be shown internal remarks, so the applicant
    # timeline filters on this flag rather than on the action.
    is_visible_to_applicant = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name        = _("application audit entry")
        verbose_name_plural = _("application audit entries")
        ordering            = ["-created_at"]
        indexes = [models.Index(fields=["application", "-created_at"])]

    def __str__(self):
        return f"{self.get_action_display()} — {self.application.reference_number}"

    @property
    def actor_name(self):
        if not self.actor_id:
            return "System"
        return self.actor.get_full_name() or self.actor.email

