"""Analytics and predictive performance services."""

from decimal import Decimal

from django.db.models import Avg, Count

from academics.models import Enrolment, StudentProfile
from teachers.models import AttendanceRecord, StudentResult


def get_platform_stats():
    from accounts.models import EduProUser
    from academics.models import Course, CourseOffering, Department
    from feedback.models import Feedback
    from finance.models import StudentFee

    return {
        "users": EduProUser.objects.count(),
        "students": EduProUser.objects.filter(role="student").count(),
        "teachers": EduProUser.objects.filter(role="teacher").count(),
        "departments": Department.objects.filter(is_active=True).count(),
        "courses": Course.objects.filter(is_active=True).count(),
        "offerings": CourseOffering.objects.filter(is_active=True).count(),
        "enrolments": Enrolment.objects.filter(is_active=True).count(),
        "feedback_total": Feedback.objects.count(),
        "fees_collected": StudentFee.objects.aggregate(t=Avg("amount_paid"))["t"] or 0,
    }


def predict_student_risk(student):
    """
    Simple predictive model: flags at-risk students based on
    attendance rate, average score, and missing assignments.
    Returns dict with risk_level and factors.
    """
    factors = []
    risk_score = 0

    # Attendance
    records = AttendanceRecord.objects.filter(student=student)
    total = records.count()
    if total > 0:
        present = records.filter(status=AttendanceRecord.Status.PRESENT).count()
        rate = present / total
        if rate < 0.6:
            risk_score += 40
            factors.append(f"Low attendance ({rate:.0%})")
        elif rate < 0.75:
            risk_score += 20
            factors.append(f"Below-average attendance ({rate:.0%})")

    # Results
    results = StudentResult.objects.filter(
        enrolment__student=student, result_sheet__status="approved", total_score__isnull=False
    )
    avg = results.aggregate(a=Avg("total_score"))["a"]
    if avg is not None:
        if float(avg) < 50:
            risk_score += 40
            factors.append(f"Low average score ({float(avg):.1f}%)")
        elif float(avg) < 60:
            risk_score += 20
            factors.append(f"Below-average score ({float(avg):.1f}%)")

    # Assignments
    from teachers.models import Assignment, AssignmentSubmission
    offerings = Enrolment.objects.filter(student=student, is_active=True).values_list("offering_id", flat=True)
    total_assignments = Assignment.objects.filter(offering_id__in=offerings, status="published").count()
    submitted = AssignmentSubmission.objects.filter(
        student=student, assignment__offering_id__in=offerings
    ).count()
    if total_assignments > 0:
        sub_rate = submitted / total_assignments
        if sub_rate < 0.5:
            risk_score += 20
            factors.append(f"Low assignment submission ({sub_rate:.0%})")

    if risk_score >= 60:
        level = "high"
    elif risk_score >= 30:
        level = "medium"
    else:
        level = "low"

    return {
        "risk_level": level,
        "risk_score": min(risk_score, 100),
        "factors": factors,
        "avg_score": float(avg) if avg else None,
    }


def get_grade_distribution():
    return list(
        StudentResult.objects.filter(result_sheet__status="approved")
        .exclude(grade="")
        .values("grade")
        .annotate(count=Count("id"))
        .order_by("-count")
    )


def get_department_enrolment_stats():
    from academics.models import Department
    return list(
        Department.objects.filter(is_active=True)
        .annotate(
            student_count=Count("programs__students", distinct=True),
        )
        .values("name", "code", "student_count")
        .order_by("-student_count")[:10]
    )


def get_smart_recommendations(student):
    """Course recommendations based on program and completed courses."""
    try:
        profile = student.academic_profile
    except StudentProfile.DoesNotExist:
        return []

    if not profile or not profile.program:
        return []

    from academics.models import CourseOffering, Semester
    current = Semester.get_current()
    if not current:
        return []

    enrolled_ids = Enrolment.objects.filter(
        student=student, is_active=True
    ).values_list("offering__course_id", flat=True)

    offerings = CourseOffering.objects.filter(
        departments=profile.program.department,
        level_name=profile.current_level.name,
        semester=current,
        is_active=True,
    ).exclude(course_id__in=enrolled_ids).select_related("course")[:5]

    return [
        {"code": o.course.code, "title": o.course.title, "offering_id": o.pk}
        for o in offerings
    ]
