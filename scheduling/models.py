"""
scheduling/models.py

Data model for the Academic Calendar & Scheduling Management System.

Everything the engine needs is persisted here:

    Buildings & Rooms          — physical venues with type + capacity
    TimeSlot                   — canonical weekly time slots
    SchedulingConfig           — per-institution engine parameters
    AcademicEvent              — academic calendar events (breaks, exams, …)
    LecturerUnavailability     — weekly windows a lecturer cannot teach
    AcademicSchedule           — one generated timetable with full lifecycle
    ScheduleHodApproval        — per-department HOD sign-off records
    ScheduleVersion            — immutable snapshots for history/rollback
    ScheduleEntry              — one weekly session placed on the schedule
    ScheduleGenerationJob      — generation run metadata / diagnostics

Design rules honoured:
  * All overlapping-time arithmetic lives in the engine (validators /
    conflict_detector), never spread across views.
  * Published schedules are never silently overwritten — the services layer
    creates a new ScheduleVersion before any mutation.
  * A "published current" timetable is single per (semester, schedule_type)
    tracked via the is_current flag (safer cross-DB than a partial unique).
"""

from datetime import timedelta
from datetime import time as dt_time

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from scheduling.constants import (
    EventRecurrence,
    EventScope,
    EventType,
    IsoWeekday,
    JobStatus,
    RoomType,
    ScheduleStatus,
    ScheduleType,
    SessionType,
)

DEFAULT_WORKDAYS = [0, 1, 2, 3, 4]


def _as_timedelta(value):
    return timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)


def times_overlap(start_a, end_a, start_b, end_b):
    """True when two time ranges overlap (end-exclusive)."""
    return _as_timedelta(start_a) < _as_timedelta(end_b) and _as_timedelta(start_b) < _as_timedelta(end_a)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


# ─────────────────────────────────────────────────────────────────────────────
# BUILDINGS & ROOMS
# ─────────────────────────────────────────────────────────────────────────────

class Building(TimeStampedModel):
    institution = models.ForeignKey(
        "academics.Institution", on_delete=models.CASCADE,
        related_name="scheduling_buildings", verbose_name=_("institution"),
    )
    name = models.CharField(_("name"), max_length=200)
    code = models.CharField(_("code"), max_length=20)
    address = models.TextField(_("address"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("building")
        verbose_name_plural = _("buildings")
        ordering = ["name"]
        unique_together = [("institution", "code")]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Room(TimeStampedModel):
    building = models.ForeignKey(
        Building, on_delete=models.CASCADE,
        related_name="rooms", verbose_name=_("building"),
    )
    name = models.CharField(_("name"), max_length=120)
    code = models.CharField(_("room number / code"), max_length=30)
    room_type = models.CharField(
        _("room type"), max_length=15,
        choices=RoomType.choices, default=RoomType.LECTURE,
    )
    capacity = models.PositiveSmallIntegerField(
        _("capacity"), default=50,
        validators=[MinValueValidator(1), MaxValueValidator(2000)],
    )
    exam_capacity = models.PositiveSmallIntegerField(
        _("exam capacity"), default=50, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(2000)],
        help_text=_("Students a single seated examination can hold. "
                    "Defaults to capacity on save if left 0."),
    )
    notes = models.TextField(_("notes"), blank=True)
    is_available = models.BooleanField(
        _("available for scheduling"), default=True,
        help_text=_("Uncheck to exclude the room from automatic generation."),
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("room")
        verbose_name_plural = _("rooms")
        ordering = ["building", "code"]
        unique_together = [("building", "code")]

    def __str__(self):
        return f"{self.building.code} {self.code} — {self.get_room_type_display()}"

    @property
    def institution(self):
        return self.building.institution

    def save(self, *args, **kwargs):
        if not self.exam_capacity:
            self.exam_capacity = self.capacity
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# TIME SLOTS
# ─────────────────────────────────────────────────────────────────────────────

class TimeSlot(TimeStampedModel):
    """Canonical, reusable weekly time slot (day + start + end)."""
    day = models.PositiveSmallIntegerField(_("day"), choices=IsoWeekday.choices)
    start_time = models.TimeField(_("start time"))
    end_time = models.TimeField(_("end time"))
    label = models.CharField(_("label"), max_length=60, blank=True)
    is_default = models.BooleanField(
        _("default slot"), default=False,
        help_text=_("Default slots are auto-offered to the generator."),
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("time slot")
        verbose_name_plural = _("time slots")
        ordering = ["day", "start_time"]
        unique_together = [("day", "start_time", "end_time")]

    def __str__(self):
        label = f" ({self.label})" if self.label else ""
        return (
            f"{self.get_day_display()} {self.start_time:%H:%M}–"
            f"{self.end_time:%H:%M}{label}"
        )

    @property
    def duration_minutes(self):
        return (_as_timedelta(self.end_time) - _as_timedelta(self.start_time)).seconds // 60


# ─────────────────────────────────────────────────────────────────────────────
# PER-INSTITUTION ENGINE CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

class SchedulingConfig(TimeStampedModel):
    institution = models.OneToOneField(
        "academics.Institution", on_delete=models.CASCADE,
        related_name="scheduling_config", verbose_name=_("institution"),
    )
    default_slot_minutes = models.PositiveSmallIntegerField(
        _("default slot length (minutes)"), default=60,
        validators=[MinValueValidator(30), MaxValueValidator(180)],
    )
    min_break_minutes = models.PositiveSmallIntegerField(
        _("minimum break between classes (minutes)"), default=15,
        validators=[MinValueValidator(0), MaxValueValidator(120)],
    )
    max_lectures_per_day = models.PositiveSmallIntegerField(
        _("maximum sessions per day per cohort"), default=8,
        validators=[MinValueValidator(1), MaxValueValidator(16)],
    )
    max_consecutive_per_day = models.PositiveSmallIntegerField(
        _("maximum consecutive sessions per cohort"), default=3,
        validators=[MinValueValidator(1), MaxValueValidator(8)],
    )
    class_target_days_per_week = models.PositiveSmallIntegerField(
        _("target scheduling days per week"), default=5,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )
    workdays = models.JSONField(
        _("working days"), default=list,
        help_text=_("List of ISO weekdays (0=Mon … 6=Sun) used for classes."),
    )
    morning_start = models.TimeField(_("morning start"), default=dt_time(8, 0))
    afternoon_start = models.TimeField(_("afternoon start"), default=dt_time(13, 0))
    evening_start = models.TimeField(_("evening start"), default=dt_time(17, 0))
    require_exam_capacity = models.BooleanField(
        _("enforce exam capacity"), default=True,
        help_text=_("Examinations must never exceed a room's exam capacity."),
    )
    enforce_lecture_hours = models.BooleanField(
        _("enforce declared weekly hours"), default=True,
        help_text=_("Generator places the full lecture+lab hours declared on "
                    "each course; unplaced sessions are reported as conflicts."),
    )
    auto_approve_hod = models.BooleanField(
        _("auto-approve on HOD submission"), default=False,
        help_text=_("If every owning HOD is the submitting admin themselves, "
                    "mark HOD_APPROVED immediately."),
    )
    generation_budget = models.PositiveIntegerField(
        _("generation backtrack budget"), default=20000,
        help_text=_("Maximum backtracks before the generator gives up on the "
                    "remaining requirements."),
    )
    optimizer_iterations = models.PositiveIntegerField(
        _("optimizer iterations"), default=2000,
        help_text=_("Local-search improvement steps applied after generation."),
    )
    seed = models.IntegerField(
        _("deterministic seed"), default=20250101,
        help_text=_("Seed for reproducible tie-breaking. Keep fixed for fully "
                    "deterministic output."),
    )

    class Meta:
        verbose_name = _("scheduling configuration")
        verbose_name_plural = _("scheduling configurations")

    def __str__(self):
        return f"Scheduling config — {self.institution}"

    @classmethod
    def for_institution(cls, institution):
        cfg, created = cls.objects.get_or_create(institution=institution)
        if created:
            if not cfg.workdays:
                cfg.workdays = list(DEFAULT_WORKDAYS)
                cfg.save(update_fields=["workdays"])
        return cfg

    @property
    def workday_set(self):
        return set(self.workdays or DEFAULT_WORKDAYS)

    def to_engine_dict(self):
        """All values the engine needs, as plain data."""
        return {
            "default_slot_minutes": self.default_slot_minutes,
            "min_break_minutes": self.min_break_minutes,
            "max_lectures_per_day": self.max_lectures_per_day,
            "max_consecutive_per_day": self.max_consecutive_per_day,
            "class_target_days_per_week": self.class_target_days_per_week,
            "workdays": list(self.workday_set),
            "morning_start": self.morning_start,
            "afternoon_start": self.afternoon_start,
            "evening_start": self.evening_start,
            "require_exam_capacity": self.require_exam_capacity,
            "enforce_lecture_hours": self.enforce_lecture_hours,
            "generation_budget": self.generation_budget,
            "optimizer_iterations": self.optimizer_iterations,
            "seed": self.seed,
        }


# ─────────────────────────────────────────────────────────────────────────────
# ACADEMIC CALENDAR EVENTS
# ─────────────────────────────────────────────────────────────────────────────

class AcademicEvent(TimeStampedModel):
    institution = models.ForeignKey(
        "academics.Institution", on_delete=models.CASCADE,
        related_name="scheduling_events", verbose_name=_("institution"),
        null=True, blank=True,
        help_text=_("Institution owning the event. Left blank when no "
                    "institution is configured yet — such events are global."),
    )
    title = models.CharField(_("title"), max_length=200)
    description = models.TextField(_("description"), blank=True)
    event_type = models.CharField(
        _("event type"), max_length=15,
        choices=EventType.choices, default=EventType.GENERAL,
    )
    scope = models.CharField(
        _("scope"), max_length=15,
        choices=EventScope.choices, default=EventScope.ALL,
    )
    programme = models.ForeignKey(
        "academics.Program", on_delete=models.CASCADE,
        null=True, blank=True, related_name="scheduling_events",
        verbose_name=_("programme"),
    )
    department = models.ForeignKey(
        "academics.Department", on_delete=models.CASCADE,
        null=True, blank=True, related_name="scheduling_events",
        verbose_name=_("department"),
    )
    level = models.ForeignKey(
        "academics.Level", on_delete=models.CASCADE,
        null=True, blank=True, related_name="scheduling_events",
        verbose_name=_("level"),
    )
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.CASCADE,
        null=True, blank=True, related_name="scheduling_events",
        verbose_name=_("course offering"),
    )
    all_day = models.BooleanField(_("all day"), default=True)
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"), null=True, blank=True)
    start_time = models.TimeField(_("start time"), null=True, blank=True)
    end_time = models.TimeField(_("end time"), null=True, blank=True)
    recurrence = models.CharField(
        _("recurrence"), max_length=10,
        choices=EventRecurrence.choices, default=EventRecurrence.NONE,
    )
    affects_scheduling = models.BooleanField(
        _("affects scheduling"), default=False,
        help_text=_("True when the event blocks class placement, e.g. a "
                    "holiday, break or examination window."),
    )
    is_public = models.BooleanField(_("public"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_events_created",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("academic calendar event")
        verbose_name_plural = _("academic calendar events")
        ordering = ["start_date", "start_time"]

    def __str__(self):
        return f"{self.title} ({self.start_date:%d %b %Y})"

    @property
    def effective_end_date(self):
        return self.end_date or self.start_date

    @property
    def is_blackout(self):
        return self.affects_scheduling

    def scoped_to_offering(self, offering):
        """Whether this event affects a specific offering's scheduling."""
        if self.scope == EventScope.ALL:
            return True

        if self.scope == EventScope.PROGRAMME:
            # Applies when the event's programme hosts the offering's level, or
            # the programme's owning department is among the offering's cohorts.
            if not self.programme_id:
                return False
            if offering.level_id:
                if offering.level.program_id == self.programme_id:
                    return True
            dep_ids = set(offering.departments.values_list("pk", flat=True))
            if self.programme.department_id in dep_ids:
                return True
            return False

        if self.scope == EventScope.DEPARTMENT:
            dep_ids = set(offering.departments.values_list("pk", flat=True))
            return self.department_id in dep_ids

        if self.scope == EventScope.LEVEL:
            return bool(self.level_id) and offering.level_id == self.level_id

        if self.scope == EventScope.OFFERING:
            return bool(self.offering_id) and offering.pk == self.offering_id

        return False


# ─────────────────────────────────────────────────────────────────────────────
# LECTURER UNAVAILABILITY
# ─────────────────────────────────────────────────────────────────────────────

class LecturerUnavailability(TimeStampedModel):
    lecturer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="scheduling_unavailability", verbose_name=_("lecturer"),
        limit_choices_to={"role": "teacher"},
    )
    day = models.PositiveSmallIntegerField(_("day"), choices=IsoWeekday.choices)
    start_time = models.TimeField(_("start time"))
    end_time = models.TimeField(_("end time"))
    reason = models.CharField(_("reason"), max_length=200, blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("lecturer unavailability")
        verbose_name_plural = _("lecturer unavailability windows")
        ordering = ["lecturer", "day", "start_time"]

    def __str__(self):
        return (
            f"{self.lecturer.get_full_name()} unavailable {self.get_day_display()} "
            f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ACADEMIC SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────

class AcademicSchedule(TimeStampedModel):
    name = models.CharField(_("name"), max_length=200)
    institution = models.ForeignKey(
        "academics.Institution", on_delete=models.CASCADE,
        related_name="scheduling_schedules", verbose_name=_("institution"),
        null=True, blank=True,
        help_text=_("Inferred from the semester's offerings on save if blank."),
    )
    semester = models.ForeignKey(
        "academics.Semester", on_delete=models.CASCADE,
        related_name="schedules", verbose_name=_("semester"),
    )
    schedule_type = models.CharField(
        _("schedule type"), max_length=15,
        choices=ScheduleType.choices, default=ScheduleType.CLASS,
    )
    status = models.CharField(
        _("status"), max_length=15,
        choices=ScheduleStatus.choices, default=ScheduleStatus.DRAFT,
        db_index=True,
    )
    consider_room_availability = models.BooleanField(
        _("respect lecturer unavailability"), default=True,
        help_text=_("Exclude lecturer-unavailable windows during generation."),
    )
    lock_published_entries = models.BooleanField(
        _("lock published entries"), default=True,
        help_text=_("Prevent edits once the schedule is published."),
    )
    is_current = models.BooleanField(
        _("current published schedule"), default=False,
        help_text=_("At most one current schedule per (semester, type)."),
    )
    notes = models.TextField(_("notes"), blank=True)

    # ── Engine statistics (refreshed by conflict_detector) ──────────────────
    hard_conflicts = models.PositiveIntegerField(_("hard conflicts"), default=0)
    soft_conflicts = models.PositiveIntegerField(_("soft conflicts"), default=0)
    unplaced = models.PositiveIntegerField(_("unplaced sessions"), default=0)
    score = models.FloatField(_("quality score"), default=0.0)

    # ── Generation metadata ──────────────────────────────────────────────────
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_schedules_generated",
        verbose_name=_("generated by"),
    )
    generated_at = models.DateTimeField(_("generated at"), null=True, blank=True)
    generation_meta = models.JSONField(_("generation metadata"), default=dict, blank=True)

    # ── Lifecycle audit ──────────────────────────────────────────────────────
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_schedules_submitted",
        verbose_name=_("submitted by"),
    )
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_schedules_approved",
        verbose_name=_("approved by"),
    )
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_schedules_published",
        verbose_name=_("published by"),
    )
    published_at = models.DateTimeField(_("published at"), null=True, blank=True)

    class Meta:
        verbose_name = _("academic schedule")
        verbose_name_plural = _("academic schedules")
        ordering = ["-semester__session__start_date", "-updated_at"]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    @property
    def session(self):
        return self.semester.session

    @property
    def is_published(self):
        return self.status == ScheduleStatus.PUBLISHED

    @property
    def has_hard_conflicts(self):
        return self.hard_conflicts > 0 or self.unplaced > 0

    @property
    def is_locked(self):
        """Edits are forbidden from submission onward, and always when published."""
        locked = (
            ScheduleStatus.UNDER_REVIEW,
            ScheduleStatus.HOD_APPROVED,
            ScheduleStatus.APPROVED,
            ScheduleStatus.PUBLISHED,
        )
        return self.status in locked

    def save(self, *args, **kwargs):
        if not self.institution_id:
            offering = self.semester.offerings.filter(
                departments__isnull=False
            ).first()
            if offering is not None:
                dept = offering.departments.first()
                if dept is not None:
                    self.institution_id = dept.institution_id
        with transaction.atomic():
            if self.status == ScheduleStatus.PUBLISHED:
                self.is_current = True
                AcademicSchedule.objects.filter(
                    semester_id=self.semester_id,
                    schedule_type=self.schedule_type,
                    is_current=True,
                ).exclude(pk=self.pk).update(is_current=False)
            super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULE HOD APPROVAL  (per department, so multiple HODs can sign off)
# ─────────────────────────────────────────────────────────────────────────────

class ScheduleHodApproval(TimeStampedModel):
    schedule = models.ForeignKey(
        AcademicSchedule, on_delete=models.CASCADE,
        related_name="hod_approvals", verbose_name=_("schedule"),
    )
    hod = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="scheduling_hod_approvals", verbose_name=_("HOD"),
    )
    department = models.ForeignKey(
        "academics.Department", on_delete=models.CASCADE,
        related_name="scheduling_hod_approvals", verbose_name=_("department"),
    )
    approved = models.BooleanField(_("approved"), default=False)
    comment = models.TextField(_("comment"), blank=True)
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)

    class Meta:
        verbose_name = _("schedule HOD approval")
        verbose_name_plural = _("schedule HOD approvals")
        unique_together = [("schedule", "department")]

    def __str__(self):
        flag = "approved" if self.approved else "pending/objected"
        return f"{self.department.code}: {flag} ({self.hod.get_full_name()})"

    def approve(self, comment=""):
        self.approved = True
        self.comment = comment
        self.decided_at = timezone.now()
        self.save(update_fields=["approved", "comment", "decided_at", "updated_at"])

    def reject(self, comment):
        self.approved = False
        self.comment = comment
        self.decided_at = timezone.now()
        self.save(update_fields=["approved", "comment", "decided_at", "updated_at"])


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULE VERSION  (immutable snapshots)
# ─────────────────────────────────────────────────────────────────────────────

class ScheduleVersion(TimeStampedModel):
    schedule = models.ForeignKey(
        AcademicSchedule, on_delete=models.CASCADE,
        related_name="versions", verbose_name=_("schedule"),
    )
    version_number = models.PositiveIntegerField(_("version number"))
    snapshot = models.JSONField(_("entries snapshot"), default=list)
    stats = models.JSONField(_("statistics snapshot"), default=dict, blank=True)
    reason = models.CharField(_("reason"), max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_versions_created",
        verbose_name=_("created by"),
    )
    is_published = models.BooleanField(_("published snapshot"), default=False)

    class Meta:
        verbose_name = _("schedule version")
        verbose_name_plural = _("schedule versions")
        ordering = ["-version_number"]
        unique_together = [("schedule", "version_number")]

    def __str__(self):
        return f"{self.schedule.name} v{self.version_number}"

    @classmethod
    def next_number(cls, schedule):
        return (cls.objects.filter(schedule=schedule).aggregate(
            m=models.Max("version_number")
        )["m"] or 0) + 1

    @classmethod
    def create_snapshot(cls, schedule, actor=None, reason="", is_published=False):
        entries = []
        for e in schedule.entries.select_related("room", "lecturer", "course").all():
            entries.append({
                "id": e.pk,
                "offering_id": e.offering_id,
                "course_code": e.course.code if e.course else "",
                "course_title": e.course.title if e.course else "",
                "lecturer_id": e.lecturer_id,
                "lecturer_name": e.lecturer_name,
                "room_id": e.room_id,
                "room_name": e.room_name,
                "session_type": e.session_type,
                "day": e.day,
                "start": e.start_time.strftime("%H:%M"),
                "end": e.end_time.strftime("%H:%M"),
                "locked": e.is_locked,
            })
        return cls.objects.create(
            schedule=schedule,
            version_number=cls.next_number(schedule),
            snapshot=entries,
            stats={
                "hard_conflicts": schedule.hard_conflicts,
                "soft_conflicts": schedule.soft_conflicts,
                "unplaced": schedule.unplaced,
                "score": schedule.score,
            },
            reason=reason,
            created_by=actor,
            is_published=is_published,
        )

    def to_entries(self, schedule):
        """Recreate ScheduleEntry rows from this snapshot (deletes current)."""
        from academics.models import CourseOffering

        schedule.entries.all().delete()
        for data in self.snapshot:
            offering = CourseOffering.objects.filter(
                pk=data.get("offering_id")
            ).select_related("course").first()
            if offering is None:
                continue
            room = None
            if data.get("room_id"):
                room = Room.objects.filter(pk=data["room_id"]).first()
            lecturer = None
            if data.get("lecturer_id"):
                lecturer = settings.AUTH_USER_MODEL._default_manager.filter(
                    pk=data["lecturer_id"]
                ).first()
            try:
                start = dt_time.fromisoformat(str(data["start"]))
                end = dt_time.fromisoformat(str(data["end"]))
            except (KeyError, ValueError):
                continue
            schedule.entries.create(
                offering=offering,
                course=offering.course,
                lecturer=lecturer,
                room=room,
                session_type=data.get("session_type", SessionType.LECTURE),
                day=data["day"],
                start_time=start,
                end_time=end,
                lecturer_name=data.get("lecturer_name", ""),
                room_name=data.get("room_name", ""),
                is_locked=bool(data.get("locked", False)),
            )


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULE ENTRY
# ─────────────────────────────────────────────────────────────────────────────

class ScheduleEntry(TimeStampedModel):
    schedule = models.ForeignKey(
        AcademicSchedule, on_delete=models.CASCADE,
        related_name="entries", verbose_name=_("schedule"),
    )
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.CASCADE,
        related_name="scheduling_entries", verbose_name=_("course offering"),
    )
    course = models.ForeignKey(
        "academics.Course", on_delete=models.CASCADE,
        related_name="scheduling_entries", verbose_name=_("course"),
    )
    lecturer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_lectures",
        verbose_name=_("lecturer"),
    )
    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_uses",
        verbose_name=_("room"),
    )
    session_type = models.CharField(
        _("session type"), max_length=15,
        choices=SessionType.choices, default=SessionType.LECTURE,
    )
    day = models.PositiveSmallIntegerField(_("day"), choices=IsoWeekday.choices)
    start_time = models.TimeField(_("start time"))
    end_time = models.TimeField(_("end time"))
    lecturer_name = models.CharField(
        _("lecturer name (snapshot)"), max_length=120, blank=True, default="",
    )
    room_name = models.CharField(
        _("room name (snapshot)"), max_length=160, blank=True, default="",
    )
    cohort_signature = models.CharField(
        _("cohort signature"), max_length=64, blank=True, default="",
        db_index=True,
        help_text=_("Hash of the enrolled departments+level so cohort clashes "
                    "can be detected without student-table joins."),
    )
    is_locked = models.BooleanField(_("locked"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_entries_created",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("schedule entry")
        verbose_name_plural = _("schedule entries")
        ordering = ["day", "start_time", "course__code"]
        indexes = [
            models.Index(fields=["schedule", "day", "start_time"], name="sched_day_start_idx"),
            models.Index(fields=["schedule", "offering"], name="sched_offering_idx"),
            models.Index(fields=["schedule", "room"], name="sched_room_idx"),
            models.Index(fields=["schedule", "lecturer"], name="sched_lecturer_idx"),
        ]

    def __str__(self):
        return (
            f"{self.course.code} | {self.get_day_display()} "
            f"{self.start_time:%H:%M}–{self.end_time:%H:%M} "
            f"({self.get_session_type_display()})"
        )

    def save(self, *args, **kwargs):
        if not self.course_id:
            self.course_id = self.offering.course_id
        if not self.lecturer_name and self.lecturer_id:
            self.lecturer_name = self.lecturer.get_full_name()
        if not self.room_name and self.room_id:
            self.room_name = str(self.room)
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# GENERATION JOB
# ─────────────────────────────────────────────────────────────────────────────

class ScheduleGenerationJob(TimeStampedModel):
    schedule = models.ForeignKey(
        AcademicSchedule, on_delete=models.CASCADE,
        related_name="generation_jobs", verbose_name=_("schedule"),
    )
    status = models.CharField(
        _("status"), max_length=15,
        choices=JobStatus.choices, default=JobStatus.PENDING, db_index=True,
    )
    request_options = models.JSONField(_("request options"), default=dict, blank=True)
    result_stats = models.JSONField(_("result statistics"), default=dict, blank=True)
    error_message = models.TextField(_("error message"), blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduling_jobs_requested",
        verbose_name=_("requested by"),
    )
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)

    class Meta:
        verbose_name = _("schedule generation job")
        verbose_name_plural = _("schedule generation jobs")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Job #{self.pk} — {self.schedule.name} ({self.get_status_display()})"

    def start(self):
        self.status = JobStatus.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at", "updated_at"])

    def complete(self, stats):
        self.status = JobStatus.COMPLETED
        self.result_stats = stats
        self.finished_at = timezone.now()
        error_message = ""
        self.save(update_fields=["status", "result_stats", "finished_at", "updated_at"])

    def fail(self, message):
        self.status = JobStatus.FAILED
        self.error_message = message
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error_message", "finished_at", "updated_at"])