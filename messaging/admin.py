from django.contrib import admin

from .models import Conversation, Message


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("subject", "created_at", "updated_at")
    search_fields = ("subject",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("sender", "recipient", "conversation", "is_read", "created_at")
    list_filter = ("is_read", "created_at")
    search_fields = ("sender__email", "recipient__email", "sender__first_name", "recipient__first_name", "body")
