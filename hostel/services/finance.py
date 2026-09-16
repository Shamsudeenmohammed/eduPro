"""Hostel finance boundary — never owns a ledger, only requests / reads finance."""

from decimal import Decimal

from django.db import DatabaseError
from django.db.models import Sum

from hostel.models import HostelFeeConfig

from .base import HostelAuditService
from .policy import HostelPolicyService


class HostelFinanceService:
    """
    Integration boundary with ``finance``. Hostel NEVER keeps its own ledger:
    it requests a ``StudentFee`` and reads balances/states back from finance.
    """

    @staticmethod
    def _student_fees(student, session):
        from finance.models import StudentFee
        qs = HostelFeeConfig.objects.filter(session=session, is_active=True)
        fs_ids = list(qs.exclude(fee_structure=None).values_list("fee_structure_id", flat=True))
        fees = StudentFee.objects.filter(student=student)
        if fs_ids:
            fees = fees.filter(fee_structure_id__in=fs_ids)
        else:
            # Fallback: match by name prefix "Hostel —".
            fees = fees.filter(fee_structure__name__startswith="Hostel —")
        return fees

    @classmethod
    def fee_config_for(cls, hostel, session, semester=None, room=None):
        """
        Resolve the most specific active HostelFeeConfig for a stay context.

        Precedence (most → least specific):
          1. room override for the exact semester
          2. room override with no semester (hostel-wide fallback per room)
          3. hostel-wide fee for the exact semester
          4. hostel-wide fee with no semester (all-semester fallback)
          5. any remaining active config for this hostel + session

        Finance remains the source of truth for payment amounts; this helper
        only picks which configured amount the student should be charged.
        """
        try:
            return cls._fee_config_scope_resolved(hostel, session, semester, room)
        except DatabaseError:
            # The semester/room columns are part of the pending hostel
            # migration; fall back to the pre-scope lookup so payments still
            # work until the migration is applied.
            qs = HostelFeeConfig.objects.filter(
                session=session, is_active=True, hostel=hostel
            ).select_related("hostel", "session", "fee_structure")
            return qs.first()

    @classmethod
    def _fee_config_scope_resolved(cls, hostel, session, semester, room):
        base = HostelFeeConfig.objects.filter(
            session=session, is_active=True,
        ).select_related("hostel", "session", "semester", "fee_structure", "room")

        if room is not None:
            room_qs = base.filter(hostel=hostel, room=room)
            if semester is not None:
                match = room_qs.filter(semester=semester).first()
                if match:
                    return match
            match = room_qs.filter(semester__isnull=True).first()
            if match:
                return match
            match = room_qs.first()
            if match:
                return match

        hostel_qs = base.filter(hostel=hostel, room__isnull=True)
        if semester is not None:
            match = hostel_qs.filter(semester=semester).first()
            if match:
                return match
        match = hostel_qs.filter(semester__isnull=True).first()
        if match:
            return match
        return hostel_qs.first()

    @classmethod
    def fee_for(cls, hostel, session, semester=None, room=None):
        """The amount a student should be charged (Decimal) or ``None``."""
        config = cls.fee_config_for(hostel, session, semester=semester, room=room)
        if config is None:
            return None
        amount = config.effective_amount
        return amount if amount is not None else Decimal("0")

    @classmethod
    def ensure_charge(cls, *, student, session, hostel=None, room=None,
                      semester=None, actor=None):
        """
        Create/fetch the StudentFee record that finance will own. Returns the
        fee, or None when no matching fee config exists.

        NOTE: ``enable_hostel_charges`` intentionally does NOT gate this
        method. That policy flag controls whether payment is REQUIRED before
        check-in (see ``payment_satisfied``). A configured fee must always be
        payable whenever a student tries to pay, so a hostel fee config alone
        is enough to create the charge.
        """
        from finance.models import FeeStructure, StudentFee

        config = cls.fee_config_for(hostel, session, semester=semester, room=room)
        if config is None and hostel is not None:
            config = HostelFeeConfig.objects.filter(
                session=session, is_active=True
            ).select_related("hostel", "session", "semester", "fee_structure", "room").first()
        if not config:
            return None

        if config.fee_structure_id:
            fs = config.fee_structure
        else:
            name = f"Hostel — {config.hostel.name} — {session.name}"
            fs, _ = FeeStructure.objects.get_or_create(
                name=name,
                session=session,
                defaults={"amount": config.amount or Decimal("0"),
                          "description": "Hostel accommodation charge"},
            )
            config.fee_structure = fs
            config.save(update_fields=["fee_structure", "updated_at"])

        amount = config.amount if config.amount is not None else fs.amount
        fee, created = StudentFee.objects.get_or_create(
            student=student,
            fee_structure=fs,
            defaults={"amount_due": amount or Decimal("0"),
                      "due_date": config.due_date},
        )
        if created:
            from core.models import AuditAction
            HostelAuditService.record(
                actor, AuditAction.CREATE, "StudentFee", fee,
                changes={"amount_due": str(amount)},
            )
        return fee

    @classmethod
    def record_external_payment(cls, *, fee, amount, reference, method="paystack", actor=None):
        """
        Record an externally-paid amount (Paystack webhook/callback) against a
        StudentFee. Idempotent per reference. Updates amount_paid + status.
        """
        from finance.models import FeePayment, FeeStatus
        done = FeePayment.objects.filter(reference=reference).first()
        if done:
            return done
        payment = FeePayment.objects.create(
            student_fee=fee,
            amount=amount,
            payment_method=method,
            reference=reference,
            received_by=actor,
            notes=f"{method} payment (reference {reference})",
        )
        fee.amount_paid += amount
        if fee.amount_paid >= fee.amount_due:
            fee.status = FeeStatus.PAID
        elif fee.amount_paid > 0:
            fee.status = FeeStatus.PARTIAL
        fee.save()
        HostelAuditService.record(
            actor, "create", "FeePayment", payment,
            changes={"amount": str(amount), "reference": reference, "method": method},
        )
        return payment

    @classmethod
    def payment_satisfied(cls, student, session, policy=None):
        """
        The single financial check used before check-in.

        * No policy / finance disabled             → always OK
        * No hostel charge configured for session  → always OK
        * ``allow_partial_payment``                → ≥60% paid, no OVERDUE
        * otherwise                                → fully paid
        """
        policy = policy or HostelPolicyService.get_policy()
        if not (policy and policy.require_payment_before_checkin):
            return True
        if not (policy and policy.enable_hostel_charges):
            return True

        from finance.models import FeeStatus
        fees = cls._student_fees(student, session)
        counts = fees.aggregate(due=Sum("amount_due"), paid=Sum("amount_paid"))
        total_due = counts["due"] or Decimal("0")
        total_paid = counts["paid"] or Decimal("0")
        if total_due <= Decimal("0"):
            return True

        if not policy.allow_partial_payment:
            return total_paid >= total_due
        return (
            (total_paid / total_due) >= Decimal("0.6")
            and not fees.filter(status=FeeStatus.OVERDUE).exists()
        )

    @classmethod
    def balance_info(cls, student, session):
        """UI-friendly financial summary (all values from finance)."""
        from finance.models import FeeStatus
        fees = cls._student_fees(student, session)
        counts = fees.aggregate(due=Sum("amount_due"), paid=Sum("amount_paid"))
        total_due = counts["due"] or Decimal("0")
        total_paid = counts["paid"] or Decimal("0")

        status = "none"
        if fees.exists():
            if total_paid <= 0:
                status = "unpaid"
            elif total_paid >= total_due:
                status = "paid"
            elif fees.filter(status=FeeStatus.OVERDUE).exists():
                status = "overdue"
            else:
                status = "partial"

        return {
            "has_charge": fees.exists(),
            "amount_due": total_due,
            "amount_paid": total_paid,
            "balance": total_due - total_paid,
            "status": status,
        }