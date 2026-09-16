"""Paystack payment gateway integration for hostel charges."""

import json
import logging
import urllib.error
import urllib.request

from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings

from .base import HostelServiceError

logger = logging.getLogger("eduPro")


class PaystackError(HostelServiceError):
    """Paystack API or gateway failure surfaced to the student."""
    pass


class PaystackService:
    """
    Thin client around Paystack's REST API (stdlib only, no third-party deps).
    Amounts are sent in minor units (GHS * 100, i.e. pesewas) as required by
    Paystack.
    """

    GA = "https://api.paystack.co"

    @classmethod
    def _base_url(cls):
        return (getattr(settings, "PAYSTACK_BASE_URL", None) or cls.GA).rstrip("/")

    @classmethod
    def enabled(cls):
        return bool(getattr(settings, "PAYSTACK_SECRET_KEY", ""))

    @staticmethod
    def _headers():
        return {
            "Authorization": "Bearer " + (settings.PAYSTACK_SECRET_KEY or ""),
            "Content-Type": "application/json",
        }

    @classmethod
    def _request(cls, method, path, payload=None, params=None):
        url = cls._base_url() + path
        if params:
            url += "?" + urlencode(params)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url, data=body, headers=cls._headers(), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8") or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8") or b"{}")
                msg = detail.get("message") or "Paystack rejected the request."
            except Exception:
                msg = "Paystack request failed."
            logger.error("Paystack HTTP %s %s -> %s: %s", method, path, exc.code, msg)
            raise PaystackError(msg) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.error("Paystack %s %s unreachable: %s", method, path, exc)
            raise PaystackError("Payment gateway is unreachable. Please try again later.") from exc

    @classmethod
    def initialize(cls, *, email, amount, reference, callback_url, metadata=None):
        """Create a transaction and return its authorization URL."""
        if not cls.enabled():
            raise PaystackError("Online payments are not configured yet.")
        payload = {
            "email": email,
            "amount": int(amount),  # Minor units (pesewas for GHS)
            "reference": reference,
            "currency": getattr(settings, "PAYSTACK_CURRENCY", "GHS"),
            "callback_url": callback_url,
        }
        if metadata:
            payload["metadata"] = metadata
        resp = cls._request("POST", "/transaction/initialize", payload=payload)
        if not resp.get("status"):
            raise PaystackError(resp.get("message") or "Could not start payment.")
        return resp.get("data") or {}

    @classmethod
    def verify(cls, reference):
        """Verify a transaction by its reference."""
        resp = cls._request("GET", f"/transaction/verify/{reference}")
        if not resp.get("status"):
            raise PaystackError(resp.get("message") or "Could not verify payment.")
        return resp.get("data") or {}

    @staticmethod
    def minor_to_major(amount_minor):
        """Convert minor units (pesewas/kobo) to the major currency (GHS/NGN)."""
        return Decimal(amount_minor or 0) / Decimal(100)