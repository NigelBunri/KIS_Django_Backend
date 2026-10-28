"""Batch sweep for scheduled book publication — belt-and-suspenders
alongside the lazy per-request check in BookListView/BookDetailView
(apps.books.services.publish_scheduled_books). Intended to run on a
schedule (cron / celery-beat) so a scheduled book becomes visible even if
nobody happens to hit the marketplace list/detail endpoint right at its
scheduled time."""
from django.core.management.base import BaseCommand

from apps.books.services import publish_scheduled_books


class Command(BaseCommand):
    help = "Publish APPROVED books whose scheduled_publish_at has arrived."

    def handle(self, *args, **options):
        count = publish_scheduled_books()
        self.stdout.write(self.style.SUCCESS(f"Published {count} scheduled book(s)."))
