from django.contrib import admin

from scheduling.models import (
    AcademicEvent,
    AcademicSchedule,
    Building,
    LecturerUnavailability,
    Room,
    ScheduleEntry,
    ScheduleGenerationJob,
    ScheduleHodApproval,
    ScheduleVersion,
    SchedulingConfig,
    TimeSlot,
)


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "institution", "is_active")
    list_filter = ("institution", "is_active")
    search_fields = ("name", "code")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("code", "building", "room_type", "capacity", "exam_capacity",
                    "is_available", "is_active")
    list_filter = ("room_type", "building__institution", "is_available", "is_active")
    search_fields = ("code", "name", "building__name")


@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ("day", "start_time", "end_time", "label", "is_default", "is_active")
    list_filter = ("day", "is_default", "is_active")
    ordering = ("day", "start_time")


@admin.register(SchedulingConfig)
class SchedulingConfigAdmin(admin.ModelAdmin):
    list_display = ("institution", "default_slot_minutes", "min_break_minutes",
                    "seed", "updated_at")
    search_fields = ("institution__name",)


@admin.register(AcademicEvent)
class AcademicEventAdmin(admin.ModelAdmin):
    list_display = ("title", "event_type", "scope", "start_date", "end_date",
                    "affects_scheduling", "is_public")
    list_filter = ("event_type", "scope", "affects_scheduling", "is_public")
    search_fields = ("title", "description")
    date_hierarchy = "start_date"


@admin.register(LecturerUnavailability)
class LecturerUnavailabilityAdmin(admin.ModelAdmin):
    list_display = ("lecturer", "day", "start_time", "end_time", "is_active")
    list_filter = ("day", "is_active")


@admin.register(AcademicSchedule)
class AcademicScheduleAdmin(admin.ModelAdmin):
    list_display = ("name", "semester", "schedule_type", "status", "hard_conflicts",
                    "unplaced", "score", "is_current")
    list_filter = ("status", "schedule_type", "semester__session")
    search_fields = ("name",)
    readonly_fields = (
        "hard_conflicts", "soft_conflicts", "unplaced", "score",
        "generated_at", "approved_at", "published_at",
    )


@admin.register(ScheduleHodApproval)
class ScheduleHodApprovalAdmin(admin.ModelAdmin):
    list_display = ("schedule", "department", "hod", "approved", "decided_at")
    list_filter = ("approved",)


@admin.register(ScheduleVersion)
class ScheduleVersionAdmin(admin.ModelAdmin):
    list_display = ("schedule", "version_number", "is_published", "reason",
                    "created_by", "created_at")
    list_filter = ("is_published",)
    readonly_fields = ("snapshot", "stats")


@admin.register(ScheduleEntry)
class ScheduleEntryAdmin(admin.ModelAdmin):
    list_display = ("course", "schedule", "day", "start_time", "end_time",
                    "room", "lecturer", "session_type", "is_locked")
    list_filter = ("session_type", "day", "is_locked")
    search_fields = ("course__code", "course__title", "lecturer_name")


@admin.register(ScheduleGenerationJob)
class ScheduleGenerationJobAdmin(admin.ModelAdmin):
    list_display = ("schedule", "status", "requested_by", "created_at",
                    "started_at", "finished_at")
    list_filter = ("status",)
    readonly_fields = ("result_stats",)