"""
scheduling/views.py

Role-based views for the Academic Calendar & Scheduling Management System.

Permission model:
  * Scheduling administration (rooms, slots, events, generation, publish,
    approvals) → admin only (server-side enforced via decorators).
  * HOD sign-off      → HOD scope enforced inside services.hod_approve.
  * Personal timetable→ students / teachers / admins read their own.
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from academics.models import (
    AcademicSession,
    Course,
    CourseOffering,
    Department,
    Institution,
    Level,
    Program,
    Semester,
)
from accounts.decorators import (
    admin_required,
    hod_required,
)

from scheduling import services
from scheduling.constants import (
    EventType,
    IsoWeekday,
    ScheduleStatus,
    ScheduleType,
)
from scheduling.forms import (
    AcademicEventForm,
    AcademicScheduleForm,
    BuildingForm,
    GenerateOptionsForm,
    LecturerUnavailabilityForm,
    RoomForm,
    ScheduleEntryForm,
    SchedulingConfigForm,
    TimeSlotForm,
)
from scheduling.models import (
    AcademicEvent,
    AcademicSchedule,
    Building,
    LecturerUnavailability,
    Room,
    ScheduleEntry,
    ScheduleVersion,
    SchedulingConfig,
    TimeSlot,
)


def _institution():
    return Institution.objects.first()


def _base_for_user(user):
    if user.is_student:
        return "students/base.html"
    if user.is_admin:
        return "admin_base.html"
    return "teachers/base.html"


def _page_obj(qs, request, per_page=20):
    return Paginator(qs, per_page).get_page(request.GET.get("page"))


def _flash_errors(request, exc):
    messages.error(request, str(exc))


# ═════════════════════════════════════════════════════════════════════════
# COMMAND CENTER DASHBOARD
# ═════════════════════════════════════════════════════════════════════════

@login_required
def dashboard(request):
    inst = _institution()
    schedules = AcademicSchedule.objects.select_related("semester__session")
    current = Semester.get_current()

    upcoming_events = AcademicEvent.objects.none()
    if inst is not None:
        upcoming_events = AcademicEvent.objects.filter(
            institution=inst, is_public=True
        ).order_by("start_date")[:8]

    health = services.health_check(inst) if inst else {}
    stats = services.schedule_health_stats()
    hod_approvals = services.hod_department_stats(
        request.user)["pending_approvals"] if request.user.is_hod else []

    recent = schedules.order_by("-updated_at")[:8]
    return render(request, "scheduling/dashboard.html", {
        "page_title": "Academic Command Center",
        "base_template": _base_for_user(request.user),
        "inst": inst,
        "current": current,
        "health": health,
        "stats": stats,
        "recent": recent,
        "schedules": schedules,
        "upcoming_events": upcoming_events,
        "status_choices": ScheduleStatus.choices,
        "status_counts": {
            row["status"]: row["c"]
            for row in schedules.values("status").annotate(c=Count("id"))
        },
        "type_choices": ScheduleType.choices,
        "hod_approvals": hod_approvals,
        "is_admin": request.user.is_admin,
        "is_hod": request.user.is_hod,
        "my_timetable": services.timetable_for_user(request.user),
    })


# ═════════════════════════════════════════════════════════════════════════
# ACADEMIC CALENDAR
# ═════════════════════════════════════════════════════════════════════════

def _item_to_json(it):
    """Normalise a calendar item dict into the JSON shape consumed by the
    time-grid renderer (day/week views)."""
    is_class = it["kind"] == "class"
    type_key = (
        (it.get("session_type_key") or "lecture")
        if is_class else (it.get("event_type") or "general")
    )
    url = (
        f"/scheduling/calendar/event/{it['entry_id']}/"
        if not is_class
        else f"/scheduling/schedule/entry/{it['entry_id']}/detail/"
    )
    subtitle = (
        (it.get("course_title") or "")
        if is_class else (it.get("description") or "")
    )
    return {
        "id": it["entry_id"],
        "kind": it["kind"],
        "title": it["title"],
        "subtitle": subtitle[:80],
        "time": it.get("time") or "",
        "start": it.get("start_min") or 0,
        "end": it.get("end_min") or 0,
        "allday": bool(it.get("all_day")),
        "type": type_key,
        "typeLabel": (
            it.get("session_type")
            if is_class else it.get("event_type_label")
        ),
        "lecturer": it.get("lecturer") or "",
        "room": it.get("room") or "",
        "date": str(it["date"]),
        "url": url,
        "status": it.get("schedule_status") or "",
    }


def _calc_hour_range(items, default_min=7, default_max=20):
    """Derive a compact hourly viewing window for the day/week timeline."""
    timed = [it for it in items
             if not it.get("all_day")
             and it.get("start_min") is not None]
    if not timed:
        return default_min, default_max
    mins = [it["start_min"] for it in timed] + [it["end_min"] for it in timed]
    lo = max(6, min(mins) // 60)
    hi = min(23, max(mins) // 60 + 1)
    if hi - lo < 8:                      # guarantee a usable viewport
        mid = (lo + hi) // 2
        lo = max(6, mid - 4)
        hi = min(23, mid + 4)
    return lo, hi


@login_required
def calendar(request):
    from calendar import monthrange
    from datetime import date, timedelta

    today = date.today()

    def _int_arg(name, default):
        try:
            return int(request.GET.get(name, default))
        except (TypeError, ValueError):
            return default

    mode = request.GET.get("mode", "month")
    if mode == "term":
        mode = "agenda"  # legacy alias
    if mode not in ("month", "week", "day", "agenda"):
        mode = "month"

    year = min(max(_int_arg("year", today.year), 2000), 2100)
    month = min(max(_int_arg("month", today.month), 1), 12)
    day = min(max(_int_arg("day", today.day), 1), 31)

    # ── semester / session resolution ──────────────────────────────────────
    current = Semester.get_current()
    semester = current
    semester_id = request.GET.get("semester")
    if semester_id:
        semester = Semester.objects.select_related("session").filter(
            pk=semester_id).first() or current

    session_id = request.GET.get("session") or None
    sessions = AcademicSession.objects.order_by("-start_date")
    semesters = Semester.objects.select_related("session").order_by(
        "-session__start_date", "name")
    if session_id:
        semesters = semesters.filter(session_id=session_id)

    if semester is None:
        semester = semesters.first()

    # ── declarative filters ─────────────────────────────────────────────────
    filter_keys = ["schedule", "status", "dept", "program", "level", "course",
                   "lecturer", "room", "type"]
    filters = {k: request.GET.get(k) for k in filter_keys if request.GET.get(k)}

    # ── date window for the selected mode ───────────────────────────────────
    if mode == "month":
        first, ndays = monthrange(year, month)
        start = date(year, month, 1)
        end = start + timedelta(days=ndays - 1)
        month_label = start.strftime("%B %Y")
    elif mode == "week":
        base = date(year, month, min(day, 28))
        start = base - timedelta(days=base.weekday())
        end = start + timedelta(days=6)
        month_label = (
            f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
        )
    elif mode == "day":
        start = end = date(year, month, min(day, 28))
        month_label = start.strftime("%A, %d %B %Y")
    else:  # agenda — full semester
        start = (semester.start_date if semester else today)
        end = (semester.end_date if semester else today + timedelta(days=90))
        month_label = (f"Semester agenda · {semester}"
                       if semester else "Academic agenda")

    data = services.calendar_items(
        request.user, semester=semester, filters=filters, start=start, end=end,
    )

    # ── assemble mode-specific view models ─────────────────────────────────
    week_rows, cells, agenda = [], [], []
    if mode == "month":
        grid_start = start - timedelta(days=start.weekday())
        for w in range(6):
            row = []
            for d in range(7):
                day_date = grid_start + timedelta(days=w * 7 + d)
                row.append({
                    "date": day_date,
                    "in_month": day_date.month == month,
                    "is_today": day_date == today,
                    "items": [it for it in data["items"] if it["date"] == day_date],
                })
            week_rows.append(row)
        py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        prev, nxt = date(py, pm, 1), date(ny, nm, 1)
    else:
        cursor = start
        while cursor <= end:
            day_items = [it for it in data["items"] if it["date"] == cursor]
            if mode == "agenda":
                cells.append({"date": cursor, "in_month": True, "items": day_items})
            else:
                cells.append({"date": cursor, "in_month": True, "items": day_items})
            cursor += timedelta(days=1)
        prev = (start - timedelta(days=7 if mode == "week" else 1)
                if mode != "agenda" else start - timedelta(days=90))
        nxt = (end + timedelta(days=7 if mode == "week" else 1)
               if mode != "agenda" else end + timedelta(days=90))
        if mode == "week":
            week_rows = [cells]

    # ── navigation query preserver ──────────────────────────────────────────
    nav_qs = "&".join(f"{k}={v}" for k, v in filters.items())
    if semester_id:
        nav_qs += f"&semester={semester_id}"
    if session_id:
        nav_qs += f"&session={session_id}"

    # ── real-time / structured data for the day & week time-grids ───────────
    hour_min, hour_max = _calc_hour_range(data["items"])
    if mode in ("day", "week"):
        items_json = json.dumps(
            [_item_to_json(it) for it in data["items"]],
            ensure_ascii=False,
        )
        days_json = json.dumps([
            {
                "date": c["date"].isoformat(),
                "label": c["date"].strftime("%a"),
                "num": c["date"].day,
                "month": c["date"].strftime("%b"),
            }
            for c in cells
        ], ensure_ascii=False)
    else:
        items_json = days_json = "[]"

    if semester and semester.session_id:
        academic_label = f"{semester.session.name} • {semester.get_name_display()}"
    elif semester:
        academic_label = str(semester)
    else:
        academic_label = ""

    view_date_is_today = False
    if mode == "day":
        view_date_is_today = start == today
    elif mode == "week":
        view_date_is_today = start <= today <= end
    elif mode == "month":
        view_date_is_today = (year == today.year and month == today.month)

    from django.conf import settings as dj_settings
    return render(request, "scheduling/calendar.html", {
        "page_title": "Academic Calendar & Scheduling Center",
        "base_template": _base_for_user(request.user),
        "mode": mode,
        "year": year, "month": month, "day": day,
        "month_label": month_label,
        "academic_label": academic_label,
        "view_date_is_today": view_date_is_today,
        "tz_name": dj_settings.TIME_ZONE,
        "week_rows": week_rows,
        "cells": cells,
        "prev": prev, "nxt": nxt,
        "nav_qs": nav_qs,
        "today": today,
        "today_iso": today.isoformat(),
        "hour_min": hour_min, "hour_max": hour_max,
        "items_json": items_json,
        "days_json": days_json,
        "semester": semester,
        "session": session_id,
        "sessions": sessions,
        "semesters": semesters,
        "filters": filters,
        "manage": request.user.is_admin,
        "is_hod": request.user.is_hod,
        "is_student": request.user.is_student,
        "event_types": EventType.choices,
        "status_choices": ScheduleStatus.choices,
        "schedule_options": data["schedules"][:40],
        "active_schedule": data["active_schedule"],
        "filter_options": _calendar_filter_options(request),
    })


def _calendar_filter_options(request):
    """Role-scoped dropdown data for the calendar filter bar."""
    from django.contrib.auth import get_user_model

    user = request.user
    semester = Semester.get_current()
    depts = Department.objects.all()
    programs = Level.objects.none()
    levels = Level.objects.none()
    courses = Course.objects.none()
    lecturers = None
    rooms = Room.objects.none()

    if user.is_admin:
        depts = Department.objects.select_related("faculty").order_by("code")
        programs = Program.objects.select_related("department").order_by("code")
        levels = Level.objects.select_related("program").order_by(
            "program__code", "order")
        courses = Course.objects.order_by("code")
        lecturers = get_user_model().objects.filter(
            role="teacher", is_active=True).order_by("last_name")
        rooms = Room.objects.filter(is_active=True).order_by("building", "code")
    elif user.is_hod:
        dept_ids = list(user.get_hod_departments().values_list("pk", flat=True))
        depts = Department.objects.filter(pk__in=dept_ids)
        programs = Program.objects.filter(department_id__in=dept_ids)
        levels = Level.objects.filter(program__department_id__in=dept_ids)
        courses = Course.objects.filter(offerings__departments__in=dept_ids)
        rooms = Room.objects.filter(building__institution__departments__in=dept_ids)
    elif user.is_student:
        offering_ids = user.enrolments.filter(
            offering__semester=semester, is_active=True, status="active",
        ).values_list("offering_id", flat=True)
        depts = Department.objects.filter(
            offerings__in=offering_ids)
        programs = Program.objects.filter(
            levels__offerings__in=offering_ids)
        levels = Level.objects.filter(offerings__in=offering_ids)
        courses = Course.objects.filter(offerings__in=offering_ids)
    else:  # teacher (incl. HOD-also-teacher)
        from academics.models import TeacherDepartment
        dept_ids = list(TeacherDepartment.objects.filter(
            teacher=user, is_active=True).values_list("department_id", flat=True))
        dept_ids += list(user.get_hod_departments().values_list("pk", flat=True))
        offering_ids = user.course_allocations.filter(
            offering__semester=semester, is_active=True,
        ).values_list("offering_id", flat=True)
        depts = Department.objects.filter(
            Q(pk__in=dept_ids) | Q(offerings__in=offering_ids)).distinct()
        programs = Program.objects.filter(
            Q(department_id__in=dept_ids) | Q(levels__offerings__in=offering_ids)
        ).distinct()
        levels = Level.objects.filter(
            Q(program__department_id__in=dept_ids)
            | Q(offerings__in=offering_ids)).distinct()
        courses = Course.objects.filter(
            Q(department_id__in=dept_ids) | Q(offerings__in=offering_ids)
        ).distinct()
        lecturers = get_user_model().objects.filter(
            role="teacher", is_active=True).filter(
            Q(first_name__icontains="") | Q(last_name__icontains="")).order_by(
            "last_name")

    # apply cascading filters chosen so far (sent via GET)
    def _q(name):
        val = request.GET.get(name)
        return int(val) if val and val.isdigit() else None

    cur_dept = _q("dept")
    cur_prog = _q("program")
    if cur_dept:
        programs = programs.filter(department_id=cur_dept)
        levels = levels.filter(program__department_id=cur_dept)
        courses = courses.filter(department_id=cur_dept)
    if cur_prog:
        levels = levels.filter(program_id=cur_prog)
    return {
        "depts": depts.distinct().order_by("code"),
        "programs": programs.distinct().order_by("code"),
        "levels": levels.distinct().order_by("program__code", "order"),
        "courses": courses.distinct().order_by("code"),
        "lecturers": lecturers,
        "rooms": rooms,
    }


@login_required
def event_detail(request, pk):
    """Fragment for the calendar event-edit modal (role-aware)."""
    from datetime import date as date_cls
    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    ev = get_object_or_404(
        AcademicEvent.objects.select_related("programme", "department", "level",
                                             "offering"),
        pk=pk,
    )
    allowed = request.user.is_admin or request.user.is_hod or ev.is_public
    if not allowed:
        raise Http404("Not found.")
    return render(request, "scheduling/_event_modal.html", {
        "ev": ev,
        "manage": request.user.is_admin,
        "is_hod": request.user.is_hod,
        "today": date_cls.today(),
    })


@login_required
def entry_detail(request, pk):
    """Fragment for the schedule-entry calendar modal (role-aware).

    Visibility mirrors `services.calendar_items`: students/lecturers may
    inspect published entries they are scoped to; HODs see their departments'
    published rows; admins may inspect any entry. Accepts an optional `date`
    query param (the occurrence date) for display context.
    """
    from scheduling.constants import ScheduleStatus

    qs = ScheduleEntry.objects.select_related(
        "schedule", "course", "room", "lecturer",
        "offering", "offering__level", "offering__level__program",
        "offering__level__program__department",
        "offering__semester__session",
    )
    entry = get_object_or_404(qs, pk=pk)

    if not request.user.is_admin:
        scoped = services._scope_entries_for_user(
            ScheduleEntry.objects.filter(pk=entry.pk),
            request.user, Semester.get_current(),
        ).filter(schedule__status=ScheduleStatus.PUBLISHED)
        if not scoped.exists():
            raise Http404("Entry not visible to this user.")

    occ_date = None
    if request.GET.get("date"):
        from datetime import date as date_cls
        try:
            occ_date = date_cls.fromisoformat(request.GET["date"][:10])
        except ValueError:
            occ_date = None

    return render(request, "scheduling/_entry_modal.html", {
        "entry": entry,
        "schedule": entry.schedule,
        "occ_date": occ_date,
        "manage": request.user.is_admin,
        "is_hod": request.user.is_hod,
    })


@login_required
def calendar_search(request):
    """Lightweight JSON search across scoped calendar entities."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})

    semester = Semester.get_current()
    results, seen = [], set()

    def push(kind, label, sub, url):
        key = (kind, label)
        if key in seen:
            return
        seen.add(key)
        results.append({"type": kind, "label": label, "sub": sub or "", "url": url})

    user = request.user
    dept_ids, offering_ids = set(), set()
    if user.is_admin:
        courses = Course.objects.filter(
            Q(code__icontains=q) | Q(title__icontains=q))[:8]
        for c in courses:
            push("course", c.code, c.title,
                 f"/scheduling/calendar/?course={c.pk}")
        rooms = Room.objects.filter(code__icontains=q)[:6]
        for r in rooms:
            push("room", str(r), r.get_room_type_display(),
                 f"/scheduling/calendar/?room={r.pk}")
        progs = Program.objects.filter(
            Q(code__icontains=q) | Q(name__icontains=q))[:6]
        for p in progs:
            push("programme", p.code, p.name,
                 f"/scheduling/calendar/?program={p.pk}")
    else:
        if user.is_hod:
            dept_ids = set(user.get_hod_departments().values_list("pk", flat=True))
        if user.is_student:
            offering_ids = set(user.enrolments.filter(
                offering__semester=semester, is_active=True, status="active",
            ).values_list("offering_id", flat=True))
        elif user.is_teacher:
            offering_ids = set(user.course_allocations.filter(
                offering__semester=semester, is_active=True,
            ).values_list("offering_id", flat=True))
        offering_ids = list(offering_ids)
        courses = Course.objects.filter(
            Q(offerings__id__in=offering_ids)
            | (Q(department_id__in=dept_ids) if dept_ids else Q(pk=None))
        ).filter(Q(code__icontains=q) | Q(title__icontains=q)).distinct()[:8]
        for c in courses:
            push("course", c.code, c.title,
                 f"/scheduling/calendar/?course={c.pk}")

    depts = Department.objects.filter(name__icontains=q)
    for d in depts[:5]:
        push("department", d.code or d.name, d.faculty.name if d.faculty_id else "",
             f"/scheduling/calendar/?dept={d.pk}")

    events = AcademicEvent.objects.filter(title__icontains=q)[:6]
    for ev in events:
        push("event", ev.title, ev.get_event_type_display(),
             f"/scheduling/calendar/?type={ev.event_type}")

    from django.contrib.auth import get_user_model
    lecturers = get_user_model().objects.filter(
        role="teacher", is_active=True).filter(
        Q(first_name__icontains=q) | Q(last_name__icontains=q))[:6]
    if user.is_admin:
        for t in lecturers:
            push("lecturer", t.get_full_name(), t.email or "",
                 f"/scheduling/calendar/?lecturer={t.pk}")

    return JsonResponse({"results": results[:30]})


@login_required
@admin_required
def event_list(request):
    qs = AcademicEvent.objects.select_related("programme", "department", "level")
    qs = qs.order_by("-start_date")
    return render(request, "scheduling/event_list.html", {
        "page_title": "Calendar Events",
        "base_template": _base_for_user(request.user),
        "events": qs[:200],
        "event_types": EventType.choices,
    })


@login_required
@admin_required
def event_create(request):
    form = AcademicEventForm(request.POST or None, institution=_institution())
    if request.method == "POST" and form.is_valid():
        ev = form.save(commit=False)
        ev.institution = _institution()
        ev.created_by = request.user
        ev.save()
        services.audit("create", request.user, instance=ev)
        messages.success(request, "Event added to the academic calendar.")
        return redirect("scheduling:event_list")
    return render(request, "scheduling/event_form.html", {
        "page_title": "New Calendar Event",
        "base_template": _base_for_user(request.user),
        "form": form,
        "cancel_url": "scheduling:event_list",
    })


@login_required
@admin_required
def event_edit(request, pk):
    ev = get_object_or_404(AcademicEvent, pk=pk)
    form = AcademicEventForm(request.POST or None, instance=ev,
                             institution=_institution())
    if request.method == "POST" and form.is_valid():
        form.save()
        services.audit("update", request.user, instance=ev)
        messages.success(request, "Event updated.")
        return redirect("scheduling:event_list")
    return render(request, "scheduling/event_form.html", {
        "page_title": f"Edit Event — {ev.title}",
        "base_template": _base_for_user(request.user),
        "form": form,
        "cancel_url": "scheduling:event_list",
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def event_delete(request, pk):
    ev = get_object_or_404(AcademicEvent, pk=pk)
    services.audit("delete", request.user, instance=ev)
    ev.delete()
    messages.success(request, "Event deleted.")
    return redirect("scheduling:event_list")


# ═════════════════════════════════════════════════════════════════════════
# BUILDINGS & ROOMS
# ═════════════════════════════════════════════════════════════════════════

@login_required
@admin_required
def building_create(request):
    form = BuildingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        b = form.save(commit=False)
        b.institution = _institution()
        b.save()
        services.audit("create", request.user, instance=b)
        messages.success(request, "Building created.")
        return redirect("scheduling:room_list")
    return render(request, "scheduling/building_form.html", {
        "page_title": "New Building",
        "base_template": _base_for_user(request.user),
        "form": form,
        "cancel_url": "scheduling:room_list",
    })


@login_required
@admin_required
def room_list(request):
    qs = Room.objects.select_related("building").order_by("building", "code")
    qs = qs.annotate(usage=Count("scheduling_uses"))
    return render(request, "scheduling/room_list.html", {
        "page_title": "Rooms",
        "base_template": _base_for_user(request.user),
        "rooms": _page_obj(qs, request, 30),
    })


@login_required
@admin_required
def room_create(request):
    form = RoomForm(request.POST or None, institution=_institution())
    if request.method == "POST" and form.is_valid():
        room = form.save()
        services.audit("create", request.user, instance=room)
        messages.success(request, "Room created.")
        return redirect("scheduling:room_list")
    return render(request, "scheduling/room_form.html", {
        "page_title": "New Room",
        "base_template": _base_for_user(request.user),
        "form": form,
        "cancel_url": "scheduling:room_list",
    })


@login_required
@admin_required
def room_edit(request, pk):
    room = get_object_or_404(Room, pk=pk)
    form = RoomForm(request.POST or None, instance=room, institution=_institution())
    if request.method == "POST" and form.is_valid():
        form.save()
        services.audit("update", request.user, instance=room)
        messages.success(request, "Room updated.")
        return redirect("scheduling:room_list")
    return render(request, "scheduling/room_form.html", {
        "page_title": f"Edit Room — {room}",
        "base_template": _base_for_user(request.user),
        "form": form,
        "cancel_url": "scheduling:room_list",
    })


@login_required
@admin_required
def room_detail(request, pk):
    room = get_object_or_404(Room.objects.select_related("building"), pk=pk)
    usage = room.scheduling_uses.select_related("schedule", "course").order_by(
        "schedule__name", "day", "start_time"
    )
    return render(request, "scheduling/room_detail.html", {
        "page_title": f"Room — {room}",
        "base_template": _base_for_user(request.user),
        "room": room,
        "usage": usage,
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def room_toggle(request, pk):
    room = get_object_or_404(Room, pk=pk)
    room.is_available = not room.is_available
    room.save(update_fields=["is_available", "updated_at"])
    services.audit("update", request.user, instance=room,
                   changes={"is_available": room.is_available})
    messages.success(request, f"{room} availability updated.")
    return redirect("scheduling:room_list")


# ═════════════════════════════════════════════════════════════════════════
# TIME SLOTS
# ═════════════════════════════════════════════════════════════════════════

@login_required
@admin_required
def slot_list(request):
    slots = TimeSlot.objects.order_by("day", "start_time")
    return render(request, "scheduling/slot_list.html", {
        "page_title": "Time Slots",
        "base_template": _base_for_user(request.user),
        "slots": slots,
        "day_labels": [IsoWeekday(i).label for i in range(7)],
    })


@login_required
@admin_required
def slot_create(request):
    form = TimeSlotForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        slot = form.save()
        services.audit("create", request.user, instance=slot)
        messages.success(request, "Time slot created.")
        return redirect("scheduling:slot_list")
    return render(request, "scheduling/slot_form.html", {
        "page_title": "New Time Slot",
        "base_template": _base_for_user(request.user),
        "form": form,
        "cancel_url": "scheduling:slot_list",
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def slot_delete(request, pk):
    slot = get_object_or_404(TimeSlot, pk=pk)
    services.audit("delete", request.user, instance=slot)
    slot.delete()
    messages.success(request, "Time slot deleted.")
    return redirect("scheduling:slot_list")


@login_required
@admin_required
@require_http_methods(["POST"])
def slot_regenerate(request):
    """Rebuild default coarse slots from the current config."""
    inst = _institution()
    if inst is None:
        messages.error(request, "No institution configured yet.")
        return redirect("scheduling:slot_list")
    cfg = SchedulingConfig.for_institution(inst)
    from scheduling.engine import generator

    existing_kw = set(
        TimeSlot.objects.filter(is_default=True)
        .values_list("day", "start_time", "end_time")
    )
    created = 0
    for slot in generator._coarse_slots(cfg):
        key = (slot["day"], slot["start_time"], slot["end_time"])
        if key in existing_kw:
            continue
        TimeSlot.objects.create(day=key[0], start_time=key[1],
                                end_time=key[2], is_default=True)
        created += 1
    services.audit("create", request.user, changes={
        "action": "slot_regenerate", "created": created})
    messages.success(request, f"{created} default slot(s) created.")
    return redirect("scheduling:slot_list")


# ═════════════════════════════════════════════════════════════════════════
# LECTURER UNAVAILABILITY
# ═════════════════════════════════════════════════════════════════════════

@login_required
@admin_required
def availability_list(request):
    qs = LecturerUnavailability.objects.select_related("lecturer").order_by(
        "lecturer__last_name", "day", "start_time"
    )
    return render(request, "scheduling/availability_list.html", {
        "page_title": "Lecturer Availability",
        "base_template": _base_for_user(request.user),
        "items": _page_obj(qs, request, 30),
    })


def availability_add(request):
    if request.user.is_teacher and not request.user.is_admin:
        initial = {"lecturer": request.user}
    else:
        initial = None
    form = LecturerUnavailabilityForm(request.POST or None, initial=initial)
    if request.user.is_teacher and not request.user.is_admin:
        form.fields["lecturer"].disabled = True
        form.fields["lecturer"].required = False
    if request.method == "POST" and form.is_valid():
        block = form.save(commit=False)
        if request.user.is_teacher and not request.user.is_admin:
            block.lecturer = request.user
        block.save()
        services.audit("create", request.user, instance=block)
        messages.success(request, "Unavailability window added.")
        return redirect("scheduling:availability_add")
    return render(request, "scheduling/availability_form.html", {
        "page_title": "Add Unavailability",
        "base_template": _base_for_user(request.user),
        "form": form,
        "cancel_url": "scheduling:availability_list",
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def availability_delete(request, pk):
    block = get_object_or_404(LecturerUnavailability, pk=pk)
    services.audit("delete", request.user, instance=block)
    block.delete()
    messages.success(request, "Unavailability window removed.")
    return redirect("scheduling:availability_list")


# ═════════════════════════════════════════════════════════════════════════
# SCHEDULING CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════

@login_required
@admin_required
def settings(request):
    inst = _institution()
    cfg = SchedulingConfig.for_institution(inst) if inst else None
    if cfg is None:
        messages.error(request, "No institution configured.")
        return redirect("scheduling:dashboard")
    form = SchedulingConfigForm(request.POST or None, instance=cfg)
    if request.method == "POST" and form.is_valid():
        form.save()
        services.audit("update", request.user, instance=cfg)
        messages.success(request, "Scheduling configuration saved.")
        return redirect("scheduling:settings")
    return render(request, "scheduling/settings.html", {
        "page_title": "Scheduling Settings",
        "base_template": _base_for_user(request.user),
        "form": form,
    })


# ═════════════════════════════════════════════════════════════════════════
# SCHEDULES
# ═════════════════════════════════════════════════════════════════════════

def _schedule_context(request, schedule):
    """Common context for the schedule detail pages."""
    return {
        "schedule": schedule,
        "base_template": _base_for_user(request.user),
        "status_choices": ScheduleStatus.choices,
        "is_editable": not schedule.is_locked,
    }


@login_required
@admin_required
def schedule_list(request):
    qs = AcademicSchedule.objects.select_related("semester__session").order_by(
        "-semester__session__start_date", "-updated_at"
    )
    semester_id = request.GET.get("semester")
    status = request.GET.get("status")
    if semester_id:
        qs = qs.filter(semester_id=semester_id)
    if status:
        qs = qs.filter(status=status)
    return render(request, "scheduling/schedule_list.html", {
        "page_title": "Schedules",
        "base_template": _base_for_user(request.user),
        "schedules": _page_obj(qs, request, 20),
        "semesters": Semester.objects.select_related("session"),
        "status_choices": ScheduleStatus.choices,
        "cur_semester": semester_id, "cur_status": status,
    })


@login_required
@admin_required
def schedule_create(request):
    form = AcademicScheduleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            schedule = services.create_schedule(
                name=form.cleaned_data["name"],
                semester=form.cleaned_data["semester"],
                schedule_type=form.cleaned_data["schedule_type"],
                actor=request.user,
                institution=_institution(),
                notes=form.cleaned_data.get("notes", ""),
            )
            schedule.consider_room_availability = form.cleaned_data[
                "consider_room_availability"]
            schedule.save()
            return redirect("scheduling:schedule_detail", pk=schedule.pk)
        except Exception as exc:
            _flash_errors(request, exc)
    return render(request, "scheduling/schedule_form.html", {
        "page_title": "New Schedule",
        "base_template": _base_for_user(request.user),
        "form": form,
        "cancel_url": "scheduling:schedule_list",
    })


@login_required
@admin_required
def schedule_edit(request, pk):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    form = AcademicScheduleForm(request.POST or None, instance=schedule)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_schedule(schedule, request.user, form.cleaned_data)
            messages.success(request, f"Updated “{schedule.name}”.")
            return redirect("scheduling:schedule_detail", pk=schedule.pk)
        except Exception as exc:
            _flash_errors(request, exc)
    return render(request, "scheduling/schedule_form.html", {
        "page_title": f"Edit — {schedule.name}",
        "base_template": _base_for_user(request.user),
        "form": form,
        "schedule": schedule,
    })


@login_required
@admin_required
def schedule_detail(request, pk):
    schedule = get_object_or_404(
        AcademicSchedule.objects.select_related("semester__session",
                                               "institution"),
        pk=pk,
    )
    entries = schedule.entries.select_related("course", "room", "lecturer") \
        .order_by("day", "start_time")
    days = [
        {"name": IsoWeekday(i).label, "weekday": i,
         "entries": [e for e in entries if e.day == i]}
        for i in range(7)
    ]
    versions = schedule.versions.all()[:6]
    ctx = _schedule_context(request, schedule)
    ctx.update({
        "entries_total": entries.count(),
        "days": days,
        "versions": versions,
        "hod_rows": services.hod_list_for(schedule),
    })
    return render(request, "scheduling/schedule_detail.html", ctx)


@login_required
@admin_required
def schedule_generate(request, pk):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    cfg = SchedulingConfig.for_institution(schedule.institution)

    options_form = GenerateOptionsForm(request.POST or None)
    options = None
    if request.method == "POST" and options_form.is_valid():
        cd = options_form.cleaned_data
        options = {
            "session_types": cd["session_types"] or None,
            "seed": cd["seed"],
            "prefer_allocated_lecturer": cd["prefer_allocated_lecturer"],
        }
        try:
            stats = services.generate_schedule(
                schedule, actor=request.user, options=options, 
            )
            messages.success(
                request,
                f"Generated {stats['placed']}/{stats['total']} sessions "
                f"({stats['unplaced']} unplaced).",
            )
            return redirect("scheduling:schedule_detail", pk=schedule.pk)
        except Exception as exc:
            _flash_errors(request, exc)

    simulated = services.simulate_options(schedule, config=cfg)
    return render(request, "scheduling/schedule_generate.html", {
        "page_title": f"Generate — {schedule.name}",
        "base_template": _base_for_user(request.user),
        "schedule": schedule,
        "form": options_form,
        "simulated": simulated,
        "cancel_url": ("scheduling:schedule_detail", schedule.pk),
    })


@login_required
@admin_required
def schedule_options(request, pk):
    """Shows 3 simulated plans; choosing one regenerates with that seed."""
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    cfg = SchedulingConfig.for_institution(schedule.institution)
    if request.method == "POST":
        seed = request.POST.get("seed")
        try:
            stats = services.generate_schedule(
                schedule, actor=request.user,
                options={"seed": int(seed)},
            )
            messages.success(request, "Selected option generated.")
            return redirect("scheduling:schedule_detail", pk=schedule.pk)
        except Exception as exc:
            _flash_errors(request, exc)
    simulated = services.simulate_options(schedule, config=cfg)
    return render(request, "scheduling/schedule_options.html", {
        "page_title": f"Compare Options — {schedule.name}",
        "base_template": _base_for_user(request.user),
        "schedule": schedule,
        "simulated": simulated,
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_optimize(request, pk):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    try:
        result = services.optimize_schedule(schedule, actor=request.user)
        messages.success(
            request,
            f"Optimised: {result['moves']} move(s), score "
            f"{result['score_before']} → {result['score_after']}.",
        )
    except Exception as exc:
        _flash_errors(request, exc)
    return redirect("scheduling:schedule_detail", pk=schedule.pk)


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_repair(request, pk):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    try:
        result = services.repair_schedule(schedule, actor=request.user)
        messages.success(
            request,
            f"Repair: {result['repaired']} moved, hard conflicts "
            f"{result['remaining_hard_before']} → "
            f"{result['remaining_hard_after']}.",
        )
    except Exception as exc:
        _flash_errors(request, exc)
    return redirect("scheduling:schedule_detail", pk=schedule.pk)


@login_required
@admin_required
def schedule_conflicts(request, pk):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    report = services.conflicts_report(schedule)
    return render(request, "scheduling/schedule_conflicts.html", {
        "page_title": f"Conflicts — {schedule.name}",
        "base_template": _base_for_user(request.user),
        "schedule": schedule,
        "report": report,
    })


# ── manual entry editing ───────────────────────────────────────────────────

@login_required
@admin_required
def schedule_entries(request, pk):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    form = ScheduleEntryForm(request.POST or None, institution=_institution())
    if request.method == "POST" and "add" in request.POST and form.is_valid():
        cd = form.cleaned_data
        try:
            services.add_entry(
                schedule, cd["offering"], cd["day"], cd["start_time"],
                cd["end_time"], cd["session_type"], room=cd.get("room"),
                lecturer=cd.get("lecturer"), actor=request.user,
            )
            messages.success(request, "Session added.")
            return redirect("scheduling:schedule_entries", pk=schedule.pk)
        except Exception as exc:
            _flash_errors(request, exc)

    if request.method == "POST" and "delete" in request.POST:
        try:
            services.delete_entry(schedule, request.POST.get("delete"),
                                  request.user)
            messages.success(request, "Session deleted.")
        except Exception as exc:
            _flash_errors(request, exc)
        return redirect("scheduling:schedule_entries", pk=schedule.pk)

    if request.method == "POST" and "lock" in request.POST:
        try:
            services.lock_entry(schedule, request.POST.get("lock"),
                                request.user, locked=True)
            messages.success(request, "Session locked.")
        except Exception as exc:
            _flash_errors(request, exc)
        return redirect("scheduling:schedule_entries", pk=schedule.pk)

    if request.method == "POST" and "unlock" in request.POST:
        try:
            services.lock_entry(schedule, request.POST.get("unlock"),
                                request.user, locked=False)
            messages.success(request, "Session unlocked.")
        except Exception as exc:
            _flash_errors(request, exc)
        return redirect("scheduling:schedule_entries", pk=schedule.pk)

    entries = schedule.entries.select_related("course", "room", "lecturer") \
        .order_by("day", "start_time")
    ctx = _schedule_context(request, schedule)
    ctx.update({
        "form": form,
        "entries": entries,
        "days": [IsoWeekday(i).label for i in range(7)],
    })
    return render(request, "scheduling/schedule_entries.html", ctx)


# ── versions ────────────────────────────────────────────────────────────────

@login_required
@admin_required
def schedule_versions(request, pk):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    v1_id = request.GET.get("v1")
    v2_id = request.GET.get("v2")
    diff = None
    if v1_id and v2_id:
        v1 = get_object_or_404(ScheduleVersion, pk=v1_id, schedule=schedule)
        v2 = get_object_or_404(ScheduleVersion, pk=v2_id, schedule=schedule)
        diff = services.compare_versions(v1, v2)
    if request.method == "POST" and request.POST.get("restore"):
        version = get_object_or_404(
            ScheduleVersion, pk=request.POST.get("restore"), schedule=schedule,
        )
        try:
            services.restore_version(schedule, version, request.user)
            messages.success(request, f"Restored version {version.version_number}.")
        except Exception as exc:
            _flash_errors(request, exc)
        return redirect("scheduling:schedule_versions", pk=schedule.pk)
    versions = schedule.versions.all()
    return render(request, "scheduling/schedule_versions.html", {
        "page_title": f"Versions — {schedule.name}",
        "base_template": _base_for_user(request.user),
        "schedule": schedule,
        "versions": versions,
        "diff": diff,
        "v1": v1_id, "v2": v2_id,
    })


# ── workflow actions (POST) ─────────────────────────────────────────────────

def _schedule_post(request, pk, callback, success):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    try:
        callback(request, schedule)
        messages.success(request, success % schedule.name)
    except Exception as exc:
        _flash_errors(request, exc)
    return redirect("scheduling:schedule_detail", pk=schedule.pk)


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_submit(request, pk):
    return _schedule_post(
        request, pk,
        lambda r, s: services.submit_for_review(s, r.user),
        "Submitted “%s” for HOD review.",
    )


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_approve(request, pk):
    return _schedule_post(
        request, pk,
        lambda r, s: services.approve_schedule(s, r.user),
        "Approved “%s”.",
    )


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_publish(request, pk):
    return _schedule_post(
        request, pk,
        lambda r, s: services.publish_schedule(
            s, r.user, notify=r.POST.get("notify") != "0"),
        "Published “%s”.",
    )


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_unpublish(request, pk):
    return _schedule_post(
        request, pk,
        lambda r, s: services.unpublish_schedule(s, r.user),
        "Unpublished “%s”.",
    )


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_archive(request, pk):
    return _schedule_post(
        request, pk,
        lambda r, s: services.archive_schedule(s, r.user),
        "Archived “%s”.",
    )


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_unarchive(request, pk):
    return _schedule_post(
        request, pk,
        lambda r, s: services.unarchive_schedule(s, r.user),
        "Restored “%s” from archive.",
    )


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_clear_unplaced(request, pk):
    return _schedule_post(
        request, pk,
        lambda r, s: services.clear_unplaced(s, r.user),
        "Cleared unplaced sessions for “%s”.",
    )


@login_required
@admin_required
@require_http_methods(["POST"])
def schedule_delete(request, pk):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    name = schedule.name
    try:
        services.delete_schedule(schedule, request.user)
        messages.success(request, f"Deleted “{name}”.")
    except Exception as exc:
        _flash_errors(request, exc)
    return redirect("scheduling:schedule_list")


# ═════════════════════════════════════════════════════════════════════════
# HOD APPROVALS
# ═════════════════════════════════════════════════════════════════════════

@login_required
@hod_required
def hod_approvals(request):
    my_departments = list(request.user.get_hod_departments())
    dept_ids = [d.pk for d in my_departments]
    schedules = AcademicSchedule.objects.filter(
        status=ScheduleStatus.UNDER_REVIEW,
        semester__offerings__departments__in=dept_ids,
    ).select_related("semester__session").distinct()

    decisions = []
    for schedule in schedules:
        mine = [
            a for a in services.hod_list_for(schedule)
            if a["department"].pk in dept_ids
        ]
        decisions.append({"schedule": schedule, "rows": mine})
    return render(request, "scheduling/hod_approvals.html", {
        "page_title": "HOD Approvals",
        "base_template": _base_for_user(request.user),
        "decisions": decisions,
    })


@login_required
@hod_required
@require_http_methods(["POST"])
def hod_decide(request, pk):
    schedule = get_object_or_404(AcademicSchedule, pk=pk)
    department_id = request.POST.get("department")
    approve = request.POST.get("action") == "approve"
    comment = request.POST.get("comment", "")[:500]
    try:
        services.hod_approve(
            schedule, request.user, department_id, approve,
            comment=comment,
        )
        messages.success(
            request,
            "Department sign-off recorded.",
        )
    except Exception as exc:
        _flash_errors(request, exc)
    return redirect("scheduling:hod_approvals")


@login_required
@hod_required
def hod_dashboard(request):
    from datetime import date

    dept_ids = list(request.user.get_hod_departments().values_list("pk", flat=True))

    pending = AcademicSchedule.objects.filter(
        status=ScheduleStatus.UNDER_REVIEW,
        semester__offerings__departments__in=dept_ids,
    ).select_related("semester__session").distinct()

    stats = services.hod_department_stats(request.user)
    published = AcademicSchedule.objects.filter(
        status=ScheduleStatus.PUBLISHED,
        semester__offerings__departments__in=dept_ids,
    ).select_related("semester__session").distinct()

    my_calendar = services.calendar_items(
        request.user, filters={"dept": dept_ids[0]} if dept_ids else None,
    )
    return render(request, "scheduling/hod_dashboard.html", {
        "page_title": "HOD Dashboard",
        "base_template": _base_for_user(request.user),
        "pending": pending,
        "published": published,
        "stats": stats,
        "upcoming": my_calendar["items"][:8],
        "today": date.today(),
    })


# ═════════════════════════════════════════════════════════════════════════
# PERSONAL TIMETABLE & WEEK GRID
# ═════════════════════════════════════════════════════════════════════════

@login_required
def my_timetable(request):
    data = services.timetable_for_user(request.user)
    return render(request, "scheduling/my_timetable.html", {
        "page_title": "My Timetable",
        "base_template": _base_for_user(request.user),
        "data": data,
        "day_labels": [IsoWeekday(i).label for i in range(7)],
    })


# ═════════════════════════════════════════════════════════════════════════
# ROOM TIMETABLE (admin view of occupancy)
# ═════════════════════════════════════════════════════════════════════════

@login_required
def room_timetable(request, pk):
    room = get_object_or_404(Room.objects.select_related("building"), pk=pk)
    schedule = AcademicSchedule.objects.filter(
        is_current=True, schedule_type=ScheduleType.CLASS,
    ).order_by("-semester__session__start_date").first()
    entries = room.scheduling_uses.select_related("course", "schedule")
    if schedule is not None:
        entries = entries.filter(schedule=schedule)
    return render(request, "scheduling/room_timetable.html", {
        "page_title": f"Room Timetable — {room}",
        "base_template": _base_for_user(request.user),
        "room": room,
        "schedule": schedule,
        "entries": list(entries.order_by("day", "start_time")),
        "day_labels": [IsoWeekday(i).label for i in range(7)],
    })