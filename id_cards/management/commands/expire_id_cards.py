from django.core.management.base import BaseCommand

from id_cards.services import process_expirations


class Command(BaseCommand):
    help = "Expire ID cards past their expiry date (optionally auto-renew)."

    def handle(self, *args, **options):
        count = process_expirations()
        self.stdout.write(
            self.style.SUCCESS(f"ID card expiry check complete: {count} card(s) expired.")
        )