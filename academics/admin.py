"""
academics/admin.py

Django admin configuration for the academics app.

Changes from v1:
- ResultSheetAdmin added with HOD-approval and finalization actions.
- All original registrations retained.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    AcademicSession,
    Course,
    CourseAllocation,
    CourseOffering,
    Department,
    Enrolment,
    Faculty,
    Institution,
    Level,
    Program,
    ResultSheet,
    Semester,
    StudentProfile,
    TeacherDepartment,
)


# ── Inlines ────────────────────────────────────────────────────────────────

class DepartmentInline(admin.TabularInline):
    model = Department
    extra = 0
    fields = ("code", "name", "hod", "is_active")
    show_change_link = True


class ProgramInline(admin.TabularInline):
    model = Program
    extra = 0
    fields = ("code", "name", "program_type", "duration_years", "is_active")
    show_change_link = True


class LevelInline(admin.TabularInline):
    model = Level
    extra = 0
    fields = ("order", "name", "is_active")


class SemesterInline(admin.TabularInline):
    model = Semester
    extra = 0
    fields = ("name", "start_date", "end_date", "is_current")


class CourseOfferingInline(admin.TabularInline):
    model = CourseOffering
    extra = 0
    fields = ("semester", "level", "venue", "max_students", "is_active")
    show_change_link = True


class CourseAllocationInline(admin.TabularInline):
    model = CourseAllocation
    extra = 0
    fields = ("teacher", "role", "is_active")


class EnrolmentInline(admin.TabularInline):
    model = Enrolment
    extra = 0
    fields = ("student", "status", "enrolled_at", "is_active")
    readonly_fields = ("enrolled_at",)


class TeacherDepartmentInline(admin.TabularInline):
    model = TeacherDepartment
    extra = 0
    fields = ("department", "is_primary", "joined_date", "is_active")


# ── Admin actions ──────────────────────────────────────────────────────────

@admin.action(description=_("Finalize selected HOD-approved result sheets"))
def finalize_sheets(modeladmin, request, queryset):
    from django.utils import timezone
    updated = queryset.filter(status=ResultSheet.Status.HOD_APPROVED).update(
        status=ResultSheet.Status.FINALIZED,
        finalized_by=request.user,
        finalized_at=timezone.now(),
    )
    modeladmin.message_user(request, f"{updated} result sheet(s) finalized.")


# ── Admin registrations ────────────────────────────────────────────────────

@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display  = ("name", "short_name", "email", "website", "is_active")
    list_filter   = ("is_active",)
    search_fields = ("name", "short_name")
    inlines       = [DepartmentInline]


@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display  = ("code", "name", "institution", "dean", "is_active")
    list_filter   = ("institution", "is_active")
    search_fields = ("code", "name")
    raw_id_fields = ("dean",)
    inlines       = [DepartmentInline]


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display  = ("code", "name", "faculty", "hod", "is_active")
    list_filter   = ("faculty__institution", "faculty", "is_active")
    search_fields = ("code", "name")
    raw_id_fields = ("hod",)
    inlines       = [ProgramInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("faculty", "hod")


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display  = ("code", "name", "department", "program_type", "duration_years", "is_active")
    list_filter   = ("program_type", "department__faculty", "is_active")
    search_fields = ("code", "name")
    inlines       = [LevelInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("department__faculty")


@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_current")
    list_filter  = ("is_current",)
    search_fields = ("name",)
    inlines       = [SemesterInline]


@admin.register(Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display  = ("__str__", "session", "start_date", "end_date", "is_current")
    list_filter   = ("session", "name", "is_current")
    search_fields = ("session__name",)


@admin.register(Level)
class LevelAdmin(admin.ModelAdmin):
    list_display  = ("name", "program", "order", "is_active")
    list_filter   = ("program__department", "is_active")
    search_fields = ("name", "program__name")
    ordering      = ("program", "order")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display  = ("code", "title", "department", "course_type", "credit_units", "is_active")
    list_filter   = ("course_type", "department__faculty", "department", "is_active")
    search_fields = ("code", "title")
    filter_horizontal = ("prerequisites",)
    inlines       = [CourseOfferingInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("department__faculty")


@admin.register(CourseOffering)
class CourseOfferingAdmin(admin.ModelAdmin):
    list_display  = (
        "course", "semester", "level", "venue", "max_students",
        "enrolled_count_display", "is_active",
    )
    list_filter   = (
        "semester__session", "semester__name",
        "level__program__department", "is_active",
    )
    search_fields = ("course__code", "course__title", "level__name")
    inlines       = [CourseAllocationInline, EnrolmentInline]

    def enrolled_count_display(self, obj):
        return obj.enrolled_count
    enrolled_count_display.short_description = _("Enrolled")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "course", "semester__session", "level__program"
        )


@admin.register(CourseAllocation)
class CourseAllocationAdmin(admin.ModelAdmin):
    list_display  = ("teacher", "offering", "role", "allocated_by", "is_active")
    list_filter   = ("role", "is_active", "offering__semester__session")
    search_fields = (
        "teacher__email", "teacher__first_name", "teacher__last_name",
        "offering__course__code",
    )
    raw_id_fields = ("teacher", "allocated_by")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "teacher", "offering__course", "offering__semester__session"
        )


@admin.register(Enrolment)
class EnrolmentAdmin(admin.ModelAdmin):
    list_display  = ("student", "offering", "status", "enrolled_at", "is_active")
    list_filter   = ("status", "is_active", "offering__semester__session")
    search_fields = (
        "student__email", "student__first_name", "student__last_name",
        "offering__course__code",
    )
    raw_id_fields    = ("student",)
    readonly_fields  = ("enrolled_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "student", "offering__course", "offering__semester__session"
        )


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display  = (
        "student", "student_number", "program", "current_level",
        "cumulative_gpa", "total_credits_earned", "is_active",
    )
    list_filter   = ("program__department", "current_level__program", "is_active")
    search_fields = (
        "student__email", "student__first_name", "student__last_name",
        "student_number",
    )
    raw_id_fields = ("student",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "student", "program__department", "current_level"
        )


@admin.register(TeacherDepartment)
class TeacherDepartmentAdmin(admin.ModelAdmin):
    list_display  = ("teacher", "department", "is_primary", "joined_date", "is_active")
    list_filter   = ("department__faculty", "is_primary", "is_active")
    search_fields = (
        "teacher__email", "teacher__first_name", "teacher__last_name",
        "department__code",
    )
    raw_id_fields = ("teacher",)


@admin.register(ResultSheet)
class ResultSheetAdmin(admin.ModelAdmin):
    list_display  = (
        "offering_code", "department", "submitted_by",
        "status", "submitted_at", "hod_approved_at", "finalized_at",
    )
    list_filter   = (
        "status",
        "department__faculty",
        "department",
        "offering__semester__session",
    )
    search_fields = (
        "offering__course__code",
        "submitted_by__email",
        "submitted_by__first_name",
        "submitted_by__last_name",
    )
    raw_id_fields    = ("submitted_by", "hod_approved_by", "finalized_by")
    readonly_fields  = (
        "submitted_at", "hod_approved_at", "hod_approved_by",
        "finalized_at", "finalized_by", "created_at", "updated_at",
    )
    actions = [finalize_sheets]

    fieldsets = (
        (None, {
            "fields": ("offering", "department", "submitted_by", "status", "notes"),
        }),
        (_("Submission"), {
            "fields": ("submitted_at",),
        }),
        (_("HOD Approval"), {
            "fields": ("hod_approved_by", "hod_approved_at"),
        }),
        (_("Finalization"), {
            "fields": ("finalized_by", "finalized_at"),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    def offering_code(self, obj):
        return obj.offering.course.code
    offering_code.short_description = _("Course")
    offering_code.admin_order_field = "offering__course__code"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "offering__course", "offering__semester__session",
            "department", "submitted_by",
        )
