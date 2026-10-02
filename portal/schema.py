"""
Build the admissions engine's tables from the models instead of from migrations.

The engine's schema lives on the models: `manage.py ensure_portal_schema` and
the `post_migrate` hook in :mod:`portal.apps` both come through here, so a
database is brought up to date the same way whichever path is taken.

Only ever *adds* tables the models declare and the database lacks. Nothing is
dropped, altered or emptied, so running it against a live database cannot touch
real admissions data.
"""

from django.apps import apps
from django.db import connection, models as db_models


def missing_models(app_label="portal"):
    """Models in *app_label* whose table is not present in the database."""
    config = apps.get_app_config(app_label)
    existing = set(connection.introspection.table_names())
    return [
        model
        for model in config.get_models(include_auto_created=False)
        # A swapped model is provided by another app, so it is not ours to build.
        if not model._meta.swapped and model._meta.db_table not in existing
    ]


def create_missing(app_label="portal", stdout=None):
    """
    Create the tables returned by :func:`missing_models`.

    Returns the list of table names created. Safe to call repeatedly — tables
    that already exist are left untouched.
    """
    models = missing_models(app_label)
    if not models:
        return []
    # Explicit order so a table is never built before something it points at.
    models.sort(key=lambda m: len([f for f in m._meta.fields if f.is_relation and f.auto_created]))
    created = []
    with connection.schema_editor() as editor:
        for model in models:
            editor.create_model(model)
            created.append(model._meta.db_table)
            if stdout is not None:
                stdout.write(f"  created {model._meta.db_table}")
    return created
