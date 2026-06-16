"""Security middleware — rate limiting and audit logging."""

import time

from django.conf import settings
from django.core.cache import cache
from django.utils.deprecation import MiddlewareMixin

from .models import AuditAction, AuditLog


class RateLimitMiddleware(MiddlewareMixin):
    """Simple IP-based rate limiting using Django cache."""

    EXEMPT_PREFIXES = ("/static/", "/media/", "/admin/jsi18n/")

    def process_request(self, request):
        if not getattr(settings, "RATE_LIMIT_ENABLED", True):
            return None
        path = request.path
        if any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return None

        ip = self._get_client_ip(request)
        key = f"ratelimit:{ip}"
        window = getattr(settings, "RATE_LIMIT_WINDOW", 60)
        limit = getattr(settings, "RATE_LIMIT_REQUESTS", 120)

        data = cache.get(key)
        now = time.time()
        if data is None:
            cache.set(key, {"count": 1, "start": now}, window)
            return None

        if now - data["start"] > window:
            cache.set(key, {"count": 1, "start": now}, window)
            return None

        if data["count"] >= limit:
            from django.http import HttpResponse
            return HttpResponse("Too many requests. Please try again later.", status=429)

        data["count"] += 1
        cache.set(key, data, window)
        return None

    @staticmethod
    def _get_client_ip(request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "0.0.0.0")


class AuditMiddleware(MiddlewareMixin):
    """Log POST mutations from authenticated admin users."""

    AUDIT_PATHS = (
        "/academics/",
        "/accounts/users/",
        "/operations/",
        "/finance/",
        "/students/admin/",
    )

    def process_response(self, request, response):
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return response
        if not request.user.is_authenticated:
            return response
        if not getattr(request.user, "is_admin", False):
            return response
        if not any(request.path.startswith(p) for p in self.AUDIT_PATHS):
            return response
        if response.status_code >= 400:
            return response

        action = AuditAction.UPDATE
        if request.method == "POST":
            action = AuditAction.CREATE
        elif request.method == "DELETE":
            action = AuditAction.DELETE

        AuditLog.objects.create(
            user=request.user,
            action=action,
            path=request.path[:300],
            ip_address=RateLimitMiddleware._get_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:300],
            object_repr=request.path,
        )
        return response
