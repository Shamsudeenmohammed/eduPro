"""Hostel policy + fee-configuration forms."""

from django import forms

from academics.models import AcademicSession, Semester
from hostel.models import HostelRoom, HostelPolicy, HostelFeeConfig

from .base import Widgets


class HostelPolicyForm(forms.ModelForm):
    class Meta:
        model = HostelPolicy
        fields = [
            "application_open", "application_close",
            "allocation_open", "allocation_close",
            "reservation_expiry_hours",
            "require_payment_before_checkin", "allow_partial_payment",
            "enable_hostel_charges",
            "allow_transfers", "allow_hostel_transfers",
            "require_active_student", "is_active",
        ]
        widgets = {
            "application_open": Widgets.DATE,
            "application_close": Widgets.DATE,
            "allocation_open": Widgets.DATE,
            "allocation_close": Widgets.DATE,
            "reservation_expiry_hours": Widgets.NUMBER,
            "require_payment_before_checkin": Widgets.CHECKBOX,
            "allow_partial_payment": Widgets.CHECKBOX,
            "enable_hostel_charges": Widgets.CHECKBOX,
            "allow_transfers": Widgets.CHECKBOX,
            "allow_hostel_transfers": Widgets.CHECKBOX,
            "require_active_student": Widgets.CHECKBOX,
            "is_active": Widgets.CHECKBOX,
        }


class HostelFeeConfigForm(forms.ModelForm):
    class Meta:
        model = HostelFeeConfig
        fields = ["hostel", "session", "semester", "room", "fee_structure", "amount", "due_date", "is_active"]
        widgets = {
            "hostel": Widgets.SELECT,
            "session": Widgets.SELECT,
            "semester": Widgets.SELECT,
            "room": Widgets.SELECT,
            "fee_structure": Widgets.SELECT,
            "amount": Widgets.NUMBER,
            "due_date": Widgets.DATE,
            "is_active": Widgets.CHECKBOX,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["session"].queryset = AcademicSession.objects.all().order_by("-start_date")
        self.fields["semester"].queryset = Semester.objects.select_related("session").order_by(
            "-session__start_date", "name"
        )
        self.fields["semester"].required = False
        self.fields["semester"].help_text = (
            "Leave empty for an all-semester fee. Set it to charge per semester "
            "(e.g. 2026/2027 Semester 1)."
        )
        self.fields["room"].queryset = HostelRoom.objects.select_related("hostel").order_by(
            "hostel__name", "room_number"
        )
        self.fields["room"].required = False
        self.fields["room"].help_text = (
            "Leave empty for a hostel-wide fee, or pick a room to override its "
            "fee (e.g. en-suite surcharge)."
        )
        self.fields["fee_structure"].required = False
        self.fields["amount"].required = False
        self.fields["due_date"].required = False