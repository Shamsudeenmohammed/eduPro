"""Hostel application service."""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditAction
from hostel.models import HostelApplication

from .base import (
    HostelAuditService,
    HostelNotificationService,
    EligibilityError,
    HostelServiceError,
    PaymentRequiredError,
    reverse_url,
)
from .eligibility import HostelEligibilityService
from .finance import HostelFinanceService
from .policy import HostelPolicyService


class HostelApplicationService:

    @classmethod
    def submit_application(cls, *, student, room, session=None, actor=None):
        errors = HostelEligibilityService.can_apply(student, session)
        if errors:
            raise EligibilityError(" ".join(str(e) for e in errors))

        session = HostelEligibilityService.get_session(session)
        if session is None:
            raise EligibilityError(_("No academic session is currently set for the institution."))

        application = HostelApplication.objects.create(
            student=student,
            room=room,
            session=session,
            status=HostelApplication.Status.PENDING,
            submitted_at=timezone.now(),
        )
        HostelNotificationService.notify(
            student,
            "Hostel application submitted",
            f"Your hostel application for {room} has been submitted successfully.",
            reverse_url("hostel:hostel"),
        )
        HostelAuditService.record(
            actor or student, AuditAction.CREATE, "HostelApplication", application,
            changes={"room": str(room), "session": str(session)},
        )
        return application

    @classmethod
    def review_application(cls, application, actor, approve, remark=""):
        """
        Approve or reject a pending application. Approval only marks the
        application; a dedicated allocation still must be created by staff.
        """
        with transaction.atomic():
            app = (
                HostelApplication.objects.select_for_update()
                .select_related("student", "room")
                .get(pk=application.pk)
            )
            if app.status not in (HostelApplication.Status.PENDING,
                                  HostelApplication.Status.UNDER_REVIEW):
                raise HostelServiceError(_("This application has already been reviewed."))

            app.admin_remark = remark or app.admin_remark
            app.reviewed_by = actor
            app.reviewed_at = timezone.now()

            if approve:
                app.status = HostelApplication.Status.APPROVED
                HostelNotificationService.notify(
                    app.student,
                    "Hostel application approved",
                    "Your hostel application has been approved.",
                    reverse_url("hostel:hostel"),
                )
                action = AuditAction.APPROVE
            else:
                app.status = HostelApplication.Status.REJECTED
                HostelNotificationService.notify(
                    app.student,
                    "Hostel application rejected",
                    f"Your hostel application was rejected{(' — ' + remark) if remark else ''}.",
                    reverse_url("hostel:hostel"),
                )
                action = AuditAction.REJECT

            app.save()
            HostelAuditService.record(
                actor, action, "HostelApplication", app,
                changes={"status": app.status, "remark": remark},
            )
        return app

    @classmethod
    def cancel_application(cls, application, actor=None, remark=""):
        with transaction.atomic():
            app = HostelApplication.objects.select_for_update().get(pk=application.pk)
            if app.status in (HostelApplication.Status.CONFIRMED,
                              HostelApplication.Status.CANCELLED,
                              HostelApplication.Status.EXPIRED):
                raise HostelServiceError(_("This application cannot be cancelled."))
            app.status = HostelApplication.Status.CANCELLED
            app.admin_remark = remark or app.admin_remark
            app.save()
            HostelAuditService.record(
                actor, AuditAction.UPDATE, "HostelApplication", app,
                changes={"status": app.status},
            )
        return app

    @classmethod
    def mark_payment_verified(cls, application, actor=None):
        """
        Confirms through finance that the student's hostel payment requirement
        is satisfied. Does not create an allocation (that is staff-driven).
        """
        policy = HostelPolicyService.get_policy()
        with transaction.atomic():
            app = HostelApplication.objects.select_for_update().select_related("student").get(pk=application.pk)
            if app.status != HostelApplication.Status.APPROVED:
                raise HostelServiceError(_("Only approved applications can confirm payment."))
            if policy and policy.require_payment_before_checkin and policy.enable_hostel_charges:
                if not HostelFinanceService.payment_satisfied(
                    app.student, app.session, policy=policy
                ):
                    raise PaymentRequiredError(
                        _("Your hostel payment has not been fully confirmed. "
                          "Contact the finance office.")
                    )
            app.payment_verified_at = timezone.now()
            app.save()
            HostelNotificationService.notify(
                app.student,
                "Hostel payment confirmed",
                "Your hostel payment has been confirmed. A bed will be allocated shortly.",
                reverse_url("hostel:hostel"),
            )
        return app