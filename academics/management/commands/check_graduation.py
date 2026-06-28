"""
Check graduation eligibility for active students.

A student is eligible to graduate only if they have no outstanding
failed courses — i.e., every course they failed (grade F, I, or W) has
been successfully retaken (retake enrolment with a non-failing grade).

Usage:
    python manage.py check_graduation
    python manage.py check_graduation --student 12345
    python manage.py check_graduation --ineligible --csv
    python manage.py check_graduation --eligible --csv
"""

import csv

from django.core.management.base import BaseCommand

from academics.models import StudentProfile


class Command(BaseCommand):
    help = "Check graduation eligibility for active students"

    def add_arguments(self, parser):
        parser.add_argument(
            "--student",
            help="Check a specific student by ID or student_number",
        )
        parser.add_argument(
            "--eligible",
            action="store_true",
            help="Show only eligible students",
        )
        parser.add_argument(
            "--ineligible",
            action="store_true",
            help="Show only ineligible students",
        )
        parser.add_argument(
            "--csv",
            action="store_true",
            help="Output as CSV",
        )
    def handle(self, *args, **options):
        qs = StudentProfile.objects.filter(
            is_active=True,
            program__isnull=False,
            current_level__isnull=False,
        ).select_related("student", "program", "current_level")

        if options["student"]:
            val = options["student"]
            qs = qs.filter(
                student__id__iexact=val,
            ) | qs.filter(student_number__iexact=val)

        total = 0
        eligible = 0
        ineligible = 0
        rows = []

        for profile in qs.iterator():
            total += 1
            result = profile.check_graduation_eligibility()
            name = profile.student.get_full_name()
            sid = profile.student_number or "—"
            level = profile.current_level.name if profile.current_level else "—"
            program = profile.program.code if profile.program else "—"

            status = "ELIGIBLE" if result["eligible"] else "INELIGIBLE"
            if result["eligible"]:
                eligible += 1
            else:
                ineligible += 1

            n_no_retake = len(result["failed_without_retake"])
            n_failed_retake = len(result["failed_with_failed_retake"])
            n_passed_retake = len(result["failed_with_passed_retake"])
            flag = ""

            if n_no_retake:
                courses = ", ".join(
                    e.offering.course.code for e in result["failed_without_retake"]
                )
                flag = f"  FAILED (no retake): {courses}"
            elif n_failed_retake:
                courses = ", ".join(
                    e[0].offering.course.code for e in result["failed_with_failed_retake"]
                )
                flag = f"  RETRY FAILED: {courses}"

            if options["eligible"] and not result["eligible"]:
                continue
            if options["ineligible"] and result["eligible"]:
                continue

            if options["csv"]:
                rows.append([
                    sid, name, program, level, status,
                    n_no_retake, n_failed_retake, n_passed_retake,
                ])
            else:
                self.stdout.write(
                    f"{sid:15s} {name:30s} {program:6s} {level:>4s}  {status:10s}{flag}"
                )

        if options["csv"]:
            writer = csv.writer(self.stdout)
            writer.writerow(["StudentID", "Name", "Program", "Level", "Status",
                             "FailedNoRetake", "FailedRetake", "PassedRetake"])
            writer.writerows(rows)
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone. Total={total}  Eligible={eligible}  Ineligible={ineligible}"
            ))
