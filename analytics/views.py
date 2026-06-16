from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count
from django.shortcuts import render

from accounts.decorators import admin_required, teacher_required
from academics.models import StudentProfile
from feedback.models import Feedback
from .services import (
    get_department_enrolment_stats,
    get_grade_distribution,
    get_platform_stats,
    get_smart_recommendations,
    predict_student_risk,
)


@login_required
@admin_required
def analytics_dashboard(request):
    stats = get_platform_stats()
    at_risk = []
    for sp in StudentProfile.objects.filter(is_active=True).select_related("student")[:50]:
        pred = predict_student_risk(sp.student)
        if pred["risk_level"] in ("high", "medium"):
            at_risk.append({"student": sp.student, "profile": sp, **pred})

    sentiment_breakdown = list(
        Feedback.objects.exclude(sentiment="")
        .values("sentiment")
        .annotate(count=Count("id"))
    )

    return render(request, "analytics/dashboard.html", {
        "stats": stats,
        "grade_dist": get_grade_distribution(),
        "dept_stats": get_department_enrolment_stats(),
        "at_risk": at_risk[:15],
        "sentiment_breakdown": sentiment_breakdown,
        "page_title": "Analytics & AI Insights",
    })


@login_required
@teacher_required
def teacher_analytics(request, offering_pk):
    from academics.models import CourseOffering
    from teachers.models import StudentResult

    offering = CourseOffering.objects.get(pk=offering_pk)
    results = StudentResult.objects.filter(
        result_sheet__offering=offering, total_score__isnull=False
    )
    avg_score = results.aggregate(a=Avg("total_score"))["a"]
    return render(request, "analytics/teacher_course.html", {
        "offering": offering,
        "avg_score": avg_score,
        "results": results.select_related("enrolment__student"),
        "page_title": f"Analytics — {offering.course.code}",
    })


@login_required
def student_recommendations(request):
    if not getattr(request.user, "is_student", False):
        raise PermissionDenied
    return render(request, "analytics/student_insights.html", {
        "recommendations": get_smart_recommendations(request.user),
        "risk": predict_student_risk(request.user),
        "page_title": "Academic Insights",
    })
