"""
accounts/templatetags/role_tags.py

Custom template tags and filters for role-based rendering in templates.

Usage:
    {% load role_tags %}
    {% if request.user|is_admin %}...{% endif %}
    {% if request.user|has_role:"teacher" %}...{% endif %}
    {% if request.user|has_role:"hod" %}...{% endif %}
"""

from django import template
from accounts.models import Role

register = template.Library()


@register.filter(name="is_admin")
def is_admin(user):
    """Return True if the user has the ADMIN role or is a Django superuser."""
    if not user or user.is_anonymous:
        return False
    return getattr(user, "role", None) == Role.ADMIN or getattr(user, "is_superuser", False)


@register.filter(name="is_teacher")
def is_teacher(user):
    """Return True if the user has the TEACHER role."""
    if not user or user.is_anonymous:
        return False
    return getattr(user, "role", None) == Role.TEACHER


@register.filter(name="is_student")
def is_student(user):
    """Return True if the user has the STUDENT role."""
    if not user or user.is_anonymous:
        return False
    return getattr(user, "role", None) == Role.STUDENT


@register.filter(name="has_role")
def has_role(user, role_name: str):
    """
    Checks if a user matches a primary role OR any assigned fine-grained staff responsibilities.
    Accepts comma-separated roles: {{ user|has_role:"admin,hod" }}
    """
    if not user or user.is_anonymous:
        return False

    if getattr(user, "is_superuser", False):
        return True

    # Split comma-separated inputs (e.g., "teacher,hod" -> ["teacher", "hod"])
    target_roles = [r.strip().lower() for r in role_name.split(",")]

    # 1. Check backward compatibility against the primary classification field
    user_primary_role = getattr(user, "role", "").lower()
    if user_primary_role in target_roles:
        return True

    # 2. Check fine-grained stacked responsibilities from your new model architecture
    if hasattr(user, "has_responsibility"):
        for role in target_roles:
            if user.has_responsibility(role):
                return True

    return False


@register.simple_tag(takes_context=True)
def active_if(context, *url_names):
    """
    Returns 'active' CSS class string if the current URL name matches.
    Usage: {% active_if "accounts:profile" "accounts:change_password" %}
    """
    request = context.get("request")
    if request is None:
        return ""
    current = request.resolver_match.view_name if request.resolver_match else ""
    return "active" if current in url_names else ""