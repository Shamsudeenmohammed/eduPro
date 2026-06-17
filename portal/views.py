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
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required

# Original forms (kept for original views)
from .forms import (
    AdmissionApplicationForm,
    AdmissionCycleForm,
    AdmissionForm,
    ApplicationRejectForm,
    ApplicationReviewForm,
    ApplicationStatusCheckForm,
    ContactForm,
    DocumentRequestForm,
)
from .models import (
    AdmissionApplication,
    AdmissionCycle,
    AdmissionStatus,
    ContactMessage,
    DocumentRequest,
    PublicAnnouncement,
    WebsitePage,
)


# ─────────────────────────────────────────────────────────────────────────────
# ORIGINAL VIEWS — preserved exactly, not modified
# ─────────────────────────────────────────────────────────────────────────────

def home(request):
    announcements = PublicAnnouncement.objects.filter(is_published=True)[:6]
    return render(request, "portal/home.html", {
        "page_title": "Welcome",
        "announcements": announcements,
    })


def about(request):
    page = WebsitePage.objects.filter(slug="about", is_published=True).first()
    return render(request, "portal/page.html", {"page": page, "page_title": "About Us"})


def programs_public(request):
    from academics.models import Program
    programs = Program.objects.select_related("department__faculty").filter(is_active=True)
    return render(request, "portal/programs.html", {
        "page_title": "Programs",
        "programs": programs,
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


# ─────────────────────────────────────────────────────────────────────────────
# NEW: PUBLIC CONTROLLED-ONBOARDING VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def application_form(request):
    """
    New controlled public application form (replaces the legacy admission_apply
    for new intake cycles).  Requires an active AdmissionCycle.
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
    )

    if request.method == "POST" and form.is_valid():
        application = form.save(commit=False)
        application.cycle = active_cycle
        application.save()

        # Create user account so the applicant can log in immediately
        from accounts.models import EduProUser
        user = EduProUser.objects.create_user(
            email=application.email,
            password=form.cleaned_data["password1"],
            first_name=application.first_name,
            last_name=application.last_name,
            role="student",
            is_active=True,  # active from start so they can check status
        )
        application.user = user
        application.save(update_fields=["user"])

        return redirect("portal:application_confirmed", ref=application.reference_number)

    return render(request, "portal/application_form.html", {
        "page_title": "Apply for Admission",
        "form": form,
        "cycle": active_cycle,
    })


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
@_admissions_required
def admissions_dashboard(request):
    active_cycle = AdmissionCycle.get_active()
    cycle_qs = (
        AdmissionApplication.objects.filter(cycle=active_cycle)
        if active_cycle else AdmissionApplication.objects.none()
    )

    status_counts = {
        "pending":   cycle_qs.filter(status=AdmissionStatus.PENDING).count(),
        "reviewing": cycle_qs.filter(status=AdmissionStatus.REVIEWING).count(),
        "approved":  cycle_qs.filter(status=AdmissionStatus.APPROVED).count(),
        "rejected":  cycle_qs.filter(status=AdmissionStatus.REJECTED).count(),
        "withdrawn": cycle_qs.filter(status=AdmissionStatus.WITHDRAWN).count(),
        "total":     cycle_qs.count(),
    }

    recent_pending = (
        cycle_qs
        .filter(status=AdmissionStatus.PENDING)
        .select_related("program_applied__department")
        .order_by("-created_at")[:8]
    )

    return render(request, "portal/admissions_dashboard.html", {
        "page_title":     "Admissions Dashboard",
        "active_cycle":   active_cycle,
        "status_counts":  status_counts,
        "recent_pending": recent_pending,
    })


@login_required
@_admissions_required
def application_list(request):
    qs = (
        AdmissionApplication.objects
        .select_related("cycle", "program_applied__department", "reviewed_by", "approved_by")
        .order_by("-created_at")
    )

    status_filter = request.GET.get("status", "")
    cycle_filter  = request.GET.get("cycle", "")
    search_query  = request.GET.get("q", "").strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if cycle_filter:
        qs = qs.filter(cycle__id=cycle_filter)
    if search_query:
        from django.db.models import Q
        qs = qs.filter(
            Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(reference_number__icontains=search_query)
        )

    paginator = Paginator(qs, 20)

    return render(request, "portal/application_list.html", {
        "page_title":     "Applications",
        "page_obj":       paginator.get_page(request.GET.get("page")),
        "status_choices": AdmissionStatus.choices,
        "cycles":         AdmissionCycle.objects.order_by("-start_date"),
        "status_filter":  status_filter,
        "cycle_filter":   cycle_filter,
        "search_query":   search_query,
    })


@login_required
@_admissions_required
def application_detail(request, pk):
    application = get_object_or_404(
        AdmissionApplication.objects.select_related(
            "cycle", "program_applied__department",
            "reviewed_by", "approved_by", "rejected_by", "user",
        ),
        pk=pk,
    )
    doc_requests = application.document_requests.select_related("requested_by").order_by("-requested_at")

    return render(request, "portal/application_detail.html", {
        "page_title":      f"Application — {application.get_full_name()}",
        "application":     application,
        "doc_requests":    doc_requests,
        "AdmissionStatus": AdmissionStatus,
    })


@login_required
@_admissions_required
@require_http_methods(["POST"])
def application_review(request, pk):
    application = get_object_or_404(AdmissionApplication, pk=pk)
    try:
        application.mark_reviewing(request.user)
        messages.info(request, f"Application {application.reference_number} is now Under Review.")
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("portal:application_detail", pk=pk)


@login_required
@_admissions_required
@require_http_methods(["GET", "POST"])
def application_approve(request, pk):
    application = get_object_or_404(
        AdmissionApplication.objects.select_related("program_applied", "cycle"),
        pk=pk,
    )

    if application.status not in (AdmissionStatus.PENDING, AdmissionStatus.REVIEWING):
        messages.warning(
            request,
            f"This application is already {application.get_status_display()} "
            "and cannot be approved again."
        )
        return redirect("portal:application_detail", pk=pk)

    if request.method == "POST":
        review_notes = request.POST.get("review_notes", "")
        try:
            new_user = application.approve(actor=request.user)
            if review_notes:
                application.review_notes = review_notes
                application.save(update_fields=["review_notes"])
            messages.success(
                request,
                f"✅ Application approved! {new_user.get_full_name()} can now log in "
                f"at /accounts/login/ with the password they chose during application."
            )
        except ValidationError as e:
            messages.error(request, str(e.message))
        return redirect("portal:application_detail", pk=pk)

    return render(request, "portal/application_approve_confirm.html", {
        "page_title":  "Approve Application",
        "application": application,
    })


@login_required
@_admissions_required
@require_http_methods(["GET", "POST"])
def application_reject(request, pk):
    application = get_object_or_404(AdmissionApplication, pk=pk)

    if application.status in (AdmissionStatus.APPROVED, AdmissionStatus.WITHDRAWN):
        messages.warning(
            request,
            f"Cannot reject an already {application.get_status_display()} application."
        )
        return redirect("portal:application_detail", pk=pk)

    form = ApplicationRejectForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            application.reject(
                actor=request.user,
                reason=form.cleaned_data["rejection_reason"],
            )
            messages.info(request, f"Application {application.reference_number} rejected.")
        except ValidationError as e:
            messages.error(request, str(e.message))
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


# ─────────────────────────────────────────────────────────────────────────────
# NEW: ADMISSION CYCLE MANAGEMENT (admin only)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def cycle_list(request):
    cycles = AdmissionCycle.objects.order_by("-start_date")
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
