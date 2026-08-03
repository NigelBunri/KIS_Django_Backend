# apps/media/management/commands/expire_media_uploads.py
"""Manual/cron fallback for expiring abandoned presigned-upload intents.

Wraps the exact same functions the Celery tasks
(apps.media.tasks.expire_abandoned_media_uploads,
apps.media.tasks.expire_unattached_media_uploads) call — this command
exists for deployments that don't run Celery Beat, not as a second
implementation. Safe to run repeatedly; never touches an attached upload.
"""
from django.core.management.base import BaseCommand

from apps.media.upload_intent import (
    expire_abandoned_upload_intents,
    expire_unattached_confirmed_intents,
)


class Command(BaseCommand):
    help = (
        "Expire abandoned direct-to-S3 upload intents (presigned URL issued, never "
        "confirmed) and confirmed-but-never-attached intents."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of expired intents to process per sweep in this run (default: 500).",
        )
        parser.add_argument(
            "--unattached-grace-seconds",
            type=int,
            default=None,
            help="Override MEDIA_UPLOAD_UNATTACHED_GRACE_SECONDS for this run.",
        )

    def handle(self, *args, **options):
        abandoned = expire_abandoned_upload_intents(limit=options["limit"])
        unattached = expire_unattached_confirmed_intents(
            limit=options["limit"], grace_period_seconds=options["unattached_grace_seconds"]
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Abandoned: expired {abandoned['expired_count']} intent(s), "
                f"{abandoned['cleanup_failures']} S3 cleanup failure(s). "
                f"Unattached: expired {unattached['expired_count']} intent(s), "
                f"{unattached['cleanup_failures']} S3 cleanup failure(s)."
            )
        )
