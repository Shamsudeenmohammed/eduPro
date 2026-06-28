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
    path("hostel/apply/", views.hostel_apply, name="hostel_apply"),
    path("hostel/vacate/", views.hostel_vacate, name="hostel_vacate"),
    path("hostel/confirm-payment/<int:pk>/", views.hostel_confirm_payment, name="hostel_confirm_payment"),
    path("hostel/applications/", views.hostel_applications_admin, name="hostel_applications_admin"),
    path("hostel/applications/<int:pk>/approve/", views.hostel_application_approve, name="hostel_application_approve"),
    path("hostel/applications/<int:pk>/reject/", views.hostel_application_reject, name="hostel_application_reject"),
    path("hostel/renew/", views.hostel_renew_allocation, name="hostel_renew_allocation"),
    path("api/hostel-rooms/", views.hostel_rooms_api, name="hostel_rooms_api"),
]
