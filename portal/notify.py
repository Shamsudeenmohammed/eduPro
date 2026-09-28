"""
portal/notify.py

The single bridge between the admissions engine and eduPro's existing
notification architecture (requirement 18).

No view or service in ``portal`` talks to Sailup, SMTP or the in-app bell
directly. They call the functions here, which in turn call the existing
``notifications.services.NotificationService``. Sailup (SMS) and the Django
SMTP/email path are therefore reused exactly as the rest of eduPro uses them —
no second notification system is introduced.

Every function is defensive: a notification failure is logged and swallowed by
the caller so that a flaky SMS gateway can never roll back an admission
decision.
"""

import logging

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _applicant_link():
    try:
        return reverse("portal:applicant_dashboard")
    except NoReverseMatch:
        return "/portal/applicant/"


def _base_context(application):
    program = application.program_applied
    return {
        "ref": application.reference_number,
        "program": program.name if program else "",
        "program_suffix": f" for {program.name}" if program else "",
        "link": _applicant_link(),
    }


def _send(notification_type, application, context, *, priority="normal",
          channels=None, force_channels=None):
    """
    Dispatch one notification to the applicant.

    An application with no linked user (e.g. a legacy public application) is
    simply skipped — there is nobody to notify and the DB should not grow rows
    for an anonymous addressee.
    """
    recipient = application.user
    if recipient is None or not getattr(recipient, "pk", None):
        logger.info(
            "Admissions notification %s skipped: application %s has no linked user",
            notification_type, application.reference_number,
        )
        return []

    from notifications.models import NotificationType, Priority
    from notifications.services import NotificationService

    if isinstance(notification_type, str):
        # The event maps above name the type as it is declared on
        # ``NotificationType`` (``"APPLICATION_SUBMITTED"``). The notification
        # service keys its category/channel tables on the enum *member*, so a
        # bare string raises KeyError and the applicant is silently never told
        # anything. Resolve it here, once, at the boundary.
        notification_type = getattr(NotificationType, notification_type, None)
        if notification_type is None:
            logger.error(
                "Unknown admissions notification type %r for application %s",
                notification_type, application.reference_number,
            )
            return []

    payload = {**_base_context(application), **(context or {})}
    return NotificationService.send(
        notification_type=notification_type,
        recipients=[recipient],
        context=payload,
        obj=application,
        module="ADMISSIONS",
        priority=getattr(Priority, priority.upper(), Priority.NORMAL),
        channels=channels,
        force_channels=force_channels,
        # Re-running a workflow step must not spam the applicant.
        idempotency_key=(
            f"{notification_type.name}:{application.pk}:{payload.get('to_status', '')}"
            f"{payload.get('event', '')}"
        ),
    )


# ── Event hooks ─────────────────────────────────────────────────────────────

#: Which notification a given workflow transition produces. Transitions absent
#: from this map are intentionally silent.
_STATUS_EVENTS = {
    "submitted": "APPLICATION_SUBMITTED",
    "payment_confirmed": "APPLICATION_PAYMENT_CONFIRMED",
    "needs_correction": "APPLICATION_CORRECTION_REQUESTED",
    "under_review": "APPLICATION_UNDER_REVIEW",
    "resubmitted": "APPLICATION_SUBMITTED",
    "shortlisted": "APPLICATION_SHORTLISTED",
    "interview_required": "APPLICATION_INTERVIEW_REQUIRED",
    # NOTE: "completed" is deliberately absent. Reaching COMPLETED records a
    # decision, it does not mean an offer exists. DecisionService owns the
    # outcome message and OfferService owns the offer-issued message, so
    # mapping COMPLETED here would claim an offer before one was issued.
    "converted": "APPLICATION_CONVERTED",
}


def notify_status_change(application, to_status, actor=None, remark=""):
    """Called by :class:`portal.workflow.ApplicationWorkflow` after a move."""
    from .workflow import ApplicationWorkflow

    event = _STATUS_EVENTS.get(to_status)
    if event is None:
        return []

    context = {
        "to_status": to_status,
        "event": to_status,
        "status": application.get_status_display(),
        "action": ApplicationWorkflow.next_action_for_applicant(application),
        "reason": remark or "",
        "missing": ", ".join(application.missing_requirements[:5]),
    }
    priority = "high" if to_status in (
        ApplicationWorkflow.S.NEEDS_CORRECTION,
        ApplicationWorkflow.S.INTERVIEW_REQUIRED,
    ) else "normal"
    return _send(event, application, context, priority=priority)


def notify_payment_confirmed(payment):
    return _send(
        "APPLICATION_PAYMENT_CONFIRMED",
        payment.application,
        {"amount": payment.amount, "event": "payment"},
    )


def notify_document_event(application, document, event, reason=""):
    mapping = {
        "uploaded": "APPLICATION_DOCUMENT_UPLOADED",
        "verified": "APPLICATION_DOCUMENT_VERIFIED",
        "rejected": "APPLICATION_DOCUMENT_REJECTED",
        "replacement": "APPLICATION_DOCUMENT_REJECTED",
    }
    notification_type = mapping.get(event)
    if notification_type is None:
        return []
    return _send(
        notification_type,
        application,
        {
            "document": document.get_document_type_display(),
            "reason": reason,
            "event": event,
            "version": document.version,
        },
    )


_DECISION_MESSAGES = {
    "accepted": "Congratulations — you have been admitted. Your offer will appear in your portal shortly.",
    "conditionally_accepted": "You have been admitted subject to conditions. Read them carefully in your portal.",
    "rejected": "We regret that we cannot offer you a place this cycle. You are welcome to apply again.",
    "waitlisted": "You have been placed on the waiting list. We will contact you if a place becomes available.",
    "pending": "Your application is still awaiting a decision.",
}


def notify_decision(application, decision, notes=""):
    return _send(
        "APPLICATION_DECISION_MADE",
        application,
        {
            "decision": application.get_decision_display(),
            "notes": notes,
            "action": _DECISION_MESSAGES.get(decision, ""),
            "event": decision,
        },
        priority="high",
    )


_OFFER_MESSAGES = {
    "issued": "APPLICATION_OFFER_ISSUED",
    "accepted": "APPLICATION_OFFER_ACCEPTED",
    "declined": "APPLICATION_OFFER_DECLINED",
    "expiring": "APPLICATION_OFFER_EXPIRING",
    "expired": "APPLICATION_OFFER_EXPIRING",
}


def notify_offer_event(application, offer, event):
    notification_type = _OFFER_MESSAGES.get(event)
    if notification_type is None:
        return []
    return _send(
        notification_type,
        application,
        {
            "offer_number": offer.offer_number,
            "expiry": offer.expiry_date,
            "conditions": offer.conditions,
            "event": event,
        },
        priority="high",
    )


def notify_converted(application, profile):
    return _send(
        "APPLICATION_CONVERTED",
        application,
        {
            "student_number": profile.student_number,
            "event": "converted",
        },
        priority="high",
    )


def notify_offer_expiring(offer):
    """Called by the ``notify_expiring_offers`` management command."""
    from .models import OfferStatus
    if offer.status != OfferStatus.ISSUED:
        return []
    return notify_offer_event(offer.application, offer, "expiring")
