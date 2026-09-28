"""
portal/permissions.py

Authorisation for the admissions engine (requirements 16 & 22).

eduPro already has a role system, and this module *uses* it rather than
inventing a parallel one:

* ``Role`` (``admin`` / ``teacher`` / ``student``) is untouched.
* Staff authority comes from ``StaffResponsibility`` — admissions officers get
  ``ADMISSIONS_OFFICER``, HODs get ``HOD`` scoped to their departments, and
  finance access is admin-only because eduPro has no bursar role yet (adding
  one is flagged in the implementation report, not smuggled in here).
* Applicants are **not** a new role. An applicant is the owner of an
  application (``application.user_id == request.user.pk``), and an applicant
  who has not yet been converted has no ``academic_profile``. Object-level
  ownership is therefore the permission check, which is what actually prevents
  one applicant reading another's application (requirement 22 / scenario 12).

Every helper returns a plain ``bool`` and is used both to filter querysets
(list views) and to assert on a single object (detail views), so a view cannot
forget the check by forgetting a filter.
"""

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from accounts.models import StaffResponsibility


# ── Who is staff here? ───────────────────────────────────────────────────────

def is_admissions_staff(user):
    """Admin, superuser, or an admissions officer."""
    if not (user and user.is_authenticated):
        return False
    return bool(
        user.is_admin
        or user.is_superuser
        or user.has_responsibility(StaffResponsibility.ADMISSIONS_OFFICER)
    )


def is_finance_staff(user):
    """
    May manage application payments.

    eduPro currently has no bursar/finance responsibility, so this is
    admin-only — exactly matching how the rest of the ``finance`` app is gated
    (``@admin_required``). Deliberately does NOT grant any power over academic
    admission decisions (requirement 16).
    """
    return bool(user and user.is_authenticated and user.is_admin)


def is_hod(user):
    if not (user and user.is_authenticated):
        return False
    if user.is_admin or user.is_superuser:
        return True
    return bool(
        user.has_responsibility(StaffResponsibility.HOD) or user.is_hod
    )


def hod_departments(user):
    """Departments this HOD is authorised to see (empty for non-HODs)."""
    if not (user and user.is_authenticated):
        return None
    if user.is_admin or user.is_superuser:
        return None  # None means "no restriction"
    if is_hod(user):
        return user.get_hod_departments()
    return []


# ── Queryset scoping ─────────────────────────────────────────────────────────

def scope_applications(user, queryset=None):
    """
    Restrict an application queryset to what ``user`` may see.

    * applicant  → only their own applications
    * HOD        → only applications for programmes in their departments
    * admissions → everything
    * anyone else→ nothing
    """
    from .models import AdmissionApplication

    if queryset is None:
        queryset = AdmissionApplication.objects.all()

    if not (user and user.is_authenticated):
        return queryset.none()

    if is_admissions_staff(user):
        return queryset

    if is_hod(user):
        departments = hod_departments(user)
        if departments is None:
            return queryset
        return queryset.filter(
            program_applied__department__in=departments
        ).distinct()

    # Everyone else sees only what they own. Staff roles that are not
    # admissions-related (e.g. a teacher) still cannot browse applications.
    return queryset.filter(user=user)


def visible_application(user, pk):
    """
    Fetch one application or raise ``PermissionDenied``.

    Uses ``scope_applications`` so the detail view and the list view can never
    disagree about who may see what.
    """
    return get_object_or_404(scope_applications(user), pk=pk)


def can_decide(user, application):
    """
    May this user record an admission decision?

    Deliberately narrower than :func:`is_admissions_staff`: an HOD may review
    and shortlist their own department's applications, but the academic
    decision is an admissions-office responsibility.
    """
    return bool(is_admissions_staff(user) and application.program_applied_id)


def can_verify_documents(user, application=None):
    return is_admissions_staff(user) or is_hod(user)


def can_manage_cycles(user):
    """Cycle management is admin-only, matching the existing cycle views."""
    return bool(user and user.is_authenticated and user.is_admin)


def can_manage_payments(user):
    return is_finance_staff(user)


def assert_can_view(user, application):
    if not scope_applications(user).filter(pk=application.pk).exists():
        raise PermissionDenied


def assert_can_manage_documents(user, application):
    if not can_verify_documents(user, application):
        raise PermissionDenied


def assert_is_owner(user, application):
    """
    Object-level ownership check used by every applicant-facing action.
    Returns False rather than raising so callers can pick the right response.
    """
    return bool(
        user
        and user.is_authenticated
        and application.user_id == user.pk
    )


def is_applicant(user, application):
    return assert_is_owner(user, application)
