"""
Seed the default "standard" grading scheme and its grade boundaries.

Idempotent: re-running never duplicates rows and never overwrites an
existing boundary's settings.

Usage:
    python manage.py seed_grading_scheme
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from academics.models import GradeBoundary, GradingScheme

DEFAULT_BOUNDARIES = [
    ("A+", Decimal("90.00"), None,          Decimal("4.0")),
    ("A",  Decimal("85.00"), Decimal("90.00"), Decimal("4.0")),
    ("A-", Decimal("80.00"), Decimal("85.00"), Decimal("3.7")),
    ("B+", Decimal("77.00"), Decimal("80.00"), Decimal("3.3")),
    ("B",  Decimal("73.00"), Decimal("77.00"), Decimal("3.0")),
    ("B-", Decimal("70.00"), Decimal("73.00"), Decimal("2.7")),
    ("C+", Decimal("67.00"), Decimal("70.00"), Decimal("2.3")),
    ("C",  Decimal("63.00"), Decimal("67.00"), Decimal("2.0")),
    ("C-", Decimal("60.00"), Decimal("63.00"), Decimal("1.7")),
    ("D",  Decimal("50.00"), Decimal("60.00"), Decimal("1.0")),
    ("F",  Decimal("0.00"),  Decimal("50.00"), Decimal("0.0")),
]


class Command(BaseCommand):
    help = "Seed the default 'standard' grading scheme and its grade boundaries."

    def handle(self, *args, **options):
        scheme, created = GradingScheme.objects.get_or_create(
            name="standard",
            defaults={
                "label": "Standard (A-F, 100pt, 4.0 scale)",
                "ca_weight": 30,
                "exam_weight": 70,
                "is_default": True,
                "is_active": True,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created scheme '{scheme.name}'."))

        if not GradingScheme.objects.filter(is_default=True).exists():
            scheme.is_default = True
            scheme.save(update_fields=["is_default"])
            self.stdout.write(self.style.SUCCESS("Set 'standard' as the default scheme."))

        created_count = 0
        for grade, min_score, max_score, grade_point in DEFAULT_BOUNDARIES:
            boundary, was_created = GradeBoundary.objects.get_or_create(
                scheme=scheme,
                grade=grade,
                defaults={
                    "min_score": min_score,
                    "max_score": max_score,
                    "grade_point": grade_point,
                    "is_active": True,
                },
            )
            if was_created:
                created_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Scheme '{scheme.name}' now has {scheme.boundaries.count()} boundary(ies) "
            f"({created_count} created, existing untouched)."
        ))