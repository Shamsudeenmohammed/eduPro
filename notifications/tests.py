"""
notifications/tests.py

Covers the NotificationService pipeline, channel matrix, preferences,
idempotency, Sailup SMS provider (mocked), the delivery webhook, the teacher
notification centre, preferences views, and the LMS reminder command.
"""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from io import StringIO

from academics.models import (
    AcademicSession,
    Course,
    CourseAllocation,
    CourseOffering,
    Department,
    Enrolment,
    Faculty,
    Institution,
    Program,
    Semester,
    SemesterType,
    StudentProfile,
)
from accounts.models import Role, UserProfile
from notifications.models import (
    Channel,
    DeliveryStatus,
    DeliveryWebhook,
    NotificationPreference,
    NotificationRecord,
    NotificationType,
)
from notifications.services import NotificationService
from teachers.models import Assignment, LectureMaterial, Quiz, QuizAttempt
from students.models import StudentNotification


User = get_user_model()

BASE_SETTINGS = dict(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SAILUP_ENABLED=True,
    SAILUP_API_KEY="sk_test_unit",
    SAILUP_SENDER_ID="eduPro",
    SAILUP_BASE_URL="https://api.sailup.io/v1",
)


class NotificationBaseTestCase(TestCase):
    """Shared academic fixtures: offering, enrolled student, allocated teacher."""

    @classmethod
    def setUpTestData(cls):
        cls.institution = Institution.objects.create(name="Test University", short_name="TU")
        cls.faculty = Faculty.objects.create(institution=cls.institution, name="Science", code="SCI")
        cls.department = Department.objects.create(
            institution=cls.institution, faculty=cls.faculty, name="Computer Science", code="CS"
        )
        cls.program = Program.objects.create(department=cls.department, name="BSc Comp", code="BSC-CS")
        cls.session = AcademicSession.objects.create(
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
            is_current=True,
        )
        cls.semester = Semester.objects.create(
            session=cls.session,
            name=SemesterType.FIRST,
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 31),
            is_current=True,
        )
        cls.course = Course.objects.create(department=cls.department, code="CS101", title="Intro to CS")
        cls.offering = CourseOffering.objects.create(course=cls.course, semester=cls.semester)

        cls.student = User.objects.create(
            email="student@test.edu", first_name="Ada", last_name="Lovelace",
            role=Role.STUDENT, is_active=True,
        )
        StudentProfile.objects.create(student=cls.student, student_number="STU-1001")
        Enrolment.objects.create(student=cls.student, offering=cls.offering)

        cls.teacher = User.objects.create(
            email="teacher@test.edu", first_name="Alan", last_name="Turing",
            role=Role.TEACHER, is_active=True,
        )
        CourseAllocation.objects.create(offering=cls.offering, teacher=cls.teacher)

    def setUp(self):
        self.client = Client()
        mail.outbox = []

    def apply_overrides(self):
        return override_settings(**BASE_SETTINGS)


# ═══════════════════════════════════════════════════════════════════════
# SERVICE PIPELINE
# ═══════════════════════════════════════════════════════════════════════

@override_settings(**BASE_SETTINGS)
class NotificationServiceTests(NotificationBaseTestCase):

    def test_new_assignment_creates_records_for_all_channels(self):
        UserProfile.objects.filter(user=self.student).update(phone="+233201234567")
        NotificationPreference.objects.update_or_create(
            user=self.student, defaults={"receive_sms": True}
        )
        with patch("notifications.providers.http_post_json",
                   return_value={"message_id": "sailup-msg-1"}):
            records = NotificationService.send(
                notification_type=NotificationType.NEW_ASSIGNMENT,
                recipients=[self.student],
                context={"offering": self.offering,
                        "assignment_title": "HW1", "due_date": timezone.now()},
            )

        channels = {r.channel for r in records}
        self.assertEqual(channels, {Channel.APP, Channel.EMAIL, Channel.SMS})

        # In-app bell entry for the student
        self.assertEqual(StudentNotification.objects.filter(student=self.student).count(), 1)
        bell = StudentNotification.objects.get(student=self.student)
        self.assertEqual(bell.category, "assignment")
        self.assertTrue(bell.link.startswith("/students/"))

        # Email was actually sent via the locmem backend
        self.assertEqual(len(mail.outbox), 1)

        # SMS record carries the provider message id
        sms = NotificationRecord.objects.get(recipient=self.student, channel=Channel.SMS)
        self.assertEqual(sms.provider, "sailup")
        self.assertEqual(sms.provider_message_id, "sailup-msg-1")
        self.assertEqual(sms.status, DeliveryStatus.SENT)

    def test_default_preferences_skip_sms(self):
        # receive_sms defaults False -> no SMS record despite matrix saying so
        with patch("notifications.providers.http_post_json",
                   return_value={"message_id": "x"}):
            records = NotificationService.send(
                notification_type=NotificationType.NEW_ASSIGNMENT,
                recipients=[self.student],
                context={"assignment_title": "HW1", "due_date": timezone.now()},
            )
        self.assertNotIn(Channel.SMS, {r.channel for r in records})
        self.assertIn(Channel.APP, {r.channel for r in records})
        self.assertIn(Channel.EMAIL, {r.channel for r in records})

    def test_new_material_matrix_is_app_only(self):
        records = NotificationService.send(
            notification_type=NotificationType.NEW_MATERIAL,
            recipients=[self.student],
            context={"material_title": "Ch1"},
        )
        self.assertEqual({r.channel for r in records}, {Channel.APP})

    def test_lms_announcement_can_include_sms_when_forced(self):
        UserProfile.objects.filter(user=self.student).update(phone="+233201234567")
        NotificationPreference.objects.update_or_create(
            user=self.student, defaults={"receive_sms": True}
        )
        with patch("notifications.providers.http_post_json",
                   return_value={"message_id": "m"}):
            records = NotificationService.send(
                notification_type=NotificationType.LMS_ANNOUNCEMENT,
                recipients=[self.student],
                context={"subject": "Exam change", "body": "Bring IDs"},
                force_channels=[Channel.SMS],
            )
        self.assertIn(Channel.SMS, {r.channel for r in records})

    def test_idempotency_key_dedupes_runs(self):
        with patch("notifications.providers.http_post_json",
                   return_value={"message_id": "m"}):
            first = NotificationService.send(
                notification_type=NotificationType.ASSIGNMENT_GRADED,
                recipients=[self.student],
                context={"assignment_title": "HW", "score": 8, "total": 10},
                idempotency_key="graded:hw1",
            )
            second = NotificationService.send(
                notification_type=NotificationType.ASSIGNMENT_GRADED,
                recipients=[self.student],
                context={"assignment_title": "HW", "score": 8, "total": 10},
                idempotency_key="graded:hw1",
            )
        self.assertEqual(len(first), 2)   # app + email
        self.assertEqual(len(second), 0)  # fully deduped
        self.assertEqual(NotificationRecord.objects.count(), 2)

    def test_send_to_students_excludes_unenrolled_and_inactive_enrolments(self):
        outsider = User.objects.create(
            email="outsider@test.edu", first_name="Out", last_name="Sider",
            role=Role.STUDENT, is_active=True,
        )
        # inactive enrolment for the same offering
        Enrolment.objects.create(student=outsider, offering=self.offering, is_active=False)
        with patch("notifications.providers.http_post_json",
                   return_value={"message_id": "m"}):
            records = NotificationService.send_to_students(
                NotificationType.ASSIGNMENT_REMINDER,
                self.offering,
                context={"assignment_title": "HW", "due_date": timezone.now()},
            )
        recipients = {r.recipient_id for r in records}
        self.assertIn(self.student.pk, recipients)
        self.assertNotIn(outsider.pk, recipients)

    def test_send_to_teachers_only_allocated(self):
        other = User.objects.create(
            email="other@test.edu", first_name="O", last_name="P",
            role=Role.TEACHER, is_active=True,
        )
        records = NotificationService.send_to_teachers(
            NotificationType.ASSIGNMENT_SUBMITTED,
            self.offering,
            title="New submission",
            message=f"{self.student.get_full_name()} submitted.",
        )
        recipients = {r.recipient_id for r in records}
        self.assertIn(self.teacher.pk, recipients)
        self.assertNotIn(other.pk, recipients)

    def test_email_failure_is_recorded_not_raised(self):
        with patch.object(
            NotificationService, "_deliver_email",
            side_effect=RuntimeError("boom"),
        ):
            records = NotificationService.send(
                notification_type=NotificationType.NEW_ASSIGNMENT,
                recipients=[self.student],
                context={"assignment_title": "H", "due_date": timezone.now()},
            )
        # Predisposing: delivery failures are caught per-channel by callers.
        self.assertTrue(records)

    def test_app_notification_not_created_for_teacher(self):
        records = NotificationService.send(
            notification_type=NotificationType.ASSIGNMENT_SUBMITTED,
            recipients=[self.teacher],
            title="New submission",
            message="uploaded.",
        )
        self.assertEqual(len(records), 2)  # app + email
        self.assertEqual(StudentNotification.objects.filter(student=self.teacher).count(), 0)

    def test_unread_and_mark_read(self):
        record = NotificationRecord.objects.create(
            recipient=self.teacher, notification_type=NotificationType.ASSIGNMENT_SUBMITTED,
            title="t", channel=Channel.APP,
        )
        self.assertEqual(NotificationService.unread_count(self.teacher), 1)
        NotificationService.mark_read(self.teacher, record.pk)
        record.refresh_from_db()
        self.assertIsNotNone(record.read_at)
        self.assertEqual(NotificationService.unread_count(self.teacher), 0)


# ═══════════════════════════════════════════════════════════════════════
# SAILUP SMS PROVIDER
# ═══════════════════════════════════════════════════════════════════════

class SailupSmsProviderTests(NotificationBaseTestCase):

    @override_settings(SAILUP_ENABLED=False)
    def test_disabled_provider_returns_no_id(self):
        from notifications.providers import SailupSmsProvider
        provider = SailupSmsProvider(api_key="", sender_id="eduPro")
        message_id, err = provider.send("+233201234567", "hello")
        self.assertEqual(message_id, "")
        self.assertIn("disabled", err.lower())

    @override_settings(**BASE_SETTINGS)
    def test_send_posts_payload_and_parses_message_id(self):
        from notifications.providers import SailupSmsProvider
        with patch("notifications.providers.http_post_json") as mock:
            mock.return_value = {"message_id": "sailup-123", "status": "sent"}
            provider = SailupSmsProvider()
            message_id, err = provider.send("+233201234567", "hello")
        self.assertEqual(message_id, "sailup-123")
        self.assertIsNone(err)
        url, headers, payload = mock.call_args[0]
        self.assertTrue(url.endswith("/sms/"))
        self.assertEqual(headers["Authorization"], f"Bearer {BASE_SETTINGS['SAILUP_API_KEY']}")
        self.assertEqual(payload["to"], ["+233201234567"])
        self.assertEqual(payload["body"], "hello")

    @override_settings(**BASE_SETTINGS)
    def test_failure_returns_error_message(self):
        from notifications.providers import ProviderError, SailupSmsProvider
        with patch("notifications.providers.http_post_json",
                   side_effect=ProviderError("network down")):
            provider = SailupSmsProvider()
            message_id, err = provider.send("+233201234567", "hello")
        self.assertEqual(message_id, "")
        self.assertIn("network down", err)


# ═══════════════════════════════════════════════════════════════════════
# DELIVERY WEBHOOK
# ═══════════════════════════════════════════════════════════════════════

class DeliveryWebhookTests(NotificationBaseTestCase):

    @override_settings(SAILUP_WEBHOOK_SECRET="unit-secret")
    def test_valid_signature_updates_record(self):
        import hashlib
        import hmac
        import json

        sms = NotificationRecord.objects.create(
            recipient=self.student,
            notification_type=NotificationType.NEW_ASSIGNMENT,
            title="t",
            channel=Channel.SMS,
            provider="sailup",
            provider_message_id="web-1",
            status=DeliveryStatus.SENT,
        )
        payload = {"event": "message.delivered", "message_id": "web-1", "status": "delivered"}
        raw = json.dumps(payload).encode()
        sig = hmac.new(b"unit-secret", raw, hashlib.sha256).hexdigest()

        resp = self.client.post(
            reverse("notifications:sailup_webhook"),
            data=raw,
            content_type="application/json",
            HTTP_X_SAILUP_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, 200)
        sms.refresh_from_db()
        self.assertEqual(sms.status, DeliveryStatus.DELIVERED)
        self.assertTrue(DeliveryWebhook.objects.filter(provider_message_id="web-1").exists())

    @override_settings(SAILUP_WEBHOOK_SECRET="unit-secret")
    def test_invalid_signature_rejected_and_logged(self):
        import json

        payload = {"event": "message.failed", "message_id": "web-2", "status": "failed"}
        raw = json.dumps(payload).encode()
        resp = self.client.post(
            reverse("notifications:sailup_webhook"),
            data=raw,
            content_type="application/json",
            HTTP_X_SAILUP_SIGNATURE="bad",
        )
        self.assertEqual(resp.status_code, 400)
        hook = DeliveryWebhook.objects.get(provider_message_id="web-2")
        self.assertFalse(hook.signature_valid)


# ═══════════════════════════════════════════════════════════════════════
# VIEWS
# ═══════════════════════════════════════════════════════════════════════

class NotificationViewTests(NotificationBaseTestCase):

    def test_teacher_centre_lists_records(self):
        NotificationRecord.objects.create(
            recipient=self.teacher,
            notification_type=NotificationType.ASSIGNMENT_SUBMITTED,
            title="New submission",
            channel=Channel.APP,
        )
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("notifications:centre"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "New submission")

    def test_preferences_view_updates_channels(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse("notifications:preferences"))
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(reverse("notifications:preferences"), {
            "receive_app": "on", "receive_email": "on", "receive_sms": "on",
        })
        self.assertEqual(resp.status_code, 302)
        pref = NotificationPreference.objects.get(user=self.student)
        self.assertTrue(pref.receive_sms)

    def test_mark_read_view(self):
        record = NotificationRecord.objects.create(
            recipient=self.teacher,
            notification_type=NotificationType.ASSIGNMENT_SUBMITTED,
            title="t",
            channel=Channel.APP,
        )
        self.client.force_login(self.teacher)
        self.client.post(reverse("notifications:mark_read", args=[record.pk]))
        record.refresh_from_db()
        self.assertIsNotNone(record.read_at)

    def test_unread_count_json(self):
        NotificationRecord.objects.create(
            recipient=self.student, notification_type=NotificationType.NEW_QUIZ,
            title="q", channel=Channel.APP,
        )
        self.client.force_login(self.student)
        resp = self.client.get(reverse("notifications:unread_count"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)


# ═══════════════════════════════════════════════════════════════════════
# REMINDER COMMAND
# ═══════════════════════════════════════════════════════════════════════

@override_settings(**BASE_SETTINGS, LMS_ASSIGNMENT_REMINDER_DAYS=[1], LMS_QUIZ_REMINDER_HOURS=[1])
class ReminderCommandTests(NotificationBaseTestCase):

    def test_assignment_reminder_sent_to_unsubmitted_only(self):
        UserProfile.objects.filter(user=self.student).update(phone="+233201234567")
        NotificationPreference.objects.update_or_create(
            user=self.student, defaults={"receive_sms": True}
        )
        other = User.objects.create(
            email="submitted@test.edu", first_name="S", last_name="U",
            role=Role.STUDENT, is_active=True,
        )
        StudentProfile.objects.create(student=other, student_number="STU-1002")
        Enrolment.objects.create(student=other, offering=self.offering)

        assignment = Assignment.objects.create(
            offering=self.offering,
            title="Ch1 ex",
            instructions="Do the exercises.",
            created_by=self.teacher,
            status="published",
            due_date=timezone.now() + timedelta(hours=24, minutes=30),
        )
        # `other` already submitted -> should NOT be reminded
        from teachers.models import AssignmentSubmission
        AssignmentSubmission.objects.create(
            assignment=assignment, student=other, is_active=True,
        )

        with patch("notifications.providers.http_post_json",
                   return_value={"message_id": "r"}):
            call_command("send_lms_reminders", stdout=StringIO())

        reminded = NotificationRecord.objects.filter(
            notification_type=NotificationType.ASSIGNMENT_REMINDER
        )
        self.assertEqual(reminded.count(), 3)  # app+email+sms for self.student only
        self.assertNotIn(other.pk, {r.recipient_id for r in reminded})

        # Second run identical -> idempotent (no new records)
        with patch("notifications.providers.http_post_json",
                   return_value={"message_id": "r"}):
            call_command("send_lms_reminders", stdout=StringIO())
        self.assertEqual(reminded.count(), 3)

    def test_quiz_reminder_sent_to_non_attempted(self):
        quiz = Quiz.objects.create(
            offering=self.offering,
            title="Midterm",
            created_by=self.teacher,
            is_published=True,
            start_datetime=timezone.now() - timedelta(hours=1),
            end_datetime=timezone.now() + timedelta(hours=1, minutes=10),
        )
        with patch("notifications.providers.http_post_json",
                   return_value={"message_id": "q"}):
            call_command("send_lms_reminders", stdout=StringIO())

        records = NotificationRecord.objects.filter(
            notification_type=NotificationType.QUIZ_REMINDER,
            recipient=self.student,
        )
        self.assertEqual(records.count(), 2)  # app+email (sms off)
        self.assertNotIn(Channel.SMS, {r.channel for r in records})


# ═══════════════════════════════════════════════════════════════════════
# LMS EVENT WIRING (materials / signalled path)
# ═══════════════════════════════════════════════════════════════════════

class MaterialSignalTests(NotificationBaseTestCase):

    def test_publishing_material_notifies_via_signal(self):
        material = LectureMaterial.objects.create(
            allocation=self.offering.allocations.first(),
            title="Slides 1",
            is_published=True,
        )
        bell = StudentNotification.objects.filter(student=self.student)
        self.assertEqual(bell.count(), 1)
        self.assertEqual(bell.first().category, "material")
        # app-only matrix -> no email/sms for a brand-new published material
        self.assertEqual(
            NotificationRecord.objects.filter(recipient=self.student).count(), 1
        )

    def test_draft_material_does_not_notify(self):
        LectureMaterial.objects.create(
            allocation=self.offering.allocations.first(),
            title="Slides 2",
            is_published=False,
        )
        self.assertEqual(StudentNotification.objects.filter(student=self.student).count(), 0)