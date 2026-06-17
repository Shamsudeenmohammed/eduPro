"""
portal/forms.py

All forms for the admissions portal.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import AdmissionApplication, AdmissionCycle, ContactMessage, DocumentRequest


# ── Original forms (PRESERVED — used by legacy views) ────────────────────────

class ContactForm(forms.ModelForm):
    """Original contact form — used by portal:contact."""
    class Meta:
        model  = ContactMessage
        fields = ["name", "email", "phone", "subject", "message"]
        widgets = {
            "name":    forms.TextInput(attrs={"class": "form-input", "placeholder": "Your name"}),
            "email":   forms.EmailInput(attrs={"class": "form-input", "placeholder": "you@email.com"}),
            "phone":   forms.TextInput(attrs={"class": "form-input", "placeholder": "Phone (optional)"}),
            "subject": forms.TextInput(attrs={"class": "form-input", "placeholder": "Subject"}),
            "message": forms.Textarea(attrs={"class": "form-input", "rows": 5, "placeholder": "Your message"}),
        }


class AdmissionForm(forms.ModelForm):
    """Original simple admission form — used by legacy portal:admission_apply."""
    class Meta:
        model  = AdmissionApplication
        fields = [
            "first_name", "last_name", "email", "phone", "date_of_birth",
            "program_applied", "previous_school", "qualifications", "documents",
        ]
        widgets = {
            "date_of_birth":  forms.DateInput(attrs={"type": "date", "class": "form-input"}),
            "qualifications": forms.Textarea(attrs={"class": "form-input", "rows": 4}),
        }


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


# ── Public: Application Form ──────────────────────────────────────────────────

class AdmissionApplicationForm(StyledFieldsMixin, forms.ModelForm):
    """
    Public-facing application form for prospective students.
    Creates a user account automatically so the applicant can log in
    and track their application status.

    The cycle is injected via __init__ and is NOT shown as a field.
    """

    password1 = forms.CharField(
        label=_("Create a Password"),
        widget=forms.PasswordInput(attrs={
            "placeholder": "At least 8 characters",
            "autocomplete": "new-password",
        }),
        min_length=8,
    )
    password2 = forms.CharField(
        label=_("Confirm Password"),
        widget=forms.PasswordInput(attrs={
            "placeholder": "Re-enter your password",
            "autocomplete": "new-password",
        }),
        min_length=8,
    )

    class Meta:
        model  = AdmissionApplication
        fields = [
            # Program intent
            "program_applied",
            "application_type",
            # Personal
            "first_name",
            "last_name",
            "other_names",
            "date_of_birth",
            "gender",
            "nationality",
            "email",
            "phone",
            "address",
            # Academic background
            "previous_school",
            "qualification",
            "year_of_completion",
            "aggregate_score",
            # Documents
            "transcript",
            "id_document",
            "passport_photo",
            "personal_statement",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(
                attrs={"type": "date"}, format="%Y-%m-%d"
            ),
            "personal_statement": forms.Textarea(attrs={
                "rows": 6,
                "placeholder": "Tell us about yourself and why you are applying…",
            }),
            "address": forms.Textarea(attrs={"rows": 3, "placeholder": "Your residential address"}),
            "first_name": forms.TextInput(attrs={"placeholder": "First name"}),
            "last_name":  forms.TextInput(attrs={"placeholder": "Last name"}),
            "other_names": forms.TextInput(attrs={"placeholder": "Middle / other names (optional)"}),
            "email": forms.EmailInput(attrs={"placeholder": "your@email.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "+1 555 000 0000"}),
            "nationality": forms.TextInput(attrs={"placeholder": "e.g. Ghanaian"}),
            "previous_school": forms.TextInput(attrs={"placeholder": "Name of previous school / institution"}),
            "qualification": forms.TextInput(attrs={"placeholder": "e.g. WASSCE, A-Levels, HND"}),
        }

    def __init__(self, cycle=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cycle = cycle

        # Scope program choices to active cycle programs only (if determinable)
        from academics.models import Program
        self.fields["program_applied"].queryset = (
            Program.objects.filter(is_active=True)
            .select_related("department__faculty")
            .order_by("department__name", "name")
        )
        self.fields["program_applied"].empty_label = "— Select a program —"
        self.fields["program_applied"].required = False

    def clean_email(self):
        email = self.cleaned_data.get("email", "").lower().strip()
        # Allow re-applications from the same email in different cycles.
        # Uniqueness per cycle is checked below.
        return email

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        if email and self._cycle:
            existing = AdmissionApplication.objects.filter(
                email__iexact=email,
                cycle=self._cycle,
            ).exists()
            if existing:
                raise forms.ValidationError(
                    _(
                        "An application from %(email)s already exists for this cycle. "
                        "Use the status-check page to track your application."
                    ) % {"email": email}
                )
        pw1 = cleaned.get("password1")
        pw2 = cleaned.get("password2")
        if pw1 and pw2 and pw1 != pw2:
            self.add_error("password2", _("Passwords do not match."))
        if email:
            from accounts.models import EduProUser
            existing_user = EduProUser.objects.filter(email=email).first()
            if existing_user and existing_user.role in ("student", "teacher", "admin"):
                self.add_error(
                    "email",
                    _("This email is already registered. Please log in instead."),
                )
        return cleaned

    def save(self, commit=True):
        application = super().save(commit=False)
        application.email = application.email.lower().strip()
        if commit:
            application.save()
        return application


# ── Public: Status Check ──────────────────────────────────────────────────────

class ApplicationStatusCheckForm(StyledFieldsMixin, forms.Form):
    reference_number = forms.CharField(
        label=_("Reference Number"),
        max_length=30,
        widget=forms.TextInput(attrs={
            "placeholder": "APP-2025-XXXXXXXX",
            "autocomplete": "off",
        }),
    )
    email = forms.EmailField(
        label=_("Email Address"),
        widget=forms.EmailInput(attrs={"placeholder": "you@example.com"}),
    )

    def clean_reference_number(self):
        return self.cleaned_data["reference_number"].strip().upper()

    def clean_email(self):
        return self.cleaned_data["email"].lower().strip()


# ── Staff: Reject Form ────────────────────────────────────────────────────────

class ApplicationRejectForm(StyledFieldsMixin, forms.Form):
    rejection_reason = forms.CharField(
        label=_("Reason for Rejection"),
        widget=forms.Textarea(attrs={
            "rows": 4,
            "placeholder": "Please provide a clear reason for the rejection…",
        }),
        min_length=20,
        help_text=_("Minimum 20 characters. This may be shared with the applicant."),
    )


# ── Staff: Review Notes Form ──────────────────────────────────────────────────

class ApplicationReviewForm(StyledFieldsMixin, forms.Form):
    review_notes = forms.CharField(
        label=_("Review Notes"),
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 4,
            "placeholder": "Internal notes for the admissions team (not shown to applicant)…",
        }),
    )


# ── Staff: Document Request Form ──────────────────────────────────────────────

class DocumentRequestForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = DocumentRequest
        fields = ["document_name", "instructions"]
        widgets = {
            "document_name": forms.TextInput(attrs={
                "placeholder": "e.g. Official University Transcript"
            }),
            "instructions": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": "Please upload a certified copy of…",
            }),
        }


# ── Admin: Admission Cycle Form ───────────────────────────────────────────────

class AdmissionCycleForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = AdmissionCycle
        fields = [
            "name", "academic_year", "start_date", "end_date",
            "is_active", "max_applications",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date":   forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "name": forms.TextInput(attrs={"placeholder": "e.g. 2025/2026 Main Intake"}),
            "academic_year": forms.TextInput(attrs={"placeholder": "e.g. 2025/2026"}),
        }
