"""
Expire hostel reservations that were never checked in.

Un-checked-in beds block availability, so this sweeps RESERVED allocations
whose ``reservation_expires_at`` has passed, releases the bed back to
AVAILABLE, and marks the allocation EXPIRED. Idempotent — safe to schedule
repeatedly (cron, Task Scheduler, etc.).

Usage::

    python manage.py expire_hostel_reservations
"""

from django.core.management.base import BaseCommand

from hostel.services import HostelAllocationService


class Command(BaseCommand):
    help = "Expire un-checked-in hostel reservations past their reservation window"

    def handle(self, *args, **options):
        expired = HostelAllocationService.expire_due_reservations()
        count = len(expired)

        if count:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Expired {count} reservation(s): {', '.join(str(pk) for pk in expired)}"
                )
            )
        else:
            self.stdout.write("No reservations due for expiry.")