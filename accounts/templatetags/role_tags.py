from django import template
from accounts.models import StaffResponsibility

register = template.Library()


@register.filter(name="is_admin")
def is_admin(user):
    if not user or user.is_anonymous:
        return False
    return getattr(user, "is_admin", False) or getattr(user, "is_superuser", False)


@register.filter(name="is_teacher")
def is_teacher(user):
    if not user or user.is_anonymous:
        return False
    return getattr(user, "is_teacher", False)


@register.filter(name="is_student")
def is_student(user):
    if not user or user.is_anonymous:
        return False
    return getattr(user, "is_student", False)


@register.filter(name="has_role")
def has_role(user, role_name: str):
    if not user or user.is_anonymous:
        return False

    if getattr(user, "is_superuser", False):
        return True

    target_roles = [r.strip().lower() for r in role_name.split(",")]

    if getattr(user, "role", "").lower() in target_roles:
        return True

    if hasattr(user, "has_responsibility"):
        for role in target_roles:
            if role == "hod":
                if user.has_responsibility(StaffResponsibility.HOD):
                    return True
            elif user.has_responsibility(role):
                return True

    if hasattr(user, "is_hod") and "hod" in target_roles and user.is_hod:
        return True
    if hasattr(user, "is_admin") and "admin" in target_roles and user.is_admin:
        return True
    if hasattr(user, "is_teacher") and "teacher" in target_roles and user.is_teacher:
        return True
    if hasattr(user, "is_student") and "student" in target_roles and user.is_student:
        return True

    return False


@register.filter(name="is_hod_of")
def is_hod_of(user, department):
    if not user or not user.is_authenticated:
        return False

    if getattr(user, "is_superuser", False) or getattr(user, "is_admin", False):
        return True

    if hasattr(user, "is_hod_of") and callable(user.is_hod_of):
        return user.is_hod_of(department)

    return getattr(department, "hod_id", None) == user.pk


@register.simple_tag(takes_context=True)
def active_if(context, *url_names):
    request = context.get("request")
    if not request or not request.resolver_match:
        return ""

    current_view = request.resolver_match.view_name
    current_url = request.resolver_match.url_name

    for url in url_names:
        if url == current_view or url == current_url:
            return "active"

    return ""