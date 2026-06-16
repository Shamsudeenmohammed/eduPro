"""Finance — school fees and payroll basics."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class FeeStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PARTIAL = "partial", _("Partially Paid")
    PAID = "paid", _("Paid")
    OVERDUE = "overdue", _("Overdue")
    WAIVED = "waived", _("Waived")


class FeeStructure(TimeStampedModel):
    """Fee template per program/session."""
    name = models.CharField(max_length=100)
    program = models.ForeignKey(
        "academics.Program", on_delete=models.CASCADE,
        related_name="fee_structures", null=True, blank=True,
    )
    session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.CASCADE,
        related_name="fee_structures",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} — {self.amount}"


class StudentFee(TimeStampedModel):
    """Fee assigned to a student."""
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="fees", limit_choices_to={"role": "student"},
    )
    fee_structure = models.ForeignKey(FeeStructure, on_delete=models.PROTECT, related_name="student_fees")
    amount_due = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=FeeStatus.choices, default=FeeStatus.PENDING)
    due_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student.get_full_name()} — {self.fee_structure.name}"

    @property
    def balance(self):
        return self.amount_due - self.amount_paid


class FeePayment(TimeStampedModel):
    student_fee = models.ForeignKey(StudentFee, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=50, default="cash")
    reference = models.CharField(max_length=100, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.amount} — {self.student_fee}"


class PayrollStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PROCESSED = "processed", _("Processed")
    PAID = "paid", _("Paid")


class PayrollRecord(TimeStampedModel):
    """Basic payroll for teachers/staff."""
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payroll_records",
    )
    period_month = models.PositiveSmallIntegerField()
    period_year = models.PositiveSmallIntegerField()
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2)
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=PayrollStatus.choices, default=PayrollStatus.DRAFT)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="payroll_processed",
    )

    class Meta:
        unique_together = [("employee", "period_month", "period_year")]
        ordering = ["-period_year", "-period_month"]

    def __str__(self):
        return f"{self.employee.get_full_name()} — {self.period_month}/{self.period_year}"

    def save(self, *args, **kwargs):
        self.net_pay = self.basic_salary + self.allowances - self.deductions
        super().save(*args, **kwargs)
