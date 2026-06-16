"""Export students to CSV."""

import csv
import sys

from django.core.management.base import BaseCommand

from academics.models import StudentProfile


class Command(BaseCommand):
    help = "Export students to CSV (stdout or file)"

    def add_arguments(self, parser):
        parser.add_argument("-o", "--output", type=str, default="")

    def handle(self, *args, **options):
        profiles = StudentProfile.objects.select_related("student", "program").filter(is_active=True)
        out = open(options["output"], "w", newline="", encoding="utf-8") if options["output"] else sys.stdout
        writer = csv.writer(out)
        writer.writerow(["email", "first_name", "last_name", "student_number", "program_code", "gpa"])
        for p in profiles:
            writer.writerow([
                p.student.email,
                p.student.first_name,
                p.student.last_name,
                p.student_number,
                p.program.code if p.program else "",
                p.cumulative_gpa or "",
            ])
        if options["output"]:
            out.close()
            self.stdout.write(self.style.SUCCESS(f"Exported to {options['output']}"))
