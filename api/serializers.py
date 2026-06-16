from rest_framework import serializers

from academics.models import Course, CourseOffering, Department, Program, StudentProfile
from accounts.models import EduProUser
from feedback.models import Feedback
from operations.models import Announcement
from teachers.models import Assignment, AttendanceRecord, AttendanceSheet, StudentResult


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = EduProUser
        fields = ["id", "email", "first_name", "last_name", "full_name", "role", "is_active", "date_joined"]
        read_only_fields = ["id", "date_joined"]


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "code", "faculty", "is_active"]


class CourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = ["id", "code", "title", "credit_units", "course_type", "department"]


class CourseOfferingSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)

    class Meta:
        model = CourseOffering
        fields = ["id", "course", "course_code", "course_title", "semester", "level", "venue", "max_students"]


class StudentProfileSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.get_full_name", read_only=True)

    class Meta:
        model = StudentProfile
        fields = [
            "id", "student", "student_name", "student_number", "program",
            "current_level", "cumulative_gpa", "total_credits_earned",
        ]


class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Announcement
        fields = ["id", "title", "content", "priority", "is_pinned", "created_at"]


class AssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = ["id", "title", "offering", "due_date", "total_marks", "status"]


class StudentResultSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="enrolment.student.get_full_name", read_only=True)
    course_code = serializers.CharField(source="enrolment.offering.course.code", read_only=True)

    class Meta:
        model = StudentResult
        fields = [
            "id", "student_name", "course_code", "ca_score", "exam_score",
            "total_score", "grade", "grade_point",
        ]


class FeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feedback
        fields = ["id", "category", "subject", "message", "rating", "sentiment", "sentiment_score", "created_at"]
        read_only_fields = ["sentiment", "sentiment_score"]


class AttendanceSheetSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="offering.course.code", read_only=True)

    class Meta:
        model = AttendanceSheet
        fields = ["id", "offering", "course_code", "date", "week_number", "topic_covered", "present_count", "total_count"]
        read_only_fields = ["present_count", "total_count"]


class AttendanceRecordSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.get_full_name", read_only=True)
    course_code = serializers.CharField(source="sheet.offering.course.code", read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = ["id", "sheet", "student", "student_name", "course_code", "status", "remarks"]
