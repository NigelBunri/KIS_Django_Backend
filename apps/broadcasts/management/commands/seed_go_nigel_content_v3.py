"""Third production content batch, split across two real accounts: GO
(apps.partners.seed.get_go_user) and the separate user "Nigel Bah" (phone
+2347013341831 - NOT the GO identity; a distinct real account on
production). 200 items total: 100 image, 40 video, 20 text, 20 document
(PDF), 20 freestyle (5 poll / 5 link / 5 rich_text / 5 gallery) - split
100/100 between the two accounts.

All media is real: downloaded from verified-current, properly-licensed
Wikimedia Commons sources (2024-2025 photos; videos transcoded from webm
to faststart H.264 mp4 for cross-platform playback - see
kis_go_broadcast_content_and_media_gotchas memory on why faststart
matters) plus 20 originally-written PDF study guides rendered via
reportlab. Every file was uploaded as a real object to this app's own S3
bucket under broadcast_v2/{images,videos,pdfs}/ - nothing here links to
a third-party host.

ITEMS (the fixed 200-item content plan, with real S3 URLs baked in) lives
in seed_v3_data.py alongside this command - generated once from the
drafted content + S3 upload manifest, not regenerated at runtime.

GO's half lands in the same 5 named channels as batches 1-2 (handles
unchanged). Nigel has no named channels of his own, so his half goes
through the channel-less/personal-channel path
(apps.broadcasts.feed_entry_store.sync_channel_content_from_feed_entry),
which auto-provisions a personal BroadcastChannel for him on first use -
same mechanism already used for GO's own channel-less posts in batches
1-2.

Usage (on the production box):
    python manage.py seed_go_nigel_content_v3
"""
from __future__ import annotations

import random
from datetime import timedelta
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.broadcasts.feed_entry_store import sync_channel_content_from_feed_entry
from apps.broadcasts.models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelContent,
    ChannelContentAsset,
    ContentModerationStatus,
)
from apps.broadcasts.seed_data.seed_v3_data import ITEMS
from apps.partners.seed import get_go_user

SEED_TAG = "go_nigel_content_2026_11_v3"
RANDOM_SEED = 20261118
NIGEL_PHONE = "+2347013341831"

CHANNELS = [
    {"handle": "daily-devotionals", "display_name": "Daily Devotionals"},
    {"handle": "morning-evening-prayers", "display_name": "Morning & Evening Prayers"},
    {"handle": "scripture-and-art", "display_name": "Scripture & Art"},
    {"handle": "hymns-and-worship", "display_name": "Hymns & Worship"},
    {"handle": "faith-encouragement", "display_name": "Faith Encouragement"},
]


class Command(BaseCommand):
    help = "Seed 200 items (100 GO / 100 Nigel) of current, real-file Christian+education content."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if len(ITEMS) != 200:
            raise CommandError(f"Expected exactly 200 content items, found {len(ITEMS)}.")

        rng = random.Random(RANDOM_SEED)
        shuffled = list(ITEMS)
        rng.shuffle(shuffled)

        go_items = shuffled[:100]
        nigel_items = shuffled[100:]

        counts = {}
        for it in ITEMS:
            counts[it["content_type"]] = counts.get(it["content_type"], 0) + 1
        self.stdout.write(self.style.SUCCESS(f"Content type breakdown (all 200): {counts}"))

        if options["dry_run"]:
            self.stdout.write(f"GO would get {len(go_items)} items, Nigel would get {len(nigel_items)} items.")
            return

        go_user = get_go_user()
        if not go_user:
            raise CommandError("GO user not found - refusing to fabricate the identity.")

        nigel_user = User.objects.filter(phone=NIGEL_PHONE).first()
        if not nigel_user:
            raise CommandError(
                f"Nigel user not found (phone={NIGEL_PHONE}) - refusing to fabricate this "
                "identity either. This must be a real, already-existing account."
            )
        if nigel_user.id == go_user.id:
            raise CommandError("Nigel's phone resolved to the GO account - refusing to proceed.")

        self.stdout.write(self.style.SUCCESS(
            f"GO: {go_user.id} ({go_user.email}) — Nigel: {nigel_user.id} ({nigel_user.phone})"
        ))

        # Split each account's 100 into channelless (15) + channel-scoped (85),
        # same ratio as prior batches. Channel-less goes first - see batch 1's
        # memory note on why order matters for _get_or_create_personal_channel.
        self._seed_account(go_user, go_items, seed_named_channels=True)
        self._seed_account(nigel_user, nigel_items, seed_named_channels=False)

        self.stdout.write(self.style.SUCCESS("Done."))

    def _seed_account(self, user, items: list[dict], *, seed_named_channels: bool) -> None:
        channelless_count = 15
        channelless_items = items[:channelless_count]
        channel_items = items[channelless_count:]

        self.stdout.write(self.style.SUCCESS(
            f"[{user.phone or user.email}] {len(channelless_items)} channel-less, "
            f"{len(channel_items)} channel-scoped."
        ))

        self._seed_channelless_items(user, channelless_items)

        if seed_named_channels:
            with transaction.atomic():
                channels = self._seed_named_channels(user)
                self._seed_channel_items(channels, channel_items)
        else:
            # Nigel has no named channels - route the rest through his
            # personal channel too (same auto-provisioned channel the
            # channel-less items above already created).
            self._seed_channelless_items(user, channel_items)

    def _seed_named_channels(self, go_user) -> list[BroadcastChannel]:
        channels = []
        for cfg in CHANNELS:
            channel, created = BroadcastChannel.objects.get_or_create(
                handle=cfg["handle"],
                defaults={
                    "owner_type": BroadcastChannel.OwnerType.USER,
                    "owner_id": go_user.id,
                    "owner_user": go_user,
                    "display_name": cfg["display_name"],
                    "category": "faith",
                    "is_public": True,
                    "is_verified": True,
                },
            )
            BroadcastChannelRole.objects.get_or_create(
                channel=channel, user=go_user, role=BroadcastChannelRole.Role.OWNER,
            )
            channels.append(channel)
            self.stdout.write(f"Channel ready: @{channel.handle} ({'created' if created else 'existing'})")
        return channels

    def _seed_channel_items(self, channels: list[BroadcastChannel], items: list[dict]) -> None:
        now = timezone.now()
        for index, item in enumerate(items):
            channel = channels[index % len(channels)]
            published_at = now - timedelta(hours=(len(items) - index))
            metadata = {"seeded": True, "seed_source": SEED_TAG}
            if item.get("poll"):
                metadata["poll"] = item["poll"]
            if item.get("link"):
                metadata["link"] = item["link"]

            content, _ = ChannelContent.objects.update_or_create(
                channel=channel,
                text_plain=item["text_plain"],
                content_type=item["content_type"],
                defaults={
                    "title": item["title"],
                    "description": item["description"],
                    "text_doc": item.get("text_doc") or {},
                    "visibility": ChannelContent.Visibility.PUBLIC,
                    "status": ChannelContent.Status.PUBLISHED,
                    "published_at": published_at,
                    "moderation_status": ContentModerationStatus.PASSED,
                    "moderation_passed_at": published_at,
                    "moderation_expires_at": now + timedelta(days=365),
                    "metadata": metadata,
                    "tags": ["faith", "education", item["content_type"]],
                },
            )
            ChannelContentAsset.objects.filter(content=content).delete()
            for pos, asset in enumerate(item["assets"]):
                ChannelContentAsset.objects.create(
                    content=content,
                    asset_type=asset["asset_type"],
                    url=asset["url"],
                    caption=asset.get("caption", ""),
                    mime_type=asset.get("mime_type", ""),
                    sort_order=asset.get("sort_order", pos),
                    processing_status="ready",
                    metadata={"seeded": True},
                )
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(items)} channel-scoped posts."))

    def _seed_channelless_items(self, user, items: list[dict]) -> None:
        now = timezone.now()
        for item in items:
            entry = {
                "id": str(uuid4()),
                "is_broadcast": True,
                "content_type": item["content_type"],
                "title": item["title"],
                "text_plain": item["text_plain"],
                "summary": item["description"],
                "attachments": [
                    {
                        "url": asset["url"],
                        "asset_type": asset["asset_type"],
                        "caption": asset.get("caption", ""),
                        "mime_type": asset.get("mime_type", ""),
                    }
                    for asset in item["assets"]
                ],
            }
            if item.get("poll"):
                entry["poll"] = item["poll"]
            if item.get("link"):
                entry["link"] = item["link"]

            content = sync_channel_content_from_feed_entry(user, None, entry)
            if content is None:
                self.stdout.write(self.style.WARNING(f"Skipped channel-less item: {item['title']}"))
                continue
            content.moderation_status = ContentModerationStatus.PASSED
            content.moderation_passed_at = now
            content.moderation_expires_at = now + timedelta(days=365)
            metadata = dict(content.metadata or {})
            metadata["seeded"] = True
            metadata["seed_source"] = SEED_TAG
            content.metadata = metadata
            content.save(update_fields=["moderation_status", "moderation_passed_at", "moderation_expires_at", "metadata"])
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(items)} channel-less feed posts."))
