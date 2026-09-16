"""Student hostel forms: application + transfer request."""

from django import forms

from hostel.models import Hostel, HostelBed, HostelRoom

from .base import Widgets


class HostelApplyForm(forms.Form):
    """Student applies to a room that still has at least one available bed."""

    hostel = forms.ModelChoiceField(
        queryset=Hostel.objects.filter(is_active=True),
        empty_label="Select Hostel",
        widget=forms.Select(attrs={"class": "form-control", "id": "id_hostel"}),
    )
    room = forms.ModelChoiceField(
        queryset=HostelRoom.objects.none(),
        empty_label="Select Room",
        widget=forms.Select(attrs={"class": "form-control", "id": "id_room"}),
    )
    notes = forms.CharField(
        label="Notes (optional)",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hostel_id = self.data.get("hostel") or self.initial.get("hostel")
        if hostel_id:
            self._set_room_queryset(hostel_id)

    def _set_room_queryset(self, hostel_id):
        try:
            hostel_id = int(hostel_id)
        except (TypeError, ValueError):
            return
        room_ids = HostelBed.objects.filter(
            state=HostelBed.BedState.AVAILABLE,
            room__hostel_id=hostel_id,
            room__is_available=True,
        ).values_list("room_id", flat=True).distinct()
        self.fields["room"].queryset = HostelRoom.objects.filter(
            pk__in=room_ids
        ).select_related("hostel", "floor__block").order_by("room_number")

    def clean(self):
        cleaned = super().clean()
        room = cleaned.get("room")
        if room and not room.beds.filter(state=HostelBed.BedState.AVAILABLE).exists():
            self.add_error(
                "room", "This room is no longer available. Please choose another room."
            )
        return cleaned


class HostelTransferRequestForm(forms.Form):
    """Student requests a move to a specific currently-available bed."""

    new_bed = forms.ModelChoiceField(
        label="Target bed",
        queryset=HostelBed.objects.filter(
            state=HostelBed.BedState.AVAILABLE
        ).select_related("room__hostel", "room__floor__block"),
        empty_label="Select an available bed",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    reason = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 3})
    )

    def clean_new_bed(self):
        bed = self.cleaned_data["new_bed"]
        if bed.state != HostelBed.BedState.AVAILABLE:
            raise forms.ValidationError("This bed is no longer available.")
        return bed