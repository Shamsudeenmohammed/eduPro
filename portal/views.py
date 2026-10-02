"""
portal/views.py

Merged view file — all original public views are preserved exactly as they
were, and the new admissions-workflow views are added below them.

Original views (UNCHANGED):
    home, about, programs_public, contact, admission_apply,
    news_list, news_detail, admin_contacts, admin_admissions

New views (added by refactor):
    application_form, application_confirmed, application_status,
    application_withdraw, admissions_dashboard, application_list,
    application_detail, application_review, application_approve,
    application_reject, document_request_create, document_request_fulfill,
    cycle_list, cycle_create, cycle_edit
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required, anonymous_required

# Original forms (kept for original views)
from .forms import (
    AdmissionApplicationForm,
    AdmissionCycleForm,
    AdmissionForm,
    ApplicationCorrectionForm,
    ApplicationDocumentUploadForm,
    ApplicationPaymentRecordForm,
    ApplicationRejectForm,
    ApplicationReviewForm,
    ApplicationStatusCheckForm,
    ApplicationSubmitForm,
    AdmissionDecisionForm,
    AdmissionOfferForm,
    ContactForm,
    DocumentRequestForm,
    DocumentVerificationForm,
    OfferResponseForm,
)
from .models import (
    AdmissionApplication,
    AdmissionCycle,
    AdmissionStatus,
    ApplicationType,
    ApplicationTypeCode,
    ContactMessage,
    CycleStatus,
    DocumentRequest,
    PublicAnnouncement,
    WebsitePage,
)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC FRONTEND HELPERS
# Read-only helpers shared by the public pages. They never change data; they
# only gather what the templates render so no page has to hard-code academic
# content.
# ─────────────────────────────────────────────────────────────────────────────

def _public_events(limit=None, upcoming_only=False):
    """Public calendar events, newest first. The operations app is optional."""
    try:
        from operations.models import CalendarEvent
    except Exception:  # noqa: BLE001
        return []
    qs = CalendarEvent.objects.filter(is_public=True)
    if upcoming_only:
        from django.utils import timezone
        qs = qs.filter(start_date__gte=timezone.localdate())
    qs = qs.order_by("start_date", "created_at")
    return list(qs[:limit]) if limit else list(qs)


def _public_open_cycles():
    """Every admission cycle currently accepting applications."""
    today = timezone.now().date()
    return list(
        AdmissionCycle.objects.filter(
            is_active=True,
            status=CycleStatus.OPEN,
            start_date__lte=today,
            end_date__gte=today,
        ).order_by("-start_date")
    )


def _public_stats():
    """Headline numbers for the public site, counted from the real records."""
    from academics.models import Department, Faculty, Program
    return {
        "programmes": Program.objects.filter(is_active=True).count(),
        "faculties": Faculty.objects.filter(is_active=True).count(),
        "departments": Department.objects.filter(is_active=True).count(),
    }


def _public_faculties():
    """Faculties with live department and programme counts for the same school."""
    from django.db.models import Count, Prefetch, Q
    from academics.models import Department, Faculty, Program
    return list(
        Faculty.objects.annotate(
            department_count=Count("departments", filter=Q(departments__is_active=True), distinct=True),
            programme_count=Count("departments__programs", filter=Q(departments__programs__is_active=True), distinct=True),
        )
        .prefetch_related(
            Prefetch("departments", queryset=Department.objects.filter(is_active=True).select_related("hod", "faculty")),
            Prefetch("departments__programs", queryset=Program.objects.filter(is_active=True).select_related("department__faculty")),
        )
        .filter(is_active=True)
    )


# Public "why choose us" and "student life" copy lives here so it can be
# edited in one place. Each item is (icon, title, body); the icon is a name
# resolved by the template against templates/portal/partials/icon.html.
PUBLIC_HIGHLIGHTS = (
    ("award", "Quality Education",
     "Programmes designed to meet the standards of a modern tertiary institution, with assessment built around course work and examinations."),
    ("users", "Experienced Faculty",
     "Teaching delivered by academic staff organised into faculties and departments, with department heads accountable for their programmes."),
    ("laptop", "Digital Learning",
     "Every student works from a single portal for course materials, assignments, attendance, results and announcements."),
    ("book", "Practical Learning",
     "Coursework and assessment designed so graduates can apply what they learn, not only recall it."),
    ("heart", "Student Support",
     "Academic progress, fees, accommodation requests and support tickets are all handled inside the same student portal."),
    ("shield", "Secure Records",
     "Applications, results and identity records are protected by role-based access and a full audit trail of every change."),
)

PUBLIC_STUDENT_LIFE = (
    ("book", "Learning Resources",
     "Course notes, lecture materials, assignments and discussion forums for every registered course."),
    ("calendar", "Academic Planning",
     "The academic calendar, your timetable and course registration in one place, so you always know what comes next."),
    ("home", "Accommodation",
     "Hostel applications, allocations and payments are handled online through the same portal you use for your studies."),
    ("chart", "Academic Progress",
     "Follow your attendance, results and credits as they are recorded, with transcripts available when you need them."),
    ("chat", "Support & Feedback",
     "Raise a support ticket or send feedback to the institution and follow the response to resolution."),
    ("card", "Fees & Payments",
     "Fee statements, records of payment and receipts stay available to you for the whole session."),
)


# ─────────────────────────────────────────────────────────────────────────────
# ORIGINAL VIEWS — preserved exactly, not modified
# ─────────────────────────────────────────────────────────────────────────────

def home(request):
    from academics.models import Program
    announcements = list(PublicAnnouncement.objects.filter(is_published=True)[:6])
    programs = list(
        Program.objects.select_related("department__faculty")
        .filter(is_active=True)[:6]
    )
    return render(request, "portal/home.html", {
        "page_title": "Welcome",
        "announcements": announcements,
        "featured_announcement": announcements[0] if announcements else None,
        "programs": programs,
        "faculties": _public_faculties(),
        "open_cycles": _public_open_cycles(),
        "events": _public_events(limit=3, upcoming_only=True),
        "stats": _public_stats(),
        "highlights": PUBLIC_HIGHLIGHTS,
        "student_life": PUBLIC_STUDENT_LIFE,
        "application_types": ApplicationType.objects.filter(is_active=True).order_by("order", "label")[:4],
        "home_sections": {
            slug: WebsitePage.objects.filter(slug=slug, is_published=True).first()
            for slug in ("mission", "vision", "values", "testimonials")
        },
    })


def about(request):
    page = WebsitePage.objects.filter(slug="about", is_published=True).first()
    # Optional CMS pages so an administrator can publish the standard
    # institutional sections without a code change. Anything not published is
    # simply omitted rather than filled with invented copy.
    sections = {
        slug: WebsitePage.objects.filter(slug=slug, is_published=True).first()
        for slug in ("history", "mission", "vision", "values", "leadership", "facilities")
    }
    return render(request, "portal/page.html", {
        "page": page,
        "page_title": "About Us",
        "sections": sections,
        "faculties": _public_faculties(),
        "stats": _public_stats(),
    })


def programs_public(request):
    from academics.models import Department, Faculty, Program, ProgramType
    programs = Program.objects.select_related("department__faculty").filter(is_active=True)

    query = (request.GET.get("q") or "").strip()
    program_type = (request.GET.get("type") or "").strip()
    faculty = (request.GET.get("faculty") or "").strip()
    department = (request.GET.get("department") or "").strip()

    if query:
        programs = programs.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(description__icontains=query)
            | Q(department__name__icontains=query)
            | Q(department__faculty__name__icontains=query)
        )
    if program_type:
        programs = programs.filter(program_type=program_type)
    if faculty:
        programs = programs.filter(department__faculty__code=faculty)
    if department:
        programs = programs.filter(department__code=department)

    departments = Department.objects.filter(is_active=True).select_related("faculty")

    return render(request, "portal/programs.html", {
        "page_title": "Programmes",
        "programs": programs,
        "program_types": ProgramType.choices,
        "faculties": Faculty.objects.filter(is_active=True),
        "departments": departments,
        "departments_by_faculty": departments.order_by("faculty__name", "name"),
        "selected": {
            "q": query, "type": program_type,
            "faculty": faculty, "department": department,
        },
    })


def program_detail(request, pk):
    """Public programme profile built from the existing academic records."""
    from academics.models import Program
    program = get_object_or_404(
        Program.objects.select_related("department__faculty"),
        pk=pk, is_active=True,
    )
    siblings = (
        Program.objects.filter(department=program.department, is_active=True)
        .exclude(pk=program.pk)
        .select_related("department__faculty")
    )
    return render(request, "portal/program_detail.html", {
        "page_title": program.name,
        "program": program,
        "siblings": siblings,
        "levels": program.levels.filter(is_active=True).order_by("order", "name"),
        "department_courses": program.department.courses.filter(is_active=True)
        .order_by("code")[:12],
        "application_types": program.available_application_types(),
        "open_cycles": _public_open_cycles(),
    })


def academic_structure(request):
    """Faculties -> Departments -> Programmes, from the academic models."""
    faculties = _public_faculties()
    for faculty in faculties:
        faculty.live_departments = list(faculty.departments.all())
        for dept in faculty.live_departments:
            dept.live_programs = list(dept.programs.all())
    return render(request, "portal/academic.html", {
        "page_title": "Faculties & Departments",
        "faculties": faculties,
        "stats": _public_stats(),
    })


def admissions_landing(request):
    """Guidance page for prospective applicants. The application itself is
    still started on the existing ``portal:apply`` route."""
    from academics.models import Program
    from .models import ApplicationRequirement
    types = list(ApplicationType.objects.filter(is_active=True).order_by("order", "label"))
    requirements = {}
    if types:
        for req in (ApplicationRequirement.objects
                    .filter(application_type__in=types, is_required=True)
                    .select_related("application_type")
                    .order_by("order", "label")):
            requirements.setdefault(req.application_type_id, []).append(req)
    type_cards = [
        {"type": atype, "requirements": requirements.get(atype.pk, [])}
        for atype in types
    ]
    return render(request, "portal/admissions.html", {
        "page_title": "Admissions",
        "open_cycles": _public_open_cycles(),
        "all_cycles": list(AdmissionCycle.objects.order_by("-start_date")[:4]),
        "application_types": types,
        "type_cards": type_cards,
        "programmes": Program.objects.filter(is_active=True)
        .select_related("department__faculty")[:8],
        "stats": _public_stats(),
    })


def events_list(request):
    """Public events from the academic calendar (is_public entries only)."""
    from django.utils import timezone
    today = timezone.localdate()
    events = _public_events()
    return render(request, "portal/events.html", {
        "page_title": "Events",
        "upcoming_events": [e for e in events if e.start_date >= today],
        "past_events": [e for e in events if e.start_date < today][:12],
    })


def student_life(request):
    """Student life overview. Every capability listed here is served by an
    existing eduPro module; the page adds no new promises."""
    return render(request, "portal/student_life.html", {
        "page_title": "Student Life",
        "stats": _public_stats(),
        "events": _public_events(limit=3, upcoming_only=True),
    })



@require_http_methods(["GET", "POST"])
def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Thank you! Your message has been received.")
        return redirect("portal:contact")
    return render(request, "portal/contact.html", {"form": form, "page_title": "Contact Us"})


@require_http_methods(["GET", "POST"])
def admission_apply(request):
    """Legacy simple application form — kept for backward compatibility."""
    form = AdmissionForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        active_cycle = AdmissionCycle.get_active()
        if not active_cycle:
            messages.error(request, "Admissions are currently closed. No active cycle found.")
        else:
            app = form.save(commit=False)
            app.cycle = active_cycle
            app.save()
            messages.success(request, "Application submitted successfully! We will contact you soon.")
        return redirect("portal:admission_apply")
    return render(request, "portal/admission.html", {"form": form, "page_title": "Apply for Admission"})


def news_list(request):
    items = PublicAnnouncement.objects.filter(is_published=True)
    paginator = Paginator(items, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "portal/news_list.html", {"page_obj": page_obj, "page_title": "News"})


def news_detail(request, pk):
    item = get_object_or_404(PublicAnnouncement, pk=pk, is_published=True)
    return render(request, "portal/news_detail.html", {"item": item, "page_title": item.title})


@login_required
@admin_required
def admin_contacts(request):
    qs = ContactMessage.objects.order_by("-created_at")
    paginator = Paginator(qs, 25)
    return render(request, "portal/admin_contacts.html", {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "page_title": "Contact Messages",
    })


@login_required
@admin_required
def admin_admissions(request):
    qs = AdmissionApplication.objects.order_by("-created_at")
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    paginator = Paginator(qs, 25)
    return render(request, "portal/admin_admissions.html", {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "page_title": "Admission Applications",
    })


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPER
# ─────────────────────────────────────────────────────────────────────────────

from accounts.models import StaffResponsibility  # noqa: E402


def _is_admissions_staff(user):
    """True if user is admin/superuser OR holds ADMISSIONS_OFFICER responsibility."""
    return (
        user.is_authenticated
        and (
            user.is_admin
            or user.is_superuser
            or user.has_responsibility(StaffResponsibility.ADMISSIONS_OFFICER)
        )
    )


def _admissions_required(view_func):
    """Admits ADMIN role and ADMISSIONS_OFFICER responsibility."""
    from functools import wraps

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")
        if _is_admissions_staff(request.user):
            return view_func(request, *args, **kwargs)
        messages.error(request, "Admissions officer or administrator access required.")
        return redirect(request.user.get_dashboard_url())

    return wrapper


def _admissions_read_required(view_func):
    """
    Also admits HODs, who may READ applications for their own departments.

    Every view using this must scope its queryset with
    ``permissions.scope_applications``; the department restriction is enforced
    per object, not by the decorator. Mutating actions (decision, offer,
    convert, transitions) deliberately use ``_admissions_required`` instead, so
    an HOD can see and verify documents for their department but cannot record
    an admission decision.
    """
    from functools import wraps

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        from .permissions import is_admissions_staff, is_hod

        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")
        if is_admissions_staff(request.user) or is_hod(request.user):
            return view_func(request, *args, **kwargs)
        messages.error(
            request,
            "Admissions officer, HOD or administrator access required.",
        )
        return redirect(request.user.get_dashboard_url())

    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# NEW: PUBLIC CONTROLLED-ONBOARDING VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def _application_prefill(request):
    """
    Pre-select the programme and/or application type when the applicant
    arrived from a deep link such as
    ``/portal/apply/?program=<pk>&type=undergraduate``.

    Convenience only: the form still validates both server-side, and unknown
    or inactive values are ignored so a stale link can never break the form.
    """
    if request.method != "GET":
        return None
    initial = {}
    raw_program = request.GET.get("program")
    if raw_program and str(raw_program).isdigit():
        from academics.models import Program
        if Program.objects.filter(pk=int(raw_program), is_active=True).exists():
            initial["program_applied"] = int(raw_program)
    raw_type = (request.GET.get("type") or "").strip()
    if raw_type in dict(ApplicationTypeCode.choices):
        initial["application_type"] = raw_type
    return initial or None


def application_form(request):
    """
    Public application form.

    Preserves the original behaviour (an account is created so the applicant
    can track progress) and adds draft support: posting with ``action=draft``
    stores the application as a DRAFT so it can be finished later.

    The account created here is an APPLICANT account, not a student: it has no
    ``academic_profile``, therefore no student number and no dashboard. A
    student record is only created by
    :class:`portal.services.StudentConversionService`, after an admission
    decision and an accepted offer (requirement 7).
    """
    if request.user.is_authenticated and request.user.is_student:
        messages.info(request, "You already have a student account.")
        return redirect(request.user.get_dashboard_url())

    active_cycle = AdmissionCycle.get_active()
    if not active_cycle or not active_cycle.is_open:
        return render(request, "portal/admissions_closed.html", {
            "page_title": "Admissions Closed",
            "cycle": active_cycle,
        })

    form = AdmissionApplicationForm(
        cycle=active_cycle,
        data=request.POST or None,
        files=request.FILES or None,
        initial=_application_prefill(request),
    )

    save_as_draft = request.method == "POST" and request.POST.get("action") == "draft"

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]

        # One live application per person per cycle. Checked on the server so a
        # crafted POST cannot slip past the form.
        duplicate = AdmissionApplication.objects.filter(
            cycle=active_cycle, email__iexact=email,
        ).exclude(status=AdmissionStatus.WITHDRAWN).exists()
        if duplicate:
            form.add_error(
                "email",
                "You already have an application for this cycle. Sign in to track "
                "or update it, or contact admissions if you believe this is a mistake.",
            )
            return render(request, "portal/application_form.html", {
                "page_title": "Apply for Admission",
                "form": form,
                "cycle": active_cycle,
            })

        # Atomic: the application and its applicant account must both exist or
        # neither does. Previously a duplicate email raised IntegrityError after
        # the application row was already committed, orphaning it.
        with transaction.atomic():
            application = form.save(commit=False)
            application.cycle = active_cycle
            application.status = (
                AdmissionStatus.DRAFT if save_as_draft else AdmissionStatus.SUBMITTED
            )
            application.submitted_at = None if save_as_draft else timezone.now()
            application.save()

            # Create the applicant account so they can log in and track progress.
            # An existing account with this address is reused rather than
            # duplicated; a second application with the same address was
            # already rejected above, so this is a returning applicant.
            from accounts.models import EduProUser
            user = EduProUser.objects.filter(email__iexact=email).first()
            if user is None:
                user = EduProUser.objects.create_user(
                    email=email,
                    password=form.cleaned_data["password1"],
                    first_name=application.first_name,
                    last_name=application.last_name,
                    role="student",
                    is_active=True,  # active so they can check status / finish a draft
                )
            else:
                # Keep the name they gave us current on the account.
                changed = []
                if application.first_name and user.first_name != application.first_name:
                    user.first_name = application.first_name
                    changed.append("first_name")
                if application.last_name and user.last_name != application.last_name:
                    user.last_name = application.last_name
                    changed.append("last_name")
                if changed:
                    user.save(update_fields=changed)

            application.user = user
            application.save(update_fields=["user"])

            from .services import AuditService
            from .models import AuditAction
            AuditService.record(
                application,
                AuditAction.CREATED if save_as_draft else AuditAction.SUBMITTED,
                actor=user,
                remark=("Application started as a draft." if save_as_draft
                        else "Application submitted via the public form."),
                visible_to_applicant=True,
            )
            _notify_new_application(application, save_as_draft)

        if save_as_draft:
            messages.success(
                request,
                "Your application has been saved as a draft. Sign in to continue "
                "where you left off.",
            )
            return redirect("accounts:login")
        return redirect("portal:application_confirmed", ref=application.reference_number)

    return render(request, "portal/application_form.html", {
        "page_title": "Apply for Admission",
        "form": form,
        "cycle": active_cycle,
    })


def _notify_new_application(application, is_draft):
    """Best-effort acknowledgement; never blocks the applicant."""
    try:
        from .notify import _send
        _send(
            "APPLICATION_DRAFT_SAVED" if is_draft else "APPLICATION_SUBMITTED",
            application,
            {
                "event": "draft" if is_draft else "created",
                "action": (
                    "Sign in to finish and submit it."
                    if is_draft else
                    "Pay the application fee to move your application to review."
                ),
            },
        )
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("New application notification failed")



def application_confirmed(request, ref):
    application = get_object_or_404(AdmissionApplication, reference_number=ref)
    return render(request, "portal/application_confirmed.html", {
        "page_title": "Application Submitted",
        "application": application,
        "ref": ref,
    })


@require_http_methods(["GET", "POST"])
def application_status(request):
    form = ApplicationStatusCheckForm(request.POST or None)
    application = None

    if request.method == "POST" and form.is_valid():
        ref   = form.cleaned_data["reference_number"]
        email = form.cleaned_data["email"]
        try:
            application = AdmissionApplication.objects.get(
                reference_number__iexact=ref,
                email__iexact=email,
            )
        except AdmissionApplication.DoesNotExist:
            messages.error(
                request,
                "No application found with that reference number and email. "
                "Please double-check and try again."
            )

    return render(request, "portal/application_status.html", {
        "page_title": "Check Application Status",
        "form": form,
        "application": application,
    })


@require_http_methods(["POST"])
def application_withdraw(request, ref):
    application = get_object_or_404(AdmissionApplication, reference_number=ref)
    try:
        application.withdraw()
        messages.info(request, f"Application {ref} has been withdrawn.")
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("portal:application_status")


# ─────────────────────────────────────────────────────────────────────────────
# NEW: STAFF ADMISSIONS DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@_admissions_read_required
def admissions_dashboard(request):
    """
    Admissions dashboard (requirement 20).

    Extended — not replaced — with the full statistics set, filterable by
    cycle / type / faculty / department / programme / status / payment status.
    Every count is derived from the SAME scoped queryset used by the list view,
    so the numbers can never disagree with the rows underneath them.
    """
    from .models import ApplicationType, CycleStatus
    from .permissions import scope_applications
    from .services import ReportingService

    qs = scope_applications(request.user).select_related(
        "cycle", "program_applied__department__faculty",
    )

    # ── Filters ─────────────────────────────────────────────────────────────
    cycle_filter = request.GET.get("cycle", "")
    type_filter = request.GET.get("type", "")
    faculty_filter = request.GET.get("faculty", "")
    department_filter = request.GET.get("department", "")
    program_filter = request.GET.get("program", "")
    status_filter = request.GET.get("status", "")
    payment_filter = request.GET.get("payment", "")
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")

    filtered = qs
    if cycle_filter:
        filtered = filtered.filter(cycle_id=cycle_filter)
    if type_filter:
        filtered = filtered.filter(application_type=type_filter)
    if faculty_filter:
        filtered = filtered.filter(
            program_applied__department__faculty_id=faculty_filter
        )
    if department_filter:
        filtered = filtered.filter(
            program_applied__department_id=department_filter
        )
    if program_filter:
        filtered = filtered.filter(program_applied_id=program_filter)
    if status_filter:
        filtered = filtered.filter(status=status_filter)
    if date_from:
        filtered = filtered.filter(created_at__date__gte=date_from)
    if date_to:
        filtered = filtered.filter(created_at__date__lte=date_to)

    # Payment status is a derived property (only *successful* payments count),
    # so it is filtered in Python. Normalising to a list here keeps one code
    # path for both the count and the sample rows below.
    if payment_filter in ("paid", "unpaid"):
        candidates = filtered.select_related("cycle").prefetch_related("payments")
        ids = [
            app.pk for app in candidates
            if app.is_fee_paid == (payment_filter == "paid")
        ]
        filtered = AdmissionApplication.objects.filter(pk__in=ids)

    rows = list(filtered.order_by("-created_at")[:8])

    from academics.models import Department, Faculty, Program

    context = {
        "page_title": "Admissions Dashboard",
        "active_cycle": AdmissionCycle.get_active(),
        "cycles": AdmissionCycle.objects.order_by("-start_date"),
        "status_counts": ReportingService.statistics(filtered),
        "filtered_count": filtered.count(),
        "recent_pending": rows[:8],
        # Filter options
        "application_types": ApplicationType.objects.filter(is_active=True),
        "faculties": Faculty.objects.filter(is_active=True),
        "departments": Department.objects.filter(is_active=True),
        "programs": Program.objects.filter(is_active=True).select_related("department"),
        "status_choices": AdmissionStatus.choices,
        "cycle_choices": AdmissionCycle.objects.order_by("-start_date"),
        "cycle_statuses": CycleStatus.choices,
        # Current filter state
        "cycle_filter": cycle_filter,
        "type_filter": type_filter,
        "faculty_filter": faculty_filter,
        "department_filter": department_filter,
        "program_filter": program_filter,
        "status_filter": status_filter,
        "payment_filter": payment_filter,
        "date_from": date_from,
        "date_to": date_to,
        "has_filters": any([
            cycle_filter, type_filter, faculty_filter, department_filter,
            program_filter, status_filter, payment_filter, date_from, date_to,
        ]),
    }
    return render(request, "portal/admissions_dashboard.html", context)


@login_required
@_admissions_read_required
def application_list(request):
    """
    Application list.

    Scoped through ``portal.permissions.scope_applications``, which is what
    stops an HOD seeing other departments' applications (scenario 7) and stops
    any non-staff user browsing applications at all.
    """
    from .models import ApplicationType
    from .permissions import scope_applications
    from academics.models import Department, Faculty, Program

    qs = (
        scope_applications(request.user)
        .select_related("cycle", "program_applied__department__faculty",
                        "reviewed_by", "approved_by")
        .order_by("-created_at")
    )

    status_filter = request.GET.get("status", "")
    cycle_filter  = request.GET.get("cycle", "")
    type_filter   = request.GET.get("type", "")
    decision_filter = request.GET.get("decision", "")
    search_query  = request.GET.get("q", "").strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if cycle_filter:
        qs = qs.filter(cycle_id=cycle_filter)
    if type_filter:
        qs = qs.filter(application_type=type_filter)
    if decision_filter:
        qs = qs.filter(decision=decision_filter)
    if search_query:
        qs = qs.filter(
            Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(reference_number__icontains=search_query)
            | Q(program_applied__code__icontains=search_query)
            | Q(program_applied__name__icontains=search_query)
        )

    paginator = Paginator(qs, 20)

    from .models import AdmissionDecision

    return render(request, "portal/application_list.html", {
        "page_title":     "Applications",
        "page_obj":       paginator.get_page(request.GET.get("page")),
        "status_choices": AdmissionStatus.choices,
        "decision_choices": AdmissionDecision.choices,
        "application_types": ApplicationType.objects.filter(is_active=True),
        "cycles":         AdmissionCycle.objects.order_by("-start_date"),
        "status_filter":  status_filter,
        "cycle_filter":   cycle_filter,
        "type_filter":    type_filter,
        "decision_filter": decision_filter,
        "search_query":   search_query,
        "total":          qs.count(),
    })


@login_required
@_admissions_read_required
def application_detail(request, pk):
    """
    Application detail with the staff checklist, document verification, offers,
    audit timeline and every permitted next action (requirement 13).
    """
    from .permissions import visible_application
    from .services import AuditService, ChecklistService, OfferService
    from .workflow import ApplicationWorkflow

    application = visible_application(request.user, pk)
    doc_requests = application.document_requests.select_related(
        "requested_by"
    ).order_by("-requested_at")

    offer = OfferService.active_offer(application)
    conversion_blocker = ""
    from .services import StudentConversionService
    if application.is_admitted:
        conversion_blocker = StudentConversionService.preflight(application) or ""

    # The workflow returns bare status codes (domain logic stays free of
    # presentation); the template needs readable labels, so pair them here.
    staff_targets = [
        {
            "value": target,
            "label": AdmissionStatus(target).label
            if target in AdmissionStatus.values else target.replace("_", " ").title(),
        }
        for target in ApplicationWorkflow.staff_targets(application)
    ]

    return render(request, "portal/application_detail.html", {
        "page_title":      f"Application — {application.get_full_name()}",
        "application":     application,
        "doc_requests":    doc_requests,
        "AdmissionStatus": AdmissionStatus,
        # New engine context
        "checklist":       ChecklistService.build(application),
        "documents":       application.uploaded_documents.select_related(
            "verified_by").order_by("document_type", "-version"),
        "offers":          application.offers.select_related("issued_by").order_by("-issue_date"),
        "payments":        application.payments.select_related("verified_by").order_by("-created_at"),
        "audit_logs":      AuditService.staff_timeline(application)[:50],
        "active_offer":    offer,
        "staff_targets":   staff_targets,
        "can_decide":      _can_decide(request.user, application),
        "conversion_blocker": conversion_blocker,
        "decision_form":   AdmissionDecisionForm(),
        "offer_form":      AdmissionOfferForm(),
        "correction_form": ApplicationCorrectionForm(),
    })


def _can_decide(user, application):
    from .permissions import can_decide
    return can_decide(user, application)



@login_required
@_admissions_required
@require_http_methods(["POST"])
def application_review(request, pk):
    """Start (or restart) review of an application via the workflow layer."""
    from .services import DecisionService  # noqa: F401  (kept for symmetry)
    from .workflow import ApplicationWorkflow

    application = get_object_or_404(AdmissionApplication, pk=pk)
    try:
        ApplicationWorkflow.mark_reviewing(application, request.user)
        messages.info(
            request,
            f"Application {application.reference_number} is now Under Review.",
        )
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))
    return redirect("portal:application_detail", pk=pk)


@login_required
@_admissions_required
@require_http_methods(["GET", "POST"])
def application_approve(request, pk):
    """
    Record an ADMITTED decision and issue an admission offer.

    Behaviour change (deliberate, requirement 7 & 8): this no longer creates a
    student record. The old implementation provisioned an active account *and*
    a StudentProfile with a student number at approval time, which meant an
    applicant became a student without ever receiving or accepting an offer.

    Now: decision = Accepted, then an offer is issued. The student record is
    created only after the applicant accepts, via
    :class:`portal.services.StudentConversionService`.
    """
    from .services import DecisionService, OfferService
    from .models import AdmissionDecision

    application = get_object_or_404(
        AdmissionApplication.objects.select_related("program_applied", "cycle"),
        pk=pk,
    )

    allowed, reason = DecisionService.can_make(
        application, AdmissionDecision.ACCEPTED
    )
    if not allowed:
        messages.warning(request, reason or "This application cannot be accepted.")
        return redirect("portal:application_detail", pk=pk)

    if request.method == "POST":
        decision_notes = request.POST.get("review_notes", "").strip()
        try:
            with transaction.atomic():
                DecisionService.record(
                    application, AdmissionDecision.ACCEPTED,
                    actor=request.user, notes=decision_notes,
                )
                offer = OfferService.issue(application, actor=request.user)
            messages.success(
                request,
                f"✅ {application.get_full_name()} has been accepted. "
                f"Offer {offer.offer_number} was issued and is valid until "
                f"{offer.expiry_date}. The applicant must accept it before a "
                f"student record is created.",
            )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))
        return redirect("portal:application_detail", pk=pk)

    return render(request, "portal/application_approve_confirm.html", {
        "page_title":  "Accept Application & Issue Offer",
        "application": application,
    })


@login_required
@_admissions_required
@require_http_methods(["GET", "POST"])
def application_reject(request, pk):
    """Record a REJECTED decision (kept separate from the workflow status)."""
    from .services import DecisionService
    from .models import AdmissionDecision

    application = get_object_or_404(AdmissionApplication, pk=pk)

    if application.is_terminal:
        messages.warning(
            request,
            f"Cannot reject an application that is already "
            f"{application.get_status_display()}.",
        )
        return redirect("portal:application_detail", pk=pk)

    form = ApplicationRejectForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            DecisionService.record(
                application, AdmissionDecision.REJECTED,
                actor=request.user, reason=form.cleaned_data["rejection_reason"],
            )
            application.rejection_reason = form.cleaned_data["rejection_reason"]
            application.rejected_by = request.user
            application.rejected_at = timezone.now()
            application.save(update_fields=[
                "rejection_reason", "rejected_by", "rejected_at", "updated_at",
            ])
            messages.info(
                request,
                f"Application {application.reference_number} rejected. "
                "No student record was created.",
            )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))
        return redirect("portal:application_detail", pk=pk)

    return render(request, "portal/application_reject_form.html", {
        "page_title":  "Reject Application",
        "application": application,
        "form":        form,
    })



# ─────────────────────────────────────────────────────────────────────────────
# NEW: DOCUMENT REQUEST VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@_admissions_required
@require_http_methods(["GET", "POST"])
def document_request_create(request, application_pk):
    application = get_object_or_404(AdmissionApplication, pk=application_pk)
    form = DocumentRequestForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        doc_req = form.save(commit=False)
        doc_req.application  = application
        doc_req.requested_by = request.user
        doc_req.save()
        messages.success(request, f"Document request '{doc_req.document_name}' created.")
        return redirect("portal:application_detail", pk=application_pk)

    return render(request, "portal/document_request_form.html", {
        "page_title":  "Request Document",
        "application": application,
        "form":        form,
    })


@login_required
@_admissions_required
@require_http_methods(["POST"])
def document_request_fulfill(request, pk):
    doc_req = get_object_or_404(DocumentRequest, pk=pk)
    doc_req.mark_fulfilled()
    messages.success(request, f"Document request '{doc_req.document_name}' fulfilled.")
    return redirect("portal:application_detail", pk=doc_req.application_id)


@login_required
@require_http_methods(["POST"])
def applicant_upload_document(request, pk):
    """Applicant-facing: upload a file to fulfill a document request."""
    doc_req = get_object_or_404(
        DocumentRequest.objects.select_related("application"),
        pk=pk,
    )
    # Ensure this document belongs to the current user's application
    if doc_req.application.user_id != request.user.pk:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    if doc_req.status != DocumentRequest.RequestStatus.PENDING:
        messages.warning(request, "This document request has already been fulfilled or waived.")
        return redirect("accounts:student_pending")

    uploaded = request.FILES.get("uploaded_file")
    if uploaded:
        doc_req.uploaded_file = uploaded
        doc_req.mark_fulfilled()
        doc_req.save(update_fields=["uploaded_file", "status", "fulfilled_at"])
        messages.success(request, f"'{doc_req.document_name}' uploaded successfully. Thank you!")
    else:
        messages.error(request, "Please select a file to upload.")
    return redirect("accounts:student_pending")


# ─────────────────────────────────────────────────────────────────────────────
# NEW: ADMISSION CYCLE MANAGEMENT (admin only)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def cycle_list(request):
    # Each row renders its accepted types, so prefetch them rather than
    # running two queries per cycle.
    cycles = AdmissionCycle.objects.select_related("application_type").prefetch_related(
        "application_type_links__application_type"
    ).order_by("-start_date")
    return render(request, "portal/cycle_list.html", {
        "page_title": "Admission Cycles",
        "cycles":     cycles,
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def cycle_create(request):
    form = AdmissionCycleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Admission cycle created.")
        return redirect("portal:cycle_list")
    return render(request, "portal/cycle_form.html", {
        "page_title": "New Admission Cycle",
        "form":       form,
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def cycle_edit(request, pk):
    cycle = get_object_or_404(AdmissionCycle, pk=pk)
    form  = AdmissionCycleForm(request.POST or None, instance=cycle)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Admission cycle updated.")
        return redirect("portal:cycle_list")
    return render(request, "portal/cycle_form.html", {
        "page_title": "Edit Admission Cycle",
        "form":       form,
        "cycle":      cycle,
    })


# ─────────────────────────────────────────────────────────────────────────────
# PORTAL LOGIN — email + password for applicants
# ─────────────────────────────────────────────────────────────────────────────

from django.contrib.auth import login as auth_login  # noqa: E402


@anonymous_required()
@require_http_methods(["GET", "POST"])
def portal_login(request):
    from django.contrib.auth.forms import AuthenticationForm
    from accounts.models import EduProUser

    class _ApplicantAuthForm(AuthenticationForm):
        def clean(self):
            username = self.cleaned_data.get("username")
            password = self.cleaned_data.get("password")
            if username and password:
                try:
                    user = EduProUser.objects.get(email=username)
                except EduProUser.DoesNotExist:
                    raise self.get_invalid_login_error()
                if not user.check_password(password) or not user.is_active:
                    raise self.get_invalid_login_error()
                self.user_cache = user
            return self.cleaned_data

    form = _ApplicantAuthForm(request=request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        auth_login(request, user)
        messages.info(request, f"Welcome, {user.get_short_name()}!")
        return redirect("accounts:student_pending")
    return render(request, "portal/login.html", {
        "form": form,
        "page_title": "Applicant Sign In",
    })


# ── Letter downloads (applicant-facing) ────────────────────────────────────

from .utils import render_admission_letter, render_application_letter  # noqa: E402



def application_letter_pdf(request, ref):
    """Download the application letter as PDF (applicant only)."""
    application = get_object_or_404(
        AdmissionApplication, reference_number__iexact=ref,
    )
    if application.user_id != request.user.pk:
        raise PermissionDenied
    return render_application_letter(application)



def admission_letter_pdf(request, ref):
    """
    Download the admission letter (applicant only).

    Extended: the letter is now available once an OFFER has been issued, not
    only after conversion to a student — which is when an applicant actually
    needs it. A student number is shown once conversion has happened.
    """
    from .services import OfferService
    from .models import AdmissionDecision, OfferStatus

    application = get_object_or_404(
        AdmissionApplication, reference_number__iexact=ref,
    )
    if application.user_id != request.user.pk:
        raise PermissionDenied

    has_offer = application.offers.exclude(
        status__in=[OfferStatus.REVOKED]
    ).exists()
    if application.decision not in (
        AdmissionDecision.ACCEPTED, AdmissionDecision.CONDITIONALLY_ACCEPTED,
    ) and not has_offer:
        from django.http import HttpResponseNotFound
        return HttpResponseNotFound(
            "An admission letter is only available once an offer has been issued."
        )

    from academics.models import StudentProfile
    profile = StudentProfile.all_objects.filter(
        student_id=application.user_id
    ).first()
    student_id = profile.student_number if profile else "Pending registration"

    return render_admission_letter(application, student_id=student_id)


# ═════════════════════════════════════════════════════════════════════════════
# APPLICATIONS & ADMISSIONS ENGINE — APPLICANT PORTAL
#
# Every view below is ownership-scoped through
# ``portal.permissions.assert_is_owner``: an applicant may only ever reach
# their own application (requirement 22 / scenario 12). None of them mutate
# workflow status directly — they go through portal.services / portal.workflow.
# ═════════════════════════════════════════════════════════════════════════════

def _owned_application(request, pk):
    """Fetch an application, 404-ing unless the requester owns it."""
    from .permissions import assert_is_owner
    application = get_object_or_404(
        AdmissionApplication.objects.select_related(
            "cycle", "program_applied__department", "user",
        ),
        pk=pk,
    )
    if not assert_is_owner(request.user, application):
        raise PermissionDenied
    return application


@login_required
@require_http_methods(["GET"])
def applicant_dashboard(request):
    """
    Applicant dashboard (requirement 21).

    Shows every application this account owns with its number, type, cycle,
    programme, workflow status, payment status, completion percentage, missing
    requirements, admission decision and offer status — plus a plain-language
    statement of the action currently required.
    """
    from .services import ChecklistService, OfferService
    from .workflow import ApplicationWorkflow

    applications = (
        AdmissionApplication.objects.filter(user=request.user)
        .select_related("cycle", "program_applied__department__faculty")
        .prefetch_related("payments", "offers", "uploaded_documents")
        .order_by("-created_at")
    )

    cards = []
    for app in applications:
        checklist = ChecklistService.build(app)
        offer = OfferService.active_offer(app)
        cards.append({
            "application": app,
            "checklist": checklist,
            "offer": offer,
            "next_action": ApplicationWorkflow.next_action_for_applicant(app),
            "editable": ApplicationWorkflow.is_applicant_editable(app),
            "fee_paid": app.is_fee_paid,
        })

    open_cycles = [
        c for c in AdmissionCycle.objects.filter(is_active=True)
        if c.is_open
    ]

    return render(request, "portal/applicant_dashboard.html", {
        "page_title": "My Applications",
        "cards": cards,
        "open_cycles": open_cycles,
    })


@login_required
@require_http_methods(["GET", "POST"])
def applicant_application_edit(request, pk):
    """
    Edit a draft / send-back-for-correction application (requirement 3, 22).

    Editability is enforced on the server: once submitted, the form is not even
    bound with data, so a crafted POST cannot modify a locked application.
    """
    from .forms import ApplicationDraftForm
    from .services import AuditService
    from .models import AuditAction
    from .workflow import ApplicationWorkflow

    application = _owned_application(request, pk)

    if not ApplicationWorkflow.is_applicant_editable(application):
        messages.warning(
            request,
            "This application can no longer be edited. Contact admissions if "
            "something needs to change.",
        )
        return redirect("portal:applicant_dashboard")

    form = ApplicationDraftForm(
        instance=application,
        application_type=application.application_type,
        cycle=application.cycle,
    )

    if request.method == "POST":
        form = ApplicationDraftForm(
            data=request.POST,
            instance=application,
            application_type=application.application_type,
            cycle=application.cycle,
        )
        if form.is_valid():
            application = form.save()
            AuditService.record(
                application, AuditAction.UPDATED, actor=request.user,
                remark="Applicant updated their application.",
                visible_to_applicant=True,
            )
            messages.success(request, "Your changes have been saved.")
            return redirect("portal:applicant_dashboard")

    checklist = application.checklist()
    return render(request, "portal/application_edit.html", {
        "page_title": f"Edit application {application.reference_number}",
        "application": application,
        "form": form,
        "checklist": checklist,
    })


@login_required
@require_http_methods(["GET", "POST"])
def applicant_application_submit(request, pk):
    """
    Submit a draft, or resubmit after a correction was requested.

    Submission is BLOCKED while any required item is outstanding, and the
    missing items are listed for the applicant (scenario 4).
    """
    from .services import ChecklistService
    from .workflow import ApplicationWorkflow

    application = _owned_application(request, pk)
    current = ApplicationWorkflow.normalize(application.status)

    if current not in (AdmissionStatus.DRAFT, AdmissionStatus.NEEDS_CORRECTION):
        messages.info(request, "This application has already been submitted.")
        return redirect("portal:applicant_dashboard")

    blocking = ChecklistService.blocking_items(application)
    if blocking:
        messages.error(
            request,
            "Your application is not complete yet. Please supply: "
            + ", ".join(item["label"] for item in blocking),
        )
        return redirect("portal:applicant_application_edit", pk=pk)

    form = ApplicationSubmitForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            ApplicationWorkflow.transition(
                application, AdmissionStatus.SUBMITTED,
                actor=request.user, as_applicant=True,
                remark="Submitted by applicant.",
            )
            messages.success(
                request,
                f"Application {application.reference_number} submitted successfully.",
            )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("portal:applicant_dashboard")

    return render(request, "portal/application_submit.html", {
        "page_title": f"Submit {application.reference_number}",
        "application": application,
        "form": form,
        "checklist": ChecklistService.build(application),
    })


@login_required
@require_http_methods(["GET", "POST"])
def applicant_application_withdraw(request, pk):
    """Applicant withdraws their own application."""
    from .workflow import ApplicationWorkflow

    application = _owned_application(request, pk)
    try:
        ApplicationWorkflow.withdraw(application, actor=request.user)
        messages.info(request, f"Application {application.reference_number} withdrawn.")
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    return redirect("portal:applicant_dashboard")


@login_required
@require_http_methods(["GET"])
def applicant_checklist(request, pk):
    """The applicant's own checklist with completion progress."""
    from .services import ChecklistService

    application = _owned_application(request, pk)
    return render(request, "portal/application_checklist.html", {
        "page_title": f"Checklist — {application.reference_number}",
        "application": application,
        "checklist": ChecklistService.build(application),
    })


@login_required
@require_http_methods(["GET", "POST"])
def applicant_document_upload(request, pk):
    """
    Upload a supporting document against the applicant's own application.

    File type and size are validated server-side (requirement 22). Uploaded
    only while the application is still editable, or always for a DRAFT.
    """
    from .services import DocumentService
    from .workflow import ApplicationWorkflow

    application = _owned_application(request, pk)
    form = ApplicationDocumentUploadForm(request.POST or None, request.FILES or None)

    if not ApplicationWorkflow.is_applicant_editable(application) \
            and not application.uploaded_documents.exists():
        messages.warning(
            request,
            "Documents cannot be added after submission unless admissions "
            "asks you for a replacement.",
        )
        return redirect("portal:applicant_dashboard")

    if request.method == "POST" and form.is_valid():
        try:
            document = DocumentService.upload(
                application,
                document_type=form.cleaned_data["document_type"],
                uploaded=form.cleaned_data["file"],
                uploaded_by=request.user,
            )
            messages.success(
                request,
                f"{document.get_document_type_display()} uploaded and awaiting verification.",
            )
            return redirect("portal:applicant_checklist", pk=pk)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))

    return render(request, "portal/document_upload.html", {
        "page_title": f"Upload document — {application.reference_number}",
        "application": application,
        "form": form,
    })


@login_required
@require_http_methods(["GET"])
def document_download(request, pk):
    """
    Serve an uploaded document.

    Authorisation: the applicant who owns the application, or staff who are
    allowed to see the application. This is the *only* supported way to read an
    uploaded file, so a leaked URL still cannot expose another applicant's
    document (requirement 22 / scenario 12).
    """
    from .models import ApplicationDocument
    from .permissions import scope_applications

    document = get_object_or_404(
        ApplicationDocument.objects.select_related("application"),
        pk=pk,
    )
    if not scope_applications(request.user).filter(
        pk=document.application_id
    ).exists():
        raise PermissionDenied

    try:
        handle = document.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404("That file is no longer available.")
    filename = document.file.name.rsplit("/", 1)[-1]
    return FileResponse(handle, as_attachment=True, filename=filename)


@login_required
@require_http_methods(["GET", "POST"])
def applicant_offer_detail(request, application_pk, offer_pk):
    """
    Show an offer and let the applicant accept or decline it (requirement 8).

    This is the only route to becoming a student, and it never creates one —
    accepting merely records the response; staff then run the conversion.
    """
    from .services import OfferService

    application = _owned_application(request, application_pk)
    offer = get_object_or_404(
        application.offers.all(), pk=offer_pk,
    )

    form = OfferResponseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            if form.cleaned_data["response"] == OfferResponseForm.ACCEPT:
                OfferService.accept(offer, actor=request.user)
                messages.success(
                    request,
                    "Thank you — your acceptance has been recorded. Our team will "
                    "complete your registration and send you your student number.",
                )
            else:
                OfferService.decline(offer, actor=request.user)
                messages.info(request, "Your offer has been declined.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("portal:applicant_dashboard")

    return render(request, "portal/offer_detail.html", {
        "page_title": f"Offer {offer.offer_number}",
        "application": application,
        "offer": offer,
        "form": form,
    })


@login_required
@require_http_methods(["GET", "POST"])
def applicant_payment_start(request, pk):
    """
    Start (or resume) payment of the application fee.

    Creating the payment record does NOT mark the fee as paid — only
    :meth:`portal.services.PaymentService.confirm` /
    :meth:`~portal.services.PaymentService.verify_with_provider` do that
    (requirement 15).

    The row is created only on POST: rendering this page must be a safe,
    repeatable read, otherwise every page refresh would mint a new payment
    reference.
    """
    from .services import PaymentService

    application = _owned_application(request, pk)

    if application.is_fee_paid:
        messages.info(request, "Your application fee has already been confirmed.")
        return redirect("portal:applicant_dashboard")

    if not application.cycle.requires_payment:
        messages.info(request, "This admission cycle has no application fee.")
        return redirect("portal:applicant_dashboard")

    payment = None
    if request.method == "POST":
        payment, error = PaymentService.initiate(application)
        if error:
            messages.info(request, error)
            return redirect("portal:applicant_dashboard")
        if payment.status == "successful":
            messages.info(request, "Your application fee has already been confirmed.")
            return redirect("portal:applicant_dashboard")
        messages.success(
            request,
            f"Payment reference {payment.payment_reference} created. "
            "Complete the payment to continue your application.",
        )
        return redirect("portal:applicant_payment_start", pk=pk)
    else:
        # Read-only: show the in-flight payment, if there is one.
        payment = PaymentService.pending_payment(application)

    return render(request, "portal/application_payment.html", {
        "page_title": f"Application fee — {application.reference_number}",
        "application": application,
        "payment": payment,
        "amount_due": application.payment_balance,
    })


# ═════════════════════════════════════════════════════════════════════════════
# APPLICATIONS & ADMISSIONS ENGINE — STAFF ACTIONS
#
# Each of these is a thin shell: authorise → delegate to a service → report.
# None of them assign ``application.status`` directly (requirement 5).
# ═════════════════════════════════════════════════════════════════════════════

@login_required
@_admissions_required
@require_http_methods(["POST"])
def application_transition(request, pk):
    """
    Move an application to another workflow stage (requirement 5).

    This is the ONLY staff endpoint that changes workflow status, and it goes
    through ``ApplicationWorkflow.transition`` so the transition graph, the
    payment gate, the audit trail and notifications all apply. A crafted POST
    naming an arbitrary status is rejected by the graph, not trusted.
    """
    from .permissions import visible_application
    from .workflow import ApplicationWorkflow

    application = visible_application(request.user, pk)
    target = request.POST.get("to_status", "").strip()
    remark = request.POST.get("remark", "").strip()
    bypass_payment = request.POST.get("bypass_payment_gate") == "1"

    if not target:
        messages.error(request, "No target stage was supplied.")
        return redirect("portal:application_detail", pk=pk)

    try:
        ApplicationWorkflow.transition(
            application, target, actor=request.user, remark=remark,
            bypass_payment_gate=bypass_payment and request.user.is_admin,
        )
        messages.success(
            request,
            f"Application {application.reference_number} moved to "
            f"{application.get_status_display()}.",
        )
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    return redirect("portal:application_detail", pk=pk)


@login_required
@_admissions_required
@require_http_methods(["GET", "POST"])
def application_decision(request, pk):
    """
    Record the admission decision (requirement 6).

    Separated from workflow status: the decision is stored on
    ``application.decision`` with its own actor, timestamp and notes.
    """
    from .permissions import visible_application
    from .services import ChecklistService, DecisionService

    application = visible_application(request.user, pk)

    allowed, reason = DecisionService.can_make(application, "accepted")
    if not allowed:
        messages.warning(request, reason or "You cannot decide on this application.")
        return redirect("portal:application_detail", pk=pk)

    form = AdmissionDecisionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        decision = form.cleaned_data["decision"]
        try:
            DecisionService.record(
                application, decision, actor=request.user,
                notes=form.cleaned_data.get("notes", ""),
            )
            messages.success(
                request,
                f"Decision recorded: {application.get_decision_display()}. "
                + (
                    "Issue an offer to proceed."
                    if decision in DecisionService.OFFERABLE
                    else ""
                ),
            )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("portal:application_detail", pk=pk)

    return render(request, "portal/application_decision_form.html", {
        "page_title": f"Admission decision — {application.reference_number}",
        "application": application,
        "form": form,
        "checklist": ChecklistService.build(application),
    })


@login_required
@_admissions_required
@require_http_methods(["GET", "POST"])
def application_offer_issue(request, pk):
    """Issue an admission offer (requirement 8)."""
    from .models import AdmissionDecision
    from .permissions import visible_application
    from .services import DecisionService, OfferService

    application = visible_application(request.user, pk)

    if application.decision not in DecisionService.OFFERABLE:
        messages.warning(
            request,
            "Record an accepted admission decision before issuing an offer.",
        )
        return redirect("portal:application_detail", pk=pk)

    form = AdmissionOfferForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            offer = OfferService.issue(
                application,
                actor=request.user,
                expiry_date=form.cleaned_data.get("expiry_date") or None,
                conditions=form.cleaned_data.get("conditions", ""),
                admission_type=form.cleaned_data.get("admission_type") or "full_time",
            )
            messages.success(
                request,
                f"Offer {offer.offer_number} issued to {application.get_full_name()}, "
                f"valid until {offer.expiry_date}.",
            )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("portal:application_detail", pk=pk)

    return render(request, "portal/application_offer_form.html", {
        "page_title": f"Issue offer — {application.reference_number}",
        "application": application,
        "form": form,
    })


@login_required
@_admissions_required
@require_http_methods(["POST"])
def application_convert(request, pk):
    """
    Convert an admitted applicant into a student (requirement 9).

    Refuses unless there is an accepted decision AND an accepted offer, and is
    idempotent so a double submission cannot create a second student, account
    or enrolment (scenario 10).
    """
    from .permissions import visible_application
    from .services import StudentConversionService

    application = visible_application(request.user, pk)

    try:
        profile, created = StudentConversionService.convert(
            application, actor=request.user,
        )
        if created:
            messages.success(
                request,
                f"{application.get_full_name()} is now a student. "
                f"Student number: {profile.student_number}.",
            )
        else:
            messages.info(
                request,
                f"{application.get_full_name()} was already converted "
                f"(student number {profile.student_number}). No duplicate was created.",
            )
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    return redirect("portal:application_detail", pk=pk)


@login_required
@_admissions_required
@require_http_methods(["GET", "POST"])
def application_correction_request(request, pk):
    """Ask the applicant to correct their application (requirement 18 event)."""
    from .permissions import visible_application
    from .services import DocumentService
    from .workflow import ApplicationWorkflow

    application = visible_application(request.user, pk)
    form = ApplicationCorrectionForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            ApplicationWorkflow.request_correction(
                application, request.user, form.cleaned_data["reason"],
            )
            messages.success(
                request,
                "Correction requested. The applicant has been notified.",
            )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("portal:application_detail", pk=pk)

    return render(request, "portal/application_correction_form.html", {
        "page_title": f"Request correction — {application.reference_number}",
        "application": application,
        "form": form,
    })


@login_required
@_admissions_required
@require_http_methods(["POST"])
def document_verify(request, pk):
    """
    Verify / reject / request replacement for an uploaded document (req 12).

    A verified document can never be silently replaced: the applicant's next
    upload supersedes it and revokes the earlier sign-off.
    """
    from .models import ApplicationDocument
    from .services import DocumentService

    document = get_object_or_404(
        ApplicationDocument.objects.select_related("application"),
        pk=pk,
    )
    from .permissions import assert_can_manage_documents
    if not assert_can_manage_documents(request.user, document.application):
        raise PermissionDenied

    form = DocumentVerificationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        action = form.cleaned_data["action"]
        reason = form.cleaned_data.get("reason", "")
        try:
            if action == DocumentVerificationForm.VERIFY:
                DocumentService.verify(document, request.user)
                messages.success(request, "Document verified.")
            elif action == DocumentVerificationForm.REJECT:
                DocumentService.reject(document, request.user, reason)
                messages.warning(request, "Document rejected and the applicant notified.")
            else:
                DocumentService.request_replacement(document, request.user, reason)
                messages.info(request, "Replacement requested.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("portal:application_detail", pk=document.application_id)

    return render(request, "portal/document_verify_form.html", {
        "page_title": "Verify document",
        "document": document,
        "form": form,
    })


# ── Finance: application-fee payments (requirement 15/16) ───────────────────

@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def application_payment_record(request, pk):
    """
    Finance records and confirms an application-fee payment.

    Admin-only, mirroring how the rest of the ``finance`` app is gated, and
    deliberately without any power over admission decisions (requirement 16).
    """
    from .models import ApplicationPayment
    from .services import PaymentService

    application = get_object_or_404(
        AdmissionApplication.objects.select_related("cycle"),
        pk=pk,
    )
    form = ApplicationPaymentRecordForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        payment = form.save(commit=False)
        payment.application = application
        payment.status = "pending"
        payment.payment_reference = ApplicationPayment.generate_reference(application)
        payment.save()
        try:
            PaymentService.confirm(
                payment, actor=request.user,
                transaction_id=form.cleaned_data.get("transaction_id", ""),
                note=form.cleaned_data.get("notes", ""),
            )
            messages.success(
                request,
                f"Payment {payment.payment_reference} confirmed. The application has "
                "been moved forward automatically.",
            )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("portal:application_detail", pk=pk)

    return render(request, "portal/application_payment_record.html", {
        "page_title": f"Record payment — {application.reference_number}",
        "application": application,
        "form": form,
        "amount_due": application.payment_balance,
    })


@login_required
@admin_required
@require_http_methods(["POST"])
def application_payment_verify(request, pk):
    """Verify a pending payment against the configured provider (Paystack)."""
    from .models import ApplicationPayment
    from .services import PaymentService

    payment = get_object_or_404(ApplicationPayment, pk=pk)
    try:
        PaymentService.verify_with_provider(payment, actor=request.user)
        messages.success(
            request, f"Payment {payment.payment_reference} verified with the provider."
        )
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    return redirect("portal:application_detail", pk=payment.application_id)


@login_required
@admin_required
@require_http_methods(["GET"])
def application_payment_list(request):
    """Finance report: application payments, filterable by status."""
    from .models import ApplicationPayment
    from .models import ApplicationPaymentStatus

    qs = (
        ApplicationPayment.objects
        .select_related("application", "application__program_applied", "verified_by")
        .order_by("-created_at")
    )
    status_filter = request.GET.get("status", "")
    if status_filter:
        qs = qs.filter(status=status_filter)

    from django.core.paginator import Paginator
    return render(request, "portal/application_payment_list.html", {
        "page_title": "Application Payments",
        "page_obj": Paginator(qs, 25).get_page(request.GET.get("page")),
        "status_choices": ApplicationPaymentStatus.choices,
        "status_filter": status_filter,
        "total": qs.count(),
    })


# ── Applicant: correction response ──────────────────────────────────────────

@login_required
@require_http_methods(["POST"])
def applicant_correction_response(request, pk):
    """
    Applicant acknowledges a correction request.

    Simply routes the application to RESUBMITTED once the requested changes are
    saved. The workflow graph is the authority: if a transition is not
    permitted from the current stage the applicant is told why.
    """
    from .workflow import ApplicationWorkflow

    application = _owned_application(request, pk)
    try:
        ApplicationWorkflow.transition(
            application, AdmissionStatus.RESUBMITTED,
            actor=request.user, as_applicant=True,
            remark="Corrections supplied by applicant.",
        )
        messages.success(
            request, "Thank you — your corrections have been received."
        )
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    return redirect("portal:applicant_dashboard")

