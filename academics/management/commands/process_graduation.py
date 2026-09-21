"""
Evaluate graduation eligibility and create graduation records.

Only active students already at the final level of their programme are
considered. Eligible students get a CONFIRMED GraduationRecord; ineligible
students get a REJECTED record with the reasons attached.

Usage:
    python manage.py process_graduation [--session 2025/2026] [--dry-run]
    python manage.py process_graduation --program CS
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from academics.models import AcademicSession, StudentProfile
from academics.services import (
    create_graduation_record,
    evaluate_graduation,
    is_final_level,
)


class Command(BaseCommand):
    help = "Evaluate final-level students and create graduation records"

    def add_arguments(self, parser):
        parser.add_argument(
            "--session",
            help="Completing academic session (defaults to current)",
        )
        parser.add_argument(
            "--program",
            help="Restrict to a program code",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report eligibility without writing records",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        session = self._resolve_session(options["session"])

        profiles = StudentProfile.objects.filter(
            is_active=True,
            program__isnull=False,
            current_level__isnull=False,
        ).select_related("student", "program", "current_level")

        if options["program"]:
            profiles = profiles.filter(program__code__iexact=options["program"])

        confirmed = 0
        rejected = 0
        for profile in profiles.iterator():
            result = evaluate_graduation(profile.student)
            if result["profile"] is None or not result["profile"].current_level:
                continue
            if not is_final_level(result["profile"]):
                continue

            name = profile.student.get_full_name()
            status = "CONFIRMED" if result["eligible"] else "REJECTED"
            cls = (
                result["award_class"].replace("_", " ").title()
                if result["eligible"] else "—"
            )
            gpa = result["cgpa"]
            gpa_txt = f"{gpa:.2f}" if gpa is not None else "—"
            self.stdout.write(
                f"  {name:30s} {status:10s} CGPA={gpa_txt}  Class={cls}"
            )

            if dry_run:
                if result["eligible"]:
                    confirmed += 1
                else:
                    rejected += 1
                continue

            actor = self._actor()
            record = create_graduation_record(profile.student, session, actor=actor)
            if record is None:
                continue
            if record.eligible:
                confirmed += 1
            else:
                rejected += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nDry run. Would create {confirmed + rejected} records "
                f"({confirmed} confirmed, {rejected} rejected)."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone. Records={confirmed + rejected}  "
                f"Confirmed={confirmed}  Rejected={rejected}"
            ))

    def _resolve_session(self, name):
        if name:
            session = AcademicSession.objects.filter(name=name).first()
            if session is None:
                raise CommandError(f"No academic session named '{name}'.")
            return session
        session = AcademicSession.get_current()
        if session is None:
            raise CommandError(
                "No --session given and no session marked as current. "
                "Pass --session '2025/2026'."
            )
        return session

    def _actor(self):
        User = get_user_model()
        return User.objects.filter(is_superuser=True).order_by("pk").first()