from django.urls import path
from . import views

app_name = "elearning"

urlpatterns = [
    path("course/<int:offering_pk>/", views.lms_course, name="lms_course"),
    path("course/<int:offering_pk>/manage/", views.lms_manage, name="lms_manage"),
    path("course/<int:offering_pk>/forum/", views.forum_view, name="forum"),
]
