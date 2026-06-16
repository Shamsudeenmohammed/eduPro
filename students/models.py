
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ─────────────────────────────────────────────────────────────────────────────
# STUDENT NOTIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class NotificationCategory(models.TextChoices):
    RESULT      = "result",      _("Result Published")
    ASSIGNMENT  = "assignment",  _("Assignment Posted")
    QUIZ        = "quiz",        _("Quiz Available")
    MATERIAL    = "material",    _("New Material")
    ATTENDANCE  = "attendance",  _("Attendance Alert")
    GENERAL     = "general",     _("General")
    DEADLINE    = "deadline",    _("Deadline Reminder")


class StudentNotification(TimeStampedModel):
    """
    In-platform notification delivered to a student.
    Created by the system when teachers post assignments, results, materials, etc.
    """
    student     = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_notifications",
        limit_choices_to={"role": "student"},
    )
    category    = models.CharField(
        max_length=15,
        choices=NotificationCategory.choices,
        default=NotificationCategory.GENERAL,
    )
    title       = models.CharField(max_length=200)
    message     = models.TextField()
    link        = models.CharField(max_length=300, blank=True,
                                   help_text=_("Relative URL to navigate to on click."))
    is_read     = models.BooleanField(default=False)
    read_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "student notification"
        verbose_name_plural = "student notifications"

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title} → {self.student.get_full_name()}"

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])


# ─────────────────────────────────────────────────────────────────────────────
# COURSE REGISTRATION REQUEST
# ─────────────────────────────────────────────────────────────────────────────

class RegistrationStatus(models.TextChoices):
    PENDING  = "pending",  _("Pending")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")


class CourseRegistrationRequest(TimeStampedModel):
    """
    Student self-service course registration request.
    Admin / HOD approves → creates academics.Enrolment automatically via signal.
    """
    student   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="registration_requests",
        limit_choices_to={"role": "student"},
    )
    offering  = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.CASCADE,
        related_name="registration_requests",
    )
    status    = models.CharField(
        max_length=10,
        choices=RegistrationStatus.choices,
        default=RegistrationStatus.PENDING,
    )
    reason    = models.TextField(
        blank=True,
        help_text=_("Optional reason / note from student."),
    )
    reviewed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="registration_reviews",
    )
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    rejection_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("student", "offering")]
        verbose_name = "course registration request"
        verbose_name_plural = "course registration requests"

    def __str__(self):
        return (
            f"{self.student.get_full_name()} → "
            f"{self.offering.course.code} ({self.get_status_display()})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# MATERIAL DOWNLOAD LOG
# ─────────────────────────────────────────────────────────────────────────────

class MaterialDownloadLog(TimeStampedModel):
    """
    Records every time a student accesses / downloads a LectureMaterial.
    Feeds into future LMS analytics.
    """
    student  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="material_downloads",
        limit_choices_to={"role": "student"},
    )
    material = models.ForeignKey(
        "teachers.LectureMaterial",
        on_delete=models.CASCADE,
        related_name="download_logs",
    )
    accessed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-accessed_at"]
        verbose_name = "material download log"
        verbose_name_plural = "material download logs"

    def __str__(self):
        return f"{self.student.get_full_name()} ↓ {self.material.title}"
