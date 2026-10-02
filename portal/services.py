"""
portal/services.py

Business services for the unified Applications & Admissions engine.

Everything that *changes* admissions state lives here or in
:mod:`portal.workflow`, never in a view. Views are deliberately thin: they
authorise, delegate, and report. That is what makes the workflow auditable and
what stops an unrelated view from quietly moving an application to "Accepted".

Service map
-----------
``AuditService``            append-only trail for every sensitive action (req 19)
``ChecklistService``        configurable requirements + completeness (req 11/13)
``PaymentService``          application fee, verified-only (req 15)
``DocumentService``         upload / verify / reject / replace (req 12)
``DecisionService``         admission decision, separate from workflow (req 6)
``OfferService``            issue / accept / decline / expire offers (req 8)
``StudentConversionService``  applicant -> student, transactional (req 7/9)
``ReportingService``        dashboard statistics (req 20)
``CycleService``            cycle opening/closing + bootstrap data
"""

from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import (
    AdmissionDecision,
    AdmissionOffer,
    AdmissionStatus,
    ApplicationAuditLog,
    ApplicationDocument,
    ApplicationDocumentType,
    ApplicationPayment,
    ApplicationPaymentStatus,
    ApplicationRequirement,
    ApplicationType,
    ApplicationTypeCode,
    AuditAction,
    DocumentVerificationStatus,
    OfferStatus,
    RequirementKind,
)

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT
# ─────────────────────────────────────────────────────────────────────────────

class AuditService:
    """Append-only audit trail. Never updated, never deleted by callers."""

    @classmethod
    def record(cls, application, action, actor=None, remark="", *,
               from_status="", to_status="", visible_to_applicant=False):
        return ApplicationAuditLog.objects.create(
            application=application,
            action=action,
            actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
            from_status=from_status or "",
            to_status=to_status or "",
            remark=remark or "",
            is_visible_to_applicant=visible_to_applicant,
        )

    @classmethod
    def staff_timeline(cls, application):
        """Full internal history — staff only."""
        return application.audit_logs.select_related("actor").all()

    @classmethod
    def applicant_timeline(cls, application):
        """
        Public history. Internal remarks are excluded at the query level, so
        there is no template that can leak them by accident (req 19/22).
        """
        return application.audit_logs.filter(
            is_visible_to_applicant=True
        ).select_related("actor")


# ─────────────────────────────────────────────────────────────────────────────
# CHECKLIST / COMPLETENESS
# ─────────────────────────────────────────────────────────────────────────────

class ChecklistService:
    """
    Builds the applicant checklist and completion percentage from
    :class:`~portal.models.ApplicationRequirement` rows, so undergraduate and
    postgraduate differ by configuration rather than by code (req 11/13/14).

    If an institution has not configured any requirements for a type, a
    sensible default set is derived from the application type code so the
    checklist is never blank on a fresh install.
    """

    #: Fallback requirements used when nothing is configured. Keys are
    #: ``document type code`` / ``field name``; values are (label, required).
    DEFAULT_DOCUMENTS = {
        ApplicationTypeCode.UNDERGRADUATE: [
            (ApplicationDocumentType.WA_SSCE_SLIP, "WASSCE / SSSCE Results Slip"),
            (ApplicationDocumentType.ID_DOCUMENT, "ID / Passport"),
            (ApplicationDocumentType.PASSPORT_PHOTO, "Passport Photograph"),
        ],
        ApplicationTypeCode.POSTGRADUATE: [
            (ApplicationDocumentType.DEGREE_CERTIFICATE, "Degree Certificate"),
            (ApplicationDocumentType.TRANSCRIPT, "Academic Transcript"),
            (ApplicationDocumentType.CV, "Curriculum Vitae"),
            (ApplicationDocumentType.REFERENCE, "Reference Letter"),
            (ApplicationDocumentType.STATEMENT_OF_PURPOSE, "Statement of Purpose"),
        ],
    }
    DEFAULT_DOCUMENTS.update({
        code: [(ApplicationDocumentType.TRANSCRIPT, "Academic Transcript"),
               (ApplicationDocumentType.ID_DOCUMENT, "ID / Passport")]
        for code in (
            ApplicationTypeCode.DIPLOMA, ApplicationTypeCode.CERTIFICATE,
            ApplicationTypeCode.MATURE, ApplicationTypeCode.TRANSFER,
            ApplicationTypeCode.INTERNATIONAL, ApplicationTypeCode.DISTANCE_LEARNING,
            ApplicationTypeCode.EXCHANGE,
        )
    })

    #: Information fields every application must carry.
    CORE_FIELDS = [
        ("first_name", "Personal Information"),
        ("last_name", "Personal Information"),
        ("email", "Contact Information"),
        ("phone", "Contact Information"),
    ]

    ACADEMIC_FIELDS = {
        ApplicationTypeCode.UNDERGRADUATE: [
            ("previous_school", "Secondary School"),
            ("qualification", "Qualification"),
            ("aggregate_score", "Aggregate Score"),
        ],
        ApplicationTypeCode.POSTGRADUATE: [
            ("previous_school", "Institution Attended"),
            ("qualification", "Previous Degree"),
            ("year_of_completion", "Year Completed"),
        ],
    }
    ACADEMIC_FIELDS.update({
        code: [("previous_school", "Institution Attended"),
               ("qualification", "Highest Qualification")]
        for code in (
            ApplicationTypeCode.DIPLOMA, ApplicationTypeCode.CERTIFICATE,
            ApplicationTypeCode.MATURE, ApplicationTypeCode.TRANSFER,
            ApplicationTypeCode.INTERNATIONAL, ApplicationTypeCode.DISTANCE_LEARNING,
        )
    })

    @classmethod
    def requirements_for(cls, application):
        """
        The requirement rows in force for this application. Returns
        :class:`ApplicationRequirement` objects when the institution has
        configured them, otherwise ``None`` to signal "use defaults".
        """
        return ApplicationRequirement.resolve_for(
            application.application_type, application.program_applied
        )

    @classmethod
    def build(cls, application):
        """
        Return the full checklist plus a completion percentage.

        Shape::

            {
              "items": [ {kind, code, label, required, done, note}, ... ],
              "percent": int,
              "missing": [label, ...],       # required + not done
              "satisfied": bool,             # nothing required is missing
              "configured": bool,            # were requirements configured?
            }
        """
        requirements = cls.requirements_for(application)
        configured = bool(requirements)
        items = []

        if configured:
            for req in requirements:
                if req.kind == RequirementKind.DOCUMENT:
                    doc = cls._current_document(
                        application, req.document_type
                    )
                    # An upload satisfies the *submission* checklist; staff
                    # verification is a separate concern and is reported as a
                    # note. A document staff have rejected (or asked to be
                    # replaced) is however not a satisfied requirement — it must
                    # not report "done" while asking for a replacement.
                    done = bool(doc) and doc.status not in (
                        DocumentVerificationStatus.REJECTED,
                        DocumentVerificationStatus.REQUIRES_REPLACEMENT,
                    )
                    note = ""
                    if doc is not None and doc.status == DocumentVerificationStatus.REJECTED:
                        note = _("Rejected - please upload a replacement.")
                    elif doc is not None and doc.status == DocumentVerificationStatus.REQUIRES_REPLACEMENT:
                        note = _("A replacement was requested.")
                    elif doc is not None and not doc.is_verified:
                        note = _("Awaiting verification.")
                    items.append({
                        "kind": RequirementKind.DOCUMENT,
                        "code": req.code,
                        "label": req.label,
                        "required": req.is_required,
                        "done": done,
                        "note": note,
                        "document": doc,
                    })
                else:
                    value = getattr(application, req.field_name, None)
                    done = bool(str(value).strip()) if value is not None else False
                    items.append({
                        "kind": RequirementKind.FIELD,
                        "code": req.field_name,
                        "label": req.label,
                        "required": req.is_required,
                        "done": done,
                        "note": "",
                        "document": None,
                    })
        else:
            items = cls._default_items(application)

        total = len(items)
        done = sum(1 for i in items if i["done"])
        missing = [i["label"] for i in items if i["required"] and not i["done"]]

        return {
            "items": items,
            "percent": int(round((done / total) * 100)) if total else 100,
            "missing": missing,
            "satisfied": not missing,
            "configured": configured,
        }

    @classmethod
    def _default_items(cls, application):
        items = []
        for field_name, label in cls.CORE_FIELDS:
            value = getattr(application, field_name, "")
            items.append({
                "kind": RequirementKind.FIELD,
                "code": field_name,
                "label": label,
                "required": True,
                "done": bool(str(value).strip()),
                "note": "",
                "document": None,
            })
        for field_name, label in cls.ACADEMIC_FIELDS.get(
            application.application_type, []
        ):
            value = getattr(application, field_name, "")
            items.append({
                "kind": RequirementKind.FIELD,
                "code": field_name,
                "label": label,
                "required": True,
                "done": bool(str(value).strip()),
                "note": "",
                "document": None,
            })
        for doc_code, label in cls.DEFAULT_DOCUMENTS.get(
            application.application_type, []
        ):
            doc = cls._current_document(application, doc_code)
            items.append({
                "kind": RequirementKind.DOCUMENT,
                "code": doc_code,
                "label": label,
                "required": True,
                "done": bool(doc),
                "note": _("Awaiting verification.") if doc and not doc.is_verified else "",
                "document": doc,
            })
        return items

    @staticmethod
    def _current_document(application, document_type):
        """
        The live copy of a document slot: a verified copy if one exists,
        otherwise the most recent upload. Never a superseded replacement.
        """
        qs = application.uploaded_documents.filter(document_type=document_type)
        verified = qs.filter(status=DocumentVerificationStatus.VERIFIED).first()
        if verified:
            return verified
        return qs.order_by("-version", "-uploaded_at").first()

    @classmethod
    def blocking_items(cls, application):
        """Required items that must be satisfied before submission."""
        data = cls.build(application)
        return [i for i in data["items"] if i["required"] and not i["done"]]

    @classmethod
    def can_submit(cls, application):
        return not cls.blocking_items(application)


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENTS
# ─────────────────────────────────────────────────────────────────────────────

class PaymentService:
    """
    Application-fee payments.

    Kept out of ``portal.workflow`` on purpose (req 15): a payment is a finance
    fact, and finance staff may verify one without being allowed to touch the
    academic decision. Successful status is only ever set by
    :meth:`confirm` / :meth:`verify_with_provider`.
    """

    @classmethod
    def pending_payment(cls, application):
        return application.payments.filter(
            status=ApplicationPaymentStatus.PENDING
        ).order_by("-created_at").first()

    @classmethod
    def initiate(cls, application, provider="", amount=None):
        """
        Create (or reuse) a PENDING payment. This deliberately does **not**
        mark anything as paid — opening a payment page is not payment.
        """
        fee = application.application_fee
        try:
            # A DecimalField can still hold whatever was assigned to it
            # (a str from a form or a cycle built in code), so coerce before
            # comparing rather than letting a str/int TypeError escape.
            fee = Decimal(fee) if fee else Decimal("0")
        except (InvalidOperation, TypeError, ValueError):
            fee = Decimal("0")
        if fee <= 0:
            return None, _("This admission cycle has no application fee.")
        existing = cls.pending_payment(application)
        if existing:
            return existing, ""
        try:
            amount = Decimal(amount) if amount is not None else fee
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError(_("Invalid payment amount."))
        payment = ApplicationPayment.objects.create(
            application=application,
            amount=amount,
            payment_reference=ApplicationPayment.generate_reference(application),
            provider=provider,
            status=ApplicationPaymentStatus.PENDING,
        )
        AuditService.record(
            application, AuditAction.PAYMENT_RECORDED, remark=str(amount),
        )
        return payment, ""

    @classmethod
    def confirm(cls, payment, actor=None, transaction_id="", note=""):
        """
        Manual confirmation by an authorised staff member (cash, bank transfer,
        mobile money). Idempotent: confirming twice is harmless.
        """
        if payment.status == ApplicationPaymentStatus.SUCCESSFUL:
            return payment
        with transaction.atomic():
            payment.mark_successful(actor=actor, transaction_id=transaction_id)
            application = payment.application
            AuditService.record(
                application, AuditAction.PAYMENT_CONFIRMED, actor=actor,
                remark=note or f"Confirmed {payment.amount} ({payment.payment_reference})",
                visible_to_applicant=True,
            )
            cls._advance_after_payment(application, actor)
        cls._notify_paid(payment)
        return payment

    @classmethod
    def verify_with_provider(cls, payment, actor=None):
        """
        Confirm using the project's EXISTING Paystack verification client
        (``hostel.services.paystack.PaystackService``) rather than trusting a
        browser redirect. Returns the payment, or raises on a failed check.
        """
        from hostel.services.paystack import PaystackError, PaystackService

        if payment.status == ApplicationPaymentStatus.SUCCESSFUL:
            return payment
        try:
            data = PaystackService.verify(payment.payment_reference)
        except (PaystackError, ImportError) as exc:
            payment.mark_failed(str(exc))
            raise ValidationError(
                _("Could not verify the payment with the provider: %(e)s") % {"e": exc}
            )
        if data.get("status") != "success":
            payment.mark_failed(_("Provider reported the transaction as not successful."))
            raise ValidationError(_("The provider has not confirmed this payment."))
        amount_minor = data.get("amount") or 0
        amount = PaystackService.minor_to_major(amount_minor)
        if Decimal(str(amount)) != Decimal(payment.amount):
            payment.mark_failed(
                _("Amount mismatch: provider confirmed %(a)s, expected %(e)s.")
                % {"a": amount, "e": payment.amount}
            )
            raise ValidationError(_("The confirmed amount does not match the fee due."))
        return cls.confirm(
            payment, actor=actor,
            transaction_id=str(data.get("reference") or payment.payment_reference),
            note="Verified with payment provider.",
        )

    @classmethod
    def fail(cls, payment, reason):
        payment.mark_failed(reason)
        return payment

    @classmethod
    def cancel(cls, payment, actor=None, reason=""):
        if payment.status == ApplicationPaymentStatus.SUCCESSFUL:
            raise ValidationError(_("A confirmed payment cannot be cancelled."))
        payment.status = ApplicationPaymentStatus.CANCELLED
        payment.failure_reason = reason
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        AuditService.record(
            payment.application, AuditAction.PAYMENT_RECORDED, actor=actor,
            remark=f"Payment cancelled: {reason}",
        )
        return payment

    @staticmethod
    def _advance_after_payment(application, actor):
        """
        Move a submitted application along once its fee is genuinely settled.
        This is the *only* automatic workflow movement triggered by finance.
        """
        from .workflow import ApplicationWorkflow
        current = ApplicationWorkflow.normalize(application.status)
        if current in (AdmissionStatus.PAYMENT_PENDING, AdmissionStatus.SUBMITTED):
            if not application.is_fee_paid:
                if current == AdmissionStatus.SUBMITTED:
                    ApplicationWorkflow.transition(
                        application, AdmissionStatus.PAYMENT_PENDING, actor=actor,
                        remark="Awaiting application fee.",
                    )
                return
            ApplicationWorkflow.transition(
                application, AdmissionStatus.PAYMENT_CONFIRMED, actor=actor,
                remark="Application fee confirmed.",
            )

    @staticmethod
    def _notify_paid(payment):
        try:
            from .notify import notify_payment_confirmed
            notify_payment_confirmed(payment)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "Payment notification failed for %s", payment.payment_reference
            )


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENTS
# ─────────────────────────────────────────────────────────────────────────────

class DocumentService:
    """Applicant uploads and staff verification (req 12/22)."""

    #: Server-side allow-list and size ceiling. Checked again on upload so a
    #: crafted request cannot bypass the form.
    ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
    MAX_BYTES = 5 * 1024 * 1024

    @classmethod
    def validate_upload(cls, uploaded):
        if not uploaded:
            raise ValidationError(_("No file was selected."))
        name = getattr(uploaded, "name", "") or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in cls.ALLOWED_EXTENSIONS:
            raise ValidationError(
                _("%(ext)s files are not accepted. Upload a PDF, JPG or PNG.")
                % {"ext": ext.upper() or "Unknown"}
            )
        size = getattr(uploaded, "size", 0) or 0
        if size > cls.MAX_BYTES:
            raise ValidationError(
                _("That file is too large. Maximum size is 5 MB.")
            )
        if size == 0:
            raise ValidationError(_("That file appears to be empty."))
        return True

    @classmethod
    def upload(cls, application, document_type, uploaded, uploaded_by=None):
        """
        Store a document against an application.

        A verified document is never overwritten in place: the old copy is
        superseded, the version counter advances and the new copy starts life
        as PENDING so it must be verified again.
        """
        cls.validate_upload(uploaded)

        with transaction.atomic():
            previous = application.uploaded_documents.filter(
                document_type=document_type
            ).order_by("-version").first()
            version = (previous.version + 1) if previous else 1
            if previous is not None and previous.is_verified:
                # Explicitly revoke the old sign-off.
                previous.require_replacement(
                    _("Replaced by a newer upload (version %(v)s).") % {"v": version}
                )
            document = ApplicationDocument.objects.create(
                application=application,
                document_type=document_type,
                file=uploaded,
                status=DocumentVerificationStatus.PENDING,
                version=version,
            )
            AuditService.record(
                application, AuditAction.DOCUMENT_UPLOADED, actor=uploaded_by,
                remark=document.get_document_type_display(),
            )
        cls._notify_document(application, document, "uploaded")
        return document

    @classmethod
    def verify(cls, document, actor):
        if document.status == DocumentVerificationStatus.VERIFIED:
            return document
        with transaction.atomic():
            document.mark_verified(actor)
            AuditService.record(
                document.application, AuditAction.DOCUMENT_VERIFIED, actor=actor,
                remark=document.get_document_type_display(),
            )
        cls._notify_document(document.application, document, "verified")
        return document

    @classmethod
    def reject(cls, document, actor, reason):
        if not reason or len(reason.strip()) < 5:
            raise ValidationError(_("Please give a reason for rejecting this document."))
        with transaction.atomic():
            document.mark_rejected(actor, reason)
            AuditService.record(
                document.application, AuditAction.DOCUMENT_REJECTED, actor=actor,
                remark=f"{document.get_document_type_display()}: {reason}",
            )
        cls._notify_document(document.application, document, "rejected", reason)
        return document

    @classmethod
    def request_replacement(cls, document, actor, reason):
        with transaction.atomic():
            document.require_replacement(reason)
            AuditService.record(
                document.application, AuditAction.DOCUMENT_REJECTED, actor=actor,
                remark=f"Replacement requested for {document.get_document_type_display()}: {reason}",
            )
        cls._notify_document(document.application, document, "replacement", reason)
        return document

    @staticmethod
    def _notify_document(application, document, event, reason=""):
        try:
            from .notify import notify_document_event
            notify_document_event(application, document, event, reason)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception("Document notification failed")


# ─────────────────────────────────────────────────────────────────────────────
# ADMISSION DECISION
# ─────────────────────────────────────────────────────────────────────────────

class DecisionService:
    """
    Records the academic outcome (req 6).

    The decision is deliberately independent of the workflow status: an
    application can be "Completed" (admission concluded) while carrying an
    "Accepted" decision, and a rejection sets the decision *and* the workflow
    status together so the two never disagree.
    """

    #: Decisions that permit an offer to be issued.
    OFFERABLE = {
        AdmissionDecision.ACCEPTED,
        AdmissionDecision.CONDITIONALLY_ACCEPTED,
    }

    @classmethod
    def record(cls, application, decision, actor, notes="", *, notify=True):
        from .workflow import ApplicationWorkflow

        decision = decision if hasattr(decision, "value") else str(decision)
        if decision not in AdmissionDecision.values:
            raise ValidationError(_("Unknown admission decision."))
        if not actor or not getattr(actor, "is_authenticated", False):
            raise ValidationError(_("A decision must be attributed to a staff member."))

        if application.is_terminal and decision != AdmissionDecision.REJECTED:
            raise ValidationError(
                _("This application is closed and cannot receive a new decision.")
            )

        with transaction.atomic():
            application.decision = decision
            application.decision_made_by = actor
            application.decision_at = timezone.now()
            application.decision_notes = notes or ""
            application.save(update_fields=[
                "decision", "decision_made_by", "decision_at",
                "decision_notes", "updated_at",
            ])
            AuditService.record(
                application, AuditAction.DECISION_RECORDED, actor=actor,
                remark=notes or decision.replace("_", " ").title(),
                visible_to_applicant=True,
            )
            # Keep workflow status consistent with the outcome.
            if decision == AdmissionDecision.REJECTED:
                if ApplicationWorkflow.normalize(application.status) not in (
                    ApplicationWorkflow.TERMINAL
                ):
                    ApplicationWorkflow.transition(
                        application, AdmissionStatus.REJECTED, actor=actor,
                        remark=notes or "Application rejected.",
                    )
            elif decision in cls.OFFERABLE:
                if not ApplicationWorkflow.is_terminal(application.status):
                    target = (
                        AdmissionStatus.COMPLETED
                        if ApplicationWorkflow.normalize(application.status)
                        == AdmissionStatus.DECISION_PENDING
                        else AdmissionStatus.DECISION_PENDING
                    )
                    ApplicationWorkflow.transition(
                        application, target, actor=actor,
                        remark="Admission decision: "
                               + decision.replace("_", " ").title(),
                    )

        if notify:
            try:
                from .notify import notify_decision
                notify_decision(application, decision, notes)
            except Exception:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).exception("Decision notification failed")
        return application

    @classmethod
    def can_make(cls, application, decision):
        if decision not in AdmissionDecision.values:
            return False, _("Unknown admission decision.")
        if decision in cls.OFFERABLE and not application.program_applied_id:
            return False, _("Select a programme before recording an admission decision.")
        if application.is_terminal and decision != AdmissionDecision.REJECTED:
            return False, _("This application is closed.")
        return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# ADMISSION OFFERS
# ─────────────────────────────────────────────────────────────────────────────

class OfferService:
    """Issue / accept / decline / expire admission offers (req 8)."""

    DEFAULT_VALIDITY_DAYS = 30

    @classmethod
    def active_offer(cls, application):
        return application.offers.exclude(
            status__in=[OfferStatus.DECLINED, OfferStatus.REVOKED, OfferStatus.EXPIRED]
        ).order_by("-issue_date").first()

    @classmethod
    def accepted_offer(cls, application):
        return application.offers.filter(status=OfferStatus.ACCEPTED).first()

    @classmethod
    def issue(cls, application, actor, expiry_date=None, conditions="",
              admission_type="full_time"):
        """
        Issue an offer. Requires a positive decision — this is what stops a
        rejected applicant from acquiring a student record.
        """
        if application.decision not in DecisionService.OFFERABLE:
            raise ValidationError(
                _("An offer can only be issued to an applicant who has been "
                  "accepted (current decision: %s).")
                % application.get_decision_display()
            )
        if not application.program_applied_id:
            raise ValidationError(_("Select a programme before issuing an offer."))

        session = (
            application.cycle.academic_session
            if application.cycle_id else None
        ) or _fallback_session()
        if session is None:
            raise ValidationError(
                _("This admission cycle is not linked to an academic session, so an "
                  "offer cannot be issued. Link the cycle to a session first.")
            )

        existing = cls.active_offer(application)
        if existing and existing.status == OfferStatus.ISSUED and not existing.is_expired:
            raise ValidationError(
                _("An offer (%(ref)s) is already awaiting a response.")
                % {"ref": existing.offer_number}
            )

        expiry_date = expiry_date or (
            timezone.localdate()
            + timezone.timedelta(days=cls.DEFAULT_VALIDITY_DAYS)
        )
        with transaction.atomic():
            offer = AdmissionOffer.objects.create(
                application=application,
                applicant=application.user,
                program=application.program_applied,
                academic_session=session,
                admission_type=admission_type,
                issue_date=timezone.localdate(),
                expiry_date=expiry_date,
                conditions=conditions or "",
                issued_by=actor,
                status=OfferStatus.ISSUED,
            )
            AuditService.record(
                application, AuditAction.OFFER_ISSUED, actor=actor,
                remark=f"Offer {offer.offer_number} issued"
                       + (f" with conditions: {conditions}" if conditions else ""),
                visible_to_applicant=True,
            )
        _notify_offer(application, offer, "issued")
        return offer

    @classmethod
    def accept(cls, offer, actor=None):
        if offer.status == OfferStatus.ACCEPTED:
            return offer
        if offer.status != OfferStatus.ISSUED:
            raise ValidationError(
                _("This offer is %(status)s and can no longer be accepted.")
                % {"status": offer.get_status_display()}
            )
        if offer.expiry_date < timezone.localdate():
            offer.mark_expired()
            AuditService.record(
                offer.application, AuditAction.OFFER_EXPIRED,
                remark="Offer expired before a response was received.",
                visible_to_applicant=True,
            )
            raise ValidationError(_("This offer has expired. Please contact admissions."))
        with transaction.atomic():
            offer.mark_accepted()
            AuditService.record(
                offer.application, AuditAction.OFFER_ACCEPTED, actor=actor,
                remark=f"Offer {offer.offer_number} accepted.",
                visible_to_applicant=True,
            )
        _notify_offer(offer.application, offer, "accepted")
        return offer

    @classmethod
    def decline(cls, offer, actor=None):
        if offer.status != OfferStatus.ISSUED:
            raise ValidationError(_("This offer can no longer be declined."))
        with transaction.atomic():
            offer.mark_declined()
            AuditService.record(
                offer.application, AuditAction.OFFER_DECLINED, actor=actor,
                remark=f"Offer {offer.offer_number} declined.",
                visible_to_applicant=True,
            )
        _notify_offer(offer.application, offer, "declined")
        return offer

    @classmethod
    def revoke(cls, offer, actor, reason=""):
        if offer.status == OfferStatus.ACCEPTED:
            raise ValidationError(
                _("An accepted offer cannot be revoked. Withdraw the student record "
                  "instead.")
            )
        with transaction.atomic():
            offer.mark_revoked()
            AuditService.record(
                offer.application, AuditAction.OFFER_ISSUED, actor=actor,
                remark=f"Offer {offer.offer_number} revoked: {reason}",
            )
        return offer

    @classmethod
    def expire_overdue(cls, as_of=None):
        """Sweep offers past their expiry date. Idempotent."""
        as_of = as_of or timezone.localdate()
        stale = AdmissionOffer.objects.filter(
            status=OfferStatus.ISSUED, expiry_date__lt=as_of,
        ).select_related("application")
        count = 0
        for offer in stale:
            with transaction.atomic():
                offer.mark_expired()
                AuditService.record(
                    offer.application, AuditAction.OFFER_EXPIRED,
                    remark=f"Offer {offer.offer_number} expired on {offer.expiry_date}.",
                    visible_to_applicant=True,
                )
            count += 1
        return count

    @classmethod
    def expiring_soon(cls, days=7, as_of=None):
        as_of = as_of or timezone.localdate()
        horizon = as_of + timezone.timedelta(days=days)
        return AdmissionOffer.objects.filter(
            status=OfferStatus.ISSUED,
            expiry_date__gte=as_of,
            expiry_date__lte=horizon,
        ).select_related("application", "applicant", "program")


def _fallback_session():
    from academics.models import AcademicSession
    return AcademicSession.get_current() or AcademicSession.objects.order_by(
        "-start_date"
    ).first()


def _notify_offer(application, offer, event):
    try:
        from .notify import notify_offer_event
        notify_offer_event(application, offer, event)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("Offer notification failed")


# ─────────────────────────────────────────────────────────────────────────────
# STUDENT CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

class StudentConversionService:
    """
    Turns an admitted applicant into a real student (req 7 & 9).

    The lifecycle enforced here is strictly:

        application -> accepted decision -> offer issued -> offer accepted
                    -> conversion -> student

    ``preflight`` returns a human-readable reason when the application is not
    ready; ``convert`` is the only function in the codebase permitted to create
    a :class:`~academics.models.StudentProfile` from an application, and it
    runs inside a single transaction so a partial conversion is impossible
    (req 9 / scenario 10).
    """

    @classmethod
    def preflight(cls, application):
        """
        Return ``None`` if conversion may proceed, otherwise a message
        explaining what is missing. Pure read-only — safe to call from the
        workflow gate and from the UI.
        """
        if application.is_converted:
            return _("This application has already been converted to a student.")

        if application.decision not in DecisionService.OFFERABLE:
            return _(
                "Only an applicant with an accepted admission decision can become "
                "a student (current decision: %(d)s)."
            ) % {"d": application.get_decision_display()}

        offer = OfferService.accepted_offer(application)
        if offer is None:
            issued = OfferService.active_offer(application)
            if issued and issued.status == OfferStatus.ISSUED:
                return _(
                    "Offer %(ref)s is awaiting the applicant's response. It must be "
                    "accepted before a student record can be created."
                ) % {"ref": issued.offer_number}
            return _("No admission offer has been issued for this application.")

        if not application.program_applied_id:
            return _("This application has no programme, so no student record can be created.")

        if offer.program_id != application.program_applied_id:
            return _(
                "The offer is for %(offer)s but the application is for %(app)s. "
                "Reissue the offer to match."
            ) % {
                "offer": offer.program.code,
                "app": application.program_applied.code,
            }

        return None

    @classmethod
    def is_ready(cls, application):
        return cls.preflight(application) is None

    @classmethod
    @transaction.atomic
    def convert(cls, application, actor, default_password=None, notify=True):
        """
        Perform the conversion. Idempotent by design: calling it twice returns
        the same student instead of creating a second account (scenario 10).

        Steps (req 9):
          1. preflight the application,
          2. resolve/create the user account and activate it,
          3. create the StudentProfile with a number from the EXISTING
             ``StudentProfile.ensure_for_student`` logic,
          4. attach the programme (department and faculty come from the
             existing academic relationships),
          5. record the admission session,
          6. enrol into any course offerings already published for the session,
          7. link everything back to the application and mark it CONVERTED.
        """
        from academics.models import Enrolment, StudentProfile

        # ── Already converted? Return the existing student, do not duplicate ──
        # This must come BEFORE preflight: preflight refuses an application
        # whose ``converted_at`` is set, so checking afterwards would make the
        # documented idempotency unreachable and a retried conversion (a double
        # click, a retried request) would fail instead of returning the student
        # that already exists.
        if application.user_id and application.converted_at:
            existing = StudentProfile.all_objects.filter(
                student_id=application.user_id
            ).first()
            if existing is not None:
                return existing, False

        problem = cls.preflight(application)
        if problem:
            raise ValidationError(problem)

        offer = OfferService.accepted_offer(application)
        program = application.program_applied
        session = offer.academic_session

        # ── 2. User account ──────────────────────────────────────────────────
        user = application.user
        if user is None:
            email = application.email
            user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User.objects.create_user(
                email=application.email,
                password=default_password or User.DEFAULT_PASSWORD,
                first_name=application.first_name,
                last_name=application.last_name,
                role="student",
                is_active=True,
            )
        else:
            # Activate and promote: an applicant is not a student until now.
            changed = []
            if not user.is_active:
                user.is_active = True
                changed.append("is_active")
            if user.role != "student":
                user.role = "student"
                changed.append("role")
            if not user.approved_at:
                user.approved_at = timezone.now()
                changed.append("approved_at")
            if changed:
                if "approved_at" in changed and actor is not None:
                    user.approved_by = actor
                    changed.append("approved_by")
                user.save(update_fields=changed)

        application.user = user

        # ── 3-5. Student record via the EXISTING numbering logic ─────────────
        profile, created = StudentProfile.ensure_for_student(
            user, program=program, year=timezone.now().year,
        )
        if program is not None and profile.current_level_id is None:
            level = program.get_starting_level()
            if level is not None:
                profile.current_level = level
        if not profile.admission_date:
            profile.admission_date = offer.issue_date or timezone.localdate()
        profile.is_active = True
        profile.save()

        # ── 6. Enrolment ─────────────────────────────────────────────────────
        enrolments = cls._enrol_available_offerings(user, program, session)

        # ── 7. Link back and close the workflow ──────────────────────────────
        from .workflow import ApplicationWorkflow
        ApplicationWorkflow.transition(
            application, AdmissionStatus.CONVERTED, actor=actor,
            remark=(
                f"Converted to student {profile.student_number}."
                + (f" Auto-enrolled in {len(enrolments)} course offering(s)."
                   if enrolments else " Enrolment deferred: no course offerings "
                                      "published for this session and level yet.")
            ),
            notify=False,
        )

        # Persisted AFTER the transition on purpose: the CONVERTED gate re-runs
        # ``preflight``, which refuses an application whose ``converted_at`` is
        # already set. Recording it first would abort every conversion.
        application.converted_at = timezone.now()
        application.converted_by = actor if (actor and actor.pk) else None
        application.user = user
        application.save(update_fields=["converted_at", "converted_by", "user", "updated_at"])
        AuditService.record(
            application, AuditAction.CONVERTED, actor=actor,
            remark=f"Student record created: {profile.student_number}",
            visible_to_applicant=True,
        )

        if notify:
            try:
                from .notify import notify_converted
                notify_converted(application, profile)
            except Exception:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).exception("Conversion notification failed")

        return profile, created

    @staticmethod
    def _enrol_available_offerings(user, program, session):
        """
        eduPro models enrolment per *CourseOffering* — there is no
        programme-level enrolment row. So we enrol the new student into every
        offering already published for the admission session at their starting
        level (falling back to their department), and simply enrol nothing when
        the department has not published offerings yet. Inventing enrolments
        against non-existent offerings would corrupt the teaching timeline.
        """
        from academics.models import CourseOffering, Enrolment

        if program is None or session is None:
            return []

        level = program.get_starting_level()
        offerings = CourseOffering.objects.filter(
            semester__session=session, is_active=True,
        )
        if level is not None:
            named = offerings.filter(level_name=level.name)
            if named.exists():
                offerings = named
            else:
                offerings = offerings.filter(
                    departments=program.department
                ) or offerings.filter(level_name=level.name)

        created = []
        for offering in offerings.select_related("course")[:200]:
            _enrolment, made = Enrolment.all_objects.get_or_create(
                student=user,
                offering=offering,
                defaults={
                    "status": Enrolment.EnrolmentStatus.ACTIVE,
                    "is_active": True,
                },
            )
            if made:
                created.append(offering)
        return created


# ─────────────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────────────

class ReportingService:
    """Dashboard statistics for admissions staff (req 20)."""

    @classmethod
    def statistics(cls, queryset):
        from django.db.models import Count, Q

        base = queryset
        counts = base.aggregate(
            total=Count("id"),
            draft=Count("id", filter=Q(status=AdmissionStatus.DRAFT)),
            submitted=Count("id", filter=Q(status=AdmissionStatus.SUBMITTED)),
            payment_pending=Count("id", filter=Q(status=AdmissionStatus.PAYMENT_PENDING)),
            payment_confirmed=Count("id", filter=Q(status=AdmissionStatus.PAYMENT_CONFIRMED)),
            under_review=Count("id", filter=Q(status=AdmissionStatus.UNDER_REVIEW)),
            needs_correction=Count("id", filter=Q(status=AdmissionStatus.NEEDS_CORRECTION)),
            resubmitted=Count("id", filter=Q(status=AdmissionStatus.RESUBMITTED)),
            shortlisted=Count("id", filter=Q(status=AdmissionStatus.SHORTLISTED)),
            interview=Count("id", filter=Q(status=AdmissionStatus.INTERVIEW_REQUIRED)),
            decision_pending=Count("id", filter=Q(status=AdmissionStatus.DECISION_PENDING)),
            completed=Count("id", filter=Q(status=AdmissionStatus.COMPLETED)),
            converted=Count("id", filter=Q(status=AdmissionStatus.CONVERTED)),
            withdrawn=Count("id", filter=Q(status=AdmissionStatus.WITHDRAWN)),
            accepted=Count("id", filter=Q(decision=AdmissionDecision.ACCEPTED)),
            conditional=Count(
                "id", filter=Q(decision=AdmissionDecision.CONDITIONALLY_ACCEPTED)
            ),
            rejected=Count("id", filter=Q(decision=AdmissionDecision.REJECTED)),
            waitlisted=Count("id", filter=Q(decision=AdmissionDecision.WAITLISTED)),
        )

        counts["offers_awaiting_response"] = AdmissionOffer.objects.filter(
            status=OfferStatus.ISSUED,
            application__in=base,
        ).count()
        counts["applications_with_outstanding_fee"] = sum(
            1 for app in base.select_related("cycle").prefetch_related("payments")
            if app.cycle_id and app.cycle.requires_payment and not app.is_fee_paid
        )
        counts["by_type"] = list(
            base.values("application_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        return counts


# ─────────────────────────────────────────────────────────────────────────────
# CYCLES
# ─────────────────────────────────────────────────────────────────────────────

class CycleService:
    """Admission cycle helpers, including first-run bootstrap data."""

    @classmethod
    def bootstrap(cls):
        """
        Idempotently create the standard application types and their default
        requirements. Safe to run on an existing database; it only adds rows
        that are missing.
        """
        created_types = ApplicationType.seed_defaults()
        created_requirements = []
        for code, docs in ChecklistService.DEFAULT_DOCUMENTS.items():
            atype = ApplicationType.for_code(code)
            if atype is None:
                continue
            for order, (doc_code, label) in enumerate(docs, start=1):
                _req, made = ApplicationRequirement.objects.get_or_create(
                    application_type=atype,
                    program=None,
                    kind=RequirementKind.DOCUMENT,
                    code=doc_code,
                    defaults={"label": label, "is_required": True, "order": order * 10},
                )
                if made:
                    created_requirements.append(f"{atype.code}:{doc_code}")
        return created_types, created_requirements

    @classmethod
    def open_cycle(cls, cycle):
        from .models import CycleStatus
        cycle.is_active = True
        cycle.status = CycleStatus.OPEN
        cycle.save()
        return cycle

    @classmethod
    def close_cycle(cls, cycle):
        from .models import CycleStatus
        cycle.status = CycleStatus.CLOSED
        cycle.save()
        return cycle


class ApplicationTypeCatalog:
    """
    Answers the question "what may an applicant choose right now?".

    Two limits are applied, in this order:

    1. the cycle — if the cycle names the types it accepts, only those;
    2. the programme — a programme only offers the types it actually runs, so a
       diploma programme is not offered postgraduate and the reverse.

    Either limit being absent (a cycle that accepts anything, a programme that
    has no restriction recorded) means "no restriction from here".
    """

    @staticmethod
    def active_types():
        return ApplicationType.objects.filter(is_active=True).order_by("order", "code")

    @classmethod
    def for_cycle(cls, cycle):
        """
        Types the given cycle accepts, ignoring programmes.

        An unrestricted cycle falls back to every active type, so the dropdown
        is never empty just because nobody configured it.
        """
        accepted = cycle.accepted_types() if cycle is not None else []
        usable = [t for t in accepted if t.is_active]
        return usable if usable else list(cls.active_types())

    @classmethod
    def for_program(cls, program):
        """Types *program* accepts, ignoring the cycle."""
        from .models import program_available_types

        return program_available_types(program)

    @classmethod
    def available(cls, cycle=None, program=None):
        """
        The types to offer, narrowed by the cycle and then by the programme.

        Returns an empty list only when both limits genuinely rule everything
        out; callers should treat that as "nothing to offer" rather than
        silently widening the list.
        """
        types = cls.for_cycle(cycle)
        if program is not None:
            allowed = {t.pk for t in cls.for_program(program)}
            types = [t for t in types if t.pk in allowed]
        return types

    @classmethod
    def is_valid_pair(cls, cycle, program, application_type):
        """
        True when this programme may be applied for under this type.

        Used for server-side validation so a crafted POST cannot post a
        combination the dropdown never offered.
        """
        if application_type is None:
            return True
        atype = ApplicationType.for_code(application_type) if isinstance(
            application_type, str
        ) else application_type
        if atype is None:
            return True
        if not cls.active_types().filter(pk=atype.pk).exists():
            return False
        allowed = {t.pk for t in cls.available(cycle=cycle, program=program)}
        return atype.pk in allowed


# Imported late to avoid a circular import at module load time.
