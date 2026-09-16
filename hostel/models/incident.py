"""Hostel incident model."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import TimeStampedModel
from .allocation import HostelAllocation
from .core import HostelBed, HostelRoom


class IncidentCategory(models.TextChoices):
    ROOM_DAMAGE = "room_damage", _("Room Damage")
    MISCONDUCT = "misconduct", _("Misconduct")
    LOST_KEY = "lost_key", _("Lost Key")
    UNAUTHORIZED_OCCUPANCY = "unauthorized_occupancy", _("Unauthorized Occupancy")
    DISCIPLINARY = "disciplinary", _("Disciplinary Incident")
    MAINTENANCE_COMPLAINT = "maintenance_complaint", _("Maintenance Complaint")
    OTHER = "other", _("Other")


class IncidentStatus(models.TextChoices):
    OPEN = "open", _("Open")
    INVESTIGATING = "investigating", _("Investigating")
    RESOLVED = "resolved", _("Resolved")
    CLOSED = "closed", _("Closed")


class HostelIncident(TimeStampedModel):
    """A structured resident incident record (staff-only visibility)."""
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_incidents",
        verbose_name=_("student"),
    )
    room = models.ForeignKey(
        HostelRoom, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="incidents", verbose_name=_("room"),
    )
    bed = models.ForeignKey(
        HostelBed, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="incidents", verbose_name=_("bed"),
    )
    allocation = models.ForeignKey(
        HostelAllocation, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="incidents", verbose_name=_("allocation"),
    )
    category = models.CharField(
        _("category"), max_length=30, choices=IncidentCategory.choices,
        default=IncidentCategory.OTHER,
    )
    description = models.TextField(_("description"))
    status = models.CharField(
        _("status"), max_length=15, choices=IncidentStatus.choices,
        default=IncidentStatus.OPEN, db_index=True,
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_incidents_reported",
        verbose_name=_("reported by"),
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_incidents_assigned",
        verbose_name=_("assigned to"),
    )
    resolution = models.TextField(_("resolution"), blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_incidents_resolved",
        verbose_name=_("resolved by"),
    )
    resolved_at = models.DateTimeField(_("resolved at"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("hostel incident")
        verbose_name_plural = _("hostel incidents")

    def __str__(self):
        who = self.student.get_full_name() if self.student_id else "Unknown"
        return f"{who} — {self.get_category_display()} ({self.get_status_display()})"