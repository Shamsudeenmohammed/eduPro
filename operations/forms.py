from django import forms
from .models import Announcement, CalendarEvent, SupportTicket, TimetableSlot


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
