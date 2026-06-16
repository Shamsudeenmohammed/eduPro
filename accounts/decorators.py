"""
accounts/decorators.py

Access-control decorators for eduPro ERP.

Hierarchy
---------
- anonymous_required  : blocks authenticated users (login/register pages)
- login_required      : standard Django decorator (re-exported for convenience)
- active_required     : user must be active (approved)
- role_required       : one or more primary roles
- admin_required      : primary role == ADMIN (or superuser)
- teacher_required    : primary role == TEACHER (or admin/superuser)
- student_required    : primary role == STUDENT
- responsibility_required : holds one of the given StaffResponsibilities
- hod_required        : HOD of *any* department (for general HOD pages)
- hod_of_dept_required: HOD of a *specific* department (view-level kwarg)

All decorators redirect unauthenticated users to login and send authenticated
but unauthorized users back to their role dashboard with a clear message.
"""

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

from .models import Role, StaffResponsibility


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _login_redirect(request):
    return redirect(f"/accounts/login/?next={request.path}")


def _deny(request, msg=_("You do not have permission to access that page.")):
    messages.error(request, msg)
    return redirect(request.user.get_dashboard_url())


# ─────────────────────────────────────────────────────────────────────────────
# ANONYMOUS / PUBLIC GATE
# ─────────────────────────────────────────────────────────────────────────────

def anonymous_required(redirect_url=None):
    """Redirect authenticated users away from login/register pages."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated:
                target = redirect_url or request.user.get_dashboard_url()
                return redirect(target)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVE ACCOUNT GATE
# ─────────────────────────────────────────────────────────────────────────────

def active_required(view_func):
    """
    Ensure the user's account has been approved (is_active=True).
    Inactive users are logged out and sent back to login with an explanation.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _login_redirect(request)
        if not request.user.is_active:
            from django.contrib.auth import logout
            logout(request)
            messages.warning(
                request,
                _(
                    "Your account is pending approval. "
                    "You will receive an email once it is activated."
                ),
            )
            return redirect("accounts:login")
        return view_func(request, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# PRIMARY ROLE DECORATORS
# ─────────────────────────────────────────────────────────────────────────────

def role_required(*roles):
    """Allow access only to users whose primary role is in `roles`."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return _login_redirect(request)
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            if request.user.role not in roles:
                return _deny(request)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def admin_required(view_func):
    """Require ADMIN primary role (or superuser)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _login_redirect(request)
        if request.user.is_superuser or request.user.role == Role.ADMIN:
            return view_func(request, *args, **kwargs)
        return _deny(request, _("Administrator access required."))
    return wrapper


def teacher_required(view_func):
    """Require TEACHER or ADMIN primary role (or superuser)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _login_redirect(request)
        if request.user.is_superuser or request.user.role in (Role.TEACHER, Role.ADMIN):
            return view_func(request, *args, **kwargs)
        return _deny(request, _("Teacher access required."))
    return wrapper


def student_required(view_func):
    """Require STUDENT primary role (or superuser)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _login_redirect(request)
        if request.user.is_superuser or request.user.role == Role.STUDENT:
            return view_func(request, *args, **kwargs)
        return _deny(request, _("Student access required."))
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-RESPONSIBILITY DECORATORS
# ─────────────────────────────────────────────────────────────────────────────

def responsibility_required(*responsibilities):
    """
    Require the user to hold at least one of the given StaffResponsibilities.
    Admin / superuser always bypass.

    Usage::

        @responsibility_required(StaffResponsibility.HOD, StaffResponsibility.DEAN)
        def some_view(request): ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return _login_redirect(request)
            if request.user.is_superuser or request.user.role == Role.ADMIN:
                return view_func(request, *args, **kwargs)
            user_resp = set(request.user.get_active_responsibilities())
            if user_resp.intersection(set(responsibilities)):
                return view_func(request, *args, **kwargs)
            return _deny(
                request,
                _(
                    "You need one of the following responsibilities to access "
                    "this page: %(resp)s."
                ) % {"resp": ", ".join(responsibilities)},
            )
        return wrapper
    return decorator


def hod_required(view_func):
    """
    Require the user to be HOD of at least one department.
    Admin / superuser bypass.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _login_redirect(request)
        if request.user.is_superuser or request.user.role == Role.ADMIN:
            return view_func(request, *args, **kwargs)
        if request.user.has_responsibility(StaffResponsibility.HOD):
            return view_func(request, *args, **kwargs)
        return _deny(request, _("Head of Department access required."))
    return wrapper


def hod_of_dept_required(dept_kwarg: str = "dept_pk"):
    """
    Require the user to be HOD of the department identified by a URL kwarg.

    Usage::

        @hod_of_dept_required(dept_kwarg="dept_pk")
        def approve_results(request, dept_pk): ...

    The decorator fetches the Department by pk from the URL kwargs and checks
    whether the current user holds HOD responsibility for it.
    Admin / superuser bypass.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return _login_redirect(request)
            if request.user.is_superuser or request.user.role == Role.ADMIN:
                return view_func(request, *args, **kwargs)

            from academics.models import Department
            from django.shortcuts import get_object_or_404

            dept_pk = kwargs.get(dept_kwarg)
            if dept_pk is None:
                return _deny(request, _("Department not specified."))

            department = get_object_or_404(Department, pk=dept_pk)
            if request.user.is_hod_of(department):
                return view_func(request, *args, **kwargs)

            return _deny(
                request,
                _(
                    "You are not the Head of Department for %(dept)s."
                ) % {"dept": department.name},
            )
        return wrapper
    return decorator
