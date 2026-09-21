"""
notifications/services.py

NotificationService — the single entry point for every LMS notification.
Responsibilities:
  * resolve recipients (enrolled students / allocated teachers)
  * apply the per-event channel matrix + per-user NotificationPreference
  * render title/message/link via notifications.messages.build()
  * persist one NotificationRecord per (recipient, channel)
  * deliver via InAppProvider / EmailProvider / SailupSmsProvider
  * deduplicate via idempotency keys and never raise into caller views
"""

import logging

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.utils import timezone

from . import messages as message_builder
from .models import (
    Channel,
    DeliveryStatus,
    NotificationPreference,
    NotificationRecord,
    NotificationType,
    Priority,
)
from .providers import EmailProvider, SailupSmsProvider

logger = logging.getLogger("eduPro.notifications")


class NotificationService:
    """Stateless facade over the notification pipeline."""

    # Default-on channels per event type (the LMS spec matrix).
    # "Optional" channels (email for NEW_MATERIAL, SMS for announcements) are
    # OFF by default and can be turned on via `force_channels`.
    CHANNEL_MATRIX = {
        NotificationType.NEW_MATERIAL: (Channel.APP,),
        NotificationType.NEW_ASSIGNMENT: (Channel.APP, Channel.EMAIL, Channel.SMS),
        NotificationType.ASSIGNMENT_REMINDER: (Channel.APP, Channel.EMAIL, Channel.SMS),
        NotificationType.ASSIGNMENT_SUBMITTED: (Channel.APP, Channel.EMAIL),
        NotificationType.ASSIGNMENT_GRADED: (Channel.APP, Channel.EMAIL),
        NotificationType.NEW_QUIZ: (Channel.APP, Channel.EMAIL, Channel.SMS),
        NotificationType.QUIZ_REMINDER: (Channel.APP, Channel.EMAIL, Channel.SMS),
        NotificationType.QUIZ_RESULT_AVAILABLE: (Channel.APP, Channel.EMAIL),
        NotificationType.LMS_ANNOUNCEMENT: (Channel.APP, Channel.EMAIL),
        NotificationType.RESULT_SUBMITTED: (Channel.APP, Channel.EMAIL),
        NotificationType.RESULT_APPROVED: (Channel.APP, Channel.EMAIL),
        NotificationType.RESULT_REJECTED: (Channel.APP, Channel.EMAIL),
        NotificationType.RESULT_PUBLISHED: (Channel.APP, Channel.EMAIL),
        NotificationType.RESULT_REVISED: (Channel.APP, Channel.EMAIL),
    }

    # Map NotificationType -> students.NotificationCategory for the in-app bell.
    _CATEGORY_MAP = {
        NotificationType.NEW_MATERIAL: "material",
        NotificationType.NEW_ASSIGNMENT: "assignment",
        NotificationType.ASSIGNMENT_REMINDER: "deadline",
        NotificationType.ASSIGNMENT_SUBMITTED: "assignment",
        NotificationType.ASSIGNMENT_GRADED: "result",
        NotificationType.NEW_QUIZ: "quiz",
        NotificationType.QUIZ_REMINDER: "deadline",
        NotificationType.QUIZ_RESULT_AVAILABLE: "result",
        NotificationType.LMS_ANNOUNCEMENT: "general",
        NotificationType.RESULT_PUBLISHED: "result",
    }

    # ── Public API ───────────────────────────────────────────────────────────

    @classmethod
    def send(
        cls,
        *,
        notification_type,
        recipients,
        context=None,
        link=None,
        title=None,
        message=None,
        obj=None,
        priority=Priority.NORMAL,
        module="LMS",
        channels=None,
        force_channels=None,
        idempotency_key=None,
        ignore_preferences=False,
    ):
        """
        Deliver a notification.

        recipients : User or queryset/iterable of Users.
        title/message : override the auto-rendered copy (e.g. teacher-facing).
        channels   : explicit channel list (skips the matrix) when given.
        force_channels : extra channels to append to the matrix default.
        idempotency_key : if provided (or derivable), duplicate sends are skipped.
        """
        from . import recipients as recipients_util

        users = recipients_util.as_users(recipients)
        if not users:
            return []

        built_title, built_text, built_link = message_builder.build(notification_type, context or {})
        title = title or built_title
        text = message or built_text
        link = link or built_link

        matrix_channels = [Channel(c) for c in (channels or cls.CHANNEL_MATRIX[notification_type])]
        if force_channels:
            for ch in force_channels:
                if Channel(ch) not in matrix_channels:
                    matrix_channels.append(Channel(ch))

        content_type = None
        if obj is not None:
            content_type = ContentType.objects.get_for_model(obj)

        records = []
        for user in users:
            channels_for_user = cls._resolve_channels(user, matrix_channels, ignore_preferences)
            if not channels_for_user:
                continue

            unique_obj = f"{content_type.app_label}:{content_type.model}:{obj.pk}" if obj is not None else None

            for channel in channels_for_user:
                key = cls._default_key(notification_type, unique_obj, user, channel)
                if idempotency_key:
                    # Callers pass a *base* key (e.g. "new_quiz:{pk}"); scope it
                    # per recipient + channel so every recipient is notified
                    # while re-runs of the same event stay idempotent.
                    key = f"{idempotency_key}:{user.pk}:{channel}"
                record = cls._create_record(
                    user=user,
                    notification_type=notification_type,
                    title=title,
                    message=text,
                    link=link,
                    channel=channel,
                    priority=priority,
                    module=module,
                    content_type=content_type,
                    obj=obj,
                    key=key,
                    context=context or {},
                )
                if record is None:
                    continue  # duplicate skipped
                records.append(record)
                try:
                    cls._deliver(record, user, notification_type, title, text, link, context or {})
                except Exception as exc:  # noqa: BLE001 -- fail one channel, keep the rest
                    logger.warning("Delivery failed for record %s: %s", record.pk, exc)
                    record.status = DeliveryStatus.FAILED
                    record.error_message = f"{type(exc).__name__}: {exc}"
                    record.save(update_fields=["status", "error_message"])

        return records

    # ── LMS convenience helpers ──────────────────────────────────────────────

    @classmethod
    def send_to_students(cls, notification_type, offering, context=None, **kwargs):
        """Notify every active enrolled student of `offering`."""
        return cls.send(
            notification_type=notification_type,
            recipients=EnrolmentQuery(offering),
            context={**(context or {}), "offering": offering},
            **kwargs,
        )

    @classmethod
    def send_to_teachers(cls, notification_type, offering, context=None, **kwargs):
        """Notify every active teacher allocated to `offering`."""
        from academics.models import CourseAllocation

        teacher_ids = CourseAllocation.objects.filter(
            offering=offering, is_active=True
        ).values_list("teacher_id", flat=True)
        from accounts.models import EduProUser

        teachers = EduProUser.objects.filter(pk__in=list(teacher_ids))
        return cls.send(
            notification_type=notification_type,
            recipients=teachers,
            context={**(context or {}), "offering": offering},
            **kwargs,
        )

    @classmethod
    def enrollment_announcement(cls, offering, subject, body, channels=None, **kwargs):
        """Course-specific LMS announcement with explicable channel selection."""
        return cls.send(
            notification_type=NotificationType.LMS_ANNOUNCEMENT,
            recipients=EnrolmentQuery(offering),
            context={"subject": subject, "body": body, "offering": offering},
            channels=channels,
            **kwargs,
        )

    # ── Internals ────────────────────────────────────────────────────────────

    @staticmethod
    def _default_key(notification_type, unique_obj, user, channel):
        parts = [notification_type]
        if unique_obj:
            parts.append(unique_obj)
        parts.extend([str(user.pk), channel])
        return ":".join(parts)

    @staticmethod
    def _resolve_channels(user, matrix_channels, ignore_preferences):
        if ignore_preferences:
            return list(matrix_channels)
        try:
            prefs = NotificationPreference.objects.get(user=user)
        except NotificationPreference.DoesNotExist:
            prefs = NotificationPreference(user=user)

        allowed = []
        for channel in matrix_channels:
            if channel == Channel.APP and prefs.receive_app:
                allowed.append(Channel.APP)
            elif channel == Channel.EMAIL and prefs.receive_email:
                allowed.append(Channel.EMAIL)
            elif channel == Channel.SMS and prefs.receive_sms:
                allowed.append(Channel.SMS)
        return allowed

    @staticmethod
    def _json_safe(obj):
        if isinstance(obj, dict):
            return {k: NotificationService._json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [NotificationService._json_safe(v) for v in obj]
        if hasattr(obj, "pk"):
            return str(obj.pk)
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        return str(obj)

    @classmethod
    def _create_record(cls, user, notification_type, title, message, link, channel,
                       priority, module, content_type, obj, key, context):
        existing_qs = NotificationRecord.objects.filter(idempotency_key=key)
        if key and existing_qs.exists():
            logger.info("Skipping duplicate notification key=%s", key)
            return None

        record = NotificationRecord(
            recipient=user,
            notification_type=notification_type,
            title=title,
            message=message,
            link=link,
            channel=channel,
            priority=priority,
            module=module,
            idempotency_key=key or None,
            status=DeliveryStatus.QUEUED,
        )
        if content_type is not None:
            record.content_type = content_type
            record.object_id = obj.pk
        record.meta = cls._json_safe(dict(context or {}))
        record.save()
        return record

    @classmethod
    def _deliver(cls, record, user, notification_type, title, text, link, context):
        if record.channel == Channel.APP:
            cls._deliver_app(record, user, notification_type, title, text, link)
        elif record.channel == Channel.EMAIL:
            cls._deliver_email(record, user, notification_type, title, text, link, context)
        elif record.channel == Channel.SMS:
            cls._deliver_sms(record, user, text)

    @staticmethod
    def _deliver_app(record, user, notification_type, title, text, link):
        if getattr(user, "is_student", False):
            StudentNotificationModel = apps.get_model("students", "StudentNotification")
            StudentNotificationModel.objects.create(
                student=user,
                category=NotificationService._CATEGORY_MAP.get(notification_type, "general"),
                title=title,
                message=text,
                link=link,
            )
        # Channel=APP records are "delivered" instantly (unread until opened).
        record.status = DeliveryStatus.DELIVERED
        record.sent_at = timezone.now()
        record.save(update_fields=["status", "sent_at"])

    @staticmethod
    def _deliver_email(record, user, notification_type, title, text, link, context):
        to_email = getattr(user, "email", "") or ""
        if not to_email:
            record.status = DeliveryStatus.FAILED
            record.error_message = "No email address"
            record.save(update_fields=["status", "error_message"])
            return

        html = EmailTemplate.render(notification_type, title, text, link, context)
        provider = EmailProvider()
        _, err = provider.send(to_email, f"{title} — {settings.EDUPRO_SITE_NAME or 'eduPro'}", html, text)
        if err:
            record.status = DeliveryStatus.FAILED
            record.error_message = err
            record.provider = "django-smtp"
        else:
            record.status = DeliveryStatus.SENT
            record.provider = "django-smtp"
            record.sent_at = timezone.now()
        record.save(update_fields=["status", "error_message", "provider", "sent_at"])

    @staticmethod
    def _deliver_sms(record, user, text):
        from accounts.models import UserProfile

        profile = UserProfile.objects.filter(user=user).first()
        phone = (profile.phone if profile else "") or ""
        phone = str(phone).strip()
        if not phone:
            record.status = DeliveryStatus.FAILED
            record.error_message = "No phone number"
            record.save(update_fields=["status", "error_message"])
            return

        provider = SailupSmsProvider()
        provider_message_id, err = provider.send(phone, text)
        record.provider = "sailup"
        record.provider_message_id = provider_message_id
        if provider_message_id:
            record.status = DeliveryStatus.SENT
            record.sent_at = timezone.now()
        else:
            record.status = DeliveryStatus.FAILED
            record.error_message = err or "Sailup disabled or rejected"
        record.save(
            update_fields=["status", "provider", "provider_message_id", "error_message", "sent_at"]
        )

    # ── Read state helpers ───────────────────────────────────────────────────

    @staticmethod
    def mark_read(user, record_pk):
        record = NotificationRecord.objects.filter(pk=record_pk, recipient=user).first()
        if record and not record.read_at:
            record.read_at = timezone.now()
            record.status = DeliveryStatus.DELIVERED
            record.save(update_fields=["read_at", "status"])
        return record

    @staticmethod
    def mark_all_read(user):
        return NotificationRecord.objects.filter(
            recipient=user, channel=Channel.APP, read_at__isnull=True
        ).update(read_at=timezone.now())

    @classmethod
    def unread_count(cls, user):
        return NotificationRecord.objects.filter(
            recipient=user, channel=Channel.APP, read_at__isnull=True
        ).count()


# Small proxies to keep call-site imports clean
from .recipients import EnrolmentQuery  # noqa: E402
from .email_templates import EmailTemplate  # noqa: E402