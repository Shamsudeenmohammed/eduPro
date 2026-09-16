"""Staff allocation / check-in / checkout / transfer-review forms."""

from django import forms
from django.contrib.auth import get_user_model

from academics.models import AcademicSession
from accounts.models import Role
from hostel.models import HostelAllocation, HostelApplication, HostelBed

from .base import Widgets


class AllocationForm(forms.Form):
    """Officer assign a bed to a student for an academic session."""

    student = forms.ModelChoiceField(
        label="Student",
        queryset=get_user_model().objects.filter(role=Role.STUDENT),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    bed = forms.ModelChoiceField(
        label="Bed",
        queryset=HostelBed.objects.filter(
            state=HostelBed.BedState.AVAILABLE
        ).select_related("room__hostel", "room__floor__block").order_by(
            "room__hostel_id", "room__room_number", "bed_number"
        ),
        empty_label="Select an available bed",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    session = forms.ModelChoiceField(
        label="Academic session",
        queryset=AcademicSession.objects.all().order_by("-start_date"),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    application = forms.ModelChoiceField(
        label="Link application (optional)",
        required=False,
        queryset=HostelApplication.objects.filter(
            status=HostelApplication.Status.APPROVED
        ).select_related("student", "room"),
        empty_label="— none —",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    reservation_expires_at = forms.DateTimeField(
        label="Reservation expires at (optional)",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
        help_text="Leave empty to use the policy default.",
    )
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 2})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = (
            self.fields["student"].queryset
            .filter(is_active=True)
            .order_by("last_name", "first_name")
        )

    def clean(self):
        cleaned = super().clean()
        bed = cleaned.get("bed")
        student = cleaned.get("student")
        session = cleaned.get("session")

        if bed and bed.state != HostelBed.BedState.AVAILABLE:
            self.add_error("bed", "This bed is no longer available.")

        if student and session and HostelAllocation.objects.filter(
            student=student, session=session,
            status__in=(HostelAllocation.Status.RESERVED, HostelAllocation.Status.ACTIVE),
        ).exists():
            self.add_error(
                "student",
                "This student already has a live allocation for the selected session.",
            )

        if bed and session and HostelAllocation.objects.filter(
            bed=bed, session=session,
            status__in=(HostelAllocation.Status.RESERVED, HostelAllocation.Status.ACTIVE),
        ).exists():
            self.add_error(
                "bed",
                "This bed is already allocated for the selected session.",
            )
        return cleaned


class CheckInForm(forms.Form):
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 2})
    )


class CheckoutForm(forms.Form):
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 2})
    )


class TransferReviewForm(forms.Form):
    review_note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 3})
    )