"""
id_cards/forms.py

Forms used by the id_cards module.  Photo upload is intentionally NOT a
ModelForm — it goes through the validated pipeline in :mod:`id_cards.services`.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import (
    BACK_FIELD_CHOICES,
    FRONT_FIELD_CHOICES,
    DEFAULT_FRONT_FIELDS,
    DEFAULT_BACK_FIELDS,
    IDCardSettings,
    IDCardTemplate,
    ReplacementReason,
)


class TemplateForm(forms.ModelForm):
    front_fields = forms.MultipleChoiceField(
        choices=FRONT_FIELD_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        initial=DEFAULT_FRONT_FIELDS,
        label=_("Front fields"),
        help_text=_("Choose which stable fields appear on the front of the card. "
                    "Academic level can never be added."),
    )
    back_fields = forms.MultipleChoiceField(
        choices=BACK_FIELD_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        initial=DEFAULT_BACK_FIELDS,
        label=_("Back fields"),
    )

    class Meta:
        model = IDCardTemplate
        fields = [
            "name", "status", "is_default",
            "card_width_mm", "card_height_mm",
            "primary_color", "secondary_color", "text_color",
            "background", "watermark", "logo",
            "front_fields", "back_fields",
            "custom_front_text", "custom_back_text",
            "return_instructions", "card_terms",
            "authorized_signature_name", "show_signature_line",
            "validity_years",
        ]
        widgets = {
            "card_width_mm": forms.NumberInput(attrs={"step": "0.01", "min": "50"}),
            "card_height_mm": forms.NumberInput(attrs={"step": "0.01", "min": "30"}),
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "secondary_color": forms.TextInput(attrs={"type": "color"}),
            "text_color": forms.TextInput(attrs={"type": "color"}),
            "card_terms": forms.Textarea(attrs={"rows": 4}),
            "return_instructions": forms.Textarea(attrs={"rows": 3}),
            "custom_front_text": forms.Textarea(attrs={"rows": 2}),
            "custom_back_text": forms.Textarea(attrs={"rows": 2}),
            "status": forms.Select(attrs={"class": "form-control"}),
        }

    def clean_front_fields(self):
        return list(self.cleaned_data["front_fields"])

    def clean_is_default(self):
        is_default = self.cleaned_data.get("is_default")
        if is_default and self.instance.pk:
            current = IDCardTemplate.objects.filter(
                pk=self.instance.pk, status="archived",
            ).exists()
            if current:
                raise forms.ValidationError(
                    _("An archived template cannot be the default template.")
                )
        return is_default


class SettingsForm(forms.ModelForm):
    class Meta:
        model = IDCardSettings
        fields = [
            "validity_years",
            "card_number_prefix",
            "verification_token_length",
            "auto_renewal_enabled",
            "require_photo_approval",
            "allow_student_replacement_requests",
            "verification_requires_login",
        ]
        widgets = {
            "verification_token_length": forms.NumberInput(attrs={"min": "16", "max": "64"}),
            "validity_years": forms.NumberInput(attrs={"min": "1", "max": "10"}),
        }


class ReplacementRequestForm(forms.Form):
    reason = forms.ChoiceField(
        choices=ReplacementReason.choices,
        label=_("Reason"),
    )
    details = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        label=_("Details"),
        required=False,
        help_text=_("Optional details (e.g. where the card was lost)."),
    )


class PhotoUploadForm(forms.Form):
    """Thin wrapper — real validation happens in validators.validate_photo."""

    photo = forms.ImageField(label=_("Passport photograph"))


class RejectionForm(forms.Form):
    reason = forms.CharField(
        label=_("Rejection reason"),
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _(
            "Explain why the photo was rejected so the student can fix it."
        )}),
        max_length=1000,
    )