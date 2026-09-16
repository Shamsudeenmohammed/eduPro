"""Student hostel application model."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import TimeStampedModel
from .core import HostelRoom


class HostelApplication(TimeStampedModel):
    """
    A student's request for hostel accommodation.

    The application is deliberately separate from the allocation: approval of
    an application does NOT place a student in a room. Allocation is a
    subsequent, officer-driven operation.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Submitted — Pending Review")
        UNDER_REVIEW = "under_review", _("Under Review")
        APPROVED = "approved", _("Approved — Payment/Allocation Required")
        REJECTED = "rejected", _("Rejected")
        CONFIRMED = "confirmed", _("Confirmed — Allocated")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="hostel_applications", limit_choices_to={"role": "student"},
        verbose_name=_("student"),
    )
    room = models.ForeignKey(
        HostelRoom, on_delete=models.CASCADE, related_name="applications",
        verbose_name=_("preferred room"),
    )
    session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.PROTECT,
        null=True, blank=True, related_name="hostel_applications",
        verbose_name=_("academic session"),
    )
    status = models.CharField(
        _("status"), max_length=15, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )
    admin_remark = models.TextField(_("admin remark"), blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_reviews",
        verbose_name=_("reviewed by"),
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    payment_verified_at = models.DateTimeField(_("payment verified at"), null=True, blank=True)
    allocation = models.OneToOneField(
        "HostelAllocation", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="application",
        verbose_name=_("allocation"),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "session"]),
            models.Index(fields=["student", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.student.get_full_name()} → {self.room} ({self.get_status_display()})"