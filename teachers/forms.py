"""
teachers/forms.py
All forms for the teachers app.
"""

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import (
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSheet,
    LectureMaterial,
    Quiz,
    QuizChoice,
    QuizQuestion,
    ResultSheet,
    StudentResult,
    TeacherProfile,
)


# ── Styling mixin ─────────────────────────────────────────────────────────────

class StyledFieldsMixin:
    FIELD_CSS = (
        "w-full px-4 py-2.5 rounded-lg border border-slate-200 bg-white text-slate-800 "
        "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent "
        "transition duration-150 text-sm"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.FileInput,
                                         forms.CheckboxSelectMultiple)):
                continue
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("rows", 3)
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{self.FIELD_CSS} {existing}".strip()


# ── TEACHER PROFILE ───────────────────────────────────────────────────────────

class TeacherProfileForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = TeacherProfile
        fields = [
            "staff_id", "rank", "employment_type", "specialization",
            "highest_qualification", "office_location", "office_hours", "joined_date",
        ]
        widgets = {
            "staff_id":              forms.TextInput(attrs={"placeholder": "e.g. STF/2024/001"}),
            "specialization":        forms.TextInput(attrs={"placeholder": "e.g. Machine Learning, Algorithms"}),
            "highest_qualification": forms.TextInput(attrs={"placeholder": "e.g. PhD Computer Science"}),
            "office_location":       forms.TextInput(attrs={"placeholder": "e.g. Block A, Room 204"}),
            "office_hours":          forms.TextInput(attrs={"placeholder": "e.g. Mon/Wed 2pm–4pm"}),
            "joined_date":           forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


# ── LECTURE MATERIAL ─────────────────────────────────────────────────────────

class LectureMaterialForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = LectureMaterial
        fields = [
            "title", "material_type", "description", "file",
            "external_url", "week_number", "is_published",
        ]
        widgets = {
            "title":        forms.TextInput(attrs={"placeholder": "Material title"}),
            "description":  forms.Textarea(attrs={"rows": 3, "placeholder": "Brief description (optional)"}),
            "external_url": forms.URLInput(attrs={"placeholder": "https://youtube.com/..."}),
            "week_number":  forms.NumberInput(attrs={"min": 1, "max": 52, "placeholder": "Week #"}),
        }

    def __init__(self, *args, **kwargs):
        self.teacher = kwargs.pop("teacher", None)
        super().__init__(*args, **kwargs)
        if self.teacher:
            from academics.models import CourseAllocation
            self.fields["allocation"] = forms.ModelChoiceField(
                queryset=CourseAllocation.objects.filter(
                    teacher=self.teacher, is_active=True
                ).select_related("offering__course", "offering__semester"),
                label=_("Course"),
            )
            self._meta.fields = ["allocation"] + list(self._meta.fields)
            # Re-apply styling
            f = self.fields["allocation"]
            f.widget.attrs["class"] = self.FIELD_CSS


# ── ASSIGNMENT ────────────────────────────────────────────────────────────────

class AssignmentForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = Assignment
        fields = [
            "title", "instructions", "attachment",
            "total_marks", "due_date", "status", "allow_late",
        ]
        widgets = {
            "title":        forms.TextInput(attrs={"placeholder": "Assignment title"}),
            "instructions": forms.Textarea(attrs={"rows": 5, "placeholder": "Full assignment instructions"}),
            "total_marks":  forms.NumberInput(attrs={"min": 1}),
            "due_date":     forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        self.teacher = kwargs.pop("teacher", None)
        super().__init__(*args, **kwargs)
        if self.teacher:
            from academics.models import CourseOffering, CourseAllocation
            offering_pks = CourseAllocation.objects.filter(
                teacher=self.teacher, is_active=True
            ).values_list("offering_id", flat=True)
            self.fields["offering"] = forms.ModelChoiceField(
                queryset=CourseOffering.objects.filter(
                    pk__in=offering_pks, is_active=True
                ).select_related("course", "semester"),
                label=_("Course Offering"),
            )
            self._meta.fields = ["offering"] + list(self._meta.fields)
            self.fields["offering"].widget.attrs["class"] = self.FIELD_CSS


class AssignmentGradeForm(StyledFieldsMixin, forms.ModelForm):
    """Grade a single student's submission."""
    class Meta:
        model  = AssignmentSubmission
        fields = ["score", "feedback"]
        widgets = {
            "score":    forms.NumberInput(attrs={"min": 0, "step": "0.5"}),
            "feedback": forms.Textarea(attrs={"rows": 3, "placeholder": "Feedback to student"}),
        }


# ── QUIZ ──────────────────────────────────────────────────────────────────────

class QuizForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = Quiz
        fields = [
            "title", "instructions", "quiz_type", "total_marks",
            "duration_minutes", "start_datetime", "end_datetime",
            "randomise_questions", "show_result_immediately",
            "max_attempts", "is_published",
        ]
        widgets = {
            "title":          forms.TextInput(attrs={"placeholder": "Quiz/exam title"}),
            "instructions":   forms.Textarea(attrs={"rows": 3}),
            "total_marks":    forms.NumberInput(attrs={"min": 1}),
            "duration_minutes": forms.NumberInput(attrs={"min": 1, "max": 480}),
            "start_datetime": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "end_datetime":   forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "max_attempts":   forms.NumberInput(attrs={"min": 1}),
        }

    def __init__(self, *args, **kwargs):
        self.teacher = kwargs.pop("teacher", None)
        super().__init__(*args, **kwargs)
        if self.teacher:
            from academics.models import CourseOffering, CourseAllocation
            offering_pks = CourseAllocation.objects.filter(
                teacher=self.teacher, is_active=True
            ).values_list("offering_id", flat=True)
            self.fields["offering"] = forms.ModelChoiceField(
                queryset=CourseOffering.objects.filter(
                    pk__in=offering_pks, is_active=True
                ).select_related("course", "semester"),
                label=_("Course Offering"),
            )
            self._meta.fields = ["offering"] + list(self._meta.fields)
            self.fields["offering"].widget.attrs["class"] = self.FIELD_CSS


class QuizQuestionForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = QuizQuestion
        fields = ["text", "question_type", "marks", "order", "explanation"]
        widgets = {
            "text":        forms.Textarea(attrs={"rows": 3, "placeholder": "Question text"}),
            "explanation": forms.Textarea(attrs={"rows": 2, "placeholder": "Answer explanation (optional)"}),
            "marks":       forms.NumberInput(attrs={"min": 1}),
            "order":       forms.NumberInput(attrs={"min": 1}),
        }


QuizChoiceFormSet = forms.inlineformset_factory(
    QuizQuestion, QuizChoice,
    fields=["order", "text", "is_correct"],
    extra=4, can_delete=True,
    widgets={
        "text":  forms.TextInput(attrs={"placeholder": "Choice text"}),
        "order": forms.NumberInput(attrs={"min": 1, "style": "width:60px"}),
    },
)


# ── ATTENDANCE ────────────────────────────────────────────────────────────────

class AttendanceSheetForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = AttendanceSheet
        fields = ["date", "week_number", "topic_covered", "notes"]
        widgets = {
            "date":          forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "week_number":   forms.NumberInput(attrs={"min": 1, "max": 52}),
            "topic_covered": forms.TextInput(attrs={"placeholder": "Topic covered in this session"}),
            "notes":         forms.Textarea(attrs={"rows": 2, "placeholder": "Optional notes"}),
        }

    def __init__(self, *args, **kwargs):
        self.teacher = kwargs.pop("teacher", None)
        super().__init__(*args, **kwargs)
        if self.teacher:
            from academics.models import CourseOffering, CourseAllocation
            offering_pks = CourseAllocation.objects.filter(
                teacher=self.teacher, is_active=True
            ).values_list("offering_id", flat=True)
            self.fields["offering"] = forms.ModelChoiceField(
                queryset=CourseOffering.objects.filter(
                    pk__in=offering_pks, is_active=True
                ).select_related("course", "semester"),
                label=_("Course Offering"),
            )
            self._meta.fields = ["offering"] + list(self._meta.fields)
            self.fields["offering"].widget.attrs["class"] = self.FIELD_CSS

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > timezone.now().date():
            raise forms.ValidationError(_("Attendance date cannot be in the future."))
        return date


class AttendanceRecordForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = AttendanceRecord
        fields = ["status", "remark"]
        widgets = {
            "remark": forms.TextInput(attrs={"placeholder": "Optional remark"}),
        }


# ── RESULTS ───────────────────────────────────────────────────────────────────

class ResultSheetForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = ResultSheet
        fields = ["grading_scheme", "ca_weight", "exam_weight"]
        widgets = {
            "ca_weight":   forms.NumberInput(attrs={"min": 0, "max": 100}),
            "exam_weight": forms.NumberInput(attrs={"min": 0, "max": 100}),
        }
        help_texts = {
            "ca_weight":   _("CA % (must sum to 100 with exam weight)"),
            "exam_weight": _("Exam % (must sum to 100 with CA weight)"),
        }

    def clean(self):
        cleaned = super().clean()
        ca   = cleaned.get("ca_weight", 0)
        exam = cleaned.get("exam_weight", 0)
        if ca + exam != 100:
            raise forms.ValidationError(_("CA weight + Exam weight must equal 100%."))
        return cleaned


class StudentResultForm(StyledFieldsMixin, forms.ModelForm):
    """Used in the bulk result entry view — one form per enrolled student."""
    class Meta:
        model  = StudentResult
        fields = [
            "ca_test_score", "ca_assignment_score", "ca_quiz_score",
            "ca_score", "exam_score", "is_absent", "remark",
        ]
        widgets = {
            "ca_test_score":       forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.5", "style": "width:80px"}),
            "ca_assignment_score": forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.5", "style": "width:80px"}),
            "ca_quiz_score":       forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.5", "style": "width:80px"}),
            "ca_score":            forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.5", "style": "width:80px"}),
            "exam_score":          forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.5", "style": "width:80px"}),
            "remark":              forms.TextInput(attrs={"placeholder": "Remark", "style": "width:160px"}),
        }


# Inline formset for bulk result entry (one row per enrolment)
StudentResultFormSet = forms.modelformset_factory(
    StudentResult,
    form=StudentResultForm,
    extra=0,
    can_delete=False,
)
