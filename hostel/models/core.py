"""Hostel hierarchy + bed + maintenance.

Hierarchy:  Hostel → HostelBlock → HostelFloor → HostelRoom → HostelBed

The Bed is the authoritative allocatable unit. Room/hostel occupancy is
derived from the states of the beds they contain (never from a manual
`occupied_count` column).
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .base import TimeStampedModel


class Amenity(TimeStampedModel):
    """Configurable facility/inclusion (Wi‑Fi, aircon, en‑suite, etc.)."""

    name = models.CharField(_("name"), max_length=100, unique=True)
    description = models.TextField(_("description"), blank=True)
    icon = models.CharField(
        _("icon"), max_length=50, blank=True,
        help_text=_("Optional icon reference or emoji, e.g. 🧼"),
    )
    is_active = models.BooleanField(_("active"), default=True)
    display_order = models.PositiveSmallIntegerField(_("display order"), default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = _("amenity")
        verbose_name_plural = _("amenities")

    def __str__(self):
        return self.name


class Hostel(TimeStampedModel):
    name = models.CharField(_("name"), max_length=100)
    location = models.CharField(_("location"), max_length=200, blank=True)
    description = models.TextField(_("description"), blank=True)
    amenities = models.ManyToManyField(
        Amenity, blank=True, related_name="hostels",
        verbose_name=_("amenities"),
    )
    # Retained for backwards compatibility / quick reference. Authoritative
    # capacity is derived from HostelBed records.
    capacity = models.PositiveIntegerField(_("legacy capacity"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def amenity_list(self):
        return self.amenities.all().order_by("display_order", "name")

    @property
    def total_blocks(self):
        return self.blocks.count()

    @property
    def total_rooms(self):
        return self.rooms.count()

    @property
    def total_beds(self):
        return HostelBed.objects.filter(room__hostel=self).count()

    @property
    def available_beds(self):
        return HostelBed.objects.filter(
            room__hostel=self,
            state=HostelBed.BedState.AVAILABLE,
        ).count()


class HostelBlock(TimeStampedModel):
    """A named block within a hostel (e.g. "Block A")."""
    hostel = models.ForeignKey(
        Hostel, on_delete=models.CASCADE, related_name="blocks",
        verbose_name=_("hostel"),
    )
    name = models.CharField(_("name"), max_length=100)
    description = models.TextField(_("description"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["hostel", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["hostel", "name"],
                name="uniq_hostel_block",
            ),
        ]

    def __str__(self):
        return f"{self.hostel.name} — {self.name}"


class HostelFloor(TimeStampedModel):
    """A floor within a block (e.g. "First Floor")."""
    block = models.ForeignKey(
        HostelBlock, on_delete=models.CASCADE, related_name="floors",
        verbose_name=_("block"),
    )
    name = models.CharField(_("name"), max_length=100)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["block", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["block", "name"],
                name="uniq_block_floor",
            ),
        ]

    @property
    def hostel(self):
        return self.block.hostel

    def __str__(self):
        return f"{self.block} — {self.name}"


class HostelRoom(TimeStampedModel):
    """A room inside the hostel. Hostel FK retained for backwards
    compatibility and fast lookup; the structured hierarchy is via `floor`."""
    hostel = models.ForeignKey(
        Hostel, on_delete=models.CASCADE, related_name="rooms",
        verbose_name=_("hostel"),
    )
    floor = models.ForeignKey(
        HostelFloor, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rooms", verbose_name=_("floor"),
    )
    room_number = models.CharField(_("room number"), max_length=20)
    description = models.TextField(_("description"), blank=True)
    amenities = models.ManyToManyField(
        Amenity, blank=True, related_name="rooms",
        verbose_name=_("amenities"),
    )
    # Legacy convenience capacity. Authoritative occupancy comes from beds.
    capacity = models.PositiveSmallIntegerField(_("legacy capacity"), default=2)
    is_available = models.BooleanField(_("administratively available"), default=True)

    class Meta:
        ordering = ["hostel", "room_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["hostel", "room_number"],
                name="uniq_hostel_room",
            ),
        ]

    def __str__(self):
        return f"{self.hostel.name} — Room {self.room_number}"

    @property
    def block(self):
        return self.floor.block if self.floor else None

    @property
    def occupied_beds(self):
        return self.beds.filter(state=HostelBed.BedState.OCCUPIED).count()

    @property
    def available_beds(self):
        return self.beds.filter(state=HostelBed.BedState.AVAILABLE).count()

    @property
    def is_full(self):
        return not self.beds.filter(
            state__in=(
                HostelBed.BedState.AVAILABLE,
                HostelBed.BedState.HELD,
                HostelBed.BedState.RESERVED,
            )
        ).exists()

    @property
    def effective_amenities(self):
        """Room amenities plus the hostel-level amenities they inherit."""
        combined = {}
        for a in self.hostel.amenities.all():
            combined[a.pk] = a
        for a in self.amenities.all():
            combined[a.pk] = a
        return sorted(combined.values(), key=lambda a: (a.display_order, a.name))


class HostelBed(TimeStampedModel):
    """The actual allocatable unit."""

    class BedState(models.TextChoices):
        AVAILABLE = "available", _("Available")
        HELD = "held", _("Held")
        RESERVED = "reserved", _("Reserved")
        OCCUPIED = "occupied", _("Occupied")
        MAINTENANCE = "maintenance", _("Maintenance")
        BLOCKED = "blocked", _("Blocked")

    room = models.ForeignKey(
        HostelRoom, on_delete=models.CASCADE, related_name="beds",
        verbose_name=_("room"),
    )
    bed_number = models.CharField(_("bed number"), max_length=20)
    label = models.CharField(_("label"), max_length=120, blank=True,
                             help_text=_("Human-friendly label, e.g. A203-03"))
    state = models.CharField(
        _("state"), max_length=15, choices=BedState.choices,
        default=BedState.AVAILABLE, db_index=True,
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        ordering = ["room", "bed_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "bed_number"],
                name="uniq_room_bed",
            ),
        ]

    def __str__(self):
        return self.label or f"{self.room.room_number}-{self.bed_number}"

    @property
    def hostel(self):
        return self.room.hostel

    @property
    def display_name(self):
        return self.label or f"{self.room.room_number}-{self.bed_number}"

    @property
    def is_allocatable(self):
        return self.state == HostelBed.BedState.AVAILABLE

    @property
    def is_occupiable(self):
        return self.state not in (HostelBed.BedState.MAINTENANCE, HostelBed.BedState.BLOCKED)


class BedMaintenance(TimeStampedModel):
    """
    A maintenance record for a bed. While an active (ongoing) record exists
    the bed is forced to MAINTENANCE state and cannot be allocated.
    """

    class Status(models.TextChoices):
        ONGOING = "ongoing", _("Ongoing")
        COMPLETED = "completed", _("Completed")

    bed = models.ForeignKey(
        HostelBed, on_delete=models.CASCADE, related_name="maintenance_records",
        verbose_name=_("bed"),
    )
    reason = models.TextField(_("reason"))
    status = models.CharField(
        _("status"), max_length=15, choices=Status.choices,
        default=Status.ONGOING, db_index=True,
    )
    started_at = models.DateTimeField(_("started at"), default=timezone.now)
    ended_at = models.DateTimeField(_("ended at"), null=True, blank=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="bed_maintenance_reported",
        verbose_name=_("reported by"),
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="bed_maintenance_completed",
        verbose_name=_("completed by"),
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.bed} — {self.get_status_display()}"