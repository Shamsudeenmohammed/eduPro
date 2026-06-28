from django import forms
from django.contrib.auth import get_user_model

from .models import FeePayment, FeeStructure, PayrollRecord, StudentFee

User = get_user_model()


class FeeStructureForm(forms.ModelForm):
    class Meta:
        model = FeeStructure
        fields = ["name", "program", "session", "amount", "due_date", "description"]
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}


class StudentFeeForm(forms.ModelForm):
    class Meta:
        model = StudentFee
        fields = ["student", "fee_structure", "amount_due", "due_date"]
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}


class FeePaymentForm(forms.ModelForm):
    class Meta:
        model = FeePayment
        fields = ["amount", "payment_method", "reference", "notes"]


class PayrollForm(forms.ModelForm):
    class Meta:
        model = PayrollRecord
        fields = [
            "employee", "period_month", "period_year",
            "basic_salary", "allowances", "deductions", "status",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = User.objects.filter(
            role__in=["admin", "teacher"]
        )
