"""Shared view helpers."""

import logging

from django.contrib import messages
from django.core.paginator import Paginator

from accounts.models import StaffResponsibility

from accounts.decorators import responsibility_required

from hostel import services

logger = logging.getLogger("eduPro")


def _base_for_user(user):
    if getattr(user, "is_student", False):
        return "students/base.html"
    if getattr(user, "is_admin", False):
        return "admin_base.html"
    return "teachers/base.html"


hostel_staff_required = responsibility_required(
    StaffResponsibility.HOSTEL_OFFICER,
    StaffResponsibility.WARDEN,
)


def _service_message(request, exc):
    """Map a service error to a user-facing flash message."""
    if isinstance(exc, services.HostelServiceError):
        messages.error(request, str(exc))
    else:
        logger.exception("Unexpected hostel error for %s", request.user)
        messages.error(request, "An unexpected error occurred. Please try again.")
    return None


def _page_obj(qs, request, per_page=25):
    paginator = Paginator(qs, per_page)
    return paginator.get_page(request.GET.get("page"))