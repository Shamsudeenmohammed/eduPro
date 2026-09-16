"""Hostel URL configuration — namespace ``hostel``, mounted at ``/hostel/``."""

from django.urls import include, path

from . import staff, student

app_name = "hostel"

urlpatterns = [
    path("", include(student.urlpatterns)),
    path("staff/", include(staff.urlpatterns)),
]