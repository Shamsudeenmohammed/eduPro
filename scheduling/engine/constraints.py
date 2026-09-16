"""
scheduling.engine.constraints

Constraint evaluation for a single ScheduleEntry candidate against existing entries.

Every public function returns a list of violation dicts:

    {
        "severity": "hard" | "soft",
        "kind":     <ConflictKind>,      # from scheduling.constants
        "message":  <str>,
        "entry_id": <int | None>,
        "fix_hint": <str>,              # plain-text advice for the UI
    }

The engine never raises for constraint violations — it accumulates results so
the UI can display *all* problems simultaneously.
"""

from scheduling.constants import ConflictKind, ConflictSeverity
from scheduling.models import (
    Room,
    ScheduleEntry,
    times_overlap,
)


HARD = ConflictSeverity.HARD
SOFT = ConflictSeverity.SOFT


# ── Helpers ───────────────────────────────────────────────────────────────────

def _violation(severity, kind, message, entry_id=None, fix_hint=""):
    return {
        "severity": severity,
        "kind": kind.value,
        "message": message,
        "entry_id": entry_id,
        "fix_hint": fix_hint,
    }


def _time_delta_minutes(a, b):
    from scheduling.models import _as_timedelta as _td
    return int((_td(b) - _td(a)).total_seconds() // 60)


def _same_group_sig(entry_a, entry_b):
    return (entry_a.cohort_signature
            and entry_b.cohort_signature
            and entry_a.cohort_signature == entry_b.cohort_signature)


# ── Hard constraints ──────────────────────────────────────────────────────────

def check_lecturer_double_booked(schedule, day, start, end,
                                 exclude_entry_id=None, lecturer_ids=None):
    """Lecturer is already teaching at this time."""
    conflicts = []
    existing = (
        ScheduleEntry.objects
        .filter(schedule=schedule, day=day, lecturer_id__in=lecturer_ids)
        .exclude(pk=exclude_entry_id)
    )
    for e in existing:
        if times_overlap(start, end, e.start_time, e.end_time):
            conflicts.append(_violation(
                HARD, ConflictKind.LECTURER_BUSY,
                f"Lecturer is already scheduled for {e.course.code} "
                f"({e.get_day_display()} {e.start_time:%H:%M}–{e.end_time:%H:%M}).",
                entry_id=e.pk,
                fix_hint="Swap the lecturer or move this session to a free slot.",
            ))
    return conflicts


def check_room_double_booked(schedule, day, start, end,
                             exclude_entry_id=None, room_id=None):
    """Room is already in use at this time."""
    if room_id is None:
        return []
    existing = (
        ScheduleEntry.objects
        .filter(schedule=schedule, day=day, room_id=room_id)
        .exclude(pk=exclude_entry_id)
    )
    conflicts = []
    for e in existing:
        if times_overlap(start, end, e.start_time, e.end_time):
            conflicts.append(_violation(
                HARD, ConflictKind.ROOM_BUSY,
                f"Room already assigned to {e.course.code} "
                f"({e.get_day_display()} {e.start_time:%H:%M}–{e.end_time:%H:%M}).",
                entry_id=e.pk,
                fix_hint="Use a different room or reschedule.",
            ))
    return conflicts


def check_cohort_clash(schedule, day, start, end,
                       exclude_entry_id=None, cohort_signature=None):
    """Cohort (group of students) is in another class at this time."""
    if not cohort_signature:
        return []
    existing = (
        ScheduleEntry.objects
        .filter(schedule=schedule, day=day, cohort_signature=cohort_signature)
        .exclude(pk=exclude_entry_id)
    )
    conflicts = []
    for e in existing:
        if times_overlap(start, end, e.start_time, e.end_time):
            conflicts.append(_violation(
                HARD, ConflictKind.GROUP_CLASH,
                f"Cohort clash — overlapping with {e.course.code} "
                f"({e.get_day_display()} {e.start_time:%H:%M}–{e.end_time:%H:%M}).",
                entry_id=e.pk,
                fix_hint="Move one of the two sessions to a free slot.",
            ))
    return conflicts


def check_room_capacity(room_id, session_type, num_students=0):
    """Room is too small for the required group."""
    if room_id is None:
        return []
    try:
        room = Room.objects.get(pk=room_id)
    except Room.DoesNotExist:
        return [_violation(
            HARD, ConflictKind.WRONG_ROOM_TYPE,
            "Selected room no longer exists.",
        )]
    if session_type == "exam" and room.exam_capacity < num_students:
        return [_violation(
            HARD, ConflictKind.CAPACITY,
            f"Exam capacity ({room.exam_capacity}) exceeded by {num_students} students.",
            fix_hint="Select a room with a larger exam capacity.",
        )]
    if session_type != "exam" and room.capacity < num_students:
        return [_violation(
            HARD, ConflictKind.CAPACITY,
            f"Room capacity ({room.capacity}) exceeded by {num_students} students.",
            fix_hint="Select a larger room or split the cohort.",
        )]
    return []


def check_room_type(room_id, session_type):
    """Room type is inappropriate for the session type."""
    if room_id is None:
        return []
    try:
        room = Room.objects.get(pk=room_id)
    except Room.DoesNotExist:
        return []
    type_map = {
        "lecture": ("lecture", "seminar", "exam"),
        "lab":     ("lab",),
        "exam":    ("exam", "lecture"),
    }
    allowed = type_map.get(session_type, ())
    if room.room_type not in allowed:
        return [_violation(
            HARD, ConflictKind.WRONG_ROOM_TYPE,
            f"Room type '{room.get_room_type_display()}' is not suitable for "
            f"a {session_type} session.",
            fix_hint="Select a room of the correct type.",
        )]
    return []


def check_workday(day, workdays):
    """Slot falls on a non-working day."""
    if day not in workdays:
        return [_violation(
            HARD, ConflictKind.NON_WORKDAY,
            f"Day {day} is not a working day.",
            fix_hint="Move this session to a working day.",
        )]
    return []


def check_blackout(schedule, day, start, end,
                   blackout_dates, exclude_entry_id=None):
    """Session falls during a blackout period (holiday / exam window)."""
    if not blackout_dates:
        return []
    return [_violation(
        HARD, ConflictKind.BLACKOUT,
        "Session overlaps with an academic calendar blackout period.",
        fix_hint="Schedule outside the blackout window.",
    )]


def check_lecturer_unavailable(day, start, end, lecturer_id, unavail_windows):
    """Lecturer has declared unavailability at this time."""
    for w in unavail_windows:
        if w["day"] == day and times_overlap(start, end, w["start_time"], w["end_time"]):
            return [_violation(
                HARD, ConflictKind.LECTURER_UNSURE,
                "Lecturer has declared this window as unavailable.",
                fix_hint="Use a different lecturer or move to their available slot.",
            )]
    return []


def check_daily_session_limit(schedule, day, cohort_signature,
                              max_sessions, exclude_entry_id=None):
    """Too many sessions for this cohort on the same day."""
    if not cohort_signature:
        return []
    count = ScheduleEntry.objects.filter(
        schedule=schedule, day=day, cohort_signature=cohort_signature,
    ).exclude(pk=exclude_entry_id).count()
    if count >= max_sessions:
        return [_violation(
            HARD, ConflictKind.LECTURER_DAY_LIMIT,
            f"Cohort already has {count} sessions on this day (limit {max_sessions}).",
            fix_hint="Move this session to another day.",
        )]
    return []


def check_minimum_break(schedule, day, start, end,
                        exclude_entry_id=None, cohort_signature=None,
                        min_break_minutes=15):
    """Consecutive sessions are too close without a minimum break."""
    if not cohort_signature:
        return []
    adjacent = (
        ScheduleEntry.objects
        .filter(schedule=schedule, day=day, cohort_signature=cohort_signature)
        .exclude(pk=exclude_entry_id)
    )
    violations = []
    for e in adjacent:
        gap = _time_delta_minutes(end, e.start_time) if e.start_time >= end else _time_delta_minutes(e.end_time, start)
        if 0 <= gap < min_break_minutes:
            violations.append(_violation(
                HARD, ConflictKind.BREAK_VIOLATION,
                f"Gap of {gap} min with {e.course.code} is below the "
                f"{min_break_minutes} min minimum break.",
                entry_id=e.pk,
                fix_hint="Increase the gap between sessions.",
            ))
    return violations


# ── Soft constraints ──────────────────────────────────────────────────────────

def check_early_morning(day, start, morning_start):
    """Session starts before the institution's morning start."""
    if start < morning_start:
        return [_violation(
            SOFT, ConflictKind.EARLY_OR_LATE,
            f"Session starts at {start:%H:%M}, before morning start "
            f"({morning_start:%H:%M}).",
            fix_hint="Move to a later slot.",
        )]
    return []


def check_late_evening(day, start, end, evening_start):
    """Session extends past the institution's evening start."""
    if end > evening_start:
        return [_violation(
            SOFT, ConflictKind.EARLY_OR_LATE,
            f"Session ends at {end:%H:%M}, after evening start "
            f"({evening_start:%H:%M}).",
            fix_hint="Move to an earlier slot.",
        )]
    return []


def check_consecutive_load(schedule, day, cohort_signature,
                           max_consecutive, exclude_entry_id=None):
    """Too many consecutive sessions for the cohort in one day."""
    if not cohort_signature:
        return []
    day_entries = list(
        ScheduleEntry.objects
        .filter(schedule=schedule, day=day, cohort_signature=cohort_signature)
        .exclude(pk=exclude_entry_id)
        .order_by("start_time")
    )
    if not day_entries:
        return []
    run = 1
    worst = 1
    for i in range(1, len(day_entries)):
        gap = _time_delta_minutes(day_entries[i - 1].end_time, day_entries[i].start_time)
        if 0 <= gap <= 15:
            run += 1
            worst = max(worst, run)
        else:
            run = 1
    if worst > max_consecutive:
        return [_violation(
            SOFT, ConflictKind.CONCENTRATED,
            f"Cohort has {worst} consecutive sessions (soft limit {max_consecutive}).",
            fix_hint="Introduce a break in the middle of the day.",
        )]
    return []


def check_day_spread(schedule, cohort_signature, target_days,
                     exclude_entry_id=None):
    """Sessions for a cohort are concentrated on fewer days than ideal."""
    if not cohort_signature:
        return []
    days_used = (
        ScheduleEntry.objects
        .filter(schedule=schedule, cohort_signature=cohort_signature)
        .exclude(pk=exclude_entry_id)
        .values_list("day", flat=True)
        .distinct()
        .count()
    )
    if days_used < target_days:
        return [_violation(
            SOFT, ConflictKind.SPREAD,
            f"Cohort's classes span {days_used} day(s) but target is {target_days}.",
            fix_hint="Spread sessions across more days.",
        )]
    return []
