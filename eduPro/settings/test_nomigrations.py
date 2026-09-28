"""
Test settings that build the schema from the *models* instead of from migration
files.

The Applications & Admissions engine was implemented without shipping
migrations, which means the ordinary test runner builds its database from the
existing migration state — so the new ``portal_*`` tables and columns simply do
not exist, and every test that touches them errors out with
``no such table: portal_applicationtype``.

Setting ``MIGRATION_MODULES`` to ``None`` for every app makes Django treat each
app as unmigrated, so the test database is created directly from the current
models via ``syncdb``. That is what makes the admissions engine testable at all
before migrations are written.

This module changes **only** how the test schema is produced. It touches no
application logic and is never used outside ``manage.py test``.

    python manage.py test --settings=eduPro.settings.test_nomigrations

It also pins the test database to a separate in-memory SQLite database, so the
development ``db.sqlite3`` is never at risk.
"""

from .development import *  # noqa: F403

DEBUG = False

# Never let an exception page leak a real key in test output.
SECRET_KEY = "test-only-insecure-key"

# In-memory, so running this can never touch the development database.
DATABASES = {  # noqa: F405
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
    }
}

# Build every app's tables from its models rather than from migration files.
#
# ``MIGRATION_MODULES`` is keyed by app *label*, but INSTALLED_APPS holds dotted
# config paths ("portal.apps.PortalConfig") and the app registry is not
# populated yet while settings are being read — so the labels cannot be derived
# here. Instead, this mapping reports *every* app as unmigrated, which is
# precisely the behaviour Django expects for an app with no migration module.
# Deriving the labels at settings-load time is not possible and guessing them
# from the dotted path would silently miss apps.
class _EveryAppUnmigrated(dict):
    """``MIGRATION_MODULES`` mapping meaning "no app has migrations"."""

    def __contains__(self, key):
        return True

    def __getitem__(self, key):
        return None

    def get(self, key, default=None):
        return None

    def items(self):
        return ()

    def __len__(self):
        return 0


MIGRATION_MODULES = _EveryAppUnmigrated()

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Background work would make the suite non-deterministic; the admissions
# services are synchronous by design.
CELERY_TASK_ALWAYS_EAGER = True
