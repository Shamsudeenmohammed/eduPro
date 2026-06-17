"""
academics/views.py

All views for the academics app.
Access control via accounts.decorators.

Changes from v1:
- Result sheet views added:
    result_sheet_list    — teacher sees own sheets, HOD sees dept pending,
                           admin sees all
    result_sheet_create  — teacher only (must have allocation for offering)
    result_sheet_submit  — teacher submits DRAFT → SUBMITTED
    result_sheet_hod_approve  — HOD approves SUBMITTED → HOD_APPROVED
                                (auto-scoped: user must be HOD of sheet.dept)
    result_sheet_finalize     — admin finalizes HOD_APPROVED → FINALIZED
    result_sheet_revert       — HOD/admin reverts SUBMITTED → DRAFT
- my_courses: filters strictly to teacher's own allocations.
- academics_dashboard: updated for multi-responsibility context.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.decorators import (
    admin_required,
    hod_required,
    teacher_required,
    responsibility_required,
)
from accounts.models import StaffResponsibility

from .forms import (
    AcademicSessionForm,
    BulkAllocationForm,
    CourseAllocationForm,
    CourseForm,
    CourseOfferingForm,
    DepartmentForm,
    EnrolmentForm,
    FacultyForm,
    InstitutionForm,
    LevelForm,
    ProgramForm,
    ResultSheetForm,
    SemesterForm,
    StudentProfileForm,
    TeacherDepartmentForm,
)
from .models import (
    AcademicSession,
    Course,
    CourseAllocation,
    CourseOffering,
    Department,
    Enrolment,
    Faculty,
    Institution,
    Level,
    Program,
    ResultSheet,
    Semester,
    StudentProfile,
    TeacherDepartment,
)


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD / OVERVIEW
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def academics_dashboard(request):
    current_session  = AcademicSession.get_current()
    current_semester = Semester.get_current()
    context = {
        "page_title":        "Academics",
        "current_session":   current_session,
        "current_semester":  current_semester,
    }

    if request.user.is_admin or request.user.is_superuser:
        context.update({
            "total_faculties":   Faculty.objects.count(),
            "total_departments": Department.objects.count(),
            "total_programs":    Program.objects.count(),
            "total_courses":     Course.objects.count(),
            "total_offerings":   CourseOffering.objects.filter(is_active=True).count(),
            "total_allocations": CourseAllocation.objects.filter(is_active=True).count(),
            "total_enrolments":  Enrolment.objects.filter(is_active=True).count(),
            "pending_finalize":  ResultSheet.objects.filter(
                status=ResultSheet.Status.HOD_APPROVED
            ).count(),
            "recent_offerings": (
                CourseOffering.objects
                .select_related("course", "semester__session", "level__program")
                .filter(is_active=True)
                .order_by("-created_at")[:8]
            ),
        })

    elif request.user.is_teacher:
        my_allocations = (
            CourseAllocation.objects
            .filter(teacher=request.user, is_active=True)
            .select_related(
                "offering__course",
                "offering__semester__session",
                "offering__level__program",
            )
            .order_by("-offering__semester__session__start_date")
        )
        responsibilities = request.user.get_active_responsibilities()
        is_hod = StaffResponsibility.HOD in responsibilities

        context.update({
            "my_allocations":   my_allocations,
            "allocation_count": my_allocations.count(),
            "responsibilities": responsibilities,
            "is_hod":           is_hod,
        })

        if is_hod:
            hod_depts = request.user.get_hod_departments()
            context["pending_hod_sheets"] = ResultSheet.objects.filter(
                department__in=hod_depts,
                status=ResultSheet.Status.SUBMITTED,
            ).count()

    else:  # student
        my_enrolments = (
            Enrolment.objects
            .filter(student=request.user, is_active=True)
            .select_related(
                "offering__course",
                "offering__semester__session",
                "offering__level__program",
            )
            .order_by("-offering__semester__session__start_date")
        )
        try:
            student_profile = request.user.academic_profile
        except StudentProfile.DoesNotExist:
            student_profile = None

        context.update({
            "my_enrolments":    my_enrolments,
            "enrolment_count":  my_enrolments.count(),
            "student_profile":  student_profile,
        })

    return render(request, "academics/dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# INSTITUTION (admin-only, unchanged)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def institution_list(request):
    institutions = Institution.all_objects.order_by("name")
    return render(request, "academics/institution_list.html", {
        "page_title": "Institutions",
        "institutions": institutions,
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def institution_create(request):
    form = InstitutionForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Institution created successfully.")
        return redirect("academics:institution_list")
    return render(request, "academics/institution_form.html", {
        "page_title": "Add Institution",
        "form": form,
        "cancel_url": "academics:institution_list",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def institution_edit(request, pk):
    institution = get_object_or_404(Institution.all_objects, pk=pk)
    form = InstitutionForm(request.POST or None, request.FILES or None, instance=institution)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Institution updated.")
        return redirect("academics:institution_list")
    return render(request, "academics/institution_form.html", {
        "page_title": "Edit Institution",
        "form": form,
        "cancel_url": "academics:institution_list",
    })


# ─────────────────────────────────────────────────────────────────────────────
# FACULTY (admin-only)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def faculty_list(request):
    faculties = Faculty.all_objects.select_related("institution").order_by("name")
    return render(request, "academics/faculty_list.html", {
        "page_title": "Faculties",
        "faculties": faculties,
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def faculty_create(request):
    form = FacultyForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Faculty created.")
        return redirect("academics:faculty_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Add Faculty",
        "form": form,
        "cancel_url": "academics:faculty_list",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def faculty_edit(request, pk):
    faculty = get_object_or_404(Faculty.all_objects, pk=pk)
    form = FacultyForm(request.POST or None, request.FILES or None, instance=faculty)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Faculty updated.")
        return redirect("academics:faculty_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Edit Faculty",
        "form": form,
        "cancel_url": "academics:faculty_list",
    })
    
@login_required
@admin_required
def faculty_detail(request, pk):
    faculty = get_object_or_404(Faculty.all_objects.prefetch_related("departments"), pk=pk)
    return render(request, "academics/faculty_detail.html", {
        "page_title": faculty.name,
        "faculty": faculty,
    })


# ─────────────────────────────────────────────────────────────────────────────
# DEPARTMENT (admin-only)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def department_list(request):
    departments = (
        Department.all_objects
        .select_related("faculty__institution", "hod")
        .order_by("faculty__name", "name")
    )
    return render(request, "academics/department_list.html", {
        "page_title": "Departments",
        "departments": departments,
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def department_create(request):
    form = DepartmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Department created.")
        return redirect("academics:department_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Add Department",
        "form": form,
        "cancel_url": "academics:department_list",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def department_edit(request, pk):
    dept = get_object_or_404(Department.all_objects, pk=pk)
    form = DepartmentForm(request.POST or None, instance=dept)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Department updated.")
        return redirect("academics:department_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Edit Department",
        "form": form,
        "cancel_url": "academics:department_list",
    })

@login_required
@admin_required
def department_detail(request, pk):
    dept = get_object_or_404(
        Department.all_objects
        .select_related("faculty")
        .prefetch_related("programs", "courses", "teacher_memberships__teacher"),
        pk=pk,
    )
    return render(request, "academics/department_detail.html", {
        "page_title": dept.name,
        "dept": dept,
    })

# ─────────────────────────────────────────────────────────────────────────────
# PROGRAM / LEVEL / SESSION / SEMESTER / COURSE / OFFERING / ALLOCATION
# (admin-only — structure unchanged from v1, keeping stubs for brevity)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def program_list(request):
    qs = Program.all_objects.select_related("department__faculty").order_by("code")
    return render(request, "academics/program_list.html", {
        "page_title": "Programs", "programs": qs,
    })
    
    
@login_required
def program_detail(request, pk):
    program = get_object_or_404(
        Program.all_objects.select_related("department__faculty")
        .prefetch_related("levels", "students__student"),
        pk=pk,
    )
    return render(request, "academics/program_detail.html", {
        "page_title": program.name,
        "program": program,
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def program_create(request):
    form = ProgramForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Program created.")
        return redirect("academics:program_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Add Program", "form": form, "cancel_url": "academics:program_list",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def program_edit(request, pk):
    program = get_object_or_404(Program.all_objects, pk=pk)
    form = ProgramForm(request.POST or None, instance=program)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Program updated.")
        return redirect("academics:program_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Edit Program", "form": form, "cancel_url": "academics:program_list",
    })


@login_required
@admin_required
def session_list(request):
    sessions = AcademicSession.objects.order_by("-start_date")
    return render(request, "academics/session_list.html", {
        "page_title": "Academic Sessions", "sessions": sessions,
    })
    
@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def session_edit(request, pk):
    session = get_object_or_404(AcademicSession, pk=pk)
    form = AcademicSessionForm(request.POST or None, instance=session)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Session updated.")
        return redirect("academics:session_list")
    return render(request, "academics/generic_form.html", {
        "page_title": f"Edit: {session.name}",
        "form": form,
        "object": session,
        "cancel_url": "academics:session_list",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def session_create(request):
    form = AcademicSessionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Session created.")
        return redirect("academics:session_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Add Session", "form": form, "cancel_url": "academics:session_list",
    })


@login_required
@admin_required
def semester_list(request):
    semesters = Semester.objects.select_related("session").order_by("-session__start_date", "name")
    return render(request, "academics/semester_list.html", {
        "page_title": "Semesters", "semesters": semesters,
    })

@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def semester_edit(request, pk):
    semester = get_object_or_404(Semester, pk=pk)
    form = SemesterForm(request.POST or None, instance=semester)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Semester updated.")
        return redirect("academics:semester_list")
    return render(request, "academics/generic_form.html", {
        "page_title": f"Edit: {semester}",
        "form": form,
        "object": semester,
        "cancel_url": "academics:semester_list",
    })

@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def semester_create(request):
    form = SemesterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Semester created.")
        return redirect("academics:semester_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Add Semester", "form": form, "cancel_url": "academics:semester_list",
    })


@login_required
def course_list(request):
    qs = (
        Course.objects  # changed from all_objects → only active courses
        .select_related("department__faculty")
        .order_by("code")
    )
    dept_id = request.GET.get("dept")
    if dept_id:
        qs = qs.filter(department_id=dept_id)

    paginator   = Paginator(qs, 25)
    page_obj    = paginator.get_page(request.GET.get("page"))
    departments = Department.objects.select_related("faculty").order_by("name")

    return render(request, "academics/course_list.html", {
        "page_title":    "Course Catalogue",
        "page_obj":      page_obj,
        "departments":   departments,
        "selected_dept": dept_id,
    })
    

@login_required
def course_detail(request, pk):
    course = get_object_or_404(
        Course.all_objects
        .select_related("department__faculty")
        .prefetch_related("offerings__semester__session", "offerings__level", "prerequisites"),
        pk=pk,
    )
    return render(request, "academics/course_detail.html", {
        "page_title": course.title,
        "course": course,
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def course_create(request):
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Course created.")
        return redirect("academics:course_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Add Course", "form": form, "cancel_url": "academics:course_list",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def course_edit(request, pk):
    course = get_object_or_404(Course.all_objects, pk=pk)
    form = CourseForm(request.POST or None, instance=course)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Course updated.")
        return redirect("academics:course_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Edit Course", "form": form, "cancel_url": "academics:course_list",
    })


@login_required
@admin_required
def offering_list(request):
    qs = (
        CourseOffering.all_objects
        .select_related("course__department", "semester__session", "level__program")
        .order_by("-semester__session__start_date", "course__code")
    )
    paginator = Paginator(qs, 30)
    return render(request, "academics/offering_list.html", {
        "page_title": "Course Offerings",
        "page_obj": paginator.get_page(request.GET.get("page")),
    })
    
@login_required
def offering_detail(request, pk):
    offering = get_object_or_404(
        CourseOffering.all_objects
        .select_related("course__department", "semester__session", "level__program")
        .prefetch_related(
            "allocations__teacher",
            "enrolments__student",
        ),
        pk=pk,
    )
    # Check teacher access
    if request.user.is_teacher and not request.user.is_superuser:
        if not offering.allocations.filter(teacher=request.user, is_active=True).exists():
            messages.error(request, "You are not allocated to this course offering.")
            return redirect("academics:dashboard")

    return render(request, "academics/offering_detail.html", {
        "page_title": f"{offering.course.code} — Offering Detail",
        "offering": offering,
    })

@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def offering_edit(request, pk):
    offering = get_object_or_404(CourseOffering.all_objects, pk=pk)
    form = CourseOfferingForm(request.POST or None, instance=offering)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Offering updated.")
        return redirect("academics:offering_detail", pk=pk)
    return render(request, "academics/generic_form.html", {
        "page_title": f"Edit Offering: {offering.course.code}",
        "form": form,
        "object": offering,
        "cancel_url": "academics:offering_list",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def offering_create(request):
    form = CourseOfferingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            form.save(); messages.success(request, "Course offering created.")
            return redirect("academics:offering_list")
        except IntegrityError:
            messages.error(request, "This offering already exists.")
    return render(request, "academics/generic_form.html", {
        "page_title": "Add Course Offering", "form": form, "cancel_url": "academics:offering_list",
    })


@login_required
@admin_required
def allocation_list(request):
    qs = (
        CourseAllocation.all_objects
        .select_related("teacher", "offering__course", "offering__semester__session")
        .order_by("-offering__semester__session__start_date", "teacher__last_name")
    )
    paginator = Paginator(qs, 30)
    return render(request, "academics/allocation_list.html", {
        "page_title": "Course Allocations",
        "page_obj": paginator.get_page(request.GET.get("page")),
    })

@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def allocation_bulk(request):
    """Bulk-allocate one teacher to multiple offerings at once."""
    form = BulkAllocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        teacher   = form.cleaned_data["teacher"]
        offerings = form.cleaned_data["offerings"]
        role      = form.cleaned_data["role"]
        created   = 0
        skipped   = 0
        for offering in offerings:
            _, was_created = CourseAllocation.all_objects.get_or_create(
                teacher=teacher, offering=offering, role=role,
                defaults={"allocated_by": request.user, "is_active": True},
            )
            if was_created:
                created += 1
            else:
                skipped += 1
        messages.success(
            request,
            f"Bulk allocation done: {created} created, {skipped} already existed.",
        )
        return redirect("academics:allocation_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Bulk Teacher Allocation",
        "form": form,
        "cancel_url": "academics:allocation_list",
    })

@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def allocation_create(request):
    form = CourseAllocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            alloc = form.save(commit=False)
            alloc.allocated_by = request.user
            alloc.save()
            messages.success(request, f"Teacher allocated to {alloc.offering.course.code}.")
            return redirect("academics:allocation_list")
        except IntegrityError:
            messages.error(request, "This allocation already exists.")
    return render(request, "academics/generic_form.html", {
        "page_title": "Assign Teacher to Course", "form": form,
        "cancel_url": "academics:allocation_list",
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def allocation_deactivate(request, pk):
    alloc = get_object_or_404(CourseAllocation.all_objects, pk=pk)
    alloc.is_active = False
    alloc.save()
    messages.info(request, "Allocation deactivated.")
    return redirect("academics:allocation_list")


@login_required
@admin_required
@require_http_methods(["POST"])
def allocation_activate(request, pk):
    alloc = get_object_or_404(CourseAllocation.all_objects, pk=pk)
    alloc.is_active = True
    alloc.save()
    messages.success(request, "Allocation activated.")
    return redirect("academics:allocation_list")


# ─────────────────────────────────────────────────────────────────────────────
# ENROLMENT (admin-only)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def enrolment_list(request):
    qs = (
        Enrolment.all_objects
        .select_related("student", "offering__course", "offering__semester__session")
        .order_by("-enrolled_at")
    )
    paginator = Paginator(qs, 30)
    return render(request, "academics/enrolment_list.html", {
        "page_title": "Student Enrolments",
        "page_obj": paginator.get_page(request.GET.get("page")),
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def enrolment_create(request):
    form = EnrolmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            e = form.save()
            messages.success(request, f"{e.student.get_full_name()} enrolled in {e.offering.course.code}.")
            return redirect("academics:enrolment_list")
        except IntegrityError:
            messages.error(request, "This student is already enrolled in that offering.")
    return render(request, "academics/generic_form.html", {
        "page_title": "Enrol Student", "form": form, "cancel_url": "academics:enrolment_list",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def enrolment_bulk(request):
    from .forms import BulkEnrolmentForm
    form = BulkEnrolmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        offering = form.cleaned_data["offering"]
        students = form.cleaned_data["students"]
        created = skipped = 0
        for student in students:
            _, was_created = Enrolment.all_objects.get_or_create(
                student=student, offering=offering,
                defaults={"is_active": True},
            )
            if was_created: created += 1
            else:           skipped += 1
        messages.success(request, f"Bulk enrolment done: {created} enrolled, {skipped} already existed.")
        return redirect("academics:enrolment_list")
    return render(request, "academics/enrolment_bulk.html", {
        "page_title": "Bulk Enrol Students", "form": form,
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def enrolment_activate(request, pk):
    e = get_object_or_404(Enrolment.all_objects, pk=pk)
    e.is_active = True; e.save()
    messages.success(request, "Enrolment activated.")
    return redirect("academics:enrolment_list")


@login_required
@admin_required
@require_http_methods(["POST"])
def enrolment_deactivate(request, pk):
    e = get_object_or_404(Enrolment.all_objects, pk=pk)
    e.is_active = False; e.save()
    messages.info(request, "Enrolment deactivated.")
    return redirect("academics:enrolment_list")


# ─────────────────────────────────────────────────────────────────────────────
# MY COURSES — teacher's own allocations only
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def my_courses(request):
    """
    Teacher's view of THEIR OWN allocated courses only.
    Strict: filtered by teacher=request.user — cannot see others' courses.
    """
    allocations = (
        CourseAllocation.objects
        .filter(teacher=request.user, is_active=True)
        .select_related(
            "offering__course__department",
            "offering__semester__session",
            "offering__level__program",
        )
        .order_by("-offering__semester__session__start_date", "offering__course__code")
    )
    return render(request, "academics/my_courses.html", {
        "page_title": "My Courses",
        "allocations": allocations,
    })


# ─────────────────────────────────────────────────────────────────────────────
# MY ENROLMENTS — student view
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def my_enrolments(request):
    enrolments = (
        Enrolment.objects
        .filter(student=request.user, is_active=True)
        .select_related(
            "offering__course__department",
            "offering__semester__session",
            "offering__level__program",
        )
        .order_by("-offering__semester__session__start_date")
    )
    try:
        student_profile = request.user.academic_profile
    except StudentProfile.DoesNotExist:
        student_profile = None

    return render(request, "academics/my_enrolments.html", {
        "page_title":      "My Courses",
        "enrolments":      enrolments,
        "student_profile": student_profile,
    })


# ─────────────────────────────────────────────────────────────────────────────
# STUDENT PROFILE (admin)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def student_profile_edit(request, user_pk):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    student = get_object_or_404(User, pk=user_pk, role="student")
    profile, _ = StudentProfile.all_objects.get_or_create(student=student)
    form = StudentProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Academic profile updated for {student.get_full_name()}.")
        return redirect("accounts:user_detail", pk=user_pk)
    return render(request, "academics/generic_form.html", {
        "page_title": f"Academic Profile: {student.get_full_name()}",
        "form": form,
        "cancel_url": "accounts:user_list",
    })


# ─────────────────────────────────────────────────────────────────────────────
# TEACHER–DEPARTMENT ASSIGNMENT (admin)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def teacher_dept_list(request):
    memberships = (
        TeacherDepartment.all_objects
        .select_related("teacher", "department__faculty")
        .order_by("teacher__last_name")
    )
    return render(request, "academics/teacher_dept_list.html", {
        "page_title": "Teacher–Department Assignments",
        "memberships": memberships,
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def teacher_dept_create(request):
    form = TeacherDepartmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Teacher assigned to department.")
        return redirect("academics:teacher_dept_list")
    return render(request, "academics/generic_form.html", {
        "page_title": "Assign Teacher to Department",
        "form": form,
        "cancel_url": "academics:teacher_dept_list",
    })


# ─────────────────────────────────────────────────────────────────────────────
# RESULT SHEET VIEWS  ← NEW
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def result_sheet_list(request):
    """
    - Admin / superuser: see all sheets.
    - HOD: see sheets for their departments.
    - Teacher: see only their own sheets.
    """
    user = request.user

    if user.is_admin or user.is_superuser:
        qs = (
            ResultSheet.objects
            .select_related(
                "offering__course", "offering__semester__session",
                "department", "submitted_by",
            )
            .order_by("-created_at")
        )
        page_title = "All Result Sheets"

    elif user.has_responsibility(StaffResponsibility.HOD):
        hod_depts = user.get_hod_departments()
        qs = (
            ResultSheet.objects
            .filter(department__in=hod_depts)
            .select_related(
                "offering__course", "offering__semester__session",
                "department", "submitted_by",
            )
            .order_by("-created_at")
        )
        page_title = "Department Result Sheets"

    else:
        qs = (
            ResultSheet.objects
            .filter(submitted_by=user)
            .select_related(
                "offering__course", "offering__semester__session",
                "department",
            )
            .order_by("-created_at")
        )
        page_title = "My Result Sheets"

    # Status filter
    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "teachers/result_sheet_list.html", {
        "page_title":     page_title,
        "page_obj":       page_obj,
        "sheets":         page_obj.object_list,
        "status_choices": ResultSheet.Status.choices,
        "status_filter":  status_filter,
    })


@login_required
@teacher_required
@require_http_methods(["GET", "POST"])
def result_sheet_create(request):
    """
    Teacher creates a result sheet for one of their allocated offerings.
    The department is automatically populated from the offering's course.
    """
    form = ResultSheetForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            sheet, created = ResultSheet.create_for_offering(
                offering=form.cleaned_data["offering"],
                submitted_by=request.user,
            )
            if created:
                messages.success(request, "Result sheet created as DRAFT.")
            else:
                messages.info(request, "A result sheet already exists for this offering.")
            return redirect("academics:result_sheet_detail", pk=sheet.pk)
        except ValidationError as e:
            messages.error(request, str(e.message))

    return render(request, "academics/result_sheet_form.html", {
        "page_title": "Create Result Sheet",
        "form": form,
        "cancel_url": "academics:result_sheet_list",
    })


@login_required
@teacher_required
def result_sheet_detail(request, pk):
    """View a result sheet. Access: teacher (own), HOD (dept), admin."""
    sheet = get_object_or_404(
        ResultSheet.objects.select_related(
            "offering__course", "offering__semester__session",
            "offering__level__program", "department",
            "submitted_by", "hod_approved_by", "finalized_by",
        ),
        pk=pk,
    )
    user = request.user

    # Access control
    is_owner     = sheet.submitted_by == user
    is_dept_hod  = user.is_hod_of(sheet.department)
    is_admin_usr = user.is_admin or user.is_superuser
    if not (is_owner or is_dept_hod or is_admin_usr):
        raise PermissionDenied

    return render(request, "academics/result_sheet_detail.html", {
        "page_title":   f"Result Sheet — {sheet.offering.course.code}",
        "sheet":        sheet,
        "is_owner":     is_owner,
        "is_dept_hod":  is_dept_hod,
        "is_admin_usr": is_admin_usr,
    })


@login_required
@teacher_required
@require_http_methods(["POST"])
def result_sheet_submit(request, pk):
    """Teacher submits a DRAFT sheet for HOD review."""
    sheet = get_object_or_404(ResultSheet, pk=pk, submitted_by=request.user)
    try:
        sheet.submit(request.user)
        messages.success(
            request,
            f"Result sheet for {sheet.offering.course.code} submitted for HOD review."
        )
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("academics:result_sheet_detail", pk=pk)


@login_required
@hod_required
@require_http_methods(["POST"])
def result_sheet_hod_approve(request, pk):
    """
    HOD approves a SUBMITTED sheet.
    Enforces department match: the acting user must be HOD of the sheet's dept.
    """
    sheet = get_object_or_404(
        ResultSheet.objects.select_related("department"),
        pk=pk,
        status=ResultSheet.Status.SUBMITTED,
    )
    try:
        sheet.hod_approve(request.user)
        messages.success(
            request,
            f"Result sheet for {sheet.offering.course.code} approved. "
            f"Pending final admin sign-off."
        )
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("academics:result_sheet_detail", pk=pk)


@login_required
@admin_required
@require_http_methods(["POST"])
def result_sheet_finalize(request, pk):
    """Admin finalizes an HOD_APPROVED sheet. After this it is immutable."""
    sheet = get_object_or_404(
        ResultSheet,
        pk=pk,
        status=ResultSheet.Status.HOD_APPROVED,
    )
    try:
        sheet.finalize(request.user)
        messages.success(
            request,
            f"Result sheet for {sheet.offering.course.code} has been finalized."
        )
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("academics:result_sheet_detail", pk=pk)


@login_required
@require_http_methods(["POST"])
def result_sheet_revert(request, pk):
    """
    HOD or admin reverts a SUBMITTED sheet back to DRAFT
    (allows teacher to correct errors).
    """
    sheet = get_object_or_404(
        ResultSheet.objects.select_related("department"),
        pk=pk,
        status=ResultSheet.Status.SUBMITTED,
    )
    try:
        sheet.revert_to_draft(request.user)
        messages.info(
            request,
            f"Result sheet for {sheet.offering.course.code} reverted to DRAFT."
        )
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(getattr(e, "message", e)))
    return redirect("academics:result_sheet_detail", pk=pk)


@login_required
@admin_required
def result_sheet_admin_list(request):
    """Admin: view all sheets with any status filter."""
    qs = (
        ResultSheet.objects
        .select_related(
            "offering__course", "offering__semester__session",
            "department", "submitted_by",
        )
        .order_by("-created_at")
    )
    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs, 25)
    return render(request, "academics/result_sheet_admin.html", {
        "page_title":     "Result Sheets — Admin View",
        "page_obj":       paginator.get_page(request.GET.get("page")),
        "status_choices": ResultSheet.Status.choices,
        "status_filter":  status_filter,
    })
