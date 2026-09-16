"""Hostel policy and fee-configuration models."""

from django.db import models
from django.utils.translation import gettext_lazy as _

from .base import TimeStampedModel
from .core import Hostel, HostelRoom


class HostelPolicy(TimeStampedModel):
    """
    Global policy layer. A single active policy governs business rules so they
    are not hard-coded across views/services.

    All rules are optional windows; a `None` value means the rule is disabled.
    """

    # ── Windows ──────────────────────────────────────────────────────────
    application_open = models.DateField(_("application opens"), null=True, blank=True)
    application_close = models.DateField(_("application closes"), null=True, blank=True)
    allocation_open = models.DateField(_("allocation opens"), null=True, blank=True)
    allocation_close = models.DateField(_("allocation closes"), null=True, blank=True)

    # ── Reservations ─────────────────────────────────────────────────────
    reservation_expiry_hours = models.PositiveSmallIntegerField(
        _("reservation expiry hours"), default=48,
        help_text=_("Hours before an un-checked-in reservation expires."),
    )

    # ── Finance / check-in ───────────────────────────────────────────────
    require_payment_before_checkin = models.BooleanField(
        _("require payment before check-in"), default=True,
    )
    allow_partial_payment = models.BooleanField(
        _("allow partial payment before check-in"), default=True,
    )
    enable_hostel_charges = models.BooleanField(
        _("enable hostel charges"), default=False,
        help_text=_("When enabled, allocations create StudentFee records in finance."),
    )

    # ── Transfers ────────────────────────────────────────────────────────
    allow_transfers = models.BooleanField(_("allow room transfers"), default=True)
    allow_hostel_transfers = models.BooleanField(_("allow hostel transfers"), default=True)

    # ── Eligibility ──────────────────────────────────────────────────────
    require_active_student = models.BooleanField(_("require active student"), default=True)

    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("hostel policy")
        verbose_name_plural = _("hostel policies")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Hostel Policy (reservations {self.reservation_expiry_hours}h)"

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).order_by("-created_at").first()


class HostelFeeConfig(TimeStampedModel):
    """
    Configuration linking a hostel + academic session to a finance charge.

    The amount/payment truth lives in `finance` (FeeStructure / StudentFee /
    FeePayment). This record only defines *how soon/what* the hostel should
    ask finance to charge.
    """

    hostel = models.ForeignKey(
        Hostel, on_delete=models.CASCADE, related_name="fee_configs",
        verbose_name=_("hostel"),
    )
    session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.PROTECT,
        related_name="hostel_fee_configs", verbose_name=_("academic session"),
    )
    semester = models.ForeignKey(
        "academics.Semester", on_delete=models.PROTECT,
        null=True, blank=True, related_name="hostel_fee_configs",
        verbose_name=_("semester"),
        help_text=_(
            "Optional. Leave empty to apply to any semester (fallback); "
            "set it to charge per semester, e.g. 2026/2027 First Semester."
        ),
    )
    room = models.ForeignKey(
        HostelRoom, on_delete=models.CASCADE,
        null=True, blank=True, related_name="fee_configs",
        verbose_name=_("room override"),
        help_text=_(
            "Optional. Leave empty for a hostel-wide fee; set it to override "
            "the fee for a specific room (e.g. en‑suite surcharge)."
        ),
    )
    fee_structure = models.ForeignKey(
        "finance.FeeStructure", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
        verbose_name=_("fee structure"),
        help_text=_("Finance fee structure used to generate StudentFee records."),
    )
    amount = models.DecimalField(
        _("amount payable"), max_digits=12, decimal_places=2,
        null=True, blank=True,
        help_text=_("Overrides the fee structure amount when set."),
    )
    due_date = models.DateField(_("due date"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["-session__start_date", "hostel"]
        verbose_name = _("hostel fee config")
        verbose_name_plural = _("hostel fee configs")
        constraints = [
            models.UniqueConstraint(
                fields=["hostel", "session", "semester", "room"],
                name="uniq_hostel_fee_config_scope",
                nulls_distinct=False,
            ),
        ]

    def __str__(self):
        bits = [self.hostel.name, self.session.name]
        if self.semester:
            bits.append(self.semester.get_name_display())
        if self.room:
            bits.append(f"Room {self.room.room_number} override")
        return " — ".join(bits)

    @property
    def scope_label(self):
        """Human summary of the config's scope (for list displays)."""
        if self.room:
            return f"Room {self.room.room_number}"
        if self.semester:
            return self.semester.get_name_display()
        return "All semesters"

    @property
    def effective_amount(self):
        return self.amount if self.amount is not None else (
            self.fee_structure.amount if self.fee_structure else self.amount
        )