from django.urls import include, path
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register("users", views.UserViewSet)
router.register("departments", views.DepartmentViewSet)
router.register("courses", views.CourseViewSet)
router.register("offerings", views.CourseOfferingViewSet)
router.register("students", views.StudentProfileViewSet)
router.register("announcements", views.AnnouncementViewSet)
router.register("assignments", views.AssignmentViewSet)
router.register("results", views.StudentResultViewSet)
router.register("attendance-sheets", views.AttendanceSheetViewSet)
router.register("attendance-records", views.AttendanceRecordViewSet)
router.register("feedback", views.FeedbackViewSet)

app_name = "api"

urlpatterns = [
    path("", views.api_root, name="root"),
    path("v1/", include(router.urls)),
    path("v1/stats/", views.stats_api, name="stats"),
]
