"""
accounts/admin.py

Django admin for EduProUser, UserProfile, and UserStaffRole.

Changes:
- UserStaffRoleInline added to EduProUserAdmin.
- Pending-approval action: bulk activate selected users.
- is_active shown prominently in list.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import EduProUser, UserProfile, UserStaffRole


# ─────────────────────────────────────────────────────────────────────────────
# INLINES
# ─────────────────────────────────────────────────────────────────────────────

class UserProfileInline(admin.StackedInline):
    model              = UserProfile
    fk_name            = "user"
    can_delete         = False
    verbose_name_plural = _("Profile Details")
    fields             = ("department", "phone", "date_of_birth", "avatar", "bio")


class UserStaffRoleInline(admin.TabularInline):
    model       = UserStaffRole
    fk_name     = "user"   # resolves admin.E202: UserStaffRole has two FKs to EduProUser (user + granted_by)
    extra       = 0
    fields      = ("responsibility", "department", "is_active", "granted_by", "granted_at")
    readonly_fields = ("granted_at",)
    verbose_name_plural = _("Staff Responsibilities")


# ─────────────────────────────────────────────────────────────────────────────
# ACTIONS
# ─────────────────────────────────────────────────────────────────────────────

@admin.action(description=_("Approve and activate selected users"))
def approve_users(modeladmin, request, queryset):
    updated = queryset.filter(is_active=False).update(
        is_active=True,
        approved_by=request.user,
        approved_at=timezone.now(),
    )
    modeladmin.message_user(request, f"{updated} user(s) approved and activated.")


@admin.action(description=_("Deactivate selected users"))
def deactivate_users(modeladmin, request, queryset):
    updated = queryset.filter(is_active=True).update(is_active=False)
    modeladmin.message_user(request, f"{updated} user(s) deactivated.")


# ─────────────────────────────────────────────────────────────────────────────
# USER ADMIN
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(EduProUser)
class EduProUserAdmin(BaseUserAdmin):
    inlines        = (UserProfileInline, UserStaffRoleInline)
    USERNAME_FIELD = "email"
    actions        = [approve_users, deactivate_users]

    list_display = (
        "email", "first_name", "last_name",
        "role", "is_active", "is_staff", "date_joined",
    )
    list_filter  = ("role", "is_active", "is_staff", "date_joined")
    search_fields = ("email", "first_name", "last_name")
    ordering      = ("last_name", "first_name")
    readonly_fields = ("date_joined", "last_login", "approved_by", "approved_at")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal Info"), {"fields": ("first_name", "last_name", "role")}),
        (_("Activation"), {"fields": ("is_active", "approved_by", "approved_at")}),
        (_("Permissions"), {
            "classes": ("collapse",),
            "fields": ("is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        (_("Metadata"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "email", "first_name", "last_name", "role",
                "password1", "password2", "is_active", "is_staff",
            ),
        }),
    )


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE ADMIN
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ("user", "department", "phone", "updated_at")
    search_fields = ("user__email", "user__first_name", "user__last_name", "department")
    readonly_fields = ("updated_at",)


# ─────────────────────────────────────────────────────────────────────────────
# STAFF ROLE ADMIN
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(UserStaffRole)
class UserStaffRoleAdmin(admin.ModelAdmin):
    list_display  = ("user", "responsibility", "department", "is_active", "granted_by", "granted_at")
    list_filter   = ("responsibility", "is_active", "department")
    search_fields = ("user__email", "user__first_name", "user__last_name")
    raw_id_fields = ("user", "granted_by")
    readonly_fields = ("granted_at",)
