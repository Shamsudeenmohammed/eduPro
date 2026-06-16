"""
accounts/apps.py

AppConfig — wires up the post_save signal that auto-creates a UserProfile
whenever a new EduProUser is saved.
"""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Accounts & Authentication"

    def ready(self):
        # Import signals to register them with Django's signal dispatcher.
        import accounts.signals  # noqa: F401
