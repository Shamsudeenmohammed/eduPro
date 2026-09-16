from django.contrib import admin

from .models import (
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
)


@admin.register(Amenity)
class AmenityAdmin(admin.ModelAdmin):
    list_display = ["name", "icon", "display_order", "is_active"]
    list_editable = ["display_order", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]


# ─────────────────────────────────────────────────────────────────────────────
# Hostel hierarchy
# ─────────────────────────────────────────────────────────────────────────────

class HostelBlockInline(admin.TabularInline):
    model = HostelBlock
    extra = 0
    fields = ["name", "is_active"]
    show_change_link = True


class HostelRoomInline(admin.TabularInline):
    model = HostelRoom
    extra = 0
    fields = ["floor", "room_number", "capacity", "is_available"]
    show_change_link = True


@admin.register(Hostel)
class HostelAdmin(admin.ModelAdmin):
    list_display = ["name", "location", "capacity", "total_beds", "available_beds", "is_active"]
    list_filter = ["is_active", "amenities"]
    search_fields = ["name", "location"]
    filter_horizontal = ["amenities"]
    inlines = [HostelBlockInline, HostelRoomInline]

    @admin.display(description="Available beds")
    def available_beds(self, obj):
        return obj.available_beds


class HostelFloorInline(admin.TabularInline):
    model = HostelFloor
    extra = 0
    fields = ["name", "is_active"]
    show_change_link = True


@admin.register(HostelBlock)
class HostelBlockAdmin(admin.ModelAdmin):
    list_display = ["name", "hostel", "is_active"]
    list_filter = ["hostel", "is_active"]
    search_fields = ["name", "hostel__name"]
    inlines = [HostelFloorInline]


@admin.register(HostelFloor)
class HostelFloorAdmin(admin.ModelAdmin):
    list_display = ["name", "block", "hostel", "is_active"]
    list_filter = ["block__hostel", "is_active"]
    search_fields = ["name", "block__name", "block__hostel__name"]

    @admin.display(description="Hostel")
    def hostel(self, obj):
        return obj.block.hostel


class HostelBedInline(admin.TabularInline):
    model = HostelBed
    extra = 0
    fields = ["bed_number", "label", "state", "notes"]
    show_change_link = True


@admin.register(HostelRoom)
class HostelRoomAdmin(admin.ModelAdmin):
    list_display = ["room_number", "hostel", "block", "capacity", "available_beds", "occupied_beds", "is_available"]
    list_filter = ["hostel", "is_available", "amenities"]
    search_fields = ["room_number", "hostel__name"]
    filter_horizontal = ["amenities"]
    inlines = [HostelBedInline]

    @admin.display(description="Block")
    def block(self, obj):
        return obj.block.name if obj.block else "—"

    @admin.display(description="Occupied")
    def occupied_beds(self, obj):
        return obj.occupied_beds

    @admin.display(description="Available")
    def available_beds(self, obj):
        return obj.available_beds


@admin.register(HostelBed)
class HostelBedAdmin(admin.ModelAdmin):
    list_display = ["display_name", "room", "hostel", "state"]
    list_filter = ["state", "room__hostel"]
    search_fields = ["bed_number", "label", "room__room_number", "room__hostel__name"]
    list_select_related = ["room__hostel", "room__floor__block"]

    @admin.display(description="Hostel")
    def hostel(self, obj):
        return obj.room.hostel


# ─────────────────────────────────────────────────────────────────────────────
# Applications / allocations / transfers
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(HostelApplication)
class HostelApplicationAdmin(admin.ModelAdmin):
    list_display = ["student", "room", "session", "status", "submitted_at", "reviewed_at"]
    list_filter = ["status", "session", "room__hostel"]
    search_fields = ["student__first_name", "student__last_name", "student__email", "room__room_number"]
    raw_id_fields = ["student", "room", "reviewed_by", "allocation"]
    date_hierarchy = "submitted_at"


@admin.register(HostelAllocation)
class HostelAllocationAdmin(admin.ModelAdmin):
    list_display = ["student", "bed", "room", "session", "status", "check_in", "check_out", "is_active"]
    list_filter = ["status", "session", "room__hostel"]
    search_fields = ["student__first_name", "student__last_name", "student__email",
                     "room__room_number", "bed__bed_number", "bed__label"]
    raw_id_fields = ["student", "bed", "room", "session", "allocated_by", "checked_in_by", "checked_out_by"]
    list_select_related = ["student", "bed", "room", "session"]
    date_hierarchy = "check_in"


@admin.register(HostelTransfer)
class HostelTransferAdmin(admin.ModelAdmin):
    list_display = ["student", "old_bed", "new_bed", "status", "requested_at", "reviewed_at", "completed_at"]
    list_filter = ["status"]
    search_fields = ["student__first_name", "student__last_name", "old_bed__label", "old_bed__bed_number",
                     "new_bed__label", "new_bed__bed_number"]
    raw_id_fields = ["student", "old_allocation", "old_bed", "new_bed", "new_allocation",
                     "requested_by", "reviewed_by", "completed_by"]
    list_select_related = ["student", "old_bed", "new_bed"]


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance / incidents / policy / fees
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(BedMaintenance)
class BedMaintenanceAdmin(admin.ModelAdmin):
    list_display = ["bed", "reason", "status", "started_at", "ended_at"]
    list_filter = ["status"]
    search_fields = ["reason", "bed__label", "bed__bed_number", "bed__room__room_number"]
    raw_id_fields = ["bed", "reported_by", "completed_by"]


@admin.register(HostelIncident)
class HostelIncidentAdmin(admin.ModelAdmin):
    list_display = ["student", "category", "status", "room", "reported_by", "created_at", "resolved_at"]
    list_filter = ["category", "status"]
    search_fields = ["student__first_name", "student__last_name", "description", "room__room_number"]
    raw_id_fields = ["student", "room", "bed", "allocation", "reported_by", "assigned_to", "resolved_by"]


@admin.register(HostelPolicy)
class HostelPolicyAdmin(admin.ModelAdmin):
    list_display = [
        "created_at", "reservation_expiry_hours", "enable_hostel_charges",
        "require_payment_before_checkin", "allow_transfers", "is_active",
    ]
    list_filter = ["is_active", "enable_hostel_charges"]


@admin.register(HostelFeeConfig)
class HostelFeeConfigAdmin(admin.ModelAdmin):
    list_display = ["hostel", "session", "semester", "room", "fee_structure", "amount", "due_date", "is_active"]
    list_filter = ["session", "semester", "is_active"]
    search_fields = ["hostel__name", "session__name", "room__room_number"]
    raw_id_fields = ["hostel", "session", "semester", "room", "fee_structure"]

    @admin.display(description="Room", ordering="room__room_number")
    def room(self, obj):
        return f"Room {obj.room.room_number}" if obj.room_id else "—"

    @admin.display(description="Semester", ordering="semester__name")
    def semester(self, obj):
        return obj.semester.get_name_display() if obj.semester_id else "—"