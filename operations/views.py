from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required, student_required
from .forms import AnnouncementForm, CalendarEventForm, SupportTicketForm, TimetableSlotForm
from .models import Announcement, CalendarEvent, Hostel, HostelAllocation, SupportTicket, TimetableSlot


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
    my_allocation = None
    if getattr(request.user, "is_student", False):
        my_allocation = HostelAllocation.objects.filter(
            student=request.user, is_active=True
        ).select_related("room__hostel").first()
    return render(request, "operations/hostel.html", {
        "hostels": hostels,
        "my_allocation": my_allocation,
        "page_title": "Hostel & Accommodation",
        "base_template": _base_for_user(request.user),
    })
