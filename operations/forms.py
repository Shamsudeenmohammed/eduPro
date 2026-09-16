from django import forms

from .models import (
    Announcement,
    CalendarEvent,
    SupportTicket,
    TimetableSlot,
)


class Widgets:
    TEXT = forms.TextInput(attrs={"class": "form-control"})
    TEXTAREA = forms.Textarea(attrs={"class": "form-control", "rows": 4})
    SELECT = forms.Select(attrs={"class": "form-control"})
    SELECT2 = forms.Select(attrs={"class": "form-control"})
    DATE = forms.DateInput(attrs={"type": "date", "class": "form-control"})
    DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"})
    NUMBER = forms.NumberInput(attrs={"class": "form-control"})
    CHECKBOX = forms.CheckboxInput(attrs={"class": "form-check-input"})


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "content", "priority", "target_roles", "expires_at", "is_pinned"]
        widgets = {
            "title": Widgets.TEXT,
            "expires_at": Widgets.DATETIME,
            "content": Widgets.TEXTAREA,
            "priority": Widgets.SELECT,
            "target_roles": Widgets.TEXT,
            "is_pinned": Widgets.CHECKBOX,
        }


class CalendarEventForm(forms.ModelForm):
    class Meta:
        model = CalendarEvent
        fields = ["title", "description", "start_date", "end_date", "event_type", "location", "is_public"]
        widgets = {
            "title": Widgets.TEXT,
            "description": Widgets.TEXTAREA,
            "start_date": Widgets.DATE,
            "end_date": Widgets.DATE,
            "event_type": Widgets.TEXT,
            "location": Widgets.TEXT,
            "is_public": Widgets.CHECKBOX,
        }


class TimetableSlotForm(forms.ModelForm):
    class Meta:
        model = TimetableSlot
        fields = ["offering", "day", "start_time", "end_time", "venue"]
        widgets = {
            "offering": Widgets.SELECT2,
            "day": Widgets.SELECT,
            "start_time": forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
            "end_time": forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
            "venue": Widgets.TEXT,
        }


class SupportTicketForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ["subject", "description", "category"]
        widgets = {
            "subject": Widgets.TEXT,
            "description": Widgets.TEXTAREA,
            "category": Widgets.SELECT,
        }