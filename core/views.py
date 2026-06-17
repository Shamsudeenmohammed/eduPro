"""Core views — audit logs, ID cards, bulk utilities."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.shortcuts import get_object_or_404, render

from accounts.decorators import admin_required
from accounts.models import EduProUser
from .models import AuditAction, AuditLog
from .utils import render_id_card_pdf, render_transcript_pdf


@login_required
@admin_required
def audit_log_list(request):
    qs = AuditLog.objects.select_related("user").order_by("-created_at")

    action = request.GET.get("action")
    model_name = request.GET.get("model")
    search = request.GET.get("q", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    if action:
        qs = qs.filter(action=action)
    if model_name:
        qs = qs.filter(model_name=model_name)
    if search:
        qs = qs.filter(
            models.Q(object_repr__icontains=search)
            | models.Q(user__email__icontains=search)
            | models.Q(path__icontains=search)
        )
    if date_from:
        qs = qs.filter(created_at__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__lte=f"{date_to} 23:59:59")

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    model_choices = (
        AuditLog.objects.values_list("model_name", flat=True)
        .exclude(model_name="")
        .distinct()
        .order_by("model_name")
    )

    return render(request, "core/audit_logs.html", {
        "page_title": "Audit Logs",
        "page_obj": page_obj,
        "action_choices": AuditAction.choices,
        "model_choices": model_choices,
        "current_action": action or "",
        "current_model": model_name or "",
        "current_q": search,
        "current_from": date_from,
        "current_to": date_to,
    })


@login_required
@admin_required
def audit_log_detail(request, pk):
    log = get_object_or_404(AuditLog.objects.select_related("user"), pk=pk)
    return render(request, "core/audit_log_detail.html", {
        "page_title": "Audit Log Detail",
        "log": log,
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
