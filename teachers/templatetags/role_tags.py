from django import template

register = template.Library()


@register.filter(name="has_role")
def has_role(user, role_name: str) -> bool:
    """
    Generic role check that understands both primary roles and
    StaffResponsibilities.

    Accepts comma-separated values.
    Special value "HOD" checks UserStaffRole (not the primary role field).

    Examples:
        {{ user|has_role:"HOD" }}
        {{ user|has_role:"teacher,admin" }}
        {{ user|has_role:"HOD,dean" }}
    """
    if not user or not user.is_authenticated:
        return False

    # Superusers bypass explicit restrictions globally
    if getattr(user, "is_superuser", False):
        return True

    # Standardize inputs to match comparison flags safely
    roles = [r.strip().upper() for r in role_name.split(",")]

    for role in roles:
        if role == "HOD":
            # Check UserStaffRole for dynamic HOD responsibility model tracking
            if hasattr(user, "has_responsibility"):
                from accounts.models import StaffResponsibility
                if user.has_responsibility(StaffResponsibility.HOD):
                    return True
            
            # Fallback: legacy field properties setup
            if getattr(user, "is_hod", False):
                return True

        elif role == "ADMIN":
            if getattr(user, "is_admin", False):
                return True

        elif role == "TEACHER":
            if getattr(user, "is_teacher", False):
                return True

        elif role == "STUDENT":
            if getattr(user, "is_student", False):
                return True

        else:
            # Generic catch-all fallback: Case-insensitive match on user.role
            user_role = str(getattr(user, "role", "")).strip().upper()
            if user_role == role:
                return True

    return False


@register.filter(name="is_hod_of")
def is_hod_of(user, department) -> bool:
    """
    Returns True if the user is HOD of the given Department instance.
    Uses custom model layer checks if available, falls back to direct foreign key comparison.

    Usage:
        {% if user|is_hod_of:sheet.department %}...{% endif %}
    """
    if not user or not user.is_authenticated:
        return False
        
    if getattr(user, "is_superuser", False) or getattr(user, "is_admin", False):
        return True
        
    if hasattr(user, "is_hod_of") and callable(user.is_hod_of):
        return user.is_hod_of(department)
        
    # Standard relational fallback
    return getattr(department, "hod_id", None) == user.pk


@register.filter(name="is_admin")
def is_admin(user) -> bool:
    return getattr(user, "is_admin", False) or getattr(user, "is_superuser", False)


@register.filter(name="is_teacher")
def is_teacher(user) -> bool:
    return getattr(user, "is_teacher", False)


@register.filter(name="is_student")
def is_student(user) -> bool:
    return getattr(user, "is_student", False)


@register.simple_tag(takes_context=True)
def active_if(context, *url_names) -> str:
    """
    Returns 'active' CSS helper class if current URL name matches target names.
    Checks both standard relative endpoints and full namespace resolution paths.
    """
    request = context.get("request")
    if not request or not request.resolver_match:
        return ""
        
    current_view = request.resolver_match.view_name # e.g. 'academics:dashboard'
    current_url = request.resolver_match.url_name   # e.g. 'dashboard'
    
    for url in url_names:
        if url == current_view or url == current_url:
            return "active"
            
    return ""