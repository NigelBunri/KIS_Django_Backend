# apps/media/management/commands/expire_media_uploads.py
"""Manual/cron fallback for expiring abandoned presigned-upload intents.

Wraps the exact same function the Celery task
(apps.media.tasks.expire_abandoned_media_uploads) calls — this command
exists for deployments that don't run Celery Beat, not as a second
implementation. Safe to run repeatedly; never touches confirmed uploads.
"""
from django.core.management.base import BaseCommand

from apps.media.upload_intent import expire_abandoned_upload_intents


class Command(BaseCommand):
    help = "Expire abandoned direct-to-S3 upload intents (presigned URL issued, never confirmed)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of expired intents to process in this run (default: 500).",
        )

    def handle(self, *args, **options):
        result = expire_abandoned_upload_intents(limit=options["limit"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {result['expired_count']} upload intent(s); "
                f"{result['cleanup_failures']} S3 cleanup failure(s)."
            )
        )
