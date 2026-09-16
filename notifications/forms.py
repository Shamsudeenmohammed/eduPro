from django import forms

from .models import NotificationPreference


class NotificationPreferenceForm(forms.ModelForm):
    class Meta:
        model = NotificationPreference
        fields = ["receive_app", "receive_email", "receive_sms"]
        help_texts = {
            "receive_sms": "SMS incurs carrier charges — leave off for low-value updates.",
        }