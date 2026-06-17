"""
portal/urls.py

Merged URL configuration — preserves every original route and adds
the new admissions-workflow routes alongside them.

Original routes (unchanged):
    home, about, programs, contact, admission_apply,
    news_list, news_detail, admin_contacts, admin_admissions

New routes (added by refactor):
    apply, application_confirmed, application_status, application_withdraw,
    admissions_dashboard, application_list, application_detail,
    application_review, application_approve, application_reject,
    document_request_create, document_request_fulfill,
    cycle_list, cycle_create, cycle_edit
"""

from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    # ── Original public routes (PRESERVED — templates reference these) ────────
    path("",                    views.home,             name="home"),
    path("about/",              views.about,            name="about"),
    path("programs/",           views.programs_public,  name="programs"),
    path("contact/",            views.contact,          name="contact"),
    path("news/",               views.news_list,        name="news_list"),
    path("news/<int:pk>/",      views.news_detail,      name="news_detail"),

    # Original simple admission form (legacy — kept for backward compat)
    path("admission/",          views.admission_apply,  name="admission_apply"),

    # ── Original admin routes (PRESERVED) ────────────────────────────────────
    path("admin/contacts/",     views.admin_contacts,   name="admin_contacts"),
    path("admin/admissions/",   views.admin_admissions, name="admin_admissions"),

    # ── New: controlled admission workflow ───────────────────────────────────
    path("apply/",                             views.application_form,       name="apply"),
    path("apply/confirmed/<str:ref>/",         views.application_confirmed,  name="application_confirmed"),
    path("status/",                            views.application_status,     name="application_status"),
    path("status/<str:ref>/withdraw/",         views.application_withdraw,   name="application_withdraw"),

    # ── New: staff admissions dashboard & list ────────────────────────────────
    path("admissions/",                        views.admissions_dashboard,   name="admissions_dashboard"),
    path("admissions/applications/",           views.application_list,       name="application_list"),

    # ── New: application detail & actions ────────────────────────────────────
    path("admissions/applications/<int:pk>/",            views.application_detail,   name="application_detail"),
    path("admissions/applications/<int:pk>/review/",     views.application_review,   name="application_review"),
    path("admissions/applications/<int:pk>/approve/",    views.application_approve,  name="application_approve"),
    path("admissions/applications/<int:pk>/reject/",     views.application_reject,   name="application_reject"),

    # ── New: document requests ────────────────────────────────────────────────
    path(
        "admissions/applications/<int:application_pk>/request-doc/",
        views.document_request_create,
        name="document_request_create",
    ),
    path(
        "admissions/doc-requests/<int:pk>/fulfill/",
        views.document_request_fulfill,
        name="document_request_fulfill",
    ),
    path(
        "doc-requests/<int:pk>/upload/",
        views.applicant_upload_document,
        name="applicant_upload_document",
    ),

    # ── New: admission cycle management (admin) ───────────────────────────────
    path("admissions/cycles/",               views.cycle_list,   name="cycle_list"),
    path("admissions/cycles/add/",           views.cycle_create, name="cycle_create"),
    path("admissions/cycles/<int:pk>/edit/", views.cycle_edit,   name="cycle_edit"),

    # ── Letter downloads (applicant-facing) ────────────────────────────────────
    path("letters/application/<str:ref>/",   views.application_letter_pdf,  name="application_letter_pdf"),
    path("letters/admission/<str:ref>/",     views.admission_letter_pdf,    name="admission_letter_pdf"),
]
