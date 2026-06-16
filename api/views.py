from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from academics.models import Course, CourseOffering, Department, StudentProfile
from accounts.models import EduProUser
from feedback.models import Feedback
from operations.models import Announcement
from teachers.models import Assignment, AttendanceRecord, AttendanceSheet, StudentResult

from .serializers import (
    AnnouncementSerializer,
    AssignmentSerializer,
    AttendanceRecordSerializer,
    AttendanceSheetSerializer,
    CourseOfferingSerializer,
    CourseSerializer,
    DepartmentSerializer,
    FeedbackSerializer,
    StudentProfileSerializer,
    StudentResultSerializer,
    UserSerializer,
)
from analytics.services import get_platform_stats


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EduProUser.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["role", "is_active"]
    search_fields = ["email", "first_name", "last_name"]


class DepartmentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Department.objects.filter(is_active=True)
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated]


class CourseViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Course.objects.filter(is_active=True)
    serializer_class = CourseSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ["code", "title"]


class CourseOfferingViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CourseOffering.objects.filter(is_active=True).select_related("course", "semester", "level")
    serializer_class = CourseOfferingSerializer
    permission_classes = [IsAuthenticated]


class StudentProfileViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = StudentProfile.objects.filter(is_active=True).select_related("student", "program")
    serializer_class = StudentProfileSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ["student_number", "student__email"]


class AnnouncementViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Announcement.objects.filter(is_active=True)
    serializer_class = AnnouncementSerializer
    permission_classes = [IsAuthenticated]


class AssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Assignment.objects.filter(is_active=True)
    serializer_class = AssignmentSerializer
    permission_classes = [IsAuthenticated]


class StudentResultViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = StudentResult.objects.select_related(
        "enrolment__student", "enrolment__offering__course"
    )
    serializer_class = StudentResultSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["enrolment__student", "enrolment__offering", "grade"]
    search_fields = ["enrolment__student__email", "enrolment__offering__course__code"]


class AttendanceSheetViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AttendanceSheet.objects.filter(is_active=True).select_related(
        "offering__course", "taken_by"
    )
    serializer_class = AttendanceSheetSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["offering", "date"]
    ordering_fields = ["date"]


class AttendanceRecordViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AttendanceRecord.objects.select_related(
        "student", "sheet__offering__course"
    )
    serializer_class = AttendanceRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["student", "sheet", "status"]
    search_fields = ["student__email", "sheet__offering__course__code"]


class FeedbackViewSet(viewsets.ModelViewSet):
    queryset = Feedback.objects.all()
    serializer_class = FeedbackSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        fb = serializer.save(user=self.request.user)
        from feedback.services import classify_feedback
        classify_feedback(fb)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def stats_api(request):
    if not getattr(request.user, "is_admin", False):
        return Response({"detail": "Admin only."}, status=403)
    return Response(get_platform_stats())


@api_view(["GET"])
def api_root(request):
    return Response({
        "name": "eduPro Enterprise API",
        "version": "2.0",
        "endpoints": {
            "users": "/api/v1/users/",
            "departments": "/api/v1/departments/",
            "courses": "/api/v1/courses/",
            "offerings": "/api/v1/offerings/",
            "students": "/api/v1/students/",
            "announcements": "/api/v1/announcements/",
            "assignments": "/api/v1/assignments/",
            "results": "/api/v1/results/",
            "attendance_sheets": "/api/v1/attendance-sheets/",
            "attendance_records": "/api/v1/attendance-records/",
            "feedback": "/api/v1/feedback/",
            "stats": "/api/v1/stats/",
        },
    })
