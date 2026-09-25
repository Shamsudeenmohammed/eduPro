"""Bed maintenance service."""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditAction
from hostel.models import BedMaintenance, HostelBed

from .allocation import HostelAllocationService
from .base import BedUnavailableError, HostelAuditService, HostelServiceError


class BedMaintenanceService:

    @classmethod
    def _sync_bed_state(cls, bed_id, *, ongoing=False):
        bed = HostelBed.objects.select_for_update().get(pk=bed_id)
        has_ongoing = BedMaintenance.objects.filter(
            bed_id=bed_id, status=BedMaintenance.Status.ONGOING
        ).exists()
        if ongoing or has_ongoing:
            if bed.state in (HostelBed.BedState.OCCUPIED, HostelBed.BedState.RESERVED):
                raise BedUnavailableError(
                    _("This bed is occupied/reserved and cannot be taken into maintenance.")
                )
            if bed.state != HostelBed.BedState.MAINTENANCE:
                bed.state = HostelBed.BedState.MAINTENANCE
                bed.save(update_fields=["state", "updated_at"])
            return

        if bed.state == HostelBed.BedState.MAINTENANCE:
            bed.state = HostelBed.BedState.AVAILABLE
            bed.save(update_fields=["state", "updated_at"])

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
            cls._sync_bed_state(record.bed_id)
            HostelAuditService.record(
                completed_by, AuditAction.UPDATE, "BedMaintenance", record,
                changes={"status": BedMaintenance.Status.COMPLETED},
            )
        return record

    @classmethod
    def update_status(cls, record, status, *, actor, notes=""):
        if status not in BedMaintenance.Status.values:
            raise HostelServiceError(_("Invalid maintenance status."))

        with transaction.atomic():
            record = BedMaintenance.objects.select_for_update().get(pk=record.pk)
            previous_status = record.status
            record.status = status
            if status == BedMaintenance.Status.COMPLETED:
                record.ended_at = timezone.now()
                record.completed_by = actor
            else:
                record.ended_at = None
                record.completed_by = None
            if notes:
                record.notes = (record.notes + ("\n" + notes) if record.notes else notes).strip()
            record.save()
            cls._sync_bed_state(
                record.bed_id, ongoing=status == BedMaintenance.Status.ONGOING
            )
            HostelAuditService.record(
                actor, AuditAction.UPDATE, "BedMaintenance", record,
                changes={"status": {"from": previous_status, "to": status}},
            )
        return record

    @classmethod
    def delete_maintenance(cls, record, *, actor):
        with transaction.atomic():
            record = BedMaintenance.objects.select_for_update().get(pk=record.pk)
            object_id = record.pk
            bed_id = record.bed_id
            object_repr = str(record)
            bed_repr = str(record.bed)
            record.delete()
            cls._sync_bed_state(bed_id)
            HostelAuditService.record(
                actor, AuditAction.DELETE, "BedMaintenance",
                object_id=object_id, object_repr=object_repr,
                changes={"bed": bed_repr},
            )
        return object_id
