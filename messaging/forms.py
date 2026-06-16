from django import forms
from accounts.models import EduProUser


class ComposeMessageForm(forms.Form):
    recipient = forms.ModelChoiceField(queryset=EduProUser.objects.none())
    subject = forms.CharField(max_length=200, required=False)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            self.fields["recipient"].queryset = EduProUser.objects.exclude(pk=user.pk).filter(is_active=True)
