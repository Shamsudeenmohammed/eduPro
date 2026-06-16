"""
accounts/urls.py

All URL patterns for the accounts app.
Include in main urls.py as:
    path("accounts/", include("accounts.urls", namespace="accounts")),
"""

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import PasswordResetForm, SetPasswordForm

app_name = "accounts"

urlpatterns = [
    # ── Authentication ────────────────────────────────────────────────────────
    path("login/",    views.login_view,    name="login"),
    path("logout/",   views.logout_view,   name="logout"),
    path("register/", views.register_view, name="register"),

    # ── Dashboard entry points ─────────────────────────────────────────────────
    path("dashboard/",          views.dashboard_redirect,  name="dashboard_redirect"),
    path("dashboard/student/",  views.student_dashboard,   name="student_dashboard"),
    path("dashboard/teacher/",  views.teacher_dashboard,   name="teacher_dashboard"),
    path("dashboard/admin/",    views.admin_dashboard,     name="dashboard"),

    # ── Pending approvals (admin) ──────────────────────────────────────────────
    path("pending/",               views.pending_users_view, name="pending_users"),
    path("pending/<int:pk>/approve/", views.approve_user,    name="approve_user"),
    path("pending/<int:pk>/reject/",  views.reject_user,     name="reject_user"),
    
    # =========================================================
    # Admin Registration Management
    # =========================================================
    path("admin/registrations/", views.admin_registration_requests, name="admin_registrations"),
    path("admin/registrations/<int:pk>/approve/", views.approve_registration, name="approve_registration"),
    path("admin/registrations/<int:pk>/reject/", views.reject_registration, name="reject_registration"),

    # ── Staff roles / responsibilities (admin) ─────────────────────────────────
    path("users/<int:pk>/roles/",               views.staff_roles_view,   name="staff_roles"),
    path("users/roles/<int:role_pk>/revoke/",   views.revoke_staff_role,  name="revoke_staff_role"),

    # ── Profile ───────────────────────────────────────────────────────────────
    path("profile/",                views.profile_view,         name="profile"),
    path("profile/change-password/", views.change_password_view, name="change_password"),

    # ── Admin user management ─────────────────────────────────────────────────
    path("users/",             views.user_list_view,   name="user_list"),
    path("users/<int:pk>/",    views.user_detail_view, name="user_detail"),

    # ── Password Reset ────────────────────────────────────────────────────────
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset.html",
            form_class=PasswordResetForm,
            email_template_name="accounts/emails/password_reset_email.txt",
            subject_template_name="accounts/emails/password_reset_subject.txt",
            success_url="/accounts/password-reset/done/",
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/confirm/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            form_class=SetPasswordForm,
            success_url="/accounts/password-reset/complete/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
]
