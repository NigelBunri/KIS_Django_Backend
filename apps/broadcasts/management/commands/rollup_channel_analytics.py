from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.broadcasts.models import (
    BroadcastChannel,
    ChannelAnalyticsDailyRollup,
    ChannelContent,
    ChannelContentComment,
    ChannelContentReaction,
    ChannelContentSave,
    ChannelWatchHistory,
)


class Command(BaseCommand):
    help = "Build daily analytics rollups for normalized broadcast channels."

    def add_arguments(self, parser):
        parser.add_argument("--date", dest="date", default="", help="Rollup date in YYYY-MM-DD format. Defaults to yesterday.")

    def handle(self, *args, **options):
        rollup_date = timezone.datetime.fromisoformat(options["date"]).date() if options["date"] else timezone.localdate() - timedelta(days=1)
        start = timezone.make_aware(timezone.datetime.combine(rollup_date, timezone.datetime.min.time()))
        end = start + timedelta(days=1)
        updated = 0
        for channel in BroadcastChannel.objects.filter(is_deleted=False).iterator():
            contents = ChannelContent.objects.filter(channel=channel, is_deleted=False)
            history = ChannelWatchHistory.objects.filter(content__channel=channel, last_viewed_at__gte=start, last_viewed_at__lt=end)
            payload = {
                "views": history.count(),
                "unique_viewers": history.values("user_id").distinct().count(),
                "watch_time_seconds": sum(history.values_list("progress_seconds", flat=True)),
                "shares": sum(int((content.stats or {}).get("shares") or 0) for content in contents.only("stats")),
                "saves": ChannelContentSave.objects.filter(content__channel=channel, created_at__gte=start, created_at__lt=end).count(),
                "comments": ChannelContentComment.objects.filter(content__channel=channel, is_deleted=False, created_at__gte=start, created_at__lt=end).count(),
                "reactions": ChannelContentReaction.objects.filter(content__channel=channel, created_at__gte=start, created_at__lt=end).count(),
                "metadata": {"source": "rollup_channel_analytics"},
            }
            if payload["views"] and payload["watch_time_seconds"]:
                payload["average_duration_seconds"] = max(int(payload["watch_time_seconds"] / payload["views"]), 0)
            ChannelAnalyticsDailyRollup.objects.update_or_create(
                channel=channel,
                content=None,
                date=rollup_date,
                defaults=payload,
            )
            updated += 1
        self.stdout.write(self.style.SUCCESS(f"Rolled up {updated} channels for {rollup_date.isoformat()}"))
