from django.contrib import admin

from .models import Forum, ForumPost, LMSModule, LearningResource, LiveClassSession


@admin.register(LMSModule)
class LMSModuleAdmin(admin.ModelAdmin):
    list_display = ("title", "offering", "order", "is_published")
    list_filter = ("is_published",)
    search_fields = ("title", "offering__course__code", "offering__course__title")


@admin.register(LearningResource)
class LearningResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "module", "resource_type", "duration_minutes", "order")
    list_filter = ("resource_type",)
    search_fields = ("title", "module__title", "module__offering__course__code", "module__offering__course__title")


@admin.register(Forum)
class ForumAdmin(admin.ModelAdmin):
    list_display = ("title", "offering", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "offering__course__code", "offering__course__title")


@admin.register(ForumPost)
class ForumPostAdmin(admin.ModelAdmin):
    list_display = ("title", "forum", "author", "is_pinned", "created_at")
    list_filter = ("is_pinned",)
    search_fields = ("title", "content", "author__email", "author__first_name", "author__last_name", "forum__offering__course__code")


@admin.register(LiveClassSession)
class LiveClassSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "offering", "scheduled_at", "duration_minutes", "host", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "meeting_id", "offering__course__code", "offering__course__title", "host__email", "host__first_name")
