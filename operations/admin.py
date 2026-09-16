from django.contrib import admin

from .models import (
    Announcement,
    CalendarEvent,
    SupportTicket,
    TimetableSlot,
)


@admin.register(TimetableSlot)
class TimetableSlotAdmin(admin.ModelAdmin):
    list_display = ("offering", "day", "start_time", "end_time", "venue", "is_active")
    list_filter = ("day", "is_active")
    search_fields = ("offering__course__code", "offering__course__title", "venue")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "priority", "posted_by", "is_pinned", "expires_at", "is_active")
    list_filter = ("priority", "is_pinned", "is_active")
    search_fields = ("title", "content", "posted_by__email", "posted_by__first_name")


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("title", "start_date", "end_date", "event_type", "location", "is_public")
    list_filter = ("event_type", "is_public", "start_date")
    search_fields = ("title", "description", "location")


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("subject", "student", "category", "status", "assigned_to", "created_at")
    list_filter = ("status", "category")
    search_fields = ("subject", "description", "student__email", "student__first_name", "student__last_name", "assigned_to__email")