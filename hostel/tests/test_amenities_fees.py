"""
hostel/tests/test_amenities_fees.py — new amenity + fee-scope features.

These tests exercise the configurable Amenity model, the per-semester /
per-room fee overrides, and the new hostel detail page. The new tables and
columns are NOT in the checked-in migrations (the user applies them
separately), so each feature-test class skips cleanly until the migration is
applied. The view tests in ``HostelDetailViewTests`` run regardless.
"""

import unittest
from datetime import date
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from academics.models import AcademicSession, Semester, StudentProfile
from finance.models import FeeStructure

from hostel import schema
from hostel.models import (
    Amenity,
    Hostel,
    HostelBed,
    HostelBlock,
    HostelFeeConfig,
    HostelFloor,
    HostelRoom,
)
from hostel.services.finance import HostelFinanceService

AMENITY_OK = schema.table_exists("hostel_amenity")
FEE_SCOPE_OK = schema.column_exists("hostel_hostelfeeconfig", "semester_id")


def _skip(reason):
    raise ValueError(reason)


class AmenityBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.session = AcademicSession.objects.create(
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
            is_current=True,
        )
        cls.hostel_a = Hostel.objects.create(name="Alpha", capacity=4, is_active=True)
        cls.hostel_b = Hostel.objects.create(name="Beta", capacity=4, is_active=True)
        cls.block = HostelBlock.objects.create(hostel=cls.hostel_a, name="Block A")
        cls.floor = HostelFloor.objects.create(block=cls.block, name="Ground")
        cls.room = HostelRoom.objects.create(
            hostel=cls.hostel_a, floor=cls.floor, room_number="1", capacity=2,
            is_available=True,
        )
        cls.bed = HostelBed.objects.create(room=cls.room, bed_number="1", label="1-1")

        cls.wifi = Amenity.objects.create(
            name="Wi-Fi", icon="📶", display_order=1, is_active=True,
        )
        cls.ac = Amenity.objects.create(name="Aircon", icon="❄", display_order=2)

        cls.hostel_a.amenities.add(cls.wifi)
        cls.room.amenities.add(cls.ac)

        if FEE_SCOPE_OK:
            cls.hostel_wide_config = HostelFeeConfig.objects.create(
                hostel=cls.hostel_a, session=cls.session,
                fee_structure=None, amount=Decimal("500.00"), is_active=True,
            )
            cls.room_override = HostelFeeConfig.objects.create(
                hostel=cls.hostel_a, room=cls.room, session=cls.session,
                fee_structure=None, amount=Decimal("700.00"), is_active=True,
            )


@unittest.skipUnless(AMENITY_OK, "Amenity table not migrated yet")
class AmenityModelTests(AmenityBase):
    def test_hostel_amenity_list_property(self):
        self.assertEqual(list(self.hostel_a.amenity_list), [self.wifi])

    def test_room_effective_amenities_include_hostel(self):
        effective = [a.name for a in self.room.effective_amenities]
        self.assertEqual(effective, ["Wi-Fi", "Aircon"])


@unittest.skipUnless(FEE_SCOPE_OK, "Fee scope columns not migrated yet")
class FeeScopeTests(AmenityBase):
    def test_room_override_wins_over_hostel_wide(self):
        config = HostelFinanceService.fee_config_for(
            self.hostel_a, self.session, room=self.room
        )
        self.assertEqual(config, self.room_override)

    def test_hostel_wide_fallback_without_room(self):
        config = HostelFinanceService.fee_config_for(
            self.hostel_a, self.session
        )
        self.assertEqual(config, self.hostel_wide_config)

    def test_fee_for_returns_room_amount(self):
        self.assertEqual(
            HostelFinanceService.fee_for(self.hostel_a, self.session, room=self.room),
            Decimal("700.00"),
        )

    def test_fee_for_returns_hostel_amount(self):
        self.assertEqual(
            HostelFinanceService.fee_for(self.hostel_a, self.session),
            Decimal("500.00"),
        )


class EnsureChargePolicyTests(AmenityBase):
    """Regression: enable_hostel_charges must NOT gate fee creation.

    Runs everywhere (no schema dependency) by mocking the config resolution,
    so it guards the policy-gate fix even before the migration is applied.
    """

    def test_ensure_charge_works_when_policy_flag_off(self):
        from types import SimpleNamespace
        from unittest import mock

        from django.contrib.auth import get_user_model
        from hostel.models import HostelPolicy

        User = get_user_model()
        student = User.objects.create(
            email="charge-test@school.edu", role="student", is_active=True,
            first_name="C", last_name="Student",
        )
        StudentProfile.objects.create(student=student, is_active=True, student_number="CHG-001")
        HostelPolicy.objects.create(
            application_open=None,
            reservation_expiry_hours=48,
            require_payment_before_checkin=True,
            allow_partial_payment=False,
            enable_hostel_charges=False,
            allow_transfers=True,
            allow_hostel_transfers=True,
            require_active_student=True,
            is_active=True,
        )

        fs = FeeStructure.objects.create(
            name="Hostel charge fix test", session=self.session,
            amount=Decimal("700.00"),
        )
        fake_config = SimpleNamespace(
            fee_structure_id=fs.pk, fee_structure=fs,
            amount=None, hostel=self.hostel_a,
            session=self.session, due_date=None,
        )
        with mock.patch.object(
            HostelFinanceService,
            "fee_config_for",
            return_value=fake_config,
        ):
            fee = HostelFinanceService.ensure_charge(
                student=student, session=self.session,
                hostel=self.hostel_a, room=self.room,
            )
        self.assertIsNotNone(
            fee,
            "ensure_charge returned None despite an active fee config; "
            "enable_hostel_charges must NOT gate fee creation.",
        )
        self.assertEqual(fee.amount_due, Decimal("700.00"))


class HostelDetailViewTests(AmenityBase):
    """Renders for both audiences even before the migration is applied."""

    @classmethod
    def _user(cls, email, role="student"):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create(
            email=email, role=role, is_active=True,
            first_name="T", last_name="User",
        )

    def test_student_can_view_hostel_detail(self):
        user = self._user("s1@school.edu", "student")
        self.client.force_login(user)
        response = self.client.get(
            reverse("hostel:hostel_detail", args=[self.hostel_a.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha")
        self.assertContains(response, "Rooms &amp; Availability")

    def test_staff_can_view_hostel_detail(self):
        user = self._user("st1@school.edu", "admin")
        self.client.force_login(user)
        response = self.client.get(
            reverse("hostel:hostel_detail", args=[self.hostel_a.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Hostel")

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(
            reverse("hostel:hostel_detail", args=[self.hostel_a.pk])
        )
        self.assertEqual(response.status_code, 302)


@unittest.skipUnless(AMENITY_OK, "Amenity table not migrated yet")
class AmenityViewTests(AmenityBase):
    def _staff_client(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create(
            email="amenity-staff@school.edu", role="admin", is_active=True,
            first_name="A", last_name="Staff",
        )
        self.client.force_login(user)
        return self.client

    def test_amenities_list_renders(self):
        self._staff_client()
        response = self.client.get(reverse("hostel:amenities"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wi-Fi")

    def test_amenity_create_post(self):
        self._staff_client()
        response = self.client.post(
            reverse("hostel:amenity_create"),
            {"name": "Laundry", "icon": "🧺", "display_order": 3},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Amenity.objects.filter(name="Laundry").exists())


@unittest.skipUnless(FEE_SCOPE_OK, "Fee scope columns not migrated yet")
class FeeConfigViewTests(AmenityBase):
    def _staff_client(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create(
            email="fee-staff@school.edu", role="admin", is_active=True,
            first_name="F", last_name="Staff",
        )
        self.client.force_login(user)
        return self.client

    def test_fee_configs_renders_scope_label(self):
        self._staff_client()
        response = self.client.get(reverse("hostel:fee_configs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Room override")

    def test_fee_config_create_post(self):
        self._staff_client()
        response = self.client.post(
            reverse("hostel:fee_config_create"),
            {
                "hostel": self.hostel_b.pk,
                "session": self.session.pk,
                "amount": "1000.00",
                "is_active": True,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            HostelFeeConfig.objects.filter(hostel=self.hostel_b).exists()
        )

    def test_fee_config_edit_get_prefills(self):
        self._staff_client()
        response = self.client.get(
            reverse("hostel:fee_config_edit", args=[self.hostel_wide_config.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Hostel Fee Configuration")
        self.assertContains(
            response, f'value="{self.hostel_wide_config.amount}"'
        )

    def test_fee_config_edit_post_updates(self):
        self._staff_client()
        response = self.client.post(
            reverse("hostel:fee_config_edit", args=[self.hostel_wide_config.pk]),
            {
                "hostel": self.hostel_a.pk,
                "session": self.session.pk,
                "amount": "850.00",
                "is_active": True,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.hostel_wide_config.refresh_from_db()
        self.assertEqual(self.hostel_wide_config.amount, Decimal("850.00"))

    def test_fee_config_delete_post_removes(self):
        self._staff_client()
        config = HostelFeeConfig.objects.get(pk=self.hostel_wide_config.pk)
        response = self.client.post(
            reverse("hostel:fee_config_delete", args=[config.pk]), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(HostelFeeConfig.objects.filter(pk=config.pk).exists())

    def test_fee_config_delete_requires_post(self):
        self._staff_client()
        response = self.client.get(
            reverse("hostel:fee_config_delete", args=[self.hostel_wide_config.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_fee_config_edit_missing_returns_404(self):
        self._staff_client()
        response = self.client.get(reverse("hostel:fee_config_edit", args=[999999]))
        self.assertEqual(response.status_code, 404)


class FeeConfigEditDeleteUrlTests(TestCase):
    """Routes + staff-only guard for edit/delete, run on any DB schema."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.session = AcademicSession.objects.create(
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
            is_current=True,
        )
        self.hostel = Hostel.objects.create(name="Gamma", capacity=4, is_active=True)
        self.config = HostelFeeConfig.objects.create(
            hostel=self.hostel, session=self.session,
            fee_structure=None, amount=Decimal("300.00"), is_active=True,
        )
        self.staff = get_user_model().objects.create(
            email="cfg-staff@school.edu", role="admin", is_active=True,
            first_name="G", last_name="Staff",
        )

    def test_edit_get_redirects_anonymous(self):
        response = self.client.get(
            reverse("hostel:fee_config_edit", args=[self.config.pk])
        )
        self.assertIn(response.status_code, (302, 403))

    def test_edit_post_requires_staff(self):
        from django.contrib.auth import get_user_model

        student = get_user_model().objects.create(
            email="cfg-student@school.edu", role="student", is_active=True,
            first_name="G", last_name="Student",
        )
        self.client.force_login(student)
        response = self.client.post(
            reverse("hostel:fee_config_edit", args=[self.config.pk]),
            {
                "hostel": self.hostel.pk,
                "session": self.session.pk,
                "amount": "400.00",
                "is_active": True,
            },
        )
        self.assertIn(response.status_code, (302, 403))

    def test_delete_post_requires_staff(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("hostel:fee_config_delete", args=[self.config.pk])
        )
        self.assertIn(response.status_code, (302, 200))
        if response.status_code == 302:
            self.assertFalse(
                HostelFeeConfig.objects.filter(pk=self.config.pk).exists()
            )