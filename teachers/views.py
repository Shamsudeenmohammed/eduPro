"""
teachers/views.py

All views for the teachers app.
Access: teacher_required decorator from accounts.decorators.
Result submission also accessible to admins.
"""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.decorators import role_required, teacher_required, hod_required
from accounts.models import EduProUser, StaffResponsibility
from academics.models import CourseAllocation, CourseOffering, Enrolment, TeacherDepartment
from core.models import AuditAction, AuditLog
from students.models import StudentNotification

from notifications.models import NotificationType
from notifications.recipients import EnrolmentQuery
from notifications.services import NotificationService

from .forms import (
    AssignmentForm,
    AssignmentGradeForm,
    AttendanceSheetForm,
    LectureMaterialForm,
    QuizForm,
    QuizQuestionForm,
    QuizChoiceFormSet,
    ResultSheetForm,
    StudentResultForm,
    StudentResultFormSet,
    TeacherProfileForm,
)
from .models import (
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSheet,
    LectureMaterial,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
    ResultSheet,
    ResultVersion,
    StudentResult,
    TeacherProfile,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_teacher_profile(user):
    """Get or create TeacherProfile for user; never crashes."""
    profile, _ = TeacherProfile.all_objects.get_or_create(
        teacher=user,
        defaults={"is_active": True},
    )
    return profile


def _teacher_owns_offering(user, offering):
    """Return True if the teacher is allocated to the offering."""
    return CourseAllocation.objects.filter(
        teacher=user, offering=offering, is_active=True
    ).exists()


def _teacher_owns_offering_or_404(user, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(user, offering) and not user.is_superuser:
        messages.error(
            None, "You do not have access to that course offering."
        )
        return None, offering
    return offering, offering

def notify_students(offering, title, message):
    students = Enrolment.objects.filter(
        offering=offering,
        is_active=True
    ).values_list("student", flat=True)

    StudentNotification.objects.bulk_create([
        StudentNotification(
            student_id=s,
            title=title,
            message=message,
        )
        for s in students
    ])

# ─────────────────────────────────────────────────────────────────────────────
# TEACHER DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def teacher_dashboard(request):
    user        = request.user
    profile     = _get_teacher_profile(user)
    allocations = profile.get_current_semester_allocations()

    allocation_pks = allocations.values_list("pk", flat=True)
    offering_pks   = allocations.values_list("offering_id", flat=True)

    total_students = Enrolment.objects.filter(
        offering_id__in=offering_pks, is_active=True
    ).count()
    pending_submissions = AssignmentSubmission.objects.filter(
        assignment__offering_id__in=offering_pks,
        score__isnull=True, is_active=True,
    ).count()
    open_result_sheets = ResultSheet.objects.filter(
        offering_id__in=offering_pks,
        status__in=["open", "rejected"],
    ).count()
    recent_materials = LectureMaterial.objects.filter(
        allocation__in=allocation_pks, is_active=True
    ).select_related("allocation__offering__course").order_by("-created_at")[:5]
    recent_submissions = AssignmentSubmission.objects.filter(
        assignment__offering_id__in=offering_pks,
    ).select_related("student", "assignment__offering__course").order_by("-submitted_at")[:10]
    teacher_departments = TeacherDepartment.objects.filter(
        teacher=user, is_active=True
    ).select_related("department")

    # HOD/admin stats
    hod_pending_sheets = ResultSheet.objects.none()
    if user.is_admin or user.is_superuser or user.is_hod:
        hod_depts = user.get_hod_departments()
        if hod_depts:
            hod_pending_sheets = ResultSheet.objects.filter(
                offering__course__department__in=hod_depts, status="submitted"
            ).select_related("offering__course", "submitted_by")

    context = {
        "page_title":                "Teacher Dashboard",
        "profile":                   profile,
        "teacher_allocations":       allocations,
        "teacher_course_count":      allocations.count(),
        "teacher_student_count":     total_students,
        "teacher_pending_submissions":   pending_submissions,
        "teacher_open_results":      open_result_sheets,
        "recent_materials":          recent_materials,
        "recent_submissions":        recent_submissions,
        "teacher_departments":       teacher_departments,
        "pending_hod_approvals_count": hod_pending_sheets.count(),
        "hod_pending_sheets":        hod_pending_sheets,
    }
    return render(request, "teachers/dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# TEACHER PROFILE
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def teacher_profile_edit(request):
    profile = _get_teacher_profile(request.user)
    form = TeacherProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("teachers:dashboard")
    return render(request, "teachers/profile_edit.html", {
        "page_title": "My Teaching Profile",
        "form": form,
        "profile": profile,
    })


# ─────────────────────────────────────────────────────────────────────────────
# MY COURSES (teacher's offering list)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def my_courses(request):
    allocations = CourseAllocation.objects.filter(
        teacher=request.user, is_active=True
    ).select_related(
        "offering__course__department",
        "offering__semester__session",
        "offering__level__program",
    ).order_by("-offering__semester__session__start_date", "offering__course__code")

    return render(request, "teachers/my_courses.html", {
        "page_title":  "My Courses",
        "allocations": allocations,
    })


@login_required
@teacher_required
def course_detail(request, offering_pk):
    offering = get_object_or_404(
        CourseOffering.objects.select_related(
            "course__department", "semester__session", "level__program"
        ).prefetch_related("enrolments__student", "allocations__teacher"),
        pk=offering_pk,
    )
    if not _teacher_owns_offering(request.user, offering) and not request.user.is_superuser:
        messages.error(request, "You are not allocated to this course.")
        return redirect("teachers:my_courses")

    # Fetch related objects for this offering
    materials   = LectureMaterial.objects.filter(
        allocation__teacher=request.user, allocation__offering=offering, is_active=True
    ).order_by("week_number", "-created_at")
    assignments = Assignment.objects.filter(
        offering=offering, is_active=True
    ).order_by("-due_date")
    quizzes     = Quiz.objects.filter(
        offering=offering, is_active=True
    ).order_by("-start_datetime")
    sheets      = AttendanceSheet.objects.filter(
        offering=offering, is_active=True
    ).order_by("-date")[:5]
    result_sheet = ResultSheet.objects.filter(offering=offering).first()

    return render(request, "teachers/course_detail.html", {
        "page_title":    f"{offering.course.code} — Course Detail",
        "offering":      offering,
        "materials":     materials,
        "assignments":   assignments,
        "quizzes":       quizzes,
        "recent_sheets": sheets,
        "result_sheet":  result_sheet,
    })


# ─────────────────────────────────────────────────────────────────────────────
# LECTURE MATERIALS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def material_list(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    materials = LectureMaterial.all_objects.filter(
        allocation__teacher=request.user, allocation__offering=offering
    ).order_by("week_number", "-created_at")

    return render(request, "teachers/material_list.html", {
        "page_title":  f"Materials — {offering.course.code}",
        "offering":    offering,
        "materials":   materials,
    })


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def material_create(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    allocation = get_object_or_404(
        CourseAllocation, teacher=request.user, offering=offering, is_active=True
    )
    if request.method == "POST":
        form = LectureMaterialForm(request.POST, request.FILES)
        if form.is_valid():
            material = form.save(commit=False)
            material.allocation = allocation
            material.save()
            messages.success(request, f"Material '{material.title}' uploaded.")
            return redirect("teachers:course_detail", offering_pk=offering_pk)
    else:
        form = LectureMaterialForm()

    return render(request, "teachers/material_form.html", {
        "page_title": "Upload Material",
        "form":       form,
        "offering":   offering,
    })


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def material_edit(request, pk):
    material = get_object_or_404(LectureMaterial.all_objects, pk=pk)
    if material.teacher != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    form = LectureMaterialForm(request.POST or None, request.FILES or None, instance=material)
    was_published = material.is_published
    if request.method == "POST" and form.is_valid():
        form.save()
        if material.is_published and not was_published:
            NotificationService.send_to_students(
                NotificationType.NEW_MATERIAL,
                material.offering,
                context={"material_title": material.title},
                obj=material,
                link=f"/students/courses/{material.offering.pk}/materials/",
                idempotency_key=f"new_material:{material.pk}",
            )
        messages.success(request, "Material updated.")
        return redirect("teachers:course_detail", offering_pk=material.offering.pk)

    return render(request, "teachers/material_form.html", {
        "page_title": f"Edit: {material.title}",
        "form":       form,
        "offering":   material.offering,
        "object":     material,
    })


@login_required
@teacher_required
@require_POST
def material_delete(request, pk):
    material = get_object_or_404(LectureMaterial.all_objects, pk=pk)
    if material.teacher != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")
    offering_pk = material.offering.pk
    material.is_active = False
    material.save()
    messages.success(request, "Material removed.")
    return redirect("teachers:material_list", offering_pk=offering_pk)


# ─────────────────────────────────────────────────────────────────────────────
# ASSIGNMENTS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def assignment_list(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    assignments = Assignment.all_objects.filter(
        offering=offering, created_by=request.user
    ).order_by("-due_date")

    return render(request, "teachers/assignment_list.html", {
        "page_title":  f"Assignments — {offering.course.code}",
        "offering":    offering,
        "assignments": assignments,
    })


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def assignment_create(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    if request.method == "POST":
        form = AssignmentForm(request.POST, request.FILES)
        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.offering    = offering
            assignment.created_by  = request.user
            assignment.save()
            if assignment.status == "published":
                NotificationService.send_to_students(
                    NotificationType.NEW_ASSIGNMENT,
                    assignment.offering,
                    context={
                        "assignment_title": assignment.title,
                        "due_date": assignment.due_date,
                    },
                    obj=assignment,
                    idempotency_key=f"new_assignment:{assignment.pk}",
                )
            messages.success(request, f"Assignment '{assignment.title}' created.")
            return redirect("teachers:assignment_list", offering_pk=offering_pk)
    else:
        form = AssignmentForm()
        # Remove the 'offering' field — it's baked in from the URL
        form.fields.pop("offering", None)

    return render(request, "teachers/assignment_form.html", {
        "page_title": "Create Assignment",
        "form":       form,
        "offering":   offering,
    })


@login_required
@teacher_required
def assignment_submissions(request, pk):
    """List all submissions for an assignment; allow bulk grading."""
    assignment = get_object_or_404(Assignment.all_objects, pk=pk)
    if assignment.created_by != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    submissions = assignment.submissions.select_related("student").order_by(
        "student__last_name"
    )
    return render(request, "teachers/assignment_submissions.html", {
        "page_title":  f"Submissions — {assignment.title}",
        "assignment":  assignment,
        "submissions": submissions,
    })


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def grade_submission(request, pk):
    submission = get_object_or_404(AssignmentSubmission.all_objects, pk=pk)
    assignment = submission.assignment
    if assignment.created_by != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    form = AssignmentGradeForm(request.POST or None, instance=submission)
    if request.method == "POST" and form.is_valid():
        sub = form.save(commit=False)
        sub.graded_by = request.user
        sub.graded_at = timezone.now()
        sub.save()
        NotificationService.send(
            NotificationType.ASSIGNMENT_GRADED,
            [submission.student],
            context={
                "offering": assignment.offering,
                "assignment_title": assignment.title,
                "score": sub.score,
                "total": assignment.total_marks,
            },
            obj=assignment,
            idempotency_key=f"assignment_graded:{assignment.pk}:{submission.student_id}",
        )
        messages.success(request, "Grade saved.")
        return redirect("teachers:assignment_submissions", pk=assignment.pk)

    return render(request, "teachers/grade_submission.html", {
        "page_title": f"Grade: {submission.student.get_full_name()}",
        "form":       form,
        "submission": submission,
        "assignment": assignment,
    })


# ─────────────────────────────────────────────────────────────────────────────
# QUIZZES
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def quiz_list(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    quizzes = Quiz.all_objects.filter(
        offering=offering, created_by=request.user
    ).order_by("-start_datetime")

    return render(request, "teachers/quiz_list.html", {
        "page_title": f"Quizzes — {offering.course.code}",
        "offering":   offering,
        "quizzes":    quizzes,
    })
    
@login_required
@teacher_required
@require_POST
def quiz_publish(request, pk):
    quiz = get_object_or_404(
        Quiz.all_objects,
        pk=pk,
        created_by=request.user
    )

    quiz.status = "published"
    quiz.is_published = True
    quiz.save(update_fields=["status", "is_published", "updated_at"])

    NotificationService.send_to_students(
        NotificationType.NEW_QUIZ,
        quiz.offering,
        context={
            "quiz_title": quiz.title,
            "start_datetime": quiz.start_datetime,
            "end_datetime": quiz.end_datetime,
        },
        obj=quiz,
        idempotency_key=f"new_quiz:{quiz.pk}",
    )

    messages.success(request, "Quiz published successfully.")
    return redirect(
        "teachers:quiz_list",
        offering_pk=quiz.offering.pk
    )


@login_required
@teacher_required
@require_POST
def quiz_unpublish(request, pk):
    quiz = get_object_or_404(
        Quiz.all_objects,
        pk=pk,
        created_by=request.user
    )

    quiz.status = "draft"
    quiz.is_published = False
    quiz.save(update_fields=["status", "is_published", "updated_at"])

    messages.success(request, "Quiz unpublished successfully.")

    return redirect(
        "teachers:quiz_list",
        offering_pk=quiz.offering.pk
    )


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def quiz_create(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    if request.method == "POST":
        form = QuizForm(request.POST)
        if form.is_valid():
            quiz = form.save(commit=False)
            quiz.offering   = offering
            quiz.created_by = request.user
            quiz.save()
            if quiz.is_published:
                NotificationService.send_to_students(
                    NotificationType.NEW_QUIZ,
                    quiz.offering,
                    context={
                        "quiz_title": quiz.title,
                        "end_datetime": quiz.end_datetime,
                    },
                    obj=quiz,
                    idempotency_key=f"new_quiz:{quiz.pk}",
                )
            messages.success(request, f"Quiz '{quiz.title}' created.")
            return redirect("teachers:quiz_questions", pk=quiz.pk)
    else:
        form = QuizForm()
        form.fields.pop("offering", None)

    return render(request, "teachers/quiz_form.html", {
        "page_title": "Create Quiz",
        "form":       form,
        "offering":   offering,
    })


@login_required
@teacher_required
def quiz_questions(request, pk):
    quiz = get_object_or_404(Quiz.all_objects, pk=pk)
    if quiz.created_by != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    questions = quiz.questions.prefetch_related("choices").order_by("order")
    return render(request, "teachers/quiz_questions.html", {
        "page_title": f"Questions — {quiz.title}",
        "quiz":       quiz,
        "questions":  questions,
    })


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def quiz_question_add(request, quiz_pk):
    quiz = get_object_or_404(Quiz.all_objects, pk=quiz_pk)
    if quiz.created_by != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    form = QuizQuestionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        question = form.save(commit=False)
        question.quiz = quiz
        question.save()
        formset = QuizChoiceFormSet(request.POST, instance=question)
        if formset.is_valid():
            formset.save()
        messages.success(request, "Question added.")
        return redirect("teachers:quiz_questions", pk=quiz_pk)
    else:
        formset = QuizChoiceFormSet()

    return render(request, "teachers/quiz_question_form.html", {
        "page_title": "Add Question",
        "form":       form,
        "formset":    formset,
        "quiz":       quiz,
    })


@login_required
@teacher_required
def quiz_results(request, pk):
    quiz = get_object_or_404(Quiz.all_objects, pk=pk)
    if quiz.created_by != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    attempts = QuizAttempt.objects.filter(quiz=quiz, is_complete=True).select_related("student").order_by("student__last_name")
    return render(request, "teachers/quiz_results.html", {
        "page_title": f"Results — {quiz.title}",
        "quiz":       quiz,
        "attempts":   attempts,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ATTENDANCE
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def attendance_list(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    sheets = AttendanceSheet.all_objects.filter(
        offering=offering
    ).order_by("-date")

    return render(request, "teachers/attendance_list.html", {
        "page_title": f"Attendance — {offering.course.code}",
        "offering":   offering,
        "sheets":     sheets,
    })


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def attendance_take(request, offering_pk):
    """Create a new attendance sheet and mark each enrolled student."""
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    enrolled = Enrolment.objects.filter(
        offering=offering, is_active=True, status="active"
    ).select_related("student").order_by("student__last_name")

    if request.method == "POST":
        date   = request.POST.get("date")
        topic  = request.POST.get("topic_covered", "")
        week   = request.POST.get("week_number") or None

        if not date:
            messages.error(request, "Please provide the attendance date.")
        else:
            sheet, created = AttendanceSheet.all_objects.get_or_create(
                offering=offering,
                date=date,
                defaults={
                    "taken_by":     request.user,
                    "topic_covered": topic,
                    "week_number":  week,
                    "is_active":    True,
                },
            )
            if not created:
                sheet.topic_covered = topic
                sheet.week_number   = week
                sheet.save()

            for enrolment in enrolled:
                student = enrolment.student
                status  = request.POST.get(f"status_{student.pk}", "absent")
                remark  = request.POST.get(f"remark_{student.pk}", "")
                AttendanceRecord.objects.update_or_create(
                    sheet=sheet, student=student,
                    defaults={"status": status, "remark": remark},
                )
            messages.success(request, f"Attendance saved for {date}.")
            return redirect("teachers:attendance_list", offering_pk=offering_pk)

    context = {
        "page_title": f"Take Attendance — {offering.course.code}",
        "offering":   offering,
        "enrolled":   enrolled,
        "today":      timezone.now().date(),
        "status_choices": AttendanceRecord.Status.choices,
    }
    return render(request, "teachers/attendance_take.html", context)


@login_required
@teacher_required
def attendance_sheet_detail(request, pk):
    sheet = get_object_or_404(
        AttendanceSheet.all_objects.prefetch_related(
            "records__student"
        ),
        pk=pk,
    )
    if not _teacher_owns_offering(request.user, sheet.offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    return render(request, "teachers/attendance_sheet_detail.html", {
        "page_title": f"Attendance — {sheet.date}",
        "sheet":      sheet,
    })


# ─────────────────────────────────────────────────────────────────────────────
# RESULTS
# ─────────────────────────────────────────────────────────────────────────────

def _result_sheet_context(sheet):
    """Standard context for result-lifecycle notifications."""
    return {
        "offering": sheet.offering,
        "course_code": sheet.offering.course.code,
        "sheet_pk": sheet.pk,
    }


def _department_reviewers(sheet):
    """Users to notify when a sheet is submitted: the dept HOD + HOD role holders."""
    department = sheet.offering.course.department
    if department is None:
        return EduProUser.objects.none()
    from accounts.models import UserStaffRole
    hod_ids = UserStaffRole.objects.filter(
        responsibility=StaffResponsibility.HOD,
        department=department,
        is_active=True,
    ).values_list("user_id", flat=True)
    reviewer_ids = set(hod_ids)
    if department.hod_id:
        reviewer_ids.add(department.hod_id)
    return EduProUser.objects.filter(pk__in=reviewer_ids)


def _result_audit(actor, sheet, action, changes=None):
    """Persist an immutable audit record for a result lifecycle transition."""
    AuditLog.objects.create(
        user=actor,
        action=action,
        model_name="teachers.ResultSheet",
        object_id=str(sheet.pk),
        object_repr=f"{sheet.offering.course.code} | {sheet.offering.semester}",
        changes=changes or {},
        path="/teachers/results/",
    )


REVIEW_SLA_HOURS = 72  # submitted sheets pending longer than this are "overdue"


def _hod_review_scope(user):
    """Return (dept_or_None, reviewable ResultSheet QuerySet) for a review user."""
    if user.is_superuser or getattr(user, "is_admin", False):
        return None, ResultSheet.objects
    depts = user.get_hod_departments()
    return depts.first(), ResultSheet.objects.filter(
        offering__course__department__in=depts
    )


def _sheet_in_review_scope(sheet, user):
    """True if the user may review this sheet (admin/superuser, or its dept HOD)."""
    if user.is_superuser or getattr(user, "is_admin", False):
        return True
    return user.is_hod_of(sheet.offering.course.department)


def _approve_sheet(sheet, user):
    """Transition submitted → approved; returns (ok, message)."""
    if not _sheet_in_review_scope(sheet, user):
        return False, "You can only approve results for courses in your department."
    if sheet.status != ResultSheet.SheetStatus.SUBMITTED:
        return False, "Only submitted result sheets can be approved."

    sheet.status      = ResultSheet.SheetStatus.APPROVED
    sheet.approved_by = user
    sheet.approved_at = timezone.now()
    sheet.save()

    try:
        from core.utils import update_student_gpa
        student_ids = sheet.student_results.values_list(
            "enrolment__student_id", flat=True
        ).distinct()
        for student_id in student_ids:
            try:
                update_student_gpa(EduProUser.objects.get(pk=student_id))
            except EduProUser.DoesNotExist:
                pass
    except ImportError:
        pass

    _result_audit(user, sheet, AuditAction.APPROVE)
    if sheet.submitted_by:
        NotificationService.send(
            notification_type=NotificationType.RESULT_APPROVED,
            recipients=[sheet.submitted_by],
            context=_result_sheet_context(sheet),
            module="RESULTS",
            idempotency_key=(
                f"result_approved:{sheet.pk}:{int(sheet.approved_at.timestamp())}"
                if sheet.approved_at else f"result_approved:{sheet.pk}"
            ),
        )
    return True, "Result sheet approved and locked."


def _reject_sheet(sheet, user, note):
    """Transition a sheet to rejected with a note; returns (ok, message)."""
    if not _sheet_in_review_scope(sheet, user):
        return False, "You can only reject results for courses in your department."

    note = note or "No reason provided."
    sheet.status         = ResultSheet.SheetStatus.REJECTED
    sheet.rejection_note = note
    sheet.save()
    _result_audit(user, sheet, AuditAction.REJECT, {"note": note})
    if sheet.submitted_by:
        NotificationService.send(
            notification_type=NotificationType.RESULT_REJECTED,
            recipients=[sheet.submitted_by],
            context={**_result_sheet_context(sheet), "reason": note},
            module="RESULTS",
            idempotency_key=(
                f"result_rejected:{sheet.pk}:{int(sheet.submitted_at.timestamp())}"
                if sheet.submitted_at else f"result_rejected:{sheet.pk}"
            ),
        )
    return True, "Result sheet rejected and returned for correction."


@login_required
@teacher_required
def result_sheet_list(request):
    """List all ResultSheets for this teacher's allocations."""
    offering_pks = CourseAllocation.objects.filter(
        teacher=request.user, is_active=True
    ).values_list("offering_id", flat=True)

    sheets = ResultSheet.objects.filter(
        offering_id__in=offering_pks
    ).select_related(
        "offering__course", "offering__semester__session"
    ).order_by("-offering__semester__session__start_date")

    return render(request, "teachers/result_sheet_list.html", {
        "page_title": "Result Sheets",
        "sheets":     sheets,
    })


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def result_sheet_setup(request, offering_pk):
    """Create or configure the ResultSheet for an offering."""
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering) and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:result_sheet_list")

    sheet, created = ResultSheet.objects.get_or_create(
        offering=offering,
        defaults={"submitted_by": request.user},
    )

    # If sheet is already approved, redirect to read-only view
    if sheet.is_locked:
        messages.info(request, "This result sheet is already approved and locked. Viewing in read-only mode.")
        return redirect("teachers:result_sheet_view", sheet_pk=sheet.pk)

    form = ResultSheetForm(request.POST or None, instance=sheet)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Result sheet configured.")
        return redirect("teachers:result_entry", sheet_pk=sheet.pk)

    return render(request, "teachers/result_sheet_setup.html", {
        "page_title": f"Result Sheet — {offering.course.code}",
        "form":       form,
        "sheet":      sheet,
        "offering":   offering,
        "is_new":     created,
    })


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def result_entry(request, sheet_pk):
    """
    Bulk result entry view.
    Renders one row per enrolled student; teacher fills CA + Exam.
    Saves all on POST; computes total+grade automatically via StudentResult.save().
    """
    sheet = get_object_or_404(
        ResultSheet.objects.select_related(
            "offering__course", "offering__semester"
        ),
        pk=sheet_pk,
    )
    if not sheet.can_edit(request.user) and not request.user.is_superuser:
        if sheet.is_locked:
            messages.warning(request, "This result sheet has been approved and locked. View it in read-only mode.")
            return redirect("teachers:result_sheet_view", sheet_pk=sheet.pk)
        tp = getattr(request.user, "teacher_profile", None)
        if tp and not tp.can_submit_results:
            messages.error(request, "Your account does not have permission to submit results. Contact an administrator.")
        else:
            messages.error(request, "You are not allocated to this course offering.")
        return redirect("teachers:result_sheet_list")

    # Ensure a StudentResult exists for every active enrolment
    enrolments = Enrolment.objects.filter(
        offering=sheet.offering, is_active=True
    ).select_related("student").order_by("student__last_name")

    for enrolment in enrolments:
        StudentResult.objects.get_or_create(
            result_sheet=sheet,
            enrolment=enrolment,
            defaults={"entered_by": request.user},
        )

    if request.method == "POST":
        for enrolment in enrolments:
            try:
                sr = StudentResult.objects.get(result_sheet=sheet, enrolment=enrolment)
            except StudentResult.DoesNotExist:
                continue

            form = StudentResultForm(request.POST, instance=sr,
                                     prefix=f"sr_{sr.pk}")
            if form.is_valid():
                result = form.save(commit=False)
                result.entered_by = request.user
                result.save()

        messages.success(request, "Results saved. Totals and grades computed automatically.")
        return redirect("teachers:result_entry", sheet_pk=sheet_pk)

    # Build form list for template
    student_forms = []
    for enrolment in enrolments:
        sr   = StudentResult.objects.get(result_sheet=sheet, enrolment=enrolment)
        form = StudentResultForm(instance=sr, prefix=f"sr_{sr.pk}")
        student_forms.append({"enrolment": enrolment, "result": sr, "form": form})

    return render(request, "teachers/result_entry.html", {
        "page_title":   f"Result Entry — {sheet.offering.course.code}",
        "sheet":        sheet,
        "student_forms": student_forms,
    })


@login_required
@teacher_required
@require_POST
def result_submit(request, sheet_pk):
    """Teacher submits the result sheet for HOD/Admin approval."""
    sheet = get_object_or_404(ResultSheet, pk=sheet_pk)
    if not sheet.can_edit(request.user) and not request.user.is_superuser:
        if sheet.is_locked:
            messages.warning(request, "This result sheet is already approved and locked.")
        else:
            messages.error(request, "Cannot submit this sheet. You lack permission.")
        return redirect("teachers:result_sheet_list")

    sheet.status       = ResultSheet.SheetStatus.SUBMITTED
    sheet.submitted_by = request.user
    sheet.submitted_at = timezone.now()
    sheet.save()
    _result_audit(request.user, sheet, AuditAction.SUBMIT)

    reviewers = _department_reviewers(sheet)
    if reviewers.exists():
        NotificationService.send(
            notification_type=NotificationType.RESULT_SUBMITTED,
            recipients=reviewers,
            context={
                **_result_sheet_context(sheet),
                "submitted_by_name": request.user.get_full_name(),
            },
            module="RESULTS",
            idempotency_key=f"result_submitted:{sheet.pk}:{int(sheet.submitted_at.timestamp())}",
        )

    messages.success(request, "Result sheet submitted for approval.")
    return redirect("teachers:result_sheet_list")


@login_required
@hod_required
@require_POST
def result_approve(request, sheet_pk):
    """Admin/HOD approves the result sheet, locking it.
    HODs may only approve sheets for courses in their own department.
    """
    sheet = get_object_or_404(ResultSheet, pk=sheet_pk)
    ok, msg = _approve_sheet(sheet, request.user)
    (messages.success if ok else messages.error)(request, msg)
    return redirect("teachers:result_sheet_list")


@login_required
@hod_required
@require_POST
def result_reject(request, sheet_pk):
    """Admin/HOD rejects the result sheet with a note.
    HODs may only reject sheets for courses in their own department.
    """
    sheet = get_object_or_404(ResultSheet, pk=sheet_pk)
    note = request.POST.get("rejection_note", "")
    ok, msg = _reject_sheet(sheet, request.user, note)
    (messages.success if ok else messages.error)(request, msg)
    return redirect("teachers:result_sheet_list")


@login_required
@teacher_required
@require_POST
def result_publish(request, sheet_pk):
    """
    Publish an approved result sheet.

    Creates the immutable ResultVersion snapshot for this revision and
    notifies the enrolled students.  Admins, examinations officers and deans
    may publish.
    """
    sheet = get_object_or_404(ResultSheet, pk=sheet_pk)
    if not sheet.can_publish(request.user):
        messages.error(request, "You do not have permission to publish result sheets.")
        return redirect("teachers:result_sheet_view", sheet_pk=sheet.pk)
    if sheet.status != ResultSheet.SheetStatus.APPROVED:
        messages.error(request, "Only approved result sheets can be published.")
        return redirect("teachers:result_sheet_view", sheet_pk=sheet.pk)
    if sheet.published_at is not None:
        messages.info(request, "This result sheet has already been published.")
        return redirect("teachers:result_sheet_view", sheet_pk=sheet.pk)

    sheet.published_by = request.user
    sheet.published_at = timezone.now()
    sheet.save(update_fields=["published_by", "published_at", "updated_at"])
    ResultVersion.capture(sheet, actor=request.user)
    _result_audit(request.user, sheet, AuditAction.PUBLISH, {"revision": sheet.revision})

    NotificationService.send(
            notification_type=NotificationType.RESULT_PUBLISHED,
            recipients=EnrolmentQuery(sheet.offering),
            context={
                **_result_sheet_context(sheet),
                "revision": sheet.revision,
                "student_link": reverse("students:results_list"),
            },
            module="RESULTS",
            idempotency_key=f"result_published:{sheet.pk}:{sheet.revision}",
        )

    messages.success(
        request,
        f"Result sheet published (revision {sheet.revision}). Students notified.",
    )
    return redirect("teachers:result_sheet_view", sheet_pk=sheet.pk)


@login_required
@teacher_required
@require_POST
def result_revise(request, sheet_pk):
    """
    Return a published sheet to draft so corrections can be made.

    The published snapshot stays in history (immutable); the revision counter
    is bumped so the next publish creates the next version.  A mandatory
    reason is stored and sent to the sheet teacher.
    """
    sheet = get_object_or_404(ResultSheet, pk=sheet_pk)
    if not sheet.can_revise(request.user):
        messages.error(
            request,
            "Only administrators or examinations staff can revise published result sheets.",
        )
        return redirect("teachers:result_sheet_view", sheet_pk=sheet.pk)

    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "A reason is required to revise a published result sheet.")
        return redirect("teachers:result_sheet_view", sheet_pk=sheet.pk)

    sheet.status = ResultSheet.SheetStatus.OPEN
    sheet.published_by = None
    sheet.published_at = None
    sheet.revision = (sheet.revision or 1) + 1
    sheet.last_revision_note = reason
    sheet.last_revised_by = request.user
    sheet.last_revised_at = timezone.now()
    sheet.save(update_fields=[
        "status", "published_by", "published_at", "revision",
        "last_revision_note", "last_revised_by", "last_revised_at", "updated_at",
    ])
    _result_audit(
        request.user, sheet, AuditAction.REVISE,
        {"note": reason, "revision": sheet.revision},
    )

    if sheet.submitted_by:
        NotificationService.send(
            notification_type=NotificationType.RESULT_REVISED,
            recipients=[sheet.submitted_by],
            context={**_result_sheet_context(sheet), "reason": reason},
            module="RESULTS",
            idempotency_key=f"result_revised:{sheet.pk}:{sheet.revision}",
        )

    messages.warning(request, "Published results returned to draft for correction.")
    return redirect("teachers:result_sheet_view", sheet_pk=sheet.pk)


@login_required
def result_version_history(request, sheet_pk):
    """Immutable revision history of a result sheet."""
    sheet = get_object_or_404(ResultSheet, pk=sheet_pk)
    user = request.user

    is_teacher = (
        user.is_admin or user.is_superuser or _teacher_owns_offering(user, sheet.offering)
    )
    if not (is_teacher or sheet.can_publish(user)):
        messages.error(request, "Access denied.")
        return redirect("accounts:dashboard")

    return render(request, "teachers/result_version_history.html", {
        "page_title": f"Revision History — {sheet.offering.course.code}",
        "sheet":      sheet,
        "versions":   sheet.versions.all(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# HOD DASHBOARD — department result sheets
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hod_required
def hod_result_sheets(request):
    """HOD sees all result sheets for their department; can approve/reject submitted ones."""
    from academics.models import Department
    user = request.user

    if user.is_superuser or getattr(user, "is_admin", False):
        # Admins see everything
        sheets = ResultSheet.objects.select_related(
            "offering__course__department",
            "offering__semester__session",
            "submitted_by", "approved_by",
        ).order_by("-offering__semester__session__start_date")
        dept = None
    else:
        try:
            dept = Department.objects.filter(hod=user).first()
        except Exception:
            dept = None
        if not dept:
            messages.error(request, "You are not assigned as HOD of any department.")
            return redirect("teachers:dashboard")
        sheets = ResultSheet.objects.filter(
            offering__course__department=dept
        ).select_related(
            "offering__course__department",
            "offering__semester__session",
            "submitted_by", "approved_by",
        ).order_by("-offering__semester__session__start_date")

    return render(request, "teachers/hod_result_sheets.html", {
        "page_title": "Department Result Sheets",
        "sheets":     sheets,
        "dept":       dept,
    })


# ─────────────────────────────────────────────────────────────────────────────
# HOD REVIEW CENTER — prioritised queue with batch actions
# ─────────────────────────────────────────────────────────────────────────────

HOD_REVIEW_STATUSES = (
    ("submitted", "Awaiting approval"),
    ("approved",  "Approved"),
    ("rejected",  "Rejected"),
    ("all",       "All statuses"),
)


def _awaiting_stats(sheets):
    """Queue stats across a scoped queryset."""
    now = timezone.now()
    pending_qs = sheets.filter(status=ResultSheet.SheetStatus.SUBMITTED)
    pending_count = pending_qs.count()
    oldest = pending_qs.order_by("submitted_at").first()

    avg_wait_hours = 0
    if pending_count:
        total = sum(
            (now - (s.submitted_at or now)).total_seconds() / 3600
            for s in pending_qs.only("submitted_at")
        )
        avg_wait_hours = total / pending_count

    return {
        "pending_count": pending_count,
        "oldest":        oldest,
        "avg_wait_hours": round(avg_wait_hours, 1),
        "overdue_count":  pending_qs.filter(
            submitted_at__lt=now - timedelta(hours=REVIEW_SLA_HOURS)
        ).count(),
        "approved_count": sheets.filter(status=ResultSheet.SheetStatus.APPROVED).count(),
        "published_count": sheets.filter(published_at__isnull=False).count(),
    }


@login_required
@hod_required
def hod_review_center(request):
    """Prioritised HOD review queue with filters and batch approve/reject."""
    from academics.models import Department

    user = request.user
    dept, scoped = _hod_review_scope(user)

    status = request.GET.get("status", "submitted")
    q      = (request.GET.get("q") or "").strip()
    dept_pk = request.GET.get("dept") or ""

    stats = _awaiting_stats(scoped)

    sheets = scoped.select_related(
        "offering__course__department",
        "offering__semester__session",
        "submitted_by", "approved_by", "published_by",
    )
    if status and status != "all":
        sheets = sheets.filter(status=status)
    if q:
        sheets = sheets.filter(
            Q(offering__course__code__icontains=q)
            | Q(offering__course__title__icontains=q)
        )
    if dept_pk:
        sheets = sheets.filter(offering__course__department_id=dept_pk)

    queue = list(sheets.order_by("submitted_at", "offering__course__code"))
    now = timezone.now()
    for sheet in queue:
        awaiting = (now - sheet.submitted_at).total_seconds() / 3600 if sheet.submitted_at else 0
        sheet.awaiting_hours = round(awaiting, 1)
        sheet.awaiting_days  = int(awaiting // 24)
        sheet.is_overdue     = awaiting > REVIEW_SLA_HOURS

    return render(request, "teachers/hod_review_center.html", {
        "page_title":    "HOD Review Center",
        "dept":          dept,
        "queue":         queue,
        "stats":         stats,
        "status_choices": HOD_REVIEW_STATUSES,
        "current_status": status,
        "q":             q,
        "dept_pk":       dept_pk,
        "departments":   (
            Department.objects.order_by("name") if user.is_superuser or getattr(user, "is_admin", False)
            else Department.objects.none()
        ),
        "sla_hours":     REVIEW_SLA_HOURS,
    })


@login_required
@hod_required
@require_POST
def hod_review_batch(request):
    """Approve or reject multiple submitted result sheets in one action."""
    action = request.POST.get("action")
    raw_ids = request.POST.getlist("sheet_ids")
    note = (request.POST.get("rejection_note") or "").strip()

    if action not in ("approve", "reject"):
        messages.error(request, "Unknown batch action.")
        return redirect("teachers:hod_review_center")
    if not raw_ids:
        messages.warning(request, "No result sheets were selected.")
        return redirect("teachers:hod_review_center")

    sheets = ResultSheet.objects.filter(pk__in=raw_ids)
    ok_count = 0
    for sheet in sheets:
        if action == "approve":
            ok, msg = _approve_sheet(sheet, request.user)
        else:
            ok, msg = _reject_sheet(sheet, request.user, note)
        if ok:
            ok_count += 1
        else:
            messages.warning(
                request, f"{sheet.offering.course.code}: {msg}"
            )

    if action == "approve":
        messages.success(
            request,
            f"{ok_count} result sheet(s) approved and locked.",
        )
    else:
        messages.success(
            request,
            f"{ok_count} result sheet(s) rejected and returned for correction.",
        )
    return redirect("teachers:hod_review_center")


@login_required
def result_sheet_view(request, sheet_pk):
    """
    Read-only view of a result sheet.
    Accessible to: the teacher, admin, and the students in the offering.
    """
    sheet = get_object_or_404(
        ResultSheet.objects.select_related(
            "offering__course", "offering__semester__session",
            "submitted_by", "approved_by",
        ).prefetch_related(
            "student_results__enrolment__student",
        ),
        pk=sheet_pk,
    )
    user = request.user

    # Access control
    is_teacher = _teacher_owns_offering(user, sheet.offering)
    is_admin   = user.is_admin or user.is_superuser
    is_student = Enrolment.objects.filter(
        offering=sheet.offering, student=user, is_active=True
    ).exists()

    if not (is_teacher or is_admin or is_student):
        messages.error(request, "Access denied.")
        return redirect("accounts:dashboard")

    # Students only see their own result, and only after HOD approval
    if is_student and sheet.status != ResultSheet.SheetStatus.APPROVED:
        messages.warning(request, "Results are not yet published. Please wait for HOD approval.")
        return redirect("accounts:dashboard")

    results = sheet.student_results.select_related("enrolment__student").order_by(
        "enrolment__student__last_name"
    )
    if is_student:
        results = results.filter(enrolment__student=user)

    return render(request, "teachers/result_sheet_view.html", {
        "page_title": f"Results — {sheet.offering.course.code}",
        "sheet":      sheet,
        "results":    results,
        "is_teacher": is_teacher,
        "is_admin":   is_admin,
        "can_publish": sheet.can_publish(user),
    })


# ─────────────────────────────────────────────────────────────────────────────
# STUDENT PERFORMANCE (teacher view)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def student_performance(request, offering_pk, student_pk):
    """Per-student performance overview for a teacher's offering."""
    from django.contrib.auth import get_user_model
    User = get_user_model()

    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _teacher_owns_offering(request.user, offering):
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    student    = get_object_or_404(User, pk=student_pk, role="student")
    enrolment  = get_object_or_404(Enrolment, offering=offering, student=student)
    result     = StudentResult.objects.filter(enrolment=enrolment).first()

    # Attendance summary
    total_sessions  = AttendanceSheet.objects.filter(offering=offering, is_active=True).count()
    attended        = AttendanceRecord.objects.filter(
        sheet__offering=offering,
        student=student,
        status=AttendanceRecord.Status.PRESENT,
    ).count()
    attendance_pct  = round((attended / total_sessions * 100), 1) if total_sessions else 0

    # Assignment scores
    assignment_scores = AssignmentSubmission.objects.filter(
        assignment__offering=offering,
        student=student,
        score__isnull=False,
        is_active=True,
    ).select_related("assignment")

    context = {
        "page_title":        f"Performance — {student.get_full_name()}",
        "student":           student,
        "offering":          offering,
        "enrolment":         enrolment,
        "result":            result,
        "total_sessions":    total_sessions,
        "attended":          attended,
        "attendance_pct":    attendance_pct,
        "assignment_scores": assignment_scores,
    }
    return render(request, "teachers/student_performance.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# QUIZ PUBLISH TOGGLE  (teacher can publish a draft quiz later)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
@require_POST
def quiz_publish_toggle(request, pk):
    """Toggle the is_published flag on a quiz owned by this teacher."""
    quiz = get_object_or_404(Quiz.all_objects, pk=pk)
    if quiz.created_by != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    quiz.is_published = not quiz.is_published
    quiz.save(update_fields=["is_published", "updated_at"])
    if quiz.is_published:
        NotificationService.send_to_students(
            NotificationType.NEW_QUIZ,
            quiz.offering,
            context={
                "quiz_title": quiz.title,
                "start_datetime": quiz.start_datetime,
                "end_datetime": quiz.end_datetime,
            },
            obj=quiz,
            idempotency_key=f"new_quiz:{quiz.pk}",
        )
    state = "published" if quiz.is_published else "unpublished"
    messages.success(request, f"Quiz '{quiz.title}' {state}.")
    return redirect("teachers:quiz_list", offering_pk=quiz.offering.pk)


# ─────────────────────────────────────────────────────────────────────────────
# ASSIGNMENT PUBLISH TOGGLE  (teacher can publish a draft assignment later)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
@require_POST
def assignment_publish_toggle(request, pk):
    """Toggle status between DRAFT and PUBLISHED for an assignment."""
    assignment = get_object_or_404(Assignment.all_objects, pk=pk)
    if assignment.created_by != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    if assignment.status == "draft":
        assignment.status = "published"
        msg = f"Assignment '{assignment.title}' published — students can now see it."
        NotificationService.send_to_students(
            NotificationType.NEW_ASSIGNMENT,
            assignment.offering,
            context={
                "assignment_title": assignment.title,
                "due_date": assignment.due_date,
            },
            obj=assignment,
            idempotency_key=f"new_assignment:{assignment.pk}",
        )
    elif assignment.status == "published":
        assignment.status = "draft"
        msg = f"Assignment '{assignment.title}' moved back to draft."
    else:
        messages.warning(request, "Cannot toggle status of a closed/graded assignment.")
        return redirect("teachers:assignment_list", offering_pk=assignment.offering.pk)

    assignment.save(update_fields=["status", "updated_at"])
    messages.success(request, msg)
    return redirect("teachers:assignment_list", offering_pk=assignment.offering.pk)


# ─────────────────────────────────────────────────────────────────────────────
# MANUAL QUIZ GRADING  (teacher grades short-answer quiz attempts)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def quiz_attempt_detail(request, attempt_pk):
    """Show all answers for an attempt; allow teacher to manually grade each answer."""
    attempt = get_object_or_404(
        QuizAttempt.objects.select_related("quiz", "student").prefetch_related(
            "answers__question__choices",
            "answers__selected_choices",
        ),
        pk=attempt_pk,
    )
    quiz = attempt.quiz
    if quiz.created_by != request.user and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("teachers:my_courses")

    answers = attempt.answers.select_related("question").prefetch_related(
        "question__choices", "selected_choices"
    ).order_by("question__order")

    if request.method == "POST":
        total_awarded = 0
        for answer in answers:
            field_name = f"marks_{answer.pk}"
            raw = request.POST.get(field_name, "").strip()
            if raw != "":
                try:
                    marks = float(raw)
                    marks = max(0, min(marks, answer.question.marks))
                    answer.marks_awarded = marks
                    # For MCQ/True-False auto-graded, is_correct stays as set
                    # For SHORT answer, update is_correct based on marks
                    if answer.question.question_type == "short":
                        answer.is_correct = (marks >= answer.question.marks)
                    answer.save(update_fields=["marks_awarded", "is_correct"])
                except (ValueError, TypeError):
                    pass
            total_awarded += float(answer.marks_awarded or 0)

        attempt.score = round(total_awarded, 2)
        attempt.save(update_fields=["score"])
        NotificationService.send(
            NotificationType.QUIZ_RESULT_AVAILABLE,
            [attempt.student],
            context={
                "offering": quiz.offering,
                "quiz_title": quiz.title,
                "score": round(total_awarded, 2),
                "total": quiz.total_marks,
            },
            obj=quiz,
            idempotency_key=f"quiz_result:{quiz.pk}:{attempt.student_id}",
        )
        messages.success(request, f"Quiz attempt graded. Total: {attempt.score} / {quiz.total_marks}")
        return redirect("teachers:quiz_results", pk=quiz.pk)

    return render(request, "teachers/quiz_attempt_grade.html", {
        "page_title": f"Grade Attempt — {attempt.student.get_full_name()}",
        "attempt":    attempt,
        "quiz":       quiz,
        "answers":    answers,
    })
