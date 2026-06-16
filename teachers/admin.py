"""
teachers/admin.py
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    AssignmentSubmission,
    Assignment,
    AttendanceRecord,
    AttendanceSheet,
    GradingScheme,
    LectureMaterial,
    Quiz,
    QuizAttempt,
    QuizChoice,
    QuizQuestion,
    ResultSheet,
    StudentResult,
    TeacherProfile,
)


# ── INLINES ─────────────────────────────────────────────────────────────────

class LectureMaterialInline(admin.TabularInline):
    model  = LectureMaterial
    extra  = 0
    fields = ("title", "material_type", "week_number", "is_published", "is_active")


class AssignmentSubmissionInline(admin.TabularInline):
    model  = AssignmentSubmission
    extra  = 0
    fields = ("student", "submitted_at", "score", "is_late", "is_active")
    readonly_fields = ("submitted_at",)


class QuizChoiceInline(admin.TabularInline):
    model  = QuizChoice
    extra  = 2
    fields = ("order", "text", "is_correct")


class QuizQuestionInline(admin.StackedInline):
    model  = QuizQuestion
    extra  = 0
    fields = ("order", "text", "question_type", "marks", "explanation")
    show_change_link = True


class AttendanceRecordInline(admin.TabularInline):
    model  = AttendanceRecord
    extra  = 0
    fields = ("student", "status", "remark")


class StudentResultInline(admin.TabularInline):
    model  = StudentResult
    extra  = 0
    fields = (
        "enrolment", "ca_score", "exam_score",
        "total_score", "grade", "grade_point", "is_absent",
    )
    readonly_fields = ("total_score", "grade", "grade_point")


# ── REGISTRATIONS ────────────────────────────────────────────────────────────

@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display  = (
        "teacher", "staff_id", "rank", "employment_type",
        "specialization", "can_submit_results", "is_active",
    )
    list_filter   = ("rank", "employment_type", "is_active", "can_submit_results")
    search_fields = (
        "teacher__email", "teacher__first_name", "teacher__last_name",
        "staff_id", "specialization",
    )
    raw_id_fields = ("teacher",)
    fieldsets = (
        (None, {"fields": ("teacher", "staff_id", "rank", "employment_type", "joined_date")}),
        (_("Academic"), {"fields": ("specialization", "highest_qualification")}),
        (_("Office"), {"fields": ("office_location", "office_hours")}),
        (_("Permissions"), {"fields": ("can_submit_results", "can_finalise_results", "is_active")}),
    )


@admin.register(LectureMaterial)
class LectureMaterialAdmin(admin.ModelAdmin):
    list_display  = (
        "title", "material_type", "offering_display",
        "week_number", "is_published", "download_count", "is_active",
    )
    list_filter   = ("material_type", "is_published", "is_active")
    search_fields = ("title", "allocation__offering__course__code")

    def offering_display(self, obj):
        return obj.offering.course.code
    offering_display.short_description = _("Course")


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display  = ("title", "offering", "status", "due_date", "total_marks", "submission_count")
    list_filter   = ("status", "is_active", "allow_late")
    search_fields = ("title", "offering__course__code")
    raw_id_fields = ("created_by",)
    inlines       = [AssignmentSubmissionInline]


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display  = (
        "title", "offering", "quiz_type", "duration_minutes",
        "total_marks", "question_count", "is_published",
    )
    list_filter   = ("quiz_type", "is_published", "is_active")
    search_fields = ("title", "offering__course__code")
    raw_id_fields = ("created_by",)
    inlines       = [QuizQuestionInline]


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display  = ("text_short", "quiz", "question_type", "marks", "order")
    list_filter   = ("question_type",)
    inlines       = [QuizChoiceInline]

    def text_short(self, obj):
        return obj.text[:60]
    text_short.short_description = _("Question")


@admin.register(AttendanceSheet)
class AttendanceSheetAdmin(admin.ModelAdmin):
    list_display  = (
        "offering", "date", "week_number", "taken_by",
        "present_count", "total_count",
    )
    list_filter   = ("offering__semester__session", "date")
    search_fields = ("offering__course__code",)
    inlines       = [AttendanceRecordInline]


@admin.register(ResultSheet)
class ResultSheetAdmin(admin.ModelAdmin):
    list_display  = (
        "offering", "grading_scheme", "ca_weight", "exam_weight",
        "status", "submitted_by", "submitted_at",
    )
    list_filter   = ("status", "grading_scheme")
    search_fields = ("offering__course__code",)
    readonly_fields = ("submitted_at", "approved_at")
    inlines        = [StudentResultInline]


@admin.register(StudentResult)
class StudentResultAdmin(admin.ModelAdmin):
    list_display  = (
        "student_name", "course_code", "ca_score", "exam_score",
        "total_score", "grade", "grade_point", "is_absent",
    )
    list_filter   = ("grade", "is_absent")
    search_fields = (
        "enrolment__student__email",
        "enrolment__student__last_name",
        "result_sheet__offering__course__code",
    )
    readonly_fields = ("total_score", "grade", "grade_point")

    def student_name(self, obj):
        return obj.enrolment.student.get_full_name()
    student_name.short_description = _("Student")

    def course_code(self, obj):
        return obj.result_sheet.offering.course.code
    course_code.short_description = _("Course")
