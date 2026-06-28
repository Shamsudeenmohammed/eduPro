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


admin.site.register(TimetableSlot)
admin.site.register(Announcement)
admin.site.register(CalendarEvent)
admin.site.register(SupportTicket)
