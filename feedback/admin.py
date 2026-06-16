from django.contrib import admin
from .models import Feedback


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("subject", "category", "sentiment", "sentiment_score", "rating", "is_reviewed", "created_at")
    list_filter = ("sentiment", "category", "is_reviewed")
    search_fields = ("subject", "message")
