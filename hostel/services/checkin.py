"""Hostel check-in service — physical occupancy. Staff only."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditAction
from hostel.models import HostelAllocation, HostelBed

from .allocation import HostelAllocationService
from .base import (
    BedUnavailableError,
    HostelAuditService,
    HostelNotificationService,
    HostelServiceError,
    PaymentRequiredError,
    reverse_url,
)
from .finance import HostelFinanceService
from .policy import HostelPolicyService


class HostelCheckInService:
    """Physical occupancy. Only authorized hostel staff may call this."""

    @classmethod
    def check_in(cls, *, allocation, actor, note=""):
        policy = HostelPolicyService.get_policy()
        with transaction.atomic():
            alloc = HostelAllocationService._lock_allocation(allocation)
            if alloc.status != HostelAllocation.Status.RESERVED:
                raise HostelServiceError(
                    _("Only reserved allocations can be checked in. Current status: %s.")
                    % alloc.get_status_display()
                )

            # Financial gate (finance is the source of truth).
            if policy and policy.require_payment_before_checkin and policy.enable_hostel_charges:
                if not HostelFinanceService.payment_satisfied(
                    alloc.student, alloc.session, policy=policy
                ):
                    raise PaymentRequiredError(
                        _("Hostel check-in cannot be completed because the required payment "
                          "has not been confirmed.")
                    )

            if not alloc.bed_id:
                raise HostelServiceError(_("This allocation has no bed assigned."))

            bed = HostelBed.objects.select_for_update().get(pk=alloc.bed_id)
            if not bed.is_occupiable:
                raise BedUnavailableError(
                    _("The assigned bed is currently unavailable for occupancy.")
                )
            if bed.state not in (HostelBed.BedState.RESERVED, HostelBed.BedState.AVAILABLE):
                raise BedUnavailableError(
                    _("The assigned bed can no longer be occupied.")
                )

            alloc.status = HostelAllocation.Status.ACTIVE
            alloc.checked_in_by = actor
            alloc.checked_in_at = timezone.now()
            alloc.check_in = timezone.localdate()
            alloc.notes = (alloc.notes + ("\n" + note) if alloc.notes else note).strip()
            if not alloc.expires_at:
                alloc.expires_at = alloc.check_in + timedelta(days=365)
            alloc.save()

            bed.state = HostelBed.BedState.OCCUPIED
            bed.save(update_fields=["state", "updated_at"])

            HostelAuditService.record(
                actor, AuditAction.APPROVE, "HostelAllocation", alloc,
                changes={"status": HostelAllocation.Status.ACTIVE, "note": note},
            )
            HostelNotificationService.notify(
                alloc.student,
                "Hostel check-in complete",
                f"Welcome! You are now checked in to {alloc.hierarchy_display}.",
                reverse_url("hostel:hostel"),
            )
        return alloc


class HostelCheckoutService:

    @classmethod
    def checkout(cls, *, allocation, actor, note=""):
        with transaction.atomic():
            alloc = HostelAllocationService._lock_allocation(allocation)
            if alloc.status != HostelAllocation.Status.ACTIVE:
                raise HostelServiceError(
                    _("Only active allocations can be checked out.")
                )
            alloc.status = HostelAllocation.Status.CHECKED_OUT
            alloc.checked_out_by = actor
            alloc.checked_out_at = timezone.now()
            alloc.check_out = timezone.localdate()
            alloc.notes = (alloc.notes + ("\n" + note) if alloc.notes else note).strip()
            alloc.save()

            if alloc.bed_id:
                bed = HostelBed.objects.select_for_update().get(pk=alloc.bed_id)
                if bed.state == HostelBed.BedState.OCCUPIED:
                    bed.state = HostelBed.BedState.AVAILABLE
                    bed.save(update_fields=["state", "updated_at"])

            HostelAuditService.record(
                actor, AuditAction.UPDATE, "HostelAllocation", alloc,
                changes={"status": HostelAllocation.Status.CHECKED_OUT, "note": note},
            )
        return alloc