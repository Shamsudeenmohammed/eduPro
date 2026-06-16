from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "model_name", "object_repr", "ip_address")
    list_filter = ("action", "model_name", "created_at")
    search_fields = ("object_repr", "user__email", "path")
    readonly_fields = (
        "user", "action", "model_name", "object_id", "object_repr",
        "changes", "ip_address", "user_agent", "path", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
