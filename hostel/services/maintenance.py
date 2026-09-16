"""Bed maintenance service."""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditAction
from hostel.models import BedMaintenance, HostelBed

from .allocation import HostelAllocationService
from .base import BedUnavailableError, HostelAuditService


class BedMaintenanceService:

    @classmethod
    def start_maintenance(cls, *, bed, reason, reported_by, notes=""):
        with transaction.atomic():
            bed = HostelAllocationService._lock_bed(bed)
            if bed.state in (HostelBed.BedState.OCCUPIED, HostelBed.BedState.RESERVED):
                raise BedUnavailableError(
                    _("This bed is occupied/reserved and cannot be taken into maintenance.")
                )
            record = BedMaintenance.objects.create(
                bed=bed, reason=reason, reported_by=reported_by, notes=notes,
                status=BedMaintenance.Status.ONGOING,
            )
            bed.state = HostelBed.BedState.MAINTENANCE
            bed.save(update_fields=["state", "updated_at"])
            HostelAuditService.record(
                reported_by, AuditAction.CREATE, "BedMaintenance", record,
                changes={"bed": str(bed), "reason": reason},
            )
        return record

    @classmethod
    def complete_maintenance(cls, record, completed_by, notes=""):
        with transaction.atomic():
            record = BedMaintenance.objects.select_for_update().get(pk=record.pk)
            record.status = BedMaintenance.Status.COMPLETED
            record.ended_at = timezone.now()
            record.completed_by = completed_by
            record.notes = (record.notes + ("\n" + notes) if record.notes else notes).strip()
            record.save()
            # Only release the bed if no other ongoing maintenance exists.
            still_ongoing = BedMaintenance.objects.filter(
                bed=record.bed, status=BedMaintenance.Status.ONGOING
            ).exists()
            if not still_ongoing:
                bed = HostelBed.objects.select_for_update().get(pk=record.bed_id)
                if bed.state == HostelBed.BedState.MAINTENANCE:
                    bed.state = HostelBed.BedState.AVAILABLE
                    bed.save(update_fields=["state", "updated_at"])
            HostelAuditService.record(
                completed_by, AuditAction.UPDATE, "BedMaintenance", record,
                changes={"status": BedMaintenance.Status.COMPLETED},
            )
        return record