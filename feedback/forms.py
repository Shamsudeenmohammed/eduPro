from django import forms
from .models import Feedback


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = ["category", "subject", "message", "rating", "is_anonymous"]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 5, "class": "form-input"}),
            "rating": forms.NumberInput(attrs={"min": 1, "max": 5, "class": "form-input"}),
        }


class FeedbackResponseForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = ["admin_response", "is_public"]
        widgets = {"admin_response": forms.Textarea(attrs={"rows": 4})}
