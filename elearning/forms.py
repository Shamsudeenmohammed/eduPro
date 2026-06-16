from django import forms
from .models import ForumPost, LMSModule, LiveClassSession


class LMSModuleForm(forms.ModelForm):
    class Meta:
        model = LMSModule
        fields = ["title", "description", "order", "is_published"]


class LiveClassForm(forms.ModelForm):
    class Meta:
        model = LiveClassSession
        fields = ["title", "scheduled_at", "duration_minutes", "meeting_url", "meeting_id"]
        widgets = {"scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class ForumPostForm(forms.ModelForm):
    class Meta:
        model = ForumPost
        fields = ["title", "content"]
        widgets = {"content": forms.Textarea(attrs={"rows": 4})}
