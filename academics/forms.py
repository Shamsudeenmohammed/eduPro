"""
academics/forms.py

All forms for the academics app.

Changes from v1:
- ResultSheetForm added: restricts offering choices to the teacher's own
  active allocations.  Department is read-only (derived from offering).
"""

from django import forms
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


# ── Shared mixin ──────────────────────────────────────────────────────────────

class StyledFieldsMixin:
    field_css = (
        "w-full px-4 py-3 rounded-lg border border-slate-200 "
        "bg-white text-slate-800 placeholder-slate-400 "
        "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent "
        "transition duration-150 ease-in-out"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            current = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{self.field_css} {current}".strip()


# ── Institution ───────────────────────────────────────────────────────────────

class InstitutionForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model = Institution
        fields = ["name", "short_name", "logo", "address", "website", "email", "phone", "motto", "is_active"]


class FacultyForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model = Faculty
        fields = ["institution", "name", "code", "dean", "description", "is_active"]


class DepartmentForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = Department
        fields = ["institution", "faculty", "name", "code", "hod", "description", "is_active"]


class ProgramForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = Program
        fields = ["department", "name", "code", "program_type", "duration_years", "total_credits", "description", "is_active"]


class LevelForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = Level
        fields = ["program", "name", "order", "is_active"]


class AcademicSessionForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = AcademicSession
        fields = ["name", "start_date", "end_date", "is_current"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date":   forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class SemesterForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = Semester
        fields = ["session", "name", "start_date", "end_date", "is_current"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date":   forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class CourseForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = Course
        fields = [
            "department", "code", "title", "course_type", "credit_units",
            "lecture_hours_per_week", "lab_hours_per_week",
            "description", "prerequisites", "is_active",
        ]
        widgets = {
            "description":  forms.Textarea(attrs={"rows": 4}),
            "prerequisites": forms.SelectMultiple(),
        }


class CourseOfferingForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = CourseOffering
        fields = ["course", "semester", "level_name", "departments", "venue", "max_students", "is_active"]
        widgets = {
            "departments": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["departments"].queryset = Department.objects.filter(is_active=True)
        self.fields["departments"].label = "Offering Departments"


class CourseAllocationForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = CourseAllocation
        fields = ["offering", "teacher", "role"]


class BulkAllocationForm(StyledFieldsMixin, forms.Form):
    offering = forms.ModelChoiceField(
        queryset=CourseOffering.objects.filter(is_active=True),
        label=_("Course Offering"),
    )
    teachers = forms.ModelMultipleChoiceField(
        queryset=None,
        label=_("Teachers"),
        widget=forms.SelectMultiple(),
    )

    def __init__(self, *args, **kwargs):
        from django.contrib.auth import get_user_model
        super().__init__(*args, **kwargs)
        User = get_user_model()
        self.fields["teachers"].queryset = User.objects.filter(role="teacher", is_active=True)


class EnrolmentForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = Enrolment
        fields = ["student", "offering", "status"]


class BulkEnrolmentForm(StyledFieldsMixin, forms.Form):
    offering = forms.ModelChoiceField(
        queryset=CourseOffering.objects.filter(is_active=True),
        label=_("Course Offering"),
    )
    students = forms.ModelMultipleChoiceField(
        queryset=None,
        label=_("Students"),
        widget=forms.SelectMultiple(),
    )

    def __init__(self, *args, **kwargs):
        from django.contrib.auth import get_user_model
        super().__init__(*args, **kwargs)
        User = get_user_model()
        self.fields["students"].queryset = User.objects.filter(role="student", is_active=True)


class StudentProfileForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = StudentProfile
        fields = [
            "program", "current_level", "student_number",
            "admission_date", "expected_graduation",
            "cumulative_gpa", "total_credits_earned", "is_active",
        ]
        widgets = {
            "admission_date":      forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "expected_graduation": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class TeacherDepartmentForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = TeacherDepartment
        fields = ["teacher", "department", "is_primary", "joined_date", "is_active"]
        widgets = {
            "joined_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


# ── Result Sheet ──────────────────────────────────────────────────────────────

class ResultSheetForm(StyledFieldsMixin, forms.Form):
    """
    Form for a teacher to create a new result sheet.

    The `offering` queryset is restricted to offerings where the requesting
    teacher has an active CourseAllocation, preventing teachers from
    submitting results for courses they don't teach.
    """

    offering = forms.ModelChoiceField(
        queryset=CourseOffering.objects.none(),
        label=_("Course Offering"),
        help_text=_("Only your allocated offerings are shown."),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional notes…"}),
    )

    def __init__(self, teacher, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scope to teacher's own active allocations only
        allocated_offering_ids = (
            CourseAllocation.objects
            .filter(teacher=teacher, is_active=True)
            .values_list("offering_id", flat=True)
        )
        self.fields["offering"].queryset = (
            CourseOffering.objects
            .filter(pk__in=allocated_offering_ids, is_active=True)
            .select_related("course", "semester__session", "level__program")
            .order_by("-semester__session__start_date", "course__code")
        )
