"""E-Learning — LMS modules, forums, live classes."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class LMSModule(TimeStampedModel):
    """Learning module within a course offering."""
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.CASCADE, related_name="lms_modules",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=1)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.offering.course.code} — {self.title}"


class LearningResource(TimeStampedModel):
    """Video, document, or link within an LMS module."""
    module = models.ForeignKey(LMSModule, on_delete=models.CASCADE, related_name="resources")
    title = models.CharField(max_length=200)
    resource_type = models.CharField(max_length=20, default="document")
    file = models.FileField(upload_to="elearning/resources/", blank=True, null=True)
    external_url = models.URLField(blank=True)
    duration_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.title


class Forum(TimeStampedModel):
    offering = models.OneToOneField(
        "academics.CourseOffering", on_delete=models.CASCADE, related_name="forum",
    )
    title = models.CharField(max_length=200, default="Discussion Forum")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"Forum — {self.offering.course.code}"


class ForumPost(TimeStampedModel):
    forum = models.ForeignKey(Forum, on_delete=models.CASCADE, related_name="posts")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="replies")
    title = models.CharField(max_length=200, blank=True)
    content = models.TextField()
    is_pinned = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return self.title or self.content[:50]


class LiveClassSession(TimeStampedModel):
    """Structure for live class integration (Zoom/Meet links)."""
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.CASCADE, related_name="live_sessions",
    )
    title = models.CharField(max_length=200)
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    meeting_url = models.URLField(blank=True, help_text=_("Zoom, Google Meet, or Teams link"))
    meeting_id = models.CharField(max_length=100, blank=True)
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="live_sessions_hosted",
    )
    recording_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["scheduled_at"]

    def __str__(self):
        return f"{self.title} — {self.scheduled_at}"
