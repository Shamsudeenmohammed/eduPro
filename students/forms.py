from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _

from academics.models import CourseOffering
from teachers.models import AssignmentSubmission, QuizAnswer, QuizAttempt

from accounts.models import EduProUser
from .models import CourseRegistrationRequest


class StudentProfileEditForm(forms.ModelForm):
    class Meta:
        model  = EduProUser
        fields = ["email"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        qs = EduProUser.objects.filter(email=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                _("Another account is already using this email address.")
            )
        return email


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
        from academics.models import Enrolment

        enrolled_pks = Enrolment.objects.filter(
            student=student, is_active=True
        ).values_list("offering_id", flat=True)
        pending_pks = CourseRegistrationRequest.objects.filter(
            student=student, status__in=["pending", "approved"]
        ).values_list("offering_id", flat=True)
        excluded = set(enrolled_pks) | set(pending_pks)

        qs = CourseOffering.objects.filter(is_active=True).exclude(pk__in=excluded)

        try:
            profile = student.academic_profile
            if profile.program and profile.current_level:
                qs = qs.filter(
                    departments=profile.program.department,
                    level_name=profile.current_level.name,
                )
        except ObjectDoesNotExist:
            pass

        # ── Retake: include offerings for courses the student has failed ──────
        from teachers.models import StudentResult
        failed_course_ids = (
            StudentResult.objects
            .filter(
                enrolment__student=student,
                grade="F",
                enrolment__is_active=True,
            )
            .values_list("enrolment__offering__course_id", flat=True)
            .distinct()
        )
        if failed_course_ids:
            retake_offerings = CourseOffering.objects.filter(
                is_active=True,
                course_id__in=failed_course_ids,
                level_name__in=["100", "200", "300", "400"],
            ).exclude(pk__in=enrolled_pks | set(pending_pks))
            qs = qs | retake_offerings

        self.fields["offering"].queryset = (
            qs.select_related("course", "semester__session", "level__program")
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
