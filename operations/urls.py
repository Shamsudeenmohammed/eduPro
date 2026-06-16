from django.urls import path
from . import views

app_name = "operations"

urlpatterns = [
    path("announcements/", views.announcement_list, name="announcements"),
    path("announcements/manage/", views.announcement_manage, name="announcement_manage"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("calendar/manage/", views.calendar_manage, name="calendar_manage"),
    path("timetable/", views.timetable_view, name="timetable"),
    path("timetable/manage/", views.timetable_manage, name="timetable_manage"),
    path("tickets/", views.ticket_list, name="ticket_list"),
    path("tickets/new/", views.ticket_create, name="ticket_create"),
    path("tickets/admin/", views.ticket_admin, name="ticket_admin"),
    path("hostel/", views.hostel_list, name="hostel"),
]
