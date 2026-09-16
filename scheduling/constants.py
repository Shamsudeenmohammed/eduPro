"""
scheduling/constants.py

Domain constants for the Academic Calendar & Scheduling Management System.

Centralising every choice here keeps the engine and models in agreement and
keeps the UI labels stable.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


# ── Schedule workflows ────────────────────────────────────────────────────────

class ScheduleStatus(models.TextChoices):
    """
    Lifecycle of an academic schedule (class or exam timetable).

        DRAFT        — new / edited, generation may rerun freely
        GENERATED    — engine produced entries (auto)
        UNDER_REVIEW — submitted; waiting for HOD sign-off
        HOD_APPROVED — every owning HOD accepted their departments' part
        APPROVED     — administrator confirmed the final version
        PUBLISHED    — public; students/lecturers can see it
        REJECTED     — an owning HOD objected to the submitted timetable
        CORRECTION_REQUIRED — returned to the scheduler for rework after a
                              HOD rejection (editable again)
        ARCHIVED     — superseded or closed
    """

    DRAFT        = "draft",        _("Draft")
    GENERATED    = "generated",    _("Generated")
    UNDER_REVIEW = "under_review", _("Under Review")
    HOD_APPROVED = "hod_approved", _("HOD Approved")
    APPROVED     = "approved",     _("Approved")
    PUBLISHED    = "published",    _("Published")
    REJECTED     = "rejected",     _("Rejected")
    CORRECTION_REQUIRED = "revision", _("Revision Required")
    ARCHIVED     = "archived",     _("Archived")


class ScheduleType(models.TextChoices):
    CLASS = "class", _("Class Timetable")
    EXAM  = "exam",  _("Examination Timetable")
    COMBINED = "combined", _("Combined")


class ScheduleTypeAlias(models.TextChoices):
    """Alias so both 'class' and 'combined' schedules share CLASS engine logic."""
    CLASS = "class", _("Class Timetable")
    EXAM  = "exam",  _("Examination Timetable")


# ── Session types inside a schedule entry ─────────────────────────────────────

class SessionType(models.TextChoices):
    LECTURE = "lecture", _("Lecture")
    LAB     = "lab",     _("Laboratory / Practical")
    EXAM    = "exam",    _("Examination")


# ── Academic calendar event types ─────────────────────────────────────────────

class EventType(models.TextChoices):
    SESSION   = "session",   _("Academic Session")
    SEMESTER  = "semester",  _("Semester")
    HOLIDAY   = "holiday",   _("Holiday")
    BREAK     = "break",     _("Mid-Semester Break")
    EXAM      = "exam",      _("Examination Window")
    CEREMONY  = "ceremony",  _("Ceremony / Event")
    DEADLINE  = "deadline",  _("Deadline")
    RESULT    = "result",    _("Result Release")
    GENERAL   = "general",   _("General Event")


class EventRecurrence(models.TextChoices):
    NONE   = "none",   _("Does not repeat")
    DAILY  = "daily",  _("Daily")
    WEEKLY = "weekly", _("Weekly")
    MONTHLY = "monthly", _("Monthly")
    YEARLY = "yearly", _("Yearly")


# ── Rooms ─────────────────────────────────────────────────────────────────────

class RoomType(models.TextChoices):
    LECTURE = "lecture", _("Lecture Hall")
    SEMINAR = "seminar", _("Seminar Room")
    LAB     = "lab",     _("Laboratory")
    EXAM    = "exam",    _("Examination Hall")
    OFFICE  = "office",  _("Office")
    OTHER   = "other",   _("Other")


# ── Days of the week (ISO weekdays: Monday = 0 … Sunday = 6) ─────────────────

class IsoWeekday(models.IntegerChoices):
    MONDAY    = 0, _("Monday")
    TUESDAY   = 1, _("Tuesday")
    WEDNESDAY = 2, _("Wednesday")
    THURSDAY  = 3, _("Thursday")
    FRIDAY    = 4, _("Friday")
    SATURDAY  = 5, _("Saturday")
    SUNDAY    = 6, _("Sunday")


DAYS_OF_WEEK = [d for d in range(7)]
DEFAULT_WORKDAYS = [0, 1, 2, 3, 4]  # Monday → Friday


# ── Generation jobs ───────────────────────────────────────────────────────────

class JobStatus(models.TextChoices):
    PENDING   = "pending",   _("Pending")
    RUNNING   = "running",   _("Running")
    COMPLETED = "completed", _("Completed")
    FAILED    = "failed",    _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


# ── Conflict severities / kinds ───────────────────────────────────────────────

class ConflictSeverity(models.TextChoices):
    HARD = "hard", _("Hard")
    SOFT = "soft", _("Soft")


class ConflictKind(models.TextChoices):
    LECTURER_BUSY   = "lecturer_busy",   _("Lecturer double-booked")
    ROOM_BUSY       = "room_busy",       _("Room double-booked")
    GROUP_CLASH     = "group_clash",     _("Cohort clash (same students)")
    CAPACITY        = "capacity",        _("Room capacity exceeded")
    WRONG_ROOM_TYPE = "wrong_room_type", _("Room type unsuitable")
    LECTURER_UNSURE = "lecturer_unavailable", _("Lecturer unavailable")
    BLACKOUT        = "blackout",        _("Scheduled during blocked period")
    NON_WORKDAY     = "non_workday",     _("Scheduled on a non-working day")
    LECTURER_DAY_LIMIT = "lecturer_day_limit", _("Lecturer daily load exceeded")
    LECTURER_BACK_TO_BACK = "lecturer_back_to_back", _("Lecturer back-to-back load exceeded")
    UNPLACED        = "unplaced",        _("Requirement could not be placed")
    DUPLICATE_WEEKLY_HOURS = "duplicate_weekly_hours", _("Weekly hours duplicated")
    OVERLAP         = "overlap",         _("Timetable overlap")
    BREAK_VIOLATION = "break_violation", _("Minimum break not respected")
    EARLY_OR_LATE   = "early_or_late",   _("Unfavourable time of day")
    CONCENTRATED    = "concentrated",    _("Classes too concentrated")
    SPREAD          = "spread",          _("Classes not spread across week")


# ── Conflict weights for scoring (soft) and severity thresholds ───────────────

DEFAULT_CONFLICT_WEIGHTS = {
    "early_morning":     1.0,   # before config.morning_start
    "late_evening":      2.0,   # after config.evening_start
    "consecutive_hours": 1.5,   # per extra consecutive hour beyond best practice
    "spread_violation":  2.0,   # sessions bunched on fewer days than ideal
    "room_preference":   0.5,   # lab session in seminar room (hard-only in engine)
}


# ── Default engine parameters (overridable via SchedulingConfig) ──────────────

DEFAULTS = {
    "default_slot_minutes": 60,
    "min_break_minutes": 15,
    "max_lectures_per_day": 8,
    "max_consecutive_per_day": 3,
    "class_target_days_per_week": 5,
    "exam_slot_minutes": 120,
    "max_sessions_per_day_per_cohort": 6,
}


# ── Event scopes ──────────────────────────────────────────────────────────────

class EventScope(models.TextChoices):
    ALL         = "all",         _("Whole institution")
    PROGRAMME   = "programme",   _("Specific programme")
    DEPARTMENT  = "department",  _("Specific department")
    LEVEL       = "level",       _("Specific level")
    OFFERING    = "offering",    _("Specific course offering")