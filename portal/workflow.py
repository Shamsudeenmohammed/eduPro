"""
portal/workflow.py

The single place where an application's workflow status may change.

Requirement 5 of the upgrade: *"Do not allow unrelated views or users to
arbitrarily change application status. Centralize status transitions through a
service/workflow layer."*

Every status change in eduPro admissions therefore goes through
:meth:`ApplicationWorkflow.transition`, which:

  1. resolves legacy status values onto the current vocabulary,
  2. checks the requested move against the transition graph,
  3. enforces business gates (payment, offer, conversion prerequisites),
  4. writes an :class:`~portal.models.ApplicationAuditLog` row, and
  5. fires the matching notification through the existing notification layer.

Views and models deliberately do **not** assign ``application.status``
directly. ``AdmissionApplication.approve()`` is retained only for backward
compatibility with existing bookmarks and is re-implemented on top of this
layer, so there is exactly one source of truth for workflow rules.
"""

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import AdmissionDecision, AdmissionStatus


class TransitionNotAllowed(ValidationError):
    """Raised when a requested workflow move is not permitted."""


class ApplicationWorkflow:
    """
    Declarative transition graph plus the guards that protect it.

    Stages that a particular institution does not use can simply be left out of
    an application's path: the graph is a set of permitted *edges*, not a
    mandatory sequence, so an institution that never interviews anybody jumps
    straight from ``SHORTLISTED`` to ``DECISION_PENDING``.
    """

    S = AdmissionStatus

    #: Legacy stored values mapped onto the current vocabulary. The legacy
    #: values themselves stay in the choices so old rows keep rendering.
    LEGACY_ALIASES = {
        AdmissionStatus.PENDING:   AdmissionStatus.SUBMITTED,
        AdmissionStatus.REVIEW:    AdmissionStatus.UNDER_REVIEW,
        AdmissionStatus.REVIEWING: AdmissionStatus.UNDER_REVIEW,
        AdmissionStatus.APPROVED:  AdmissionStatus.COMPLETED,
        AdmissionStatus.ACCEPTED:  AdmissionStatus.DECISION_PENDING,
    }

    #: ``from status -> statuses reachable from it``. Every value is a member
    #: of :class:`~portal.models.AdmissionStatus`.
    TRANSITIONS = {
        AdmissionStatus.DRAFT: {
            AdmissionStatus.SUBMITTED,
            AdmissionStatus.WITHDRAWN,
        },
        AdmissionStatus.SUBMITTED: {
            AdmissionStatus.PAYMENT_PENDING,
            AdmissionStatus.PAYMENT_CONFIRMED,
            AdmissionStatus.UNDER_REVIEW,
            AdmissionStatus.NEEDS_CORRECTION,
            AdmissionStatus.REJECTED,
            AdmissionStatus.WAITLIST,
            AdmissionStatus.WITHDRAWN,
        },
        AdmissionStatus.PAYMENT_PENDING: {
            AdmissionStatus.PAYMENT_CONFIRMED,
            AdmissionStatus.NEEDS_CORRECTION,
            AdmissionStatus.REJECTED,
            AdmissionStatus.WITHDRAWN,
        },
        AdmissionStatus.PAYMENT_CONFIRMED: {
            AdmissionStatus.UNDER_REVIEW,
            AdmissionStatus.NEEDS_CORRECTION,
            AdmissionStatus.REJECTED,
            AdmissionStatus.WITHDRAWN,
        },
        AdmissionStatus.UNDER_REVIEW: {
            AdmissionStatus.NEEDS_CORRECTION,
            AdmissionStatus.SHORTLISTED,
            AdmissionStatus.INTERVIEW_REQUIRED,
            AdmissionStatus.INTERVIEW_COMPLETED,
            AdmissionStatus.DECISION_PENDING,
            AdmissionStatus.REJECTED,
            AdmissionStatus.WAITLIST,
        },
        AdmissionStatus.NEEDS_CORRECTION: {
            AdmissionStatus.RESUBMITTED,
            AdmissionStatus.REJECTED,
            AdmissionStatus.WITHDRAWN,
        },
        AdmissionStatus.RESUBMITTED: {
            AdmissionStatus.PAYMENT_PENDING,
            AdmissionStatus.PAYMENT_CONFIRMED,
            AdmissionStatus.UNDER_REVIEW,
            AdmissionStatus.NEEDS_CORRECTION,
            AdmissionStatus.REJECTED,
            AdmissionStatus.WITHDRAWN,
        },
        AdmissionStatus.SHORTLISTED: {
            AdmissionStatus.INTERVIEW_REQUIRED,
            AdmissionStatus.INTERVIEW_COMPLETED,
            AdmissionStatus.DECISION_PENDING,
            AdmissionStatus.REJECTED,
            AdmissionStatus.WAITLIST,
        },
        AdmissionStatus.INTERVIEW_REQUIRED: {
            AdmissionStatus.INTERVIEW_COMPLETED,
            AdmissionStatus.DECISION_PENDING,
            AdmissionStatus.REJECTED,
        },
        AdmissionStatus.INTERVIEW_COMPLETED: {
            AdmissionStatus.SHORTLISTED,
            AdmissionStatus.DECISION_PENDING,
            AdmissionStatus.REJECTED,
        },
        AdmissionStatus.DECISION_PENDING: {
            AdmissionStatus.SHORTLISTED,
            AdmissionStatus.WAITLIST,
            AdmissionStatus.COMPLETED,
            AdmissionStatus.REJECTED,
        },
        AdmissionStatus.COMPLETED: {
            AdmissionStatus.CONVERTED,
        },
        AdmissionStatus.WAITLIST: {
            AdmissionStatus.SHORTLISTED,
            AdmissionStatus.DECISION_PENDING,
            AdmissionStatus.REJECTED,
        },
        AdmissionStatus.REJECTED: set(),
        AdmissionStatus.WITHDRAWN: set(),
        AdmissionStatus.CONVERTED: set(),
    }

    #: Statuses that close an application for good.
    TERMINAL = {
        AdmissionStatus.REJECTED,
        AdmissionStatus.WITHDRAWN,
        AdmissionStatus.CONVERTED,
    }

    #: Statuses the applicant themselves may move an application to. Anything
    #: not listed here is staff-only, which is what stops a crafted POST from
    #: shortlisting an application.
    APPLICANT_TRANSITIONS = {
        AdmissionStatus.DRAFT: {AdmissionStatus.SUBMITTED},
        AdmissionStatus.NEEDS_CORRECTION: {
            AdmissionStatus.RESUBMITTED, AdmissionStatus.WITHDRAWN,
        },
        AdmissionStatus.SUBMITTED: {AdmissionStatus.WITHDRAWN},
        AdmissionStatus.PAYMENT_PENDING: {AdmissionStatus.WITHDRAWN},
        AdmissionStatus.UNDER_REVIEW: {AdmissionStatus.WITHDRAWN},
        AdmissionStatus.RESUBMITTED: {AdmissionStatus.WITHDRAWN},
    }

    #: Statuses in which the applicant may still change their own answers.
    APPLICANT_EDITABLE = {
        AdmissionStatus.DRAFT,
        AdmissionStatus.NEEDS_CORRECTION,
    }

    #: Where each status change should point the applicant next.
    APPLICANT_NEXT_ACTION = {
        AdmissionStatus.DRAFT: "Complete and submit your application.",
        AdmissionStatus.SUBMITTED: "We have your application. Pay the application fee to continue.",
        AdmissionStatus.PAYMENT_PENDING: "Pay the application fee to continue.",
        AdmissionStatus.PAYMENT_CONFIRMED: "Payment received. Your application is queued for review.",
        AdmissionStatus.UNDER_REVIEW: "Your application is being reviewed. No action needed.",
        AdmissionStatus.NEEDS_CORRECTION: "Corrections were requested. Update and resubmit.",
        AdmissionStatus.RESUBMITTED: "Corrections received. Your application is queued for review.",
        AdmissionStatus.SHORTLISTED: "You have been shortlisted. No action needed.",
        AdmissionStatus.INTERVIEW_REQUIRED: "An interview has been scheduled. Check your contact details.",
        AdmissionStatus.INTERVIEW_COMPLETED: "Interview completed. Awaiting a decision.",
        AdmissionStatus.DECISION_PENDING: "A decision is being made. No action needed.",
        AdmissionStatus.COMPLETED: "Admission granted. Check your offer and respond to it.",
        AdmissionStatus.CONVERTED: "Welcome aboard — you are now a registered student.",
        AdmissionStatus.REJECTED: "This application was not successful.",
        AdmissionStatus.WAITLIST: "You are on the waiting list.",
        AdmissionStatus.WITHDRAWN: "You withdrew this application.",
    }

    # ── Value helpers ─────────────────────────────────────────────────────────

    @classmethod
    def normalize(cls, status):
        """Map a possibly-legacy status onto the current vocabulary."""
        return cls.LEGACY_ALIASES.get(status, status)

    @classmethod
    def is_terminal(cls, status):
        return cls.normalize(status) in cls.TERMINAL

    @classmethod
    def allowed_targets(cls, status):
        return set(cls.TRANSITIONS.get(cls.normalize(status), set()))

    # ── Guards ────────────────────────────────────────────────────────────────

    @classmethod
    def can_transition(cls, application, to_status, *, as_applicant=False,
                       bypass_payment_gate=False):
        """
        Return ``(allowed, reason)``. ``reason`` is a translated, user-facing
        explanation intended to be shown straight to the user.
        """
        current = cls.normalize(application.status)

        if to_status == current:
            return False, _("This application is already at that stage.")

        if current in cls.TERMINAL:
            return False, _(
                "This application is closed (%(status)s) and can no longer change stage."
            ) % {"status": application.get_status_display()}

        allowed = cls.allowed_targets(current)
        if to_status not in allowed:
            return False, _(
                "An application cannot move from %(frm)s to %(to)s."
            ) % {
                "frm": current.replace("_", " ").title(),
                "to": to_status.replace("_", " ").title(),
            }

        if as_applicant and to_status not in cls.APPLICANT_TRANSITIONS.get(current, set()):
            return False, _("You cannot move your application to that stage yourself.")

        gate_error = cls._check_business_gates(
            application, to_status, bypass_payment_gate=bypass_payment_gate
        )
        if gate_error:
            return False, gate_error

        return True, ""

    @classmethod
    def _check_business_gates(cls, application, to_status, bypass_payment_gate=False):
        """
        Rules that are not about the shape of the graph but about real-world
        prerequisites. Returning a message means "blocked".
        """
        cycle = application.cycle

        # Application fee must be *verified* before substantive review.
        if (
            not bypass_payment_gate
            and to_status in (AdmissionStatus.UNDER_REVIEW, AdmissionStatus.SHORTLISTED)
            and cycle
            and cycle.payment_required_to_progress
            and cycle.requires_payment
            and not application.is_fee_paid
        ):
            return _(
                "The application fee for %(cycle)s must be confirmed before this "
                "application can be reviewed."
            ) % {"cycle": cycle.name}

        # Cannot shortlist or progress while corrections are outstanding.
        if (
            to_status in (AdmissionStatus.SHORTLISTED, AdmissionStatus.DECISION_PENDING)
            and application.status == AdmissionStatus.NEEDS_CORRECTION
        ):
            return _("Outstanding corrections must be resubmitted first.")

        # Conversion requires a completed, admitted, offer-accepted application.
        if to_status == AdmissionStatus.CONVERTED:
            from .services import StudentConversionService
            problem = StudentConversionService.preflight(application)
            if problem:
                return problem

        return None

    @classmethod
    def is_applicant_editable(cls, application):
        """Server-side check backing requirement 22 (immutable once submitted)."""
        return application.status in cls.APPLICANT_EDITABLE

    @classmethod
    def applicant_targets(cls, application):
        current = cls.normalize(application.status)
        return sorted(cls.APPLICANT_TRANSITIONS.get(current, set()))

    @classmethod
    def staff_targets(cls, application):
        """Targets staff may move this application to right now."""
        current = cls.normalize(application.status)
        targets = []
        for target in sorted(cls.allowed_targets(current)):
            allowed, _ = cls.can_transition(application, target)
            if allowed:
                targets.append(target)
        return targets

    @classmethod
    def next_action_for_applicant(cls, application):
        return cls.APPLICANT_NEXT_ACTION.get(
            cls.normalize(application.status), ""
        )

    # ── The one mutating entry point ──────────────────────────────────────────

    @classmethod
    def transition(
        cls,
        application,
        to_status,
        actor=None,
        remark="",
        *,
        as_applicant=False,
        bypass_payment_gate=False,
        notify=True,
    ):
        """
        Move ``application`` to ``to_status``.

        This is the only supported way to change workflow status. It validates,
        persists, audits and notifies as one logical operation, and raises
        :class:`TransitionNotAllowed` (a ``ValidationError``) when refused.

        ``bypass_payment_gate`` waives *only* the application-fee rule, for
        staff who have confirmed a fee offline. Structural rules — the
        transition graph, terminal states and applicant restrictions — are
        never bypassable.
        """
        from .models import ApplicationAuditLog, AuditAction

        to_status = to_status if hasattr(to_status, "value") else str(to_status)
        current = cls.normalize(application.status)

        if current == to_status:
            return application

        allowed, reason = cls.can_transition(
            application, to_status,
            as_applicant=as_applicant,
            bypass_payment_gate=bypass_payment_gate,
        )
        if not allowed:
            raise TransitionNotAllowed(reason)

        application.status = to_status
        if to_status == AdmissionStatus.SUBMITTED and not application.submitted_at:
            application.submitted_at = timezone.now()
        if to_status == AdmissionStatus.NEEDS_CORRECTION:
            application.correction_requested_at = timezone.now()
            application.last_correction_note = remark or ""
        if to_status == AdmissionStatus.INTERVIEW_COMPLETED:
            application.interview_remarks = remark or application.interview_remarks
        # Keep the original "who started reviewing this" columns meaningful.
        if to_status == AdmissionStatus.UNDER_REVIEW and actor is not None:
            application.reviewed_by = actor
            application.reviewed_at = timezone.now()
        application.save(update_fields=[
            "status", "submitted_at", "correction_requested_at",
            "last_correction_note", "interview_remarks",
            "reviewed_by", "reviewed_at", "updated_at",
        ])

        ApplicationAuditLog.objects.create(
            application=application,
            action=(
                AuditAction.WITHDRAWN
                if to_status == AdmissionStatus.WITHDRAWN
                else AuditAction.STATUS_CHANGED
            ),
            actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
            from_status=current,
            to_status=to_status,
            remark=remark or "",
            is_visible_to_applicant=True,
        )

        if notify:
            cls._notify(application, to_status, actor, remark)

        return application

    # ── Notifications (delegated to the existing notifications app) ───────────

    @classmethod
    def _notify(cls, application, to_status, actor, remark):
        try:
            from .notify import notify_status_change
            notify_status_change(application, to_status, actor, remark)
        except Exception:  # noqa: BLE001 - a notification must never break a workflow move
            import logging
            logging.getLogger(__name__).exception(
                "Admissions notification failed for %s", application.reference_number
            )

    # ── Backward compatibility helpers ────────────────────────────────────────

    @classmethod
    def mark_reviewing(cls, application, actor):
        """Legacy alias for the old ``AdmissionApplication.mark_reviewing``."""
        return cls.transition(
            application, AdmissionStatus.UNDER_REVIEW, actor=actor,
            remark="Review started.",
        )

    @classmethod
    def request_correction(cls, application, actor, reason):
        return cls.transition(
            application, AdmissionStatus.NEEDS_CORRECTION,
            actor=actor, remark=reason,
        )

    @classmethod
    def submit(cls, application, actor=None):
        return cls.transition(
            application, AdmissionStatus.SUBMITTED, actor=actor,
            as_applicant=True, remark="Application submitted by applicant.",
        )

    @classmethod
    def withdraw(cls, application, actor=None):
        return cls.transition(
            application, AdmissionStatus.WITHDRAWN, actor=actor,
            as_applicant=True, remark="Withdrawn by applicant.",
        )
