"""Hostel incident form."""

from django import forms

from hostel.models import HostelBed, HostelIncident, HostelRoom, IncidentStatus

from .base import Widgets


class HostelIncidentForm(forms.ModelForm):
    class Meta:
        model = HostelIncident
        fields = ["student", "room", "bed", "category", "description", "status", "assigned_to"]
        widgets = {
            "student": Widgets.SELECT,
            "room": Widgets.SELECT,
            "bed": Widgets.SELECT,
            "category": Widgets.SELECT,
            "description": Widgets.TEXTAREA,
            "status": Widgets.SELECT,
            "assigned_to": Widgets.SELECT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = self.fields["student"].queryset.filter(
            is_active=True
        ).order_by("last_name", "first_name")
        self.fields["student"].required = False
        self.fields["room"].required = False
        self.fields["bed"].required = False
        self.fields["bed"].queryset = HostelBed.objects.select_related(
            "room__hostel"
        ).order_by("room__hostel_id", "room__room_number", "bed_number")
        self.fields["room"].queryset = HostelRoom.objects.select_related(
            "hostel"
        ).order_by("hostel__name", "room_number")
        self.fields["assigned_to"].queryset = self.fields["assigned_to"].queryset.order_by(
            "last_name", "first_name"
        )
        self.fields["assigned_to"].required = False
        self.fields["status"].initial = IncidentStatus.OPEN