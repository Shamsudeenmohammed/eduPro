"""Hostel allocation service — transaction-safe bed allocation and lifecycle."""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditAction
from hostel.models import HostelAllocation, HostelApplication, HostelBed

from .base import (
    BedUnavailableError,
    HostelAuditService,
    HostelServiceError,
    StudentConflictError,
)
from .eligibility import HostelEligibilityService

logger = logging.getLogger("eduPro")


class HostelAllocationService:

    @classmethod
    def _lock_bed(cls, bed):
        pk = bed.pk if hasattr(bed, "pk") else bed
        return (
            HostelBed.objects.select_for_update()
            .select_related("room__floor__block", "room__hostel")
            .get(pk=pk)
        )

    @classmethod
    def _lock_allocation(cls, allocation):
        return (
            HostelAllocation.objects.select_for_update()
            .select_related("bed", "bed__room", "student", "session")
            .get(pk=allocation.pk)
        )

    @classmethod
    def allocate_bed(cls, *, student, bed, session, actor,
                     application=None, note="", reservation_expires_at=None):
        """
        Transaction-safe bed allocation.

        1. Lock the bed row.
        2. Re-check the bed is allocatable.
        3. Re-check the student has no live allocation for this session.
        4. Re-check the bed has no live allocation for this session.
        5. Create the allocation (RESERVED until the student checks in).
        6. Move the bed to RESERVED.
        7. Link/confirm the originating application.
        """
        if session is None:
            raise HostelServiceError(_("No academic session is currently set."))

        try:
            with transaction.atomic():
                bed = cls._lock_bed(bed)

                if bed.state not in (HostelBed.BedState.AVAILABLE,):
                    raise BedUnavailableError(
                        _("The selected bed is no longer available. Please choose another bed.")
                    )

                if HostelAllocation.objects.filter(
                    student=student, session=session,
                    status__in=HostelEligibilityService.LIVE_ALLOCATION_STATUSES,
                ).exists():
                    raise StudentConflictError(
                        _("Student already has an active hostel allocation for this academic period.")
                    )

                if HostelAllocation.objects.filter(
                    bed=bed, session=session,
                    status__in=HostelEligibilityService.LIVE_ALLOCATION_STATUSES,
                ).exists():
                    raise BedUnavailableError(
                        _("The selected bed has just been assigned to another student. "
                          "Please choose another bed.")
                    )

                allocation = HostelAllocation.objects.create(
                    student=student,
                    bed=bed,
                    room=bed.room,
                    session=session,
                    status=HostelAllocation.Status.RESERVED,
                    check_in=timezone.localdate(),
                    allocated_by=actor,
                    allocated_at=timezone.now(),
                    reserved_at=timezone.now(),
                    reservation_expires_at=reservation_expires_at,
                    notes=note,
                )

                bed.state = HostelBed.BedState.RESERVED
                bed.save(update_fields=["state", "updated_at"])

                if application:
                    application.allocation = allocation
                    application.status = HostelApplication.Status.CONFIRMED
                    application.reviewed_by = actor
                    application.reviewed_at = timezone.now()
                    application.save()

                HostelAuditService.record(
                    actor, AuditAction.CREATE, "HostelAllocation", allocation,
                    changes={
                        "bed": str(bed),
                        "session": str(session),
                        "application": str(application.pk) if application else None,
                    },
                )
                logger.info("Hostel allocation %s created by %s", allocation.pk, actor)
        except IntegrityError:
            # DB-constrained race (same student or same bed for this session) —
            # lost to a concurrent request. Surface as a user-facing conflict.
            raise StudentConflictError(
                _("Allocation conflict — the student or bed was just assigned. "
                  "Please choose another.")
            )
        return allocation

    @classmethod
    def hold_bed(cls, *, bed, actor, note=""):
        with transaction.atomic():
            bed = cls._lock_bed(bed)
            if bed.state != HostelBed.BedState.AVAILABLE:
                raise BedUnavailableError(_("Only available beds can be held."))
            bed.state = HostelBed.BedState.HELD
            bed.notes = note or bed.notes
            bed.save(update_fields=["state", "notes", "updated_at"])
            HostelAuditService.record(
                actor, AuditAction.UPDATE, "HostelBed", bed,
                changes={"state": HostelBed.BedState.HELD},
            )

    @classmethod
    def release_hold(cls, *, bed, actor, note=""):
        with transaction.atomic():
            bed = cls._lock_bed(bed)
            if bed.state != HostelBed.BedState.HELD:
                raise HostelServiceError(_("This bed is not currently held."))
            bed.state = HostelBed.BedState.AVAILABLE
            bed.notes = ""
            bed.save(update_fields=["state", "notes", "updated_at"])
            HostelAuditService.record(
                actor, AuditAction.UPDATE, "HostelBed", bed,
                changes={"state": HostelBed.BedState.AVAILABLE},
            )

    @classmethod
    def set_bed_blocked(cls, *, bed, actor, blocked=True, note=""):
        """Administratively block/unblock a bed. Blocked beds are never allocatable."""
        with transaction.atomic():
            bed = cls._lock_bed(bed)
            if blocked:
                if bed.state not in (
                    HostelBed.BedState.AVAILABLE,
                    HostelBed.BedState.HELD,
                    HostelBed.BedState.MAINTENANCE,
                ):
                    raise BedUnavailableError(
                        _("Only unoccupied beds can be blocked.")
                    )
                bed.state = HostelBed.BedState.BLOCKED
                bed.notes = note or bed.notes
            else:
                if bed.state != HostelBed.BedState.BLOCKED:
                    raise HostelServiceError(_("This bed is not blocked."))
                bed.state = HostelBed.BedState.AVAILABLE
                bed.notes = ""
            bed.save(update_fields=["state", "notes", "updated_at"])
            HostelAuditService.record(
                actor, AuditAction.UPDATE, "HostelBed", bed,
                changes={"state": bed.state, "blocked": blocked},
            )

    @classmethod
    def cancel_allocation(cls, allocation, actor=None, reason="", application=None):
        with transaction.atomic():
            alloc = cls._lock_allocation(allocation)
            if alloc.status in (HostelAllocation.Status.CANCELLED,
                                HostelAllocation.Status.EXPIRED,
                                HostelAllocation.Status.CHECKED_OUT):
                raise HostelServiceError(_("This allocation has already been closed."))

            alloc.status = HostelAllocation.Status.CANCELLED
            alloc.cancelled_by = actor
            alloc.cancelled_at = timezone.now()
            alloc.cancel_reason = reason
            alloc.check_out = timezone.localdate()
            alloc.save()

            if alloc.bed_id:
                bed = HostelBed.objects.select_for_update().get(pk=alloc.bed_id)
                if bed.state in (HostelBed.BedState.RESERVED, HostelBed.BedState.OCCUPIED):
                    bed.state = HostelBed.BedState.AVAILABLE
                    bed.save(update_fields=["state", "updated_at"])

            if application is None and hasattr(alloc, "application"):
                application = alloc.application
            if application:
                application.status = HostelApplication.Status.CANCELLED
                application.save()

            HostelAuditService.record(
                actor, AuditAction.UPDATE, "HostelAllocation", alloc,
                changes={"status": HostelAllocation.Status.CANCELLED, "reason": reason},
            )
        return alloc

    @classmethod
    def expire_reservation(cls, allocation):
        """Expire a single reservation; bed is released. Idempotent."""
        with transaction.atomic():
            alloc = cls._lock_allocation(allocation)
            return cls._expire_reservation_locked(alloc)

    @classmethod
    def _expire_reservation_locked(cls, alloc):
        if alloc.status != HostelAllocation.Status.RESERVED:
            return None
        alloc.status = HostelAllocation.Status.EXPIRED
        alloc.check_out = timezone.localdate()
        alloc.save()
        if alloc.bed_id:
            bed = HostelBed.objects.select_for_update().get(pk=alloc.bed_id)
            if bed.state == HostelBed.BedState.RESERVED:
                bed.state = HostelBed.BedState.AVAILABLE
                bed.save(update_fields=["state", "updated_at"])
        HostelAuditService.record(
            None, AuditAction.UPDATE, "HostelAllocation", alloc,
            changes={"status": HostelAllocation.Status.EXPIRED},
        )
        return alloc

    @classmethod
    def expire_due_reservations(cls):
        """
        Expire every reservation past its ``reservation_expires_at``.
        Safe to call repeatedly (idempotent); designed for a management
        command or scheduler.
        """
        now = timezone.now()
        due = HostelAllocation.objects.filter(
            status=HostelAllocation.Status.RESERVED,
            reservation_expires_at__isnull=False,
            reservation_expires_at__lt=now,
        ).order_by("pk").values_list("pk", flat=True)
        expired = []
        for pk in due:
            try:
                cls.expire_reservation(HostelAllocation(pk=pk))
                expired.append(pk)
            except Exception as exc:  # noqa: BLE001 — keep the sweep running
                logger.exception("Failed to expire reservation %s: %s", pk, exc)
        return expired