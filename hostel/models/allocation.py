"""Allocation and transfer models."""

from datetime import date, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .base import TimeStampedModel
from .core import HostelBed, HostelRoom


class HostelAllocation(TimeStampedModel):
    """
    The actual assignment of a student to a bed for an academic session.

    Lifecycle:
        RESERVED → (check-in) → ACTIVE → (check-out/cancel) → CHECKED_OUT / CANCELLED
        RESERVED → (expiry) → EXPIRED
    """

    class Status(models.TextChoices):
        RESERVED = "reserved", _("Reserved")
        ACTIVE = "active", _("Active")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")
        CHECKED_OUT = "checked_out", _("Checked Out")

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="hostel_allocations", limit_choices_to={"role": "student"},
        verbose_name=_("student"),
    )
    bed = models.ForeignKey(
        HostelBed, on_delete=models.PROTECT, null=True, blank=True,
        related_name="allocations", verbose_name=_("bed"),
    )
    # Retained for backwards compatibility / historical records; derived from
    # `bed.room` for new allocations.
    room = models.ForeignKey(
        HostelRoom, on_delete=models.CASCADE, related_name="allocations",
        verbose_name=_("room"),
    )
    session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.PROTECT,
        null=True, blank=True, related_name="hostel_allocations",
        verbose_name=_("academic session"),
    )
    status = models.CharField(
        _("status"), max_length=15, choices=Status.choices,
        default=Status.RESERVED, db_index=True,
    )
    is_active = models.BooleanField(_("active"), default=True)

    # ── Dates ────────────────────────────────────────────────────────────
    check_in = models.DateField(_("start date"), null=True, blank=True)
    check_out = models.DateField(_("end date"), null=True, blank=True)
    expires_at = models.DateField(_("allocation expires"), null=True, blank=True)
    reserved_at = models.DateTimeField(_("reserved at"), default=timezone.now)
    reservation_expires_at = models.DateTimeField(_("reservation expires at"), null=True, blank=True)

    # ── Audit trail ──────────────────────────────────────────────────────
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_allocations_made",
        verbose_name=_("allocated by"),
    )
    allocated_at = models.DateTimeField(_("allocated at"), null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_check_ins",
        verbose_name=_("checked in by"),
    )
    checked_in_at = models.DateTimeField(_("checked in at"), null=True, blank=True)
    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_check_outs",
        verbose_name=_("checked out by"),
    )
    checked_out_at = models.DateTimeField(_("checked out at"), null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_allocations_cancelled",
        verbose_name=_("cancelled by"),
    )
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancel_reason = models.TextField(_("cancel reason"), blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # A student may not hold two live allocations for the same period.
            models.UniqueConstraint(
                fields=["student", "session"],
                condition=models.Q(status__in=("reserved", "active")),
                name="uniq_active_student_allocation_per_session",
            ),
            # A bed may not have two live allocations for the same period.
            models.UniqueConstraint(
                fields=["bed", "session"],
                condition=models.Q(status__in=("reserved", "active")) & ~models.Q(bed=None),
                name="uniq_active_bed_allocation_per_session",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(check_out__isnull=True)
                    | models.Q(check_in__isnull=True)
                    | models.Q(check_out__gte=models.F("check_in"))
                ),
                name="chk_allocation_checkout_after_checkin",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "status", "session"]),
            models.Index(fields=["bed", "status"]),
            models.Index(fields=["session", "status"]),
            models.Index(fields=["reservation_expires_at"]),
        ]

    def save(self, *args, **kwargs):
        # Derive room from bed when a bed is chosen (keeps one room source of truth).
        if self.bed_id and not self.room_id:
            self.room = self.bed.room
        # Legacy convenience: mirror boolean reflects the live statuses.
        self.is_active = self.status in (self.Status.RESERVED, self.Status.ACTIVE)
        if not self.expires_at and self.check_in:
            self.expires_at = self.check_in + timedelta(days=365)
        super().save(*args, **kwargs)

    def __str__(self):
        bed_name = str(self.bed) if self.bed_id else str(self.room)
        return f"{self.student.get_full_name()} → {bed_name} ({self.get_status_display()})"

    @property
    def is_expired(self):
        return date.today() > self.expires_at if self.expires_at else False

    @property
    def days_left(self):
        if not self.expires_at:
            return 0
        delta = (self.expires_at - date.today()).days
        return max(delta, 0)

    @property
    def hierarchy_display(self):
        """Human-readable 'Hostel → Block → Floor → Room → Bed' string."""
        room = self.bed.room if self.bed_id else self.room
        parts = [room.hostel.name]
        if room.floor:
            parts.append(room.floor.block.name)
            parts.append(room.floor.name)
        parts.append(room.room_number)
        if self.bed_id:
            parts.append(str(self.bed))
        return " › ".join(parts)


class HostelTransfer(TimeStampedModel):
    """
    A controlled room/bed transfer. The previous allocation is closed and a
    new allocation is created; the original allocation stays historically
    traceable via `old_allocation`.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        CANCELLED = "cancelled", _("Cancelled")
        COMPLETED = "completed", _("Completed")

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="hostel_transfers", limit_choices_to={"role": "student"},
        verbose_name=_("student"),
    )
    old_allocation = models.ForeignKey(
        HostelAllocation, on_delete=models.PROTECT,
        null=True, blank=True, related_name="transfers_out",
        verbose_name=_("old allocation"),
    )
    old_bed = models.ForeignKey(
        HostelBed, on_delete=models.PROTECT,
        null=True, blank=True, related_name="transfers_from",
        verbose_name=_("old bed"),
    )
    new_bed = models.ForeignKey(
        HostelBed, on_delete=models.PROTECT,
        related_name="transfers_to", verbose_name=_("new bed"),
    )
    new_allocation = models.ForeignKey(
        HostelAllocation, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_by_transfer",
        verbose_name=_("new allocation"),
    )
    reason = models.TextField(_("reason"), blank=True)
    status = models.CharField(
        _("status"), max_length=15, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )
    requested_at = models.DateTimeField(_("requested at"), default=timezone.now)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="transfers_requested",
        verbose_name=_("requested by"),
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="transfers_reviewed",
        verbose_name=_("reviewed by"),
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    review_note = models.TextField(_("review note"), blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="transfers_completed",
        verbose_name=_("completed by"),
    )
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        old = str(self.old_bed) if self.old_bed_id else "—"
        return f"{self.student.get_full_name()}: {old} → {self.new_bed} ({self.get_status_display()})"