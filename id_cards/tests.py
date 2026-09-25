"""
id_cards/tests.py

Service-layer and view coverage for the Student ID Card module.

* Photo upload / re-upload overwrite semantics and validation.
* Preflight readiness (level changes are NEVER part of the checks).
* Single + batch generation, unique constraints, lifecycle transitions.
* Replacement workflow (old card invalidated, new card generated).
* Expiry processing (with and without auto-renewal).
* Public QR verification outcomes.
* RBAC scoping (admin / ID-card officer / HOD department scope).
* Key HTTP views (student portal, staff review, public verify).
"""

import io
import re
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import EduProUser, Role, StaffResponsibility, UserStaffRole
from academics.models import Department, Faculty, Institution, Program, StudentProfile

from . import services
from . import renderers
from .models import (
    AuditAction,
    BatchStatus,
    CardStatus,
    IDCard,
    IDCardAuditLog,
    IDCardBatch,
    IDCardSettings,
    IDCardTemplate,
    PassportPhoto,
    PhotoStatus,
    ReplacementRequest,
    ReplacementStatus,
    TemplateStatus,
)
from .services import (
    CardGenerationError,
    PREFLIGHT_EXISTING_ACTIVE_CARD,
    PREFLIGHT_NO_TEMPLATE,
    PREFLIGHT_PHOTO_MISSING,
    PREFLIGHT_PHOTO_PENDING,
    PREFLIGHT_PHOTO_REJECTED,
    PREFLIGHT_READY,
    approve_photo,
    approve_replacement_request,
    cancel_card,
    create_replacement_request,
    generate_batch,
    generate_card_for_student,
    issue_card,
    mark_damaged,
    mark_lost,
    mark_printed,
    preflight,
    process_expirations,
    reject_photo,
    replace_card_direct,
    scoped_students,
    upload_photo,
    verify_token,
)

from .validators import PhotoValidationError


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def make_photo_file(size=(600, 600), color="#4a7db4", fmt="JPEG", name="photo.jpg"):
    """A valid portrait passport-sized JPEG/PNG in memory."""
    buffer = io.BytesIO()
    image = Image.new("RGB", size, color)
    image.save(buffer, format=fmt)
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type=f"image/{fmt.lower()}")


def student_institution(student):
    try:
        return student.academic_profile.program.department.institution
    except AttributeError:
        return None


class IDCardTestCase(TestCase):
    """Base fixture: two institutions incl. departments, programmes, staff."""

    @classmethod
    def setUpTestData(cls):
        cls.inst_a = Institution.objects.create(name="Test University A")
        cls.inst_b = Institution.objects.create(name="Test University B")
        IDCardSettings.objects.get_or_create(institution=cls.inst_a)
        IDCardSettings.objects.get_or_create(institution=cls.inst_b)

        cls.fac_a = Faculty.objects.create(institution=cls.inst_a, name="Science", code="SCI")
        cls.dept_a = Department.objects.create(
            institution=cls.inst_a, faculty=cls.fac_a, name="Computer Science", code="CS",
        )
        cls.prog_a = Program.objects.create(
            department=cls.dept_a, name="BSc Computer Science", code="CS-BSC",
        )

        cls.fac_b = Faculty.objects.create(institution=cls.inst_b, name="Arts", code="ART")
        cls.dept_b = Department.objects.create(
            institution=cls.inst_b, faculty=cls.fac_b, name="English", code="ENG",
        )
        cls.prog_b = Program.objects.create(
            department=cls.dept_b, name="BA English", code="ENG-BA",
        )

        # ── Staff ──────────────────────────────────────────────────────────
        cls.admin = EduProUser.objects.create_approved_user(
            "admin@test.edu", first_name="Ada", last_name="Admin", role=Role.ADMIN,
        )
        cls.officer = EduProUser.objects.create_approved_user(
            "officer@test.edu", first_name="Oz", last_name="Officer",
            role=Role.TEACHER,
        )
        UserStaffRole.objects.create(
            user=cls.officer, responsibility=StaffResponsibility.ID_CARD_OFFICER,
        )
        cls.hod = EduProUser.objects.create_approved_user(
            "hod@test.edu", first_name="Han", last_name="Od", role=Role.TEACHER,
        )
        UserStaffRole.objects.create(
            user=cls.hod, responsibility=StaffResponsibility.HOD,
            department=cls.dept_a,
        )
        cls.hod_b = EduProUser.objects.create_approved_user(
            "hod-b@test.edu", first_name="Bey", last_name="Hod", role=Role.TEACHER,
        )
        UserStaffRole.objects.create(
            user=cls.hod_b, responsibility=StaffResponsibility.HOD,
            department=cls.dept_b,
        )

        # ── Students ───────────────────────────────────────────────────────
        cls.student = cls._make_student("s1@test.edu", "Una", "Student", cls.prog_a, "S001001")
        cls.student_b = cls._make_student("s2@test.edu", "Two", "StudentB", cls.prog_b, "S002001")

        # ── Default-active template for institution A ──────────────────────
        cls.template = IDCardTemplate.objects.create(
            institution=cls.inst_a,
            name="Standard Card A",
            status=TemplateStatus.ACTIVE,
            is_default=True,
            created_by=cls.admin,
        )

    @classmethod
    def _make_student(cls, email, first, last, program, number):
        user = EduProUser.objects.create_approved_user(
            email, first_name=first, last_name=last, role=Role.STUDENT,
        )
        StudentProfile.objects.create(
            student=user, program=program, student_number=number, is_active=True,
        )
        return user

    def setUp(self):
        self.settings_ctx = override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="idcards-test-"))
        self.settings_ctx.enable()
        self.addCleanup(self.settings_ctx.disable)

    def _ready_student(self, student):
        """Photo approved + no live card ⇒ PREFLIGHT_READY."""
        photo = upload_photo(student, make_photo_file(), actor=self.officer)
        return approve_photo(photo, self.officer)

    def _card(self, student=None, expire_years=None, **overrides):
        """Generate a card for a student (status GENERATED unless overridden)."""
        student = student or self.student
        self._ready_student(student)
        return generate_card_for_student(student, self.officer, **overrides)


# ─────────────────────────────────────────────────────────────────────────────
# PHOTO UPLOAD / VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

class PhotoUploadTests(IDCardTestCase):
    def test_upload_creates_pending_photo_and_audits(self):
        photo = upload_photo(self.student, make_photo_file(), actor=self.student)
        self.assertEqual(PassportPhoto.objects.count(), 1)
        self.assertEqual(photo.status, PhotoStatus.PENDING_APPROVAL)
        self.assertTrue(IDCardAuditLog.objects.filter(
            action=AuditAction.PHOTO_UPLOADED, photo=photo,
        ).exists())

    def test_reupload_overwrites_same_row_and_resets_cycle(self):
        first = upload_photo(self.student, make_photo_file(), actor=self.student)
        approve_photo(first, self.officer)
        first_id = first.pk
        first_bytes = first.image.read()
        upload_photo(self.student, make_photo_file(color="#123456"), actor=self.student)
        self.assertEqual(PassportPhoto.objects.filter(student=self.student).count(), 1)
        photo = PassportPhoto.objects.get(pk=first_id)
        self.assertEqual(photo.status, PhotoStatus.PENDING_APPROVAL)
        self.assertIsNone(photo.reviewed_by)
        self.assertIsNone(photo.reviewed_at)
        self.assertNotEqual(photo.image.read(), first_bytes)

    def test_invalid_photo_rejected(self):
        too_small = make_photo_file(size=(50, 50))
        with self.assertRaises(PhotoValidationError):
            upload_photo(self.student, too_small, actor=self.student)
        self.assertEqual(PassportPhoto.objects.count(), 0)

        landscape = make_photo_file(size=(800, 500))
        with self.assertRaises(PhotoValidationError):
            upload_photo(self.student, landscape, actor=self.student)

        png = make_photo_file(fmt="PNG", name="photo.png")
        photo = upload_photo(self.student, png, actor=self.student)
        self.assertEqual(photo.status, PhotoStatus.PENDING_APPROVAL)

    def test_auto_approve_when_review_disabled(self):
        settings_obj = IDCardSettings.get_for(self.inst_a)
        settings_obj.require_photo_approval = False
        settings_obj.save()
        photo = upload_photo(self.student, make_photo_file(), actor=self.student)
        photo.refresh_from_db()
        self.assertEqual(photo.status, PhotoStatus.APPROVED)

    def test_reject_and_reapprove(self):
        photo = upload_photo(self.student, make_photo_file(), actor=self.student)
        reject_photo(photo, self.officer, "Not a clear headshot")
        photo.refresh_from_db()
        self.assertEqual(photo.status, PhotoStatus.REJECTED)
        self.assertEqual(photo.rejection_reason, "Not a clear headshot")
        approve_photo(photo, self.officer)
        photo.refresh_from_db()
        self.assertEqual(photo.status, PhotoStatus.APPROVED)
        self.assertEqual(photo.reviewed_by_id, self.officer.pk)


# ─────────────────────────────────────────────────────────────────────────────
# PREFLIGHT
# ─────────────────────────────────────────────────────────────────────────────

class PreflightTests(IDCardTestCase):
    def test_sequence_photo_missing_pending_approved(self):
        self.assertEqual(preflight(self.student)[0], PREFLIGHT_PHOTO_MISSING)
        photo = upload_photo(self.student, make_photo_file(), actor=self.student)
        self.assertEqual(preflight(self.student)[0], PREFLIGHT_PHOTO_PENDING)
        approve_photo(photo, self.officer)
        self.assertEqual(preflight(self.student)[0], PREFLIGHT_READY)

    def test_rejected_photo_blocks_ready(self):
        photo = upload_photo(self.student, make_photo_file(), actor=self.student)
        reject_photo(photo, self.officer, "Blurry")
        self.assertEqual(preflight(self.student)[0], PREFLIGHT_PHOTO_REJECTED)

    def test_no_template_blocks_ready(self):
        self._ready_student(self.student_b)
        code, _ = preflight(self.student_b)
        self.assertEqual(code, PREFLIGHT_NO_TEMPLATE)
        IDCardTemplate.objects.create(
            institution=self.inst_b, name="B Card", status=TemplateStatus.ACTIVE,
        )
        self.assertEqual(preflight(self.student_b)[0], PREFLIGHT_READY)

    def test_incompleteness_blocks_ready(self):
        # Remove student number → student can never be READY.
        profile = self.student.academic_profile
        profile.student_number = ""
        profile.save()
        self._ready_student(self.student)
        self.assertNotEqual(preflight(self.student)[0], PREFLIGHT_READY)


# ─────────────────────────────────────────────────────────────────────────────
# GENERATION
# ─────────────────────────────────────────────────────────────────────────────

class GenerationTests(IDCardTestCase):
    def test_generate_basic_card(self):
        card = self._card()
        card.refresh_from_db()
        self.assertEqual(card.status, CardStatus.GENERATED)
        self.assertEqual(card.student_id, self.student.pk)
        self.assertEqual(card.template_id, self.template.pk)
        self.assertRegex(card.card_number, r"^CARD-\d{4}-\d{6}$")
        self.assertEqual(len(card.verification_token), 32)
        self.assertIsNotNone(card.issue_date)
        self.assertEqual(
            card.expiry_date,
            card.issue_date + timezone.timedelta(days=365 * 4),
        )
        self.assertTrue(IDCardAuditLog.objects.filter(
            action=AuditAction.CARD_GENERATED, card=card,
        ).exists())

    def test_second_live_card_blocked(self):
        self._card()
        with self.assertRaises(CardGenerationError) as ctx:
            generate_card_for_student(self.student, self.officer)
        self.assertEqual(ctx.exception.code, PREFLIGHT_EXISTING_ACTIVE_CARD)

    def test_live_card_db_constraint(self):
        self._ready_student(self.student)
        generate_card_for_student(self.student, self.officer)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IDCard.objects.create(
                    student=self.student, institution=self.inst_a,
                    template=self.template, status=CardStatus.ACTIVE,
                    card_number="CARD-XYZ-1", verification_token="tok-x",
                    issue_date=timezone.localdate(),
                    expiry_date=timezone.localdate() + timezone.timedelta(days=30),
                )

    def test_card_number_sequence_increments(self):
        c1 = self._card()
        template_b = IDCardTemplate.objects.create(
            institution=self.inst_b, name="B", status=TemplateStatus.ACTIVE,
        )
        c2 = self._card(self.student_b, template=template_b)
        seq1 = int(c1.card_number.rsplit("-", 1)[1])
        seq2 = int(c2.card_number.rsplit("-", 1)[1])
        self.assertEqual(seq2, seq1 + 1)

    def test_generate_batch_reports_skips(self):
        # student A is ready; student B has an approved photo but no active
        # template for institution B -> skipped as NO_TEMPLATE.
        self._ready_student(self.student)
        photo_b = upload_photo(self.student_b, make_photo_file(color="#67222a"), actor=self.student_b)
        approve_photo(photo_b, self.officer)
        batch = generate_batch(
            [self.student, self.student_b], self.officer, notes="First run",
        )
        self.assertEqual(batch.total_selected, 2)
        self.assertEqual(batch.generated_count, 1)
        self.assertEqual(batch.skipped_count, 1)
        self.assertIn(PREFLIGHT_NO_TEMPLATE, batch.skips)
        self.assertEqual(batch.status, BatchStatus.GENERATED.value)
        self.assertEqual(batch.cards.count(), 1)

    def test_batch_filters_recorded(self):
        self._ready_student(self.student)
        batch = generate_batch([self.student], self.officer, filters={"q": "Una"})
        self.assertEqual(batch.filters, {"q": "Una"})


# ─────────────────────────────────────────────────────────────────────────────
# LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

class LifecycleTests(IDCardTestCase):
    def test_print_then_issue(self):
        card = self._card()
        mark_printed([card], self.officer)
        card.refresh_from_db()
        self.assertEqual(card.status, CardStatus.PRINTED)
        self.assertIsNotNone(card.printed_at)
        self.assertEqual(card.printed_by_id, self.officer.pk)

        issue_card(card, self.officer)
        card.refresh_from_db()
        self.assertEqual(card.status, CardStatus.ACTIVE)
        self.assertIsNotNone(card.issued_at)
        self.assertTrue(card.is_verified)

    def test_mark_printed_updates_batch(self):
        self._ready_student(self.student)
        batch = generate_batch([self.student], self.officer)
        card = batch.cards.first()
        mark_printed([card], self.officer)
        batch.refresh_from_db()
        self.assertEqual(batch.status, BatchStatus.PRINTED.value)

    def test_issue_invalid_state(self):
        card = self._card()
        cancel_card(card, self.officer, "Wrong identity")
        with self.assertRaises(CardGenerationError):
            issue_card(card, self.officer)

    def test_cancel_lost_damaged(self):
        card = self._card()
        cancel_card(card, self.officer, "Duplicate issue")
        card.refresh_from_db()
        self.assertEqual(card.status, CardStatus.CANCELLED)
        self.assertEqual(card.cancel_reason, "Duplicate issue")

        card2 = self._card(self.student_b, template=IDCardTemplate.objects.create(
            institution=self.inst_b, name="B", status=TemplateStatus.ACTIVE,
        ))
        mark_lost(card2, self.officer)
        card2.refresh_from_db()
        self.assertEqual(card2.status, CardStatus.LOST)

        new = replace_card_direct(card2, self.officer, reason="lost")
        mark_damaged(new, self.officer)
        new.refresh_from_db()
        self.assertEqual(new.status, CardStatus.DAMAGED)


# ─────────────────────────────────────────────────────────────────────────────
# REPLACEMENT
# ─────────────────────────────────────────────────────────────────────────────

class ReplacementTests(IDCardTestCase):
    def test_request_approve_workflow(self):
        card = self._card()
        req = create_replacement_request(
            self.student, card, "lost", details="Left in a taxi", actor=self.student,
        )
        self.assertEqual(req.status, ReplacementStatus.PENDING)
        new = approve_replacement_request(req, self.officer)

        req.refresh_from_db()
        card.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(req.status, ReplacementStatus.COMPLETED)
        self.assertEqual(req.replacement_card_id, new.pk)
        self.assertEqual(card.status, CardStatus.REPLACED)
        self.assertEqual(card.replaced_by_id, new.pk)

        # Old card no longer verifies; new card verifies once issued.
        old = verify_token(card.verification_token)
        self.assertTrue(old.found)
        self.assertFalse(old.valid)
        self.assertEqual(old.reason, "REPLACED")
        issue_card(new, self.officer)
        ok = verify_token(new.verification_token)
        self.assertTrue(ok.valid)

    def test_staff_direct_replace(self):
        card = self._card()
        new = replace_card_direct(card, self.officer, reason="damaged")
        card.refresh_from_db()
        self.assertEqual(card.status, CardStatus.REPLACED)
        self.assertEqual(new.status, CardStatus.GENERATED)
        self.assertEqual(new.replacement_reason, "damaged")

    def test_double_approve_rejected(self):
        card = self._card()
        req = create_replacement_request(self.student, card, "other", actor=self.student)
        approve_replacement_request(req, self.officer)
        with self.assertRaises(CardGenerationError):
            approve_replacement_request(req, self.officer)


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRY
# ─────────────────────────────────────────────────────────────────────────────

class ExpiryTests(IDCardTestCase):
    def test_expiry_processing(self):
        card = self._card()
        card.expiry_date = timezone.localdate() - timezone.timedelta(days=1)
        card.status = CardStatus.ACTIVE
        card.save(update_fields=["expiry_date", "status"])
        count = process_expirations()
        self.assertEqual(count, 1)
        card.refresh_from_db()
        self.assertEqual(card.status, CardStatus.EXPIRED)
        result = verify_token(card.verification_token)
        self.assertTrue(result.found)
        self.assertFalse(result.valid)

    def test_auto_renewal(self):
        settings_obj = IDCardSettings.get_for(self.inst_a)
        settings_obj.auto_renewal_enabled = True
        settings_obj.save()
        card = self._card()
        card.expiry_date = timezone.localdate() - timezone.timedelta(days=1)
        card.status = CardStatus.ACTIVE
        card.save(update_fields=["expiry_date", "status"])

        count = process_expirations()
        self.assertEqual(count, 1)
        card.refresh_from_db()
        self.assertEqual(card.status, CardStatus.REPLACED)
        self.assertIsNotNone(card.replaced_by)
        # Exactly one live card remains (the replacement).
        live = IDCard.objects.filter(
            student=self.student, status__in=CardStatus.LIVE_STATUSES,
        ).count()
        self.assertEqual(live, 1)


# ─────────────────────────────────────────────────────────────────────────────
# VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class VerificationTests(IDCardTestCase):
    def test_unknown_token(self):
        result = verify_token("does-not-exist")
        self.assertFalse(result.found)
        self.assertFalse(result.valid)

    def test_empty_token(self):
        result = verify_token("")
        self.assertFalse(result.found)

    def test_valid_after_issue(self):
        card = self._card()
        issue_card(card, self.officer)
        result = verify_token(card.verification_token)
        self.assertTrue(result.valid)
        self.assertEqual(result.reason, "VALID")
        self.assertEqual(
            result.payload["student_number"],
            self.student.academic_profile.student_number,
        )
        self.assertEqual(result.payload["card_number"], card.card_number)

    def test_expired_card_invalid(self):
        card = self._card()
        issue_card(card, self.officer)
        card.expiry_date = timezone.localdate() - timezone.timedelta(days=1)
        card.save(update_fields=["expiry_date"])
        result = verify_token(card.verification_token)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "EXPIRED")

    def test_public_payload_never_leaks_private_fields(self):
        card = self._card()
        issue_card(card, self.officer)
        payload = verify_token(card.verification_token).payload
        self.assertNotIn("email", payload)
        self.assertNotIn("level", payload)


# ─────────────────────────────────────────────────────────────────────────────
# RBAC SCOPING
# ─────────────────────────────────────────────────────────────────────────────

class ScopingTests(IDCardTestCase):
    def test_admin_and_officer_unscoped(self):
        self.assertEqual(scoped_students(self.admin).count(), 2)
        self.assertEqual(scoped_students(self.officer).count(), 2)
        self.assertTrue(services.is_id_card_admin(self.officer))
        self.assertFalse(services.is_id_card_admin(self.hod))

    def test_hod_scoped_to_own_department(self):
        ids = set(scoped_students(self.hod).values_list("pk", flat=True))
        self.assertIn(self.student.pk, ids)
        self.assertNotIn(self.student_b.pk, ids)
        ids_b = set(scoped_students(self.hod_b).values_list("pk", flat=True))
        self.assertNotIn(self.student.pk, ids_b)
        self.assertIn(self.student_b.pk, ids_b)

    def test_non_staff_have_no_scope(self):
        self.assertFalse(services.is_id_card_staff(self.student))
        self.assertEqual(scoped_students(self.student).count(), 0)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class ViewTests(IDCardTestCase):
    def test_public_verify_page(self):
        card = self._card()
        issue_card(card, self.officer)
        url = reverse("id_cards:verify_card", args=[card.verification_token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Card Verified")
        self.assertContains(response, card.card_number)

    def test_staff_views_require_staff_login(self):
        url = reverse("id_cards:photo_review")
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)

        self.client.force_login(self.hod)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_student_my_card_page(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("id_cards:my_card"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Passport Photograph")

    def test_approved_photo_hides_upload_form(self):
        self._card()  # uploads + approves a photo
        self.client.force_login(self.student)
        response = self.client.get(reverse("id_cards:my_card"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="action" value="upload_photo"')
        self.assertContains(response, "approved and locked")

    def test_approved_photo_rejects_reupload(self):
        self._card()  # uploads + approves a photo
        self.client.force_login(self.student)
        url = reverse("id_cards:my_card")
        before = PassportPhoto.objects.get(student=self.student).image.read()
        response = self.client.post(url, {
            "action": "upload_photo",
            "photo": make_photo_file(color="#123456"),
        })
        self.assertEqual(response.status_code, 200)
        photo = PassportPhoto.objects.get(student=self.student)
        self.assertEqual(photo.status, PhotoStatus.APPROVED)
        self.assertEqual(photo.image.read(), before)
        self.assertContains(response, "cannot be replaced here")

    def test_digital_combined_download(self):
        card = self._card()
        self.client.force_login(self.student)
        response = self.client.get(
            reverse("id_cards:digital_card", args=["combined"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn(f"{card.card_number}-front-back",
                      response["Content-Disposition"])

    def test_combined_pdf_download_defaults_combined(self):
        card = self._card()
        self.client.force_login(self.officer)
        response = self.client.get(
            reverse("id_cards:print_pdf") + "?cards=%d" % card.pk
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))

    def test_combined_pdf_download_explicit_modes(self):
        self.client.force_login(self.officer)
        card = self._card()
        for mode in ("combined", "front", "back", "duplex"):
            response = self.client.get(
                reverse("id_cards:print_pdf")
                + "?cards=%d&mode=%s" % (card.pk, mode)
            )
            self.assertEqual(response.status_code, 200, mode)
            self.assertEqual(response["Content-Type"], "application/pdf")

    def test_print_preview_combined_shows_pair(self):
        card = self._card()
        self.client.force_login(self.officer)
        response = self.client.get(
            reverse("id_cards:print_preview")
            + f"?cards={card.pk}&mode=combined"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "combined")
        self.assertContains(response, "front + back")

    def test_hod_cannot_manage_other_dept_student(self):
        card = self._card()
        self.client.force_login(self.hod_b)
        response = self.client.post(
            reverse("id_cards:card_action", args=[card.pk, "issue"])
        )
        self.assertEqual(response.status_code, 404)
        card.refresh_from_db()
        self.assertEqual(card.status, CardStatus.GENERATED)

    def test_admin_templates_page(self):
        self.client.force_login(self.hod)
        response = self.client.get(reverse("id_cards:template_list"))
        self.assertEqual(response.status_code, 302)  # HOD denied → redirect
        self.client.force_login(self.officer)
        response = self.client.get(reverse("id_cards:template_list"))
        self.assertEqual(response.status_code, 200)

    def test_template_status_switch(self):
        self.client.force_login(self.admin)
        url = reverse("id_cards:template_set_status", kwargs={"pk": self.template.pk})

        # fixture template starts ACTIVE + default → move to draft
        self.assertEqual(self.template.status, TemplateStatus.ACTIVE)
        self.assertTrue(self.template.is_default)
        response = self.client.post(url, {"status": "draft"})
        self.assertRedirects(response, reverse("id_cards:template_list"))
        self.template.refresh_from_db()
        self.assertEqual(self.template.status, TemplateStatus.DRAFT)
        self.assertTrue(IDCardAuditLog.objects.filter(
            action=AuditAction.TEMPLATE_CHANGED, template=self.template,
        ).exists())

        # draft → active (separate template) logs ACTIVATED
        second = IDCardTemplate.objects.create(
            institution=self.inst_a, name="Second", status=TemplateStatus.DRAFT,
            created_by=self.admin,
        )
        url2 = reverse("id_cards:template_set_status", kwargs={"pk": second.pk})
        response = self.client.post(url2, {"status": "active"})
        second.refresh_from_db()
        self.assertEqual(second.status, TemplateStatus.ACTIVE)
        self.assertTrue(IDCardAuditLog.objects.filter(
            action=AuditAction.TEMPLATE_ACTIVATED, template=second,
        ).exists())

        # draft → archived clears default and stamps archived_at
        response = self.client.post(url, {"status": "archived"})
        self.assertRedirects(response, reverse("id_cards:template_list"))
        self.template.refresh_from_db()
        self.assertEqual(self.template.status, TemplateStatus.ARCHIVED)
        self.assertFalse(self.template.is_default)
        self.assertIsNotNone(self.template.archived_at)
        self.assertTrue(IDCardAuditLog.objects.filter(
            action=AuditAction.TEMPLATE_ARCHIVED, template=self.template,
        ).exists())

        # no-op keeps state, invalid value errors
        response = self.client.post(url, {"status": "archived"})
        self.assertRedirects(response, reverse("id_cards:template_list"))
        self.template.refresh_from_db()
        self.assertEqual(self.template.status, TemplateStatus.ARCHIVED)
        response = self.client.post(url, {"status": "bogus"})
        self.assertRedirects(response, reverse("id_cards:template_list"))
        self.template.refresh_from_db()
        self.assertEqual(self.template.status, TemplateStatus.ARCHIVED)

        # archived → draft leaves archived_at cleared
        response = self.client.post(url, {"status": "draft"})
        self.template.refresh_from_db()
        self.assertEqual(self.template.status, TemplateStatus.DRAFT)
        self.assertIsNone(self.template.archived_at)


# ─────────────────────────────────────────────────────────────────────────────
# RENDERING
# ─────────────────────────────────────────────────────────────────────────────

class RenderTests(IDCardTestCase):
    """Card image + PDF rendering must work for a generated card."""

    def test_front_and_back_render_to_jpeg(self):
        card = self._card()
        for side in ("front", "back"):
            payload = renderers.digital_card_bytes(card, side)
            self.assertIsInstance(payload, bytes)
            self.assertGreater(len(payload), 1000)
            self.assertTrue(payload.startswith(b"\xff\xd8"))  # JPEG SOI

    def test_pdf_generates_duplex(self):
        card = self._card()
        stream = renderers.pdf_for_cards([card], mode="duplex", back_order="mirrored")
        self.assertTrue(stream.getvalue().startswith(b"%PDF"))

    def test_pdf_generates_back_only(self):
        card = self._card()
        stream = renderers.pdf_for_cards([card], mode="back")
        self.assertTrue(stream.getvalue().startswith(b"%PDF"))

    def test_pdf_generates_combined(self):
        card = self._card()
        stream = renderers.pdf_for_cards([card], mode="combined")
        self.assertTrue(stream.getvalue().startswith(b"%PDF"))
        self.assertGreater(len(stream.getvalue()), 2000)

    def test_combined_image_renders(self):
        card = self._card()
        payload = renderers.combined_card_bytes(card)
        self.assertIsInstance(payload, bytes)
        self.assertGreater(len(payload), 1000)
        self.assertTrue(payload.startswith(b"\xff\xd8"))  # JPEG SOI