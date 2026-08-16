"""Production settings — tuned for Render free hosting.

Requires these environment variables on Render:
  SECRET_KEY, ALLOWED_HOSTS (optional), plus either DATABASE_URL
  (set automatically when you link a Postgres instance) or DB_NAME/DB_USER/
  DB_PASSWORD/DB_HOST/DB_PORT. REDIS_URL is optional — Render free tier has
  no managed Redis, so the in-memory cache is used when it is absent.
"""

import urllib.parse

from decouple import config, Csv

from .base import *  # noqa: F403

DEBUG = False

SECRET_KEY = config("SECRET_KEY")

_RENDER_HOST = config("RENDER_EXTERNAL_HOSTNAME", default="")
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=Csv())
if _RENDER_HOST and _RENDER_HOST not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_RENDER_HOST)

CSRF_TRUSTED_ORIGINS = [
    f"https://{host}" for host in ALLOWED_HOSTS if host not in ("127.0.0.1", "localhost")
]

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Render terminates TLS at its load balancer — trust the forwarded header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Render auto-injects DATABASE_URL when a Postgres instance is linked.
_DATABASE_URL = config("DATABASE_URL", default="")
if _DATABASE_URL:
    _db = urllib.parse.urlparse(_DATABASE_URL)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _db.path.lstrip("/"),
            "USER": _db.username,
            "PASSWORD": _db.password,
            "HOST": _db.hostname,
            "PORT": _db.port or "5432",
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"connect_timeout": 10},
        }
    }
else:
    _missing_db = [k for k in ("DB_NAME", "DB_USER", "DB_PASSWORD") if not config(k, default="")]
    if _missing_db:
        raise RuntimeError(
            "No database configured. On Render, link a (free) Postgres instance "
            "to this web service — it auto-injects DATABASE_URL. Otherwise set "
            "these env vars on the service: " + ", ".join(_missing_db)
        )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": config("DB_NAME"),
            "USER": config("DB_USER"),
            "PASSWORD": config("DB_PASSWORD"),
            "HOST": config("DB_HOST", default="localhost"),
            "PORT": config("DB_PORT", default="5432"),
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"connect_timeout": 10},
        }
    }

# No managed Redis on Render free tier — only use Redis if REDIS_URL is set,
# otherwise fall back to the in-memory cache from base settings.
if config("REDIS_URL", default=""):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": config("REDIS_URL"),
        }
    }

# Render free disks are ephemeral — log to stdout only.
LOGGING["handlers"] = {"console": LOGGING["handlers"]["console"]}  # noqa: F405
LOGGING["root"]["handlers"] = ["console"]  # noqa: F405
for _logger in LOGGING.get("loggers", {}).values():  # noqa: F405
    _logger["handlers"] = ["console"]
