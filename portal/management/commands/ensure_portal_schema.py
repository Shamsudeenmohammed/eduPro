"""
Create any admissions engine table that is missing from the database.

The engine records its models in migration *state* but builds the actual tables
from the models, so a database is brought up to date here (and automatically by
the `post_migrate` hook in :mod:`portal.apps`, which uses the same code path).

    python manage.py ensure_portal_schema            # create what is missing
    python manage.py ensure_portal_schema --dry-run  # list what would be created

Safe to run repeatedly — existing tables are never dropped, altered or emptied.
It only ever adds tables that the models declare and the database lacks, so it
cannot overwrite real admissions data.
"""

from django.core.management.base import BaseCommand

from portal.schema import create_missing, missing_models


class Command(BaseCommand):
    help = "Create missing admissions engine tables from the current models."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the tables that would be created without creating them.",
        )
        parser.add_argument(
            "--app",
            default="portal",
            help="App label to build tables for (default: portal).",
        )

    def handle(self, *args, **options):
        app_label = options["app"]
        pending = missing_models(app_label)

        if not pending:
            self.stdout.write(self.style.SUCCESS(
                f"All {app_label} tables are present."
            ))
            return

        self.stdout.write(f"{len(pending)} missing table(s):")
        for model in pending:
            self.stdout.write(f"  + {model._meta.db_table}")

        if options["dry_run"]:
            self.stdout.write("Dry run: nothing was created.")
            return

        create_missing(app_label, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS("Schema up to date."))
