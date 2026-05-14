from __future__ import annotations

from dataclasses import dataclass
import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.broadcasts.feed_entry_store import get_feed_entries, sync_channel_content_from_feed_entry
from apps.broadcasts.models import (
    BroadcastChannel,
    BroadcastFeedProfile,
    BroadcastItem,
    BroadcastSourceType,
    ChannelContent,
)


@dataclass
class BackfillCounts:
    profiles_seen: int = 0
    profiles_changed: int = 0
    entries_seen: int = 0
    entries_backfilled: int = 0
    channels_created: int = 0
    content_created: int = 0
    content_updated: int = 0
    broadcast_items_linked: int = 0
    skipped_invalid_entries: int = 0
    errors: int = 0


class Command(BaseCommand):
    help = "Backfill BroadcastFeedProfile JSON feed entries into normalized BroadcastChannel/ChannelContent rows."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
        parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode.")
        parser.add_argument("--limit", type=int, default=500, help="Maximum profile rows to scan.")

    def handle(self, *args, **options):
        apply = bool(options.get("apply")) and not bool(options.get("dry_run"))
        limit = max(int(options.get("limit") or 500), 1)
        counts = BackfillCounts()
        rows = (
            BroadcastFeedProfile.objects.select_related("profile", "profile__user")
            .order_by("created_at")
            [:limit]
        )

        for feed_profile in rows:
            counts.profiles_seen += 1
            try:
                self._process_profile(feed_profile, apply=apply, counts=counts)
            except Exception as exc:
                counts.errors += 1
                self.stderr.write(f"profile={feed_profile.id} skipped error={exc.__class__.__name__}")

        mode = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(
            self.style.SUCCESS(
                " ".join(
                    [
                        f"mode={mode}",
                        f"profiles_seen={counts.profiles_seen}",
                        f"profiles_changed={counts.profiles_changed}",
                        f"entries_seen={counts.entries_seen}",
                        f"entries_backfilled={counts.entries_backfilled}",
                        f"channels_created={counts.channels_created}",
                        f"content_created={counts.content_created}",
                        f"content_updated={counts.content_updated}",
                        f"broadcast_items_linked={counts.broadcast_items_linked}",
                        f"skipped_invalid_entries={counts.skipped_invalid_entries}",
                        f"errors={counts.errors}",
                    ]
                )
            )
        )

    def _process_profile(self, feed_profile: BroadcastFeedProfile, *, apply: bool, counts: BackfillCounts):
        profile = feed_profile.profile
        user = getattr(profile, "user", None)
        entries = get_feed_entries(feed_profile.payload)
        if not user or not entries:
            return

        profile_changed = False
        existing_channel = BroadcastChannel.objects.filter(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=user.id,
            owner_user=user,
            is_deleted=False,
        ).order_by("created_at").first()
        if not existing_channel:
            counts.channels_created += 1

        for entry in entries:
            counts.entries_seen += 1
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id:
                counts.skipped_invalid_entries += 1
                continue
            try:
                uuid.UUID(entry_id)
            except ValueError:
                counts.skipped_invalid_entries += 1
                continue

            existing_content = ChannelContent.objects.filter(legacy_feed_entry_id=entry_id).first()
            if existing_content:
                counts.content_updated += 1
            else:
                counts.content_created += 1

            broadcast_item = BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
                source_id=entry_id,
                broadcasted_by=user,
                is_deleted=False,
            ).first()
            if broadcast_item:
                metadata = broadcast_item.metadata if isinstance(broadcast_item.metadata, dict) else {}
                if not metadata.get("channel_content_id"):
                    counts.broadcast_items_linked += 1

            if apply:
                with transaction.atomic():
                    content = sync_channel_content_from_feed_entry(user, feed_profile.payload, entry, broadcast_item=broadcast_item)
                    if content:
                        counts.entries_backfilled += 1
                        profile_changed = True
            else:
                counts.entries_backfilled += 1
                profile_changed = True

        if apply:
            channel = BroadcastChannel.objects.filter(
                owner_type=BroadcastChannel.OwnerType.USER,
                owner_id=user.id,
                owner_user=user,
                is_deleted=False,
            ).order_by("created_at").first()
            if channel:
                count = ChannelContent.objects.filter(channel=channel, is_deleted=False).count()
                BroadcastChannel.objects.filter(id=channel.id).update(content_count=count)
        if profile_changed:
            counts.profiles_changed += 1
