"""
Initialize the scheduling module for an institution.

Creates the per-institution SchedulingConfig (if missing), seeds default
time slots for the configured workdays, reports available rooms/lecturers
and warns when the current semester has no offerings.

Usage:
    python manage.py init_scheduling [--institution-ids 1 2]
    python manage.py init_scheduling --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from academics.models import Institution
from scheduling.constants import DEFAULT_WORKDAYS, IsoWeekday, ScheduleStatus
from scheduling.models import Building, Room, SchedulingConfig, TimeSlot


class Command(BaseCommand):
    help = "Seed scheduling config + default time slots for an institution."

    def add_arguments(self, parser):
        parser.add_argument(
            "--institution-ids", nargs="+", type=int, default=None,
            help="Only process these institution PKs (default: all).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print what would be created without writing anything.",
        )

    def handle(self, *args, **options):
        qs = Institution.objects.all()
        if options.get("institution_ids"):
            qs = qs.filter(pk__in=options["institution_ids"])
        if not qs.exists():
            raise CommandError("No institutions found — run after migration.")

        for institution in qs:
            self._process(institution, dry_run=options["dry_run"])

    def _process(self, institution, dry_run=False):
        self.stdout.write(f"\n== {institution} ==")

        cfg, created = SchedulingConfig.objects.get_or_create(
            institution=institution,
            defaults={"workdays": list(DEFAULT_WORKDAYS)},
        )
        if dry_run and not created:
            self.stdout.write("  scheduling config: exists (skip)")
        elif created:
            self.stdout.write(self.style.SUCCESS(
                f"  scheduling config created ({cfg!s})"))
        else:
            self.stdout.write("  scheduling config: exists")

        if dry_run:
            self.stdout.write("  time slots: (dry-run, nothing written)")
            self._report(institution)
            return

        if not cfg.workdays:
            cfg.workdays = list(DEFAULT_WORKDAYS)
            cfg.save(update_fields=["workdays"])
            self.stdout.write("  config workdays backfilled")

        # ── default weekly slots (08:00 → 16:00, hourly) ───────────────
        slot_times = []
        for hour in range(8, 16):
            slot_times.append((f"{hour:02d}:00", f"{hour + 1:02d}:00"))

        created_slots = self._seed_slots(list(cfg.workday_set), slot_times)
        if created_slots:
            self.stdout.write(self.style.SUCCESS(
                f"  {created_slots} default time slot(s) created"))
        else:
            self.stdout.write("  all default slots already exist")

        self._report(institution)

    @staticmethod
    def _seed_slots(workdays, slot_times):
        created = 0
        for weekday in sorted(workdays):
            label = IsoWeekday(weekday).label
            for start_time, end_time in slot_times:
                slot, was_created = TimeSlot.objects.get_or_create(
                    day=weekday,
                    start_time=start_time,
                    end_time=end_time,
                    defaults={"is_default": True, "label": label},
                )
                if was_created:
                    created += 1
                elif not slot.is_default:
                    slot.is_default = True
                    slot.save(update_fields=["is_default"])
        return created

    def _report(self, institution):
        rooms = Room.objects.filter(
            building__institution=institution, is_active=True,
        )
        buildings = Building.objects.filter(institution=institution)
        windows = TimeSlot.objects.filter(is_default=True)
        self.stdout.write("  report:")
        self.stdout.write(f"    buildings            : {buildings.count()}")
        self.stdout.write(f"    active rooms         : {rooms.count()}")

        from academics.models import Semester

        current = Semester.get_current()
        if current:
            offerings = current.offerings.count()
            self.stdout.write(f"    current semester     : {current} "
                              f"({offerings} offering(s))")
            if offerings == 0:
                self.stdout.write(self.style.WARNING(
                    "    current semester has NO offerings — scheduling is "
                    "empty until courses are offered."))
        else:
            self.stdout.write(self.style.WARNING(
                "    no current semester set yet"))

        from scheduling.models import AcademicSchedule

        statuses = {s.value: 0 for s in ScheduleStatus}
        for row in (
            AcademicSchedule.objects.values("status")
            .annotate(n=Count("id"))
        ):
            statuses[row["status"]] = row["n"]
        self.stdout.write("    schedules by status  : " + ", ".join(
            f"{k}={v}" for k, v in statuses.items()))