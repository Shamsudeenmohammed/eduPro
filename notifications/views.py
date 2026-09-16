"""
notifications/views.py

Web views + the Sailup delivery webhook.
"""

import hashlib
import hmac
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.decorators import teacher_required
from .forms import NotificationPreferenceForm
from .models import (
    Channel,
    DeliveryStatus,
    DeliveryWebhook,
    NotificationPreference,
    NotificationRecord,
)
from .services import NotificationService


# ── Delivery webhook (Sailup) ───────────────────────────────────────────────

def _signature_matches(raw_body, signature):
    secret = settings.SAILUP_WEBHOOK_SECRET
    if not secret or not signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@csrf_exempt
@require_POST
def sailup_webhook(request):
    """Receive Sailup delivery-status callbacks and update NotificationRecords."""
    raw = request.body
    signature = request.headers.get("X-Sailup-Signature", "")
    valid = _signature_matches(raw, signature)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("Invalid JSON")

    event = payload.get("event") or payload.get("type") or ""
    provider_message_id = (
        payload.get("message_id")
        or (payload.get("data") or {}).get("message_id")
        or ""
    )
    status = (
        payload.get("status")
        or (payload.get("data") or {}).get("status")
        or ""
    )

    with transaction.atomic():
        DeliveryWebhook.objects.create(
            provider_message_id=str(provider_message_id),
            event=str(event),
            status=str(status),
            payload=payload,
            signature_valid=valid,
        )

        if provider_message_id and valid:
            NotificationRecord.objects.filter(
                provider_message_id=str(provider_message_id)
            ).update(status=_map_delivery_status(status))

    if not valid:
        return JsonResponse({"ok": False, "reason": "invalid signature"}, status=400)
    return JsonResponse({"ok": True})


def _map_delivery_status(status):
    status = (status or "").lower()
    mapping = {
        "sent": DeliveryStatus.SENT,
        "queued": DeliveryStatus.QUEUED,
        "delivered": DeliveryStatus.DELIVERED,
        "failed": DeliveryStatus.FAILED,
        "expired": DeliveryStatus.EXPIRED,
        "rejected": DeliveryStatus.REJECTED,
    }
    return mapping.get(status, DeliveryStatus.SENT)


# ── Teacher notification centre ─────────────────────────────────────────────

@login_required
@teacher_required
def teacher_notification_centre(request):
    records = NotificationRecord.objects.filter(
        recipient=request.user, channel=Channel.APP
    )
    paginator = Paginator(records, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "notifications/centre.html", {
        "page_title": "Notifications",
        "page_obj": page_obj,
        "is_teacher": True,
    })


@login_required
@teacher_required
@require_POST
def mark_read(request, pk):
    NotificationService.mark_read(request.user, pk)
    return redirect("notifications:centre")


@login_required
@require_POST
def mark_all_read(request):
    NotificationService.mark_all_read(request.user)
    next_url = request.POST.get("next") or "notifications:centre"
    try:
        return redirect(next_url)
    except Exception:  # noqa: BLE001
        return redirect("notifications:centre")


# ── Notification preferences ────────────────────────────────────────────────

@login_required
def preferences(request):
    pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
    form = NotificationPreferenceForm(request.POST or None, instance=pref)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Notification preferences saved.")
        return redirect("notifications:preferences")

    return render(request, "notifications/preferences.html", {
        "page_title": "Notification Settings",
        "form": form,
    })


@login_required
def unread_count_json(request):
    count = NotificationService.unread_count(request.user)
    return JsonResponse({"count": count})