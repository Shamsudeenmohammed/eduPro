"""Feedback system with sentiment analysis."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class FeedbackCategory(models.TextChoices):
    TEACHING = "teaching", _("Teaching Quality")
    FACILITIES = "facilities", _("Facilities")
    ADMINISTRATION = "administration", _("Administration")
    COURSES = "courses", _("Courses")
    HOSTEL = "hostel", _("Hostel")
    GENERAL = "general", _("General")


class SentimentLabel(models.TextChoices):
    POSITIVE = "positive", _("Positive")
    NEUTRAL = "neutral", _("Neutral")
    NEGATIVE = "negative", _("Negative")
    MIXED = "mixed", _("Mixed")


class Feedback(TimeStampedModel):
    """User feedback with auto sentiment classification."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="feedbacks",
    )
    category = models.CharField(max_length=20, choices=FeedbackCategory.choices, default=FeedbackCategory.GENERAL)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    rating = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text=_("1-5 star rating (optional)"),
    )
    is_anonymous = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False)
    sentiment = models.CharField(
        max_length=10, choices=SentimentLabel.choices,
        blank=True, default="",
    )
    sentiment_score = models.FloatField(
        null=True, blank=True,
        help_text=_("Compound score from -1 (negative) to 1 (positive)"),
    )
    keywords = models.JSONField(default=list, blank=True)
    admin_response = models.TextField(blank=True)
    is_reviewed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "feedback"

    def __str__(self):
        return f"{self.subject} ({self.sentiment or 'unclassified'})"
