"""
academics/services.py

Central Academic Calculation Engine — the single authoritative source for:

  * grade resolution          score → letter grade → grade point
  * weighted totals           (CA + Exam, per result-sheet weights)
  * semester GPA / CGPA       and persistence onto the student profile

Design rules:
  * No other module re-implements these formulas; call into this engine.
  * Grading is configurable via the `GradingScheme` / `GradeBoundary`
    models.  When no boundaries are configured (or the new tables have not
    been migrated yet) the engine transparently falls back to the legacy
    hard-coded scale (teachers.models.GRADE_POINTS / compute_grade), so
    existing behaviour is preserved in every environment.
  * All DB access uses lazy imports to avoid circular imports between the
    academics and teachers apps.
"""

from decimal import Decimal

from django.db.utils import OperationalError, ProgrammingError

_DB_UNAVAILABLE = (OperationalError, ProgrammingError)


# ── Scheme resolution ─────────────────────────────────────────────────────────

def get_grade_scheme(name=None):
    """
    Return the active GradingScheme to use.

    Prefers the scheme matching `name`; otherwise the default scheme, then
    any single active scheme.  Returns None when no configuration exists
    (or the grading tables are not yet migrated).
    """
    from .models import GradingScheme

    try:
        if name:
            scheme = GradingScheme.objects.filter(name=name, is_active=True).first()
        else:
            scheme = (
                GradingScheme.objects.filter(is_default=True, is_active=True).first()
                or GradingScheme.objects.filter(is_active=True).order_by("name").first()
            )
    except _DB_UNAVAILABLE:
        return None
    return scheme


def active_boundaries(scheme):
    """Ordered (descending min_score) active boundaries for a scheme."""
    if scheme is None:
        return []
    try:
        return list(
            scheme.boundaries.filter(is_active=True).order_by("-min_score")
        )
    except _DB_UNAVAILABLE:
        return []


# ── Grade resolution ──────────────────────────────────────────────────────────

def _legacy_scale():
    """The historical hard-coded scale, used only as a fallback."""
    from teachers.models import GRADE_POINTS, compute_grade

    return GRADE_POINTS, compute_grade


def resolve_grade(score, scheme=None):
    """
    Map a 0–100 total score to (letter grade, grade point).

    Resolves against the configurable boundaries of `scheme` (inclusive of
    min_score, exclusive of max_score).  Falls back to the legacy hard-coded
    scale when no boundary matches or no scheme is configured.
    """
    if score is None:
        return "", Decimal("0.0")

    score = Decimal(str(score))
    for boundary in active_boundaries(scheme or get_grade_scheme()):
        if score < boundary.min_score:
            continue
        if boundary.max_score is None or score < boundary.max_score:
            return boundary.grade, boundary.grade_point

    GRADE_POINTS, compute_grade = _legacy_scale()
    grade = compute_grade(float(score))
    return grade, Decimal(str(GRADE_POINTS.get(grade, 0.0)))


# ── Weighted totals ───────────────────────────────────────────────────────────

def compute_total(ca_score, exam_score, ca_weight=30, exam_weight=70):
    """
    Weighted total (0–100), rounded to 2 decimal places.

    ca_score / exam_score are each out of 100; ca_weight + exam_weight = 100.
    Returns None when both component scores are missing.
    """
    if ca_score is None and exam_score is None:
        return None

    ca = float(ca_score or 0)
    exam = float(exam_score or 0)
    total = round((ca * ca_weight) / 100 + (exam * exam_weight) / 100, 2)
    return Decimal(str(total))


def compute_result(result):
    """
    Recompute a StudentResult's total, grade and grade point in place
    (no save).  Uses the weights and scheme carried by its result sheet.
    """
    sheet = result.result_sheet
    total = compute_total(
        result.ca_score, result.exam_score,
        ca_weight=sheet.ca_weight, exam_weight=sheet.exam_weight,
    )
    if total is None:
        return result

    grade, grade_point = resolve_grade(total, get_grade_scheme(sheet.grading_scheme))
    result.total_score = total
    result.grade = grade
    result.grade_point = grade_point
    return result


# ── GPA / CGPA ────────────────────────────────────────────────────────────────

def _gpa_from_results(results):
    """Canonical weighted GPA over an iterable of StudentResults."""
    total_points = Decimal("0")
    total_credits = 0
    for result in results:
        if result.grade_point is None:
            continue
        credits = result.enrolment.offering.course.credit_units
        total_points += Decimal(str(result.grade_point)) * credits
        total_credits += credits

    if total_credits == 0:
        return None
    return round(total_points / total_credits, 2)


def calculate_semester_gpa(student, semester):
    """
    GPA for one semester: sum(grade_point × credits) / sum(credits)
    across all approved results in that semester.
    """
    from teachers.models import StudentResult

    results = StudentResult.objects.filter(
        enrolment__student=student,
        enrolment__offering__semester=semester,
        result_sheet__status="approved",
        grade_point__isnull=False,
    ).select_related("enrolment__offering__course")
    return _gpa_from_results(results)


def calculate_cgpa(student):
    """Cumulative GPA across all approved results of the student."""
    from teachers.models import StudentResult

    results = StudentResult.objects.filter(
        enrolment__student=student,
        result_sheet__status="approved",
        grade_point__isnull=False,
    ).select_related("enrolment__offering__course")
    return _gpa_from_results(results)


def update_student_gpa(student):
    """Persist the student's CGPA onto their academic profile."""
    from .models import StudentProfile

    cgpa = calculate_cgpa(student)
    if cgpa is None:
        return
    try:
        profile = student.academic_profile
        profile.cumulative_gpa = cgpa
        profile.save(update_fields=["cumulative_gpa", "updated_at"])
    except StudentProfile.DoesNotExist:
        pass


# ── Progression & graduation engine ──────────────────────────────────────────

def _session_results(student, session):
    """Approved StudentResults for a student across one academic session."""
    from teachers.models import StudentResult

    return (
        StudentResult.objects.filter(
            enrolment__student=student,
            enrolment__offering__semester__session=session,
            result_sheet__status="approved",
            grade_point__isnull=False,
        ).select_related(
            "enrolment__offering__course",
            "enrolment__offering__semester__session",
        )
    )


def session_gpa(student, session):
    """Weighted GPA across all approved results in an academic session."""
    return _gpa_from_results(_session_results(student, session))


def session_credits_attempted(student, session):
    """Total credit units attempted in a session (approved results only)."""
    return sum(
        r.enrolment.offering.course.credit_units
        for r in _session_results(student, session)
    )


def session_credits_passed(student, session):
    """Credit units passed in a session (grades outside F / I / W)."""
    return sum(
        r.enrolment.offering.course.credit_units
        for r in _session_results(student, session)
        if r.grade not in ("F", "I", "W")
    )


def failed_courses_in_session(student, session):
    """Dicts for courses failed (F / I / W) in a session, approved results only."""
    from teachers.models import StudentResult

    results = StudentResult.objects.filter(
        enrolment__student=student,
        enrolment__offering__semester__session=session,
        result_sheet__status="approved",
        grade__in=("F", "I", "W"),
    ).select_related("enrolment__offering__course")
    return [
        {
            "course":     r.enrolment.offering.course.code,
            "title":      r.enrolment.offering.course.title,
            "credits":    r.enrolment.offering.course.credit_units,
            "grade":      r.grade,
            "course_id":  r.enrolment.offering.course_id,
        }
        for r in results
    ]


def get_progression_policy(program=None):
    """
    Resolve the active ProgressionPolicy for `program`.

    Order of preference:
      1. a policy explicitly configured for the program,
      2. the institution-wide default (program = NULL),
      3. any single active policy.
    Returns None (no rules configured / tables not migrated yet).
    """
    from .models import ProgressionPolicy

    try:
        queryset = ProgressionPolicy.objects.filter(is_active=True)
        if program:
            policy = queryset.filter(program=program).first()
            if policy:
                return policy
        policy = (
            queryset.filter(program__isnull=True).first()
            or queryset.order_by("program").first()
        )
    except _DB_UNAVAILABLE:
        return None
    return policy


def is_final_level(profile):
    """True when the student sits at the highest active level of their programme."""
    from .models import Level

    if not profile or not profile.program or not profile.current_level:
        return False
    try:
        higher = Level.objects.filter(
            program=profile.program,
            order__gt=profile.current_level.order,
            is_active=True,
        ).exists()
    except _DB_UNAVAILABLE:
        return False
    return not higher


def next_level(profile):
    """The next active level after the student's current one, if any."""
    from .models import Level

    if not profile or not profile.program or not profile.current_level:
        return None
    try:
        return Level.objects.filter(
            program=profile.program,
            order=profile.current_level.order + 1,
            is_active=True,
        ).first()
    except _DB_UNAVAILABLE:
        return None


def _progression_thresholds(policy):
    """Float thresholds taken from a policy (with sensible defaults)."""
    if policy is None:
        return {
            "min_advance": 1.0,
            "probation":   0.5,
            "withdraw":    0.2,
            "max_failed":  2,
            "min_credits": 30,
        }
    return {
        "min_advance": float(policy.min_cgpa_to_advance),
        "probation":   float(policy.probation_threshold),
        "withdraw":    float(policy.withdraw_threshold),
        "max_failed":  policy.max_failed_per_year,
        "min_credits": policy.min_credits_per_year,
    }


def evaluate_progression(student, session, policy=None):
    """
    Evaluate a student's annual academic standing for `session`.

    Returns a dict:
        student, profile, session,
        session_gpa, cgpa, credits_attempted, credits_passed,
        pass_ratio, failed_courses, decision, reasons, next_level, policy
    """
    from .models import StudentProfile

    try:
        profile = student.academic_profile
    except StudentProfile.DoesNotExist:
        profile = None

    if profile is None or not profile.current_level:
        return {
            "student":  student,
            "profile":  profile,
            "session":  session,
            "decision": "DEFERRED",
            "reasons":  ["No active academic profile / level assignment."],
            "session_gpa": None,
            "cgpa": None,
            "credits_attempted": 0,
            "credits_passed": 0,
            "pass_ratio": 0.0,
            "failed_courses": [],
            "next_level": None,
            "policy": policy,
        }

    policy = policy or get_progression_policy(profile.program)
    thresholds = _progression_thresholds(policy)

    sgpa  = session_gpa(student, session)
    cgpa  = calculate_cgpa(student)
    attempted = session_credits_attempted(student, session)
    passed    = session_credits_passed(student, session)
    failed    = failed_courses_in_session(student, session)
    pass_ratio = round(passed / attempted, 2) if attempted else None

    reasons = []
    if sgpa is None:
        decision = "DEFERRED"
        reasons.append("No approved results recorded for the session.")
    elif sgpa < thresholds["withdraw"]:
        decision = "WITHDRAWN"
        reasons.append(
            f"Session GPA {sgpa} is below the withdrawal threshold "
            f"{thresholds['withdraw']}."
        )
    elif len(failed) > thresholds["max_failed"]:
        decision = "REPEAT"
        reasons.append(
            f"{len(failed)} failed course(s) exceed the maximum of "
            f"{thresholds['max_failed']}."
        )
    elif sgpa < thresholds["probation"]:
        decision = "PROBATION"
        reasons.append(
            f"Session GPA {sgpa} is below the probation threshold "
            f"{thresholds['probation']}."
        )
    elif sgpa < thresholds["min_advance"]:
        decision = "PROBATION"
        reasons.append(
            f"Session GPA {sgpa} is below the minimum {thresholds['min_advance']} "
            f"required to advance."
        )
    else:
        nxt = next_level(profile)
        if nxt is None and is_final_level(profile):
            decision = "COMPLETED"
            reasons.append("Final level reached; student qualifies for graduation.")
        else:
            decision = "ADVANCE"
            reasons.append("Session performance meets progression requirements.")

    return {
        "student":  student,
        "profile":  profile,
        "session":  session,
        "session_gpa":   sgpa,
        "cgpa":          cgpa,
        "credits_attempted": attempted,
        "credits_passed":    passed,
        "pass_ratio":        pass_ratio,
        "failed_courses":    failed,
        "decision":          decision,
        "reasons":           reasons,
        "next_level":        next_level(profile) if decision == "ADVANCE" else None,
        "policy":            policy,
    }


def apply_progression(student, session, outcome, actor=None):
    """
    Persist a progression outcome and, when advancing, move the student to
    the next level and accumulate their session credits.
    """
    from django.utils import timezone

    from .models import StudentStatus

    profile = outcome["profile"]
    status, created = StudentStatus.objects.update_or_create(
        student=student,
        session=session,
        defaults={
            "level_at_decision": profile.current_level if profile else None,
            "decision":          outcome["decision"],
            "session_gpa":       outcome["session_gpa"],
            "cgpa_at_decision":  outcome["cgpa"],
            "credits_earned":    outcome["credits_passed"],
            "next_level":        outcome.get("next_level"),
            "reasons":           "\n".join(outcome["reasons"]),
            "reviewed_by":       actor,
            "reviewed_at":       timezone.now(),
        },
    )

    if profile is None or not created:
        # Already decided for this session: never double-advance or
        # double-accumulate credits on a re-run.
        return status

    if outcome["decision"] == "ADVANCE" and outcome.get("next_level"):
        profile.current_level = outcome["next_level"]
    profile.total_credits_earned = profile.total_credits_earned + outcome["credits_passed"]
    profile.save(update_fields=["current_level", "total_credits_earned", "updated_at"])

    return status


def run_progression_for_session(session, students=None, actor=None, dry_run=False):
    """
    Evaluate (and optionally apply) progression for all active students in
    a session.  Returns a summary dict of decisions.
    """
    from .models import StudentProfile

    profiles = StudentProfile.objects.filter(
        is_active=True,
        current_level__isnull=False,
    )
    if students is not None:
        profiles = profiles.filter(student__in=students)

    summary = {}
    try:
        profiles = profiles.select_related("program", "current_level")
        for profile in profiles:
            outcome = evaluate_progression(profile.student, session)
            decision = outcome["decision"]
            summary[decision] = summary.get(decision, 0) + 1
            if not dry_run:
                apply_progression(profile.student, session, outcome, actor=actor)
    except _DB_UNAVAILABLE:
        pass
    return summary


# ── Graduation ────────────────────────────────────────────────────────────────

AWARD_CLASS_THRESHOLDS = (
    ("first",         Decimal("3.60")),
    ("second_upper",  Decimal("3.00")),
    ("second_lower",  Decimal("2.50")),
    ("third",         Decimal("2.00")),
    ("pass",          Decimal("1.00")),
)


def _award_class(cgpa):
    """Map a CGPA to an award class code ("" when below pass)."""
    if cgpa is None:
        return ""
    for code, threshold in AWARD_CLASS_THRESHOLDS:
        if cgpa >= threshold:
            return code
    return "not_awarded"


def evaluate_graduation(student):
    """
    Assess whether a student is eligible to graduate.

    Rules:
      * must have an active profile with a program,
      * must have reached the final level,
      * CGPA >= 1.00,
      * no outstanding failures (F/I/W without a passing retake).
    """
    from .models import StudentProfile

    try:
        profile = student.academic_profile
    except StudentProfile.DoesNotExist:
        profile = None

    reasons = []
    eligible = True

    if profile is None or not profile.program:
        reasons.append("No academic profile / programme assigned.")
        eligible = False
    elif not is_final_level(profile):
        reasons.append("Student has not reached the final level of the programme.")
        eligible = False

    cgpa = calculate_cgpa(student)
    if cgpa is None:
        reasons.append("No approved results available.")
        eligible = False
    elif cgpa < Decimal("1.00"):
        reasons.append(f"CGPA {cgpa} is below the 1.00 graduation minimum.")
        eligible = False

    if profile is not None:
        outstanding = profile.check_graduation_eligibility()
        if outstanding["failed_without_retake"] or outstanding["failed_with_failed_retake"]:
            count = (
                len(outstanding["failed_without_retake"])
                + len(outstanding["failed_with_failed_retake"])
            )
            reasons.append(f"{count} outstanding failed course(s) not cleared.")
            eligible = False

    return {
        "student":   student,
        "profile":   profile,
        "eligible":  eligible,
        "cgpa":      cgpa,
        "award_class": _award_class(cgpa) if eligible else "",
        "total_credits": profile.total_credits_earned if profile else 0,
        "reasons":   reasons,
    }


def create_graduation_record(student, session, actor=None):
    """
    Create/update a GraduationRecord for a student in `session`.
    Confirmed when eligible, rejected otherwise.
    """
    from django.utils import timezone

    from .models import GraduationRecord

    outcome = evaluate_graduation(student)
    profile = outcome["profile"]
    if profile is None or not profile.program:
        return None

    record, _ = GraduationRecord.objects.update_or_create(
        student=student,
        program=profile.program,
        session=session,
        defaults={
            "level_at_graduation": profile.current_level,
            "cgpa":          outcome["cgpa"],
            "total_credits": outcome["total_credits"],
            "eligible":      outcome["eligible"],
            "award_class":   outcome["award_class"],
            "status":        "confirmed" if outcome["eligible"] else "rejected",
            "reasons":       "\n".join(outcome["reasons"]) or "Eligible for graduation.",
            "reviewed_by":   actor,
            "reviewed_at":   timezone.now(),
        },
    )
    return record


def process_graduation_candidates(session, students=None, actor=None, dry_run=False):
    """
    Evaluate graduation eligibility and create graduation records for all
    active final-level students.  Returns a summary dict.
    """
    from django.contrib.auth import get_user_model

    from .models import StudentProfile

    profiles = StudentProfile.objects.filter(
        is_active=True,
        program__isnull=False,
        current_level__isnull=False,
    ).select_related("program", "current_level")

    if students is not None:
        profiles = profiles.filter(student__in=students)

    summary = {"confirmed": 0, "rejected": 0, "records": 0}
    try:
        user = actor
        if user is None:
            User = get_user_model()
            user = User.objects.filter(is_superuser=True).order_by("pk").first()
        for profile in profiles:
            if not is_final_level(profile):
                continue
            record = create_graduation_record(profile.student, session, actor=user)
            if record is None:
                continue
            summary["records"] += 1
            if record.eligible:
                summary["confirmed"] += 1
            else:
                summary["rejected"] += 1
    except _DB_UNAVAILABLE:
        pass
    return summary