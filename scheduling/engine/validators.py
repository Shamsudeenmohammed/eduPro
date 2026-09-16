"""
scheduling.engine.validators

Single-entry validation helpers used by the generator and by the views.

Public API:
    validate_entry(schedule, entry_data, config, unavail, blackouts) -> (list, list)
        returns (hard_violations, soft_violations)

    validate_schedule(schedule) -> (hard, soft)
        re-runs the full validation on a persisted schedule

    get_or_none(model, **kw) -> instance | None
"""

from scheduling.engine.constraints import (
    check_lecturer_double_booked,
    check_lecturer_unavailable,
    check_blackout,
    check_cohort_clash,
    check_daily_session_limit,
    check_early_morning,
    check_late_evening,
    check_minimum_break,
    check_room_capacity,
    check_room_double_booked,
    check_room_type,
    check_workday,
)


def get_or_none(model_or_path, **kwargs):
    """Generic get-or-None helper for the engine."""
    if isinstance(model_or_path, str):
        from importlib import import_module
        app_label, model_name = model_or_path.split(".")
        models = import_module(f"{app_label}.models")
        model = getattr(models, model_name)
    else:
        model = model_or_path
    try:
        return model.objects.get(**kwargs)
    except model.DoesNotExist:
        return None


def validate_entry(schedule, offering, day, start, end,
                   config, session_type, room_id, lecturer_id,
                   cohort_sig, exclude_entry_id=None,
                   unavail_windows=None, blackout_dates=None):
    """
    Validate a single entry candidate against every constraint.

    Returns:
        (hard_violations, soft_violations)  — lists of violation dicts
    """
    hard = []
    soft = []

    workdays = config.workday_set
    unavail_windows = unavail_windows or []
    blackout_dates = blackout_dates or []

    # ── hard checks ──────────────────────────────────────────────────────
    hard += check_workday(day, workdays)
    hard += check_blackout(schedule, day, start, end, blackout_dates)
    hard += check_lecturer_unavailable(day, start, end, lecturer_id, unavail_windows)
    hard += check_room_capacity(room_id, session_type, offering.enrolled_count)
    hard += check_room_type(room_id, session_type)
    hard += check_lecturer_double_booked(
        schedule, day, start, end,
        exclude_entry_id=exclude_entry_id,
        lecturer_ids=[lecturer_id] if lecturer_id else [],
    )
    hard += check_room_double_booked(
        schedule, day, start, end,
        exclude_entry_id=exclude_entry_id, room_id=room_id,
    )
    hard += check_cohort_clash(
        schedule, day, start, end,
        exclude_entry_id=exclude_entry_id, cohort_signature=cohort_sig,
    )
    hard += check_daily_session_limit(
        schedule, day, cohort_sig,
        max_sessions=config.max_lectures_per_day,
        exclude_entry_id=exclude_entry_id,
    )
    hard += check_minimum_break(
        schedule, day, start, end,
        exclude_entry_id=exclude_entry_id,
        cohort_signature=cohort_sig,
        min_break_minutes=config.min_break_minutes,
    )

    # ── soft checks ──────────────────────────────────────────────────────
    soft += check_early_morning(day, start, config.morning_start)
    soft += check_late_evening(day, start, end, config.evening_start)

    return hard, soft


def validate_schedule(schedule):
    """Full re-validation of all persisted entries.  Returns counts."""
    from scheduling.models import ScheduleEntry
    entries = schedule.entries.select_related("course", "room", "lecturer").all()
    hard_total = 0
    soft_total = 0
    for entry in entries:
        from scheduling.models import (
            LecturerUnavailability,
            SchedulingConfig,
        )
        cfg = SchedulingConfig.for_institution(schedule.institution)
        unavail = list(
            LecturerUnavailability.objects
            .filter(lecturer=entry.lecturer, day=entry.day, is_active=True)
            .values("day", "start_time", "end_time")
        )
        hard, soft = validate_entry(
            schedule, entry.offering, entry.day,
            entry.start_time, entry.end_time,
            cfg, entry.session_type, entry.room_id, entry.lecturer_id,
            entry.cohort_signature, exclude_entry_id=entry.pk,
            unavail_windows=unavail,
        )
        hard_total += len(hard)
        soft_total += len(soft)
    return hard_total, soft_total