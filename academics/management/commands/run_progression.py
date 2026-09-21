"""
Run the annual academic progression engine for a session.

Evaluates each active student's standing for the given academic session
(advance / probation / repeat / withdrawn / deferred / completed) and,
unless --dry-run is given, records a StudentStatus entry and advances
qualifying students to the next level.

Usage:
    python manage.py run_progression [--session 2025/2026] [--dry-run]
    python manage.py run_progression --student 12345
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from academics.models import AcademicSession, StudentProfile
from academics.services import apply_progression, evaluate_progression


class Command(BaseCommand):
    help = "Evaluate (and apply) annual academic progression for a session"

    def add_arguments(self, parser):
        parser.add_argument(
            "--session",
            help="Academic session name (defaults to the current session)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Evaluate without persisting any decisions or level changes",
        )
        parser.add_argument(
            "--student",
            help="Restrict to a single student by ID or student number",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        session = self._resolve_session(options["session"])

        profiles = StudentProfile.objects.filter(
            is_active=True,
            current_level__isnull=False,
        ).select_related("student", "program", "current_level")

        if options["student"]:
            val = options["student"]
            profiles = profiles.filter(student__id__iexact=val) | \
                profiles.filter(student_number__iexact=val)

        actor = None
        if not dry_run:
            User = get_user_model()
            actor = User.objects.filter(is_superuser=True).order_by("pk").first()

        summary = {}
        for profile in profiles.iterator():
            outcome = evaluate_progression(profile.student, session)
            decision = outcome["decision"]
            summary[decision] = summary.get(decision, 0) + 1
            self._print_outcome(outcome)

            if not dry_run:
                apply_progression(profile.student, session, outcome, actor=actor)

        if not summary:
            self.stdout.write(self.style.WARNING(
                "No active students found for this session."
            ))
            return

        parts = ", ".join(f"{k}={v}" for k, v in sorted(summary.items()))
        action = "Evaluated (dry run)" if dry_run else "Applied"
        self.stdout.write(self.style.SUCCESS(f"\nDone. [{action}]  {parts}"))

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

    def _print_outcome(self, outcome):
        name = outcome["student"].get_full_name()
        decision = outcome["decision"].upper()
        gpa = outcome["session_gpa"]
        gpa_txt = f"  GPA={gpa:.2f}" if gpa is not None else "  GPA=—"
        nxt = outcome.get("next_level")
        nxt_txt = f"  → {nxt.name}" if nxt else ""
        self.stdout.write(f"  {name:30s} {decision:10s}{gpa_txt}{nxt_txt}")