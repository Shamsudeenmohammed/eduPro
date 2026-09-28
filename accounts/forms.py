"""
accounts/forms.py

All authentication and profile forms for eduPro.

Changes from v1:
- RegisterForm replaced by PendingRegistrationForm:
    * Forces is_active=False on save (inactive-until-approved).
    * Removes role field from public form (staff only, default TEACHER).
    * Does NOT log the user in after save.
- UserStaffRoleForm: admin assigns fine-grained responsibilities.
"""

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import PasswordResetForm as DjangoPasswordResetForm
from django.contrib.auth.forms import SetPasswordForm as DjangoSetPasswordForm
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from academics.models import Program, StudentProfile

from .models import EduProUser, Role, UserProfile, UserStaffRole, StaffResponsibility


# ── Shared widget mixin ───────────────────────────────────────────────────────

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


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginForm(StyledFieldsMixin, forms.Form):
    """Authenticates a user by student ID, staff ID, or email + password."""

    identifier = forms.CharField(
        label=_("Student ID / Staff ID"),
        max_length=254,
        widget=forms.TextInput(attrs={
            "autofocus": True,
            "placeholder": "Student ID or Staff ID",
            "autocomplete": "username",
        }),
    )
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={
            "placeholder": "••••••••",
            "autocomplete": "current-password",
        }),
    )
    remember_me = forms.BooleanField(
        label=_("Keep me signed in"),
        required=False,
        widget=forms.CheckboxInput(attrs={
            "class": (
                "h-4 w-4 rounded border-slate-300 text-indigo-600 "
                "focus:ring-indigo-500 cursor-pointer"
            )
        }),
    )

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self._user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        identifier = cleaned.get("identifier", "").strip()
        password   = cleaned.get("password", "")

        if identifier and password:
            self._user_cache = authenticate(self.request, username=identifier, password=password)
            if self._user_cache is None:
                raise forms.ValidationError(
                    _("Invalid credentials. Please try again."),
                    code="invalid_login",
                )
            if not self._user_cache.is_active:
                raise forms.ValidationError(
                    _(
                        "Your account is pending approval. "
                        "You will receive an email once it is activated by an administrator."
                    ),
                    code="inactive",
                )
        return cleaned

    def get_user(self):
        return self._user_cache


# ── Staff Registration (pending approval) ────────────────────────────────────

class PendingRegistrationForm(StyledFieldsMixin, forms.ModelForm):
    """
    Staff self-registration form.

    The created account is always inactive (is_active=False) and must be
    explicitly approved by an admin before the user can log in.

    Admission applicants should use portal.AdmissionApplication instead.
    """

    password1 = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={
            "placeholder": "Create a password",
            "autocomplete": "new-password",
        }),
        min_length=8,
        help_text=_("Minimum 8 characters."),
    )
    password2 = forms.CharField(
        label=_("Confirm password"),
        widget=forms.PasswordInput(attrs={
            "placeholder": "Repeat your password",
            "autocomplete": "new-password",
        }),
    )

    class Meta:
        model  = EduProUser
        fields = ["first_name", "last_name", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "First name", "autocomplete": "given-name"}),
            "last_name":  forms.TextInput(attrs={"placeholder": "Last name",  "autocomplete": "family-name"}),
            "email":      forms.EmailInput(attrs={"placeholder": "you@institution.edu", "autocomplete": "email"}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if EduProUser.objects.filter(email=email).exists():
            raise forms.ValidationError(
                _("An account with this email already exists.")
            )
        return email

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1", "")
        p2 = self.cleaned_data.get("password2", "")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError(_("The two passwords do not match."))
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.email     = user.email.lower().strip()
        user.role      = Role.TEACHER     # default role for staff registrations
        user.is_active = False            # ← always inactive until admin approves
        if commit:
            user.save()
        return user


# ── Profile ───────────────────────────────────────────────────────────────────

class ProfileForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = UserProfile
        fields = ["avatar", "bio", "phone", "department", "date_of_birth"]
        widgets = {
            "bio": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "Tell us a little about yourself…",
            }),
            "phone":         forms.TextInput(attrs={"placeholder": "+1 555 000 0000"}),
            "department":    forms.TextInput(attrs={"placeholder": "e.g. Computer Science"}),
            "date_of_birth": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "avatar":        forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.date_of_birth:
            self.fields["date_of_birth"].widget.attrs["value"] = (
                self.instance.date_of_birth.strftime("%Y-%m-%d")
            )


class UserInfoForm(StyledFieldsMixin, forms.ModelForm):
    class Meta:
        model  = EduProUser
        fields = ["first_name", "last_name", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "First name"}),
            "last_name":  forms.TextInput(attrs={"placeholder": "Last name"}),
            "email":      forms.EmailInput(attrs={"placeholder": "you@example.com"}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        qs = EduProUser.objects.filter(email=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                _("Another account is already using this email address.")
            )
        return email


# ── Bulk Student Upload ───────────────────────────────────────────────────────

class BulkStudentUploadForm(forms.Form):
    csv_file = forms.FileField(
        label=_("CSV File"),
        help_text=_("Upload a CSV file with columns: first_name, last_name, email (optional), program_code (optional)."),
        widget=forms.ClearableFileInput(attrs={
            "accept": ".csv",
            "class": (
                "block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 "
                "file:rounded-lg file:border-0 file:text-sm file:font-semibold "
                "file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100"
            ),
        }),
    )

    def clean_csv_file(self):
        f = self.cleaned_data["csv_file"]
        if not f.name.endswith(".csv"):
            raise forms.ValidationError(_("Only CSV files are supported."))
        import csv, io
        decoded = f.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded))
        rows = list(reader)
        if not rows:
            raise forms.ValidationError(_("CSV file is empty."))
        required = {"first_name", "last_name"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise forms.ValidationError(
                _("Missing required columns: %(cols)s.") % {"cols": ", ".join(missing)}
            )
        self._rows = rows
        self._fieldnames = reader.fieldnames
        return f

    @property
    def rows(self):
        return getattr(self, "_rows", [])

    @property
    def fieldnames(self):
        return getattr(self, "_fieldnames", [])


# ── Staff Role Assignment ─────────────────────────────────────────────────────

class UserStaffRoleForm(StyledFieldsMixin, forms.ModelForm):
    """
    Admin form to grant a StaffResponsibility to a user.
    The `department` field is required when responsibility=HOD.
    """

    class Meta:
        model  = UserStaffRole
        fields = ["responsibility", "department"]

    def clean(self):
        cleaned = super().clean()
        responsibility = cleaned.get("responsibility")
        department     = cleaned.get("department")

        if responsibility == StaffResponsibility.HOD and not department:
            raise forms.ValidationError(
                _("A department must be specified when assigning the HOD responsibility.")
            )
        return cleaned


# ── Password Reset ────────────────────────────────────────────────────────────

class PasswordResetForm(StyledFieldsMixin, DjangoPasswordResetForm):
    pass


class SetPasswordForm(StyledFieldsMixin, DjangoSetPasswordForm):
    pass


# ── Admin User Creation ────────────────────────────────────────────────────────

class AdminUserCreationForm(StyledFieldsMixin, forms.ModelForm):
    """
    Admin form to create a new user with full control over role and active status.

    A student is a complete academic record the moment the account exists, so
    this form also does the work that used to be a separate manual step: it
    assigns the student ID number and the default password, and builds the
    StudentProfile with program, admission date and starting level. Creating a
    student here needs no application or second approval pass.
    """

    password1 = forms.CharField(
        label=_("Password"),
        required=False,
        widget=forms.PasswordInput(attrs={
            "placeholder": "Create a password",
            "autocomplete": "new-password",
        }),
        min_length=8,
        help_text=_("Minimum 8 characters. Leave blank for a student to get the default password."),
    )
    password2 = forms.CharField(
        label=_("Confirm password"),
        required=False,
        widget=forms.PasswordInput(attrs={
            "placeholder": "Repeat your password",
            "autocomplete": "new-password",
        }),
    )
    program = forms.ModelChoiceField(
        label=_("Program"),
        required=False,
        queryset=Program.objects.filter(is_active=True).select_related("department"),
        help_text=_("Students only. Sets the student ID prefix and starting level."),
    )

    class Meta:
        model  = EduProUser
        fields = ["first_name", "last_name", "email", "role", "is_active"]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "First name", "autocomplete": "given-name"}),
            "last_name":  forms.TextInput(attrs={"placeholder": "Last name",  "autocomplete": "family-name"}),
            "email":      forms.EmailInput(attrs={"placeholder": "you@institution.edu", "autocomplete": "email"}),
            "role":       forms.Select(),
            "is_active":  forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        # The admin creating the account is the approver of record, so the
        # audit trail is filled in rather than left blank.
        self.actor = actor
        # Staff passwords are the admin's responsibility; a student's are not.
        if not self.is_bound:
            self._apply_role_rules(Role.STUDENT)

    def _is_student(self):
        role = self.data.get("role") if self.is_bound else (
            self.initial.get("role") or Role.STUDENT
        )
        return role == Role.STUDENT

    def _apply_role_rules(self, role):
        """Make the password boxes mandatory only for staff."""
        self.fields["password1"].required = role != Role.STUDENT
        self.fields["password2"].required = role != Role.STUDENT

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role") or Role.STUDENT
        self._apply_role_rules(role)

        p1 = cleaned.get("password1") or ""
        p2 = self.data.get("password2") or ""

        if role != Role.STUDENT:
            # Staff must be given a real password: there is no default to fall
            # back on, and a blank one would lock the account out entirely.
            if not p1:
                self.add_error("password1", _("A password is required for teachers and admins."))
            elif p1 != p2:
                self.add_error("password2", _("The two passwords do not match."))
        elif p1 and p1 != p2:
            self.add_error("password2", _("The two passwords do not match."))

        if role == Role.STUDENT:
            # A student's typed password is discarded in favour of the default,
            # so any error on those fields (minimum length, for instance) would
            # block a save over a value that is never used. Clear them.
            for name in ("password1", "password2"):
                self._errors.pop(name, None)

        return cleaned

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if EduProUser.objects.filter(email=email).exists():
            raise forms.ValidationError(
                _("An account with this email already exists.")
            )
        return email

    def clean_password2(self):
        # Validated against password1 in clean(); returning None here stops the
        # field's own "this field is required" message firing for students.
        return self.data.get("password2") or None

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = user.email.lower().strip()

        is_student = user.role == Role.STUDENT
        if is_student:
            # Students always land on the default password, whatever was typed
            # into the optional fields, so every new student can be handed
            # working credentials on the spot.
            user.set_default_password()
            user.is_active = True   # a manually added student skips the queue
        else:
            user.set_password(self.cleaned_data.get("password1") or "")

        if self.actor is not None:
            user.approved_by = self.actor
            user.approved_at = timezone.now()

        if not commit:
            return user

        user.save()

        if is_student:
            # Gives the student an ID number, admission date and starting level
            # in the same transaction, so the account is immediately usable for
            # enrolment, billing and ID cards.
            profile, _ = StudentProfile.ensure_for_student(
                user, program=self.cleaned_data.get("program")
            )
            self.created_profile = profile

        return user

