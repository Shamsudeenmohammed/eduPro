from django.contrib import admin
from django.utils import timezone

from .models import CourseRegistrationRequest, MaterialDownloadLog, StudentNotification


@admin.register(StudentNotification)
class StudentNotificationAdmin(admin.ModelAdmin):
    list_display  = ("student", "category", "title", "is_read", "created_at")
    list_filter   = ("category", "is_read")
    search_fields = ("student__email", "student__first_name", "title")
    readonly_fields = ("created_at", "read_at")


@admin.register(CourseRegistrationRequest)
class CourseRegistrationRequestAdmin(admin.ModelAdmin):
    list_display  = ("student", "offering", "status", "created_at", "reviewed_by")
    list_filter   = ("status", "offering__semester__session")
    search_fields = ("student__email", "student__first_name", "student__last_name", "offering__course__code", "offering__course__title")
    readonly_fields = ("created_at", "reviewed_at")
    autocomplete_fields = ("student", "offering", "reviewed_by")
    list_select_related = ("student", "offering", "reviewed_by")
    actions = ["approve_requests", "reject_requests"]

    @admin.action(description="Approve selected requests")
    def approve_requests(self, request, queryset):
        queryset.filter(status="pending").update(
            status="approved",
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )

    @admin.action(description="Reject selected requests")
    def reject_requests(self, request, queryset):
        queryset.filter(status="pending").update(
            status="rejected",
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )


@admin.register(MaterialDownloadLog)
class MaterialDownloadLogAdmin(admin.ModelAdmin):
    list_display  = ("student", "material", "accessed_at")
    list_filter   = ("material__material_type",)
    search_fields = ("student__email", "material__title")
    readonly_fields = ("accessed_at",)
