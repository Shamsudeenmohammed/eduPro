"""
id_cards/permissions.py

Access-control helpers for the ID card module.

The module does not use Django permissions — it builds on the existing role
system (admin / ID-card officer / HOD) so that no database permissions need to
be configured per institution.
"""

from django.contrib import messages
from django.shortcuts import redirect

from accounts.models import StaffResponsibility
from academics.models import Institution


def current_institution(request):
    """The institution the staff member manages (first institution)."""
    return Institution.objects.first()


def _deny(request, msg):
    messages.error(request, msg)
    return redirect(request.user.get_dashboard_url())


def staff_required(view_func):
    """
    Allow admin or ID-card officer (global), or HOD (scoped to own dept).
    """
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("/accounts/login/?next={}".format(request.path))
        from id_cards.services import is_id_card_staff
        if is_id_card_staff(request.user):
            return view_func(request, *args, **kwargs)
        return _deny(request, "ID card staff access required.")
    return wrapper


def admin_required(view_func):
    """Allow admin and ID-card officers only (they may manage templates)."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("/accounts/login/?next={}".format(request.path))
        from id_cards.services import is_id_card_admin
        if is_id_card_admin(request.user):
            return view_func(request, *args, **kwargs)
        return _deny(request, "ID card admin access required.")
    return wrapper