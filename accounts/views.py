"""
accounts/views.py

Authentication + core account management for eduPro ERP.

Key changes from v1:
- register_view: user is created inactive; shown "pending approval" page.
  No login() call after registration.
- login_view: LoginForm already rejects inactive users via the form's clean().
- dashboard_redirect: routes to role-appropriate dashboard.
- teacher_dashboard: dynamically renders combined modules based on
  StaffResponsibilities (Teacher + HOD panels rendered together).
- admin_approval views: admin can approve/reject pending (inactive) users.
- Activation flow: portal.AdmissionApplication → admin approves →
  provision_student_from_application() creates/links the EduProUser.
"""

import csv

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods,require_POST

from .decorators import (
    admin_required,
    anonymous_required,
    hod_required,
    teacher_required,
)
from .forms import (
    BulkStudentUploadForm,
    LoginForm,
    PendingRegistrationForm,
    ProfileForm,
    UserInfoForm,
)
from .models import EduProUser, Role, StaffResponsibility, UserProfile, UserStaffRole
from students.models import CourseRegistrationRequest

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _set_session_expiry(request, remember_me: bool):
    request.session.set_expiry(60 * 60 * 24 * 14 if remember_me else 0)


def _safe_redirect(next_url: str):
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return None


def _role_dashboard_name(user):
    """Return URL name (string) for this user's primary dashboard."""
    if user.is_admin:
        return "accounts:dashboard"
    if user.is_teacher:
        return "teachers:dashboard"
    if user.is_approved_student:
        return "students:dashboard"
    return "accounts:student_pending"


# ─────────────────────────────────────────────────────────────────────────────
# AUTH VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@anonymous_required()
@require_http_methods(["GET", "POST"])
def login_view(request):
    form = LoginForm(request=request, data=request.POST or None)
    next_url = request.POST.get("next") or request.GET.get("next")

    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)
        _set_session_expiry(request, form.cleaned_data.get("remember_me", False))
        messages.success(request, f"Welcome back, {user.get_short_name()}!")

        safe_next = _safe_redirect(next_url)
        if safe_next:
            return redirect(safe_next)
        return redirect(_role_dashboard_name(user))

    return render(request, "accounts/login.html", {
        "form": form,
        "next": next_url or "",
        "page_title": "Sign In",
    })


def logout_view(request):
    logout(request)
    messages.info(request, "You have been signed out successfully.")
    return redirect("accounts:login")


@login_required
def dashboard_redirect(request):
    """Single entry point → redirects user to correct dashboard."""
    return redirect(_role_dashboard_name(request.user))


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRATION — controlled onboarding (no immediate dashboard access)
# ─────────────────────────────────────────────────────────────────────────────

@anonymous_required()
@require_http_methods(["GET", "POST"])
def register_view(request):
    """
    Self-registration for *staff* accounts only (teachers, admissions officers).

    Admission applicants MUST use portal.AdmissionApplication instead.

    The created account has is_active=False.  No login() is called.
    User sees a "pending approval" confirmation page.
    """
    form = PendingRegistrationForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.save()
        # ← No login() call here: account is inactive until admin approves
        return render(request, "accounts/registration_pending.html", {
            "page_title": "Registration Received",
            "user_email": user.email,
        })

    return render(request, "accounts/register.html", {
        "form": form,
        "page_title": "Staff Registration",
    })


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARDS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def student_dashboard(request):
    if not (request.user.is_student or request.user.is_superuser):
        raise PermissionDenied
    if not request.user.is_approved_student:
        return redirect("accounts:student_pending")

    from academics.models import Enrolment, StudentProfile, AcademicSession, Semester

    current_session  = AcademicSession.get_current()
    current_semester = Semester.get_current()
    enrolments = (
        Enrolment.objects
        .filter(student=request.user, is_active=True)
        .select_related("offering__course", "offering__semester__session")
        .order_by("-offering__semester__session__start_date")
    )
    try:
        student_profile = request.user.academic_profile
    except StudentProfile.DoesNotExist:
        student_profile = None

    return render(request, "students/dashboard.html", {
        "page_title":       "Student Dashboard",
        "current_session":  current_session,
        "current_semester": current_semester,
        "enrolments":       enrolments,
        "student_profile":  student_profile,
    })


@login_required
def student_pending(request):
    """
    Landing for all applicants (approved or not) after portal login.
    Approved students see their admission letter here instead of being
    sent straight to the student dashboard.
    """
    if not (request.user.is_student or request.user.is_superuser):
        raise PermissionDenied

    from portal.models import AdmissionApplication
    application = AdmissionApplication.objects.filter(
        user=request.user
    ).order_by("-created_at").first()

    doc_requests = application.document_requests.all() if application else []

    return render(request, "students/pending.html", {
        "page_title": "Application Status",
        "application": application,
        "doc_requests": doc_requests,
    })


@login_required
@teacher_required
def teacher_dashboard(request):
    """
    Combined dashboard that renders different module panels based on the
    teacher's active StaffResponsibilities.

    Panels rendered:
        always:     my_courses (CourseAllocations)
        if HOD:     dept_results_pending (ResultSheets awaiting HOD approval)
        if DEAN:    faculty overview (stub)
    """
    from academics.models import CourseAllocation, AcademicSession, Semester

    responsibilities = request.user.get_active_responsibilities()
    is_hod   = StaffResponsibility.HOD   in responsibilities
    is_dean  = StaffResponsibility.DEAN  in responsibilities

    current_session  = AcademicSession.get_current()
    current_semester = Semester.get_current()

    my_allocations = (
        CourseAllocation.objects
        .filter(teacher=request.user, is_active=True)
        .select_related(
            "offering__course",
            "offering__semester__session",
            "offering__level__program__department",
        )
        .order_by("-offering__semester__session__start_date")
    )

    context = {
        "page_title":        "Teacher Dashboard",
        "current_session":   current_session,
        "current_semester":  current_semester,
        "responsibilities":  responsibilities,
        "is_hod":            is_hod,
        "is_dean":           is_dean,
        "my_allocations":    my_allocations,
        "allocation_count":  my_allocations.count(),
    }

    # ── HOD panel ────────────────────────────────────────────────────────────
    if is_hod:
        from academics.models import ResultSheet

        hod_departments = request.user.get_hod_departments()
        pending_sheets = (
            ResultSheet.objects
            .filter(
                department__in=hod_departments,
                status=ResultSheet.Status.SUBMITTED,
            )
            .select_related("department", "offering__course", "submitted_by")
            .order_by("-submitted_at")
        )
        context.update({
            "hod_departments":   hod_departments,
            "pending_sheets":    pending_sheets,
            "pending_count":     pending_sheets.count(),
        })

    return render(request, "teachers/dashboard.html", context)


@login_required
@admin_required
def admin_dashboard(request):
    from academics.models import AcademicSession, Semester

    current_session  = AcademicSession.get_current()
    current_semester = Semester.get_current()

    pending_users = EduProUser.objects.filter(is_active=False).order_by("-date_joined")
    recent_users  = EduProUser.objects.filter(is_active=True).order_by("-date_joined")[:8]

    # Portal: pending admission applications
    from portal.models import AdmissionApplication, AdmissionStatus
    pending_applications = AdmissionApplication.objects.filter(
        status=AdmissionStatus.PENDING
    ).order_by("-created_at")

    # Course registration requests for dashboard
    from students.models import CourseRegistrationRequest
    recent_registrations = (
        CourseRegistrationRequest.objects
        .select_related("student", "offering__course")
        .order_by("-created_at")[:10]
    )
    pending_reg_count = (
        CourseRegistrationRequest.objects
        .filter(status="pending")
        .count()
    )

    # Hostel applications
    from operations.models import HostelApplication
    pending_hostel_count = HostelApplication.objects.filter(
        status=HostelApplication.Status.PENDING
    ).count()

    context = {
        "page_title":             "Admin Dashboard",
        "current_session":        current_session,
        "current_semester":       current_semester,
        "pending_users":          pending_users,
        "pending_user_count":     pending_users.count(),
        "recent_users":           recent_users,
        "pending_applications":   pending_applications,
        "application_count":      pending_applications.count(),
        "recent_registrations":   recent_registrations,
        "pending_request_count":  pending_reg_count,
        "pending_registrations":  pending_reg_count,
        "pending_hostel_count":   pending_hostel_count,
    }

    # Optional analytics if available
    try:
        from analytics.services import get_platform_stats, get_department_enrolment_stats
        platform = get_platform_stats()
        dept_raw = get_department_enrolment_stats()
        max_students = max((d.get("student_count") or 0 for d in dept_raw), default=1) or 1
        context.update({
            "total_users":      platform["users"],
            "total_students":   platform["students"],
            "total_teachers":   platform["teachers"],
            "total_courses":    platform["courses"],
            "total_enrolments": platform["enrolments"],
            "dept_breakdown": [
                {
                    "name":  d["name"],
                    "count": d["student_count"],
                    "pct":   int((d["student_count"] or 0) / max_students * 100),
                }
                for d in dept_raw[:5]
            ],
        })
    except Exception:
        pass

    return render(request, "accounts/dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: USER APPROVAL
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def pending_users_view(request):
    """List all users awaiting approval (is_active=False)."""
    qs = (
        EduProUser.objects
        .filter(is_active=False)
        .select_related("profile")
        .order_by("-date_joined")
    )
    return render(request, "accounts/pending_users.html", {
        "page_title": "Pending Approvals",
        "pending_users": qs,
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def approve_user(request, pk):
    """Approve a pending user account."""
    user = get_object_or_404(EduProUser, pk=pk, is_active=False)
    user.is_active  = True
    user.approved_by = request.user
    user.approved_at = timezone.now()
    user.save(update_fields=["is_active", "approved_by", "approved_at"])
    messages.success(
        request,
        f"{user.get_full_name()} ({user.email}) has been approved and activated."
    )
    return redirect("accounts:pending_users")


@login_required
@admin_required
@require_http_methods(["POST"])
def reject_user(request, pk):
    """Permanently delete a rejected pending user account."""
    user = get_object_or_404(EduProUser, pk=pk, is_active=False)
    name = user.get_full_name()
    user.delete()
    messages.info(request, f"Account for {name} has been rejected and removed.")
    return redirect("accounts:pending_users")


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: STAFF ROLE MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def staff_roles_view(request, pk):
    """View and manage a user's StaffRole assignments."""
    from .forms import UserStaffRoleForm

    target_user = get_object_or_404(EduProUser, pk=pk)
    roles = UserStaffRole.objects.filter(user=target_user).select_related("department")

    form = UserStaffRoleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        role = form.save(commit=False)
        role.user       = target_user
        role.granted_by = request.user
        role.save()
        messages.success(
            request,
            f"Responsibility '{role.get_responsibility_display()}' granted to "
            f"{target_user.get_full_name()}."
        )
        return redirect("accounts:staff_roles", pk=pk)

    return render(request, "accounts/staff_roles.html", {
        "page_title":   f"Responsibilities: {target_user.get_full_name()}",
        "target_user":  target_user,
        "roles":        roles,
        "form":         form,
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def revoke_staff_role(request, role_pk):
    """Deactivate a UserStaffRole."""
    role = get_object_or_404(UserStaffRole, pk=role_pk)
    user_pk = role.user_id
    role.is_active = False
    role.save(update_fields=["is_active"])
    messages.info(request, f"Responsibility '{role.get_responsibility_display()}' revoked.")
    return redirect("accounts:staff_roles", pk=user_pk)


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request):
    user = request.user
    try:
        profile = user.profile
    except ObjectDoesNotExist:
        profile = UserProfile.objects.create(user=user)

    user_form    = UserInfoForm(request.POST or None, instance=user)
    profile_form = ProfileForm(request.POST or None, request.FILES or None, instance=profile)

    if request.method == "POST":
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, "Profile updated successfully.")
            return redirect("accounts:profile")
        messages.error(request, "Please correct the errors below.")

    return render(request, "accounts/profile.html", {
        "page_title":    "My Profile",
        "user_form":     user_form,
        "profile_form":  profile_form,
        "profile":       profile,
        "responsibilities": request.user.get_active_responsibilities(),
    })


@login_required
@require_http_methods(["GET", "POST"])
def change_password_view(request):
    form = PasswordChangeForm(user=request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Password updated successfully.")
        return redirect("accounts:profile")

    return render(request, "accounts/change_password.html", {
        "page_title": "Change Password",
        "form": form,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: USER MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def bulk_student_upload_view(request):
    from academics.models import Program, StudentProfile

    form = BulkStudentUploadForm(request.POST or None, request.FILES or None)
    results = None

    if request.method == "POST" and form.is_valid():
        rows = form.rows
        fieldnames = form.fieldnames
        has_program = "program_code" in fieldnames
        has_email = "email" in fieldnames

        created = []
        skipped = []
        errors = []
        auto_enrolled = []

        for i, row in enumerate(rows, start=2):
            first_name = row.get("first_name", "").strip()
            last_name  = row.get("last_name", "").strip()
            email      = row.get("email", "").strip().lower() if has_email else ""
            program_code = row.get("program_code", "").strip() if has_program else ""

            if not first_name or not last_name:
                errors.append(f"Row {i}: first_name and last_name are required.")
                continue

            try:
                if not email:
                    email = f"{first_name.lower()}.{last_name.lower()}.{i}@placeholder.edu"

                if EduProUser.objects.filter(email=email).exists():
                    skipped.append(f"Row {i}: {email} already exists.")
                    continue

                user = EduProUser(
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    role=Role.STUDENT,
                    is_active=True,
                    approved_by=request.user,
                    approved_at=timezone.now(),
                )
                user.set_default_password()
                user.save()

                program = None
                if program_code:
                    program = Program.objects.filter(code=program_code).first()

                prefix = f"{program_code if program_code else 'STU'}/{timezone.now().year}/"
                last_sp = StudentProfile.objects.filter(student_number__startswith=prefix).order_by('student_number').last()
                if last_sp:
                    try:
                        last_num = int(last_sp.student_number.rsplit('/', 1)[-1])
                        new_num = last_num + 1
                    except (ValueError, IndexError):
                        new_num = 1
                else:
                    new_num = 1

                from academics.models import Level
                profile = StudentProfile.objects.create(
                    student=user,
                    program=program,
                    student_number=f"{prefix}{new_num:04d}",
                    admission_date=timezone.now().date(),
                )
                if program:
                    first_level = program.get_starting_level()
                    if first_level:
                        profile.current_level = first_level
                        profile.save(update_fields=["current_level"])

                # ── Auto-enrol into matching course offerings ────────────────
                if profile.current_level and program:
                    from academics.models import CourseOffering, Enrolment, Semester
                    current_sem = Semester.get_current()
                    if current_sem:
                        offerings = CourseOffering.objects.filter(
                            is_active=True,
                            level_name=profile.current_level.name,
                            departments=program.department,
                            semester=current_sem,
                        )
                        enrolled = []
                        for off in offerings:
                            _, created_enr = Enrolment.objects.get_or_create(
                                student=user,
                                offering=off,
                                defaults={
                                    "status": Enrolment.EnrolmentStatus.ACTIVE,
                                    "is_active": True,
                                },
                            )
                            if created_enr:
                                enrolled.append(off.course.code)
                        if enrolled:
                            auto_enrolled.append(
                                f"{first_name} {last_name} → {', '.join(enrolled)}"
                            )

                created.append(f"Row {i}: {first_name} {last_name} — {email}")

            except Exception as e:
                errors.append(f"Row {i}: {first_name} {last_name} — {str(e)}")

        results = {
            "created": created,
            "skipped": skipped,
            "errors": errors,
            "total": len(created) + len(skipped) + len(errors),
            "auto_enrolled": auto_enrolled,
        }

        if created:
            messages.success(request, f"{len(created)} student(s) created successfully.")
        if auto_enrolled:
            messages.info(request, f"{len(auto_enrolled)} student(s) auto-enrolled into courses.")
        if errors:
            messages.warning(request, f"{len(errors)} row(s) had errors.")

    programs = Program.objects.filter(is_active=True).values_list("code", "name", "department__code")
    return render(request, "accounts/student_bulk_upload.html", {
        "page_title": "Bulk Student Upload",
        "form": form,
        "results": results,
        "programs": programs,
    })


@login_required
@admin_required
def user_list_view(request):
    qs = EduProUser.objects.select_related("profile").order_by("last_name")
    paginator = Paginator(qs, 25)
    page_obj  = paginator.get_page(request.GET.get("page"))

    return render(request, "accounts/user_list.html", {
        "page_title": "User Management",
        "page_obj":   page_obj,
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def toggle_user_active(request, pk):
    user = get_object_or_404(EduProUser, pk=pk)
    user.is_active = not user.is_active
    user.save(update_fields=["is_active"])
    label = "activated" if user.is_active else "deactivated"
    messages.success(request, f"{user.get_full_name()} ({user.email}) has been {label}.")
    return redirect("accounts:user_list")


@login_required
@admin_required
def user_detail_view(request, pk):
    target_user = get_object_or_404(EduProUser, pk=pk)
    profile, _  = UserProfile.objects.get_or_create(user=target_user)

    user_form    = UserInfoForm(request.POST or None, instance=target_user)
    profile_form = ProfileForm(request.POST or None, request.FILES or None, instance=profile)

    if request.method == "POST":
        if "reset_password" in request.POST:
            target_user.set_default_password()
            target_user.save(update_fields=["password"])
            messages.success(
                request,
                f"Password for {target_user.get_full_name()} has been reset to default (0-9)."
            )
            return redirect("accounts:user_detail", pk=pk)
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, f"{target_user.get_full_name()} updated successfully.")
            return redirect("accounts:user_detail", pk=pk)
        messages.error(request, "Please correct errors below.")

    from academics.models import StudentProfile as SP
    try:
        student_profile = target_user.academic_profile
    except SP.DoesNotExist:
        student_profile = None

    return render(request, "accounts/profile.html", {
        "page_title":      "Edit User",
        "target_user":     target_user,
        "user_form":       user_form,
        "profile_form":    profile_form,
        "profile":         profile,
        "student_profile": student_profile,
    })


@login_required
@admin_required
def admin_registration_requests(request):
    requests = (
        CourseRegistrationRequest.objects
        .select_related("student", "offering__course", "offering__semester")
        .order_by("-created_at")
    )
    paginator = Paginator(requests, 30)
    page_obj  = paginator.get_page(request.GET.get("page"))
    return render(request, "accounts/admin_registrations.html", {
        "page_title": "Course Registration Requests",
        "page_obj":   page_obj,
    })


@login_required
@admin_required
@require_POST
def approve_registration(request, pk):
    reg = get_object_or_404(CourseRegistrationRequest, pk=pk, status="pending")
    reg.status      = "approved"
    reg.reviewed_by = request.user
    reg.reviewed_at = timezone.now()
    reg.save()
    messages.success(
        request,
        f"Approved: {reg.student.get_full_name()} → {reg.offering.course.code}",
    )
    return redirect("accounts:admin_registrations")


@login_required
@admin_required
@require_POST
def reject_registration(request, pk):
    reg = get_object_or_404(CourseRegistrationRequest, pk=pk, status="pending")
    reg.status         = "rejected"
    reg.reviewed_by    = request.user
    reg.reviewed_at    = timezone.now()
    reg.rejection_note = request.POST.get("rejection_note", "No reason provided.")
    reg.save()
    messages.warning(request, f"Rejected: {reg.student.get_full_name()} → {reg.offering.course.code}")
    return redirect("accounts:admin_registrations")


@login_required
@admin_required
def download_sample_csv(request):
    from academics.models import Program

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="student_upload_sample.csv"'

    writer = csv.writer(response)

    programs = Program.objects.filter(is_active=True).select_related("department")
    if programs:
        writer.writerow([f"# Available programs: code (name) — dept"])
        for p in programs:
            writer.writerow([f"#   {p.code} ({p.name}) — {p.department.code}"])
        writer.writerow([])

    writer.writerow(["first_name", "last_name", "email", "program_code"])
    writer.writerow(["John", "Doe", "john.doe@example.com", programs[0].code if programs else "BSC-CS"])
    if len(programs) > 1:
        writer.writerow(["Jane", "Smith", "jane.smith@example.com", programs[1].code])
    writer.writerow(["Alice", "Johnson", "", ""])

    return response