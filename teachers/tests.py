"""Tests for the unified result lifecycle (Phase 3): publish, revise, versions."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import EduProUser, Role, StaffResponsibility
from academics.models import (
    AcademicSession,
    Course,
    CourseOffering,
    Department,
    Enrolment,
    Faculty,
    Institution,
    Level,
    Program,
    Semester,
)
from core.models import AuditAction, AuditLog
from notifications.models import NotificationType
from students.models import StudentNotification
from teachers.models import ResultSheet, ResultVersion, StudentResult

from academics.tests import AcademicGraphMixin


class ResultLifecycleTests(TestCase, AcademicGraphMixin):
    def setUp(self):
        institution = Institution.objects.create(name="Test University", short_name="TU")
        faculty = Faculty.objects.create(institution=institution, code="SCI", name="Science")
        self.department = Department.objects.create(
            institution=institution, faculty=faculty,
            code="CS", name="Computer Science",
        )
        self.program = Program.objects.create(
            department=self.department, code="BS-CS", name="BSc CS",
        )
        self.level = Level.objects.get_or_create(
            program=self.program, order=1,
            defaults={"name": "100", "is_active": True},
        )[0]
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31",
        )

        names = {
            "first": ("First", "2025-09-01", "2026-01-31"),
            "second": ("Second", "2026-02-01", "2026-07-31"),
        }
        self.semesters = {}
        for key, (label, start, end) in names.items():
            self.semesters[key] = Semester.objects.create(
                session=session, name=label.lower(), start_date=start, end_date=end,
            )

        self.student = EduProUser.objects.create_user(
            email="student@test.edu", password="pw", role=Role.STUDENT,
            first_name="Razak", last_name="Abdullah", is_active=True,
        )
        from academics.models import StudentProfile
        StudentProfile.objects.create(
            student=self.student, program=self.program, current_level=self.level,
        )
        self.teacher = EduProUser.objects.create_user(
            email="teacher@test.edu", password="pw", role=Role.TEACHER,
            first_name="Mahamadu", last_name="Azindoo", is_active=True,
        )
        self.hod = EduProUser.objects.create_user(
            email="hod@test.edu", password="pw", role=Role.TEACHER,
            first_name="Sam", last_name="Hod", is_active=True,
        )
        self.department.hod = self.hod
        self.department.save(update_fields=["hod"])
        from accounts.models import UserStaffRole
        UserStaffRole.objects.create(
            user=self.hod, responsibility=StaffResponsibility.HOD, department=self.department,
        )
        self.admin = EduProUser.objects.create_superuser(
            email="admin@test.edu", password="pw",
            first_name="Admin", last_name="User",
        )

    def _make_sheet(self, status="open", with_result=False, dept=None):
        dept = dept or self.department
        course = Course.objects.create(
            department=dept, code=f"CS{110 + ResultSheet.objects.count()}",
            title="A Course", credit_units=3,
        )
        offering = CourseOffering.objects.create(
            course=course, semester=self.semesters["first"], level=self.level,
            level_name="100", venue="LT1",
        )
        offering.departments.add(dept)
        enrolment = Enrolment.objects.create(
            student=self.student, offering=offering, is_active=True,
        )
        sheet = ResultSheet.objects.create(
            offering=offering, submitted_by=self.teacher, status=status,
            ca_weight=30, exam_weight=70,
        )
        if with_result:
            StudentResult.objects.create(
                result_sheet=sheet, enrolment=enrolment,
                ca_score=Decimal("45.00"), exam_score=Decimal("50.00"),
            )
        return sheet

    def _publish(self, sheet):
        sheet.status = ResultSheet.SheetStatus.APPROVED
        sheet.save(update_fields=["status"])
        self.client.force_login(self.admin)
        return self.client.post(reverse("teachers:result_publish", args=[sheet.pk]))

    # ── Publish ──────────────────────────────────────────────────────────────

    def test_publish_requires_approved(self):
        sheet = self._make_sheet(status="open", with_result=True)
        self._publish(sheet)
        sheet.refresh_from_db()
        self.assertIsNone(sheet.published_at)
        self.assertEqual(ResultVersion.objects.filter(result_sheet=sheet).count(), 0)

    def test_publish_creates_version_snapshot(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        response = self._publish(sheet)
        sheet.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(sheet.published_at)
        self.assertEqual(sheet.published_by, self.admin)
        self.assertEqual(sheet.revision, 1)

        version = ResultVersion.objects.get(result_sheet=sheet, revision=1)
        self.assertEqual(version.sheet_status, "approved")
        self.assertEqual(len(version.results), 1)
        self.assertEqual(version.results[0]["student_id"], self.student.pk)
        self.assertEqual(version.results[0]["grade"], "F")

    def test_publish_is_idempotent(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)
        self._publish(sheet)
        self.assertEqual(ResultVersion.objects.filter(result_sheet=sheet).count(), 1)

    def test_publish_notifies_students(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)
        self.assertTrue(
            StudentNotification.objects.filter(
                student=self.student, category="result"
            ).exists()
        )

    def test_publish_audited(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditAction.PUBLISH).exists()
        )

    # ── Revise ──────────────────────────────────────────────────────────────

    def _revise(self, sheet, reason="Marks entry error."):
        self.client.force_login(self.admin)
        return self.client.post(reverse("teachers:result_revise", args=[sheet.pk]), {
            "reason": reason,
        })

    def test_revise_requires_reason(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)
        self._revise(sheet, reason="")
        sheet.refresh_from_db()
        self.assertTrue(sheet.is_published)
        self.assertEqual(sheet.revision, 1)

    def test_revise_returns_sheet_to_draft_and_bumps_revision(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)
        self._revise(sheet, reason="Marks entry error.")

        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.SheetStatus.OPEN)
        self.assertIsNone(sheet.published_at)
        self.assertEqual(sheet.revision, 2)
        self.assertEqual(sheet.last_revision_note, "Marks entry error.")
        self.assertEqual(sheet.last_revised_by, self.admin)

        # Revision 1 snapshot remains in history.
        self.assertEqual(ResultVersion.objects.filter(result_sheet=sheet).count(), 1)

    def test_republish_after_revise_creates_next_version(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)
        self._revise(sheet)
        sheet.status = ResultSheet.SheetStatus.APPROVED
        sheet.save(update_fields=["status"])
        self._publish(sheet)

        self.assertEqual(ResultVersion.objects.filter(result_sheet=sheet).count(), 2)
        versions = list(ResultVersion.objects.filter(result_sheet=sheet).order_by("revision"))
        self.assertEqual([v.revision for v in versions], [1, 2])

    def test_only_board_can_revise(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)

        self.client.force_login(self.teacher)
        response = self.client.post(reverse("teachers:result_revise", args=[sheet.pk]), {
            "reason": "Nope.",
        })
        sheet.refresh_from_db()
        self.assertTrue(sheet.is_published)
        self.assertEqual(response.status_code, 302)

    def test_revise_notifies_teacher(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)
        self._revise(sheet)
        from notifications.models import NotificationRecord
        self.assertTrue(
            NotificationRecord.objects.filter(
                recipient=self.teacher,
                notification_type=NotificationType.RESULT_REVISED,
            ).exists()
        )

    # ── History views ───────────────────────────────────────────────────────

    def test_version_history_view(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("teachers:result_version_history", args=[sheet.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revision 1")
        self.assertContains(response, "45.00")


    def test_version_restore_into_sheet(self):
        sheet = self._make_sheet(status="approved", with_result=True)
        self._publish(sheet)
        original = ResultVersion.objects.get(result_sheet=sheet, revision=1)

        result = sheet.student_results.get()
        result.ca_score = Decimal("90.00")
        result.save()
        self.assertNotEqual(result.total_score, original.results[0]["total_score"])

        original.restore_into_sheet()
        result.refresh_from_db()
        self.assertEqual(str(result.ca_score), original.results[0]["ca_score"])
        self.assertEqual(str(result.total_score), original.results[0]["total_score"])
        self.assertEqual(result.grade, original.results[0]["grade"])


class HODReviewCenterTests(ResultLifecycleTests):
    """Phase 4 — prioritised HOD review queue and batch decisions."""

    def _submit(self, sheet, hours_ago):
        sheet.status = ResultSheet.SheetStatus.SUBMITTED
        sheet.submitted_at = timezone.now() - timedelta(hours=hours_ago)
        sheet.save(update_fields=["status", "submitted_at"])
        return sheet

    def _other_department_sheet(self, status="submitted"):
        from academics.models import Department
        other = Department.objects.create(
            institution=self.department.institution,
            faculty=self.department.faculty,
            code="MATH", name="Mathematics",
        )
        return self._make_sheet(status=status, dept=other)

    # ── Queue rendering & priority ─────────────────────────────────────────

    def test_review_center_prioritises_oldest_first(self):
        old = self._submit(self._make_sheet(status="submitted"), hours_ago=100)
        new = self._submit(self._make_sheet(status="submitted"), hours_ago=5)

        self.client.force_login(self.hod)
        response = self.client.get(reverse("teachers:hod_review_center"))
        self.assertEqual(response.status_code, 200)

        old_pos = response.content.decode().find(old.offering.course.code)
        new_pos = response.content.decode().find(new.offering.course.code)
        self.assertGreaterEqual(old_pos, 0)
        self.assertGreaterEqual(new_pos, 0)
        self.assertLess(old_pos, new_pos, "oldest submitted sheet must be queued first")

        stats = response.context["stats"]
        self.assertEqual(stats["pending_count"], 2)
        self.assertEqual(stats["overdue_count"], 1)
        self.assertEqual(stats["published_count"], 0)

        queue_sheets = {s.pk: s for s in response.context["queue"]}
        self.assertTrue(queue_sheets[old.pk].is_overdue)
        self.assertFalse(queue_sheets[new.pk].is_overdue)

    def test_review_center_scopes_to_hod_departments(self):
        self._submit(self._make_sheet(status="submitted"), hours_ago=5)
        foreign = self._other_department_sheet(status="submitted")

        self.client.force_login(self.hod)
        response = self.client.get(reverse("teachers:hod_review_center"))
        self.assertEqual(response.status_code, 200)
        pks = [s.pk for s in response.context["queue"]]
        self.assertNotIn(foreign.pk, pks)
        with self.subTest("admin sees all departments"):
            self.client.force_login(self.admin)
            response = self.client.get(reverse("teachers:hod_review_center"))
            self.assertIn(foreign.pk, [s.pk for s in response.context["queue"]])

    def test_review_center_filters_and_search(self):
        a = self._submit(self._make_sheet(status="submitted"), hours_ago=5)
        self._submit(self._make_sheet(status="approved"), hours_ago=20)

        self.client.force_login(self.hod)
        url = reverse("teachers:hod_review_center")
        # status filter: submitted only
        response = self.client.get(url, {"status": "submitted"})
        self.assertIn(a.pk, [s.pk for s in response.context["queue"]])
        # course search
        response = self.client.get(url, {"q": a.offering.course.code})
        self.assertEqual([s.pk for s in response.context["queue"]], [a.pk])

    def test_non_hod_cannot_open_review_center(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("teachers:hod_review_center"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response, reverse("accounts:teacher_dashboard")
        )

    # ── Batch approve ──────────────────────────────────────────────────────

    def test_batch_approve(self):
        first = self._submit(self._make_sheet(status="submitted"), hours_ago=10)
        second = self._submit(self._make_sheet(status="submitted"), hours_ago=2)

        self.client.force_login(self.hod)
        response = self.client.post(reverse("teachers:hod_review_batch"), {
            "action": "approve",
            "sheet_ids": [first.pk, second.pk],
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("teachers:hod_review_center"))

        for sheet in (first, second):
            sheet.refresh_from_db()
            self.assertEqual(sheet.status, ResultSheet.SheetStatus.APPROVED)
            self.assertEqual(sheet.approved_by, self.hod)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditAction.APPROVE).count() >= 2
        )

    def test_batch_approve_skips_non_submitted(self):
        already = self._make_sheet(status="approved")
        pending = self._submit(self._make_sheet(status="submitted"), hours_ago=3)

        self.client.force_login(self.hod)
        self.client.post(reverse("teachers:hod_review_batch"), {
            "action": "approve",
            "sheet_ids": [already.pk, pending.pk],
        })

        pending.refresh_from_db()
        already.refresh_from_db()
        self.assertEqual(pending.status, ResultSheet.SheetStatus.APPROVED)
        self.assertEqual(already.status, ResultSheet.SheetStatus.APPROVED)

    # ── Batch reject ───────────────────────────────────────────────────────

    def test_batch_reject_with_note(self):
        sheet = self._submit(self._make_sheet(status="submitted"), hours_ago=8)

        self.client.force_login(self.hod)
        self.client.post(reverse("teachers:hod_review_batch"), {
            "action": "reject",
            "sheet_ids": [sheet.pk],
            "rejection_note": "Marks missing on several rows.",
        })
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.SheetStatus.REJECTED)
        self.assertEqual(sheet.rejection_note, "Marks missing on several rows.")

    def test_batch_reject_defaults_note(self):
        sheet = self._submit(self._make_sheet(status="submitted"), hours_ago=1)
        self.client.force_login(self.hod)
        self.client.post(reverse("teachers:hod_review_batch"), {
            "action": "reject",
            "sheet_ids": [sheet.pk],
        })
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.SheetStatus.REJECTED)
        self.assertEqual(sheet.rejection_note, "No reason provided.")

    # ── Batch scope & guards ───────────────────────────────────────────────

    def test_batch_skips_cross_department_sheets(self):
        inside = self._submit(self._make_sheet(status="submitted"), hours_ago=4)
        outside = self._other_department_sheet(status="submitted")

        self.client.force_login(self.hod)
        response = self.client.post(reverse("teachers:hod_review_batch"), {
            "action": "approve",
            "sheet_ids": [inside.pk, outside.pk],
        })
        inside.refresh_from_db()
        outside.refresh_from_db()
        self.assertEqual(inside.status, ResultSheet.SheetStatus.APPROVED)
        self.assertEqual(
            outside.status, ResultSheet.SheetStatus.SUBMITTED,
            "HOD must not approve sheets outside their department",
        )
        self.assertRedirects(response, reverse("teachers:hod_review_center"))

    def test_batch_requires_selection_and_valid_action(self):
        sheet = self._submit(self._make_sheet(status="submitted"), hours_ago=2)
        self.client.force_login(self.hod)
        url = reverse("teachers:hod_review_batch")

        response = self.client.post(url, {"action": "approve"})
        self.assertRedirects(response, reverse("teachers:hod_review_center"))

        response = self.client.post(url, {"action": "explode", "sheet_ids": [sheet.pk]})
        self.assertRedirects(response, reverse("teachers:hod_review_center"))
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.SheetStatus.SUBMITTED)

    def test_non_hod_cannot_run_batch(self):
        sheet = self._submit(self._make_sheet(status="submitted"), hours_ago=2)
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("teachers:hod_review_batch"), {
            "action": "approve",
            "sheet_ids": [sheet.pk],
        })
        self.assertRedirects(response, reverse("accounts:teacher_dashboard"))
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.SheetStatus.SUBMITTED)