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

# Serve /static/ straight from the static/ dirs during development.
# Without this, WhiteNoiseMiddleware answers every /static/ request from
# STATIC_ROOT and any newly added or edited asset 404s until collectstatic runs.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True

# Ensure logs directory exists
(BASE_DIR / "logs").mkdir(exist_ok=True)  # noqa: F405
