"""
Advance all active students to the next level at the end of an academic year.

Usage:
    python manage.py advance_students [--dry-run]

This finds the current level for each active student, looks up the next
level (by order) in the same program, and promotes them. Students already
at the highest level are left unchanged.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from academics.models import StudentProfile, Level


class Command(BaseCommand):
    help = "Advance all active students to the next level"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        qs = StudentProfile.objects.filter(
            is_active=True,
            current_level__isnull=False,
        ).select_related("current_level", "program")

        promoted = 0
        at_max = 0
        skipped = 0

        for profile in qs:
            if not profile.program:
                skipped += 1
                continue

            current = profile.current_level
            next_level = (
                Level.objects
                .filter(program=profile.program, order=current.order + 1)
                .first()
            )

            if not next_level:
                at_max += 1
                continue

            if dry_run:
                self.stdout.write(
                    f"  {profile.student.get_full_name():30s} "
                    f"{current.name:>4s} → {next_level.name}"
                )
            else:
                with transaction.atomic():
                    profile.current_level = next_level
                    profile.save(update_fields=["current_level", "updated_at"])
                promoted += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Promoted={promoted}  Already-at-max={at_max}  Skipped={skipped}"
        ))