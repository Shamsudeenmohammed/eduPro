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
from django.utils.translation import gettext_lazy as _

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
    """Authenticates a user by email + password. Rejects inactive accounts."""

    email = forms.EmailField(
        label=_("Email address"),
        max_length=254,
        widget=forms.EmailInput(attrs={
            "autofocus": True,
            "placeholder": "you@example.com",
            "autocomplete": "email",
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
        email    = cleaned.get("email", "").lower().strip()
        password = cleaned.get("password", "")

        if email and password:
            self._user_cache = authenticate(self.request, username=email, password=password)
            if self._user_cache is None:
                raise forms.ValidationError(
                    _("Invalid email or password. Please try again."),
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
