"""
students/views.py

All views for the students app.
Every view enforces role='student' via the student_required decorator.
Results, materials, assignments, quizzes, and attendance are read from
academics/teachers models — never duplicated here.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.decorators import approved_student_required, student_required
from academics.models import (
    AcademicSession,
    CourseOffering,
    Enrolment,
    Semester,
    StudentProfile,
)
from teachers.models import (
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSheet,
    LectureMaterial,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizChoice,
    QuizQuestion,
    ResultSheet,
    StudentResult,
)

from .forms import (
    AssignmentSubmitForm,
    CourseRegistrationForm,
    NotificationMarkReadForm,
    QuizStartForm,
)
from .models import (
    CourseRegistrationRequest,
    MaterialDownloadLog,
    StudentNotification,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_profile(user):
    try:
        return user.academic_profile
    except StudentProfile.DoesNotExist:
        return None


def _enrolled_offering_or_404(user, offering_pk):
    """Return offering only if the student is actively enrolled."""
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not Enrolment.objects.filter(
        student=user, offering=offering, is_active=True
    ).exists():
        return None, offering
    return offering, offering


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@approved_student_required
def student_dashboard(request):
    if not request.user.is_approved_student:
        return redirect("accounts:student_pending")
    user    = request.user
    profile = _get_profile(user)
    current_semester = Semester.get_current()

    enrolments = (
        Enrolment.objects
        .filter(student=user, is_active=True)
        .select_related(
            "offering__course__department",
            "offering__semester__session",
            "offering__level__program",
        )
        .order_by("-offering__semester__session__start_date")
    )

    # Stats
    current_enrolments = enrolments.filter(
        offering__semester=current_semester
    ) if current_semester else enrolments.none()

    # Pending assignments across all enrolled offerings
    enrolled_offering_pks = enrolments.values_list("offering_id", flat=True)
    pending_assignments = Assignment.objects.filter(
        offering_id__in=enrolled_offering_pks,
        status="published",
        due_date__gte=timezone.now(),
        is_active=True,
    ).exclude(
        submissions__student=user, submissions__is_active=True
    ).count()

    # Available quizzes
    open_quizzes = Quiz.objects.filter(
        offering_id__in=enrolled_offering_pks,
        is_published=True,
        is_active=True,
    ).count()

    # Unread notifications
    unread_count = StudentNotification.objects.filter(
        student=user, is_read=False
    ).count()

    # Recent results
    recent_results = (
        StudentResult.objects
        .filter(enrolment__student=user, result_sheet__status="approved")
        .select_related(
            "result_sheet__offering__course",
            "result_sheet__offering__semester__session",
        )
        .order_by("-result_sheet__approved_at")[:5]
    )

    # Recent notifications
    recent_notifications = StudentNotification.objects.filter(
        student=user
    ).order_by("-created_at")[:5]

    context = {
        "page_title":          "Student Dashboard",
        "profile":             profile,
        "current_semester":    current_semester,
        "enrolments":          enrolments[:6],
        "current_count":       current_enrolments.count(),
        "pending_assignments": pending_assignments,
        "open_quizzes":        open_quizzes,
        "unread_count":        unread_count,
        "recent_results":      recent_results,
        "recent_notifications": recent_notifications,
    }
    return render(request, "students/dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# MY COURSES
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@approved_student_required
def my_courses(request):
    enrolments = (
        Enrolment.objects
        .filter(student=request.user, is_active=True)
        .select_related(
            "offering__course__department",
            "offering__semester__session",
            "offering__level__program",
        )
        .order_by("-offering__semester__session__start_date", "offering__course__code")
    )
    return render(request, "students/my_courses.html", {
        "page_title": "My Courses",
        "enrolments": enrolments,
    })


@login_required
@approved_student_required
def course_home(request, offering_pk):
    """Hub for a single enrolled course — materials, assignments, quizzes, results."""
    offering, _ = _enrolled_offering_or_404(request.user, offering_pk)
    if offering is None:
        messages.error(request, "You are not enrolled in this course.")
        return redirect("students:my_courses")

    enrolment = get_object_or_404(Enrolment, student=request.user, offering=offering)

    materials   = LectureMaterial.objects.filter(
        allocation__offering=offering, is_published=True, is_active=True
    ).order_by("week_number", "-created_at")

    assignments = Assignment.objects.filter(
        offering=offering, status="published", is_active=True
    ).order_by("due_date")

    quizzes = Quiz.objects.filter(
        offering=offering, is_published=True, is_active=True
    ).order_by("start_datetime")

    result = StudentResult.objects.filter(enrolment=enrolment).first()

    context = {
        "page_title":  f"{offering.course.code} — Course",
        "offering":    offering,
        "enrolment":   enrolment,
        "materials":   materials,
        "assignments": assignments,
        "quizzes":     quizzes,
        "result":      result,
    }
    return render(request, "students/course_home.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# COURSE REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@approved_student_required
def course_registration(request):
    """Student self-service course registration request."""
    if request.method == "POST":
        form = CourseRegistrationForm(request.user, request.POST)
        if form.is_valid():
            reg = form.save(commit=False)
            reg.student = request.user
            try:
                reg.save()
                messages.success(
                    request,
                    f"Registration request submitted for {reg.offering.course.code}. "
                    f"Awaiting approval.",
                )
                return redirect("students:registration_list")
            except IntegrityError:
                messages.error(request, "You already have a request for that offering.")
    else:
        form = CourseRegistrationForm(request.user)

    return render(request, "students/course_registration.html", {
        "page_title": "Register for a Course",
        "form":       form,
    })


@login_required
@approved_student_required
def registration_list(request):
    requests = (
        CourseRegistrationRequest.objects
        .filter(student=request.user)
        .select_related("offering__course", "offering__semester__session")
        .order_by("-created_at")
    )
    return render(request, "students/registration_list.html", {
        "page_title": "My Registration Requests",
        "requests":   requests,
    })


# ─────────────────────────────────────────────────────────────────────────────
# LECTURE MATERIALS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@approved_student_required
def materials_list(request, offering_pk):
    offering, _ = _enrolled_offering_or_404(request.user, offering_pk)
    if offering is None:
        messages.error(request, "Not enrolled in this course.")
        return redirect("students:my_courses")

    materials = LectureMaterial.objects.filter(
        allocation__offering=offering, is_published=True, is_active=True
    ).order_by("week_number", "-created_at")

    return render(request, "students/materials_list.html", {
        "page_title": f"Materials — {offering.course.code}",
        "offering":   offering,
        "materials":  materials,
    })


@login_required
@approved_student_required
def material_access(request, pk):
    """Log the download and redirect to file or external URL."""
    material = get_object_or_404(
        LectureMaterial, pk=pk, is_published=True, is_active=True
    )
    # Verify enrolment
    if not Enrolment.objects.filter(
        student=request.user, offering=material.offering, is_active=True
    ).exists():
        messages.error(request, "Access denied.")
        return redirect("students:my_courses")

    # Log access
    MaterialDownloadLog.objects.create(student=request.user, material=material)
    # Increment counter
    LectureMaterial.all_objects.filter(pk=pk).update(
        download_count=models.F("download_count") + 1
    )

    if material.external_url:
        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(material.external_url)
    if material.file:
        from django.http import FileResponse
        return FileResponse(material.file.open("rb"), as_attachment=True)

    messages.error(request, "Material file not available.")
    return redirect("students:course_home", offering_pk=material.offering.pk)


# ─────────────────────────────────────────────────────────────────────────────
# ASSIGNMENTS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@approved_student_required
def assignment_list(request, offering_pk):
    offering, _ = _enrolled_offering_or_404(request.user, offering_pk)
    if offering is None:
        messages.error(request, "Not enrolled.")
        return redirect("students:my_courses")

    assignments = Assignment.objects.filter(
        offering=offering, status__in=["published", "closed", "graded"], is_active=True
    ).order_by("due_date")

    # Attach submission status
    my_submissions = {
        s.assignment_id: s
        for s in AssignmentSubmission.objects.filter(
            assignment__offering=offering, student=request.user, is_active=True
        )
    }
    for a in assignments:
        a.my_submission = my_submissions.get(a.pk)

    return render(request, "students/assignment_list.html", {
        "page_title":  f"Assignments — {offering.course.code}",
        "offering":    offering,
        "assignments": assignments,
    })


@login_required
@approved_student_required
@require_http_methods(["GET", "POST"])
def assignment_submit(request, pk):
    # CHANGED: Query against Assignment.objects instead of Assignment.all_objects
    # to ensure students can see the active, published assignments.
    assignment = get_object_or_404(Assignment.objects, pk=pk, status="published", is_active=True)

    # Verify enrolment
    if not Enrolment.objects.filter(
        student=request.user, offering=assignment.offering, is_active=True
    ).exists():
        messages.error(request, "Not enrolled in this course.")
        return redirect("students:my_courses")

    # Check existing submission
    existing = AssignmentSubmission.all_objects.filter(
        assignment=assignment, student=request.user
    ).first()

    if existing and existing.score is not None:
        messages.info(request, "This assignment has already been graded.")
        return redirect("students:assignment_list", offering_pk=assignment.offering.pk)

    if request.method == "POST":
        form = AssignmentSubmitForm(request.POST, request.FILES, instance=existing)
        if form.is_valid():
            sub = form.save(commit=False)
            sub.assignment   = assignment
            sub.student      = request.user
            sub.submitted_at = timezone.now()
            sub.is_late      = timezone.now() > assignment.due_date
            sub.is_active    = True
            sub.save()
            messages.success(request, "Assignment submitted successfully.")
            return redirect("students:assignment_list", offering_pk=assignment.offering.pk)
    else:
        form = AssignmentSubmitForm(instance=existing)

    return render(request, "students/assignment_submit.html", {
        "page_title": f"Submit: {assignment.title}",
        "assignment": assignment,
        "form":       form,
        "existing":   existing,
    })

# ─────────────────────────────────────────────────────────────────────────────
# QUIZZES / CBT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@approved_student_required
def quiz_list(request, offering_pk):
    offering, _ = _enrolled_offering_or_404(request.user, offering_pk)
    if offering is None:
        messages.error(request, "Not enrolled.")
        return redirect("students:my_courses")

    quizzes = Quiz.objects.filter(
        offering=offering, is_published=True, is_active=True
    ).order_by("start_datetime")

    my_attempts = {
        a.quiz_id: a
        for a in QuizAttempt.objects.filter(
            quiz__offering=offering, student=request.user
        ).order_by("-attempt_number")
    }
    for q in quizzes:
        q.my_attempt = my_attempts.get(q.pk)

    return render(request, "students/quiz_list.html", {
        "page_title": f"Quizzes — {offering.course.code}",
        "offering":   offering,
        "quizzes":    quizzes,
    })


@login_required
@approved_student_required
@require_http_methods(["GET", "POST"])
def quiz_start(request, pk):
    quiz = get_object_or_404(Quiz.all_objects, pk=pk, is_published=True, is_active=True)

    if not Enrolment.objects.filter(
        student=request.user, offering=quiz.offering, is_active=True
    ).exists():
        messages.error(request, "Not enrolled.")
        return redirect("students:my_courses")

    if not quiz.is_open:
        messages.error(request, "This quiz is not currently open.")
        return redirect("students:quiz_list", offering_pk=quiz.offering.pk)

    # Check attempts
    attempt_count = QuizAttempt.objects.filter(quiz=quiz, student=request.user).count()
    if attempt_count >= quiz.max_attempts:
        messages.error(request, "You have used all allowed attempts for this quiz.")
        return redirect("students:quiz_list", offering_pk=quiz.offering.pk)

    if request.method == "POST":
        form = QuizStartForm(request.POST)
        if form.is_valid():
            attempt = QuizAttempt.objects.create(
                quiz=quiz,
                student=request.user,
                attempt_number=attempt_count + 1,
                started_at=timezone.now(),
            )
            return redirect("students:quiz_take", attempt_pk=attempt.pk)
    else:
        form = QuizStartForm()

    return render(request, "students/quiz_start.html", {
        "page_title":    f"Start Quiz: {quiz.title}",
        "quiz":          quiz,
        "form":          form,
        "attempt_count": attempt_count,
    })


@login_required
@approved_student_required
@require_http_methods(["GET", "POST"])
def quiz_take(request, attempt_pk):
    attempt = get_object_or_404(
        QuizAttempt, pk=attempt_pk, student=request.user, is_complete=False
    )
    quiz = attempt.quiz

    # Time check
    elapsed = (timezone.now() - attempt.started_at).total_seconds()
    time_limit_seconds = quiz.duration_minutes * 60
    time_remaining = max(0, int(time_limit_seconds - elapsed))

    questions = quiz.questions.prefetch_related("choices").order_by("order")
    if quiz.randomise_questions:
        import random
        questions = list(questions)
        random.shuffle(questions)

    if request.method == "POST" or time_remaining == 0:
        # Save answers
        total_score = 0
        for question in quiz.questions.prefetch_related("choices"):
            answer, _ = QuizAnswer.objects.get_or_create(
                attempt=attempt, question=question
            )
            if question.question_type in ["mcq", "multi", "true_false"]:
                selected_ids = request.POST.getlist(f"q_{question.pk}")
                answer.selected_choices.set(
                    QuizChoice.objects.filter(pk__in=selected_ids)
                )
                correct_ids = set(
                    question.choices.filter(is_correct=True).values_list("pk", flat=True)
                )
                selected_set = set(int(x) for x in selected_ids)
                is_correct = correct_ids == selected_set
                answer.is_correct = is_correct
                answer.marks_awarded = question.marks if is_correct else 0
            else:
                answer.text_answer = request.POST.get(f"q_{question.pk}", "")
                answer.is_correct  = None
                answer.marks_awarded = 0
            answer.save()
            total_score += float(answer.marks_awarded)

        attempt.score       = total_score
        attempt.submitted_at = timezone.now()
        attempt.is_complete = True
        attempt.save()

        messages.success(request, f"Quiz submitted. Your score: {total_score}/{quiz.total_marks}")
        return redirect("students:quiz_result", attempt_pk=attempt.pk)

    return render(request, "students/quiz_take.html", {
        "page_title":     quiz.title,
        "quiz":           quiz,
        "attempt":        attempt,
        "questions":      questions,
        "time_remaining": time_remaining,
    })


@login_required
@approved_student_required
def quiz_result(request, attempt_pk):
    attempt = get_object_or_404(
        QuizAttempt, pk=attempt_pk, student=request.user, is_complete=True
    )
    answers = attempt.answers.select_related("question").prefetch_related(
        "selected_choices", "question__choices"
    ).order_by("question__order")

    show_answers = attempt.quiz.show_result_immediately

    return render(request, "students/quiz_result.html", {
        "page_title":   f"Quiz Result — {attempt.quiz.title}",
        "attempt":      attempt,
        "answers":      answers,
        "show_answers": show_answers,
    })


# ─────────────────────────────────────────────────────────────────────────────
# RESULTS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@approved_student_required
def results_list(request):
    """All approved results across all of the student's enrolments."""
    results = (
        StudentResult.objects
        .filter(
            enrolment__student=request.user,
            result_sheet__status="approved",
        )
        .select_related(
            "result_sheet__offering__course",
            "result_sheet__offering__semester__session",
            "enrolment",
        )
        .order_by("-result_sheet__offering__semester__session__start_date")
    )

    # GPA per semester grouping
    sessions = {}
    for r in results:
        session_name = r.result_sheet.offering.semester.session.name
        sessions.setdefault(session_name, []).append(r)

    return render(request, "students/results_list.html", {
        "page_title": "My Results",
        "results":    results,
        "sessions":   sessions,
    })


@login_required
@approved_student_required
def result_detail(request, offering_pk):
    """Single course result detail view."""

    offering = get_object_or_404(CourseOffering, pk=offering_pk)

    enrolment = get_object_or_404(
        Enrolment,
        student=request.user,
        offering=offering
    )

    result = StudentResult.objects.filter(
        enrolment=enrolment,
        result_sheet__status="approved"
    ).select_related(
        "result_sheet",
        "enrolment",
        "enrolment__offering",
    ).first()

    if not result:
        messages.warning(request, "Result has not been published yet.")
        return redirect("students:results_list")

    context = {
        "page_title": f"Result — {offering.course.code}",
        "result": result,
        "offering": offering,
    }

    return render(request, "students/result_detail.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# ATTENDANCE
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@approved_student_required
def attendance_summary(request, offering_pk):
    offering, _ = _enrolled_offering_or_404(request.user, offering_pk)
    if offering is None:
        messages.error(request, "Not enrolled.")
        return redirect("students:my_courses")

    total_sessions = AttendanceSheet.objects.filter(
        offering=offering, is_active=True
    ).count()

    records = AttendanceRecord.objects.filter(
        sheet__offering=offering, student=request.user
    ).select_related("sheet").order_by("-sheet__date")

    present = records.filter(status="present").count()
    late    = records.filter(status="late").count()
    absent  = records.filter(status="absent").count()
    excused = records.filter(status="excused").count()

    attendance_pct = round((present + late) / total_sessions * 100, 1) if total_sessions else 0

    return render(request, "students/attendance_summary.html", {
        "page_title":     f"Attendance — {offering.course.code}",
        "offering":       offering,
        "records":        records,
        "total_sessions": total_sessions,
        "present":        present,
        "late":           late,
        "absent":         absent,
        "excused":        excused,
        "attendance_pct": attendance_pct,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ACADEMIC PROGRESS / TRANSCRIPT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@approved_student_required
def academic_progress(request):
    """
    Comprehensive academic progress view:
    - Program / level info from StudentProfile
    - All approved results with grade points
    - GPA per semester, CGPA overall
    - Attendance summary across all courses
    """
    user    = request.user
    profile = _get_profile(user)

    results = (
        StudentResult.objects
        .filter(
            enrolment__student=user,
            result_sheet__status="approved",
        )
        .select_related(
            "result_sheet__offering__course",
            "result_sheet__offering__semester__session",
            "result_sheet__offering__level",
        )
        .order_by("result_sheet__offering__semester__session__start_date")
    )

    # Compute GPA per session
    session_stats = {}
    total_points  = 0.0
    total_credits = 0

    for r in results:
        session_name = r.result_sheet.offering.semester.session.name
        credits = r.result_sheet.offering.course.credit_units
        gp      = float(r.grade_point or 0)

        s = session_stats.setdefault(session_name, {
            "results":       [],
            "total_points":  0.0,
            "total_credits": 0,
            "gpa":           0.0,
        })
        s["results"].append(r)
        s["total_points"]  += gp * credits
        s["total_credits"] += credits
        total_points  += gp * credits
        total_credits += credits

    for s in session_stats.values():
        if s["total_credits"]:
            s["gpa"] = round(s["total_points"] / s["total_credits"], 2)

    cgpa = round(total_points / total_credits, 2) if total_credits else None

    return render(request, "students/academic_progress.html", {
        "page_title":    "Academic Progress",
        "profile":       profile,
        "session_stats": session_stats,
        "cgpa":          cgpa,
        "total_credits": total_credits,
    })


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@student_required
def notifications(request):
    notifs = StudentNotification.objects.filter(
        student=request.user
    ).order_by("-created_at")
    # Auto-mark displayed ones as read
    unread_ids = list(
        notifs.filter(is_read=False).values_list("pk", flat=True)[:50]
    )
    if unread_ids:
        StudentNotification.objects.filter(pk__in=unread_ids).update(
            is_read=True, read_at=timezone.now()
        )

    return render(request, "students/notifications.html", {
        "page_title": "Notifications",
        "notifs":     notifs,
    })


@login_required
@student_required
@require_POST
def mark_all_read(request):
    StudentNotification.objects.filter(
        student=request.user, is_read=False
    ).update(is_read=True, read_at=timezone.now())
    messages.success(request, "All notifications marked as read.")
    return redirect("students:notifications")


# ─────────────────────────────────────────────────────────────────────────────
# STUDENT PROFILE (academic)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@student_required
def student_profile(request):
    profile = _get_profile(request.user)
    enrolments = Enrolment.objects.filter(
        student=request.user, is_active=True
    ).select_related("offering__course", "offering__semester__session")

    return render(request, "accounts/profile.html", {
        "page_title": "Academic Profile",
        "profile":    profile,
        "enrolments": enrolments,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: Registration request management
# ─────────────────────────────────────────────────────────────────────────────


# need models import for F()
from django.db import models


@login_required
@approved_student_required
def transcript_redirect(request):
    from core.views import transcript_pdf
    return transcript_pdf(request)


@login_required
@approved_student_required
def my_fees_redirect(request):
    from finance.views import my_fees
    return my_fees(request)


@login_required
@approved_student_required
def insights_redirect(request):
    from analytics.views import student_recommendations
    return student_recommendations(request)

