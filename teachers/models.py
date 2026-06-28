"""
teachers/models.py

Teacher domain for eduPro.

Hierarchy of ownership for academic resources:
  EduProUser (role=teacher)
    └── TeacherProfile          — bio, rank, qualifications
    └── CourseAllocation        — (academics) what they teach
          └── LectureMaterial   — uploaded files per offering
          └── Assignment        — created assignments
          └── Quiz              — CBT quizzes
          └── AttendanceSheet   — per offering, per date
                └── AttendanceRecord — per student
          └── ResultSheet       — per offering per session
                └── StudentResult — per enrolled student

Result structure supports:
  - Continuous Assessment (CA) with configurable sub-scores
  - Exam score
  - Total / Grade / Grade Point
  - GPA computation at Enrolment and CGPA at StudentProfile level
  - Transcript generation (reads ResultSheet → StudentResult)

FK targets:
  - settings.AUTH_USER_MODEL  (accounts.EduProUser)
  - academics.CourseOffering
  - academics.CourseAllocation
  - academics.Enrolment
  - academics.Department
"""

import os
import uuid

from django.conf import settings
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


def material_upload_path(instance, filename):
    ext = filename.split(".")[-1]
    unique = uuid.uuid4().hex[:10]
    return f"teacher_materials/{instance.allocation.teacher.pk}/{unique}.{ext}"


def assignment_upload_path(instance, filename):
    ext = filename.split(".")[-1]
    unique = uuid.uuid4().hex[:10]
    return f"assignments/{instance.offering.course.code}/{unique}.{ext}"


def submission_upload_path(instance, filename):
    ext = filename.split(".")[-1]
    unique = uuid.uuid4().hex[:10]
    return f"submissions/{instance.assignment.pk}/{unique}.{ext}"


# ─────────────────────────────────────────────────────────────────────────────
# TEACHER PROFILE
# ─────────────────────────────────────────────────────────────────────────────

class AcademicRank(models.TextChoices):
    PROFESSOR        = "professor",        _("Professor")
    ASSOC_PROFESSOR  = "assoc_professor",  _("Associate Professor")
    ASST_PROFESSOR   = "asst_professor",   _("Assistant Professor")
    SENIOR_LECTURER  = "senior_lecturer",  _("Senior Lecturer")
    LECTURER         = "lecturer",         _("Lecturer")
    ASST_LECTURER    = "asst_lecturer",    _("Assistant Lecturer")
    TEACHING_ASST    = "teaching_asst",    _("Teaching Assistant")
    ADJUNCT          = "adjunct",          _("Adjunct Lecturer")


class EmploymentType(models.TextChoices):
    FULL_TIME  = "full_time",  _("Full-Time")
    PART_TIME  = "part_time",  _("Part-Time")
    ADJUNCT    = "adjunct",    _("Adjunct")
    VISITING   = "visiting",   _("Visiting")
    CONTRACT   = "contract",   _("Contract")


class TeacherProfile(TimeStampedModel):
    """
    Extended academic profile for teachers.
    Complements accounts.UserProfile with rank, qualifications, and settings.
    OneToOne with EduProUser (role=teacher).

    Future hooks:
    - analytics.TeacherPerformance → FK to TeacherProfile
    - payroll → FK to TeacherProfile
    """
    teacher         = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="teacher_profile",
        verbose_name=_("teacher"),
        limit_choices_to={"role": "teacher"},
    )
    staff_id        = models.CharField(
        _("staff ID"), max_length=30, unique=True, blank=True,
    )
    rank            = models.CharField(
        _("academic rank"), max_length=20,
        choices=AcademicRank.choices,
        default=AcademicRank.LECTURER,
    )
    employment_type = models.CharField(
        _("employment type"), max_length=15,
        choices=EmploymentType.choices,
        default=EmploymentType.FULL_TIME,
    )
    specialization  = models.CharField(
        _("specialization / research area"), max_length=200, blank=True,
    )
    highest_qualification = models.CharField(
        _("highest qualification"), max_length=100, blank=True,
        help_text=_("e.g. PhD Computer Science, MSc Mathematics"),
    )
    office_location = models.CharField(_("office location"), max_length=100, blank=True)
    office_hours    = models.CharField(
        _("office hours"), max_length=200, blank=True,
        help_text=_("e.g. Mon/Wed 2pm–4pm"),
    )
    joined_date     = models.DateField(_("date of employment"), null=True, blank=True)
    # Grading authority scope — used by results ownership checks
    can_submit_results   = models.BooleanField(_("can submit results"), default=True)
    can_finalise_results = models.BooleanField(
        _("can finalise results"), default=False,
        help_text=_("Only HODs/Exams Officers should have this."),
    )
    is_active       = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("teacher profile")
        verbose_name_plural = _("teacher profiles")
        ordering            = ["teacher__last_name", "teacher__first_name"]

    def __str__(self):
        return (
            f"{self.teacher.get_full_name()} "
            f"[{self.get_rank_display()}] "
            f"({self.staff_id or 'no ID'})"
        )

    def generate_staff_id(self):
        """
        Generate unique teacher staff ID.
        Example:
            TCH-8F21A7BC
        """
        return f"TCH-{uuid.uuid4().hex[:8].upper()}"


    def save(self, *args, **kwargs):
        """
        Auto-generate unique staff ID if missing.
        """
        if not self.staff_id:
            while True:
                new_id = self.generate_staff_id()

                if not TeacherProfile.all_objects.filter(
                    staff_id=new_id
                ).exists():
                    self.staff_id = new_id
                    break

        super().save(*args, **kwargs)
        

    def get_active_allocations(self):
        """Return active CourseAllocations for this teacher."""
        from academics.models import CourseAllocation
        return CourseAllocation.objects.filter(
            teacher=self.teacher, is_active=True
        ).select_related("offering__course", "offering__semester__session", "offering__level")

    def get_current_semester_allocations(self):
        from academics.models import CourseAllocation, Semester
        current = Semester.get_current()
        if not current:
            return CourseAllocation.objects.none()
        return CourseAllocation.objects.filter(
            teacher=self.teacher, is_active=True, offering__semester=current
        ).select_related("offering__course", "offering__level")


# ─────────────────────────────────────────────────────────────────────────────
# LECTURE MATERIAL
# ─────────────────────────────────────────────────────────────────────────────

class MaterialType(models.TextChoices):
    LECTURE_NOTE  = "lecture_note",  _("Lecture Note / Slides")
    REFERENCE     = "reference",     _("Reference Material")
    PAST_QUESTION = "past_question", _("Past Question / Exam Paper")
    VIDEO_LINK    = "video_link",    _("Video / External Link")
    ASSIGNMENT    = "assignment",    _("Assignment Brief")
    OTHER         = "other",         _("Other")


ALLOWED_MATERIAL_EXTENSIONS = [
    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx",
    "txt", "zip", "mp4", "png", "jpg", "jpeg",
]


class LectureMaterial(TimeStampedModel):
    """
    Files or links uploaded by a teacher for a specific CourseOffering.
    Tied to CourseAllocation to enforce teacher ownership.

    Future hook: lms.MaterialView → FK to LectureMaterial (track who viewed it).
    """
    allocation    = models.ForeignKey(
        "academics.CourseAllocation",
        on_delete=models.CASCADE,
        related_name="materials",
        verbose_name=_("course allocation"),
    )
    title         = models.CharField(_("title"), max_length=200)
    material_type = models.CharField(
        _("type"), max_length=15,
        choices=MaterialType.choices,
        default=MaterialType.LECTURE_NOTE,
    )
    description   = models.TextField(_("description"), blank=True)
    file          = models.FileField(
        _("file"),
        upload_to=material_upload_path,
        blank=True, null=True,
        validators=[FileExtensionValidator(ALLOWED_MATERIAL_EXTENSIONS)],
    )
    external_url  = models.URLField(
        _("external URL / video link"), blank=True,
        help_text=_("Fill this OR upload a file, not both."),
    )
    week_number   = models.PositiveSmallIntegerField(
        _("week number"), null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(52)],
    )
    is_published  = models.BooleanField(_("published to students"), default=True)
    is_active     = models.BooleanField(_("active"), default=True)
    download_count = models.PositiveIntegerField(_("download count"), default=0)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("lecture material")
        verbose_name_plural = _("lecture materials")
        ordering            = ["week_number", "-created_at"]

    def __str__(self):
        return f"{self.title} — {self.allocation.offering.course.code}"

    @property
    def offering(self):
        return self.allocation.offering

    @property
    def teacher(self):
        return self.allocation.teacher

    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.file and not self.external_url:
            raise ValidationError(_("Provide either a file or an external URL."))
        if self.file and self.external_url:
            raise ValidationError(_("Provide either a file or a URL, not both."))


# ─────────────────────────────────────────────────────────────────────────────
# ASSIGNMENT
# ─────────────────────────────────────────────────────────────────────────────

class AssignmentStatus(models.TextChoices):
    DRAFT     = "draft",     _("Draft")
    PUBLISHED = "published", _("Published")
    CLOSED    = "closed",    _("Closed")
    GRADED    = "graded",    _("Graded")


class Assignment(TimeStampedModel):
    """
    Assignment created by a teacher for a specific CourseOffering.
    Each enrolled student will have an AssignmentSubmission.

    Future hook: results.CAScore → can pull assignment score into CA breakdown.
    """
    offering      = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name=_("course offering"),
    )
    created_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assignments_created",
        verbose_name=_("created by"),
        limit_choices_to={"role": "teacher"},
    )
    title         = models.CharField(_("title"), max_length=200)
    instructions  = models.TextField(_("instructions"))
    attachment    = models.FileField(
        _("attachment"), upload_to=assignment_upload_path,
        blank=True, null=True,
        validators=[FileExtensionValidator(ALLOWED_MATERIAL_EXTENSIONS)],
    )
    total_marks   = models.PositiveSmallIntegerField(
        _("total marks"), default=100,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
    )
    due_date      = models.DateTimeField(_("due date"))
    status        = models.CharField(
        _("status"), max_length=10,
        choices=AssignmentStatus.choices,
        default=AssignmentStatus.DRAFT,
    )
    allow_late    = models.BooleanField(_("allow late submissions"), default=False)
    is_active     = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("assignment")
        verbose_name_plural = _("assignments")
        ordering            = ["-due_date"]

    def __str__(self):
        return f"{self.title} — {self.offering.course.code}"

    @property
    def is_overdue(self):
        return timezone.now() > self.due_date

    @property
    def submission_count(self):
        return self.submissions.filter(is_active=True).count()


class AssignmentSubmission(TimeStampedModel):
    """
    A student's submission for an Assignment.
    Score feeds into CA result computation.
    """
    assignment  = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name=_("assignment"),
    )
    student     = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assignment_submissions",
        verbose_name=_("student"),
        limit_choices_to={"role": "student"},
    )
    file        = models.FileField(
        _("submission file"),
        upload_to=submission_upload_path,
        blank=True, null=True,
        validators=[FileExtensionValidator(ALLOWED_MATERIAL_EXTENSIONS)],
    )
    text_answer = models.TextField(_("text answer"), blank=True)
    submitted_at = models.DateTimeField(_("submitted at"), default=timezone.now)
    is_late     = models.BooleanField(_("late submission"), default=False)
    # Grading
    score       = models.DecimalField(
        _("score"), max_digits=6, decimal_places=2,
        null=True, blank=True,
    )
    feedback    = models.TextField(_("feedback / comments"), blank=True)
    graded_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="submissions_graded",
        verbose_name=_("graded by"),
    )
    graded_at   = models.DateTimeField(_("graded at"), null=True, blank=True)
    is_active   = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("assignment submission")
        verbose_name_plural = _("assignment submissions")
        unique_together     = [("assignment", "student")]
        ordering            = ["-submitted_at"]

    def __str__(self):
        return f"{self.student.get_full_name()} → {self.assignment.title}"


# ─────────────────────────────────────────────────────────────────────────────
# QUIZ / CBT
# ─────────────────────────────────────────────────────────────────────────────

class QuizType(models.TextChoices):
    QUIZ        = "quiz",        _("Quiz")
    CBT_EXAM    = "cbt_exam",    _("CBT Examination")
    PRACTICE    = "practice",    _("Practice / Self-Assessment")
    CA_TEST     = "ca_test",     _("Continuous Assessment Test")


class Quiz(TimeStampedModel):
    """
    An online quiz or CBT exam owned by a teacher for a CourseOffering.
    Questions are QuizQuestion; student attempts are QuizAttempt.
    """
    offering      = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.CASCADE,
        related_name="quizzes",
        verbose_name=_("course offering"),
    )
    created_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quizzes_created",
        verbose_name=_("created by"),
        limit_choices_to={"role": "teacher"},
    )
    title         = models.CharField(_("title"), max_length=200)
    instructions  = models.TextField(_("instructions"), blank=True)
    quiz_type     = models.CharField(
        _("quiz type"), max_length=10,
        choices=QuizType.choices,
        default=QuizType.QUIZ,
    )
    total_marks   = models.PositiveSmallIntegerField(
        _("total marks"), default=100,
    )
    duration_minutes = models.PositiveSmallIntegerField(
        _("duration (minutes)"), default=60,
        validators=[MinValueValidator(1), MaxValueValidator(480)],
    )
    start_datetime = models.DateTimeField(_("opens at"), null=True, blank=True)
    end_datetime   = models.DateTimeField(_("closes at"), null=True, blank=True)
    randomise_questions = models.BooleanField(_("randomise question order"), default=True)
    show_result_immediately = models.BooleanField(
        _("show result immediately after submission"), default=False,
    )
    max_attempts  = models.PositiveSmallIntegerField(
        _("max attempts per student"), default=1,
    )
    is_published  = models.BooleanField(_("published"), default=False)
    is_active     = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("quiz")
        verbose_name_plural = _("quizzes")
        ordering            = ["-start_datetime"]

    def __str__(self):
        return f"{self.title} — {self.offering.course.code}"

    @property
    def question_count(self):
        return self.questions.count()

    @property
    def is_open(self):
        now = timezone.now()
        if self.start_datetime and now < self.start_datetime:
            return False
        if self.end_datetime and now > self.end_datetime:
            return False
        return self.is_published


class QuestionType(models.TextChoices):
    MCQ       = "mcq",       _("Multiple Choice (single answer)")
    MULTI     = "multi",     _("Multiple Choice (multiple answers)")
    TRUE_FALSE = "true_false", _("True / False")
    SHORT     = "short",     _("Short Answer")


class QuizQuestion(TimeStampedModel):
    """A single question belonging to a Quiz."""
    quiz         = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name=_("quiz"),
    )
    text         = models.TextField(_("question text"))
    question_type = models.CharField(
        _("type"), max_length=10,
        choices=QuestionType.choices,
        default=QuestionType.MCQ,
    )
    marks        = models.PositiveSmallIntegerField(
        _("marks"), default=1,
        validators=[MinValueValidator(1)],
    )
    order        = models.PositiveSmallIntegerField(_("order"), default=1)
    explanation  = models.TextField(_("answer explanation"), blank=True)

    class Meta:
        verbose_name        = _("quiz question")
        verbose_name_plural = _("quiz questions")
        ordering            = ["order"]

    def __str__(self):
        return f"Q{self.order}: {self.text[:60]}"


class QuizChoice(models.Model):
    """Answer choices for MCQ / True-False questions."""
    question   = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name="choices",
    )
    text       = models.CharField(_("choice text"), max_length=500)
    is_correct = models.BooleanField(_("correct answer"), default=False)
    order      = models.PositiveSmallIntegerField(_("order"), default=1)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{'✓' if self.is_correct else '✗'} {self.text[:50]}"


class QuizAttempt(TimeStampedModel):
    """
    Records a student's attempt at a Quiz.
    Individual answers stored in QuizAnswer.
    Score feeds into CA result computation.
    """
    quiz        = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="attempts",
        verbose_name=_("quiz"),
    )
    student     = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quiz_attempts",
        verbose_name=_("student"),
        limit_choices_to={"role": "student"},
    )
    attempt_number = models.PositiveSmallIntegerField(_("attempt #"), default=1)
    started_at  = models.DateTimeField(_("started at"), default=timezone.now)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    score       = models.DecimalField(
        _("score"), max_digits=6, decimal_places=2,
        null=True, blank=True,
    )
    is_complete = models.BooleanField(_("completed"), default=False)

    class Meta:
        verbose_name        = _("quiz attempt")
        verbose_name_plural = _("quiz attempts")
        unique_together     = [("quiz", "student", "attempt_number")]
        ordering            = ["-started_at"]

    def __str__(self):
        return f"{self.student.get_full_name()} — {self.quiz.title} (#{self.attempt_number})"


class QuizAnswer(models.Model):
    """Student's answer to a single QuizQuestion within an attempt."""
    attempt   = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name="answers")
    question  = models.ForeignKey(QuizQuestion, on_delete=models.CASCADE, related_name="student_answers")
    selected_choices = models.ManyToManyField(QuizChoice, blank=True, related_name="selected_in")
    text_answer = models.TextField(blank=True)
    is_correct  = models.BooleanField(null=True, blank=True)
    marks_awarded = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        unique_together = [("attempt", "question")]


# ─────────────────────────────────────────────────────────────────────────────
# ATTENDANCE
# ─────────────────────────────────────────────────────────────────────────────

class AttendanceSheet(TimeStampedModel):
    """
    A single attendance session (date + class) for a CourseOffering.
    Created by the teacher managing the allocation.

    Future hook: analytics.AttendanceSummary → aggregates per student.
    """
    offering    = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.CASCADE,
        related_name="attendance_sheets",
        verbose_name=_("course offering"),
    )
    taken_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="attendance_sheets_taken",
        verbose_name=_("taken by"),
        limit_choices_to={"role": "teacher"},
    )
    date        = models.DateField(_("date"))
    week_number = models.PositiveSmallIntegerField(
        _("week"), null=True, blank=True,
    )
    topic_covered = models.CharField(_("topic covered"), max_length=300, blank=True)
    notes       = models.TextField(_("notes"), blank=True)
    is_active   = models.BooleanField(_("active"), default=True)

    objects     = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name        = _("attendance sheet")
        verbose_name_plural = _("attendance sheets")
        ordering            = ["-date"]
        unique_together     = [("offering", "date")]

    def __str__(self):
        return f"{self.offering.course.code} — {self.date}"

    @property
    def present_count(self):
        return self.records.filter(status=AttendanceRecord.Status.PRESENT).count()

    @property
    def total_count(self):
        return self.records.count()


class AttendanceRecord(TimeStampedModel):
    """
    Individual student attendance record within an AttendanceSheet.
    """
    class Status(models.TextChoices):
        PRESENT    = "present",    _("Present")
        ABSENT     = "absent",     _("Absent")
        EXCUSED    = "excused",    _("Excused Absence")
        LATE       = "late",       _("Late")

    sheet   = models.ForeignKey(
        AttendanceSheet,
        on_delete=models.CASCADE,
        related_name="records",
        verbose_name=_("attendance sheet"),
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendance_records",
        verbose_name=_("student"),
        limit_choices_to={"role": "student"},
    )
    status  = models.CharField(
        _("status"), max_length=10,
        choices=Status.choices,
        default=Status.PRESENT,
    )
    remark  = models.CharField(_("remark"), max_length=200, blank=True)

    class Meta:
        verbose_name        = _("attendance record")
        verbose_name_plural = _("attendance records")
        unique_together     = [("sheet", "student")]
        ordering            = ["student__last_name"]

    def __str__(self):
        return f"{self.student.get_full_name()} — {self.sheet.date} — {self.get_status_display()}"


# ─────────────────────────────────────────────────────────────────────────────
# RESULTS / GRADING
# ─────────────────────────────────────────────────────────────────────────────

class GradingScheme(models.TextChoices):
    """
    Configurable grading scales.
    STANDARD is the 100-point scale producing letter grades.
    Future: institution-specific schemes can be added.
    """
    STANDARD   = "standard",   _("Standard (A–F, 100pt)")
    PERCENTAGE = "percentage", _("Percentage Only")
    PASS_FAIL  = "pass_fail",  _("Pass / Fail")


class ResultSheet(TimeStampedModel):
    """
    Master result record for a CourseOffering in a Semester.
    Created once per offering; teacher submits StudentResults against it.

    Ownership: teacher who holds the CourseAllocation submits results.
    Approval:  teacher with can_finalise_results=True (HOD / Exams Officer) locks it.

    Future hooks:
    - results.Transcript → reads ResultSheet → StudentResult
    - analytics → reads StudentResult for GPA computation
    """

    class SheetStatus(models.TextChoices):
        OPEN        = "open",        _("Open — accepting entries")
        SUBMITTED   = "submitted",   _("Submitted — awaiting approval")
        APPROVED    = "approved",    _("Approved — locked")
        REJECTED    = "rejected",    _("Rejected — returned for correction")

    offering        = models.OneToOneField(
        "academics.CourseOffering",
        on_delete=models.CASCADE,
        related_name="result_sheet",
        verbose_name=_("course offering"),
    )
    submitted_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="result_sheets_submitted",
        verbose_name=_("submitted by"),
    )
    approved_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="result_sheets_approved",
        verbose_name=_("approved by"),
    )
    grading_scheme  = models.CharField(
        _("grading scheme"), max_length=12,
        choices=GradingScheme.choices,
        default=GradingScheme.STANDARD,
    )
    ca_weight       = models.PositiveSmallIntegerField(
        _("CA weight (%)"), default=30,
        validators=[MaxValueValidator(100)],
    )
    exam_weight     = models.PositiveSmallIntegerField(
        _("exam weight (%)"), default=70,
        validators=[MaxValueValidator(100)],
    )
    status          = models.CharField(
        _("status"), max_length=12,
        choices=SheetStatus.choices,
        default=SheetStatus.OPEN,
    )
    submitted_at    = models.DateTimeField(_("submitted at"), null=True, blank=True)
    approved_at     = models.DateTimeField(_("approved at"), null=True, blank=True)
    rejection_note  = models.TextField(_("rejection note"), blank=True)

    class Meta:
        verbose_name        = _("result sheet")
        verbose_name_plural = _("result sheets")
        ordering            = ["-offering__semester__session__start_date"]

    def __str__(self):
        return f"Result Sheet — {self.offering.course.code} | {self.offering.semester}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.ca_weight + self.exam_weight != 100:
            raise ValidationError(_("CA weight + Exam weight must equal 100%."))

    @property
    def is_locked(self):
        return self.status == self.SheetStatus.APPROVED

    def can_edit(self, user):
        """Return True if this user may edit results on this sheet."""
        if self.is_locked:
            return False
        tp = getattr(user, "teacher_profile", None)
        if tp is not None and not tp.can_submit_results:
            return False
        from academics.models import CourseAllocation
        return CourseAllocation.objects.filter(
            offering=self.offering, teacher=user, is_active=True
        ).exists()


class GradeChoice(models.TextChoices):
    A_PLUS  = "A+",  _("A+ (Distinction)")
    A       = "A",   _("A (Excellent)")
    A_MINUS = "A-",  _("A-")
    B_PLUS  = "B+",  _("B+")
    B       = "B",   _("B (Good)")
    B_MINUS = "B-",  _("B-")
    C_PLUS  = "C+",  _("C+")
    C       = "C",   _("C (Average)")
    C_MINUS = "C-",  _("C-")
    D       = "D",   _("D (Pass)")
    F       = "F",   _("F (Fail)")
    I       = "I",   _("I (Incomplete)")
    W       = "W",   _("W (Withdrawn)")


GRADE_POINTS = {
    "A+": 4.0, "A": 4.0, "A-": 3.7,
    "B+": 3.3, "B": 3.0, "B-": 2.7,
    "C+": 2.3, "C": 2.0, "C-": 1.7,
    "D":  1.0,
    "F":  0.0, "I": 0.0, "W": 0.0,
}


def compute_grade(total_score: float) -> str:
    """Derive a letter grade from a 0–100 total score."""
    if total_score >= 90:  return "A+"
    if total_score >= 85:  return "A"
    if total_score >= 80:  return "A-"
    if total_score >= 77:  return "B+"
    if total_score >= 73:  return "B"
    if total_score >= 70:  return "B-"
    if total_score >= 67:  return "C+"
    if total_score >= 63:  return "C"
    if total_score >= 60:  return "C-"
    if total_score >= 50:  return "D"
    return "F"


class StudentResult(TimeStampedModel):
    """
    A single student's result within a ResultSheet.
    One record per Enrolment per ResultSheet.

    Components:
      ca_score   — Continuous Assessment (test + assignment + quiz weighted)
      exam_score — Final examination mark
      total      — computed from weights in ResultSheet
      grade      — auto-computed letter grade
      grade_point — decimal grade point for GPA

    Transcript: read StudentResult joined to ResultSheet → CourseOffering
                → Course (code, title, credits) + StudentProfile for CGPA.

    GPA computation: sum(grade_point × credits) / sum(credits) for a semester.
    CGPA: same across all semesters.
    """
    result_sheet = models.ForeignKey(
        ResultSheet,
        on_delete=models.CASCADE,
        related_name="student_results",
        verbose_name=_("result sheet"),
    )
    enrolment    = models.OneToOneField(
        "academics.Enrolment",
        on_delete=models.CASCADE,
        related_name="result",
        verbose_name=_("enrolment"),
    )
    # CA breakdown (sub-scores; sum should equal ca_score)
    ca_test_score       = models.DecimalField(
        _("CA test score"), max_digits=5, decimal_places=2,
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    ca_assignment_score = models.DecimalField(
        _("assignment score"), max_digits=5, decimal_places=2,
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    ca_quiz_score       = models.DecimalField(
        _("quiz score"), max_digits=5, decimal_places=2,
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    ca_score     = models.DecimalField(
        _("CA total score (out of 100)"), max_digits=5, decimal_places=2,
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    exam_score   = models.DecimalField(
        _("exam score (out of 100)"), max_digits=5, decimal_places=2,
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    total_score  = models.DecimalField(
        _("total score"), max_digits=5, decimal_places=2,
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    grade        = models.CharField(
        _("grade"), max_length=3,
        choices=GradeChoice.choices,
        blank=True,
    )
    grade_point  = models.DecimalField(
        _("grade point"), max_digits=3, decimal_places=1,
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(4)],
    )
    is_absent    = models.BooleanField(_("absent from exam"), default=False)
    remark       = models.CharField(_("remark"), max_length=200, blank=True)
    entered_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="results_entered",
        verbose_name=_("entered by"),
    )

    class Meta:
        verbose_name        = _("student result")
        verbose_name_plural = _("student results")
        ordering            = ["enrolment__student__last_name"]

    def __str__(self):
        return (
            f"{self.enrolment.student.get_full_name()} — "
            f"{self.result_sheet.offering.course.code} — "
            f"{self.grade or 'Pending'}"
        )

    def compute_total(self):
        """
        Compute weighted total from ca_score and exam_score.
        Call save() after to persist.
        """
        rs = self.result_sheet
        if self.ca_score is None and self.exam_score is None:
            return
        ca_component   = (float(self.ca_score or 0) * rs.ca_weight) / 100
        exam_component = (float(self.exam_score or 0) * rs.exam_weight) / 100
        total = round(ca_component + exam_component, 2)
        self.total_score = total
        self.grade       = compute_grade(total)
        self.grade_point = GRADE_POINTS.get(self.grade, 0.0)

    def save(self, *args, **kwargs):
        if self.ca_score is not None or self.exam_score is not None:
            self.compute_total()
        super().save(*args, **kwargs)
