from django import forms


class Widgets:
    TEXT = forms.TextInput(attrs={"class": "form-control"})
    TEXTAREA = forms.Textarea(attrs={"class": "form-control", "rows": 4})
    SELECT = forms.Select(attrs={"class": "form-control"})
    SELECT2 = forms.Select(attrs={"class": "form-control"})
    DATE = forms.DateInput(attrs={"type": "date", "class": "form-control"})
    DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"})
    NUMBER = forms.NumberInput(attrs={"class": "form-control"})
    CHECKBOX = forms.CheckboxInput(attrs={"class": "form-check-input"})