from django.contrib import admin

from .models import DeliveryWebhook, NotificationPreference, NotificationRecord


@admin.register(NotificationRecord)
class NotificationRecordAdmin(admin.ModelAdmin):
    list_display = ("recipient", "notification_type", "channel", "status", "created_at", "read_at")
    list_filter = ("notification_type", "channel", "status", "priority", "module")
    search_fields = ("recipient__email", "recipient__first_name", "recipient__last_name", "title")
    readonly_fields = ("created_at", "sent_at", "read_at")
    date_hierarchy = "created_at"


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "receive_app", "receive_email", "receive_sms", "updated_at")
    search_fields = ("user__email", "user__first_name", "user__last_name")


@admin.register(DeliveryWebhook)
class DeliveryWebhookAdmin(admin.ModelAdmin):
    list_display = ("event", "status", "signature_valid", "received_at")
    list_filter = ("event", "status", "signature_valid")
    readonly_fields = ("received_at",)