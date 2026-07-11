"""Core views — audit logs, ID cards, bulk utilities."""

from django.contrib import admin as admin_site
from django.contrib.admin.utils import quote
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

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


@login_required
@admin_required
def admin_global_search(request):
    query = request.GET.get("q", "").strip()
    results = []

    if query:
        for model_class, model_admin in admin_site.site._registry.items():
            search_fields = getattr(model_admin, "search_fields", None)
            if not search_fields:
                continue
            if not request.user.has_perm(f"{model_class._meta.app_label}.view_{model_class._meta.model_name}"):
                continue

            q_object = models.Q()
            for field in search_fields:
                q_object |= models.Q(**{f"{field}__icontains": query})

            qs = model_class.objects.filter(q_object).distinct()[:10]

            if qs:
                opts = model_class._meta
                for obj in qs:
                    try:
                        change_url = reverse(
                            f"admin:{opts.app_label}_{opts.model_name}_change",
                            args=(quote(obj.pk),),
                        )
                    except Exception:
                        change_url = None
                    results.append({
                        "model_name": opts.verbose_name.title(),
                        "app_label": opts.app_label.title(),
                        "object": obj,
                        "url": change_url,
                    })

    return render(request, "core/admin_global_search.html", {
        "page_title": "Search Results",
        "query": query,
        "results": results,
        "title": f"Search: {query}" if query else "Search",
    })
