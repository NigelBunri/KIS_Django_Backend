"""Batch sweep for BookPublishingRestriction expiry — belt-and-suspenders
alongside the lazy per-check expiry in apps.books.services. Intended to run
on a schedule (cron / celery-beat) so a temporary restriction's expiry is
visible even for a user who never triggers the lazy check themselves."""
from django.core.management.base import BaseCommand

from apps.books.services import expire_stale_restrictions


class Command(BaseCommand):
    help = "Expire BookPublishingRestriction rows whose ends_at has passed."

    def handle(self, *args, **options):
        count = expire_stale_restrictions()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} restriction(s)."))
