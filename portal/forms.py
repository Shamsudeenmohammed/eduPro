"""
portal/forms.py

All forms for the admissions portal.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

import json

from .models import (
    AdmissionApplication,
    AdmissionCycle,
    ApplicationType,
    ContactMessage,
    DocumentRequest,
)


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
        self._scope_application_types()

    def _scope_application_types(self):
        """
        Offer only the application types the active cycle actually accepts.

        The model field carries all nine :class:`ApplicationTypeCode` choices,
        which is wrong on the public page: if the cycle is running for
        undergraduate and mature applicants only, an applicant must not be able
        to post ``certificate`` and be believed. Narrowing the choices here also
        makes the browser reject an out-of-list value on its own, and
        :meth:`clean` re-checks on the server.
        """
        from .services import ApplicationTypeCatalog

        allowed = ApplicationTypeCatalog.for_cycle(self._cycle)
        codes = [t.code for t in allowed]
        self.available_type_codes = codes

        field = self.fields["application_type"]
        # Keep the model default out of the narrowed list, otherwise an
        # unrestricted cycle would silently pre-select "undergraduate" for
        # somebody applying for a certificate.
        if field.initial is None and not self.is_bound:
            field.initial = codes[0] if len(codes) == 1 else None
        field.choices = [
            (code, label) for code, label in field.choices if code in codes
        ] or field.choices
        field.widget.choices = field.choices
        # Narrowing the choices means Django's own check rejects an out-of-list
        # value with "Select a valid choice", which means nothing to an
        # applicant. Say what is actually on offer instead.
        field.error_messages = dict(
            field.error_messages,
            invalid_choice=(
                "This intake is not accepting applications for that type. "
                "Please choose: %s."
                % (", ".join(
                    str(dict(field.choices).get(c, c)) for c in codes
                ) or "no application types")
            ),
        )
        # The browser re-renders nothing on its own, so the page needs the full
        # per-programme map to narrow the list as the programme changes.
        field.widget.attrs["data-types-by-program"] = json.dumps(
            self._types_by_program()
        )
        field.widget.attrs["data-types-all"] = json.dumps(codes)

    def _types_by_program(self):
        """
        ``{program_id: [type codes]}`` for every active programme.

        A programme with no entry accepts everything the cycle does, which is
        what a missing entry means in
        :meth:`~portal.services.ApplicationTypeCatalog.for_program`.
        """
        from academics.models import Program

        from .services import ApplicationTypeCatalog

        allowed = {t.code for t in ApplicationTypeCatalog.for_cycle(self._cycle)}
        mapping = {}
        for program in Program.objects.filter(is_active=True).only("id", "program_type"):
            codes = {
                t.code
                for t in ApplicationTypeCatalog.for_program(program)
                if t.is_active
            }
            narrowed = sorted(codes & allowed)
            if narrowed:
                mapping[str(program.pk)] = narrowed
        return mapping

    def clean_application_type(self):
        code = self.cleaned_data.get("application_type")
        if not code:
            return code
        # The cycle limit, checked independently of the widget's choices so a
        # crafted POST cannot slip a type past a narrowed dropdown.
        if self._cycle is not None:
            allowed = set(getattr(self, "available_type_codes", []))
            if allowed and code not in allowed:
                label = str(
                    dict(self.fields["application_type"].choices).get(code, code)
                )
                raise forms.ValidationError(
                    "This intake is not accepting applications for %s." % label
                )
        return code

    def clean(self):
        cleaned = super().clean()
        # The programme limit, which can only be checked once both parts of the
        # pair are known.
        program = cleaned.get("program_applied")
        code = cleaned.get("application_type")
        if program is not None and code:
            from .services import ApplicationTypeCatalog

            if not ApplicationTypeCatalog.is_valid_pair(
                self._cycle, program, code
            ):
                allowed = sorted(
                    t.label
                    for t in ApplicationTypeCatalog.for_program(program)
                    if t.is_active
                )
                cleaned.pop("application_type", None)
                self.add_error(
                    "application_type",
                    "This program does not accept %s applications. It accepts: %s."
                    % (
                        str(
                            dict(self.fields["application_type"].choices).get(code, code)
                        ),
                        ", ".join(str(label) for label in allowed)
                        or "no application types",
                    ),
                )
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

class ApplicationTypeSelectMultiple(forms.SelectMultiple):
    """
    Renders every application type with its machine code and description
    attached as data attributes.

    ``code`` is what staff see as the short note beside the label, and both
    code and description are added to the text selector2 searches, so a type can
    be found by its label, its code or a word from its description.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._by_pk = {}

    def set_types(self, queryset):
        """Cache the rows the option list is being built from."""
        self._by_pk = {str(t.pk): t for t in queryset}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        atype = self._by_pk.get(str(value))
        if atype is not None:
            option["attrs"]["data-note"] = atype.code
            option["attrs"]["data-search"] = " ".join(
                part for part in (atype.code, atype.description) if part
            )
        return option


class AdmissionCycleForm(StyledFieldsMixin, forms.ModelForm):
    # A cycle can recruit for several application types at once, so the types
    # are a multi-select. `AdmissionCycle.application_type` is no longer a
    # form field: it is derived from the chosen set on save (the first type in
    # display order) so staff are never asked the same question twice and the
    # admin filters still have a single value to group on.
    application_types = forms.ModelMultipleChoiceField(
        queryset=ApplicationType.objects.none(),
        required=False,
        widget=ApplicationTypeSelectMultiple,
        label=_("Application types accepted"),
        help_text=_(
            "Choose every application type this cycle accepts. Leave empty to "
            "accept any type."
        ),
    )

    class Meta:
        model  = AdmissionCycle
        fields = [
            "name", "academic_year", "start_date", "end_date",
            "is_active", "max_applications",
            # Added by the admissions upgrade — all optional.
            "academic_session", "application_fee",
            "status", "payment_required_to_progress",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date":   forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "name": forms.TextInput(attrs={"placeholder": "e.g. 2025/2026 Main Intake"}),
            "academic_year": forms.TextInput(attrs={"placeholder": "e.g. 2025/2026"}),
            "application_fee": forms.NumberInput(attrs={
                "step": "0.01", "min": "0",
                "placeholder": "0.00 — leave 0 if there is no application fee",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from academics.models import AcademicSession

        self.fields["academic_session"].queryset = (
            AcademicSession.objects.order_by("-start_date")
        )
        self.fields["academic_session"].empty_label = "— Select academic session —"
        self.fields["academic_session"].required = False

        # Every application type, ordered the way they are shown everywhere
        # else, so the first ticked one is the primary type on save.
        self.application_type_options = ApplicationType.objects.filter(
            is_active=True
        ).order_by("order", "code")
        self.fields["application_types"].queryset = self.application_type_options
        self.fields["application_types"].widget.set_types(self.application_type_options)

        # Opt this field into selector2: searchable, multi-select. The opt-in is
        # a data attribute rather than a class so the styling of the clipped
        # native select is decided by the component, not by the form.
        self.fields["application_types"].widget.attrs.update({
            "data-selector2": "1",
            "data-search-placeholder": "Search application types…",
            "data-search-label": "Search application types",
            "data-placeholder": "Any application type",
            "size": "8",
        })
        if self.instance and self.instance.pk:
            self.fields["application_types"].initial = [
                t.pk for t in self.instance.accepted_types()
            ]

        self.fields["application_fee"].required = False
        self.fields["status"].required = False

    def clean_application_types(self):
        types = self.cleaned_data.get("application_types")
        if not types:
            return ApplicationType.objects.none()
        inactive = types.exclude(is_active=True)
        if inactive.exists():
            raise forms.ValidationError(
                "These application types are no longer active: %s"
                % ", ".join(t.code for t in inactive)
            )
        return types

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and start > end:
            self.add_error(
                "end_date", _("The closing date must be on or after the opening date.")
            )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        # Set the single-value FK from the chosen set before writing, so the
        # row and its join rows are always consistent.
        instance.set_accepted_types(self.cleaned_data.get("application_types"))
        if commit:
            instance.save()
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# APPLICATIONS & ADMISSIONS UPGRADE
# ─────────────────────────────────────────────────────────────────────────────

class ApplicationDraftForm(StyledFieldsMixin, forms.ModelForm):
    """
    Applicant-facing create/edit form for the unified engine.

    Used for BOTH starting an application and editing it, because an
    application is one row whether it is a draft or submitted — only its
    workflow status differs. The view enforces editability server-side via
    :meth:`portal.workflow.ApplicationWorkflow.is_applicant_editable`; this
    form narrows the fields to what the current application type actually
    needs so an undergraduate is not shown postgraduate-only inputs.
    """

    #: Fields collected for every application type.
    COMMON_FIELDS = [
        "first_name", "last_name", "other_names", "date_of_birth", "gender",
        "nationality", "email", "phone", "address",
    ]
    UNDERGRADUATE_FIELDS = [
        "program_applied", "previous_school", "qualification",
        "year_of_completion", "aggregate_score",
    ]
    POSTGRADUATE_FIELDS = [
        "program_applied", "previous_school", "qualification",
        "year_of_completion", "personal_statement",
    ]
    OTHER_FIELDS = [
        "program_applied", "previous_school", "qualification", "year_of_completion",
    ]

    class Meta:
        model = AdmissionApplication
        fields = [
            "first_name", "last_name", "other_names", "date_of_birth", "gender",
            "nationality", "email", "phone", "address", "program_applied",
            "previous_school", "qualification", "year_of_completion",
            "aggregate_score", "personal_statement",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "personal_statement": forms.Textarea(attrs={
                "rows": 6, "placeholder": "Tell us about yourself and why you are applying…",
            }),
            "address": forms.Textarea(attrs={"rows": 3, "placeholder": "Your residential address"}),
            "first_name": forms.TextInput(attrs={"placeholder": "First name"}),
            "last_name":  forms.TextInput(attrs={"placeholder": "Last name"}),
            "other_names": forms.TextInput(attrs={"placeholder": "Middle / other names (optional)"}),
            "email": forms.EmailInput(attrs={"placeholder": "your@email.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "+233 20 000 0000"}),
            "nationality": forms.TextInput(attrs={"placeholder": "e.g. Ghanaian"}),
            "previous_school": forms.TextInput(attrs={
                "placeholder": "School / institution attended",
            }),
            "qualification": forms.TextInput(attrs={"placeholder": "e.g. WASSCE, BSc Computer Science"}),
        }

    def __init__(self, *args, application_type=None, cycle=None,
                 is_new=False, **kwargs):
        super().__init__(*args, **kwargs)
        from academics.models import Program

        self._is_new = is_new
        self._cycle = cycle
        self._type_code = application_type or (
            self.instance.application_type if self.instance.pk else None
        )

        self.fields["program_applied"].queryset = (
            Program.objects.filter(is_active=True)
            .select_related("department__faculty")
            .order_by("department__name", "name")
        )
        self.fields["program_applied"].empty_label = "— Select a programme —"
        self.fields["program_applied"].required = False

        self._apply_type_rules()

    def _apply_type_rules(self):
        """
        Show the inputs that make sense for this application type.

        This is presentation only — the CHECKLIST service independently decides
        what is *required*, so hiding a field can never let an applicant skip a
        requirement.
        """
        from .models import ApplicationTypeCode

        code = self._type_code
        if code == ApplicationTypeCode.POSTGRADUATE:
            keep = set(self.COMMON_FIELDS) | set(self.POSTGRADUATE_FIELDS)
        elif code == ApplicationTypeCode.UNDERGRADUATE:
            keep = set(self.COMMON_FIELDS) | set(self.UNDERGRADUATE_FIELDS)
        else:
            keep = set(self.COMMON_FIELDS) | set(self.OTHER_FIELDS)

        for name in list(self.fields):
            if name not in keep:
                self.fields.pop(name)

        # Postgraduate applicants do not supply an aggregate score.
        if code == ApplicationTypeCode.POSTGRADUATE:
            self.fields.pop("aggregate_score", None)

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            return email
        duplicates = AdmissionApplication.objects.filter(email__iexact=email)
        if self._cycle is not None:
            duplicates = duplicates.filter(cycle=self._cycle)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            # Requirement 10: one application per cycle for this email. A
            # different cycle is always allowed, so legitimate re-application
            # is not blocked.
            raise forms.ValidationError(
                _("An application from this email address already exists for this "
                  "admission cycle. You may still apply in a future cycle, or use the "
                  "status-check page to track your existing application.")
            )
        return email


class ApplicationSubmitForm(StyledFieldsMixin, forms.Form):
    """
    Confirmation + completeness gate before submission.

    Submission is refused while a required item is outstanding, and the missing
    items are returned to the template so the applicant sees exactly what to do
    (scenario 4).
    """

    confirm = forms.BooleanField(
        label=_("I confirm the information given above is correct and complete."),
        required=True,
    )


class ApplicationDocumentUploadForm(StyledFieldsMixin, forms.Form):
    document_type = forms.ChoiceField(label=_("Document type"), choices=[])
    file = forms.FileField(label=_("File"), help_text=_("PDF, JPG or PNG. Maximum 5 MB."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import ApplicationDocumentType
        self.fields["document_type"].choices = [
            ("", "— Select a document —"),
            *ApplicationDocumentType.choices,
        ]

    def clean_file(self):
        from .services import DocumentService
        DocumentService.validate_upload(self.cleaned_data["file"])
        return self.cleaned_data["file"]


class AdmissionDecisionForm(StyledFieldsMixin, forms.Form):
    """Staff recording the admission decision (requirement 6)."""

    decision = forms.ChoiceField(
        label=_("Admission decision"), choices=[],
        help_text=_("This is the academic outcome and is recorded separately from "
                    "the application workflow stage."),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Internal remarks…"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import AdmissionDecision
        self.fields["decision"].choices = [
            (value, label) for value, label in AdmissionDecision.choices
            if value != "pending"
        ]


class AdmissionOfferForm(StyledFieldsMixin, forms.Form):
    """Staff issuing an admission offer (requirement 8)."""

    expiry_date = forms.DateField(
        label=_("Offer valid until"), required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text=_("Defaults to 30 days from today."),
    )
    admission_type = forms.CharField(
        label=_("Admission type"), required=False, initial="full_time",
        widget=forms.TextInput(attrs={"placeholder": "e.g. full_time / part_time"}),
    )
    conditions = forms.CharField(
        label=_("Conditions"), required=False,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "placeholder": "e.g. Must pass all outstanding courses at 50% or above.",
        }),
    )

    def clean_expiry_date(self):
        from django.utils import timezone
        value = self.cleaned_data.get("expiry_date")
        if value and value < timezone.localdate():
            raise forms.ValidationError(_("An offer cannot expire in the past."))
        return value


class OfferResponseForm(StyledFieldsMixin, forms.Form):
    """Applicant accepting or declining an offer."""

    ACCEPT = "accept"
    DECLINE = "decline"
    response = forms.ChoiceField(
        label=_("Response"), choices=[(ACCEPT, _("Accept offer")), (DECLINE, _("Decline offer"))],
    )


class ApplicationPaymentRecordForm(StyledFieldsMixin, forms.ModelForm):
    """Finance recording an application-fee payment (requirement 15)."""

    class Meta:
        from .models import ApplicationPayment
        model = ApplicationPayment
        fields = ["amount", "provider", "transaction_id", "notes"]
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "provider": forms.TextInput(attrs={"placeholder": "e.g. cash, bank transfer, mobile money"}),
            "transaction_id": forms.TextInput(attrs={"placeholder": "Provider reference (optional)"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_amount(self):
        from decimal import Decimal
        value = self.cleaned_data.get("amount")
        if value is None or Decimal(value) <= 0:
            raise forms.ValidationError(_("Enter a payment amount greater than zero."))
        return value


class DocumentVerificationForm(StyledFieldsMixin, forms.Form):
    """Staff verifying, rejecting or requesting a replacement document."""

    VERIFY = "verify"
    REJECT = "reject"
    REPLACE = "replace"
    action = forms.ChoiceField(
        label=_("Action"),
        choices=[
            (VERIFY, _("Verify")),
            (REJECT, _("Reject")),
            (REPLACE, _("Request replacement")),
        ],
    )
    reason = forms.CharField(
        label=_("Reason"), required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Required when rejecting or requesting a replacement."),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") in (self.REJECT, self.REPLACE):
            reason = (cleaned.get("reason") or "").strip()
            if len(reason) < 5:
                self.add_error(
                    "reason", _("Please give a reason of at least 5 characters.")
                )
        return cleaned


class ApplicationCorrectionForm(StyledFieldsMixin, forms.Form):
    """Staff asking an applicant to correct their application."""

    reason = forms.CharField(
        label=_("What needs correcting?"),
        min_length=10,
        widget=forms.Textarea(attrs={
            "rows": 4,
            "placeholder": "Be specific — the applicant sees this message.",
        }),
        help_text=_("Minimum 10 characters. This is shown to the applicant."),
    )
