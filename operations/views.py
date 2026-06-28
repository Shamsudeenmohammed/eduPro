from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.decorators import admin_required, student_required
from .forms import (
    AnnouncementForm, CalendarEventForm, HostelApplyForm,
    SupportTicketForm, TimetableSlotForm,
)
from .models import (
    Announcement, CalendarEvent, Hostel, HostelAllocation,
    HostelApplication, HostelRoom, SupportTicket, TimetableSlot,
)


def _base_for_user(user):
    return "students/base.html" if getattr(user, "is_student", False) else "admin_base.html"


@login_required
def announcement_list(request):
    from django.db.models import Q
    qs = Announcement.objects.filter(is_active=True).filter(
        Q(target_roles="") | Q(target_roles__icontains=request.user.role)
    )
    return render(request, "operations/announcements.html", {
        "announcements": qs[:50],
        "page_title": "Announcements",
        "base_template": _base_for_user(request.user),
    })


@login_required
@admin_required
def announcement_manage(request):
    if request.method == "POST":
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            ann = form.save(commit=False)
            ann.posted_by = request.user
            ann.save()
            messages.success(request, "Announcement posted.")
            return redirect("operations:announcement_manage")
    else:
        form = AnnouncementForm()
    items = Announcement.objects.order_by("-created_at")[:30]
    return render(request, "operations/announcement_manage.html", {
        "form": form, "items": items, "page_title": "Manage Announcements",
    })


@login_required
def calendar_view(request):
    events = CalendarEvent.objects.filter(is_public=True)
    if getattr(request.user, "is_admin", False):
        events = CalendarEvent.objects.all()
    return render(request, "operations/calendar.html", {
        "events": events,
        "page_title": "Academic Calendar",
        "base_template": _base_for_user(request.user),
    })


@login_required
@admin_required
def calendar_manage(request):
    form = CalendarEventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ev = form.save(commit=False)
        ev.created_by = request.user
        ev.save()
        messages.success(request, "Event added.")
        return redirect("operations:calendar_manage")
    events = CalendarEvent.objects.order_by("start_date")
    return render(request, "operations/calendar_manage.html", {
        "form": form, "events": events, "page_title": "Manage Calendar",
    })


@login_required
def timetable_view(request):
    from academics.models import CourseAllocation, Enrolment
    slots = TimetableSlot.objects.filter(is_active=True).select_related(
        "offering__course", "offering__semester"
    )
    if getattr(request.user, "is_student", False):
        offering_ids = Enrolment.objects.filter(
            student=request.user, is_active=True
        ).values_list("offering_id", flat=True)
        slots = slots.filter(offering_id__in=offering_ids)
    elif getattr(request.user, "is_teacher", False):
        offering_ids = CourseAllocation.objects.filter(
            teacher=request.user, is_active=True
        ).values_list("offering_id", flat=True)
        slots = slots.filter(offering_id__in=offering_ids)
    return render(request, "operations/timetable.html", {
        "slots": slots, "page_title": "Timetable",
        "base_template": _base_for_user(request.user),
    })


@login_required
@admin_required
def timetable_manage(request):
    form = TimetableSlotForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Timetable slot added.")
        return redirect("operations:timetable_manage")
    slots = TimetableSlot.objects.select_related("offering__course").order_by("day", "start_time")
    return render(request, "operations/timetable_manage.html", {
        "form": form, "slots": slots, "page_title": "Manage Timetable",
    })


@login_required
@student_required
def ticket_list(request):
    tickets = SupportTicket.objects.filter(student=request.user)
    return render(request, "operations/tickets.html", {
        "tickets": tickets, "page_title": "Support Tickets",
    })


@login_required
@student_required
@require_http_methods(["GET", "POST"])
def ticket_create(request):
    form = SupportTicketForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        t = form.save(commit=False)
        t.student = request.user
        t.save()
        messages.success(request, "Ticket submitted.")
        return redirect("operations:ticket_list")
    return render(request, "operations/ticket_form.html", {"form": form, "page_title": "New Ticket"})


@login_required
@admin_required
def ticket_admin(request):
    qs = SupportTicket.objects.select_related("student").order_by("-created_at")
    paginator = Paginator(qs, 25)
    return render(request, "operations/ticket_admin.html", {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "page_title": "Support Tickets",
    })


@login_required
def hostel_list(request):
    hostels = Hostel.objects.filter(is_active=True).prefetch_related("rooms")
    is_student = getattr(request.user, "is_student", False)

    # Auto-expire allocations past their expiry date
    expired_qs = HostelAllocation.objects.filter(
        is_active=True, expires_at__isnull=False, expires_at__lt=date.today()
    )
    for alloc in expired_qs:
        alloc.expire()

    my_allocation = None
    my_application = None
    show_form = False
    can_renew = False

    if is_student:
        my_allocation = HostelAllocation.objects.filter(
            student=request.user, is_active=True
        ).select_related("room__hostel").first()
        my_application = HostelApplication.objects.filter(
            student=request.user
        ).exclude(status=HostelApplication.Status.CONFIRMED).first()

        if my_allocation:
            can_renew = my_allocation.days_left <= 60
        else:
            show_form = not my_application

    # Build availability data per hostel
    hostel_data = []
    for h in hostels:
        occupied_room_ids = HostelAllocation.objects.filter(
            room__hostel=h, is_active=True
        ).values_list("room_id", flat=True)
        approved_room_ids = HostelApplication.objects.filter(
            room__hostel=h, status=HostelApplication.Status.APPROVED
        ).values_list("room_id", flat=True)
        excluded_ids = list(occupied_room_ids) + list(approved_room_ids)
        rooms = h.rooms.filter(is_available=True).exclude(pk__in=excluded_ids)

        total_beds = sum(r.capacity for r in h.rooms.all())
        occupied_beds = HostelAllocation.objects.filter(
            room__hostel=h, is_active=True
        ).count()
        pending_approvals = HostelApplication.objects.filter(
            room__hostel=h, status=HostelApplication.Status.APPROVED
        ).count()

        hostel_data.append({
            "hostel": h,
            "rooms": rooms,
            "total_beds": total_beds,
            "occupied_beds": occupied_beds,
            "pending_approvals": pending_approvals,
            "available_beds": total_beds - occupied_beds - pending_approvals,
        })

    form = HostelApplyForm() if show_form else None

    return render(request, "operations/hostel.html", {
        "hostel_data": hostel_data,
        "my_allocation": my_allocation,
        "my_application": my_application,
        "form": form,
        "can_renew": can_renew,
        "page_title": "Hostel & Accommodation",
        "base_template": _base_for_user(request.user),
    })


@login_required
@student_required
@require_POST
def hostel_apply(request):
    """Student submits a hostel application for admin review."""
    # Guard: existing allocation
    if HostelAllocation.objects.filter(student=request.user, is_active=True).exists():
        messages.warning(request, "You already have an active hostel allocation.")
        return redirect("operations:hostel")

    # Guard: existing pending/approved application
    if HostelApplication.objects.filter(
        student=request.user, status__in=[
            HostelApplication.Status.PENDING,
            HostelApplication.Status.APPROVED,
        ]
    ).exists():
        messages.warning(request, "You already have a pending application.")
        return redirect("operations:hostel")

    form = HostelApplyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please select a valid hostel and room.")
        return redirect("operations:hostel")

    room = form.cleaned_data["room"]

    # Check room availability (active allocations + approved applications)
    occupied = HostelAllocation.objects.filter(room=room, is_active=True).count()
    approved = HostelApplication.objects.filter(
        room=room, status=HostelApplication.Status.APPROVED
    ).count()
    if occupied + approved >= room.capacity:
        messages.error(request, "This room is no longer available. Please choose another.")
        return redirect("operations:hostel")

    HostelApplication.objects.create(
        student=request.user,
        room=room,
        status=HostelApplication.Status.PENDING,
    )
    messages.success(
        request,
        f"Application submitted for {room}. Awaiting admin review.",
    )
    return redirect("operations:hostel")


@login_required
def hostel_rooms_api(request):
    """JSON endpoint returning available rooms for a given hostel."""
    hostel_id = request.GET.get("hostel")
    if not hostel_id:
        return JsonResponse({"rooms": []})
    occupied_ids = HostelAllocation.objects.filter(
        room__hostel_id=hostel_id, is_active=True
    ).values_list("room_id", flat=True)
    rooms = HostelRoom.objects.filter(
        hostel_id=hostel_id, is_available=True
    ).exclude(pk__in=occupied_ids).values("id", "room_number", "capacity")
    return JsonResponse({"rooms": list(rooms)})


@login_required
@student_required
@require_POST
def hostel_vacate(request):
    """Student vacates their current hostel room."""
    allocation = get_object_or_404(
        HostelAllocation,
        student=request.user,
        is_active=True,
    )
    allocation.is_active = False
    allocation.check_out = date.today()
    allocation.save()
    messages.success(
        request,
        f"You have vacated {allocation.room}. Thank you for staying with us.",
    )
    return redirect("operations:hostel")


# ── Admin: Hostel Application Management ─────────────────────────────────


@login_required
@admin_required
def hostel_applications_admin(request):
    """Admin lists all applications with approve/reject actions."""
    status_filter = request.GET.get("status", "")
    qs = HostelApplication.objects.select_related(
        "student", "room__hostel"
    ).order_by("-created_at")
    if status_filter in dict(HostelApplication.Status.choices):
        qs = qs.filter(status=status_filter)
    return render(request, "operations/hostel_applications_admin.html", {
        "applications": qs,
        "current_status": status_filter,
        "status_choices": HostelApplication.Status.choices,
        "page_title": "Hostel Applications",
    })


@login_required
@admin_required
@require_POST
def hostel_application_approve(request, pk):
    """Admin approves a pending application. Student must then pay to confirm."""
    application = get_object_or_404(
        HostelApplication, pk=pk, status=HostelApplication.Status.PENDING,
    )
    # Double-check room still has capacity
    occupied = HostelAllocation.objects.filter(
        room=application.room, is_active=True
    ).count()
    approved = HostelApplication.objects.filter(
        room=application.room, status=HostelApplication.Status.APPROVED
    ).exclude(pk=application.pk).count()
    if occupied + approved >= application.room.capacity:
        messages.error(request, "Room is now full. Reject this application instead.")
        return redirect("operations:hostel_applications_admin")

    application.status = HostelApplication.Status.APPROVED
    application.reviewed_by = request.user
    application.reviewed_at = timezone.now()
    application.admin_remark = request.POST.get("admin_remark", "")
    application.save()
    messages.success(
        request,
        f"Application from {application.student.get_full_name()} approved. "
        f"They can now complete payment to confirm.",
    )
    return redirect("operations:hostel_applications_admin")


@login_required
@admin_required
@require_POST
def hostel_application_reject(request, pk):
    """Admin rejects a pending application."""
    application = get_object_or_404(
        HostelApplication, pk=pk, status=HostelApplication.Status.PENDING,
    )
    application.status = HostelApplication.Status.REJECTED
    application.reviewed_by = request.user
    application.reviewed_at = timezone.now()
    application.admin_remark = request.POST.get("admin_remark", "No reason provided.")
    application.save()
    messages.warning(
        request,
        f"Application from {application.student.get_full_name()} rejected.",
    )
    return redirect("operations:hostel_applications_admin")


@login_required
@student_required
@require_POST
def hostel_confirm_payment(request, pk):
    """Student confirms payment for an approved application → allocation created."""
    application = get_object_or_404(
        HostelApplication,
        pk=pk,
        student=request.user,
        status=HostelApplication.Status.APPROVED,
    )
    # Final availability check
    occupied = HostelAllocation.objects.filter(
        room=application.room, is_active=True
    ).count()
    if occupied >= application.room.capacity:
        messages.error(request, "Sorry, this room is now full. Contact admin.")
        return redirect("operations:hostel")

    with transaction.atomic():
        HostelAllocation.objects.create(
            student=request.user,
            room=application.room,
            check_in=date.today(),
            is_active=True,
        )
        application.status = HostelApplication.Status.CONFIRMED
        application.save()

    messages.success(
        request,
        f"Payment confirmed! You have been allocated to {application.room}. "
        f"Welcome to {application.room.hostel.name}!",
    )
    return redirect("operations:hostel")


@login_required
@student_required
@require_POST
def hostel_renew_allocation(request):
    """Student renews their current allocation for another year."""
    allocation = get_object_or_404(
        HostelAllocation,
        student=request.user,
        is_active=True,
    )
    if allocation.days_left > 60:
        messages.warning(
            request,
            f"Your allocation still has {allocation.days_left} days remaining. "
            f"Renewal is available within 60 days of expiry.",
        )
        return redirect("operations:hostel")

    from datetime import timedelta
    if not allocation.expires_at:
        allocation.expires_at = allocation.check_in + timedelta(days=365)
    allocation.expires_at = date(allocation.expires_at.year + 1,
                                  allocation.expires_at.month,
                                  allocation.expires_at.day)
    allocation.save(update_fields=["expires_at"])
    messages.success(
        request,
        f"Your allocation has been renewed! New expiry: {allocation.expires_at}.",
    )
    return redirect("operations:hostel")
