"""Core system models — audit logs and shared utilities."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditAction(models.TextChoices):
    CREATE = "create", _("Create")
    UPDATE = "update", _("Update")
    DELETE = "delete", _("Delete")
    LOGIN = "login", _("Login")
    LOGOUT = "logout", _("Logout")
    VIEW = "view", _("View")
    EXPORT = "export", _("Export")
    IMPORT = "import", _("Import")
    APPROVE = "approve", _("Approve")
    REJECT = "reject", _("Reject")


class AuditLog(TimeStampedModel):
    """Immutable audit trail for admin and sensitive actions."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=20, choices=AuditAction.choices)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=50, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    path = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("audit log")
        verbose_name_plural = _("audit logs")
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["model_name", "-created_at"]),
        ]

    def __str__(self):
        who = self.user.get_full_name() if self.user else "System"
        return f"{who} — {self.action} — {self.object_repr or self.model_name}"
