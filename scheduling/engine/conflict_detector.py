"""
scheduling.engine.conflict_detector

Full-schedule conflict sweep and statistics refresh.

Called after generation, after manual edits, and before publish.
"""

from django.db import transaction

from scheduling.engine.validators import validate_entry
from scheduling.models import (
    ScheduleEntry,
    SchedulingConfig,
    LecturerUnavailability,
)


def detect_conflicts(schedule, config=None):
    """
    Scan every entry in `schedule` and refresh the schedule's aggregate
    conflict fields: hard_conflicts, soft_conflicts, unplaced, score.

    Returns dict of summary stats.
    """
    from scheduling.engine.scoring import score_schedule

    if config is None:
        config_obj = SchedulingConfig.for_institution(schedule.institution)
    else:
        config_obj = config

    entries = list(
        ScheduleEntry.objects
        .filter(schedule=schedule)
        .select_related("course", "room", "lecturer", "offering")
    )

    hard_total = 0
    soft_total = 0

    # Batch-load unavailability windows per lecturer
    lecturer_ids = {e.lecturer_id for e in entries if e.lecturer_id}
    unavail_qs = (
        LecturerUnavailability.objects
        .filter(lecturer_id__in=lecturer_ids, is_active=True)
        .values_list("lecturer_id", "day", "start_time", "end_time")
    )
    unavail_map = {}
    for lid, day, start, end in unavail_qs:
        unavail_map.setdefault(lid, []).append(
            {"day": day, "start_time": start, "end_time": end}
        )

    # A pairwise conflict (entry A ↔ entry B) is reported from BOTH sides by the
    # per-entry validator, which historically doubled the aggregate counts.
    # We therefore only count one violation per unique (kind, entry pair).
    seen_keys = set()

    def first_seen(kind, entry_id, entry_pk):
        if entry_id is not None:
            key = (kind, frozenset((entry_pk, entry_id)))
        else:
            key = (kind, entry_pk)
        if key in seen_keys:
            return False
        seen_keys.add(key)
        return True

    for entry in entries:
        unavail = unavail_map.get(entry.lecturer_id, [])
        hard, soft = validate_entry(
            schedule,
            entry.offering,
            entry.day,
            entry.start_time,
            entry.end_time,
            config_obj,
            entry.session_type,
            entry.room_id,
            entry.lecturer_id,
            entry.cohort_signature,
            exclude_entry_id=entry.pk,
            unavail_windows=unavail,
        )
        hard_total += sum(
            1 for v in hard if first_seen(v["kind"], v.get("entry_id"), entry.pk)
        )
        soft_total += sum(
            1 for v in soft if first_seen(v["kind"], v.get("entry_id"), entry.pk)
        )

    score = score_schedule(entries, config_obj)

    with transaction.atomic():
        schedule.hard_conflicts = hard_total
        schedule.soft_conflicts = soft_total
        schedule.score = score
        schedule.save(update_fields=[
            "hard_conflicts", "soft_conflicts", "score", "updated_at",
        ])

    return {
        "hard_conflicts": hard_total,
        "soft_conflicts": soft_total,
        "score": score,
        "entry_count": len(entries),
    }


def explain_entry(schedule, entry_id):
    """
    Generate a human-readable explanation of a single entry's validity.

    Returns a dict:
        {
            "entry_id": ...,
            "hard_violations": [...],
            "soft_violations": [...],
            "all_clear": bool,
        }
    """
    from scheduling.models import LecturerUnavailability

    try:
        entry = ScheduleEntry.objects.select_related(
            "course", "room", "lecturer", "offering",
        ).get(pk=entry_id, schedule=schedule)
    except ScheduleEntry.DoesNotExist:
        return {"error": "Entry not found."}

    config = SchedulingConfig.for_institution(schedule.institution)
    unavail = list(
        LecturerUnavailability.objects
        .filter(lecturer=entry.lecturer, day=entry.day, is_active=True)
        .values("day", "start_time", "end_time")
    )

    hard, soft = validate_entry(
        schedule,
        entry.offering,
        entry.day,
        entry.start_time,
        entry.end_time,
        config,
        entry.session_type,
        entry.room_id,
        entry.lecturer_id,
        entry.cohort_signature,
        exclude_entry_id=entry.pk,
        unavail_windows=unavail,
    )

    return {
        "entry_id": entry.pk,
        "hard_violations": hard,
        "soft_violations": soft,
        "all_clear": not hard and not soft,
    }
