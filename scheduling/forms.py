"""
scheduling/forms.py — ModelForms for the scheduling domain.

Widgets mirror the shared design system (`.form-control` etc.) just like the
other eduPro modules.
"""

from django import forms

from academics.models import CourseOffering
from scheduling.constants import EventScope, ScheduleType, SessionType
from scheduling.models import (
    AcademicEvent,
    AcademicSchedule,
    Building,
    LecturerUnavailability,
    Room,
    SchedulingConfig,
    TimeSlot,
)


class Widgets:
    TEXT = forms.TextInput(attrs={"class": "form-control"})
    TEXTAREA = forms.Textarea(attrs={"class": "form-control", "rows": 4})
    SELECT = forms.Select(attrs={"class": "form-control"})
    SELECT2 = forms.Select(attrs={"class": "form-control"})
    DATE = forms.DateInput(attrs={"type": "date", "class": "form-control"})
    TIME = forms.TimeInput(attrs={"type": "time", "class": "form-control"})
    NUMBER = forms.NumberInput(attrs={"class": "form-control"})
    CHECKBOX = forms.CheckboxInput(attrs={"class": "form-check-input"})


# ── Physical assets ───────────────────────────────────────────────────────────

class BuildingForm(forms.ModelForm):
    class Meta:
        model = Building
        fields = ["name", "code", "address", "is_active"]
        widgets = {
            "name": Widgets.TEXT,
            "code": Widgets.TEXT,
            "address": Widgets.TEXTAREA,
            "is_active": Widgets.CHECKBOX,
        }


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["building", "name", "code", "room_type", "capacity",
                  "exam_capacity", "notes", "is_available", "is_active"]
        widgets = {
            "building": Widgets.SELECT2,
            "name": Widgets.TEXT,
            "code": Widgets.TEXT,
            "room_type": Widgets.SELECT,
            "capacity": Widgets.NUMBER,
            "exam_capacity": Widgets.NUMBER,
            "notes": Widgets.TEXTAREA,
            "is_available": Widgets.CHECKBOX,
            "is_active": Widgets.CHECKBOX,
        }

    def __init__(self, *args, institution=None, **kwargs):
        super().__init__(*args, **kwargs)
        if institution is not None:
            self.fields["building"].queryset = Building.objects.filter(
                institution=institution
            )


class TimeSlotForm(forms.ModelForm):
    class Meta:
        model = TimeSlot
        fields = ["day", "start_time", "end_time", "label", "is_default", "is_active"]
        widgets = {
            "day": Widgets.SELECT,
            "start_time": Widgets.TIME,
            "end_time": Widgets.TIME,
            "label": Widgets.TEXT,
            "is_default": Widgets.CHECKBOX,
            "is_active": Widgets.CHECKBOX,
        }


# ── Academic calendar ─────────────────────────────────────────────────────────

class AcademicEventForm(forms.ModelForm):
    class Meta:
        model = AcademicEvent
        fields = [
            "title", "description", "event_type", "scope",
            "programme", "department", "level", "offering",
            "all_day", "start_date", "end_date", "start_time", "end_time",
            "recurrence", "affects_scheduling", "is_public",
        ]
        widgets = {
            "title": Widgets.TEXT,
            "description": Widgets.TEXTAREA,
            "event_type": Widgets.SELECT,
            "scope": Widgets.SELECT,
            "programme": Widgets.SELECT2,
            "department": Widgets.SELECT2,
            "level": Widgets.SELECT2,
            "offering": Widgets.SELECT2,
            "start_date": Widgets.DATE,
            "end_date": Widgets.DATE,
            "start_time": Widgets.TIME,
            "end_time": Widgets.TIME,
            "recurrence": Widgets.SELECT,
            "all_day": Widgets.CHECKBOX,
            "affects_scheduling": Widgets.CHECKBOX,
            "is_public": Widgets.CHECKBOX,
        }

    def __init__(self, *args, institution=None, **kwargs):
        super().__init__(*args, **kwargs)
        if institution is not None:
            from academics.models import Department, Level, Program

            self.fields["programme"].queryset = Program.objects.filter(
                department__faculty__institution=institution
            )
            self.fields["department"].queryset = Department.objects.filter(
                institution=institution
            )
            self.fields["level"].queryset = Level.objects.filter(
                program__department__faculty__institution=institution
            )
            self.fields["offering"].queryset = CourseOffering.objects.filter(
                course__department__faculty__institution=institution
            )
        for field in ("programme", "department", "level", "offering"):
            self.fields[field].required = False

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get("scope")
        if scope == EventScope.PROGRAMME and not cleaned.get("programme"):
            self.add_error("programme", "Select a programme for this scope.")
        if scope == EventScope.DEPARTMENT and not cleaned.get("department"):
            self.add_error("department", "Select a department for this scope.")
        if scope == EventScope.LEVEL and not cleaned.get("level"):
            self.add_error("level", "Select a level for this scope.")
        if scope == EventScope.OFFERING and not cleaned.get("offering"):
            self.add_error("offering", "Select an offering for this scope.")
        if cleaned.get("all_day"):
            cleaned["start_time"] = None
            cleaned["end_time"] = None
        return cleaned


# ── Lecturer unavailability ───────────────────────────────────────────────────

class LecturerUnavailabilityForm(forms.ModelForm):
    class Meta:
        model = LecturerUnavailability
        fields = ["lecturer", "day", "start_time", "end_time", "reason", "is_active"]
        widgets = {
            "lecturer": Widgets.SELECT2,
            "day": Widgets.SELECT,
            "start_time": Widgets.TIME,
            "end_time": Widgets.TIME,
            "reason": Widgets.TEXT,
            "is_active": Widgets.CHECKBOX,
        }


# ── Schedules ─────────────────────────────────────────────────────────────────

class AcademicScheduleForm(forms.ModelForm):
    class Meta:
        model = AcademicSchedule
        fields = ["name", "semester", "schedule_type", "consider_room_availability",
                  "notes"]
        widgets = {
            "name": Widgets.TEXT,
            "semester": Widgets.SELECT2,
            "schedule_type": Widgets.SELECT,
            "consider_room_availability": Widgets.CHECKBOX,
            "notes": Widgets.TEXTAREA,
        }
        labels = {
            "consider_room_availability": "Respect lecturer unavailability",
        }


class SchedulingConfigForm(forms.ModelForm):
    class Meta:
        model = SchedulingConfig
        fields = [
            "default_slot_minutes", "min_break_minutes",
            "max_lectures_per_day", "max_consecutive_per_day",
            "class_target_days_per_week", "morning_start", "afternoon_start",
            "evening_start", "workdays", "require_exam_capacity",
            "enforce_lecture_hours", "auto_approve_hod",
            "generation_budget", "optimizer_iterations", "seed",
        ]
        widgets = {
            "default_slot_minutes": Widgets.NUMBER,
            "min_break_minutes": Widgets.NUMBER,
            "max_lectures_per_day": Widgets.NUMBER,
            "max_consecutive_per_day": Widgets.NUMBER,
            "class_target_days_per_week": Widgets.NUMBER,
            "morning_start": Widgets.TIME,
            "afternoon_start": Widgets.TIME,
            "evening_start": Widgets.TIME,
            "workdays": forms.CheckboxSelectMultiple,
            "require_exam_capacity": Widgets.CHECKBOX,
            "enforce_lecture_hours": Widgets.CHECKBOX,
            "auto_approve_hod": Widgets.CHECKBOX,
            "generation_budget": Widgets.NUMBER,
            "optimizer_iterations": Widgets.NUMBER,
            "seed": Widgets.NUMBER,
        }

    def clean_workdays(self):
        value = self.cleaned_data.get("workdays") or []
        if isinstance(value, (list, tuple)):
            value = [int(v) for v in value]
        if not value:
            raise forms.ValidationError("At least one working day is required.")
        return value


class ScheduleEntryForm(forms.Form):
    """Manual add/update of a single weekly session."""
    offering = forms.ModelChoiceField(
        queryset=CourseOffering.objects.none(),
        required=True, label="Course offering",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    session_type = forms.ChoiceField(
        choices=SessionType.choices, initial=SessionType.LECTURE,
        widget=Widgets.SELECT,
    )
    day = forms.ChoiceField(
        choices=(
            (0, "Monday"), (1, "Tuesday"), (2, "Wednesday"),
            (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday"),
        ),
        widget=Widgets.SELECT,
    )
    start_time = forms.TimeField(widget=Widgets.TIME)
    end_time = forms.TimeField(widget=Widgets.TIME)
    room = forms.ModelChoiceField(
        queryset=Room.objects.none(), required=False,
        label="Room (optional)", widget=Widgets.SELECT2,
    )
    lecturer = forms.ModelChoiceField(
        queryset=None, required=False, label="Lecturer (optional)",
        widget=Widgets.SELECT2,
    )

    def __init__(self, *args, institution=None, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model

        if institution is not None:
            self.fields["offering"].queryset = CourseOffering.objects.filter(
                course__department__faculty__institution=institution,
                is_active=True,
            ).select_related("course")
            self.fields["room"].queryset = Room.objects.filter(
                building__institution=institution, is_active=True,
            )
        self.fields["lecturer"].queryset = get_user_model().objects.filter(
            role="teacher"
        )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", "End time must be after start time.")
        return cleaned


# ── Generation options (non-persisted form for the generate screen) ───────────

class GenerateOptionsForm(forms.Form):
    session_types = forms.MultipleChoiceField(
        choices=SessionType.choices,
        initial=[SessionType.LECTURE, SessionType.LAB],
        label="Session types to generate",
        widget=forms.CheckboxSelectMultiple,
    )
    seed = forms.IntegerField(required=False, label="Seed (optional)",
                              widget=Widgets.NUMBER)
    prefer_allocated_lecturer = forms.BooleanField(
        initial=True, required=False,
        label="Prefer the allocated lecturer for each offering",
        widget=Widgets.CHECKBOX,
    )

    def clean_seed(self):
        value = self.cleaned_data.get("seed") or None
        return value