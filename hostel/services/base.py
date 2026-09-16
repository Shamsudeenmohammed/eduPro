"""Shared service primitives: errors, audit, notifications, URL helper."""

from datetime import timedelta

from django.urls import reverse as _reverse
from django.utils import timezone


def reverse_url(name, *args, **kwargs):
    try:
        return _reverse(name, args=args, kwargs=kwargs)
    except Exception:
        return ""


# ── Exceptions ──────────────────────────────────────────────────────────────

class HostelServiceError(Exception):
    """User-facing business-rule failure. ``message`` is safe to display."""
    pass


class BedUnavailableError(HostelServiceError):
    pass


class StudentConflictError(HostelServiceError):
    pass


class EligibilityError(HostelServiceError):
    pass


class PaymentRequiredError(HostelServiceError):
    pass


# ── Audit / notifications ───────────────────────────────────────────────────

class HostelAuditService:
    """Writes detailed audit entries through the existing core audit system."""

    @staticmethod
    def record(user, action, model_name, obj=None, *, object_id="", object_repr="", changes=None):
        from core.models import AuditLog
        actor = user if (user and getattr(user, "is_authenticated", False)) else None
        AuditLog.objects.create(
            user=actor,
            action=action,
            model_name=model_name,
            object_id=str((obj and getattr(obj, "pk", "")) or object_id)[:50],
            object_repr=(object_repr or (str(obj) if obj else ""))[:255],
            changes=changes or {},
        )


class HostelNotificationService:
    """In-app notifications through the existing students.StudentNotification."""

    @staticmethod
    def notify(student, title, message, link=""):
        """
        Create an in-app notification, deduplicating identical recent ones so
        retries / callback + webhook double-dispatch don't flood the list.
        """
        from students.models import StudentNotification

        cutoff = timezone.now() - timedelta(hours=24)
        if StudentNotification.objects.filter(
            student=student, category="hostel",
            title=title, message=message, link=link,
            created_at__gte=cutoff,
        ).exists():
            return
        StudentNotification.objects.create(
            student=student,
            category="hostel",
            title=title,
            message=message,
            link=link,
        )