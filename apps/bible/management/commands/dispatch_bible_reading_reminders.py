from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.bible.models import BibleReadingPlanEvent
from apps.notifications.services import create_notification


class Command(BaseCommand):
    help = "Create in-app/push notifications for due Bible reading planner reminders."

    def add_arguments(self, parser):
        parser.add_argument("--lookback-minutes", type=int, default=10)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        lookback = now - timezone.timedelta(minutes=options["lookback_minutes"])
        events = (
            BibleReadingPlanEvent.objects.filter(
                status="scheduled",
                start_at__gte=lookback - timezone.timedelta(days=1),
                start_at__lte=now + timezone.timedelta(days=1),
            )
            .select_related("user", "translation")
            .order_by("start_at")
        )
        created = 0
        checked = 0
        for event in events:
            offsets = event.reminder_offsets or [0]
            channels = event.reminder_channels or ["in_app"]
            for raw_offset in offsets:
                try:
                    offset = int(raw_offset)
                except (TypeError, ValueError):
                    continue
                due_at = event.start_at - timezone.timedelta(minutes=offset)
                if due_at < lookback or due_at > now:
                    continue
                checked += 1
                title = "Bible reading reminder"
                if offset == 0:
                    body = f"Time to read {event.passage_ref}."
                else:
                    body = f"{event.passage_ref} starts in {offset} minute{'s' if offset != 1 else ''}."
                dedup_key = f"bible-reading:{event.id}:{offset}:{due_at.isoformat()}"
                channel = "PUSH" if "push" in channels else "IN_APP"
                if options["dry_run"]:
                    self.stdout.write(f"DRY RUN {channel}: {event.user_id} {body}")
                    continue
                create_notification(
                    user_id=event.user_id,
                    type="BIBLE_READING_REMINDER",
                    channel=channel,
                    priority="MEDIUM",
                    dedup_key=dedup_key,
                    title=title,
                    body=body,
                    target_type="bible_reading_event",
                    source="bible",
                    context={
                        "event_id": str(event.id),
                        "target_id": str(event.id),
                        "target_type": "bible_reading_event",
                        "passage_ref": event.passage_ref,
                        "start_at": event.start_at.isoformat(),
                        "offset_minutes": offset,
                    },
                )
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Checked {checked} due reminders; created {created} notifications."))
