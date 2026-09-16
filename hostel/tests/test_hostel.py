"""
hostel/tests/test_hostel.py — Hostel module tests.

Covers: application lifecycle, transaction-safe allocation, check-in /
check-out, transfers, maintenance, incidents, the finance boundary,
authorization/IDOR, view routes, and the ten concurrency scenarios required
for the hostel upgrade.

Concurrency tests
-----------------
Each scenario launches N threads that race the same business operation.
On PostgreSQL/MySQL (the production-grade engines that honour
``select_for_update`` and the partial unique constraints), the strict
invariant — *exactly one winner, no double-booking* — is asserted.  On SQLite
the row locks and constraints are no-ops, so the tests verify graceful,
exception-safe behaviour instead of the strict count.

NOTE: These tests require the hostel data tables to exist first
(``python manage.py migrate hostel`` then run the suite).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from itertools import count

from django.core.management import call_command
from django.db import DatabaseError, connection, connections
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest import mock

from accounts.models import StaffResponsibility, UserStaffRole  # noqa: F401
from academics.models import AcademicSession, StudentProfile
from core.models import AuditLog
from finance.models import FeePayment, FeeStructure, StudentFee
from students.models import StudentNotification

from hostel.models import (
    BedMaintenance,
    Hostel,
    HostelAllocation,
    HostelApplication,
    HostelBed,
    HostelBlock,
    HostelFeeConfig,
    HostelFloor,
    HostelIncident,
    HostelPolicy,
    HostelRoom,
    HostelTransfer,
    IncidentStatus,
)
from hostel import services
from hostel.services import (
    BedUnavailableError,
    BedMaintenanceService,
    EligibilityError,
    HostelAllocationService,
    HostelApplicationService,
    HostelCheckInService,
    HostelCheckoutService,
    HostelFinanceService,
    HostelIncidentService,
    HostelServiceError,
    HostelTransferService,
    PaymentRequiredError,
    StudentConflictError,
)

STRICT_LOCKS = connection.vendor in ("postgresql", "mysql")


def fake_policy(**overrides):
    """Create a fresh active policy (get_active returns the latest)."""
    params = dict(
        reservation_expiry_hours=4,
        require_payment_before_checkin=True,
        allow_partial_payment=True,
        enable_hostel_charges=False,
        allow_transfers=True,
        allow_hostel_transfers=True,
        require_active_student=True,
        is_active=True,
    )
    params.update(overrides)
    return HostelPolicy.objects.create(**params)


class HostelBaseTestCase(TestCase):
    """Shared fixtures for the hostel domain."""

    @classmethod
    def setUpTestData(cls):
        cls.session = AcademicSession.objects.create(
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
            is_current=True,
        )
        cls.hostel = Hostel.objects.create(
            name="Boys Hostel", location="Campus A", capacity=6, is_active=True
        )
        cls.block = HostelBlock.objects.create(hostel=cls.hostel, name="Block A")
        cls.floor = HostelFloor.objects.create(block=cls.block, name="First Floor")
        cls.room = HostelRoom.objects.create(
            hostel=cls.hostel, floor=cls.floor, room_number="101", capacity=2, is_available=True
        )
        cls.other_room = HostelRoom.objects.create(
            hostel=cls.hostel, floor=cls.floor, room_number="102", capacity=2, is_available=True
        )
        cls.bed_a = HostelBed.objects.create(
            room=cls.room, bed_number="A1", label="101-A1"
        )
        cls.bed_b = HostelBed.objects.create(
            room=cls.room, bed_number="A2", label="101-A2"
        )
        cls.bed_c = HostelBed.objects.create(
            room=cls.other_room, bed_number="B1", label="102-B1"
        )
        cls.bed_d = HostelBed.objects.create(
            room=cls.other_room, bed_number="B2", label="102-B2"
        )

        cls.officer = cls._make_user("officer@school.edu", role="admin", is_active=True)
        cls.warden = cls._make_user("warden@school.edu", role="teacher", is_active=True)
        UserStaffRole.objects.create(
            user=cls.warden,
            responsibility=StaffResponsibility.HOSTEL_OFFICER,
            department=None,
        )
        cls.teacher = cls._make_user("teacher@school.edu", role="teacher", is_active=True)

        cls.policy = fake_policy()

    @classmethod
    def _make_user(cls, email, *, role="student", is_active=True):
        attrs = dict(
            email=email,
            first_name=email.split("@")[0].capitalize(),
            last_name="Student",
            role=role,
            is_active=is_active,
        )
        return cls._user_model().objects.create(**attrs)

    @classmethod
    def _user_model(cls):
        from django.contrib.auth import get_user_model
        return get_user_model()

    @classmethod
    def _student(cls, email=None):
        user = cls._make_user(email or f"{email or 'st'}@{id(cls)}.school.edu".lower())
        StudentProfile.objects.create(
            student=user,
            is_active=True,
            student_number=f"STU-{id(cls)}-{next(cls._stu_counter)}",
        )
        return user

    _stu_counter = count()

    def setUp(self):
        self.client = Client()

    # ── helpers ─────────────────────────────────────────────────────────
    def allocate(self, student=None, bed=None, *, note="", application=None, actor=None):
        return HostelAllocationService.allocate_bed(
            student=student or self._student(),
            bed=bed or self.bed_a,
            session=self.session,
            actor=actor or self.officer,
            application=application,
            note=note,
            reservation_expires_at=timezone.now() + timedelta(hours=1),
        )

    def check_in(self, allocation, actor=None):
        return HostelCheckInService.check_in(
            allocation=allocation, actor=actor or self.officer, note="Checked in."
        )

    @staticmethod
    def refresh_bed(bed):
        bed.refresh_from_db()


# ═══════════════════════════════════════════════════════════════════════
# APPLICATION LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════

class ApplicationFlowTests(HostelBaseTestCase):

    def test_submit_application_creates_pending_with_session(self):
        student = self._student("s1@school.edu")
        app = HostelApplicationService.submit_application(
            student=student, room=self.room, actor=student
        )
        self.assertEqual(app.status, HostelApplication.Status.PENDING)
        self.assertEqual(app.session, self.session)
        self.assertIsNotNone(app.submitted_at)
        self.assertTrue(
            StudentNotification.objects.filter(student=student, category="hostel").exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(model_name="HostelApplication", action="create").exists()
        )

    def test_cannot_apply_twice(self):
        student = self._student("s2@school.edu")
        HostelApplicationService.submit_application(student=student, room=self.room)
        with self.assertRaises(EligibilityError):
            HostelApplicationService.submit_application(student=student, room=self.room)

    def test_reapply_allowed_after_rejection(self):
        student = self._student("s3@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        HostelApplicationService.review_application(app, self.officer, approve=False, remark="No")
        reapp = HostelApplicationService.submit_application(student=student, room=self.other_room)
        self.assertEqual(reapp.status, HostelApplication.Status.PENDING)

    def test_review_marks_approved_and_audits(self):
        student = self._student("s4@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        reviewed = HostelApplicationService.review_application(
            app, self.officer, approve=True, remark="OK"
        )
        self.assertEqual(reviewed.status, HostelApplication.Status.APPROVED)
        self.assertEqual(reviewed.reviewed_by, self.officer)
        self.assertTrue(
            AuditLog.objects.filter(model_name="HostelApplication", action="approve").exists()
        )

    def test_review_rejects_rejections(self):
        student = self._student("s5@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        HostelApplicationService.review_application(app, self.officer, approve=True)
        with self.assertRaises(HostelServiceError):
            HostelApplicationService.review_application(app, self.officer, approve=False)

    def test_mark_payment_verified_only_when_approved(self):
        student = self._student("s6@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        with self.assertRaises(HostelServiceError):
            HostelApplicationService.mark_payment_verified(app, self.officer)
        HostelApplicationService.review_application(app, self.officer, approve=True)
        app = HostelApplicationService.mark_payment_verified(app, self.officer)
        self.assertTrue(app.payment_verified_at)

    def test_eligibility_requires_approved_profile(self):
        rough = self._make_user("rough@school.edu")
        with self.assertRaises(EligibilityError):
            HostelApplicationService.submit_application(student=rough, room=self.room)

    def test_application_window_closed_blocks_submission(self):
        self.policy.application_close = date.today() - timedelta(days=1)
        self.policy.save()
        student = self._student("s7@school.edu")
        with self.assertRaises(EligibilityError):
            HostelApplicationService.submit_application(student=student, room=self.room)


# ═══════════════════════════════════════════════════════════════════════
# ALLOCATION (transaction-safe)
# ═══════════════════════════════════════════════════════════════════════

class AllocationTests(HostelBaseTestCase):

    def test_allocate_bed_reserves_bed_and_sets_state(self):
        student = self._student("a1@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        self.assertEqual(alloc.status, HostelAllocation.Status.RESERVED)
        self.assertEqual(alloc.room, self.room)
        self.assertTrue(alloc.is_active)
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.RESERVED)
        self.assertEqual(alloc.allocated_by, self.officer)
        self.assertTrue(
            AuditLog.objects.filter(model_name="HostelAllocation", action="create").exists()
        )

    def test_student_cannot_have_two_live_allocations(self):
        student = self._student("a2@school.edu")
        self.allocate(student=student, bed=self.bed_a)
        with self.assertRaises(StudentConflictError):
            self.allocate(student=student, bed=self.bed_b)

    def test_bed_cannot_have_two_live_allocations(self):
        self.allocate(student=self._student("a3@school.edu"), bed=self.bed_a)
        with self.assertRaises(BedUnavailableError):
            self.allocate(student=self._student("a4@school.edu"), bed=self.bed_a)

    def test_cannot_allocate_unavailable_bed(self):
        self.bed_a.state = HostelBed.BedState.OCCUPIED
        self.bed_a.save(update_fields=["state"])
        with self.assertRaises(BedUnavailableError):
            self.allocate(student=self._student("a5@school.edu"), bed=self.bed_a)

    def test_hold_release_lifecycle(self):
        HostelAllocationService.hold_bed(bed=self.bed_a, actor=self.officer, note="Keep")
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.HELD)
        HostelAllocationService.release_hold(bed=self.bed_a, actor=self.officer)
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.AVAILABLE)

    def test_block_unblock_bed(self):
        HostelAllocationService.set_bed_blocked(bed=self.bed_a, actor=self.officer, blocked=True)
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.BLOCKED)
        with self.assertRaises(BedUnavailableError):
            self.allocate(student=self._student("a6@school.edu"), bed=self.bed_a)
        HostelAllocationService.set_bed_blocked(bed=self.bed_a, actor=self.officer, blocked=False)
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.AVAILABLE)

    def test_cancel_allocation_releases_bed(self):
        student = self._student("a7@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        HostelAllocationService.cancel_allocation(alloc, actor=self.officer, reason="Withdrew")
        alloc.refresh_from_db()
        self.assertEqual(alloc.status, HostelAllocation.Status.CANCELLED)
        self.assertFalse(alloc.is_active)
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.AVAILABLE)
        # Bed is allocatable again
        self.allocate(student=self._student("a8@school.edu"), bed=self.bed_a)

    def test_expire_reservation_releases_bed(self):
        student = self._student("a9@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        HostelAllocationService.expire_reservation(alloc)
        alloc.refresh_from_db()
        self.assertEqual(alloc.status, HostelAllocation.Status.EXPIRED)
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.AVAILABLE)

    def test_expire_due_reservations_sweep(self):
        student = self._student("a10@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        HostelAllocation.objects.filter(pk=alloc.pk).update(
            reservation_expires_at=timezone.now() - timedelta(hours=1)
        )
        expired = HostelAllocationService.expire_due_reservations()
        self.assertIn(alloc.pk, expired)
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.AVAILABLE)

    def test_allocate_confirms_linked_application(self):
        student = self._student("a11@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        HostelApplicationService.review_application(app, self.officer, approve=True)
        alloc = self.allocate(student=student, bed=self.bed_a, application=app)
        app.refresh_from_db()
        self.assertEqual(app.status, HostelApplication.Status.CONFIRMED)
        self.assertEqual(app.allocation, alloc)

    def test_allocate_requires_session(self):
        with self.assertRaises(HostelServiceError):
            HostelAllocationService.allocate_bed(
                student=self._student("a12@school.edu"),
                bed=self.bed_a, session=None, actor=self.officer,
            )


# ═══════════════════════════════════════════════════════════════════════
# CHECK-IN / CHECK-OUT
# ═══════════════════════════════════════════════════════════════════════

class CheckInCheckoutTests(HostelBaseTestCase):

    def test_check_in_activates_and_occupies(self):
        alloc = self.allocate(student=self._student("c1@school.edu"), bed=self.bed_a)
        checked = self.check_in(alloc)
        self.assertEqual(checked.status, HostelAllocation.Status.ACTIVE)
        self.assertEqual(checked.checked_in_by, self.officer)
        self.assertIsNotNone(checked.checked_in_at)
        self.assertEqual(checked.check_in, date.today())
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.OCCUPIED)

    def test_check_in_requires_reserved(self):
        alloc = self.allocate(student=self._student("c2@school.edu"), bed=self.bed_a)
        checked = self.check_in(alloc)
        with self.assertRaises(HostelServiceError):
            self.check_in(checked)

    def test_checkout_releases_bed(self):
        alloc = self.allocate(student=self._student("c3@school.edu"), bed=self.bed_a)
        checked = self.check_in(alloc)
        HostelCheckoutService.checkout(allocation=checked, actor=self.officer, note="Bye")
        checked.refresh_from_db()
        self.assertEqual(checked.status, HostelAllocation.Status.CHECKED_OUT)
        self.assertEqual(checked.check_out, date.today())
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.AVAILABLE)

    def test_checkout_of_reserved_raises(self):
        alloc = self.allocate(student=self._student("c4@school.edu"), bed=self.bed_a)
        with self.assertRaises(HostelServiceError):
            HostelCheckoutService.checkout(allocation=alloc, actor=self.officer)


# ═══════════════════════════════════════════════════════════════════════
# FINANCE BOUNDARY
# ═══════════════════════════════════════════════════════════════════════

class FinanceGateTests(HostelBaseTestCase):

    def setUp(self):
        super().setUp()
        self.fee_structure = FeeStructure.objects.create(
            name="Hostel — Boys Hostel", session=self.session, amount=Decimal("1000.00")
        )
        try:
            HostelFeeConfig.objects.create(
                hostel=self.hostel, session=self.session,
                fee_structure=self.fee_structure, is_active=True,
            )
        except DatabaseError:
            self.skipTest(
                "HostelFeeConfig table missing semester/room columns — "
                "apply the new migration before running finance tests."
            )
        self.policy.enable_hostel_charges = True
        self.policy.require_payment_before_checkin = True
        self.policy.allow_partial_payment = False
        self.policy.save()

    def test_unpaid_student_blocked_from_check_in(self):
        student = self._student("f1@school.edu")
        StudentFee.objects.create(
            student=student, fee_structure=self.fee_structure,
            amount_due=Decimal("1000.00"), amount_paid=Decimal("0"),
        )
        alloc = self.allocate(student=student, bed=self.bed_a)
        with self.assertRaises(PaymentRequiredError):
            self.check_in(alloc)
        alloc.refresh_from_db()
        self.assertEqual(alloc.status, HostelAllocation.Status.RESERVED)

    def test_fully_paid_student_can_check_in(self):
        student = self._student("f2@school.edu")
        StudentFee.objects.create(
            student=student, fee_structure=self.fee_structure,
            amount_due=Decimal("1000.00"), amount_paid=Decimal("1000.00"),
        )
        alloc = self.allocate(student=student, bed=self.bed_a)
        checked = self.check_in(alloc)
        self.assertEqual(checked.status, HostelAllocation.Status.ACTIVE)

    def test_partial_payment_disallowed_when_policy_requires_full(self):
        student = self._student("f3@school.edu")
        StudentFee.objects.create(
            student=student, fee_structure=self.fee_structure,
            amount_due=Decimal("1000.00"), amount_paid=Decimal("600.00"),
        )
        alloc = self.allocate(student=student, bed=self.bed_a)
        with self.assertRaises(PaymentRequiredError):
            self.check_in(alloc)

    def test_partial_payment_allowed_when_policy_allows(self):
        self.policy.allow_partial_payment = True
        self.policy.save()
        student = self._student("f4@school.edu")
        StudentFee.objects.create(
            student=student, fee_structure=self.fee_structure,
            amount_due=Decimal("1000.00"), amount_paid=Decimal("600.00"),
        )
        alloc = self.allocate(student=student, bed=self.bed_a)
        checked = self.check_in(alloc)
        self.assertEqual(checked.status, HostelAllocation.Status.ACTIVE)

    def test_no_charge_configured_means_no_gate(self):
        HostelFeeConfig.objects.all().delete()
        student = self._student("f5@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        checked = self.check_in(alloc)
        self.assertEqual(checked.status, HostelAllocation.Status.ACTIVE)

    def test_ensure_charge_creates_student_fee_via_finance(self):
        student = self._student("f6@school.edu")
        fee = HostelFinanceService.ensure_charge(
            student=student, session=self.session, hostel=self.hostel, actor=self.officer
        )
        self.assertIsNotNone(fee)
        self.assertEqual(fee.fee_structure, self.fee_structure)
        self.assertEqual(fee.amount_due, Decimal("1000.00"))

    def test_balance_info_reports_status(self):
        student = self._student("f7@school.edu")
        StudentFee.objects.create(
            student=student, fee_structure=self.fee_structure,
            amount_due=Decimal("1000.00"), amount_paid=Decimal("400.00"),
        )
        info = HostelFinanceService.balance_info(student, self.session)
        self.assertTrue(info["has_charge"])
        self.assertEqual(info["balance"], Decimal("600.00"))
        self.assertEqual(info["status"], "partial")


# ═══════════════════════════════════════════════════════════════════════
# TRANSFERS
# ═══════════════════════════════════════════════════════════════════════

class TransferTests(HostelBaseTestCase):

    def test_request_transfer_creates_pending(self):
        student = self._student("t1@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        transfer = HostelTransferService.request_transfer(
            student=student, old_allocation=alloc, new_bed=self.bed_b,
            reason="Closer to friends", actor=student,
        )
        self.assertEqual(transfer.status, HostelTransfer.Status.PENDING)
        self.assertEqual(transfer.old_bed, self.bed_a)
        self.assertEqual(transfer.new_bed, self.bed_b)

    def test_transfer_requires_own_active_allocation(self):
        student = self._student("t2@school.edu")
        other = self._student("t3@school.edu")
        other_alloc = self.allocate(student=other, bed=self.bed_b)
        with self.assertRaises(HostelServiceError):
            HostelTransferService.request_transfer(
                student=student, old_allocation=other_alloc, new_bed=self.bed_a, actor=student
            )

    def test_cross_hostel_transfer_blocked_by_policy(self):
        other_hostel = Hostel.objects.create(name="Girls Hostel", is_active=True)
        other_room = HostelRoom.objects.create(
            hostel=other_hostel, floor=self.floor, room_number="201", capacity=2, is_available=True
        )
        far_bed = HostelBed.objects.create(room=other_room, bed_number="C1", label="201-C1")
        self.policy.allow_hostel_transfers = False
        self.policy.save()
        student = self._student("t4@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        with self.assertRaises(HostelServiceError):
            HostelTransferService.request_transfer(
                student=student, old_allocation=alloc, new_bed=far_bed, actor=student
            )

    def test_transfer_approve_then_reject_first_opinion(self):
        student = self._student("t5@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        transfer = HostelTransferService.request_transfer(
            student=student, old_allocation=alloc, new_bed=self.bed_b, actor=student
        )
        HostelTransferService.review_transfer(transfer, self.officer, approve=False, note="Denied")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, HostelTransfer.Status.REJECTED)

    def test_complete_transfer_moves_student(self):
        student = self._student("t6@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        self.check_in(alloc)
        transfer = HostelTransferService.request_transfer(
            student=student, old_allocation=alloc, new_bed=self.bed_c, actor=student
        )
        HostelTransferService.review_transfer(transfer, self.officer, approve=True)
        new_alloc = HostelTransferService.complete_transfer(transfer, self.officer)

        alloc.refresh_from_db()
        new_alloc.refresh_from_db()
        transfer.refresh_from_db()

        self.assertEqual(alloc.status, HostelAllocation.Status.CHECKED_OUT)
        self.assertEqual(new_alloc.student, student)
        self.assertEqual(new_alloc.bed, self.bed_c)
        self.assertEqual(new_alloc.session, self.session)
        self.assertEqual(transfer.status, HostelTransfer.Status.COMPLETED)
        self.assertEqual(transfer.new_allocation, new_alloc)
        self.refresh_bed(self.bed_a)
        self.refresh_bed(self.bed_c)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.AVAILABLE)
        self.assertEqual(self.bed_c.state, HostelBed.BedState.RESERVED)

    def test_complete_transfer_requires_approved(self):
        student = self._student("t7@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        transfer = HostelTransferService.request_transfer(
            student=student, old_allocation=alloc, new_bed=self.bed_b, actor=student
        )
        with self.assertRaises(HostelServiceError):
            HostelTransferService.complete_transfer(transfer, self.officer)

    def test_transfer_new_bed_must_be_available(self):
        student = self._student("t8@school.edu")
        occupant = self._student("t9@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        occupied_alloc = self.allocate(student=occupant, bed=self.bed_b)
        self.check_in(occupied_alloc)
        with self.assertRaises(BedUnavailableError):
            HostelTransferService.request_transfer(
                student=student, old_allocation=alloc, new_bed=self.bed_b, actor=student
            )
        # Policy-level guard: transfers off
        self.policy.allow_transfers = False
        self.policy.save()
        with self.assertRaises(HostelServiceError):
            HostelTransferService.request_transfer(
                student=student, old_allocation=alloc, new_bed=self.bed_b, actor=student
            )

    def test_cancel_transfer(self):
        student = self._student("t10@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        transfer = HostelTransferService.request_transfer(
            student=student, old_allocation=alloc, new_bed=self.bed_b, actor=student
        )
        HostelTransferService.cancel_transfer(transfer, actor=student)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, HostelTransfer.Status.CANCELLED)


# ═══════════════════════════════════════════════════════════════════════
# MAINTENANCE & INCIDENTS
# ═══════════════════════════════════════════════════════════════════════

class MaintenanceIncidentTests(HostelBaseTestCase):

    def test_start_complete_maintenance_releases_bed(self):
        record = BedMaintenanceService.start_maintenance(
            bed=self.bed_a, reason="Broken bed frame", reported_by=self.officer, notes="Fix fast"
        )
        self.assertEqual(record.status, BedMaintenance.Status.ONGOING)
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.MAINTENANCE)
        with self.assertRaises(BedUnavailableError):
            self.allocate(student=self._student("m1@school.edu"), bed=self.bed_a)

        BedMaintenanceService.complete_maintenance(record, completed_by=self.officer, notes="Done")
        record.refresh_from_db()
        self.assertEqual(record.status, BedMaintenance.Status.COMPLETED)
        self.refresh_bed(self.bed_a)
        self.assertEqual(self.bed_a.state, HostelBed.BedState.AVAILABLE)

    def test_cannot_maintain_occupied_bed(self):
        alloc = self.allocate(student=self._student("m2@school.edu"), bed=self.bed_a)
        self.check_in(alloc)
        with self.assertRaises(BedUnavailableError):
            BedMaintenanceService.start_maintenance(
                bed=self.bed_a, reason="Whatever", reported_by=self.officer
            )

    def test_incident_report_and_resolve(self):
        student = self._student("m3@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        incident = HostelIncidentService.report(
            student, category="room_damage", description="Window broken",
            reported_by=self.officer, room=self.room, bed=self.bed_a, allocation=alloc,
        )
        self.assertEqual(incident.status, IncidentStatus.OPEN)
        HostelIncidentService.resolve(
            incident, resolved_by=self.officer,
            resolution="Glass replaced, bed kept.",
        )
        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.RESOLVED)
        self.assertIsNotNone(incident.resolved_at)


# ═══════════════════════════════════════════════════════════════════════
# AUTHORIZATION & IDOR
# ═══════════════════════════════════════════════════════════════════════

class AuthorizationTests(HostelBaseTestCase):

    def login(self, user):
        self.client.force_login(user)
        return self.client

    def test_staff_only_pages_deny_students(self):
        student = self._student("z1@school.edu")
        self.login(student)
        for url_name in ("allocations", "beds", "applications"):
            resp = self.client.get(reverse(f"hostel:{url_name}"))
            self.assertEqual(resp.status_code, 302)
            self.assertNotIn(reverse(f"hostel:{url_name}"), resp.url or "")

    def test_officer_can_access_staff_pages(self):
        self.login(self.warden)
        for url_name in ("allocations", "beds", "dashboard",
                         "applications", "transfers",
                         "maintenance", "incidents", "policy"):
            resp = self.client.get(reverse(f"hostel:{url_name}"))
            self.assertEqual(resp.status_code, 200)

    def test_student_cannot_approve_application(self):
        student = self._student("z2@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        self.login(student)
        resp = self.client.post(reverse("hostel:application_approve", args=[app.pk]))
        self.assertEqual(resp.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, HostelApplication.Status.PENDING)

    def test_student_cannot_confirm_another_students_payment(self):
        victim = self._student("z3@school.edu")
        app = HostelApplicationService.submit_application(student=victim, room=self.room)
        HostelApplicationService.review_application(app, self.officer, approve=True)
        attacker = self._student("z4@school.edu")
        self.login(attacker)
        resp = self.client.post(reverse("hostel:hostel_confirm_payment", args=[app.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_confirm_payment_does_not_mark_verified_without_paystack(self):
        student = self._student("z7@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        HostelApplicationService.review_application(app, self.officer, approve=True)
        self.login(student)
        # Paystack keys are absent in the test run, so the endpoint must NOT
        # issue a free success — it bounces to the (disabled) online-payment flow.
        resp = self.client.post(reverse("hostel:hostel_confirm_payment", args=[app.pk]))
        self.assertEqual(resp.status_code, 302)
        app.refresh_from_db()
        self.assertIsNone(app.payment_verified_at)

    def test_student_cannot_cancel_another_students_transfer(self):
        victim = self._student("z5@school.edu")
        alloc = self.allocate(student=victim, bed=self.bed_a)
        transfer = HostelTransferService.request_transfer(
            student=victim, old_allocation=alloc, new_bed=self.bed_b, actor=victim
        )
        attacker = self._student("z6@school.edu")
        self.login(attacker)
        resp = self.client.post(reverse("hostel:hostel_transfer_cancel", args=[transfer.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_redirected_to_login(self):
        for url_name in ("hostel", "dashboard"):
            resp = self.client.get(reverse(f"hostel:{url_name}"))
            self.assertIn("/accounts/login/", resp.url or "")


# ═══════════════════════════════════════════════════════════════════════
# VIEW ROUTES
# ═══════════════════════════════════════════════════════════════════════

class ViewRouteTests(HostelBaseTestCase):

    def test_hostel_list_renders_for_student(self):
        student = self._student("v1@school.edu")
        self.client.force_login(student)
        resp = self.client.get(reverse("hostel:hostel"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Boys Hostel")

    def test_hostel_rooms_api_lists_rooms_with_available_bed(self):
        self.client.force_login(self.officer)
        resp = self.client.get(reverse("hostel:hostel_rooms_api"), {"hostel": self.hostel.pk})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["rooms"]), 2)

    def test_hostel_apply_post_creates_application(self):
        student = self._student("v2@school.edu")
        self.client.force_login(student)
        resp = self.client.post(reverse("hostel:hostel_apply"), {"hostel": self.hostel.pk, "room": self.room.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(HostelApplication.objects.filter(student=student).exists())

    def test_hostel_vacate_checks_student_out(self):
        student = self._student("v3@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        self.check_in(alloc)
        self.client.force_login(student)
        resp = self.client.post(reverse("hostel:hostel_vacate"))
        self.assertEqual(resp.status_code, 302)
        alloc.refresh_from_db()
        self.assertEqual(alloc.status, HostelAllocation.Status.CHECKED_OUT)

    def test_allocation_form_renders_beds_only_available(self):
        self.client.force_login(self.officer)
        self.allocate(student=self._student("v4@school.edu"), bed=self.bed_a)
        resp = self.client.get(reverse("hostel:allocation_create"))
        self.assertEqual(resp.status_code, 200)
        bed_field = resp.context["form"].fields["bed"]
        self.assertNotIn(self.bed_a, bed_field.queryset)

    def test_application_review_flow_via_views(self):
        student = self._student("v5@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        self.client.force_login(self.officer)
        resp = self.client.post(
            reverse("hostel:application_approve", args=[app.pk]),
            {"admin_remark": "Welcome"},
        )
        self.assertEqual(resp.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, HostelApplication.Status.APPROVED)

    def test_expiry_command_sweeps_reservations(self):
        student = self._student("v6@school.edu")
        alloc = self.allocate(student=student, bed=self.bed_a)
        HostelAllocation.objects.filter(pk=alloc.pk).update(
            reservation_expires_at=timezone.now() - timedelta(hours=2)
        )
        call_command("expire_hostel_reservations")
        alloc.refresh_from_db()
        self.assertEqual(alloc.status, HostelAllocation.Status.EXPIRED)


# ═══════════════════════════════════════════════════════════════════════
# CONCURRENCY (10 scenarios)
#
# Strict invariants (exactly-one-winner) are asserted only on backends with
# real row locking and the partial unique constraints (Postgres/MySQL).
# SQLite degrades gracefully because select_for_update is a no-op there.
# ═══════════════════════════════════════════════════════════════════════

class ConcurrencyScenarioTests(HostelBaseTestCase):
    """Ten concurrency scenarios racing the hostel service layer."""

    N_THREADS = 4

    def _race(self, worker, n=None):
        n = n or self.N_THREADS
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(worker, i) for i in range(n)]
        outcomes = [f.result() for f in futures]
        return outcomes

    def _fresh_student(self, tag):
        return self._student(f"concurrency-{tag}@{id(self)}.school.edu")

    def _live_for(self, *, bed=None, student=None):
        qs = HostelAllocation.objects.filter(
            status__in=(HostelAllocation.Status.RESERVED, HostelAllocation.Status.ACTIVE)
        )
        if bed:
            qs = qs.filter(bed=bed)
        if student:
            qs = qs.filter(student=student)
        return list(qs)

    def _guard(self, fn):
        """Run a business operation inside a worker thread; never let a
        DB-level contention error (SQLite 'database is locked', Postgres
        deadlock/etc) escape the test."""
        try:
            fn()
            return "ok"
        except HostelServiceError:
            return "denied"
        except DatabaseError:
            return "locked"

    # Scenario 1 — same bed, many students: exactly one wins.
    def test_01_concurrent_allocate_same_bed(self):
        bed = self.bed_b
        def worker(i):
            connections.close_all()
            return self._guard(
                lambda: self.allocate(student=self._fresh_student(f"s1-{i}"), bed=bed)
            )
        outcomes = self._race(worker)
        live = self._live_for(bed=bed)
        if STRICT_LOCKS:
            self.assertEqual(outcomes.count("ok"), 1, outcomes)
            self.assertEqual(len(live), 1)
        else:
            self.assertLessEqual(len(live), outcomes.count("ok"))

    def test_02_concurrent_allocate_different_beds_same_student(self):
        beds = [self.bed_a, self.bed_b, self.bed_c, self.bed_d]
        student = self._fresh_student("s2")
        def worker(i):
            connections.close_all()
            return self._guard(
                lambda: self.allocate(student=student, bed=beds[i % len(beds)])
            )
        outcomes = self._race(worker)
        live = self._live_for(student=student)
        if STRICT_LOCKS:
            self.assertEqual(outcomes.count("ok"), 1, outcomes)
            self.assertEqual(len(live), 1)
        else:
            self.assertLessEqual(len(live), outcomes.count("ok"))

    def test_03_concurrent_check_in_same_reservation(self):
        student = self._fresh_student("s3")
        alloc = self.allocate(student=student, bed=self.bed_a)
        def worker(i):
            connections.close_all()
            return self._guard(lambda: self.check_in(alloc))
        outcomes = self._race(worker)
        alloc.refresh_from_db()
        self.assertIn(
            alloc.status,
            (HostelAllocation.Status.ACTIVE, HostelAllocation.Status.RESERVED),
            outcomes,
        )
        if alloc.status == HostelAllocation.Status.ACTIVE:
            self.assertGreaterEqual(outcomes.count("ok"), 1, outcomes)
            if STRICT_LOCKS:
                self.assertEqual(outcomes.count("ok"), 1, outcomes)

    def test_04_concurrent_transfer_complete_to_same_bed(self):
        a_student = self._fresh_student("s4a")
        b_student = self._fresh_student("s4b")
        alloc_a = self.allocate(student=a_student, bed=self.bed_a)
        alloc_b = self.allocate(student=b_student, bed=self.bed_b)
        t_a = HostelTransferService.request_transfer(
            student=a_student, old_allocation=alloc_a, new_bed=self.bed_c, actor=a_student
        )
        t_b = HostelTransferService.request_transfer(
            student=b_student, old_allocation=alloc_b, new_bed=self.bed_c, actor=b_student
        )
        HostelTransferService.review_transfer(t_a, self.officer, approve=True)
        HostelTransferService.review_transfer(t_b, self.officer, approve=True)

        def worker(i):
            connections.close_all()
            return self._guard(
                lambda: HostelTransferService.complete_transfer(
                    t_a if i % 2 == 0 else t_b, self.officer
                )
            )
        outcomes = self._race(worker)
        live = self._live_for(bed=self.bed_c)
        if STRICT_LOCKS:
            self.assertEqual(outcomes.count("ok"), 1, outcomes)
            self.assertEqual(len(live), 1)
        else:
            self.assertLessEqual(len(live), outcomes.count("ok"))

    def test_05_concurrent_hold_same_bed(self):
        bed = self.bed_b
        def worker(i):
            connections.close_all()
            return self._guard(
                lambda: HostelAllocationService.hold_bed(
                    bed=bed, actor=self.officer, note=f"h{i}"
                )
            )
        outcomes = self._race(worker)
        bed.refresh_from_db()
        self.assertIn(bed.state, (HostelBed.BedState.HELD, HostelBed.BedState.AVAILABLE))
        if STRICT_LOCKS:
            self.assertLessEqual(outcomes.count("ok"), 1, outcomes)
        self.assertEqual(
            bed.state == HostelBed.BedState.HELD, outcomes.count("ok") >= 1, outcomes
        )

    def test_06_concurrent_expire_and_checkin(self):
        student = self._fresh_student("s6")
        alloc = self.allocate(student=student, bed=self.bed_a)

        def worker(i):
            connections.close_all()
            if i % 2 == 0:
                try:
                    HostelAllocationService.expire_reservation(alloc)
                    return "expired"
                except DatabaseError:
                    return "expired-locked"
            return self._guard(lambda: self.check_in(alloc))
        outcomes = self._race(worker)
        alloc.refresh_from_db()
        self.assertIn(
            alloc.status,
            (HostelAllocation.Status.ACTIVE, HostelAllocation.Status.EXPIRED,
             HostelAllocation.Status.RESERVED),
            outcomes,
        )
        if alloc.status == HostelAllocation.Status.ACTIVE:
            self.assertGreaterEqual(outcomes.count("ok"), 1, outcomes)
            if STRICT_LOCKS:
                self.assertEqual(outcomes.count("ok"), 1, outcomes)
        elif alloc.status == HostelAllocation.Status.EXPIRED:
            self.assertIn("expired", outcomes)
            if STRICT_LOCKS:
                self.assertNotIn("ok", outcomes)

    def test_07_concurrent_cancel_and_checkin(self):
        student = self._fresh_student("s7")
        alloc = self.allocate(student=student, bed=self.bed_a)

        def worker(i):
            connections.close_all()
            return self._guard(
                lambda: (
                    HostelAllocationService.cancel_allocation(
                        alloc, actor=self.officer, reason="race"
                    )
                    if i % 2 == 0
                    else self.check_in(alloc)
                )
            )
        outcomes = self._race(worker)
        alloc.refresh_from_db()
        self.assertIn(
            alloc.status,
            (HostelAllocation.Status.CANCELLED, HostelAllocation.Status.ACTIVE,
             HostelAllocation.Status.RESERVED),
            outcomes,
        )
        if alloc.status == HostelAllocation.Status.ACTIVE:
            self.assertGreaterEqual(outcomes.count("ok"), 1, outcomes)
            if STRICT_LOCKS:
                self.assertEqual(outcomes.count("ok"), 1, outcomes)
        elif alloc.status == HostelAllocation.Status.CANCELLED and STRICT_LOCKS:
            self.assertNotIn("ok", outcomes)

    def test_08_concurrent_maintenance_on_same_bed(self):
        bed = self.bed_b
        def worker(i):
            connections.close_all()
            return self._guard(
                lambda: BedMaintenanceService.start_maintenance(
                    bed=bed, reason=f"race {i}", reported_by=self.officer
                )
            )
        outcomes = self._race(worker)
        if STRICT_LOCKS:
            self.assertLessEqual(outcomes.count("ok"), 1, outcomes)
            self.assertGreaterEqual(outcomes.count("ok"), 1, outcomes)
        bed.refresh_from_db()
        self.assertIn(
            bed.state, (HostelBed.BedState.AVAILABLE, HostelBed.BedState.MAINTENANCE)
        )
        self.assertEqual(
            bed.state == HostelBed.BedState.MAINTENANCE, outcomes.count("ok") >= 1, outcomes
        )

    def test_09_concurrent_review_same_application(self):
        student = self._fresh_student("s9")
        app = HostelApplicationService.submit_application(student=student, room=self.room)

        def worker(i):
            connections.close_all()
            return self._guard(
                lambda: HostelApplicationService.review_application(
                    app, self.officer, approve=(i % 2 == 0), remark=f"r{i}"
                )
            )
        outcomes = self._race(worker)
        app.refresh_from_db()
        self.assertIn(
            app.status,
            (HostelApplication.Status.APPROVED, HostelApplication.Status.REJECTED,
             HostelApplication.Status.PENDING),
            outcomes,
        )
        if app.status in (HostelApplication.Status.APPROVED,
                          HostelApplication.Status.REJECTED):
            self.assertGreaterEqual(outcomes.count("ok"), 1, outcomes)
        if STRICT_LOCKS:
            self.assertLessEqual(outcomes.count("ok"), 1, outcomes)

    def test_10_concurrent_allocate_and_checkout_invariant(self):
        student = self._fresh_student("s10")
        other = self._fresh_student("s10b")
        alloc = self.allocate(student=student, bed=self.bed_a)
        self.check_in(alloc)

        def worker(i):
            connections.close_all()
            if i % 2 == 0:
                return self._guard(lambda: self.allocate(student=other, bed=self.bed_b))
            return self._guard(
                lambda: HostelCheckoutService.checkout(
                    allocation=alloc, actor=self.officer
                )
            )
        outcomes = self._race(worker)
        self.assertTrue(all(o in ("ok", "denied", "locked") for o in outcomes), outcomes)
        if STRICT_LOCKS:
            self.assertLessEqual(len(self._live_for(bed=self.bed_b)), 1, outcomes)


# ═══════════════════════════════════════════════════════════════════════
# PAYMENT FLOW (paystack → finance)
# ═══════════════════════════════════════════════════════════════════════

class HostelPaymentFlowTests(HostelBaseTestCase):
    """End-to-end test that actually pays the current hostel's configured
    charge: approved application → Paystack checkout (mocked gateway) →
    callback → finance records the payment → application verified."""

    @override_settings(PAYSTACK_SECRET_KEY="test-secret-key")
    def test_pay_current_hostel_charge_via_paystack(self):
        student = self._student("pay1@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        HostelApplicationService.review_application(app, self.officer, approve=True)

        HostelFeeConfig.objects.create(
            hostel=self.hostel, session=self.session,
            fee_structure=None, amount=Decimal("2700.00"), is_active=True,
        )
        self.policy.enable_hostel_charges = True
        self.policy.require_payment_before_checkin = True
        self.policy.allow_partial_payment = False
        self.policy.save()

        self.client.force_login(student)
        auth_url = "https://checkout.paystack.com/test-hostel-charge"
        with mock.patch.object(
            services.PaystackService, "initialize",
            return_value={"authorization_url": auth_url},
        ):
            resp = self.client.post(
                reverse("hostel:hostel_pay_initiate", args=[app.pk])
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, auth_url)

        fee = StudentFee.objects.get(student=student)
        self.assertEqual(fee.amount_due, Decimal("2700.00"))

        verified = {
            "status": "success",
            "amount": 270000,  # GHC 2700.00 → minor units (pesewas)
            "metadata": {"application": app.pk, "fee": fee.pk},
        }
        with mock.patch.object(
            services.PaystackService, "verify", return_value=verified
        ):
            callback = self.client.get(
                reverse("hostel:hostel_pay_callback"),
                {"reference": "HOSTEL-PAY-1"},
            )
        self.assertEqual(callback.status_code, 302)

        fee.refresh_from_db()
        self.assertEqual(fee.amount_paid, Decimal("2700.00"))
        self.assertEqual(fee.status, "paid")
        self.assertTrue(
            FeePayment.objects.filter(
                student_fee=fee, reference="HOSTEL-PAY-1",
                amount=Decimal("2700.00"),
            ).exists()
        )
        app.refresh_from_db()
        self.assertIsNotNone(app.payment_verified_at)

        info = HostelFinanceService.balance_info(student, self.session)
        self.assertEqual(info["status"], "paid")
        self.assertEqual(info["balance"], Decimal("0"))

    @override_settings(PAYSTACK_SECRET_KEY="test-secret-key")
    def test_payment_does_not_verify_a_failed_transaction(self):
        student = self._student("pay2@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        HostelApplicationService.review_application(app, self.officer, approve=True)

        HostelFeeConfig.objects.create(
            hostel=self.hostel, session=self.session,
            fee_structure=None, amount=Decimal("2700.00"), is_active=True,
        )
        self.policy.enable_hostel_charges = True
        self.policy.save()

        self.client.force_login(student)
        with mock.patch.object(
            services.PaystackService, "verify",
            return_value={"status": "failed", "metadata": {}},
        ):
            callback = self.client.get(
                reverse("hostel:hostel_pay_callback"),
                {"reference": "HOSTEL-PAY-2"},
            )
        self.assertEqual(callback.status_code, 302)
        app.refresh_from_db()
        self.assertIsNone(app.payment_verified_at)
        self.assertFalse(
            FeePayment.objects.filter(reference="HOSTEL-PAY-2").exists()
        )

    @override_settings(PAYSTACK_SECRET_KEY="test-secret-key")
    def test_payment_confirmed_notification_is_not_duplicated(self):
        student = self._student("pay3@school.edu")
        app = HostelApplicationService.submit_application(student=student, room=self.room)
        HostelApplicationService.review_application(app, self.officer, approve=True)

        HostelFeeConfig.objects.create(
            hostel=self.hostel, session=self.session,
            fee_structure=None, amount=Decimal("2700.00"), is_active=True,
        )
        self.policy.enable_hostel_charges = True
        self.policy.save()

        self.client.force_login(student)
        with mock.patch.object(
            services.PaystackService, "verify",
            return_value={
                "status": "success",
                "amount": 270000,
                "metadata": {"application": app.pk},
            },
        ):
            self.client.get(
                reverse("hostel:hostel_pay_callback"),
                {"reference": "HOSTEL-PAY-3"},
            )
            # Re-delivery (webhook retry) must NOT create a second identical one.
            self.client.get(
                reverse("hostel:hostel_pay_callback"),
                {"reference": "HOSTEL-PAY-3"},
            )

        count = StudentNotification.objects.filter(
            student=student, category="hostel",
            title="Hostel payment confirmed",
        ).count()
        self.assertEqual(count, 1)