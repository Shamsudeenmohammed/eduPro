"""
Bulk import students from CSV.

CSV columns: email, first_name, last_name, student_number, program_code, password
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from accounts.models import EduProUser, Role
from academics.models import Program, StudentProfile


class Command(BaseCommand):
    help = "Import students from a CSV file"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to CSV file")

    def handle(self, *args, **options):
        path = Path(options["csv_file"])
        if not path.exists():
            self.stderr.write(f"File not found: {path}")
            return

        created = 0
        errors = []

        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    email = row["email"].strip().lower()
                    program = None
                    code = row.get("program_code", "").strip()
                    if code:
                        program = Program.objects.filter(code=code).first()

                    user, was_created = EduProUser.objects.get_or_create(
                        email=email,
                        defaults={
                            "first_name": row["first_name"].strip(),
                            "last_name": row["last_name"].strip(),
                            "role": Role.STUDENT,
                        },
                    )
                    if was_created:
                        user.set_password(row.get("password", "Student@123"))
                        user.save()
                        created += 1

                    profile, _ = StudentProfile.objects.get_or_create(student=user)
                    profile.student_number = row.get("student_number", "").strip() or profile.student_number
                    profile.program = program or profile.program
                    profile.save()

                except Exception as e:
                    errors.append(f"{row.get('email', '?')}: {e}")

        self.stdout.write(self.style.SUCCESS(f"Created {created} new students."))
        if errors:
            self.stderr.write(f"Errors ({len(errors)}):")
            for e in errors[:20]:
                self.stderr.write(f"  {e}")
