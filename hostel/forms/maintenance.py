"""Bed maintenance form."""

from django import forms

from hostel.models import BedMaintenance, HostelBed

from .base import Widgets


class BedMaintenanceForm(forms.ModelForm):
    class Meta:
        model = BedMaintenance
        fields = ["bed", "reason", "notes"]
        widgets = {
            "bed": Widgets.SELECT,
            "reason": Widgets.TEXTAREA,
            "notes": Widgets.TEXTAREA,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bed"].queryset = HostelBed.objects.select_related(
            "room__hostel", "room__floor__block"
        ).order_by("room__hostel_id", "room__room_number", "bed_number")