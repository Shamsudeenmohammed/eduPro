"""
``manage.py seed_admission_types``

Idempotently creates the configurable ``ApplicationType`` rows and their default
``ApplicationRequirement`` checklist.

This is a *data* command, not a migration. The Applications & Admissions engine
was built without shipping migrations, so this is the supported way to populate
the reference data an installation needs on first run.

Safe to run repeatedly: it only adds rows that are missing and never edits or
deletes existing configuration.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from portal.services import CycleService


class Command(BaseCommand):
    help = (
        "Create the standard admission application types and their default "
        "document/field requirements. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created without writing anything.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["dry_run"]:
            # Report against a rolled-back transaction so the preview is exact.
            self.stdout.write(self.style.WARNING("Dry run — nothing will be saved."))
            types, requirements = CycleService.bootstrap()
            transaction.set_rollback(True)
        else:
            types, requirements = CycleService.bootstrap()

        self.stdout.write(
            self.style.SUCCESS(
                f"Application types: {len(types)} created, "
                f"requirements: {len(requirements)} created."
            )
        )
        for code in types:
            self.stdout.write(f"  + application type: {code}")
        for name in requirements:
            self.stdout.write(f"  + requirement: {name}")
        if not types and not requirements:
            self.stdout.write("  nothing to do — already seeded.")
