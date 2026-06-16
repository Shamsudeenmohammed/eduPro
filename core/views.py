"""Core views — audit logs, ID cards, bulk utilities."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from accounts.decorators import admin_required
from accounts.models import EduProUser
from .models import AuditLog
from .utils import render_id_card_pdf, render_transcript_pdf


@login_required
@admin_required
def audit_log_list(request):
    qs = AuditLog.objects.select_related("user").order_by("-created_at")
    action = request.GET.get("action")
    if action:
        qs = qs.filter(action=action)
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "core/audit_logs.html", {
        "page_title": "Audit Logs",
        "page_obj": page_obj,
    })


@login_required
def transcript_pdf(request, user_pk=None):
    if user_pk and getattr(request.user, "is_admin", False):
        student = get_object_or_404(EduProUser, pk=user_pk, role="student")
    elif getattr(request.user, "is_student", False):
        student = request.user
    else:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return render_transcript_pdf(student)


@login_required
def id_card_pdf(request, user_pk=None):
    target = request.user
    if user_pk:
        if not getattr(request.user, "is_admin", False):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        target = get_object_or_404(EduProUser, pk=user_pk)
    return render_id_card_pdf(target)
