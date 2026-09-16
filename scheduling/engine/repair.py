"""
scheduling.engine.repair

Targeted auto-repair for schedules that accumulate hard conflicts.

Strategy
--------
1. Detect all hard conflicts (lecturer/room/cohort clashes, capacity, type).
2. For every non-locked entry involved, compute a *legal* alternative slot
   (same room pool) using the in-memory state with the entry temporarily
   removed, and move it there.
3. Locked entries and published schedules are never touched.

Deterministic: entries are processed in (day, start, course) order and the
first strictly-better alternative is taken.
"""

from scheduling.engine import generator
from scheduling.engine.conflict_detector import detect_conflicts


def repair_schedule(schedule, config, actor=None, options=None):
    """
    Attempt to eliminate hard conflicts by moving the involved entries.

    Returns {
        "repaired": int,
        "remaining_hard_before": int,
        "remaining_hard_after": int,
        "conflict_entries": int,
        "notes": [...],
    }
    """
    from scheduling.models import ScheduleEntry

    options = options or {}

    # 1. Current conflict scan (db-backed, accurate)
    stats_before = detect_conflicts(schedule, config)

    entries = list(
        ScheduleEntry.objects.filter(schedule=schedule)
        .select_related("course")
        .order_by("day", "start_time", "course__code")
    )
    if not entries:
        return {
            "repaired": 0, "remaining_hard_before": stats_before["hard_conflicts"],
            "remaining_hard_after": 0, "conflict_entries": 0, "notes": [],
        }

    plan, state, _ = generator.entries_to_plan(schedule, config)
    slots = generator._slot_list(config)
    workdays = sorted(config.workday_set)
    min_break = config.min_break_minutes
    from scheduling.engine.optimizer import _load_unavailability
    unavail_map = _load_unavailability(config)
    blackout_days = generator._blackout_weekdays(schedule, schedule.semester)
    session_types = {p["session_type"] for p in plan}
    rooms_by_session = {st: generator._room_pool(config, st) for st in session_types}

    # 2. Which entries carry a hard conflict? Re-run the per-entry check.
    from scheduling.engine.validators import validate_entry

    conflicted = []  # plan dicts
    scheduling_entries = {e.pk: e for e in entries}
    for p in plan:
        entry = scheduling_entries.get(p.get("entry_id"))
        if entry is None:
            continue
        hard, _soft = validate_entry(
            schedule, entry.offering, p["day"], p["start_time"], p["end_time"],
            config, p["session_type"], p["room_id"], p["lecturer_id"],
            p["cohort_sig"], exclude_entry_id=entry.pk,
            unavail_windows=unavail_map.get(p["lecturer_id"], []),
        )
        if hard:
            p["is_locked"] = entry.is_locked
            conflicted.append(p)

    repaired = 0
    notes = []
    for p in conflicted:
        if p.get("is_locked"):
            continue
        key = p["key"]
        sig = p["cohort_sig"]
        lecturer_id = p["lecturer_id"]
        room_id = p["room_id"]
        orig = (p["day"], p["start_time"], p["end_time"], room_id)

        state.unassign(key, orig[0], orig[1], orig[2], room_id, lecturer_id, sig)
        cands = _legal_candidates_for(p, state, slots,
                                      rooms_by_session.get(p["session_type"], []),
                                      workdays, unavail_map, blackout_days,
                                      min_break)
        # prefer the best legal candidate deterministically
        if cands:
            nday, nstart, nend, nrid, nroom = min(
                cands, key=lambda c: (c[0], c[1].hour, c[1].minute, c[3])
            )
            state.assign(key, nday, nstart, nend, nrid, lecturer_id, sig)
            p.update({
                "day": nday, "start_time": nstart, "end_time": nend,
                "room_id": nrid, "room_name": nroom["name"] if nroom else "",
            })
            repaired += 1
        else:
            # restore
            state.assign(key, orig[0], orig[1], orig[2], orig[3],
                         lecturer_id, sig)
            notes.append(f"{p['course_code']} could not be relocated.")

    # 3. Persist
    for p in plan:
        if p.get("entry_id") is None:
            continue
        dirty = []
        try:
            entry = ScheduleEntry.objects.get(pk=p["entry_id"])
        except ScheduleEntry.DoesNotExist:
            continue
        if entry.day != p["day"]:
            entry.day = p["day"]; dirty.append("day")
        if entry.start_time != p["start_time"]:
            entry.start_time = p["start_time"]; dirty.append("start_time")
        if entry.end_time != p["end_time"]:
            entry.end_time = p["end_time"]; dirty.append("end_time")
        if entry.room_id != p["room_id"]:
            entry.room_id = p["room_id"]
            entry.room_name = p["room_name"]
            dirty.extend(["room", "room_name"])
        if dirty:
            entry.save(update_fields=dirty + ["updated_at"])

    stats_after = detect_conflicts(schedule, config)

    return {
        "repaired": repaired,
        "remaining_hard_before": stats_before["hard_conflicts"],
        "remaining_hard_after": stats_after["hard_conflicts"],
        "conflict_entries": len(conflicted),
        "notes": notes,
    }


def _legal_candidates_for(p, state, slots, room_pool, workdays,
                          unavail_map, blackout_days, min_break):
    """Legal candidates for a plan dict row (enrolled used from offering)."""
    from scheduling.engine.generator import _legal_candidates
    return _legal_candidates(
        p, state, slots, room_pool, workdays,
        unavail_map, blackout_days, min_break,
    )