from django.contrib import admin
from .models import (
    Announcement, CalendarEvent, Hostel, HostelAllocation,
    HostelApplication, HostelRoom, SupportTicket, TimetableSlot,
)


class HostelRoomInline(admin.TabularInline):
    model = HostelRoom
    extra = 1
    fields = ["room_number", "capacity", "is_available"]
    show_change_link = True


@admin.register(Hostel)
class HostelAdmin(admin.ModelAdmin):
    list_display = ["name", "location", "capacity", "total_beds", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "location"]
    inlines = [HostelRoomInline]

    def total_beds(self, obj):
        return sum(r.capacity for r in obj.rooms.all())
    total_beds.short_description = "Total Beds"


@admin.register(HostelRoom)
class HostelRoomAdmin(admin.ModelAdmin):
    list_display = ["room_number", "hostel", "capacity", "occupied_beds", "available_beds", "is_available"]
    list_filter = ["hostel", "is_available"]
    search_fields = ["room_number", "hostel__name"]

    def occupied_beds(self, obj):
        return obj.allocations.filter(is_active=True).count()
    occupied_beds.short_description = "Occupied"

    def available_beds(self, obj):
        return obj.available_beds
    available_beds.short_description = "Available"


@admin.register(HostelAllocation)
class HostelAllocationAdmin(admin.ModelAdmin):
    list_display = ["student", "room", "check_in", "check_out", "is_active"]
    list_filter = ["is_active", "check_in"]
    search_fields = ["student__first_name", "student__last_name", "student__email", "room__room_number"]
    date_hierarchy = "check_in"
    raw_id_fields = ["student", "room"]


@admin.register(HostelApplication)
class HostelApplicationAdmin(admin.ModelAdmin):
    list_display = ["student", "room", "status", "created_at", "reviewed_at"]
    list_filter = ["status"]
    search_fields = ["student__first_name", "student__last_name", "room__room_number"]
    raw_id_fields = ["student", "room", "reviewed_by"]


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
