"""Hostel transfer service."""

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditAction
from hostel.models import HostelAllocation, HostelBed, HostelTransfer

from .base import (
    BedUnavailableError,
    HostelAuditService,
    HostelNotificationService,
    HostelServiceError,
    StudentConflictError,
    reverse_url,
)
from .eligibility import HostelEligibilityService
from .policy import HostelPolicyService


class HostelTransferService:

    @classmethod
    def request_transfer(cls, *, student, old_allocation, new_bed, reason="", actor=None):
        policy = HostelPolicyService.get_policy()
        if policy and not policy.allow_transfers:
            raise HostelServiceError(_("Hostel transfers are currently not allowed."))

        if old_allocation.student_id != student.pk:
            raise HostelServiceError(_("This allocation does not belong to you."))
        if old_allocation.status not in (HostelAllocation.Status.RESERVED,
                                         HostelAllocation.Status.ACTIVE):
            raise HostelServiceError(_("You do not have an active allocation to transfer."))
        if not old_allocation.bed_id:
            raise HostelServiceError(_("Your current allocation has no bed assigned."))

        if old_allocation.bed.room.hostel_id != new_bed.room.hostel_id:
            if policy and not policy.allow_hostel_transfers:
                raise HostelServiceError(
                    _("Transfers to a different hostel are currently not allowed.")
                )

        new_bed.refresh_from_db()
        if new_bed.state != HostelBed.BedState.AVAILABLE:
            raise BedUnavailableError(_("The requested bed is no longer available."))

        transfer = HostelTransfer.objects.create(
            student=student,
            old_allocation=old_allocation,
            old_bed=old_allocation.bed,
            new_bed=new_bed,
            reason=reason,
            status=HostelTransfer.Status.PENDING,
            requested_by=actor or student,
        )
        HostelNotificationService.notify(
            student,
            "Transfer request submitted",
            f"Your transfer request to {new_bed} has been submitted for review.",
            reverse_url("hostel:hostel"),
        )
        HostelAuditService.record(
            actor or student, AuditAction.CREATE, "HostelTransfer", transfer,
            changes={"new_bed": str(new_bed)},
        )
        return transfer

    @classmethod
    def review_transfer(cls, transfer, actor, approve, note=""):
        with transaction.atomic():
            t = HostelTransfer.objects.select_for_update().get(pk=transfer.pk)
            if t.status != HostelTransfer.Status.PENDING:
                raise HostelServiceError(_("This transfer has already been reviewed."))
            t.reviewed_by = actor
            t.reviewed_at = timezone.now()
            t.review_note = note
            if approve:
                t.status = HostelTransfer.Status.APPROVED
            else:
                t.status = HostelTransfer.Status.REJECTED
                HostelNotificationService.notify(
                    t.student,
                    "Transfer request rejected",
                    f"Your hostel transfer request was rejected{(' — ' + note) if note else ''}.",
                    reverse_url("hostel:hostel"),
                )
            t.save()
            HostelAuditService.record(
                actor, AuditAction.APPROVE if approve else AuditAction.REJECT,
                "HostelTransfer", t, changes={"status": t.status, "note": note},
            )
        return t

    @classmethod
    def complete_transfer(cls, transfer, actor):
        """
        Executes an approved transfer inside one transaction:
        close old allocation + release old bed → create new allocation →
        record history on the transfer.
        """
        with transaction.atomic():
            t = (
                HostelTransfer.objects.select_for_update()
                .select_related("student", "old_allocation", "old_bed", "new_bed")
                .get(pk=transfer.pk)
            )
            if t.status != HostelTransfer.Status.APPROVED:
                raise HostelServiceError(_("Only approved transfers can be completed."))

            new_bed = HostelBed.objects.select_for_update().get(pk=t.new_bed_id)
            if new_bed.state != HostelBed.BedState.AVAILABLE:
                raise BedUnavailableError(_("The new bed is no longer available."))

            session = t.old_allocation.session if t.old_allocation else None
            if session is None:
                raise HostelServiceError(_("The previous allocation has no academic session."))

            if HostelAllocation.objects.filter(
                student=t.student, session=session,
                status__in=HostelEligibilityService.LIVE_ALLOCATION_STATUSES,
            ).exclude(pk=t.old_allocation_id).exists():
                raise StudentConflictError(
                    _("The student already has another live allocation for this period.")
                )
            if HostelAllocation.objects.filter(
                bed=new_bed, session=session,
                status__in=HostelEligibilityService.LIVE_ALLOCATION_STATUSES,
            ).exists():
                raise BedUnavailableError(_("The new bed was just assigned to another student."))

            # Close the old allocation FIRST so the partial unique constraint
            # on (student, session) for live allocations is not violated by the
            # transient window between creating the new allocation and closing
            # the old one. Everything rolls back together if any step fails.
            old_alloc = t.old_allocation
            if old_alloc and old_alloc.status in (HostelAllocation.Status.RESERVED,
                                                  HostelAllocation.Status.ACTIVE):
                old_alloc = HostelAllocation.objects.select_for_update().get(pk=old_alloc.pk)
                old_alloc.status = HostelAllocation.Status.CHECKED_OUT
                old_alloc.checked_out_by = actor
                old_alloc.checked_out_at = timezone.now()
                old_alloc.check_out = timezone.localdate()
                old_alloc.save()
                if old_alloc.bed_id:
                    old_bed = HostelBed.objects.select_for_update().get(pk=old_alloc.bed_id)
                    old_bed.state = HostelBed.BedState.AVAILABLE
                    old_bed.save(update_fields=["state", "updated_at"])

            try:
                new_alloc = HostelAllocation.objects.create(
                    student=t.student,
                    bed=new_bed,
                    room=new_bed.room,
                    session=session,
                    status=HostelAllocation.Status.RESERVED,
                    check_in=timezone.localdate(),
                    allocated_by=actor,
                    allocated_at=timezone.now(),
                    reserved_at=timezone.now(),
                    reservation_expires_at=timezone.now() + HostelPolicyService.reservation_expiry(),
                    notes="Created by transfer #%s" % t.pk,
                )
            except IntegrityError:
                raise BedUnavailableError(
                    _("The new bed was just assigned to another student.")
                )
            new_bed.state = HostelBed.BedState.RESERVED
            new_bed.save(update_fields=["state", "updated_at"])

            t.new_allocation = new_alloc
            t.status = HostelTransfer.Status.COMPLETED
            t.completed_by = actor
            t.completed_at = timezone.now()
            t.save()

            HostelNotificationService.notify(
                t.student,
                "Transfer complete",
                f"Your hostel transfer to {new_bed} has been completed.",
                reverse_url("hostel:hostel"),
            )
            HostelAuditService.record(
                actor, AuditAction.UPDATE, "HostelTransfer", t,
                changes={
                    "old_allocation": str(old_alloc.pk) if old_alloc else None,
                    "new_allocation": str(new_alloc.pk),
                    "old_bed": str(t.old_bed),
                    "new_bed": str(new_bed),
                },
            )
        return new_alloc

    @classmethod
    def cancel_transfer(cls, transfer, actor=None, note=""):
        with transaction.atomic():
            t = HostelTransfer.objects.select_for_update().get(pk=transfer.pk)
            if t.status not in (HostelTransfer.Status.PENDING,
                                HostelTransfer.Status.APPROVED):
                raise HostelServiceError(_("This transfer can no longer be cancelled."))
            t.status = HostelTransfer.Status.CANCELLED
            t.review_note = note or t.review_note
            t.save()
            HostelAuditService.record(
                actor, AuditAction.UPDATE, "HostelTransfer", t,
                changes={"status": HostelTransfer.Status.CANCELLED},
            )
        return t