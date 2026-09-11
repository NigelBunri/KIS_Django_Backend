"""
One-off/ops command: delete every registered Device across every account so
every user's next login registers a fresh parent device with no secondary/
pairing-code prompt. Accounts themselves are untouched.

Run:
  python3 manage.py wipe_all_devices --yes
  python3 manage.py wipe_all_devices --dry-run
"""
from django.core.management.base import BaseCommand

from apps.accounts.device_admin import wipe_all_devices
from apps.accounts.models import Device, User


class Command(BaseCommand):
    help = "Delete all Device rows for all users (accounts are kept)."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Actually perform the wipe.")
        parser.add_argument("--dry-run", action="store_true", help="Report counts without deleting anything.")

    def handle(self, *args, **options):
        total_users = User.objects.count()
        total_devices = Device.objects.count()

        if options["dry_run"] or not options["yes"]:
            self.stdout.write(f"Users: {total_users}")
            self.stdout.write(f"Devices that would be deleted: {total_devices}")
            self.stdout.write(self.style.WARNING(
                "Dry run only — pass --yes to actually delete these devices."
            ))
            return

        result = wipe_all_devices(actor=None, reason="ops_console_bulk_wipe")
        self.stdout.write(self.style.SUCCESS(
            f"Wiped devices for {result['users_affected']} users "
            f"({result['devices_deleted']} device rows deleted)."
        ))
