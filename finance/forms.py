from django import forms
from .models import FeePayment, FeeStructure, PayrollRecord, StudentFee


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
