"""
notifications/providers.py

Per-channel delivery providers. All third-party calls are made with the
standard library (urllib) — the project intentionally has no `requests`
dependency. Providers never raise into views: the NotificationService wraps
every call and persists failures on the NotificationRecord.
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger("eduPro.notifications")


class ProviderError(Exception):
    """Raised when an external provider call fails."""


def http_post_json(url, headers, payload, timeout=30):
    """POST a JSON payload; return parsed JSON response (or None on failure)."""
    data = json.dumps(payload).encode("utf-8")
    headers = {k: v for k, v in headers.items() if v}
    headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        raise ProviderError(f"Sailup request failed: {exc}") from exc


class SailupSmsProvider:
    """Sends SMS via the Sailup API. POST {base}/sms/ ."""

    def __init__(self, api_key=None, base_url=None, sender_id=None):
        self.api_key = api_key if api_key is not None else settings.SAILUP_API_KEY
        self.base_url = base_url or settings.SAILUP_BASE_URL
        self.sender_id = sender_id if sender_id is not None else settings.SAILUP_SENDER_ID

    @property
    def enabled(self):
        return bool(
            settings.SAILUP_ENABLED and self.api_key and self.sender_id
        )

    def send(self, phone, body):
        """
        Send one SMS. phone must be an E.164-style string used as a plain list
        entry per the Sailup API contract ({from, to: [...], body}).
        Returns (provider_message_id, error_message).
        """
        if not self.enabled:
            return "", "Sailup disabled (SAILUP_ENABLED/API key missing)"

        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"from": self.sender_id, "to": [phone], "body": body}
        try:
            resp = http_post_json(
                f"{self.base_url.rstrip('/')}/sms/", headers, payload, timeout=60
            )
        except ProviderError as exc:
            logger.warning("Sailup SMS failed for %s: %s", phone, exc)
            return "", str(exc)

        message_id = (
            resp.get("message_id")
            or resp.get("message_ids")
            or resp.get("data", {}).get("message_id")
            or ""
        )
        if isinstance(message_id, list):
            message_id = message_id[0] if message_id else ""
        status = str(resp.get("status", "sent"))
        return str(message_id or ""), None if status in ("sent", "queued", "delivered") else json.dumps(resp)


class EmailProvider:
    """Sends email via Django's send_mail / EmailMultiAlternatives."""

    def send(self, to_email, subject, html_body, text_body):
        """
        Send an HTML email with a plain-text fallback.
        Returns (recipient, error_message). recipient is None when fine
        (Django SMTP accepts without per-address IDs).
        """
        if not to_email:
            return None, "No email address"
        try:
            from django.core.mail import EmailMultiAlternatives

            email = EmailMultiAlternatives(subject, text_body, None, [to_email])
            if html_body:
                email.attach_alternative(html_body, "text/html")
            email.send(fail_silently=False)
            return None, None
        except Exception as exc:  # noqa: BLE001 - never crash the request
            logger.warning("Email to %s failed: %s", to_email, exc)
            return None, str(exc)


class InAppProvider:
    """Writes in-app notifications.

    - Students continue to use students.StudentNotification (existing bell,
      dashboard and notification centre rely on it); category mapping lives in
      notifications.services.NotificationService.
    - Teachers/admins use notifications.NotificationRecord(channel=APP), which
      also keeps a full cross-channel history for every user.
    """