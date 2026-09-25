"""Django admin registration for id_cards (superusers only)."""

from django.contrib import admin

from .models import (
    IDCard,
    IDCardAuditLog,
    IDCardBatch,
    IDCardSettings,
    IDCardTemplate,
    PassportPhoto,
    ReplacementRequest,
)


@admin.register(PassportPhoto)
class PassportPhotoAdmin(admin.ModelAdmin):
    list_display = ("student", "status", "submitted_at", "reviewed_at")
    list_filter = ("status", "submitted_at")
    search_fields = ("student__email", "student__first_name", "student__last_name")
    readonly_fields = ("submitted_at", "reviewed_at")
    raw_id_fields = ("student", "reviewed_by")


@admin.register(IDCardSettings)
class IDCardSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "institution", "validity_years", "card_number_prefix",
        "auto_renewal_enabled", "require_photo_approval",
    )


@admin.register(IDCardTemplate)
class IDCardTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "institution", "status", "is_default", "created_at")
    list_filter = ("status", "institution")
    search_fields = ("name", "institution__name")


@admin.register(IDCardBatch)
class IDCardBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_number", "institution", "status", "generated_by",
        "generated_count", "skipped_count", "generated_at",
    )
    list_filter = ("status", "institution")


@admin.register(IDCard)
class IDCardAdmin(admin.ModelAdmin):
    list_display = (
        "card_number", "student", "institution", "status",
        "issue_date", "expiry_date", "created_at",
    )
    list_filter = ("status", "institution", "issue_date")
    search_fields = (
        "card_number", "student__email",
        "student__first_name", "student__last_name",
    )
    readonly_fields = ("verification_token",)
    raw_id_fields = ("student", "photo", "batch", "template", "replaced_by")


@admin.register(ReplacementRequest)
class ReplacementRequestAdmin(admin.ModelAdmin):
    list_display = ("card", "student", "reason", "status", "requested_at")
    list_filter = ("status", "reason")
    search_fields = ("card__card_number", "student__email")


@admin.register(IDCardAuditLog)
class IDCardAuditLogAdmin(admin.ModelAdmin):
    list_display = ("__str__", "action", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("description", "card__card_number")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False