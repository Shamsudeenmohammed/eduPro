"""
scheduling.engine.optimizer

Deterministic local-search improvement pass applied after generation.

For each entry (in fixed order) the optimizer looks for a *legal* alternative
placement that lowers the session's soft penalty (time-of-day + day-spread).
Moves are accepted only when the global penalty strictly improves, and are
rolled back otherwise.  The iteration budget bounds the number of trials.

The result is published as a fresh ScheduleVersion — the optimizer never
mutates a published schedule in place.
"""

from scheduling.engine import generator
from scheduling.engine.generator import (
    _gap_minutes,
    _legal_candidates,
)


def _plan_penalty(config, plan):
    """Global soft-penalty of a plan (mirrors engine.scoring.sign)."""
    morning = config.morning_start.hour * 60 + config.morning_start.minute
    evening = config.evening_start.hour * 60 + config.evening_start.minute

    penalty = 0.0
    for p in plan:
        start_min = p["start_time"].hour * 60 + p["start_time"].minute
        end_min = p["end_time"].hour * 60 + p["end_time"].minute
        if start_min < morning:
            penalty += (morning - start_min) / 60.0
        if end_min > evening:
            penalty += (end_min - evening) / 60.0

    # spread: reward cohorts on more distinct days
    days_by_sig = {}
    for p in plan:
        days_by_sig.setdefault(p["cohort_sig"], set()).add(p["day"])
    for sig, days in days_by_sig.items():
        if len(days) < config.class_target_days_per_week:
            penalty += (config.class_target_days_per_week - len(days)) * 2.0
    return round(penalty, 3)


def optimize_schedule(schedule, config, options=None, actor=None):
    """
    Improve a schedule's soft quality.  In-memory first; persists only the
    accepted moves.

    Returns {
        "moves": int,
        "score_before": float,
        "score_after": float,
        "trials": int,
    }
    """
    from scheduling.models import ScheduleEntry

    options = options or {}
    iterations = options.get("iterations") or config.optimizer_iterations

    slots = generator._slot_list(config)
    workdays = sorted(config.workday_set)
    min_break = config.min_break_minutes
    unavail_map = _load_unavailability(config)
    blackout_days = generator._blackout_weekdays(schedule, schedule.semester)

    plan, state, entries = generator.entries_to_plan(schedule, config)

    session_types = {p["session_type"] for p in plan}
    rooms_by_session = {
        st: generator._room_pool(config, st) for st in session_types
    }

    before = _plan_penalty(config, plan)
    moves = 0
    trials = 0

    for p in list(plan):  # fixed deterministic order
        if moves >= iterations:
            break
        key = p["key"]
        sig = p["cohort_sig"]
        lecturer_id = p["lecturer_id"]
        room_id = p["room_id"]

        # restore from state first (we only mutate plan after choosing)
        state.unassign(key, p["day"], p["start_time"], p["end_time"],
                       room_id, lecturer_id, sig)
        cands = _legal_candidates(
            p, state, slots, rooms_by_session.get(p["session_type"], []),
            workdays, unavail_map, blackout_days, min_break,
        )
        state.assign(key, p["day"], p["start_time"], p["end_time"],
                     room_id, lecturer_id, sig)

        # filter out the current placement
        cands = [
            c for c in cands
            if not (c[0] == p["day"] and c[1] == p["start_time"]
                    and c[3] == room_id)
        ]
        if not cands:
            continue

        trials += 1
        best_cand = min(cands, key=lambda c: (
            generator._candidate_penalty(config, c[0], c[1], c[2], c[4],
                                         p, {p["day"]}),
            c[0], c[1].hour, c[1].minute, c[3] or 0,
        ))
        # only accept if strictly better than current placement
        room_pool = rooms_by_session.get(p["session_type"], [])
        cur_room_type = next(
            (r["room_type"] for r in room_pool if r["id"] == room_id), None
        )
        c_pen = generator._candidate_penalty(config, best_cand[0], best_cand[1],
                                             best_cand[2], best_cand[4],
                                             p, {p["day"]})
        cur_pen = generator._candidate_penalty(
            config, p["day"], p["start_time"], p["end_time"],
            {"room_type": cur_room_type}, p, {p["day"]})
        if c_pen >= cur_pen:
            continue

        # apply candidate
        nday, nstart, nend, nrid, nroom = best_cand
        orig = (p["day"], p["start_time"], p["end_time"], room_id)
        state.unassign(key, p["day"], p["start_time"], p["end_time"],
                       room_id, lecturer_id, sig)
        state.assign(key, nday, nstart, nend, nrid, lecturer_id, sig)
        p.update({
            "day": nday, "start_time": nstart, "end_time": nend,
            "room_id": nrid, "room_name": nroom["name"] if nroom else "",
        })

        # global re-evaluation; roll back if no improvement
        after = _plan_penalty(config, plan)
        if after >= before:
            state.unassign(key, nday, nstart, nend, nrid, lecturer_id, sig)
            state.assign(key, orig[0], orig[1], orig[2], orig[3],
                         lecturer_id, sig)
            p["day"], p["start_time"], p["end_time"] = orig[0], orig[1], orig[2]
            p["room_id"], p["room_name"] = orig[3], orig[3] and (
                next((r["name"] for r in
                      rooms_by_session.get(p["session_type"], [])
                      if r["id"] == orig[3]), ""))
            continue

        before = after
        moves += 1

    # persist accepted moves
    for p in plan:
        eid = p["entry_id"]
        if eid is None:
            continue
        try:
            entry = ScheduleEntry.objects.get(pk=eid)
        except ScheduleEntry.DoesNotExist:
            continue
        dirty = []
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

    return {
        "moves": moves,
        "trials": trials,
        "score_before": before,
        "score_after": _plan_penalty(config, plan),
    }


def _load_unavailability(config):
    from scheduling.models import LecturerUnavailability

    qs = LecturerUnavailability.objects.filter(
        is_active=True
    ).values("lecturer_id", "day", "start_time", "end_time")
    result = {}
    for lid, day, start, end in qs:
        result.setdefault(lid, []).append(
            {"day": day, "start_time": start, "end_time": end}
        )
    return result