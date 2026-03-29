from django.core.management.base import BaseCommand

from apps.broadcasts.services import cleanup_expired_broadcast_items


class Command(BaseCommand):
    help = "Delete broadcast items that have been expired for longer than the retention window."

    def handle(self, *args, **options):
        deleted_count = cleanup_expired_broadcast_items()
        if deleted_count:
            self.stdout.write(
                self.style.SUCCESS(f"Deleted {deleted_count} expired broadcast item(s).")
            )
        else:
            self.stdout.write("No expired broadcast items found.")
