"""Load sample institution and academic data for development."""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from academics.models import (
    AcademicSession, Course, Department, Faculty, Institution,
    Level, Program, Semester,
)
from accounts.models import EduProUser, Role
from portal.models import PublicAnnouncement, WebsitePage


class Command(BaseCommand):
    help = "Load sample data for eduPro development"

    def handle(self, *args, **options):
        inst, _ = Institution.objects.get_or_create(
            name="eduPro Academy",
            defaults={
                "short_name": "eduPro",
                "email": "info@edupro.edu",
                "motto": "Excellence Through Education",
            },
        )

        faculty, _ = Faculty.objects.get_or_create(
            code="FOE", institution=inst,
            defaults={"name": "Faculty of Engineering"},
        )

        dept, _ = Department.objects.get_or_create(
            faculty=faculty, code="CSC",
            defaults={"name": "Computer Science", "institution": inst},
        )

        program, _ = Program.objects.get_or_create(
            department=dept, code="BSC-CS",
            defaults={"name": "B.Sc. Computer Science", "duration_years": 4},
        )

        Level.objects.get_or_create(program=program, order=1, defaults={"name": "Year 1"})
        Level.objects.get_or_create(program=program, order=2, defaults={"name": "Year 2"})

        session, _ = AcademicSession.objects.get_or_create(
            name="2025/2026",
            defaults={
                "start_date": date(2025, 9, 1),
                "end_date": date(2026, 8, 31),
                "is_current": True,
            },
        )

        Semester.objects.get_or_create(
            session=session, name="first",
            defaults={
                "start_date": date(2025, 9, 1),
                "end_date": date(2026, 1, 15),
                "is_current": True,
            },
        )

        for code, title in [
            ("CSC101", "Introduction to Programming"),
            ("CSC201", "Data Structures"),
            ("MTH101", "Calculus I"),
        ]:
            Course.objects.get_or_create(
                code=code,
                defaults={"title": title, "department": dept, "credit_units": 3},
            )

        WebsitePage.objects.get_or_create(
            slug="about",
            defaults={
                "title": "About eduPro Academy",
                "content": "<p>eduPro is a modern academic management platform.</p>",
                "is_published": True,
            },
        )

        PublicAnnouncement.objects.get_or_create(
            title="Welcome to the New Academic Session",
            defaults={
                "summary": "We welcome all students to the 2025/2026 academic session.",
                "content": "Classes begin September 1st. Check your timetable for schedules.",
                "is_published": True,
                "is_featured": True,
                "published_at": timezone.now(),
            },
        )

        self.stdout.write(self.style.SUCCESS("Sample data loaded successfully."))
