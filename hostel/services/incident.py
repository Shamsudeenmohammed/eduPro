"""Hostel incident service."""

from django.utils import timezone

from core.models import AuditAction
from hostel.models import HostelIncident, IncidentStatus

from .base import HostelAuditService


class HostelIncidentService:

    @staticmethod
    def report(student, *, category, description, reported_by,
               room=None, bed=None, allocation=None):
        incident = HostelIncident.objects.create(
            student=student,
            room=room or (bed.room if bed else None),
            bed=bed,
            allocation=allocation,
            category=category,
            description=description,
            reported_by=reported_by,
        )
        HostelAuditService.record(
            reported_by, AuditAction.CREATE, "HostelIncident", incident,
            changes={"category": category},
        )
        return incident

    @staticmethod
    def resolve(incident, resolved_by, resolution, status=IncidentStatus.RESOLVED):
        incident.status = status
        incident.resolution = resolution
        incident.resolved_by = resolved_by
        incident.resolved_at = timezone.now()
        incident.save()
        HostelAuditService.record(
            resolved_by, AuditAction.UPDATE, "HostelIncident", incident,
            changes={"status": status},
        )
        return incident