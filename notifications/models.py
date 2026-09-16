"""
notifications/models.py

Centralised notification delivery tracking.

NOTE: This app intentionally has NO migrations package. Django auto-creates
the tables for unmigrated apps via ``migrate --run-syncdb`` (also used by the
test runner), so no migration files are needed from the code author. The
tables can be migrated into a proper initial migration at a later date.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class NotificationType(models.TextChoices):
    NEW_MATERIAL = "new_material", "New Material"
    NEW_ASSIGNMENT = "new_assignment", "New Assignment"
    ASSIGNMENT_REMINDER = "assignment_reminder", "Assignment Reminder"
    ASSIGNMENT_SUBMITTED = "assignment_submitted", "Assignment Submitted"
    ASSIGNMENT_GRADED = "assignment_graded", "Assignment Graded"
    NEW_QUIZ = "new_quiz", "New Quiz"
    QUIZ_REMINDER = "quiz_reminder", "Quiz Reminder"
    QUIZ_RESULT_AVAILABLE = "quiz_result_available", "Quiz Result Available"
    LMS_ANNOUNCEMENT = "lms_announcement", "LMS Announcement"


class Channel(models.TextChoices):
    APP = "app", "In-app"
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"


class Priority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class DeliveryStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"
    REJECTED = "rejected", "Rejected"


class NotificationRecord(models.Model):
    """One record per (recipient, channel) delivery attempt."""

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_records",
        db_index=True,
    )
    notification_type = models.CharField(
        max_length=40, choices=NotificationType.choices, db_index=True
    )
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=500, blank=True)
    channel = models.CharField(max_length=10, choices=Channel.choices, db_index=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    module = models.CharField(max_length=32, default="LMS")

    content_type = models.ForeignKey(
        "contenttypes.ContentType", on_delete=models.SET_NULL, null=True, blank=True
    )
    object_id = models.PositiveBigIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=12, choices=DeliveryStatus.choices, default=DeliveryStatus.QUEUED, db_index=True
    )
    provider = models.CharField(max_length=40, blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    error_message = models.TextField(blank=True)
    idempotency_key = models.CharField(
        max_length=255, unique=True, null=True, blank=True, db_index=True
    )
    meta = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "channel", "read_at"]),
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.notification_type} -> {self.recipient} ({self.channel})"


class NotificationPreference(models.Model):
    """Per-user toggles for the three delivery channels."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    receive_app = models.BooleanField(default=True)
    receive_email = models.BooleanField(default=True)
    receive_sms = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Notification Preference"

    def __str__(self):
        return f"Preferences: {self.user}"


class DeliveryWebhook(models.Model):
    """Log of delivery webhooks received from Sailup."""

    provider_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    event = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    signature_valid = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"Webhook {self.event} {self.provider_message_id}"