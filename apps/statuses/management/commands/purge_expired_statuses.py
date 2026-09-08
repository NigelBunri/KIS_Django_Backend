# apps/statuses/management/commands/purge_expired_statuses.py
"""Manual/cron fallback for hard-deleting expired/soft-deleted StatusItem
rows and their media. Wraps apps.statuses.services.purge_expired_statuses -
see that function's docstring for why this exists and what it purges.
Mirrors apps/media/management/commands/expire_media_uploads.py's own
documented purpose and structure. Safe to run repeatedly.
"""
from django.core.management.base import BaseCommand

from apps.statuses.services import purge_expired_statuses


class Command(BaseCommand):
    help = "Hard-delete expired or soft-deleted Status rows and their media, past a grace period."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of statuses to purge per run (default: 500).",
        )
        parser.add_argument(
            "--grace-days",
            type=int,
            default=7,
            help="Days past expires_at before a row is eligible for hard deletion (default: 7).",
        )

    def handle(self, *args, **options):
        result = purge_expired_statuses(limit=options["limit"], grace_days=options["grace_days"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {result['purged_count']} status(es), "
                f"{result['file_cleanup_failures']} file cleanup failure(s)."
            )
        )
