"""
teachers/views.py

All views for the teachers app.
Access: teacher_required decorator from accounts.decorators.
Result submission also accessible to admins.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.decorators import role_required, teacher_required, hod_required
from academics.models import CourseAllocation, CourseOffering, Enrolment
from students.models import StudentNotification

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
    profile    = _get_teacher_profile(request.user)
    allocations = profile.get_current_semester_allocations()

    # Aggregate stats for this teacher
    allocation_pks    = allocations.values_list("pk", flat=True)
    offering_pks      = allocations.values_list("offering_id", flat=True)
    total_students    = Enrolment.objects.filter(
        offering_id__in=offering_pks, is_active=True
    ).count()
    pending_submissions = AssignmentSubmission.objects.filter(
        assignment__offering_id__in=offering_pks,
        score__isnull=True, is_active=True,
    ).count()
    open_result_sheets = ResultSheet.objects.filter(
        offering_id__in=offering_pks,
        status__in=[ResultSheet.SheetStatus.OPEN, ResultSheet.SheetStatus.REJECTED],
    ).count()
    recent_materials = LectureMaterial.objects.filter(
        allocation__in=allocation_pks, is_active=True
    ).select_related("allocation__offering__course").order_by("-created_at")[:5]

    context = {
        "page_title":           "Teacher Dashboard",
        "profile":              profile,
        "allocations":          allocations,
        "total_courses":        allocations.count(),
        "total_students":       total_students,
        "pending_submissions":  pending_submissions,
        "open_result_sheets":   open_result_sheets,
        "recent_materials":     recent_materials,
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
    if request.method == "POST" and form.is_valid():
        form.save()
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
            notify_students(
                assignment.offering,
                "New Assignment Posted",
                f"{assignment.title} is now available"
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
    quiz.save(update_fields=["status"])

    # Notify students
    students = Enrolment.objects.filter(
        offering=quiz.offering,
        is_active=True
    ).values_list("student", flat=True)

    StudentNotification.objects.bulk_create([
        StudentNotification(
            student_id=s,
            title="New Quiz Available",
            message=f"Quiz '{quiz.title}' is now available in {quiz.offering.course.code}.",
        )
        for s in students
    ])
    

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
    quiz.save(update_fields=["status"])

    messages.success(request, "Quiz unpublished successfully.")

    return redirect(
        "teachers:quiz_list",
        offering_pk=quiz.offering.pk
    )

    messages.success(request, "Quiz published successfully.")
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
            notify_students(
                quiz.offering,
                "New Quiz Available",
                f"{quiz.title} has been published for {quiz.offering.course.code}"
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
        messages.error(request, "This result sheet is locked or you lack permission.")
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
        messages.error(request, "Cannot submit this sheet.")
        return redirect("teachers:result_sheet_list")

    sheet.status       = ResultSheet.SheetStatus.SUBMITTED
    sheet.submitted_by = request.user
    sheet.submitted_at = timezone.now()
    sheet.save()
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

    # Department scope check for HODs
    if not request.user.is_superuser and not getattr(request.user, "is_admin", False):
        # User is HOD — verify course belongs to their department
        course_dept = sheet.offering.course.department
        teacher_profile = getattr(request.user, "teacher_profile", None)
        hod_dept = None
        if teacher_profile:
            # Check if this teacher is HOD of any department
            try:
                from academics.models import Department
                hod_dept = Department.objects.filter(hod=request.user).first()
            except Exception:
                pass
        if not hod_dept or hod_dept != course_dept:
            messages.error(request, "You can only approve results for courses in your department.")
            return redirect("teachers:result_sheet_list")

    if sheet.status != ResultSheet.SheetStatus.SUBMITTED:
        messages.error(request, "Only submitted result sheets can be approved.")
        return redirect("teachers:result_sheet_list")

    sheet.status      = ResultSheet.SheetStatus.APPROVED
    sheet.approved_by = request.user
    sheet.approved_at = timezone.now()
    sheet.save()

    try:
        from core.utils import update_student_gpa
        student_ids = sheet.student_results.values_list(
            "enrolment__student_id", flat=True
        ).distinct()
        for student_id in student_ids:
            from accounts.models import EduProUser
            try:
                update_student_gpa(EduProUser.objects.get(pk=student_id))
            except EduProUser.DoesNotExist:
                pass
    except ImportError:
        pass

    messages.success(request, "Result sheet approved and locked.")
    return redirect("teachers:result_sheet_list")


@login_required
@hod_required
@require_POST
def result_reject(request, sheet_pk):
    """Admin/HOD rejects the result sheet with a note.
    HODs may only reject sheets for courses in their own department.
    """
    sheet = get_object_or_404(ResultSheet, pk=sheet_pk)

    # Department scope check for HODs
    if not request.user.is_superuser and not getattr(request.user, "is_admin", False):
        course_dept = sheet.offering.course.department
        try:
            from academics.models import Department
            hod_dept = Department.objects.filter(hod=request.user).first()
        except Exception:
            hod_dept = None
        if not hod_dept or hod_dept != course_dept:
            messages.error(request, "You can only reject results for courses in your department.")
            return redirect("teachers:result_sheet_list")

    note  = request.POST.get("rejection_note", "No reason provided.")
    sheet.status         = ResultSheet.SheetStatus.REJECTED
    sheet.rejection_note = note
    sheet.save()
    messages.warning(request, "Result sheet rejected and returned to teacher.")
    return redirect("teachers:result_sheet_list")


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

    # Students only see their own result
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
        messages.success(request, f"Quiz attempt graded. Total: {attempt.score} / {quiz.total_marks}")
        return redirect("teachers:quiz_results", pk=quiz.pk)

    return render(request, "teachers/quiz_attempt_grade.html", {
        "page_title": f"Grade Attempt — {attempt.student.get_full_name()}",
        "attempt":    attempt,
        "quiz":       quiz,
        "answers":    answers,
    })
