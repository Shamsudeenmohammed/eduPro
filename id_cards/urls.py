"""
id_cards/urls.py
"""

from django.urls import path

from . import views

app_name = "id_cards"

urlpatterns = [
    # ── Public verification (QR) ────────────────────────────────────────────
    path("verify/<slug:token>/", views.verify_card, name="verify_card"),

    # ── Student portal ──────────────────────────────────────────────────────
    path("my-card/", views.my_card, name="my_card"),
    path("my-card/digital/<str:side>/", views.digital_card, name="digital_card"),

    # ── Staff: overview & generation ────────────────────────────────────────
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/run-expiry/", views.run_expiry_check, name="run_expiry"),
    path("students/", views.student_list, name="student_list"),
    path("students/<int:student_pk>/generate/", views.generate_single,
         name="generate_single"),
    path("batch/", views.generate_batch_view, name="generate_batch"),

    # ── Staff: photo review ─────────────────────────────────────────────────
    path("photos/", views.photo_review, name="photo_review"),
    path("photos/<int:photo_pk>/approve/", views.photo_approve, name="photo_approve"),
    path("photos/<int:photo_pk>/reject/", views.photo_reject, name="photo_reject"),

    # ── Staff: batches ──────────────────────────────────────────────────────
    path("batches/", views.batch_list, name="batch_list"),
    path("batches/<int:pk>/", views.batch_detail, name="batch_detail"),

    # ── Staff: cards ────────────────────────────────────────────────────────
    path("cards/", views.card_list, name="card_list"),
    path("cards/<int:pk>/", views.card_detail, name="card_detail"),
    path("cards/<int:pk>/action/<str:action>/", views.card_action,
         name="card_action"),
    path("cards/<int:pk>/image/<str:side>/", views.card_image, name="card_image"),

    # ── Staff: print ────────────────────────────────────────────────────────
    path("print/", views.print_preview, name="print_preview"),
    path("print.pdf", views.print_pdf, name="print_pdf"),

    # ── Admin: templates & settings ─────────────────────────────────────────
    path("templates/", views.template_list, name="template_list"),
    path("templates/new/", views.template_create, name="template_create"),
    path("templates/<int:pk>/edit/", views.template_edit, name="template_edit"),
    path("templates/<int:pk>/set-default/", views.template_set_default,
         name="template_set_default"),
    path("templates/<int:pk>/status/", views.template_set_status,
         name="template_set_status"),
    path("templates/<int:pk>/archive/", views.template_archive,
         name="template_archive"),
    path("templates/<int:pk>/duplicate/", views.template_duplicate,
         name="template_duplicate"),
    path("settings/", views.settings_view, name="settings"),

    # ── Staff: replacement requests ─────────────────────────────────────────
    path("replacements/", views.replacement_list, name="replacement_list"),
    path("replacements/<int:pk>/approve/", views.replacement_approve,
         name="replacement_approve"),
    path("replacements/<int:pk>/reject/", views.replacement_reject,
         name="replacement_reject"),

    # ── Staff: reporting & audit ────────────────────────────────────────────
    path("reports/", views.reports, name="reports"),
    path("reports/export.csv", views.export_csv, name="export_csv"),
    path("audit/", views.audit_log, name="audit_log"),
]