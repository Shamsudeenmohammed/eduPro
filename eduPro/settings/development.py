"""Development settings."""
from decouple import config
from .base import *  # noqa: F403

DEBUG = config("DEBUG", default=True, cast=bool)

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

INTERNAL_IPS = ["127.0.0.1"]

# Ensure logs directory exists
(BASE_DIR / "logs").mkdir(exist_ok=True)  # noqa: F405
