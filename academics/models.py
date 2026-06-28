"""
academics/models.py

Academic foundation for eduPro.  All original models retained and extended.

New in this version:
  ResultSheet   — carries department ownership, 4-stage approval status,
                  locked-after-approval, and full audit trail.

Approval flow:
  Teacher creates sheet (DRAFT)
    → teacher.submit()      → SUBMITTED
    → hod.approve()         → HOD_APPROVED   [HOD must match sheet.department]
    → admin.finalize()      → FINALIZED      [sheet is immutable]
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ─────────────────────────────────────────────────────────────────────────────
# MIXINS & MANAGERS  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


# ─────────────────────────────────────────────────────────────────────────────
# INSTITUTION  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class Institution(TimeStampedModel):
    name       = models.CharField(_("name"), max_length=200)
    short_name = models.CharField(_("short name / acronym"), max_length=20, blank=True)
    logo       = models.ImageField(_("logo"), upload_to="institution/logos/", blank=True, null=True)
    address    = models.TextField(_("address"), blank=True)
    website    = models.URLField(_("website"), blank=True)
    email      = models.EmailField(_("contact email"), blank=True)
    phone      = models.CharField(_("phone"), max_length=30, blank=True)
    motto      = models.CharField(_("motto"), max_length=255, blank=True)
    is_active  = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("institution")
        verbose_name_plural = _("institutions")
        ordering = ["name"]

    def __str__(self):
        return self.short_name or self.name


# ─────────────────────────────────────────────────────────────────────────────
# FACULTY  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class Faculty(TimeStampedModel):
    institution = models.ForeignKey(
        Institution, on_delete=models.CASCADE,
        related_name="faculties", verbose_name=_("institution"),
    )
    name        = models.CharField(_("name"), max_length=200)
    code        = models.CharField(_("code"), max_length=20, unique=True)
    dean        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="faculty_dean_of",
        verbose_name=_("dean"), limit_choices_to={"role": "teacher"},
    )
    description = models.TextField(_("description"), blank=True)
    is_active   = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("faculty")
        verbose_name_plural = _("faculties")
        ordering = ["name"]
        unique_together = [("institution", "code")]

    def __str__(self):
        return f"{self.code} — {self.name}"


# ─────────────────────────────────────────────────────────────────────────────
# DEPARTMENT  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class Department(TimeStampedModel):
    institution = models.ForeignKey(
        Institution, on_delete=models.CASCADE,
        related_name="departments", verbose_name=_("institution"),
    )
    faculty = models.ForeignKey(
        Faculty, on_delete=models.CASCADE,
        related_name="departments", verbose_name=_("faculty"),
    )
    name        = models.CharField(_("name"), max_length=200)
    code        = models.CharField(_("code"), max_length=20)
    hod         = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hod_of",
        verbose_name=_("head of department"),
        limit_choices_to={"role": "teacher"},
    )
    description = models.TextField(_("description"), blank=True)
    is_active   = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("department")
        verbose_name_plural = _("departments")
        ordering = ["faculty", "name"]
        unique_together = [("faculty", "code")]

    def __str__(self):
        return f"{self.code} — {self.name} ({self.faculty.code})"


# ─────────────────────────────────────────────────────────────────────────────
# PROGRAM  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class ProgramType(models.TextChoices):
    UNDERGRADUATE = "undergraduate", _("Undergraduate")
    POSTGRADUATE  = "postgraduate",  _("Postgraduate")
    DIPLOMA       = "diploma",       _("Diploma")
    CERTIFICATE   = "certificate",   _("Certificate")
    PROFESSIONAL  = "professional",  _("Professional")
    DOCTORATE     = "doctorate",     _("Doctorate")


class Program(TimeStampedModel):
    department     = models.ForeignKey(
        Department, on_delete=models.CASCADE,
        related_name="programs", verbose_name=_("department"),
    )
    name           = models.CharField(_("name"), max_length=200)
    code           = models.CharField(_("code"), max_length=20)
    program_type   = models.CharField(
        _("program type"), max_length=20,
        choices=ProgramType.choices, default=ProgramType.UNDERGRADUATE,
    )
    duration_years = models.PositiveSmallIntegerField(
        _("duration (years)"), default=4,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    total_credits  = models.PositiveSmallIntegerField(_("total credits required"), default=120)
    description    = models.TextField(_("description"), blank=True)
    is_active      = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("program")
        verbose_name_plural = _("programs")
        ordering = ["department", "name"]
        unique_together = [("department", "code")]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def faculty(self):
        return self.department.faculty

    @property
    def institution(self):
        return self.department.institution

    def get_starting_level(self):
        """
        Return the first (lowest) active Level for this program.
        For undergraduate programs this is typically "100".
        """
        return self.levels.filter(is_active=True).order_by("order").first()


# ─────────────────────────────────────────────────────────────────────────────
# ACADEMIC SESSION  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class AcademicSession(TimeStampedModel):
    name       = models.CharField(_("session name"), max_length=20, unique=True)
    start_date = models.DateField(_("start date"))
    end_date   = models.DateField(_("end date"))
    is_current = models.BooleanField(_("current session"), default=False)

    class Meta:
        verbose_name        = _("academic session")
        verbose_name_plural = _("academic sessions")
        ordering = ["-start_date"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_current:
            AcademicSession.objects.exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_current(cls):
        return cls.objects.filter(is_current=True).first()


# ─────────────────────────────────────────────────────────────────────────────
# SEMESTER  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class SemesterType(models.TextChoices):
    FIRST  = "first",  _("First Semester")
    SECOND = "second", _("Second Semester")
    SUMMER = "summer", _("Summer / Third Term")


class Semester(TimeStampedModel):
    session    = models.ForeignKey(
        AcademicSession, on_delete=models.CASCADE,
        related_name="semesters", verbose_name=_("academic session"),
    )
    name       = models.CharField(
        _("semester"), max_length=10,
        choices=SemesterType.choices, default=SemesterType.FIRST,
    )
    start_date = models.DateField(_("start date"))
    end_date   = models.DateField(_("end date"))
    is_current = models.BooleanField(_("current semester"), default=False)

    class Meta:
        verbose_name        = _("semester")
        verbose_name_plural = _("semesters")
        ordering = ["-session__start_date", "name"]
        unique_together = [("session", "name")]

    def __str__(self):
        return f"{self.get_name_display()} — {self.session.name}"

    def save(self, *args, **kwargs):
        if self.is_current:
            Semester.objects.exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_current(cls):
        return cls.objects.filter(is_current=True).first()


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class Level(TimeStampedModel):
    program   = models.ForeignKey(
        Program, on_delete=models.CASCADE,
        related_name="levels", verbose_name=_("program"),
    )
    name      = models.CharField(_("name"), max_length=50)
    order     = models.PositiveSmallIntegerField(_("order"), default=1)
    is_active = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("level")
        verbose_name_plural = _("levels")
        ordering = ["program", "order"]
        unique_together = [("program", "order")]

    def __str__(self):
        return f"{self.program.code} — {self.name}"


# ─────────────────────────────────────────────────────────────────────────────
# COURSE  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class CourseType(models.TextChoices):
    CORE       = "core",       _("Core / Compulsory")
    ELECTIVE   = "elective",   _("Elective")
    GENERAL    = "general",    _("General Education")
    LAB        = "lab",        _("Laboratory / Practical")
    PROJECT    = "project",    _("Project / Dissertation")
    INTERNSHIP = "internship", _("Internship / Industrial")


class Course(TimeStampedModel):
    department             = models.ForeignKey(
        Department, on_delete=models.CASCADE,
        related_name="courses", verbose_name=_("owning department"),
    )
    code                   = models.CharField(_("course code"), max_length=20, unique=True)
    title                  = models.CharField(_("title"), max_length=200)
    course_type            = models.CharField(
        _("type"), max_length=15,
        choices=CourseType.choices, default=CourseType.CORE,
    )
    credit_units           = models.PositiveSmallIntegerField(
        _("credit units"), default=3,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    lecture_hours_per_week = models.PositiveSmallIntegerField(_("lecture hours / week"), default=3)
    lab_hours_per_week     = models.PositiveSmallIntegerField(_("lab hours / week"), default=0)
    description            = models.TextField(_("description"), blank=True)
    prerequisites          = models.ManyToManyField(
        "self", symmetrical=False, blank=True,
        related_name="prerequisite_for", verbose_name=_("prerequisites"),
    )
    is_active = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("course")
        verbose_name_plural = _("courses")
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.title} ({self.credit_units} cr)"

    @property
    def total_hours_per_week(self):
        return self.lecture_hours_per_week + self.lab_hours_per_week


# ─────────────────────────────────────────────────────────────────────────────
# COURSE OFFERING  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class CourseOffering(TimeStampedModel):
    LEVEL_CHOICES = [
        ("100", "100"),
        ("200", "200"),
        ("300", "300"),
        ("400", "400"),
    ]

    course      = models.ForeignKey(
        Course, on_delete=models.CASCADE,
        related_name="offerings", verbose_name=_("course"),
    )
    semester    = models.ForeignKey(
        Semester, on_delete=models.CASCADE,
        related_name="offerings", verbose_name=_("semester"),
    )
    level       = models.ForeignKey(
        Level, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="offerings", verbose_name=_("level"),
    )
    level_name  = models.CharField(
        _("level"), max_length=3, choices=LEVEL_CHOICES,
        default="100",
    )
    departments = models.ManyToManyField(
        Department, related_name="offerings",
        verbose_name=_("departments"),
    )
    venue       = models.CharField(_("venue / room"), max_length=100, blank=True)
    max_students = models.PositiveSmallIntegerField(_("max enrolment"), default=50)
    is_active   = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("course offering")
        verbose_name_plural = _("course offerings")
        ordering = ["-semester__session__start_date", "course__code"]
        unique_together = [("course", "semester", "level_name")]

    def __str__(self):
        return f"{self.course.code} | Level {self.level_name} | {self.semester}"

    @property
    def session(self):
        return self.semester.session

    @property
    def enrolled_count(self):
        return self.enrolments.filter(is_active=True).count()


# ─────────────────────────────────────────────────────────────────────────────
# COURSE ALLOCATION  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class CourseAllocation(TimeStampedModel):
    class AllocationType(models.TextChoices):
        LECTURER  = "lecturer",  _("Lecturer")
        TUTOR     = "tutor",     _("Tutor / Lab Instructor")
        ASSISTANT = "assistant", _("Teaching Assistant")

    offering     = models.ForeignKey(
        CourseOffering, on_delete=models.CASCADE,
        related_name="allocations", verbose_name=_("course offering"),
    )
    teacher      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="course_allocations", verbose_name=_("teacher"),
        limit_choices_to={"role": "teacher"},
    )
    role         = models.CharField(
        _("allocation role"), max_length=15,
        choices=AllocationType.choices, default=AllocationType.LECTURER,
    )
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="allocations_made",
        verbose_name=_("allocated by"),
        limit_choices_to={"role": "admin"},
    )
    is_active = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("course allocation")
        verbose_name_plural = _("course allocations")
        ordering = ["-offering__semester__session__start_date", "teacher__last_name"]
        unique_together = [("offering", "teacher", "role")]

    def __str__(self):
        return (
            f"{self.teacher.get_full_name()} → {self.offering.course.code} "
            f"({self.get_role_display()}) | {self.offering.semester}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ENROLMENT  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class Enrolment(TimeStampedModel):
    class EnrolmentStatus(models.TextChoices):
        ACTIVE     = "active",     _("Active")
        DROPPED    = "dropped",    _("Dropped")
        INCOMPLETE = "incomplete", _("Incomplete")
        COMPLETED  = "completed",  _("Completed")

    student  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="enrolments", verbose_name=_("student"),
        limit_choices_to={"role": "student"},
    )
    offering = models.ForeignKey(
        CourseOffering, on_delete=models.CASCADE,
        related_name="enrolments", verbose_name=_("course offering"),
    )
    status      = models.CharField(
        _("status"), max_length=15,
        choices=EnrolmentStatus.choices, default=EnrolmentStatus.ACTIVE,
    )
    enrolled_at = models.DateTimeField(_("enrolled at"), default=timezone.now)
    is_active   = models.BooleanField(_("active"), default=True)
    is_retake   = models.BooleanField(_("retake"), default=False)
    original_enrolment = models.ForeignKey(
        "self", on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="retake_enrolments",
        verbose_name=_("original enrolment"),
    )

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("enrolment")
        verbose_name_plural = _("enrolments")
        ordering = ["-enrolled_at"]
        unique_together = [("student", "offering")]

    def __str__(self):
        return (
            f"{self.student.get_full_name()} ↔ {self.offering.course.code} "
            f"({self.offering.semester})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# STUDENT ACADEMIC PROFILE  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class StudentProfile(TimeStampedModel):
    student              = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="academic_profile", verbose_name=_("student"),
        limit_choices_to={"role": "student"},
    )
    program              = models.ForeignKey(
        Program, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="students", verbose_name=_("program"),
    )
    current_level        = models.ForeignKey(
        Level, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="current_students",
        verbose_name=_("current level"),
    )
    student_number       = models.CharField(_("student ID number"), max_length=30, unique=True, blank=True)
    admission_date       = models.DateField(_("admission date"), null=True, blank=True)
    expected_graduation  = models.DateField(_("expected graduation"), null=True, blank=True)
    cumulative_gpa       = models.DecimalField(
        _("cumulative GPA"), max_digits=4, decimal_places=2,
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(4)],
    )
    total_credits_earned = models.PositiveSmallIntegerField(_("total credits earned"), default=0)
    is_active            = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("student academic profile")
        verbose_name_plural = _("student academic profiles")
        ordering = ["student__last_name", "student__first_name"]

    def __str__(self):
        return (
            f"{self.student.get_full_name()} "
            f"[{self.student_number or 'No ID'}] "
            f"— {self.program.code if self.program else 'Unassigned'}"
        )

    def check_graduation_eligibility(self):
        """
        Check whether this student can graduate based on outstanding failed courses.

        A student is eligible if every course they failed (grade F, I, or W) has
        been successfully retaken (retake enrolment with a non-failing grade).

        Returns a dict:
            eligible                     – bool
            failed_without_retake        – list of enrolments (failed, no retake found)
            failed_with_passed_retake    – list of (original, retake) pairs
            failed_with_failed_retake    – list of (original, retake) pairs
        """
        failed_originals = self.student.enrolments.filter(
            is_active=True,
            is_retake=False,
            result__grade__in=("F", "I", "W"),
        ).select_related("offering__course", "result")

        failed_no_retake = []
        passed_retake = []
        failed_retake = []

        for enr in failed_originals:
            retakes = enr.retake_enrolments.filter(
                student=self.student,
                is_active=True,
            ).select_related("result")

            best = None  # ("passed", retake) | ("failed", retake) | None
            for r in retakes:
                if not hasattr(r, "result") or not r.result:
                    continue
                if r.result.grade not in ("F", "I", "W"):
                    best = ("passed", r)
                    break
                if best is None:
                    best = ("failed", r)

            if best is None:
                failed_no_retake.append(enr)
            elif best[0] == "passed":
                passed_retake.append((enr, best[1]))
            else:
                failed_retake.append((enr, best[1]))

        return {
            "eligible": not failed_no_retake and not failed_retake,
            "failed_without_retake": failed_no_retake,
            "failed_with_passed_retake": passed_retake,
            "failed_with_failed_retake": failed_retake,
        }


# ─────────────────────────────────────────────────────────────────────────────
# TEACHER DEPARTMENT MEMBERSHIP  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class TeacherDepartment(TimeStampedModel):
    teacher    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="department_memberships", verbose_name=_("teacher"),
        limit_choices_to={"role": "teacher"},
    )
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE,
        related_name="teacher_memberships", verbose_name=_("department"),
    )
    is_primary  = models.BooleanField(_("primary department"), default=True)
    joined_date = models.DateField(_("joined"), null=True, blank=True)
    is_active   = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("teacher–department link")
        verbose_name_plural = _("teacher–department links")
        ordering = ["teacher__last_name", "-is_primary"]
        unique_together = [("teacher", "department")]

    def __str__(self):
        flag = " (primary)" if self.is_primary else ""
        return f"{self.teacher.get_full_name()} → {self.department.code}{flag}"


# ─────────────────────────────────────────────────────────────────────────────
# RESULT SHEET  ← NEW
# ─────────────────────────────────────────────────────────────────────────────

class ResultSheet(TimeStampedModel):
    """
    Represents a teacher's result submission for a CourseOffering.

    Ownership & authority:
        department  — derived from the course's department; used to scope
                      HOD approval checks.
        submitted_by — the teacher (must hold a CourseAllocation for this offering).

    Approval flow:
        DRAFT → SUBMITTED → HOD_APPROVED → FINALIZED

    Lock rule: any status >= HOD_APPROVED makes the sheet read-only.
    """

    class Status(models.TextChoices):
        DRAFT        = "draft",        _("Draft")
        SUBMITTED    = "submitted",    _("Submitted")
        HOD_APPROVED = "hod_approved", _("HOD Approved")
        FINALIZED    = "finalized",    _("Finalized")

    # ── Core FK fields ───────────────────────────────────────────────────────
    offering     = models.ForeignKey(
        CourseOffering, on_delete=models.CASCADE,
        related_name="result_sheets", verbose_name=_("course offering"),
    )
    department   = models.ForeignKey(
        Department, on_delete=models.PROTECT,
        related_name="result_sheets", verbose_name=_("department"),
        help_text=_("Copied from the course's owning department on creation."),
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="academics_result_sheets_submitted",   # namespaced: avoids clash with teachers.ResultSheet
        verbose_name=_("submitted by"),
        limit_choices_to={"role": "teacher"},
    )

    # ── Status ───────────────────────────────────────────────────────────────
    status = models.CharField(
        _("status"), max_length=20,
        choices=Status.choices, default=Status.DRAFT,
        db_index=True,
    )

    # ── Audit trail ──────────────────────────────────────────────────────────
    submitted_at    = models.DateTimeField(_("submitted at"),    null=True, blank=True)
    hod_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="academics_hod_approvals",   # namespaced
        verbose_name=_("HOD approved by"),
    )
    hod_approved_at = models.DateTimeField(_("HOD approved at"), null=True, blank=True)
    finalized_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="academics_finalized_sheets",   # namespaced
        verbose_name=_("finalized by"),
    )
    finalized_at    = models.DateTimeField(_("finalized at"),    null=True, blank=True)

    # ── Optional free-text notes ─────────────────────────────────────────────
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name        = _("result sheet")
        verbose_name_plural = _("result sheets")
        ordering = ["-created_at"]
        unique_together = [("offering", "submitted_by")]

    def __str__(self):
        return (
            f"Results: {self.offering.course.code} | "
            f"{self.offering.semester} | "
            f"{self.get_status_display()}"
        )

    # ── Computed properties ──────────────────────────────────────────────────

    @property
    def is_locked(self) -> bool:
        """Sheet is read-only once HOD has approved or admin has finalized."""
        return self.status in (self.Status.HOD_APPROVED, self.Status.FINALIZED)

    @property
    def is_editable(self) -> bool:
        return not self.is_locked

    # ── State-transition methods ─────────────────────────────────────────────

    def submit(self, actor):
        """
        Teacher submits the sheet for HOD review.
        Allowed only from DRAFT status.
        actor must hold a CourseAllocation for this offering.
        """
        if self.status != self.Status.DRAFT:
            raise ValidationError(
                _("Only DRAFT sheets can be submitted. Current status: %(s)s.")
                % {"s": self.get_status_display()}
            )
        self._assert_teacher_owns(actor)
        self.status       = self.Status.SUBMITTED
        self.submitted_at = timezone.now()
        self.save(update_fields=["status", "submitted_at", "updated_at"])

    def hod_approve(self, actor):
        """
        HOD approves the sheet.
        Requires:
          1. sheet is SUBMITTED
          2. actor holds StaffResponsibility.HOD for sheet.department
        """
        if self.status != self.Status.SUBMITTED:
            raise ValidationError(
                _("Only SUBMITTED sheets can be HOD-approved. Current status: %(s)s.")
                % {"s": self.get_status_display()}
            )
        self._assert_hod_authority(actor)
        self.status          = self.Status.HOD_APPROVED
        self.hod_approved_by = actor
        self.hod_approved_at = timezone.now()
        self.save(update_fields=[
            "status", "hod_approved_by", "hod_approved_at", "updated_at"
        ])

    def finalize(self, actor):
        """
        Admin finalizes the sheet — immutable after this.
        Requires sheet to be HOD_APPROVED and actor to be admin/superuser.
        """
        if self.status != self.Status.HOD_APPROVED:
            raise ValidationError(
                _("Only HOD_APPROVED sheets can be finalized. Current status: %(s)s.")
                % {"s": self.get_status_display()}
            )
        from accounts.models import Role
        if not (actor.is_superuser or actor.role == Role.ADMIN):
            raise ValidationError(_("Only administrators can finalize result sheets."))
        self.status       = self.Status.FINALIZED
        self.finalized_by = actor
        self.finalized_at = timezone.now()
        self.save(update_fields=[
            "status", "finalized_by", "finalized_at", "updated_at"
        ])

    def revert_to_draft(self, actor):
        """
        Admin or HOD can revert a SUBMITTED sheet back to DRAFT
        (e.g. to allow the teacher to correct errors).
        Cannot revert beyond SUBMITTED.
        """
        if self.status != self.Status.SUBMITTED:
            raise ValidationError(
                _("Only SUBMITTED sheets can be reverted. Current status: %(s)s.")
                % {"s": self.get_status_display()}
            )
        from accounts.models import Role, StaffResponsibility
        is_admin = actor.is_superuser or actor.role == Role.ADMIN
        is_hod   = actor.is_hod_of(self.department)
        if not (is_admin or is_hod):
            raise ValidationError(
                _("Only the HOD of this department or an admin can revert a sheet.")
            )
        self.status = self.Status.DRAFT
        self.submitted_at = None
        self.save(update_fields=["status", "submitted_at", "updated_at"])

    # ── Internal guards ──────────────────────────────────────────────────────

    def _assert_teacher_owns(self, actor):
        """Raise ValidationError if actor has no allocation for this offering."""
        if not self.offering.allocations.filter(teacher=actor, is_active=True).exists():
            raise ValidationError(
                _(
                    "You are not allocated to teach %(course)s and cannot "
                    "submit results for it."
                ) % {"course": self.offering.course.code}
            )

    def _assert_hod_authority(self, actor):
        """Raise ValidationError if actor is not HOD of this sheet's department."""
        if not actor.is_hod_of(self.department):
            raise ValidationError(
                _(
                    "You are not the Head of Department for %(dept)s and cannot "
                    "approve results for it."
                ) % {"dept": self.department.name}
            )

    # ── Class-level factory ──────────────────────────────────────────────────

    @classmethod
    def create_for_offering(cls, offering, submitted_by):
        """
        Convenience factory.  Automatically sets department from the offering's
        course and guards against duplicate sheets.
        """
        department = offering.course.department
        sheet, created = cls.objects.get_or_create(
            offering=offering,
            submitted_by=submitted_by,
            defaults={
                "department": department,
                "status": cls.Status.DRAFT,
            },
        )
        if not created and sheet.is_locked:
            raise ValidationError(
                _("A locked result sheet already exists for this offering.")
            )
        return sheet, created
