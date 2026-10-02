from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _create_missing_engine_tables(sender, **kwargs):
    """
    Build any engine table the migrations did not create.

    The engine records its models in migration *state* but leaves the actual
    tables to be built from the models (see ``ensure_portal_schema``). Hooking
    it to `post_migrate` means a fresh `migrate` — including the one that
    builds a test database — ends up with a complete schema without anyone
    having to remember a second step.
    """
    from .schema import create_missing

    try:
        create_missing("portal")
    except Exception:  # pragma: no cover - never block migrate on this
        import logging

        logging.getLogger("portal.schema").warning(
            "Could not create missing portal tables", exc_info=True
        )


class PortalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "portal"
    verbose_name = "Public Portal"

    def ready(self):
        # Every app label (the signal is global), but the handler is idempotent
        # and only ever looks at the portal app.
        post_migrate.connect(
            _create_missing_engine_tables,
            dispatch_uid="portal.create_missing_engine_tables",
        )
