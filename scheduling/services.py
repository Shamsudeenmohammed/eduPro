"""
scheduling.services — orchestration layer.

Everything that MUTATES the system lives here so views stay thin and the
workflow (generate → review → approve → publish → archive) can never be
accidentally bypassed.

Layering:  views → services → engine (pure logic) + models.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from scheduling.constants import (
    ScheduleStatus,
    ScheduleType,
    JobStatus,
)
from scheduling.models import (
    AcademicSchedule,
    ScheduleEntry,
    ScheduleGenerationJob,
    ScheduleHodApproval,
    ScheduleVersion,
    SchedulingConfig,
)
from scheduling.engine import generator, optimizer, repair
from scheduling.engine.conflict_detector import detect_conflicts, explain_entry


ACTIONS = {
    "CREATE": "create",
    "UPDATE": "update",
    "DELETE": "delete",
    "APPROVE": "approve",
    "REJECT": "reject",
}


# ─────────────────────────────────────────────────────────────────────────────
# Audit + notifications helpers
# ─────────────────────────────────────────────────────────────────────────────

def audit(action, actor=None, model=None, instance=None, changes=None,
          model_name="", object_id="", object_repr="", request=None):
    """Write an immutable AuditLog row (best-effort, never blocks the caller)."""
    try:
        from core.models import AuditLog

        model_name = model_name or (model.__name__ if model else "")
        if instance is not None:
            model_name = model_name or instance.__class__.__name__
            object_id = str(getattr(instance, "pk", ""))
            object_repr = str(instance)
        AuditLog.objects.create(
            user=actor,
            action=action,
            model_name=model_name,
            object_id=str(object_id),
            object_repr=str(object_repr)[:255],
            changes=dict(changes or {}),
            ip_address=getattr(request, "META", {}).get("REMOTE_ADDR", None)
            if request else None,
            user_agent=getattr(request, "META", {}).get("HTTP_USER_AGENT", "")[:300]
            if request else "",
            path=(request.path if request and hasattr(request, "path") else ""),
        )
    except Exception:
        # Audit failures must never take down a scheduling operation.
        pass


def notify_students(title, message, link="", students=None):
    """Create in-platform notifications for students (reuses students app)."""
    try:
        from students.models import StudentNotification

        if students is None:
            return 0
        created = 0
        for stu in students:
            try:
                StudentNotification.objects.create(
                    student=stu, category="general",
                    title=title, message=message, link=link,
                )
                created += 1
            except Exception:
                continue
        return created
    except Exception:
        return 0


def notify_staff(recipient, title, body, sender=None, link=""):
    """Reliable-enough staff notification via the messaging app's 1:1 thread."""
    try:
        from messaging.models import Conversation, Message

        body = f"{title}\n\n{body}\n{link}" if link else f"{title}\n\n{body}"
        convo = Conversation.objects.create(subject=title)
        convo.participants.add(recipient)
        if sender is not None:
            convo.participants.add(sender)
        Message.objects.create(
            conversation=convo,
            sender=sender,
            recipient=recipient,
            body=body,
        )
    except Exception:
        pass


def students_of_semester(semester):
    """Distinct active students enrolled in any offering of a semester."""
    from academics.models import Enrolment
    from django.contrib.auth import get_user_model

    ids = (
        Enrolment.objects
        .filter(offering__semester=semester, is_active=True, status="active")
        .values_list("student_id", flat=True)
        .distinct()
    )
    return get_user_model().objects.filter(pk__in=ids)


# ─────────────────────────────────────────────────────────────────────────────
# Schedule lifecycle (create / options / generate)
# ─────────────────────────────────────────────────────────────────────────────

def create_schedule(name, semester, schedule_type, actor, institution=None,
                    notes=""):
    """Create a DRAFT schedule for a semester."""
    schedule = AcademicSchedule.objects.create(
        name=name,
        semester=semester,
        schedule_type=schedule_type,
        institution=institution,
        notes=notes,
        status=ScheduleStatus.DRAFT,
    )
    audit(ACTIONS["CREATE"], actor, instance=schedule,
          changes={"semester": str(semester), "type": schedule_type})
    return schedule


def _config_for(schedule):
    return SchedulingConfig.for_institution(schedule.institution)


def _snapshot_before_change(schedule, actor, reason, is_published=False):
    """Snapshot current entries to a version before replacing them."""
    if schedule.entries.exists():
        ScheduleVersion.create_snapshot(
            schedule, actor=actor, reason=reason, is_published=is_published,
        )


def _persist_plan(schedule, plan, unplaced, actor):
    """Delete old entries and create new ones from a generator plan."""
    schedule.entries.all().delete()
    for item in plan:
        ScheduleEntry.objects.create(
            schedule=schedule,
            offering_id=item["offering_id"],
            course_id=item["course_id"],
            lecturer_id=item["lecturer_id"],
            room_id=item["room_id"],
            session_type=item["session_type"],
            day=item["day"],
            start_time=item["start_time"],
            end_time=item["end_time"],
            lecturer_name=item["lecturer_name"],
            room_name=item["room_name"],
            cohort_signature=item["cohort_sig"],
            is_locked=False,
            created_by=actor,
        )
    schedule.unplaced = len(unplaced)
    schedule.save(update_fields=["unplaced", "updated_at"])


def generate_schedule(schedule, actor=None, options=None, config=None,
                      persist=True, reason="Regeneration"):
    """
    Run the deterministic generator and persist the result.

    Rules:
      * An already-published / under-review schedule cannot be regenerated in
        place — callers must create a new schedule (or we snapshot first).
      * Snapshot created automatically before entries are replaced.
    """
    if schedule.is_published or schedule.is_locked:
        raise ValueError(
            _("A published or under-review schedule cannot be regenerated in "
              "place. Create a new schedule instead.")
        )
    config = config or _config_for(schedule)

    job = ScheduleGenerationJob.objects.create(
        schedule=schedule,
        request_options=json_safe(options or {}),
        requested_by=actor,
    )
    job.start()

    try:
        options = options or {}
        result = generator.run_generation(schedule, config, options)
        if persist:
            with transaction.atomic():
                _snapshot_before_change(
                    schedule, actor, reason=reason, is_published=False
                )
                _persist_plan(schedule, result["plan"], result["unplaced"], actor)
                schedule.status = ScheduleStatus.GENERATED
                schedule.generated_by = actor
                schedule.generated_at = timezone.now()
                schedule.generation_meta = json_safe({
                    "seed": result["seed"],
                    "unplaced": [u.get("reason") for u in result["unplaced"]],
                    "options": options,
                })
                schedule.save()

        stats = detect_conflicts(schedule, config)
        stats.update({
            "placed": result["placed"],
            "total": result["total"],
            "unplaced": len(result["unplaced"]),
            "seed": result["seed"],
        })
        job.complete(stats)
        audit(ACTIONS["UPDATE"], actor, instance=schedule,
              changes={"action": "generate", "stats": stats})
        return stats
    except Exception as exc:
        job.fail(str(exc))
        raise


def simulate_options(schedule, config=None, seeds=None):
    """
    Generate several alternative plans WITHOUT persisting, to let the user
    compare (via `schedule options` view) before choosing one.

    Each returned dict: {seed, placed, total, unplaced, score} — score is the
    estimated conflict_detector score of the plan.
    """
    from scheduling.engine.scoring import score_schedule

    config = config or _config_for(schedule)
    seeds = seeds or [config.seed, config.seed + 1, config.seed + 2]

    results = []
    for seed in seeds:
        res = generator.run_generation(
            schedule, config, {"seed": seed}
        )
        # estimate score without touching the DB
        class _P:
            def __init__(self, d):
                self.day = d["day"]
                self.start_time = d["start_time"]
                self.end_time = d["end_time"]
                self.cohort_signature = d["cohort_sig"]
                self.session_type = d["session_type"]
                self.room = None
                self.course = None
        plan_objs = [_P(d) for d in res["plan"]]
        results.append({
            "seed": seed,
            "placed": len(res["plan"]),
            "total": len(res["plan"]) + len(res["unplaced"]),
            "unplaced": len(res["unplaced"]),
            "reason_mix": sorted(
                {u.get("reason") for u in res["unplaced"]}
            ),
            "score": score_schedule(plan_objs, config),
        })
    return sorted(results, key=lambda r: (r["unplaced"], -r["score"]))


def optimize_schedule(schedule, actor=None, config=None, iterations=None):
    """Improve soft quality via the local-search optimizer."""
    config = config or _config_for(schedule)
    if schedule.is_published or schedule.is_locked:
        raise ValueError(
            _("Cannot optimise a published or under-review schedule in place.")
        )
    base_version = ScheduleVersion.create_snapshot(
        schedule, actor=actor, reason="Before optimisation"
    )
    result = optimizer.optimize_schedule(
        schedule, config,
        {"iterations": iterations} if iterations else None,
        actor=actor,
    )
    detect_conflicts(schedule, config)
    ScheduleVersion.create_snapshot(
        schedule, actor=actor, reason=f"After {result['moves']} moves", 
    )
    audit(ACTIONS["UPDATE"], actor, instance=schedule,
          changes={"action": "optimize", "stats": result})
    return result


def repair_schedule(schedule, actor=None, config=None):
    """Auto-repair hard conflicts (never touches locked entries)."""
    config = config or _config_for(schedule)
    if schedule.is_published:
        raise ValueError(_("Cannot repair a published schedule in place."))
    result = repair.repair_schedule(schedule, config, actor=actor)
    audit(ACTIONS["UPDATE"], actor, instance=schedule,
          changes={"action": "repair", "stats": result})
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Workflow transitions
# ─────────────────────────────────────────────────────────────────────────────

def _involved_departments(schedule):
    """Department ids touched by this schedule (via its entries' offerings).

    Before any entries exist it falls back to every department hosting an
    offering in the semester, so a freshly created schedule still has a
    sensible review audience.
    """
    offering_ids = list(
        schedule.entries.values_list("offering_id", flat=True)
    )
    qs = schedule.semester.offerings.filter(
        is_active=True, departments__isnull=False
    )
    if offering_ids:
        qs = qs.filter(pk__in=set(offering_ids))
    return set(qs.values_list("departments", flat=True).distinct())


def _hod_of(department):
    from academics.models import Department

    dept = Department.objects.filter(pk=department).select_related("hod").first()
    if dept is None:
        return None
    if dept.hod is not None and dept.hod.is_active:
        return dept.hod
    from accounts.models import StaffResponsibility, UserStaffRole

    role = (
        UserStaffRole.objects
        .filter(department=dept, responsibility=StaffResponsibility.HOD,
                is_active=True)
        .select_related("user")
        .first()
    )
    if role is not None and role.user.is_active:
        return role.user
    return None


def submit_for_review(schedule, actor):
    """Teacher/admin sends GENERATED (or reworked) schedule to HOD review."""
    if schedule.status not in (ScheduleStatus.DRAFT, ScheduleStatus.GENERATED,
                               ScheduleStatus.HOD_APPROVED,
                               ScheduleStatus.CORRECTION_REQUIRED):
        raise ValueError(_("Only draft or generated schedules can be submitted."))
    if schedule.semester.offerings.count() == 0:
        raise ValueError(_("No course offerings in this semester to schedule."))
    schedule.status = ScheduleStatus.UNDER_REVIEW
    schedule.submitted_by = actor
    schedule.submitted_at = timezone.now()
    # reset prior HOD decisions
    schedule.hod_approvals.all().delete()
    schedule.save()
    # notify HODs
    for dept_id in _involved_departments(schedule):
        hod = _hod_of(dept_id)
        if hod is None:
            continue
        notify_staff(
            hod,
            f"Timetable review requested: {schedule.name}",
            "A new timetable has been submitted and needs your department "
            "sign-off.",
            sender=actor,
            link="/scheduling/hod-approvals/",
        )
    audit(ACTIONS["UPDATE"], actor, instance=schedule,
          changes={"action": "submit_for_review"})
    return schedule


def hod_approve(schedule, hod, department, approve, comment="", actor=None):
    """HOD signs off (or objects to) the schedule for one of their departments."""
    from django.db import transaction

    actor = actor or hod
    from academics.models import Department

    if not isinstance(department, Department):
        department = Department.objects.get(pk=department)
    if department.pk not in _involved_departments(schedule):
        raise ValueError(_("Your department is not part of this timetable."))
    if not schedule.status == ScheduleStatus.UNDER_REVIEW:
        raise ValueError(
            _("This timetable is no longer awaiting your review "
              "(current status: %(s)s).")
            % {"s": schedule.get_status_display()}
        )
    if not hod.is_hod_of(department):
        raise ValueError(
            _("You are not the Head of Department for %(dept)s.") % {"dept": department}
        )
    if not approve and not (comment or "").strip():
        raise ValueError(
            _("A rejection reason is required before you can reject the timetable.")
        )

    with transaction.atomic():
        approval, created = ScheduleHodApproval.objects.get_or_create(
            schedule=schedule, department=department,
            defaults={"hod": hod},
        )
        if not created and approval.hod_id != hod.pk:
            approval.hod = hod
            approval.save(update_fields=["hod", "updated_at"])
        if approve:
            approval.approve(comment)
        else:
            approval.reject(comment)
            # Transient REJECTED → CORRECTION_REQUIRED: the schedule is pulled
            # back to the scheduler for rework and becomes editable again.
            schedule.status = ScheduleStatus.CORRECTION_REQUIRED
            schedule.save(update_fields=["status", "updated_at"])
            audit(ACTIONS["REJECT"], actor, instance=schedule,
                  changes={"department": department.code, "comment": comment})
            return approval

        audit(ACTIONS["APPROVE"], actor, instance=schedule,
              changes={"department": department.code})
        _refresh_after_hod(schedule, actor)
    return approval


def _refresh_after_hod(schedule, actor):
    involved = _involved_departments(schedule)
    approved_depts = set(
        schedule.hod_approvals.filter(approved=True)
        .values_list("department_id", flat=True)
    )
    if involved and approved_depts >= involved:
        schedule.status = ScheduleStatus.HOD_APPROVED
        schedule.save(update_fields=["status", "updated_at"])
        notify_staff(
            actor,
            f"HOD approval complete: {schedule.name}",
            "Every owning department has approved this timetable.",
            sender=actor,
            link="/scheduling/list/",
        )
        audit(ACTIONS["APPROVE"], actor, instance=schedule,
              changes={"action": "hod_all_approved"})


def hod_list_for(schedule):
    """Departments + signed-off state for the HOD approvals screen."""
    involved = _involved_departments(schedule)
    from academics.models import Department

    rows = []
    for dept in Department.objects.filter(pk__in=involved).order_by("code"):
        approval = schedule.hod_approvals.filter(department=dept).first()
        hod = _hod_of(dept.pk)
        rows.append({
            "department": dept,
            "hod": hod,
            "approval": approval,
        })
    return rows


def approve_schedule(schedule, actor, note=""):
    """Administrator confirms the HOD-approved schedule."""
    if schedule.status != ScheduleStatus.HOD_APPROVED:
        raise ValueError(_("HOD approval is required before final approval."))
    schedule.status = ScheduleStatus.APPROVED
    schedule.approved_by = actor
    schedule.approved_at = timezone.now()
    schedule.save()
    audit(ACTIONS["APPROVE"], actor, instance=schedule,
          changes={"action": "admin_approve", "note": note})


def publish_schedule(schedule, actor, notify=True):
    """
    Make the schedule public.

    Hard gate: zero hard conflicts AND zero unplaced sessions.  A published
    snapshot is stored; students and lecturers are notified.
    """
    config = _config_for(schedule)
    stats = detect_conflicts(schedule, config)
    schedule.refresh_from_db()
    if stats["hard_conflicts"] > 0 or schedule.unplaced > 0:
        raise ValueError(
            _("Cannot publish: %(h)d hard conflict(s) and %(u)d unplaced "
              "session(s).")
            % {"h": stats["hard_conflicts"], "u": schedule.unplaced}
        )
    if not schedule.is_published:
        schedule.status = ScheduleStatus.PUBLISHED
        schedule.published_by = actor
        schedule.published_at = timezone.now()
        schedule.save()  # save() maintains is_current uniqueness

        ScheduleVersion.create_snapshot(
            schedule, actor=actor, reason="Published version",
            is_published=True,
        )
        if notify:
            students = students_of_semester(schedule.semester)
            notified = notify_students(
                "New timetable published",
                f"{schedule.name} is now live. Check your personal timetable.",
                link="/scheduling/my-timetable/",
                students=students,
            )
            lecturers = list(
                ScheduleEntry.objects.filter(schedule=schedule)
                .exclude(lecturer=None)
                .values_list("lecturer_id", flat=True)
                .distinct()
            )
            from django.contrib.auth import get_user_model

            for lid in lecturers:
                stu = get_user_model().objects.filter(pk=lid).first()
                if stu:
                    notify_staff(
                        stu,
                        f"Timetable published: {schedule.name}",
                        "Your teaching timetable is live.",
                        sender=actor,
                        link="/scheduling/my-timetable/",
                    )
        audit(ACTIONS["APPROVE"], actor, instance=schedule,
              changes={"action": "publish", "notifications": notified
                       if notify else 0})
    return schedule


def unpublish_schedule(schedule, actor, note=""):
    """Withdraw a published schedule (keeps version history)."""
    if not schedule.is_published:
        raise ValueError(_("Schedule is not published."))
    schedule.status = ScheduleStatus.APPROVED
    schedule.is_current = False
    schedule.published_at = None
    schedule.published_by = None
    schedule.save()
    audit(ACTIONS["UPDATE"], actor, instance=schedule,
          changes={"action": "unpublish", "note": note})


def archive_schedule(schedule, actor, note=""):
    """Archive a schedule (published or not)."""
    schedule.status = ScheduleStatus.ARCHIVED
    schedule.is_current = False
    schedule.save()
    audit(ACTIONS["UPDATE"], actor, instance=schedule,
          changes={"action": "archive", "note": note})


# ─────────────────────────────────────────────────────────────────────────────
# Entries (manual / locked)
# ─────────────────────────────────────────────────────────────────────────────

def entry_cohort_signature(offering):
    return generator._cohort_signature(offering)


def add_entry(schedule, offering, day, start, end, session_type,
              room=None, lecturer=None, actor=None, cohort_sig=None):
    """Manually add a weekly session to a schedule."""
    if schedule.is_locked:
        raise ValueError(_("Schedule is locked; edits are not allowed."))
    entry = ScheduleEntry.objects.create(
        schedule=schedule,
        offering=offering,
        course=offering.course,
        lecturer=lecturer,
        room=room,
        session_type=session_type,
        day=day,
        start_time=start,
        end_time=end,
        lecturer_name=lecturer.get_full_name() if lecturer else "",
        room_name=str(room) if room else "",
        cohort_signature=cohort_sig or entry_cohort_signature(offering),
        created_by=actor,
    )
    detect_conflicts(schedule)
    audit(ACTIONS["CREATE"], actor, instance=entry,
          changes={"schedule": schedule.pk})
    return entry


def students_of_offering(offering):
    """Distinct active students enrolled in a single offering."""
    from django.contrib.auth import get_user_model

    ids = (
        offering.enrolments
        .filter(is_active=True, status="active")
        .values_list("student_id", flat=True)
        .distinct()
    )
    return get_user_model().objects.filter(pk__in=ids)


def update_entry(schedule, entry_id, actor, notify=True, **fields):
    """Update day/time/room/lecturer of a session (not locked, not published)."""
    from django.db import transaction

    if schedule.is_locked:
        raise ValueError(_("Schedule is locked; edits are not allowed."))
    entry = ScheduleEntry.objects.get(pk=entry_id, schedule=schedule)
    if entry.is_locked:
        raise ValueError(_("Entry is locked; edits are not allowed."))
    old = {
        "day": entry.day,
        "start_time": entry.start_time,
        "end_time": entry.end_time,
        "room_id": entry.room_id,
        "lecturer_id": entry.lecturer_id,
        "session_type": entry.session_type,
    }
    for field in ("day", "start_time", "end_time", "room", "lecturer",
                  "session_type"):
        if field in fields:
            setattr(entry, field, fields[field])
    if entry.lecturer_id:
        entry.lecturer_name = entry.lecturer.get_full_name()
    if entry.room_id:
        entry.room_name = str(entry.room)
    if entry.is_locked:
        raise ValueError(_("Entry is locked; edits are not allowed."))
    with transaction.atomic():
        entry.save()
        detect_conflicts(schedule)
        audit(ACTIONS["UPDATE"], actor, instance=entry,
              changes={"schedule": schedule.pk, "fields": list(fields)})
        if notify:
            entry.refresh_from_db()
            _notify_entry_change(schedule, entry, actor, old)
    return entry


def _notify_entry_change(schedule, entry, actor, old):
    """Notify the affected students + lecturer when a session moves."""
    def fmt(day, start, end):
        from scheduling.constants import IsoWeekday
        return f"{IsoWeekday(day).label} {start:%H:%M}–{end:%H:%M}"

    from_where = fmt(old["day"], old["start_time"], old["end_time"])
    to_where = fmt(entry.day, entry.start_time, entry.end_time)
    changes = []
    if old["day"] != entry.day or (
            old["start_time"], old["end_time"]) != (
            entry.start_time, entry.end_time):
        changes.append(f"moved  from  {from_where}  to  {to_where}")
    if old["room_id"] != entry.room_id:
        changes.append(f"room: {entry.room_name or '—'}")
    if old["lecturer_id"] != entry.lecturer_id and entry.lecturer_id:
        changes.append(f"lecturer: {entry.lecturer_name or '—'}")
    if not changes:
        return

    summary = "; ".join(changes) or "updated"
    students = students_of_offering(entry.offering)
    notify_students(
        f"Class change: {entry.course.code}",
        f"{entry.course.code} ({entry.get_session_type_display()}) "
        f"was {summary} in {schedule.name}.",
        link="/scheduling/my-timetable/",
        students=students,
    )
    if entry.lecturer_id:
        from django.contrib.auth import get_user_model

        lecturer = get_user_model().objects.filter(pk=entry.lecturer_id).first()
        if lecturer is not None:
            notify_staff(
                lecturer,
                f"Class change: {entry.course.code}",
                f"Your {entry.course.code} session was {summary} in "
                f"{schedule.name}.",
                sender=actor,
                link="/scheduling/my-timetable/",
            )


def delete_entry(schedule, entry_id, actor):
    if schedule.is_locked:
        raise ValueError(_("Schedule is locked; edits are not allowed."))
    entry = ScheduleEntry.objects.get(pk=entry_id, schedule=schedule)
    if entry.is_locked:
        raise ValueError(_("Entry is locked."))
    entry.delete()
    detect_conflicts(schedule)
    audit(ACTIONS["DELETE"], actor,
          changes={"schedule": schedule.pk, "entry": entry_id})


def lock_entry(schedule, entry_id, actor, locked=True):
    """Lock / unlock a single session (ignored for published schedules)."""
    entry = ScheduleEntry.objects.get(pk=entry_id, schedule=schedule)
    if schedule.is_published:
        raise ValueError(_("Published schedules are fully locked."))
    entry.is_locked = bool(locked)
    entry.save(update_fields=["is_locked", "updated_at"])
    audit(ACTIONS["UPDATE"], actor, instance=entry,
          changes={"is_locked": bool(locked)})
    return entry


# ─────────────────────────────────────────────────────────────────────────────
# Versions
# ─────────────────────────────────────────────────────────────────────────────

def list_versions(schedule):
    return schedule.versions.all()


def restore_version(schedule, version, actor):
    """Restore a saved snapshot (draft/generated schedules only)."""
    if schedule.is_published or schedule.is_locked:
        raise ValueError(
            _("Restore is only available on draft or generated schedules.")
        )
    version = schedule.versions.get(pk=version.pk)
    _snapshot_before_change(schedule, actor, "Snapshot before restore")
    version.to_entries(schedule)
    detect_conflicts(schedule)
    audit(ACTIONS["UPDATE"], actor, instance=schedule,
          changes={"action": "restore_version", "version": version.version_number})
    return schedule


def compare_versions(v1, v2):
    """Minimal structural diff of two snapshots."""
    from collections import Counter

    def keyed(snap):
        return {
            f"{e.get('day')}|{e.get('start')}|{e.get('offering_id')}"
            f"|{e.get('room_id')}"
            for e in snap.snapshot
        }

    return {
        "only_in_first": len(keyed(v1) - keyed(v2)),
        "only_in_second": len(keyed(v2) - keyed(v1)),
        "total_first": len(v1.snapshot),
        "total_second": len(v2.snapshot),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Queries / reporting
# ─────────────────────────────────────────────────────────────────────────────

def conflicts_report(schedule, config=None):
    """Grouped per-entry conflict report for the conflicts screen."""
    config = config or _config_for(schedule)
    entries = list(
        ScheduleEntry.objects.filter(schedule=schedule)
        .select_related("course", "room", "lecturer", "offering")
        .order_by("day", "start_time")
    )
    rows = []
    for entry in entries:
        detail = explain_entry(schedule, entry.pk)
        if detail.get("error"):
            continue
        if detail["hard_violations"] or detail["soft_violations"]:
            rows.append({
                "entry": entry,
                "hard": detail["hard_violations"],
                "soft": detail["soft_violations"],
            })
    # unplaced requirements (from last generation meta)
    unplaced_meta = schedule.generation_meta.get("unplaced", [])
    return {
        "rows": rows,
        "unplaced_count": schedule.unplaced,
        "unplaced_reasons": unplaced_meta,
        "hard_total": schedule.hard_conflicts,
        "soft_total": schedule.soft_conflicts,
    }


def timetable_for_user(user, schedule=None):
    """
    Role-aware personal timetable.

      admin  — all current schedules' entries (or the given/semester schedule)
      teacher— their own lectures (or where they hold a responsibility)
      student— entries for their enrolled offerings (via cohort signature)

    Students and lecturers only ever see PUBLISHED timetables; admins may
    preview any. Returns a dict {schedule, workdays, days: [...]}.
    """
    from scheduling.constants import IsoWeekday

    if schedule is None:
        qs = AcademicSchedule.objects.filter(
            is_current=True, schedule_type=ScheduleType.CLASS,
        )
        if user.role in ("student", "teacher") and not user.is_admin:
            # students/lecturers must not see drafts or unapproved previews
            qs = qs.filter(status=ScheduleStatus.PUBLISHED)
        schedule = qs.order_by("-semester__session__start_date").first()

    if schedule is None:
        return {"schedule": None, "days": []}

    qs = ScheduleEntry.objects.filter(schedule=schedule)
    if user.role == "student":
        offerings = set(user.enrolments.filter(
            offering__semester=schedule.semester, is_active=True, status="active",
        ).values_list("offering_id", flat=True))
        if offerings:
            qs = qs.filter(offering_id__in=offerings)
        elif hasattr(user, "academic_profile") and user.academic_profile:
            sigs = set()
            for o in schedule.semester.offerings.filter(
                level=user.academic_profile.current_level
            ):
                dept_ids = sorted(set(o.departments.values_list("pk", flat=True)))
                if dept_ids:
                    sigs.add(generator._cohort_signature(o))
            qs = qs.filter(cohort_signature__in=sigs)
        else:
            qs = qs.none()
    elif user.role == "teacher":
        qs = qs.filter(lecturer=user)
    else:
        qs = qs  # admins see everything

    entries = list(qs.select_related("course", "room", "lecturer").order_by(
        "day", "start_time"
    ))
    days = [
        {"label": IsoWeekday(i).label, "weekday": i,
         "entries": [e for e in entries if e.day == i]}
        for i in range(7)
    ]
    return {"schedule": schedule, "days": days}


def health_check(institution):
    """Command-center readiness metrics for the dashboard."""
    from academics.models import Semester
    from scheduling.models import (
        AcademicEvent,
        LecturerUnavailability,
        Room,
        TimeSlot,
    )

    room_count = Room.objects.filter(
        building__institution=institution, is_active=True,
    ).count()
    slots = TimeSlot.objects.filter(is_default=True, is_active=True).count()
    unavail = LecturerUnavailability.objects.filter(is_active=True).count()
    blocked_days = AcademicEvent.objects.filter(
        institution=institution, affects_scheduling=True, all_day=True,
    ).count()
    current = Semester.get_current()
    blocked_q = AcademicEvent.objects.filter(
        affects_scheduling=True, all_day=True,
    )
    if institution is None:
        blocked_q = blocked_q.filter(institution__isnull=True)
    else:
        blocked_q = blocked_q.filter(
            Q(institution=institution) | Q(institution__isnull=True)
        )
    blocked_days = blocked_q.count()
    return {
        "room_count": room_count,
        "slots": slots,
        "unavailability_windows": unavail,
        "blocked_days_events": blocked_days,
        "has_config": SchedulingConfig.objects.filter(institution=institution).exists(),
        "current_semester": current,
        "ok": room_count > 0 and slots > 0 and current is not None,
    }


def schedule_health_stats():
    """Aggregate scheduling KPIs for the command-center dashboard."""
    from django.db.models import Count

    from scheduling.models import AcademicSchedule, ScheduleEntry

    current = ScheduleEntry.objects.filter(
        schedule__status=ScheduleStatus.PUBLISHED,
        schedule__is_current=True,
    )
    placed = current.count()
    rooms_in_use = (
        current.exclude(room=None).values("room_id").distinct().count()
    )
    lecturers_teaching = (
        current.exclude(lecturer=None).values("lecturer_id").distinct().count()
    )
    offerings_covered = (
        current.values("offering_id").distinct().count()
    )
    active = AcademicSchedule.objects.exclude(
        status__in=(ScheduleStatus.PUBLISHED, ScheduleStatus.ARCHIVED)
    )
    agg = active.aggregate(
        hard=Sum("hard_conflicts"), unplaced_sum=Sum("unplaced"),
    )
    under_review = active.filter(status=ScheduleStatus.UNDER_REVIEW).count()
    return {
        "placed": placed,
        "rooms_in_use": rooms_in_use,
        "lecturers_teaching": lecturers_teaching,
        "offerings_covered": offerings_covered,
        "hard_conflicts": agg["hard"] or 0,
        "unplaced": agg["unplaced_sum"] or 0,
        "under_review": under_review,
        "correction_required": active.filter(
            status=ScheduleStatus.CORRECTION_REQUIRED).count(),
    }


def hod_department_stats(hod):
    """Teaching + approval load for the HOD dashboard (their departments)."""
    from django.db.models import Count

    from scheduling.models import AcademicSchedule

    dept_ids = set(hod.get_hod_departments().values_list("pk", flat=True))
    schedules = AcademicSchedule.objects.filter(
        status=ScheduleStatus.UNDER_REVIEW,
        semester__offerings__departments__in=dept_ids,
    ).select_related("semester__session").distinct()

    approvals = []
    for schedule in schedules:
        approvals.append({
            "schedule": schedule,
            "rows": [
                a for a in hod_list_for(schedule)
                if a["department"].pk in dept_ids
            ],
        })

    entries = ScheduleEntry.objects.filter(
        offering__departments__in=dept_ids,
        schedule__is_current=True,
        schedule__status=ScheduleStatus.PUBLISHED,
    )
    return {
        "department_ids": sorted(dept_ids),
        "pending_approvals": approvals,
        "total_entries": entries.count(),
        "lecturers": entries.exclude(lecturer=None).values(
            "lecturer_id").distinct().count(),
        "rooms": entries.exclude(room=None).values("room_id").distinct().count(),
        "courses": entries.values("course_id").distinct().count(),
    }


def calendar_items(user, *, semester=None, filters=None, start=None, end=None):
    """
    Build the union of AcademicEvent + ScheduleEntry items a user may see.

    Scope rules:
      * students   → published timetable of their *enrolled* offerings only
      * lecturers  → published timetable rows where they teach
      * HOD        → published timetable of their departments (+ admin access)
      * admins     → any schedule in the semester (status filterable)

    `filters` keys (all optional): schedule, status, dept, program, level,
    course, lecturer, room, type.

    Returns dict: {items, active_schedule, schedules, event_count, class_count}
    where each item is {kind: 'event'|'class', ...} normalised for rendering.
    """
    from datetime import date as date_cls

    from academics.models import Semester

    filters = filters or {}
    semester = semester or Semester.get_current()

    # ── schedules eligible in this semester ─────────────────────────────────
    schedules = AcademicSchedule.objects.filter(semester=semester).order_by(
        "-is_current", "-updated_at"
    )

    if user.role in ("student", "teacher") and not user.is_admin:
        schedules = schedules.filter(status=ScheduleStatus.PUBLISHED)
    elif filters.get("status"):
        schedules = schedules.filter(status=filters["status"])

    active_schedule = None
    sched_id = filters.get("schedule")
    if sched_id:
        candidate = schedules.filter(pk=sched_id).first()
        if candidate is not None:
            active_schedule = candidate
    active_schedule = active_schedule or (
        schedules.filter(is_current=True).first() or schedules.first()
    )

    # ── schedules entries scoped + filtered ─────────────────────────────────
    entry_qs = (
        ScheduleEntry.objects.filter(schedule=active_schedule)
        if active_schedule else ScheduleEntry.objects.none()
    )
    entry_qs = _scope_entries_for_user(entry_qs, user, semester)
    entry_qs = _apply_entry_filters(entry_qs, filters)

    entries = list(
        entry_qs.select_related(
            "course", "room", "lecturer", "offering",
            "offering__level", "offering__level__program",
        )
        .order_by("day", "start_time")
    )

    # ── academic events (institution-wide + scoped) ─────────────────────────
    events = _visible_events(user, semester, filters)

    # ── date window (used to expand weekly classes + multi-day events) ───────
    start = start or (active_schedule.semester.start_date
                      if active_schedule else semester.start_date)
    end = end or (active_schedule.semester.end_date
                  if active_schedule else semester.end_date)
    # In-memory model instances can hold raw date strings until re-read from
    # the DB, so normalise defensively before any date arithmetic.
    if not isinstance(start, date_cls):
        start = date_cls.fromisoformat(str(start))
    if not isinstance(end, date_cls):
        end = date_cls.fromisoformat(str(end))

    items = []
    # Expand events within [start, end]
    for ev in events:
        ev_start = ev.start_date
        ev_end = ev.effective_end_date
        if ev_end < start or ev_start > end:
            continue
        d0 = max(start, ev_start)
        d1 = min(end, ev_end)
        _emit_event(items, ev, d0, d1)

    # Expand weekly classes across [start, end] (cap to avoid huge lists)
    horizon = min((end - start).days, 90)
    for entry in entries:
        day_offset = entry.day - int(start.weekday())
        first_occ = start + timedelta(days=day_offset % 7)
        occ = first_occ
        placed = 0
        while occ <= end and placed <= horizon:
            if occ >= start:
                items.append(_class_item(entry, occ, active_schedule))
                placed += 1
            occ += timedelta(days=7)

    items.sort(key=lambda it: (it["date"], it.get("all_day", 0), it.get("time") or ""))

    return {
        "items": items,
        "active_schedule": active_schedule,
        "schedules": schedules,
        "event_count": sum(1 for it in items if it["kind"] == "event"),
        "class_count": sum(1 for it in items if it["kind"] == "class"),
    }


def _scope_entries_for_user(qs, user, semester):
    if user.role == "student":
        offering_ids = list(
            user.enrolments.filter(
                offering__semester=semester, is_active=True, status="active",
            ).values_list("offering_id", flat=True)
        )
        return qs.filter(offering_id__in=offering_ids) if offering_ids else qs.none()
    # HODs (who are also teachers) are responsible for the whole department
    # timetable, not just their own lectures.
    dept_ids = list(user.get_hod_departments().values_list("pk", flat=True))
    if dept_ids:
        return qs.filter(offering__departments__in=dept_ids)
    if user.role == "teacher":
        return qs.filter(lecturer=user)
    return qs


def _apply_entry_filters(qs, filters):
    if filters.get("dept"):
        qs = qs.filter(offering__departments__pk=filters["dept"])
    if filters.get("program"):
        qs = qs.filter(offering__level__program_id=filters["program"])
    if filters.get("level"):
        qs = qs.filter(offering__level_id=filters["level"])
    if filters.get("course"):
        qs = qs.filter(course_id=filters["course"])
    if filters.get("lecturer"):
        qs = qs.filter(lecturer_id=filters["lecturer"])
    if filters.get("room"):
        qs = qs.filter(room_id=filters["room"])
    return qs


def _visible_events(user, semester, filters):
    from django.db.models import Q

    from academics.models import Institution
    from scheduling.models import AcademicEvent

    # Events tied to the (single) configured institution, or global (null).
    inst = Institution.objects.first()
    ev_qs = AcademicEvent.objects.filter(
        Q(institution=inst) | Q(institution__isnull=True)
    )
    if not (user.is_admin or user.is_hod):
        ev_qs = ev_qs.filter(is_public=True)

    scope_ids = _user_scope_ids(user, semester)
    type_filter = filters.get("type")
    if type_filter:
        ev_qs = ev_qs.filter(event_type=type_filter)

    out = []
    for ev in ev_qs.select_related("programme", "department", "level",
                                   "offering"):
        if not _event_visible(ev, user, scope_ids):
            continue
        out.append(ev)
    return out


def _user_scope_ids(user, semester):
    """{(dept), (program), (level), (offering)} ids relevant to the user."""
    depts, programs, levels, offerings = set(), set(), set(), set()

    def absorb(offering):
        offerings.add(offering.pk)
        for d in offering.departments.all():
            depts.add(d.pk)
        if offering.level_id:
            levels.add(offering.level_id)
            if offering.level.program_id:
                programs.add(offering.level.program_id)

    if user.role == "student":
        enrollments = user.enrolments.filter(
            offering__semester=semester, is_active=True, status="active",
        ).select_related("offering__level__program", "offering__level")
        for enr in enrollments:
            absorb(enr.offering)
        return depts, programs, levels, offerings

    if user.role == "teacher":
        from academics.models import TeacherDepartment

        depts |= set(TeacherDepartment.objects.filter(
            teacher=user, is_active=True
        ).values_list("department_id", flat=True))
        for allocation in user.course_allocations.filter(
            is_active=True, offering__semester=semester
        ).select_related("offering__level__program", "offering__level"):
            absorb(allocation.offering)
        depts |= set(user.get_hod_departments().values_list("pk", flat=True))
        return depts, programs, levels, offerings

    # HOD / staff: everything under their departments
    for dept in user.get_hod_departments():
        depts.add(dept.pk)
        for prog in dept.programs.all():
            programs.add(prog.pk)
            for level in prog.levels.all():
                levels.add(level.pk)
        for offering in dept.offerings.all():
            offerings.add(offering.pk)
    return depts, programs, levels, offerings


def _event_visible(ev, user, scope_ids):
    from scheduling.constants import EventScope

    depts, programs, levels, offerings = scope_ids
    if ev.scope == EventScope.ALL:
        return True
    if user.is_admin:
        return True
    if ev.scope == EventScope.PROGRAMME:
        return ev.programme_id in programs
    if ev.scope == EventScope.DEPARTMENT:
        return ev.department_id in depts
    if ev.scope == EventScope.LEVEL:
        return ev.level_id in levels
    if ev.scope == EventScope.OFFERING:
        return ev.offering_id in offerings
    return False


def _class_item(entry, occ_date, schedule):
    offering = entry.offering if entry.offering_id else None
    return {
        "kind": "class",
        "entry_id": entry.pk,
        "title": entry.course.code if entry.course else (
            entry.offering.course.code if entry.offering_id else "Class"),
        "course_title": entry.course.title if entry.course else "",
        "session_type": entry.get_session_type_display(),
        "session_type_key": entry.session_type,
        "date": occ_date,
        "time": f"{entry.start_time:%H:%M}–{entry.end_time:%H:%M}",
        "day": entry.day,
        "lecturer": entry.lecturer_name or (entry.lecturer.get_full_name()
                                            if entry.lecturer_id else ""),
        "room": entry.room_name or (str(entry.room) if entry.room_id else ""),
        "schedule_id": schedule.pk if schedule else entry.schedule_id,
        "schedule_name": schedule.name if schedule else "",
        "schedule_status": schedule.get_status_display() if schedule else "",
        "offering_id": entry.offering_id,
        "programme": (offering.level.program.code
                      if offering and offering.level_id and offering.level.program_id
                      else ""),
        "level": (offering.level.name if offering and offering.level_id else ""),
        "start_min": entry.start_time.hour * 60 + entry.start_time.minute,
        "end_min": entry.end_time.hour * 60 + entry.end_time.minute,
        "all_day": False,
    }


def _emit_event(items, ev, d0, d1):
    from datetime import date as date_cls

    start_min = (ev.start_time.hour * 60 + ev.start_time.minute
                 if ev.start_time else None)
    end_min = (ev.end_time.hour * 60 + ev.end_time.minute
               if ev.end_time else None)
    if start_min is None:
        start_min, end_min = 0, 1440
    elif end_min is None:
        end_min = min(start_min + 60, 1440)

    cursor = d0
    while cursor <= d1:
        items.append({
            "kind": "event",
            "entry_id": ev.pk,
            "title": ev.title,
            "description": ev.description,
            "event_type": ev.event_type,
            "event_type_label": ev.get_event_type_display(),
            "scope_label": ev.get_scope_display(),
            "date": cursor,
            "all_day": ev.all_day,
            "time": (f"{ev.start_time:%H:%M}" if ev.start_time else "") + (
                f"–{ev.end_time:%H:%M}" if ev.end_time else ""),
            "blocks_scheduling": ev.affects_scheduling,
            "is_public": ev.is_public,
            "recurrence": ev.get_recurrence_display(),
            "start_min": start_min,
            "end_min": end_min,
        })
        cursor += timedelta(days=1)


def json_safe(value):
    """Ensure JSONField-compatible data (times/objects → primitives)."""
    import datetime

    if isinstance(value, datetime.time):
        return value.strftime("%H:%M")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "pk"):
        return value.pk
    return value