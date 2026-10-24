"""Seeds four original, fictional creator channels with a realistic content
library, playlists, synthetic viewer accounts, and real subscription/
reaction/comment/watch-history rows — built to exercise KIS's channel,
Channel Studio, and discovery systems under a non-trivial content library.

SANDBOX ONLY. This command refuses to run unless
DJANGO_SETTINGS_MODULE=config.settings.creator_sandbox, so it can never be
pointed at the real dev/shared database by accident:

    DJANGO_SETTINGS_MODULE=config.settings.creator_sandbox \\
        python manage.py seed_creator_channels

Idempotent — keyed by channel handle / user phone number — safe to re-run.

Every account created here is clearly synthetic: phone numbers in the
555090xxxx block, emails under @kis.sandbox.local, display names prefixed
"Sandbox". No subscriber/view/like count is invented — every number comes
from an actual row this command created (BroadcastChannelSubscription,
ChannelWatchHistory, ChannelContentReaction, ChannelContentComment), and
ChannelContent.stats is recomputed via the same _sync_channel_content_counts
helper the real reaction/comment/save API endpoints use, not hand-set.

Media: avatars/banners/thumbnails are locally generated solid-color/
pattern PNGs (ffmpeg lavfi, no external source, no licensing concern).
Each channel also gets a small number of real, locally-generated MP4 test
videos (ffmpeg testsrc2 + sine tone) reused across several "episodes" —
genuinely valid, playable video files exercised through the actual
ChannelContent/ChannelContentAsset pipeline, not fabricated metadata. This
is a labeled test-media strategy (synthetic placeholder footage), not real
creator-produced video content.
"""
from __future__ import annotations

import os
import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import User
from apps.broadcasts.models import (
    BroadcastChannel,
    BroadcastChannelRole,
    BroadcastChannelSubscription,
    BroadcastPlaylist,
    BroadcastPlaylistItem,
    ChannelContent,
    ChannelContentAsset,
    ChannelContentComment,
    ChannelContentReaction,
    ChannelContentType,
    ChannelWatchHistory,
    ContentModerationStatus,
)
from apps.broadcasts.views import _sync_channel_content_counts  # reuse the real counting logic

MEDIA_SUBDIR = "creator_sandbox"


def _media_url(slug: str, filename: str) -> str:
    # Must be absolute — apps.core.public_web.safe_public_media_url (used
    # by every public broadcasts serializer) requires a parseable http(s)
    # scheme and silently blanks anything relative/schemeless.
    base = str(getattr(settings, "SITE_URL", "") or "").rstrip("/")
    path = settings.MEDIA_URL.rstrip("/") + f"/{MEDIA_SUBDIR}/{slug}/{filename}"
    return f"{base}{path}"


CHANNELS = [
    {
        "slug": "titan-arena",
        "display_name": "Titan Arena",
        "category": "entertainment",
        "description": (
            "Global-scale challenges, team competitions, and larger-than-life "
            "stunts — built for massive live audiences and shareable highlight "
            "moments. Original fictional creator identity, synthetic sandbox data."
        ),
        "owner": {"phone": "5550900001", "display_name": "Sandbox Titan Arena Creator", "email": "creator.titanarena@kis.sandbox.local"},
        "series": [
            ("World Challenge", "A team pushes a large-scale group challenge to its limit."),
            ("Giveaway Marathon", "A philanthropic challenge episode built around giving back."),
            ("Behind the Rig", "Behind-the-scenes look at how a stunt/challenge episode gets built."),
            ("Collab Showdown", "A crossover event episode with a guest creator team."),
        ],
    },
    {
        "slug": "pulse-grid",
        "display_name": "Pulse Grid",
        "category": "gaming",
        "description": (
            "Competitive gameplay, tournament runs, and community-driven gaming "
            "content, from ranked sessions to full tournament broadcasts. "
            "Original fictional creator identity, synthetic sandbox data."
        ),
        "owner": {"phone": "5550900002", "display_name": "Sandbox Pulse Grid Creator", "email": "creator.pulsegrid@kis.sandbox.local"},
        "series": [
            ("Ranked Grind", "A climb-the-ladder ranked gameplay episode."),
            ("Tournament Run", "A bracket-stage tournament match episode."),
            ("Patch Breakdown", "A tutorial/review episode covering a new patch or meta shift."),
            ("Community Clash", "A community-submitted challenge or clip-reaction episode."),
        ],
    },
    {
        "slug": "lumen-lab",
        "display_name": "Lumen Lab",
        "category": "education",
        "description": (
            "Science experiments, engineering breakdowns, and documentary-style "
            "explainers for a curious audience. Original fictional creator "
            "identity, synthetic sandbox data."
        ),
        "owner": {"phone": "5550900003", "display_name": "Sandbox Lumen Lab Creator", "email": "creator.lumenlab@kis.sandbox.local"},
        "series": [
            ("Experiment Log", "A hands-on science experiment walked through step by step."),
            ("Engineering Teardown", "An engineering/technology deep-dive explainer."),
            ("History Files", "A historical-discovery documentary-style episode."),
            ("Ask Lumen Lab", "A live Q&A / demonstration episode answering audience questions."),
        ],
    },
    {
        "slug": "wanderlux",
        "display_name": "Wanderlux",
        "category": "lifestyle",
        "description": (
            "Travel, culture, music, and lifestyle storytelling from around the "
            "world, blending documentary segments with original performance "
            "content. Original fictional creator identity, synthetic sandbox data."
        ),
        "owner": {"phone": "5550900004", "display_name": "Sandbox Wanderlux Creator", "email": "creator.wanderlux@kis.sandbox.local"},
        "series": [
            ("City Notebook", "A travel/culture documentary segment from one city."),
            ("Studio Sessions", "An original-music performance/behind-the-scenes episode."),
            ("Conversations", "An interview-style episode with a guest."),
            ("Style Journal", "A fashion/creative-project lifestyle episode."),
        ],
    },
]

VIEWER_COUNT = 36
ITEMS_PER_CHANNEL = 60

# Rough content-type mix per channel (sums to ITEMS_PER_CHANNEL)
CONTENT_TYPE_MIX = [
    ("video", 10),
    ("image", 15),
    ("gallery", 10),
    ("rich_text", 5),
    ("poll", 5),
    ("link", 5),
    ("event", 10),
]

LINK_TARGETS = [
    "https://en.wikipedia.org/wiki/Esports",
    "https://en.wikipedia.org/wiki/Science_communication",
    "https://en.wikipedia.org/wiki/Documentary_film",
    "https://en.wikipedia.org/wiki/Travel_literature",
    "https://en.wikipedia.org/wiki/Live_streaming",
]


class Command(BaseCommand):
    help = "Seed four synthetic creator channels with a realistic content library (sandbox DB only)."

    def add_arguments(self, parser):
        parser.add_argument("--items-per-channel", type=int, default=ITEMS_PER_CHANNEL)
        parser.add_argument("--viewers", type=int, default=VIEWER_COUNT)

    def handle(self, *args, **options):
        if os.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings.creator_sandbox":
            raise CommandError(
                "Refusing to run outside the sandbox. Set "
                "DJANGO_SETTINGS_MODULE=config.settings.creator_sandbox and try again."
            )

        items_per_channel = options["items_per_channel"]
        viewer_count = options["viewers"]

        viewers = self._seed_viewers(viewer_count)
        self.stdout.write(self.style.SUCCESS(f"Synthetic viewers ready: {len(viewers)}"))

        for config in CHANNELS:
            with transaction.atomic():
                channel, owner = self._seed_channel(config)
                contents = self._seed_contents(channel, config, items_per_channel)
                self._seed_playlists(channel, config, contents)
                self._seed_engagement(channel, contents, viewers)
                self._sync_channel_counts(channel, contents)
            self.stdout.write(self.style.SUCCESS(
                f"{config['display_name']} ({channel.handle}): "
                f"{len(contents)} content items, "
                f"{channel.subscriber_count} subscribers, "
                f"{channel.content_count} published content_count"
            ))

    # ---------- Users ----------

    def _seed_viewers(self, count: int) -> list[User]:
        viewers = []
        for i in range(1, count + 1):
            phone = f"5550910{i:03d}"
            user, _ = User.objects.get_or_create(
                phone=phone,
                defaults={
                    "display_name": f"Sandbox Viewer {i:02d}",
                    "email": f"viewer{i:03d}@kis.sandbox.local",
                    "country": "US",
                    "is_active": True,
                },
            )
            if not user.has_usable_password():
                user.set_password("SandboxTest@1234")
                user.save(update_fields=["password"])
            viewers.append(user)
        return viewers

    def _seed_owner(self, owner_cfg: dict) -> User:
        user, _ = User.objects.get_or_create(
            phone=owner_cfg["phone"],
            defaults={
                "display_name": owner_cfg["display_name"],
                "email": owner_cfg["email"],
                "country": "US",
                "is_active": True,
            },
        )
        if not user.has_usable_password():
            user.set_password("SandboxTest@1234")
            user.save(update_fields=["password"])
        return user

    # ---------- Channel ----------

    def _seed_channel(self, config: dict) -> tuple[BroadcastChannel, User]:
        owner = self._seed_owner(config["owner"])
        slug = config["slug"]
        channel, created = BroadcastChannel.objects.get_or_create(
            handle=slug,
            defaults={
                "owner_type": BroadcastChannel.OwnerType.USER,
                "owner_id": owner.id,
                "owner_user": owner,
                "display_name": config["display_name"],
                "description": config["description"],
                "category": config["category"],
                "avatar_url": _media_url(slug, "avatar.png"),
                "banner_url": _media_url(slug, "banner.png"),
                "is_public": True,
                "is_verified": False,
            },
        )
        if not created:
            channel.description = config["description"]
            channel.avatar_url = _media_url(slug, "avatar.png")
            channel.banner_url = _media_url(slug, "banner.png")
            channel.save(update_fields=["description", "avatar_url", "banner_url"])
        BroadcastChannelRole.objects.get_or_create(
            channel=channel, user=owner, role=BroadcastChannelRole.Role.OWNER,
        )
        return channel, owner

    # ---------- Content ----------

    def _content_type_sequence(self, total: int) -> list[str]:
        sequence = []
        for content_type, weight in CONTENT_TYPE_MIX:
            sequence.extend([content_type] * weight)
        random.shuffle(sequence)
        return sequence[:total]

    def _seed_contents(self, channel: BroadcastChannel, config: dict, total: int) -> list[ChannelContent]:
        slug = config["slug"]
        series_list = config["series"]
        sequence = self._content_type_sequence(total)
        contents: list[ChannelContent] = []
        now = timezone.now()

        for index, content_type in enumerate(sequence):
            series_name, series_desc = series_list[index % len(series_list)]
            episode_no = (index // len(series_list)) + 1
            title = f"{series_name} — Episode {episode_no}"
            published_at = now - timedelta(days=(total - index) * 2, hours=random.randint(0, 20))
            legacy_entry_id = None  # directly seeded, not synced from a legacy feed entry

            content, created = ChannelContent.objects.update_or_create(
                channel=channel,
                legacy_feed_entry_id=None,
                title=title,
                defaults={
                    "content_type": self._model_content_type(content_type),
                    "description": series_desc,
                    "text_plain": series_desc,
                    "visibility": ChannelContent.Visibility.PUBLIC,
                    "status": ChannelContent.Status.PUBLISHED,
                    "published_at": published_at,
                    "created_at": published_at,
                    # Public listing/discovery (BroadcastChannelContentListCreateView,
                    # filter_channel_content_broadcast_eligible) requires an
                    # explicit human PASS, not just status=published — AI-clean
                    # alone is never sufficient (see moderation_gate.py). This
                    # simulates a library that has already been through that
                    # real review step, same as any established creator's
                    # back catalog would have.
                    "moderation_status": ContentModerationStatus.PASSED,
                    "moderation_passed_at": published_at,
                    "moderation_expires_at": timezone.now() + timedelta(days=365),
                    "thumbnail_url": _media_url(slug, f"thumb_{(index % 6) + 1}.png"),
                    "metadata": {
                        "seeded": True,
                        "seed_source": "seed_creator_channels",
                        "series": series_name,
                        "episode": episode_no,
                    },
                    "tags": [config["category"], slugify(series_name)],
                },
            )
            content.created_at = published_at
            content.save(update_fields=["created_at"])
            self._attach_assets(content, content_type, slug, index)
            contents.append(content)

        return contents

    @staticmethod
    def _model_content_type(content_type: str) -> str:
        mapping = {
            "video": ChannelContentType.VIDEO,
            "image": ChannelContentType.IMAGE,
            "gallery": ChannelContentType.GALLERY,
            "rich_text": ChannelContentType.RICH_TEXT,
            "poll": ChannelContentType.POLL,
            "link": ChannelContentType.LINK,
            "event": ChannelContentType.EVENT,
        }
        return mapping[content_type]

    def _attach_assets(self, content: ChannelContent, content_type: str, slug: str, index: int) -> None:
        ChannelContentAsset.objects.filter(content=content).delete()
        if content_type == "video":
            video_file = f"sample_video_{(index % 2) + 1}.mp4"
            ChannelContentAsset.objects.create(
                content=content,
                asset_type="video",
                url=_media_url(slug, video_file),
                thumbnail_url=_media_url(slug, f"thumb_{(index % 6) + 1}.png"),
                duration_seconds=6,
                caption="",
                sort_order=0,
                processing_status="ready",
                metadata={"seeded": True, "source_file": video_file},
            )
        elif content_type == "image":
            ChannelContentAsset.objects.create(
                content=content,
                asset_type="image",
                url=_media_url(slug, f"thumb_{(index % 6) + 1}.png"),
                sort_order=0,
                processing_status="ready",
                metadata={"seeded": True},
            )
        elif content_type == "gallery":
            for pos in range(3):
                ChannelContentAsset.objects.create(
                    content=content,
                    asset_type="image",
                    url=_media_url(slug, f"thumb_{((index + pos) % 6) + 1}.png"),
                    caption=f"Frame {pos + 1}",
                    sort_order=pos,
                    processing_status="ready",
                    metadata={"seeded": True},
                )
        elif content_type == "poll":
            content.metadata = {
                **content.metadata,
                "poll": {
                    "question": f"What should {content.title.split('—')[0].strip()} cover next?",
                    "options": [
                        {"id": "opt_1", "text": "More behind-the-scenes"},
                        {"id": "opt_2", "text": "A longer deep-dive"},
                        {"id": "opt_3", "text": "A community collab"},
                    ],
                },
            }
            content.save(update_fields=["metadata"])
        elif content_type == "event":
            starts_at = timezone.now() + timedelta(days=random.randint(2, 30))
            content.metadata = {
                **content.metadata,
                "event": {
                    "title": content.title,
                    "startsAt": starts_at.isoformat(),
                    "endsAt": (starts_at + timedelta(hours=2)).isoformat(),
                    "isOnline": True,
                },
            }
            content.save(update_fields=["metadata"])
        elif content_type == "link":
            content.metadata = {
                **content.metadata,
                "link": random.choice(LINK_TARGETS),
            }
            content.save(update_fields=["metadata"])

    # ---------- Playlists ----------

    def _seed_playlists(self, channel: BroadcastChannel, config: dict, contents: list[ChannelContent]) -> None:
        for series_name, series_desc in config["series"]:
            playlist, _ = BroadcastPlaylist.objects.get_or_create(
                channel=channel,
                title=series_name,
                defaults={"description": series_desc, "visibility": BroadcastPlaylist.Visibility.PUBLIC},
            )
            matching = [c for c in contents if c.metadata.get("series") == series_name]
            matching.sort(key=lambda c: c.metadata.get("episode", 0))
            BroadcastPlaylistItem.objects.filter(playlist=playlist).delete()
            for pos, content in enumerate(matching):
                BroadcastPlaylistItem.objects.get_or_create(
                    playlist=playlist, content=content, defaults={"sort_order": pos},
                )

    # ---------- Engagement (real rows, no invented numbers) ----------

    def _seed_engagement(self, channel: BroadcastChannel, contents: list[ChannelContent], viewers: list[User]) -> None:
        # Each viewer independently decides (biased by channel popularity
        # rank, derived from list order — titan-arena/pulse-grid skew more
        # subscribers than lumen-lab/wanderlux) whether to subscribe.
        subscribe_probability = {
            "titan-arena": 0.75,
            "pulse-grid": 0.65,
            "lumen-lab": 0.45,
            "wanderlux": 0.40,
        }.get(channel.handle, 0.5)

        subscribers = [v for v in viewers if random.random() < subscribe_probability]
        for viewer in subscribers:
            BroadcastChannelSubscription.objects.get_or_create(channel=channel, user=viewer)

        if not subscribers:
            subscribers = viewers[:5]

        comment_bodies = [
            "This was such a well put-together episode!",
            "Loved the pacing on this one.",
            "Can you do a follow-up on this topic?",
            "This is why I subscribed to this channel.",
            "Great production quality as always.",
            "More of this series please!",
        ]

        for content in contents:
            watchers = random.sample(subscribers, k=min(len(subscribers), random.randint(3, len(subscribers) or 1)))
            for viewer in watchers:
                ChannelWatchHistory.objects.update_or_create(
                    content=content,
                    user=viewer,
                    defaults={
                        "progress_seconds": random.randint(10, 300),
                        "completed": random.random() < 0.5,
                        "last_viewed_at": timezone.now(),
                    },
                )
            content.stats = {**(content.stats or {}), "views": len(watchers)}
            content.save(update_fields=["stats"])

            reactors = random.sample(watchers, k=min(len(watchers), random.randint(0, len(watchers))))
            for viewer in reactors:
                ChannelContentReaction.objects.get_or_create(
                    content=content, user=viewer, defaults={"reaction": "like"},
                )

            if watchers and random.random() < 0.4:
                commenters = random.sample(watchers, k=min(len(watchers), random.randint(1, 3)))
                for viewer in commenters:
                    ChannelContentComment.objects.get_or_create(
                        content=content,
                        user=viewer,
                        body=random.choice(comment_bodies),
                    )

    def _sync_channel_counts(self, channel: BroadcastChannel, contents: list[ChannelContent]) -> None:
        for content in contents:
            _sync_channel_content_counts(content)
        channel.subscriber_count = BroadcastChannelSubscription.objects.filter(channel=channel).count()
        channel.content_count = ChannelContent.objects.filter(
            channel=channel, is_deleted=False, status=ChannelContent.Status.PUBLISHED,
        ).count()
        channel.save(update_fields=["subscriber_count", "content_count"])
