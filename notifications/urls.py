"""
notifications/urls.py
"""

from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    # Sailup delivery webhook (callbacks, no CSRF)
    path("webhooks/sailup/", views.sailup_webhook, name="sailup_webhook"),
    # Teacher / staff notification centre
    path("centre/", views.teacher_notification_centre, name="centre"),
    path("centre/<int:pk>/read/", views.mark_read, name="mark_read"),
    path("centre/read-all/", views.mark_all_read, name="mark_all_read"),
    # Shared preferences
    path("preferences/", views.preferences, name="preferences"),
    path("unread-count/", views.unread_count_json, name="unread_count"),
]