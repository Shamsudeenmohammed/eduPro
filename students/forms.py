from django import forms
from django.utils.translation import gettext_lazy as _

from academics.models import CourseOffering
from teachers.models import AssignmentSubmission, QuizAnswer, QuizAttempt

from .models import CourseRegistrationRequest


class StyledMixin:
    CSS = (
        "width:100%;padding:.7rem 1rem;border:1.5px solid #e2e8f0;"
        "border-radius:8px;font-size:.9rem;background:#fafafa;"
        "outline:none;transition:border-color .15s,box-shadow .15s;font-family:inherit;"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if isinstance(f.widget, (forms.CheckboxInput, forms.FileInput,
                                     forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue
            if isinstance(f.widget, forms.Textarea):
                f.widget.attrs.setdefault("rows", 3)
            f.widget.attrs.setdefault("style", self.CSS)


class CourseRegistrationForm(StyledMixin, forms.ModelForm):
    class Meta:
        model  = CourseRegistrationRequest
        fields = ["offering", "reason"]
        widgets = {
            "reason": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": "Optional: provide a reason for this registration request.",
            }),
        }

    def __init__(self, student, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show active offerings the student is not already enrolled in
        from academics.models import Enrolment
        enrolled_pks = Enrolment.objects.filter(
            student=student, is_active=True
        ).values_list("offering_id", flat=True)
        pending_pks = CourseRegistrationRequest.objects.filter(
            student=student, status__in=["pending", "approved"]
        ).values_list("offering_id", flat=True)
        excluded = set(enrolled_pks) | set(pending_pks)
        self.fields["offering"].queryset = (
            CourseOffering.objects.filter(is_active=True)
            .exclude(pk__in=excluded)
            .select_related("course", "semester__session", "level__program")
            .order_by("-semester__session__start_date", "course__code")
        )
        self.fields["offering"].label = "Course Offering"


class AssignmentSubmitForm(StyledMixin, forms.ModelForm):
    class Meta:
        model  = AssignmentSubmission
        fields = ["file", "text_answer"]
        widgets = {
            "text_answer": forms.Textarea(attrs={
                "rows": 6,
                "placeholder": "Type your answer here (if no file upload required).",
            }),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("file") and not cleaned.get("text_answer"):
            raise forms.ValidationError(
                _("Please upload a file or type your answer.")
            )
        return cleaned


class QuizStartForm(forms.Form):
    """Confirmation form before starting a quiz attempt."""
    confirm = forms.BooleanField(
        label=_("I understand the quiz rules and am ready to begin."),
        required=True,
    )


class NotificationMarkReadForm(forms.Form):
    """Mark all notifications read — a simple POST form."""
    pass
