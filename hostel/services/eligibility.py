"""Hostel eligibility service — reusable, view-independent checks."""

from django.utils.translation import gettext_lazy as _

from academics.models import AcademicSession
from hostel.models import HostelAllocation, HostelApplication

from .policy import HostelPolicyService


class HostelEligibilityService:
    LIVE_ALLOCATION_STATUSES = (
        HostelAllocation.Status.RESERVED,
        HostelAllocation.Status.ACTIVE,
    )

    LIVE_APPLICATION_STATUSES = (
        HostelApplication.Status.PENDING,
        HostelApplication.Status.UNDER_REVIEW,
        HostelApplication.Status.APPROVED,
    )

    @classmethod
    def get_session(cls, session=None):
        return session or AcademicSession.get_current()

    @classmethod
    def check_basic_eligibility(cls, student, session=None):
        """Return ``(session, errors)``. An empty list means eligible."""
        errors = []
        session = cls.get_session(session)

        policy = HostelPolicyService.get_policy()
        if policy and policy.require_active_student:
            if not getattr(student, "is_active", False):
                errors.append(_("Your account is not active."))
            if not getattr(student, "is_approved_student", False):
                errors.append(_("You are not an approved student."))
        else:
            if not getattr(student, "is_active", False):
                errors.append(_("Your account is not active."))

        try:
            profile = student.academic_profile
        except Exception:
            profile = None
        if profile is None:
            errors.append(_("You do not have an academic profile."))
        elif not profile.is_active:
            errors.append(_("Your academic record is currently inactive."))

        if session is None:
            errors.append(_("No academic session is currently set for the institution."))

        return session, errors

    @classmethod
    def can_apply(cls, student, session=None):
        session, errors = cls.check_basic_eligibility(student, session)

        if HostelApplication.objects.filter(
            student=student, status__in=cls.LIVE_APPLICATION_STATUSES
        ).exists():
            errors.append(_("You already have a pending hostel application."))

        if session and HostelAllocation.objects.filter(
            student=student, session=session, status__in=cls.LIVE_ALLOCATION_STATUSES
        ).exists():
            errors.append(_("You already have an active hostel allocation for this academic period."))

        if not HostelPolicyService.application_window_open():
            errors.append(_("Hostel applications are currently closed."))

        return errors