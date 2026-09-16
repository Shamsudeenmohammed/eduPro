"""
scheduling.engine.scoring

Quality scoring for generated schedules.

Objectives (all soft — rewarded, never enforced):
  * respect the institution's morning/evening boundaries
  * spread sessions across the week
  * avoid long consecutive runs
  * use rooms matching their declared type
  * avoid very early/late slots

Score is 100 − Σ penalties, clamped at 0.  Deterministic for identical input.
"""

from collections import defaultdict


def _minutes(start, end):
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def score_schedule(entries, config):
    """
    entries — iterable of objects with .day .start_time .end_time
              .cohort_signature .session_type (ScheduleEntry instances are fine)
    config  — SchedulingConfig instance
    """
    if not entries:
        return 0.0

    penalties = 0.0

    coeff_early = 0.5      # per minute before morning_start
    coeff_late = 1.0       # per minute after evening_start
    coeff_run = 2.0        # per consecutive session above limit
    coeff_spread = 4.0     # per missing day vs target for a cohort
    coeff_type = 2.0       # lab in a non-lab room

    morning = config.morning_start
    evening = config.evening_start
    max_run = config.max_consecutive_per_day
    target_days = config.class_target_days_per_week

    morning_min = morning.hour * 60 + morning.minute
    evening_min = evening.hour * 60 + evening.minute

    # ── per entry time-of-day penalties ────────────────────────────────
    for e in entries:
        start_min = e.start_time.hour * 60 + e.start_time.minute
        end_min = e.end_time.hour * 60 + e.end_time.minute

        if start_min < morning_min:
            penalties += coeff_early * (morning_min - start_min) / 60.0
        if end_min > evening_min:
            penalties += coeff_late * (end_min - evening_min) / 60.0

        if getattr(e, "session_type", None) == "lab" and not _room_is_lab(e):
            penalties += coeff_type

    # ── per cohort structure penalties ─────────────────────────────────
    by_cohort_day = defaultdict(list)          # (sig, day) -> [entries]
    days_per_cohort = defaultdict(set)         # sig -> {day}
    for e in entries:
        sig = e.cohort_signature or f"*{getattr(e, 'offering_id', 0)}"
        by_cohort_day[(sig, e.day)].append(e)
        days_per_cohort[sig].add(e.day)

    for (sig, day), day_entries in by_cohort_day.items():
        day_entries.sort(key=lambda e: (e.start_time, e.course.code))
        run = 1
        for prev, cur in zip(day_entries, day_entries[1:]):
            gap = _minutes(prev.end_time, cur.start_time)
            if 0 <= gap <= 15:
                run += 1
            else:
                run = 1
            if run > max_run:
                penalties += coeff_run * (run - max_run)

    for sig, days in days_per_cohort.items():
        if len(days) < target_days:
            penalties += coeff_spread * (target_days - len(days))

    return round(max(0.0, 100.0 - penalties), 2)


def _room_is_lab(entry):
    room = getattr(entry, "room", None)
    if room is None:
        return False
    return getattr(room, "room_type", None) == "lab"