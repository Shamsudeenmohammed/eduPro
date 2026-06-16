from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("audit-logs/", views.audit_log_list, name="audit_logs"),
    path("transcript/", views.transcript_pdf, name="transcript_pdf"),
    path("transcript/<int:user_pk>/", views.transcript_pdf, name="transcript_pdf_user"),
    path("id-card/", views.id_card_pdf, name="id_card_pdf"),
    path("id-card/<int:user_pk>/", views.id_card_pdf, name="id_card_pdf_user"),
]
