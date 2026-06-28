from django import forms
from django.utils import timezone
from .models import Announcement, CalendarEvent, Hostel, HostelAllocation, HostelRoom, SupportTicket, TimetableSlot


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "content", "priority", "target_roles", "expires_at", "is_pinned"]
        widgets = {
            "expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "content": forms.Textarea(attrs={"rows": 5}),
        }


class CalendarEventForm(forms.ModelForm):
    class Meta:
        model = CalendarEvent
        fields = ["title", "description", "start_date", "end_date", "event_type", "location", "is_public"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }


class TimetableSlotForm(forms.ModelForm):
    class Meta:
        model = TimetableSlot
        fields = ["offering", "day", "start_time", "end_time", "venue"]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }


class SupportTicketForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ["subject", "description", "category"]


class HostelApplyForm(forms.Form):
    """Student applies for a room in a selected hostel."""

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "hostel" in self.data:
            try:
                hostel_id = int(self.data.get("hostel"))
                occupied_ids = HostelAllocation.objects.filter(
                    room__hostel_id=hostel_id, is_active=True
                ).values_list("room_id", flat=True)
                self.fields["room"].queryset = HostelRoom.objects.filter(
                    hostel_id=hostel_id, is_available=True
                ).exclude(pk__in=occupied_ids)
            except (ValueError, TypeError):
                pass
