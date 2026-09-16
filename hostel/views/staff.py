"""Staff hostel management views — dashboard, applications, allocations,
beds, transfers, maintenance, incidents, settings, accommodation CRUD,
residents, check-in/out lists, outstanding balances, reports."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from hostel import services
from hostel.forms import (
    AllocationForm,
    AmenityForm,
    BedMaintenanceForm,
    CheckInForm,
    HostelBlockForm,
    HostelFeeConfigForm,
    HostelFloorForm,
    HostelForm,
    HostelIncidentForm,
    HostelPolicyForm,
    HostelRoomForm,
    TransferReviewForm,
)
from hostel.models import (
    Amenity,
    BedMaintenance,
    Hostel,
    HostelAllocation,
    HostelApplication,
    HostelBed,
    HostelBlock,
    HostelFeeConfig,
    HostelFloor,
    HostelIncident,
    HostelPolicy,
    HostelRoom,
    HostelTransfer,
    IncidentStatus,
)
from hostel.selectors.allocations import allocations_qs
from hostel.selectors.reports import (
    application_funnel,
    incident_summary,
    occupancy_report,
    transfer_summary,
)

from academics.models import AcademicSession, Semester

from .common import _base_for_user, _page_obj, _service_message, hostel_staff_required

logger = logging.getLogger("eduPro")


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def dashboard(request):
    counts = services.HostelAvailabilityService.bed_counts()
    state_total = lambda key: sum(v.get(key, 0) for v in counts.values())

    context = {
        "hostels": Hostel.objects.filter(is_active=True).order_by("name"),
        "total_beds": sum(sum(v.values()) for v in counts.values()),
        "available": state_total(HostelBed.BedState.AVAILABLE),
        "reserved": state_total(HostelBed.BedState.RESERVED),
        "occupied": state_total(HostelBed.BedState.OCCUPIED),
        "maintenance": state_total(HostelBed.BedState.MAINTENANCE),
        "blocked": state_total(HostelBed.BedState.BLOCKED),
        "pending_applications": HostelApplication.objects.filter(
            status__in=(
                HostelApplication.Status.PENDING,
                HostelApplication.Status.UNDER_REVIEW,
            )
        ).count(),
        "active_allocations": HostelAllocation.objects.filter(
            status=HostelAllocation.Status.ACTIVE
        ).count(),
        "reserved_allocations": HostelAllocation.objects.filter(
            status=HostelAllocation.Status.RESERVED
        ).count(),
        "pending_transfers": HostelTransfer.objects.filter(
            status=HostelTransfer.Status.PENDING
        ).count(),
        "open_incidents": HostelIncident.objects.filter(
            status__in=(IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)
        ).count(),
        "recent_applications": (
            HostelApplication.objects.select_related("student", "room__hostel")
            .order_by("-created_at")[:8]
        ),
        "recent_allocations": (
            HostelAllocation.objects.select_related(
                "student", "bed__room__hostel", "session"
            ).order_by("-created_at")[:8]
        ),
        "recent_transfers": (
            HostelTransfer.objects.select_related(
                "student", "old_bed__room__hostel", "new_bed__room__hostel"
            ).order_by("-requested_at")[:8]
        ),
        "page_title": "Hostel Management",
    }
    return render(request, "hostel/hostel_dashboard.html", context)


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATIONS
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def applications(request):
    status_filter = request.GET.get("status", "")
    qs = HostelApplication.objects.select_related(
        "student", "room__hostel", "session"
    ).order_by("-created_at")
    if status_filter in dict(HostelApplication.Status.choices):
        qs = qs.filter(status=status_filter)
    return render(request, "hostel/hostel_applications_admin.html", {
        "page_obj": _page_obj(qs, request),
        "status_filter": status_filter,
        "status_choices": HostelApplication.Status.choices,
        "page_title": "Hostel Applications",
    })


@login_required
@hostel_staff_required
@require_POST
def application_approve(request, pk):
    application = get_object_or_404(HostelApplication, pk=pk)
    try:
        services.HostelApplicationService.review_application(
            application,
            actor=request.user,
            approve=True,
            remark=request.POST.get("admin_remark", ""),
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        messages.success(
            request,
            f"Application from {application.student.get_full_name()} approved.",
        )
    return redirect("hostel:applications")


@login_required
@hostel_staff_required
@require_POST
def application_reject(request, pk):
    application = get_object_or_404(HostelApplication, pk=pk)
    try:
        services.HostelApplicationService.review_application(
            application,
            actor=request.user,
            approve=False,
            remark=request.POST.get("admin_remark", ""),
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        messages.warning(
            request,
            f"Application from {application.student.get_full_name()} rejected.",
        )
    return redirect("hostel:applications")


# ═══════════════════════════════════════════════════════════════════════════
# ALLOCATIONS / CHECK-IN / CHECK-OUT
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def allocations(request):
    status_filter = request.GET.get("status", "")
    return render(request, "hostel/hostel_allocations.html", {
        "page_obj": _page_obj(allocations_qs(status=status_filter), request),
        "status_filter": status_filter,
        "status_choices": HostelAllocation.Status.choices,
        "page_title": "Hostel Allocations",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def allocation_create(request):
    form = AllocationForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            expiry = form.cleaned_data.get("reservation_expires_at")
            if expiry is None:
                expiry = timezone.now() + services.HostelPolicyService.reservation_expiry()
            try:
                allocation = services.HostelAllocationService.allocate_bed(
                    student=form.cleaned_data["student"],
                    bed=form.cleaned_data["bed"],
                    session=form.cleaned_data["session"],
                    actor=request.user,
                    application=form.cleaned_data.get("application"),
                    note=form.cleaned_data.get("note", ""),
                    reservation_expires_at=expiry,
                )
            except services.HostelServiceError as exc:
                _service_message(request, exc)
            else:
                messages.success(
                    request,
                    f"Bed allocated to {allocation.student.get_full_name()} "
                    f"({allocation.bed}). Reservation valid until {expiry:%Y-%m-%d %H:%M}.",
                )
                return redirect("hostel:allocations")
        messages.error(request, "Please correct the form errors.")

    return render(request, "hostel/hostel_allocation_form.html", {
        "form": form,
        "page_title": "Allocate Bed",
    })


@login_required
@hostel_staff_required
@require_POST
def allocation_cancel(request, pk):
    allocation = get_object_or_404(HostelAllocation, pk=pk)
    reason = request.POST.get("cancel_reason", "") or "Cancelled by hostel staff."
    try:
        services.HostelAllocationService.cancel_allocation(
            allocation,
            actor=request.user,
            reason=reason,
            application=getattr(allocation, "application", None),
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        messages.success(request, "Allocation cancelled and bed released.")
    return redirect("hostel:allocations")


@login_required
@hostel_staff_required
@require_POST
def check_in(request, pk):
    allocation = get_object_or_404(HostelAllocation, pk=pk)
    try:
        services.HostelCheckInService.check_in(
            allocation=allocation,
            actor=request.user,
            note=request.POST.get("check_in_note", ""),
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        messages.success(
            request,
            f"{allocation.student.get_full_name()} checked in to {allocation.hierarchy_display}.",
        )
    return redirect("hostel:allocations")


@login_required
@hostel_staff_required
@require_POST
def check_out(request, pk):
    allocation = get_object_or_404(HostelAllocation, pk=pk)
    try:
        services.HostelCheckoutService.checkout(
            allocation=allocation,
            actor=request.user,
            note=request.POST.get("check_out_note", ""),
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        messages.success(
            request,
            f"{allocation.student.get_full_name()} checked out. Bed released.",
        )
    return redirect("hostel:allocations")


@login_required
@hostel_staff_required
def residents(request):
    """Active + reserved allocations — current residents."""
    qs = HostelAllocation.objects.filter(
        status__in=(HostelAllocation.Status.ACTIVE, HostelAllocation.Status.RESERVED),
    ).select_related(
        "student", "bed__room__hostel", "bed__room__floor__block", "session"
    ).order_by("-created_at")
    return render(request, "hostel/hostel_residents.html", {
        "page_obj": _page_obj(qs, request),
        "page_title": "Current Residents",
    })


@login_required
@hostel_staff_required
def checkins(request):
    """Allocations with a check-in date — recent first."""
    qs = HostelAllocation.objects.exclude(
        check_in__isnull=True
    ).select_related(
        "student", "bed__room__hostel", "session"
    ).order_by("-checked_in_at")
    return render(request, "hostel/hostel_checkins.html", {
        "page_obj": _page_obj(qs, request),
        "page_title": "Check-ins",
    })


@login_required
@hostel_staff_required
def checkouts(request):
    """Allocations with a check-out date — recent first."""
    qs = HostelAllocation.objects.exclude(
        check_out__isnull=True
    ).select_related(
        "student", "bed__room__hostel", "session"
    ).order_by("-checked_out_at")
    return render(request, "hostel/hostel_checkouts.html", {
        "page_obj": _page_obj(qs, request),
        "page_title": "Check-outs",
    })


# ═══════════════════════════════════════════════════════════════════════════
# BEDS
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def beds(request):
    state_filter = request.GET.get("state", "")
    hostel_filter = request.GET.get("hostel", "")
    qs = HostelBed.objects.select_related(
        "room__hostel", "room__floor__block"
    ).order_by("room__hostel_id", "room__room_number", "bed_number")
    if state_filter in dict(HostelBed.BedState.choices):
        qs = qs.filter(state=state_filter)
    if hostel_filter:
        qs = qs.filter(room__hostel_id=hostel_filter)
    return render(request, "hostel/hostel_beds.html", {
        "page_obj": _page_obj(qs, request, per_page=50),
        "state_filter": state_filter,
        "hostel_filter": hostel_filter,
        "state_choices": HostelBed.BedState.choices,
        "hostels": Hostel.objects.filter(is_active=True).order_by("name"),
        "page_title": "Hostel Beds",
    })


@login_required
@hostel_staff_required
@require_POST
def bed_action(request, pk):
    bed = get_object_or_404(HostelBed, pk=pk)
    action = request.POST.get("action", "")
    note = request.POST.get("note", "")
    try:
        if action == "hold":
            services.HostelAllocationService.hold_bed(
                bed=bed, actor=request.user, note=note
            )
        elif action == "release":
            services.HostelAllocationService.release_hold(bed=bed, actor=request.user)
        elif action == "block":
            services.HostelAllocationService.set_bed_blocked(
                bed=bed, actor=request.user, blocked=True, note=note
            )
        elif action == "unblock":
            services.HostelAllocationService.set_bed_blocked(
                bed=bed, actor=request.user, blocked=False
            )
        else:
            messages.error(request, "Unknown action.")
            return redirect("hostel:beds")
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        messages.success(request, f"Bed {bed.display_name} updated.")
    return redirect("hostel:beds")


# ═══════════════════════════════════════════════════════════════════════════
# TRANSFERS
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def transfers(request):
    status_filter = request.GET.get("status", "")
    qs = HostelTransfer.objects.select_related(
        "student", "old_bed__room__hostel", "new_bed__room__hostel"
    ).order_by("-requested_at")
    if status_filter in dict(HostelTransfer.Status.choices):
        qs = qs.filter(status=status_filter)
    return render(request, "hostel/hostel_transfers.html", {
        "page_obj": _page_obj(qs, request),
        "status_filter": status_filter,
        "status_choices": HostelTransfer.Status.choices,
        "page_title": "Hostel Transfers",
    })


@login_required
@hostel_staff_required
@require_POST
def transfer_review(request, pk):
    transfer = get_object_or_404(HostelTransfer, pk=pk)
    approve = request.POST.get("decision") == "approve"
    try:
        services.HostelTransferService.review_transfer(
            transfer,
            actor=request.user,
            approve=approve,
            note=request.POST.get("review_note", ""),
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        if approve:
            messages.success(request, "Transfer approved. Complete it to move the student.")
        else:
            messages.warning(request, "Transfer rejected.")
    return redirect("hostel:transfers")


@login_required
@hostel_staff_required
@require_POST
def transfer_complete(request, pk):
    transfer = get_object_or_404(HostelTransfer, pk=pk)
    try:
        new_alloc = services.HostelTransferService.complete_transfer(
            transfer, actor=request.user
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        messages.success(
            request,
            f"Transfer complete. New allocation #{new_alloc.pk} created.",
        )
    return redirect("hostel:transfers")


# ═══════════════════════════════════════════════════════════════════════════
# MAINTENANCE
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def maintenance(request):
    status_filter = request.GET.get("status", "")
    qs = BedMaintenance.objects.select_related(
        "bed__room__hostel", "reported_by", "completed_by"
    ).order_by("-started_at")
    if status_filter in dict(BedMaintenance.Status.choices):
        qs = qs.filter(status=status_filter)
    return render(request, "hostel/hostel_maintenance.html", {
        "page_obj": _page_obj(qs, request),
        "status_filter": status_filter,
        "status_choices": BedMaintenance.Status.choices,
        "page_title": "Bed Maintenance",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def maintenance_create(request):
    form = BedMaintenanceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            record = services.BedMaintenanceService.start_maintenance(
                bed=form.cleaned_data["bed"],
                reason=form.cleaned_data["reason"],
                reported_by=request.user,
                notes=form.cleaned_data.get("notes", ""),
            )
        except services.HostelServiceError as exc:
            _service_message(request, exc)
        else:
            messages.success(
                request,
                f"Maintenance started for {record.bed}. Bed is now unavailable.",
            )
            return redirect("hostel:maintenance")
    return render(request, "hostel/hostel_maintenance_form.html", {
        "form": form,
        "page_title": "Start Bed Maintenance",
    })


@login_required
@hostel_staff_required
@require_POST
def maintenance_complete(request, pk):
    record = get_object_or_404(BedMaintenance, pk=pk)
    services.BedMaintenanceService.complete_maintenance(
        record, completed_by=request.user, notes=request.POST.get("notes", "")
    )
    messages.success(request, "Maintenance completed. Bed released if fully serviced.")
    return redirect("hostel:maintenance")


# ═══════════════════════════════════════════════════════════════════════════
# INCIDENTS
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def incidents(request):
    status_filter = request.GET.get("status", "")
    qs = HostelIncident.objects.select_related(
        "student", "room", "bed", "assigned_to"
    ).order_by("-created_at")
    if status_filter in dict(IncidentStatus.choices):
        qs = qs.filter(status=status_filter)
    return render(request, "hostel/hostel_incidents.html", {
        "page_obj": _page_obj(qs, request),
        "status_filter": status_filter,
        "status_choices": IncidentStatus.choices,
        "page_title": "Hostel Incidents",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def incident_create(request):
    form = HostelIncidentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.HostelIncidentService.report(
            form.cleaned_data.get("student"),
            category=form.cleaned_data["category"],
            description=form.cleaned_data["description"],
            reported_by=request.user,
            room=form.cleaned_data.get("room"),
            bed=form.cleaned_data.get("bed"),
        )
        messages.success(request, "Incident recorded.")
        return redirect("hostel:incidents")
    bed_room_map = {
        str(b.pk): b.room_id
        for b in form.fields["bed"].queryset
    }
    return render(request, "hostel/hostel_incident_form.html", {
        "form": form,
        "page_title": "Record Incident",
        "bed_room_map": bed_room_map,
    })


@login_required
@hostel_staff_required
@require_POST
def incident_resolve(request, pk):
    incident = get_object_or_404(HostelIncident, pk=pk)
    resolution = request.POST.get("resolution", "")
    status = request.POST.get("status", IncidentStatus.RESOLVED)
    if status not in dict(IncidentStatus.choices):
        status = IncidentStatus.RESOLVED
    services.HostelIncidentService.resolve(
        incident,
        resolved_by=request.user,
        resolution=resolution,
        status=status,
    )
    messages.success(request, "Incident updated.")
    return redirect("hostel:incidents")


# ═══════════════════════════════════════════════════════════════════════════
# ACCOMMODATION CRUD (NEW)
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def hostels(request):
    qs = Hostel.objects.annotate(bed_count=Count("rooms__beds")).order_by("name")
    return render(request, "hostel/hostel_list.html", {
        "hostels": qs,
        "page_title": "Hostels",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def hostel_create(request):
    form = HostelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            messages.error(request, "A hostel with this name already exists.")
        else:
            messages.success(request, "Hostel created.")
            return redirect("hostel:hostels")
    return render(request, "hostel/hostel_form.html", {
        "form": form, "page_title": "Add Hostel",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def hostel_edit(request, pk):
    hostel = get_object_or_404(Hostel, pk=pk)
    form = HostelForm(request.POST or None, instance=hostel)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            messages.error(request, "A hostel with this name already exists.")
        else:
            messages.success(request, "Hostel updated.")
            return redirect("hostel:hostels")
    return render(request, "hostel/hostel_form.html", {
        "form": form, "page_title": "Edit Hostel",
    })


@login_required
def hostel_detail(request, pk):
    """Hostel detail shared by students and staff (view-only for students).

    Amenity and fee-scope sections are rendered defensively so the page
    still works when the live DB hasn't been migrated with the new tables
    yet (see ``hostel.schema``).
    """
    hostel = get_object_or_404(Hostel, pk=pk)
    rows = services.HostelAvailabilityService.hostel_overview([hostel.pk])
    summary = rows[0] if rows else None

    from hostel import schema
    from hostel.services.finance import HostelFinanceService

    amenities_enabled = schema.table_exists("hostel_amenity")
    fee_scope_enabled = schema.column_exists("hostel_hostelfeeconfig", "semester_id")

    amenities = None
    if amenities_enabled:
        amenities = list(hostel.amenities.all().order_by("display_order", "name"))

    fee_amount = None
    semester = None
    if fee_scope_enabled:
        session = AcademicSession.get_current()
        if session is not None:
            current = Semester.get_current()
            if current is not None and current.session_id == session.id:
                semester = current
            fee_amount = HostelFinanceService.fee_for(
                hostel, session, semester=semester
            )

    return render(request, "hostel/hostel_detail.html", {
        "hostel": hostel,
        "summary": summary,
        "amenities": amenities,
        "amenities_enabled": amenities_enabled,
        "fee_scope_enabled": fee_scope_enabled,
        "fee_amount": fee_amount,
        "semester": semester,
        "page_title": hostel.name,
        "base_template": _base_for_user(request.user),
    })


@login_required
@hostel_staff_required
def blocks(request):
    hostel_filter = request.GET.get("hostel", "")
    qs = HostelBlock.objects.select_related("hostel").order_by("hostel__name", "name")
    if hostel_filter:
        qs = qs.filter(hostel_id=hostel_filter)
    return render(request, "hostel/hostel_blocks.html", {
        "page_obj": _page_obj(qs, request),
        "hostel_filter": hostel_filter,
        "hostels": Hostel.objects.filter(is_active=True).order_by("name"),
        "page_title": "Blocks",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def block_create(request):
    form = HostelBlockForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            messages.error(request, "A block with this name already exists in this hostel.")
        else:
            messages.success(request, "Block created.")
            return redirect("hostel:blocks")
    return render(request, "hostel/hostel_form.html", {
        "form": form, "page_title": "Add Block",
    })


@login_required
@hostel_staff_required
def floors(request):
    block_filter = request.GET.get("block", "")
    qs = HostelFloor.objects.select_related("block__hostel").order_by("block__hostel__name", "block__name", "name")
    if block_filter:
        qs = qs.filter(block_id=block_filter)
    return render(request, "hostel/hostel_floors.html", {
        "page_obj": _page_obj(qs, request),
        "block_filter": block_filter,
        "blocks": HostelBlock.objects.select_related("hostel").filter(
            is_active=True, hostel__is_active=True
        ),
        "page_title": "Floors",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def floor_create(request):
    form = HostelFloorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            messages.error(request, "A floor with this name already exists in this block.")
        else:
            messages.success(request, "Floor created.")
            return redirect("hostel:floors")
    return render(request, "hostel/hostel_form.html", {
        "form": form, "page_title": "Add Floor",
    })


@login_required
@hostel_staff_required
def rooms(request):
    hostel_filter = request.GET.get("hostel", "")
    qs = HostelRoom.objects.select_related("hostel", "floor__block").order_by("hostel__name", "room_number")
    if hostel_filter:
        qs = qs.filter(hostel_id=hostel_filter)
    return render(request, "hostel/hostel_rooms.html", {
        "page_obj": _page_obj(qs, request),
        "hostel_filter": hostel_filter,
        "hostels": Hostel.objects.filter(is_active=True).order_by("name"),
        "page_title": "Rooms",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def room_create(request):
    form = HostelRoomForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            messages.error(request, "A room with this number already exists in this hostel.")
        else:
            messages.success(request, "Room created.")
            return redirect("hostel:rooms")
    return render(request, "hostel/hostel_form.html", {
        "form": form, "page_title": "Add Room",
    })


# ═══════════════════════════════════════════════════════════════════════════
# OUTSTANDING BALANCES (NEW)
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def outstanding_balances(request):
    """Active allocations whose finance balance is unpaid / partially paid."""
    from hostel.services.finance import HostelFinanceService
    allocs = HostelAllocation.objects.filter(
        status__in=(HostelAllocation.Status.ACTIVE, HostelAllocation.Status.RESERVED)
    ).select_related(
        "student", "bed__room__hostel", "session"
    ).order_by("-created_at")

    rows = []
    for alloc in allocs:
        if not alloc.session:
            continue
        info = HostelFinanceService.balance_info(alloc.student, alloc.session)
        if info["status"] not in ("none", "paid"):
            rows.append({"allocation": alloc, "finance": info})

    return render(request, "hostel/hostel_outstanding_balances.html", {
        "rows": rows,
        "page_title": "Outstanding Hostel Balances",
    })


# ═══════════════════════════════════════════════════════════════════════════
# POLICY + FEE CONFIG (SETTINGS)
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def policy(request):
    active = HostelPolicy.get_active()
    form = HostelPolicyForm(
        request.POST or None,
        instance=active,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Hostel policy updated.")
        return redirect("hostel:policy")
    return render(request, "hostel/hostel_policy.html", {
        "form": form,
        "policy": active,
        "page_title": "Hostel Policy",
    })


@login_required
@hostel_staff_required
def fee_configs(request):
    configs = HostelFeeConfig.objects.select_related(
        "hostel", "session", "fee_structure", "semester", "room"
    ).order_by("-session__start_date", "hostel__name")
    return render(request, "hostel/hostel_fee_configs.html", {
        "configs": configs,
        "page_title": "Hostel Fee Configurations",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def fee_config_create(request):
    form = HostelFeeConfigForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Fee configuration saved.")
        return redirect("hostel:fee_configs")
    return render(request, "hostel/hostel_fee_config_form.html", {
        "form": form,
        "config": None,
        "page_title": "Add Hostel Fee Configuration",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def fee_config_edit(request, pk):
    config = get_object_or_404(HostelFeeConfig, pk=pk)
    form = HostelFeeConfigForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Fee configuration updated.")
        return redirect("hostel:fee_configs")
    return render(request, "hostel/hostel_fee_config_form.html", {
        "form": form,
        "config": config,
        "page_title": "Edit Hostel Fee Configuration",
    })


@login_required
@hostel_staff_required
@require_POST
def fee_config_delete(request, pk):
    config = get_object_or_404(HostelFeeConfig, pk=pk)
    config.delete()
    messages.success(request, "Fee configuration deleted.")
    return redirect("hostel:fee_configs")


@login_required
@hostel_staff_required
def amenities(request):
    """Configurable amenities (Wi‑Fi, aircon, en‑suite…)."""
    qs = Amenity.objects.all().order_by("display_order", "name")
    return render(request, "hostel/hostel_amenities.html", {
        "amenities": qs,
        "page_title": "Hostel Amenities",
    })


@login_required
@hostel_staff_required
@require_http_methods(["GET", "POST"])
def amenity_create(request):
    form = AmenityForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            messages.error(request, "An amenity with this name already exists.")
        else:
            messages.success(request, "Amenity added.")
            return redirect("hostel:amenities")
    return render(request, "hostel/hostel_form.html", {
        "form": form, "page_title": "Add Amenity",
    })


# ═══════════════════════════════════════════════════════════════════════════
# REPORTS (NEW)
# ═══════════════════════════════════════════════════════════════════════════

@login_required
@hostel_staff_required
def reports(request):
    context = {
        "occupancy": occupancy_report(),
        "applications": application_funnel(),
        "incidents": incident_summary(),
        "transfers": transfer_summary(),
        "page_title": "Hostel Reports",
    }
    return render(request, "hostel/hostel_reports.html", context)