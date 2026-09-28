"""
End-to-end scenario tests for the Applications & Admissions engine.

These exercise the real workflow graph, not the individual functions in
isolation: an application is driven from draft to a converted student, and each
step asserts both that the state is correct and that the guards which protect
it actually refuse the invalid path.

Run with the no-migrations settings, which build the schema from the models::

    python manage.py test portal --settings=eduPro.settings.test_nomigrations
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError


from academics.models import AcademicSession, Program, StudentProfile
from portal.models import (
    AdmissionApplication,
    AdmissionDecision,
    AdmissionOffer,
    AdmissionStatus,
    ApplicationAuditLog,
    ApplicationPayment,
    ApplicationPaymentStatus,
    ApplicationType,
    RequirementKind,
)
from portal.services import (
    ChecklistService,
    DecisionService,
    DocumentService,
    OfferService,
    PaymentService,
    StudentConversionService,
)
from portal.workflow import ApplicationWorkflow

from .factories import (
    AdmissionsSchemaTestCase,
    make_admin,
    make_admissions_officer,
    make_applicant,
    make_application,
    make_cycle,
    make_institution,
    make_programme,
    make_session,
    seed_application_types,
    upload_file,
)


class WorkflowTestCase(AdmissionsSchemaTestCase):
    """Common fixture: one open cycle, one programme, staff, and types seeded."""

    @classmethod
    def setUpTestData(cls):
        seed_application_types()
        cls.institution = make_institution()
        cls.session = make_session()
        cls.program = make_programme(
            institution=cls.institution, session=cls.session, code="ENG"
        )
        cls.postgrad = make_programme(
            institution=cls.institution, session=cls.session, code="MCS",
            program_type="postgraduate",
        )
        cls.cycle = make_cycle(session=cls.session, fee="500.00")
        cls.officer = make_admissions_officer("officer@example.com")
        cls.admin = make_admin("admin@example.com")
        cls.applicant = make_applicant("applicant@example.com", password="s3cret-pass")

    def blocking_codes(self, app):
        """``ChecklistService.blocking_items`` yields dicts, not objects."""
        return [item["code"] for item in ChecklistService.blocking_items(app)]

    def new_application(self, **kwargs):
        kwargs.setdefault("cycle", self.cycle)
        kwargs.setdefault("program", self.program)
        kwargs.setdefault("user", self.applicant)
        return make_application(**kwargs)

    def pay(self, app, provider="paystack", transaction_id="TXN-1"):
        """Initiate and confirm a payment, returning the confirmed payment."""
        payment, error = PaymentService.initiate(app, provider=provider)
        self.assertFalse(error, f"payment could not be started: {error}")
        PaymentService.confirm(payment, self.officer, transaction_id=transaction_id)
        return payment

    def drive_to_decision(self, app=None, user=None, decision=AdmissionDecision.ACCEPTED):
        """
        Take an application all the way to an acceptable state for a decision.

        Submits, pays, satisfies the checklist, reviews, shortlists and
        interviews, asserting at each step that the workflow accepted it.
        """
        app = app or self.new_application(**({"user": user} if user else {}))
        actor = self.officer

        ApplicationWorkflow.submit(app, actor)
        self.assertEqual(app.status, AdmissionStatus.SUBMITTED)

        self.pay(app)
        app.refresh_from_db()
        self.assertEqual(app.status, AdmissionStatus.PAYMENT_CONFIRMED)

        for item in ChecklistService.build(app)["items"]:
            if item["kind"] == RequirementKind.DOCUMENT and item["required"]:
                document = DocumentService.upload(
                    app, item["code"], upload_file(f"{item['code']}.pdf"), self.officer
                )
                DocumentService.verify(document, actor)
        app.refresh_from_db()
        self.assertEqual(
            self.blocking_codes(app), [],
            f"checklist still blocked by {self.blocking_codes(app)}",
        )

        for status in (
            AdmissionStatus.UNDER_REVIEW,
            AdmissionStatus.SHORTLISTED,
            AdmissionStatus.INTERVIEW_REQUIRED,
            AdmissionStatus.INTERVIEW_COMPLETED,
            AdmissionStatus.DECISION_PENDING,
        ):
            ApplicationWorkflow.transition(app, status, actor)
            app.refresh_from_db()
            self.assertEqual(app.status, status)

        DecisionService.record(app, decision, actor, notes="scenario test")
        app.refresh_from_db()
        return app


class WorkflowTransitionTests(WorkflowTestCase):
    """The transition graph and its guards."""

    def test_new_application_starts_as_draft_and_is_editable(self):
        app = self.new_application()
        self.assertEqual(app.status, AdmissionStatus.DRAFT)
        self.assertTrue(ApplicationWorkflow.is_applicant_editable(app))

    def test_applicant_submits_and_submitted_at_is_stamped(self):
        app = self.new_application()
        ApplicationWorkflow.submit(app, self.officer)
        self.assertEqual(app.status, AdmissionStatus.SUBMITTED)
        self.assertIsNotNone(app.submitted_at)
        self.assertFalse(ApplicationWorkflow.is_applicant_editable(app))

    def test_cannot_skip_from_draft_to_accepted_decision(self):
        app = self.new_application()
        with self.assertRaises(Exception):
            ApplicationWorkflow.transition(
                app, AdmissionStatus.DECISION_PENDING, self.officer
            )
        app.refresh_from_db()
        self.assertEqual(app.status, AdmissionStatus.DRAFT)

    def test_terminal_states_reject_further_transitions(self):
        app = self.new_application()
        ApplicationWorkflow.submit(app, self.officer)
        ApplicationWorkflow.withdraw(app, self.officer)
        app.refresh_from_db()
        self.assertTrue(ApplicationWorkflow.is_terminal(app.status))
        self.assertEqual(ApplicationWorkflow.staff_targets(app), [])

    def test_legacy_status_values_are_normalised(self):
        for legacy, expected in (
            (AdmissionStatus.PENDING, AdmissionStatus.SUBMITTED),
            (AdmissionStatus.REVIEW, AdmissionStatus.UNDER_REVIEW),
            (AdmissionStatus.REVIEWING, AdmissionStatus.UNDER_REVIEW),
        ):
            with self.subTest(legacy=legacy):
                self.assertEqual(ApplicationWorkflow.normalize(legacy), expected)

    def test_legacy_normalisation_is_idempotent_for_modern_values(self):
        for status in (AdmissionStatus.DRAFT, AdmissionStatus.SUBMITTED,
                       AdmissionStatus.PAYMENT_CONFIRMED, AdmissionStatus.CONVERTED):
            with self.subTest(status=status):
                self.assertEqual(ApplicationWorkflow.normalize(status), status)

    def test_request_correction_moves_to_needs_correction_with_a_reason(self):
        app = self.new_application()
        ApplicationWorkflow.submit(app, self.officer)
        ApplicationWorkflow.request_correction(app, self.officer, "Blurry scan")
        app.refresh_from_db()
        self.assertEqual(app.status, AdmissionStatus.NEEDS_CORRECTION)
        self.assertIn("Blurry", app.last_correction_note)
        self.assertIsNotNone(app.correction_requested_at)
        self.assertTrue(ApplicationWorkflow.is_applicant_editable(app))

    def test_every_transition_writes_an_audit_record(self):
        app = self.new_application()
        ApplicationWorkflow.submit(app, self.officer)
        logs = ApplicationAuditLog.objects.filter(
            application=app, to_status=AdmissionStatus.SUBMITTED,
        )
        self.assertTrue(logs.exists(), "submitting must be auditable")


class PaymentGateTests(WorkflowTestCase):
    """A fee must actually be *paid*, not merely started."""

    def setUp(self):
        self.app = self.new_application()
        ApplicationWorkflow.submit(self.app, self.officer)

    def test_pending_payment_does_not_advance_the_application(self):
        payment, error = PaymentService.initiate(self.app, provider="paystack")
        self.assertEqual(error, "")
        self.app.refresh_from_db()
        self.assertEqual(payment.status, ApplicationPaymentStatus.PENDING)
        self.assertFalse(self.app.is_fee_paid)
        self.assertNotEqual(
            self.app.status, AdmissionStatus.PAYMENT_CONFIRMED
        )

    def test_confirmed_payment_advances_to_payment_confirmed(self):
        self.pay(self.app, transaction_id="TXN-99")
        self.app.refresh_from_db()
        self.assertTrue(self.app.is_fee_paid)
        self.assertEqual(self.app.status, AdmissionStatus.PAYMENT_CONFIRMED)

    def test_failed_payment_does_not_count_as_paid(self):
        payment, _error = PaymentService.initiate(self.app, provider="paystack")
        PaymentService.fail(payment, reason="card declined")
        self.app.refresh_from_db()
        self.assertFalse(self.app.is_fee_paid)

    def test_paid_amount_ignores_everything_except_successful_payments(self):
        _payment, _error = PaymentService.initiate(self.app, provider="paystack")
        self.app.refresh_from_db()
        self.assertEqual(self.app.paid_amount, Decimal("0.00"))

        for status, amount in (
            (ApplicationPaymentStatus.PENDING, Decimal("500.00")),
            (ApplicationPaymentStatus.FAILED, Decimal("500.00")),
            (ApplicationPaymentStatus.CANCELLED, Decimal("500.00")),
            (ApplicationPaymentStatus.SUCCESSFUL, Decimal("500.00")),
        ):
            self.app.payments.create(
                amount=amount, status=status, provider="paystack",
                transaction_id=f"TXN-{status}",
                payment_reference=f"REF-{status}",
            )
        self.app.refresh_from_db()
        self.assertEqual(self.app.paid_amount, Decimal("500.00"))

    def test_initiating_twice_reuses_the_pending_payment(self):
        first, _e1 = PaymentService.initiate(self.app, provider="paystack")
        second, _e2 = PaymentService.initiate(self.app, provider="paystack")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ApplicationPayment.objects.filter(application=self.app).count(), 1
        )

    def test_cycle_without_a_fee_reports_no_payment_needed(self):
        free_cycle = make_cycle(session=self.session, fee="0.00")
        app = self.new_application(
            cycle=free_cycle, user=make_applicant("free@example.com"),
        )
        app.refresh_from_db()
        self.assertFalse(app.cycle.requires_payment)
        payment, error = PaymentService.initiate(app)
        self.assertIsNone(payment)
        self.assertTrue(error)

    def test_transition_past_payment_is_refused_without_payment(self):
        with self.assertRaises(Exception):
            ApplicationWorkflow.transition(
                self.app, AdmissionStatus.UNDER_REVIEW, self.officer
            )
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, AdmissionStatus.SUBMITTED)


class ChecklistTests(WorkflowTestCase):
    def test_requirements_come_from_the_configured_application_type(self):
        app = self.new_application()
        self.assertTrue(ChecklistService.requirements_for(app))

    def test_blocking_items_clear_as_documents_are_verified(self):
        app = self.new_application()
        self.assertTrue(self.blocking_codes(app))

        for code, _label in ChecklistService.DEFAULT_DOCUMENTS["undergraduate"]:
            document = DocumentService.upload(
                app, code, upload_file(f"{code}.pdf"), self.officer
            )
            DocumentService.verify(document, self.officer)
        app.refresh_from_db()
        self.assertEqual(self.blocking_codes(app), [])

    def test_upload_satisfies_a_document_requirement(self):
        """Submission needs the upload; staff verification is reported as a note."""
        app = self.new_application()
        self.assertIn("wa_ssce_slip", self.blocking_codes(app))

        document = DocumentService.upload(
            app, "wa_ssce_slip", upload_file("t.pdf"), self.officer
        )
        app.refresh_from_db()
        self.assertNotIn("wa_ssce_slip", self.blocking_codes(app))
        self.assertFalse(document.is_verified)

    def test_unverified_upload_is_reported_as_awaiting_verification(self):
        app = self.new_application()
        DocumentService.upload(
            app, "wa_ssce_slip", upload_file("t.pdf"), self.officer
        )
        app.refresh_from_db()
        notes = {
            item["code"]: item["note"] for item in ChecklistService.build(app)["items"]
        }
        self.assertIn("Awaiting verification", notes["wa_ssce_slip"])

    def test_rejected_document_blocks_again(self):
        app = self.new_application()
        document = DocumentService.upload(
            app, "wa_ssce_slip", upload_file("t.pdf"), self.officer
        )
        DocumentService.verify(document, self.officer)
        app.refresh_from_db()
        self.assertNotIn("wa_ssce_slip", self.blocking_codes(app))

        DocumentService.request_replacement(document, self.officer, "Illegible")
        app.refresh_from_db()
        self.assertIn(
            "wa_ssce_slip", self.blocking_codes(app),
            "a rejected document must not report the requirement as satisfied",
        )


class OfferAndConversionTests(WorkflowTestCase):
    def test_offer_requires_an_accepted_decision(self):
        app = self.new_application()
        ApplicationWorkflow.submit(app, self.officer)
        with self.assertRaises(Exception):
            OfferService.issue(app, self.officer)
        self.assertEqual(AdmissionOffer.objects.filter(application=app).count(), 0)

    def test_full_offer_lifecycle_converts_to_a_student(self):
        applicant = make_applicant("lifecycle@example.com")
        app = self.drive_to_decision(user=applicant)

        self.assertEqual(app.decision, AdmissionDecision.ACCEPTED)
        offer = OfferService.issue(app, self.officer, conditions="Jamboree")
        self.assertEqual(offer.status, "issued")
        self.assertEqual(offer.admission_type, "full_time")
        self.assertIsNotNone(offer.offer_number)

        OfferService.accept(offer, applicant)
        offer.refresh_from_db()
        self.assertEqual(offer.status, "accepted")
        self.assertIsNotNone(offer.responded_at)

        self.assertTrue(StudentConversionService.is_ready(app))

        # The applicant must not have a student record until conversion.
        self.assertFalse(
            StudentProfile.all_objects.filter(student=applicant).exists()
        )

        profile, _created = StudentConversionService.convert(app, self.officer)

        self.assertIsNotNone(profile)
        self.assertEqual(profile.student, applicant)
        self.assertEqual(profile.program, self.program)
        self.assertTrue(profile.is_active)
        self.assertTrue(profile.student_number, "a converted student needs a number")
        # The session the student was admitted into is derived, not stored.
        self.assertEqual(profile.admission_session, self.session)

        app.refresh_from_db()
        self.assertEqual(app.status, AdmissionStatus.CONVERTED)
        self.assertIsNotNone(app.converted_at)
        self.assertEqual(app.converted_by, self.officer)

    def test_conversion_is_idempotent(self):
        applicant = make_applicant("twice@example.com")
        app = self.drive_to_decision(user=applicant)
        offer = OfferService.issue(app, self.officer)
        OfferService.accept(offer, applicant)

        first, _c1 = StudentConversionService.convert(app, self.officer)
        second, created_again = StudentConversionService.convert(app, self.officer)
        self.assertFalse(created_again, "a repeat conversion must not create a student")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            StudentProfile.all_objects.filter(student=applicant).count(), 1
        )

    def test_conversion_refuses_without_an_accepted_offer(self):
        applicant = make_applicant("nooffer@example.com")
        app = self.drive_to_decision(user=applicant)
        offer = OfferService.issue(app, self.officer)

        self.assertFalse(StudentConversionService.is_ready(app))
        problems = StudentConversionService.preflight(app)
        self.assertTrue(problems)

        with self.assertRaises(Exception):
            StudentConversionService.convert(app, self.officer)
        self.assertEqual(
            StudentProfile.all_objects.filter(student=applicant).count(), 0
        )

        OfferService.decline(offer, applicant)
        self.assertFalse(StudentConversionService.is_ready(app))

    def test_rejected_decision_cannot_be_offered_or_converted(self):
        app = self.drive_to_decision(decision=AdmissionDecision.REJECTED)
        self.assertEqual(app.decision, AdmissionDecision.REJECTED)
        with self.assertRaises(Exception):
            OfferService.issue(app, self.officer)
        self.assertFalse(StudentConversionService.is_ready(app))

    def test_a_second_offer_is_refused_while_one_awaits_a_response(self):
        app = self.drive_to_decision()
        first = OfferService.issue(app, self.officer)
        with self.assertRaises(ValidationError):
            OfferService.issue(app, self.officer)
        self.assertEqual(
            AdmissionOffer.objects.filter(application=app).count(), 1
        )

    def test_revoking_an_accepted_offer_is_refused(self):
        applicant = make_applicant("accepted-revoke@example.com")
        app = self.drive_to_decision(user=applicant)
        offer = OfferService.issue(app, self.officer)
        OfferService.accept(offer, applicant)
        with self.assertRaises(ValidationError):
            OfferService.revoke(offer, self.officer, reason="Grades withdrawn")
        offer.refresh_from_db()
        self.assertEqual(offer.status, "accepted")

    def test_revoking_an_issued_offer_blocks_conversion(self):
        applicant = make_applicant("revoked@example.com")
        app = self.drive_to_decision(user=applicant)
        offer = OfferService.issue(app, self.officer)
        OfferService.revoke(offer, self.officer, reason="Programme closed")
        offer.refresh_from_db()
        self.assertEqual(offer.status, "revoked")
        self.assertFalse(StudentConversionService.is_ready(app))


class DuplicateProtectionTests(WorkflowTestCase):
    def test_one_application_per_applicant_is_enforced_by_the_schema(self):
        """``user`` is a OneToOneField, so duplicates cannot be stored at all."""
        user = make_applicant("dupe@example.com")
        make_application(cycle=self.cycle, program=self.program, user=user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_application(cycle=self.cycle, program=self.program, user=user)
        self.assertEqual(
            AdmissionApplication.objects.filter(user=user).count(), 1
        )

    def test_withdrawn_application_is_not_deleted(self):
        user = make_applicant("reapply@example.com")
        app = make_application(cycle=self.cycle, program=self.program, user=user)
        ApplicationWorkflow.submit(app, self.officer)
        ApplicationWorkflow.withdraw(app, self.officer)
        app.refresh_from_db()
        self.assertEqual(app.status, AdmissionStatus.WITHDRAWN)
        self.assertEqual(
            AdmissionApplication.objects.filter(user=user).count(), 1
        )

    def test_postgraduate_uses_its_own_configured_requirements(self):
        postgrad_app = self.new_application(
            user=make_applicant("pg@example.com"),
            program=self.postgrad, type_code="postgraduate",
        )
        undergrad_app = self.new_application(
            user=make_applicant("ug@example.com"),
        )
        self.assertEqual(postgrad_app.application_type_ref.code, "postgraduate")
        self.assertNotEqual(
            list(ChecklistService.requirements_for(postgrad_app)),
            list(ChecklistService.requirements_for(undergrad_app)),
        )


class AccessControlTests(WorkflowTestCase):
    def test_anonymous_cannot_reach_the_dashboard(self):
        from django.test import Client

        self.assertEqual(Client().get("/portal/admissions/").status_code, 302)

    def test_officer_can_see_the_dashboard(self):
        from django.test import Client

        self.assertTrue(
            self.client.login(username=self.officer.email, password="s3cret-pass")
        )
        response = self.client.get("/portal/admissions/")
        self.assertEqual(response.status_code, 200)

    def test_applicant_cannot_see_the_staff_dashboard(self):
        from django.test import Client

        client = Client()
        self.assertTrue(
            client.login(username=self.applicant.email, password="s3cret-pass")
        )
        self.assertEqual(client.get("/portal/admissions/").status_code, 302)

    def test_officer_cannot_see_the_finance_payment_list(self):
        from django.test import Client

        client = Client()
        self.assertTrue(
            client.login(username=self.officer.email, password="s3cret-pass")
        )
        response = client.get("/portal/admissions/payments/")
        self.assertEqual(response.status_code, 302)

    def test_admin_can_see_the_finance_payment_list(self):
        from django.test import Client

        client = Client()
        self.assertTrue(
            client.login(username=self.admin.email, password="s3cret-pass")
        )
        self.assertEqual(client.get("/portal/admissions/payments/").status_code, 200)

    def test_applicant_cannot_see_someone_elses_application(self):
        from django.test import Client

        owner = make_applicant("owner@example.com", password="s3cret-pass")
        other = make_applicant("other@example.com", password="s3cret-pass")
        app = make_application(cycle=self.cycle, program=self.program, user=owner)

        client = Client()
        self.assertTrue(client.login(username=other.email, password="s3cret-pass"))
        self.assertEqual(
            client.get(f"/portal/my-applications/{app.pk}/").status_code, 404
        )


class UrlResolutionTests(WorkflowTestCase):
    def test_collection_routes_reverse_without_arguments(self):
        from django.urls import reverse

        for name in (
            "portal:admissions_dashboard",
            "portal:application_payment_list",
            "portal:applicant_dashboard",
        ):
            with self.subTest(route=name):
                self.assertTrue(reverse(name).startswith("/portal/"))

    def test_member_routes_reverse_with_the_application_pk(self):
        from django.urls import NoReverseMatch, reverse

        named = [
            "portal:application_detail",
            "portal:application_transition",
            "portal:application_decision",
            "portal:application_offer_issue",
            "portal:application_convert",
            "portal:application_correction_request",
        ]
        app = self.new_application()
        for name in named:
            with self.subTest(route=name):
                try:
                    url = reverse(name, args=[app.pk])
                except NoReverseMatch as exc:
                    self.fail(f"{name} did not reverse: {exc}")
                self.assertTrue(url.startswith("/portal/"))
