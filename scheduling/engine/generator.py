"""
scheduling.engine.generator

Deterministic constraint-based schedule generator.

Approach
--------
MRV-guided placement over weekly class requirements:

  1. Build "requirements" from the semester's active course offerings
     (lecture + lab hours decomposed into canonical slots).
  2. Build the pool of legal (room, slot) placements.
  3. Repeatedly pick the requirement with the fewest legal candidates
     (Most Constrained / MRV) and commit it to its best-scoring placement.
4. If the chosen requirement has no legal placement it is recorded as
      UNPLACED (with a reason) and the search continues — producing a
      best-effort schedule instead of giving up entirely.
   5. Rooms are optional: when no usable room exists for a slot the session
      may be placed WITHOUT a room, so a timetable is produced even if no
      rooms are configured or none match capacity.  Other hard constraints
      (lecturer busy, cohort clash, …) are never relaxed by this fallback.
   6. Determinism: candidates are always iterated in a fixed order (day, start,
      room id) seeded only by `config.seed` for tie-breaks; never random.

Hard constraints enforced (mirroring engine.constraints):
  workday, room type, capacity, lecturer busy, room busy, cohort clash,
  lecturer unavailability, daily session limit, minimum break, blackout days.

The generator works fully in-memory; the caller persists the resulting plan.
"""

import hashlib
from collections import defaultdict
from datetime import time as dt_time

from django.db.models import Q

from scheduling.constants import SessionType
from scheduling.models import (
    AcademicEvent,
    LecturerUnavailability,
    Room,
    TimeSlot,
    times_overlap,
)

UNPLACED_NO_LEGAL = "NO_LEGAL_PLACEMENT"
UNPLACED_MISSING_LECTURER = "MISSING_LECTURER"
UNPLACED_BUDGET = "GENERATION_BUDGET_EXCEEDED"


class PlanState:
    """In-memory assignment state shared by generator / optimizer / repair."""

    def __init__(self, config):
        self.config = config
        self.lecturer_slots = defaultdict(list)   # (day, lecturer_id) -> [(s,e)]
        self.room_slots = defaultdict(list)       # (day, room_id) -> [(s,e)]
        self.cohort_slots = defaultdict(list)     # (day, sig) -> [(s,e)]
        self.cohort_day_count = defaultdict(int)  # (day, sig) -> count
        self.assignments = {}                     # key -> (day, start, end, room_id)

    @staticmethod
    def _insert(intervals, start, end):
        i = 0
        while i < len(intervals) and intervals[i][0] <= start:
            i += 1
        intervals.insert(i, (start, end))

    @staticmethod
    def _remove(intervals, start, end):
        try:
            intervals.remove((start, end))
        except ValueError:
            pass

    def assign(self, key, day, start, end, room_id, lecturer_id, sig):
        self.assignments[key] = (day, start, end, room_id)
        self._insert(self.cohort_slots[(day, sig)], start, end)
        self.cohort_day_count[(day, sig)] += 1
        if room_id is not None:
            self._insert(self.room_slots[(day, room_id)], start, end)
        if lecturer_id is not None:
            self._insert(self.lecturer_slots[(day, lecturer_id)], start, end)

    def unassign(self, key, day, start, end, room_id, lecturer_id, sig):
        self.assignments.pop(key, None)
        self._remove(self.cohort_slots[(day, sig)], start, end)
        self.cohort_day_count[(day, sig)] = max(
            0, self.cohort_day_count[(day, sig)] - 1
        )
        if room_id is not None:
            self._remove(self.room_slots[(day, room_id)], start, end)
        if lecturer_id is not None:
            self._remove(self.lecturer_slots[(day, lecturer_id)], start, end)


# ── Small time helpers ───────────────────────────────────────────────────────

def _minutes(t):
    return t.hour * 60 + t.minute


def _time_from_minutes(mins):
    return dt_time(mins // 60, mins % 60)


def _gap_minutes(a_end, b_start):
    """Minutes between a session ending at a_end and one starting at b_start."""
    return _minutes(b_start) - _minutes(a_end)


# ── Problem building ─────────────────────────────────────────────────────────

def _lecturer_for_offering(offering):
    alloc = (
        offering.allocations.filter(is_active=True)
        .select_related("teacher")
        .order_by("id")
        .first()
    )
    if alloc is None:
        return None, ""
    return alloc.teacher_id, alloc.teacher.get_full_name()


def _cohort_signature(offering):
    dept_ids = sorted(set(offering.departments.values_list("pk", flat=True)))
    raw = f"{dept_ids}:{offering.level_name or ''}:{offering.level_id or 0}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def _coarse_slots(config):
    """Coarse fallback slot grid (no DB dependency): fixed workday hours."""
    workdays = sorted(config.workday_set) or [0]
    minute = config.default_slot_minutes
    result = []
    for day in workdays:
        cursor = 8 * 60
        while cursor + minute <= 19 * 60:
            result.append({
                "day": day,
                "start_time": _time_from_minutes(cursor),
                "end_time": _time_from_minutes(cursor + minute),
            })
            cursor += minute
    return result


def _slot_list(config):
    """Canonical slots: TimeSlot defaults first, else coarse workday slots."""
    slots = list(
        TimeSlot.objects.filter(is_default=True, is_active=True)
        .order_by("day", "start_time")
        .values("day", "start_time", "end_time")
    )
    if slots:
        return slots
    return _coarse_slots(config)


def _room_pool(config, session_type):
    """Rooms usable for a session type (active + available)."""
    type_map = {
        SessionType.LECTURE: ("lecture", "seminar", "exam"),
        SessionType.LAB: ("lab",),
        SessionType.EXAM: ("exam", "lecture"),
    }
    allowed = type_map.get(session_type, ())
    return list(
        Room.objects.filter(
            is_active=True, is_available=True,
            room_type__in=allowed,
            building__institution=config.institution,
        )
        .order_by("building_id", "code")
        .values("id", "name", "room_type", "capacity", "exam_capacity")
    )


def _blackout_weekdays(schedule, semester):
    """Weekdays wholly blocked for scheduling by all-day calendar events."""
    events = AcademicEvent.objects.filter(
        affects_scheduling=True,
        all_day=True,
    )
    if schedule.institution_id is not None:
        events = events.filter(
            Q(institution=schedule.institution)
            | Q(institution__isnull=True)
        )
    else:
        events = events.filter(institution__isnull=True)
    blocked = set()
    for ev in events:
        end = ev.end_date or ev.start_date
        if ev.start_date > semester.end_date or end < semester.start_date:
            continue
        if ev.recurrence not in ("none", "weekly"):
            continue
        blocked.add(ev.start_date.weekday())
    return blocked


def _build_requirements(schedule, config, options):
    """Expand offerings into individual weekly sessions (requirements)."""
    session_types = options.get("session_types") or [
        SessionType.LECTURE, SessionType.LAB,
    ]
    slot_hours = config.default_slot_minutes / 60.0

    requirements = []
    offerings = (
        schedule.semester.offerings.filter(is_active=True)
        .select_related("course", "level")
        .prefetch_related("departments", "allocations__teacher")
        .order_by("course__code")
    )
    for offering in offerings:
        course = offering.course
        cohort_sig = _cohort_signature(offering)
        enrolled = offering.enrolled_count
        if enrolled < 1:
            enrolled = offering.max_students or 50

        def make(session_type, hours):
            if hours <= 0:
                return
            count = max(1, int(round(hours / slot_hours)))
            lecturer_id, lecturer_name = _lecturer_for_offering(offering)
            for i in range(count):
                requirements.append({
                    "offering_id": offering.pk,
                    "course_id": course.pk,
                    "course_code": course.code,
                    "course_title": course.title,
                    "session_type": session_type,
                    "hours": hours,
                    "lecturer_id": lecturer_id,
                    "lecturer_name": lecturer_name,
                    "cohort_sig": cohort_sig,
                    "enrolled": enrolled,
                    "key": f"{offering.pk}:{session_type}:{i}",
                })

        if SessionType.LECTURE in session_types:
            make(SessionType.LECTURE, course.lecture_hours_per_week)
        if SessionType.LAB in session_types:
            make(SessionType.LAB, course.lab_hours_per_week)

    return requirements


# ── Candidate legality (in-memory) ───────────────────────────────────────────

def _overlaps(intervals, start, end):
    for s, e in intervals:
        if times_overlap(start, end, s, e):
            return True
    return False


def _break_violated(intervals, start, end, min_break):
    for s, e in intervals:
        if start > e and 0 <= _gap_minutes(e, start) < min_break:
            return True
        if s > end and 0 <= _gap_minutes(end, s) < min_break:
            return True
    return False


def _unavailable(day, start, end, lecturer_id, unavail_map):
    if lecturer_id is None:
        return False
    for w in unavail_map.get(lecturer_id, []):
        if w["day"] == day and times_overlap(
                start, end, w["start_time"], w["end_time"]):
            return True
    return False


def _capacity_ok(room, enrolled, session_type):
    if session_type == SessionType.EXAM:
        return room["exam_capacity"] >= enrolled
    return room["capacity"] >= enrolled


def _legal_candidates(requirement, state, slots, room_pool, workdays,
                      unavail_map, blackout_days, min_break):
    """Legal (day, start, end, room_id, room) placements, fixed ordering.

    Rooms are preferred, but when no usable room exists for a slot the
    requirement may still be placed WITHOUT a room (room_id=None, room=None).
    This keeps the generator a true best-effort producer: a timetable is
    always generated even if no rooms are configured or none match capacity.
    """
    sig = requirement["cohort_sig"]
    lecturer_id = requirement["lecturer_id"]
    enrolled = requirement["enrolled"]
    session_type = requirement["session_type"]

    candidates = []
    for slot in slots:
        day = slot["day"]
        if day not in workdays or day in blackout_days:
            continue
        start, end = slot["start_time"], slot["end_time"]

        if _unavailable(day, start, end, lecturer_id, unavail_map):
            continue
        if state.cohort_day_count[(day, sig)] >= state.config.max_lectures_per_day:
            continue
        if _overlaps(state.cohort_slots[(day, sig)], start, end):
            continue
        if _break_violated(state.cohort_slots[(day, sig)], start, end, min_break):
            continue
        # Lecturer busy is a hard constraint: it must skip the whole slot and
        # can NOT fall back to a no-room placement.
        if lecturer_id is not None and _overlaps(
                state.lecturer_slots[(day, lecturer_id)], start, end):
            continue

        slot_candidates = []
        for room in room_pool:
            rid = room["id"]
            if not _capacity_ok(room, enrolled, session_type):
                continue
            if _overlaps(state.room_slots[(day, rid)], start, end):
                continue
            slot_candidates.append((day, start, end, rid, room))

        if slot_candidates:
            candidates.extend(slot_candidates)
        else:
            # Nothing usable in this slot → allow a no-room placement so the
            # session is still part of the timetable.
            candidates.append((day, start, end, None, None))

    candidates.sort(key=lambda c: (c[0], c[1].hour, c[1].minute, c[3] or 0))
    return candidates


def _candidate_penalty(config, day, start, end, room, requirement, used_days):
    """Soft penalty for choosing among legal candidates (lower = better)."""
    morning = _minutes(config.morning_start)
    evening = _minutes(config.evening_start)
    start_min = _minutes(start)
    end_min = _minutes(end)

    penalty = 0.0
    if start_min < morning:
        penalty += (morning - start_min) / 60.0
    if end_min > evening:
        penalty += (end_min - evening) / 60.0
    if day in used_days:
        penalty += 1.0
    if (requirement["session_type"] == SessionType.LAB and room is not None
            and room["room_type"] != "lab"):
        penalty += 2.0
    return round(penalty, 3)


# ── Solver ───────────────────────────────────────────────────────────────────

def solve_plan(requirements, config, options, rooms_by_session, slots,
               unavail_map, blackout_days):
    """Deterministic MRV-guided placement.  Returns (plan, unplaced)."""
    state = PlanState(config)
    workdays = sorted(config.workday_set)
    min_break = config.min_break_minutes
    budget = config.generation_budget or 20000

    expanded = sorted(
        requirements, key=lambda r: (r["course_code"], r["session_type"], r["key"])
    )
    unplaced = []

    while expanded and budget > 0:
        # MRV: pick the requirement with the fewest legal candidates
        best_idx = -1
        best_len = None
        cands_by_idx = {}
        for i, req in enumerate(expanded):
            room_pool = rooms_by_session.get(req["session_type"], [])
            cands = _legal_candidates(
                req, state, slots, room_pool, workdays,
                unavail_map, blackout_days, min_break,
            )
            cands_by_idx[i] = cands
            n = len(cands)
            if best_len is None or n < best_len or (
                    n == best_len and (req["course_code"], req["key"])
                    < (expanded[best_idx]["course_code"], expanded[best_idx]["key"])):
                best_len = n
                best_idx = i

        if best_idx == -1:
            break

        req = expanded[best_idx]
        cands = cands_by_idx[best_idx]

        if not cands:
            reason = UNPLACED_MISSING_LECTURER if req["lecturer_id"] is None \
                else UNPLACED_NO_LEGAL
            unplaced.append({**req, "reason": reason})
            expanded.pop(best_idx)
            continue

        used_days = {
            state.assignments[k][0]
            for k in state.assignments
            if k.startswith(f"{req['offering_id']}:")
        }

        best_cand = None
        best_penalty = None
        for cand in cands:
            day, start, end, rid, room = cand
            penalty = _candidate_penalty(config, day, start, end, room, req,
                                         used_days)
            if best_penalty is None or penalty < best_penalty:
                best_penalty = penalty
                best_cand = cand

        day, start, end, rid, room = best_cand
        budget -= max(1, len(cands))
        state.assign(req["key"], day, start, end, rid,
                     req["lecturer_id"], req["cohort_sig"])
        expanded.pop(best_idx)

    for req in expanded:
        unplaced.append({**req, "reason": UNPLACED_BUDGET})

    plan = []
    for key, (day, start, end, rid) in sorted(
            state.assignments.items(), key=lambda kv: (kv[1][0], kv[1][1])):
        req = next(
            (r for r in requirements if r["key"] == key), None
        )
        if req is None:
            continue
        room = next(
            (rm for rm in rooms_by_session.get(req["session_type"], [])
             if rm["id"] == rid),
            None,
        )
        plan.append({
            "key": key,
            "offering_id": req["offering_id"],
            "course_id": req["course_id"],
            "course_code": req["course_code"],
            "course_title": req["course_title"],
            "lecturer_id": req["lecturer_id"],
            "lecturer_name": req["lecturer_name"],
            "session_type": req["session_type"],
            "day": day,
            "start_time": start,
            "end_time": end,
            "room_id": rid,
            "room_name": room["name"] if room else "",
            "cohort_sig": req["cohort_sig"],
        })

    return plan, unplaced


def run_generation(schedule, config, options=None):
    """
    Full generation pipeline (data load → solve).

    Returns dict with keys: plan, unplaced, placed, total, seed.
    """
    options = options or {}

    requirements = _build_requirements(schedule, config, options)
    slots = _slot_list(config)

    session_types = {r["session_type"] for r in requirements}
    rooms_by_session = {
        st: _room_pool(config, st) for st in session_types
    }

    unavail_qs = LecturerUnavailability.objects.filter(
        is_active=True
    ).values("lecturer_id", "day", "start_time", "end_time")
    unavail_map = defaultdict(list)
    for lid, day, start, end in unavail_qs:
        unavail_map[lid].append({"day": day, "start_time": start, "end_time": end})

    blackout_days = _blackout_weekdays(schedule, schedule.semester)

    plan, unplaced = solve_plan(
        requirements, config, options,
        rooms_by_session, slots,
        dict(unavail_map), blackout_days,
    )

    return {
        "plan": plan,
        "unplaced": unplaced,
        "placed": len(plan),
        "total": len(requirements),
        "seed": options.get("seed") or config.seed,
    }


# ── Persisted-schedule interoperability ──────────────────────────────────────

def entries_to_plan(schedule, config):
    """
    Build a workable (plan, state, entries) triple from a persisted schedule,
    used by the optimizer and the repair engine.
    """
    from scheduling.models import ScheduleEntry

    entries = list(
        ScheduleEntry.objects.filter(schedule=schedule)
        .select_related("course")
        .order_by("day", "start_time", "course__code")
    )
    state = PlanState(config)
    plan = []
    for e in entries:
        sig = e.cohort_signature or f"e{e.pk}"
        d = {
            "key": f"e{e.pk}",
            "entry_id": e.pk,
            "offering_id": e.offering_id,
            "course_id": e.course_id,
            "course_code": e.course.code if e.course_id else "",
            "lecturer_id": e.lecturer_id,
            "lecturer_name": e.lecturer_name,
            "session_type": e.session_type,
            "day": e.day,
            "start_time": e.start_time,
            "end_time": e.end_time,
            "room_id": e.room_id,
            "room_name": e.room_name,
            "cohort_sig": sig,
            "enrolled": e.offering.enrolled_count
            if e.offering_id else 0,
        }
        plan.append(d)
        state.assign(d["key"], e.day, e.start_time, e.end_time,
                     e.room_id, e.lecturer_id, sig)
    return plan, state, entries