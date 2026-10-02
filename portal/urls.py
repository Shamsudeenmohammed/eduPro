"""
portal/urls.py

Merged URL configuration — preserves every original route and adds
the new admissions-workflow routes alongside them.

    Original routes (unchanged):
    home, about, programs, contact, admission_apply,
    news_list, news_detail, admin_contacts, admin_admissions

    Public discovery routes (added by the frontend redesign — read-only):
    program_detail, academic, admissions, events_list, student_life


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

    # Public discovery pages (read-only views over existing records)
    path("programs/<int:pk>/",  views.program_detail,   name="program_detail"),
    path("academics/",          views.academic_structure, name="academic"),
    path("how-to-apply/",       views.admissions_landing,  name="admissions"),
    path("events/",             views.events_list,      name="events_list"),
    path("student-life/",       views.student_life,     name="student_life"),

    # Original simple admission form (legacy — kept for backward compat)
    path("admission/",          views.admission_apply,  name="admission_apply"),

    # ═════════════════════════════════════════════════════════════════════════
    # APPLICATIONS & ADMISSIONS ENGINE
    # ═════════════════════════════════════════════════════════════════════════

    # ── Applicant portal (ownership-scoped) ──────────────────────────────────
    path("applicant/",                          views.applicant_dashboard,             name="applicant_dashboard"),
    path("applicant/applications/<int:pk>/edit/",     views.applicant_application_edit,    name="applicant_application_edit"),
    path("applicant/applications/<int:pk>/submit/",   views.applicant_application_submit,  name="applicant_application_submit"),
    path("applicant/applications/<int:pk>/withdraw/", views.applicant_application_withdraw,name="applicant_application_withdraw"),
    path("applicant/applications/<int:pk>/checklist/",views.applicant_checklist,           name="applicant_checklist"),
    path("applicant/applications/<int:pk>/documents/upload/",
                                                     views.applicant_document_upload,      name="applicant_document_upload"),
    path("applicant/applications/<int:application_pk>/offers/<int:offer_pk>/",
                                                     views.applicant_offer_detail,         name="applicant_offer_detail"),
    path("applicant/applications/<int:pk>/payment/",  views.applicant_payment_start,       name="applicant_payment_start"),
    path("applicant/applications/<int:pk>/corrections/respond/",
                                                     views.applicant_correction_response, name="applicant_correction_response"),
    path("documents/<int:pk>/download/",          views.document_download,               name="document_download"),

    # ── Original admin routes (PRESERVED) ────────────────────────────────────
    path("admin/contacts/",     views.admin_contacts,   name="admin_contacts"),
    path("admin/admissions/",   views.admin_admissions, name="admin_admissions"),

    # ── New: portal login (email-only, applicant-facing) ─────────────────────
    path("login/",                             views.portal_login,           name="portal_login"),

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

    # ── Admissions engine: staff workflow actions ─────────────────────────────
    # Status changes go exclusively through application_transition, which calls
    # portal.workflow.ApplicationWorkflow (requirement 5).
    path("admissions/applications/<int:pk>/transition/", views.application_transition,       name="application_transition"),
    path("admissions/applications/<int:pk>/decision/",   views.application_decision,         name="application_decision"),
    path("admissions/applications/<int:pk>/offer/",      views.application_offer_issue,      name="application_offer_issue"),
    path("admissions/applications/<int:pk>/convert/",    views.application_convert,         name="application_convert"),
    path("admissions/applications/<int:pk>/correction/", views.application_correction_request, name="application_correction_request"),
    path("admissions/documents/<int:pk>/verify/",        views.document_verify,              name="document_verify"),

    # ── Admissions engine: finance (admin, no decision rights) ────────────────
    path("admissions/applications/<int:pk>/payment/record/", views.application_payment_record, name="application_payment_record"),
    path("admissions/payments/",                    views.application_payment_list,     name="application_payment_list"),
    path("admissions/payments/<int:pk>/verify/",   views.application_payment_verify,   name="application_payment_verify"),

    # ── Letter downloads (applicant-facing) ────────────────────────────────────
    path("letters/application/<str:ref>/",   views.application_letter_pdf,  name="application_letter_pdf"),
    path("letters/admission/<str:ref>/",     views.admission_letter_pdf,    name="admission_letter_pdf"),
]
