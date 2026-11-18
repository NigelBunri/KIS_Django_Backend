"""One-time production content seed for the platform GO account.

Creates five GO-owned faith channels plus a batch of GO-authored
channel-less feed posts, covering text / image / video / poll / link /
rich_text / gallery content types. Requested directly by GO (Nigel) to
seed the app's content before real users arrive.

Media sourcing: every image/video URL points at a verified public-domain
work (classic religious paintings centuries old, via Wikimedia Commons;
two historic 1903/1912 silent Gospel films, via archive.org — both long
out of copyright). No binaries are uploaded through this command; URLs
are stored directly on ChannelContentAsset.url/thumbnail_url, which are
plain URLFields — see ChannelContent model docstring. This deliberately
avoids the real-S3-upload path (apps.media.storage_backends.S3MediaStorage)
entirely.

Idempotent by (channel, title) / by legacy_feed_entry_id for the
channel-less posts — safe to re-run; re-running updates rather than
duplicates.

Requires the GO user (apps.partners.seed.get_go_user) to already exist —
refuses to fabricate that identity, since GO is the real account owner.

Usage (on the production box):
    python manage.py seed_go_faith_content
"""
from __future__ import annotations

import random
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.broadcasts.feed_entry_store import sync_channel_content_from_feed_entry
from apps.broadcasts.models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelContent,
    ChannelContentAsset,
    ChannelContentType,
    ContentModerationStatus,
)
from apps.partners.seed import get_go_user

SEED_TAG = "go_faith_content_2026_11"
RANDOM_SEED = 20261117

# ---------------------------------------------------------------------------
# Verified public-domain media
# ---------------------------------------------------------------------------

IMAGES = [
    # URLs are Wikimedia's resized (1280px) thumbnails, not the raw
    # originals - some originals run 20MB+ (e.g. the Last Supper scan),
    # which silently failed/blanked out as a feed-card image on-device.
    # 3 of the 13 (Prodigal Son, Light of the World, Transfiguration)
    # have no smaller thumbnail available (native resolution is already
    # below the 1280px bucket) and keep their original URL.
    ("The Return of the Prodigal Son", "Rembrandt, c. 1669",
     "https://upload.wikimedia.org/wikipedia/commons/2/29/Rembrandt_-_The_Return_of_the_Prodigal_Son_-_WGA19074.jpg"),
    ("Gethsemane", "Carl Bloch, 1873",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/5/54/Carl_Heinrich_Bloch_-_Gethsemane.jpg/1280px-Carl_Heinrich_Bloch_-_Gethsemane.jpg"),
    ("The Sermon on the Mount", "Carl Bloch, 1877",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/9/96/Bloch-SermonOnTheMount.jpg/1280px-Bloch-SermonOnTheMount.jpg"),
    ("The Crucifixion", "Gustave Doré",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/5/51/Crucifixion-dore.jpg/1280px-Crucifixion-dore.jpg"),
    ("The Deluge", "Gustave Doré, Holy Bible illustrations, 1866",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/5/56/Gustave_Dor%C3%A9_-_The_Holy_Bible_-_Plate_I%2C_The_Deluge.jpg/1280px-Gustave_Dor%C3%A9_-_The_Holy_Bible_-_Plate_I%2C_The_Deluge.jpg"),
    ("The Calling of Saint Matthew", "Caravaggio, 1599-1600",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/4/48/The_Calling_of_Saint_Matthew-Caravaggo_%281599-1600%29.jpg/1280px-The_Calling_of_Saint_Matthew-Caravaggo_%281599-1600%29.jpg"),
    ("Supper at Emmaus", "Caravaggio, 1601",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/e/e0/Caravaggio_-_Cena_in_Emmaus.jpg/1280px-Caravaggio_-_Cena_in_Emmaus.jpg"),
    ("The Last Supper", "Leonardo da Vinci, 1495-1498",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/0/08/Leonardo_da_Vinci_%281452-1519%29_-_The_Last_Supper_%281495-1498%29.jpg/1280px-Leonardo_da_Vinci_%281452-1519%29_-_The_Last_Supper_%281495-1498%29.jpg"),
    ("The Light of the World", "William Holman Hunt, 1853-54",
     "https://upload.wikimedia.org/wikipedia/commons/1/10/Hunt_Light_of_the_World.jpg"),
    ("The Creation of Adam", "Michelangelo, Sistine Chapel, c. 1512",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/f/f6/Michelangelo%2C_The_Creation_of_Adam.jpg/1280px-Michelangelo%2C_The_Creation_of_Adam.jpg"),
    ("Daniel in the Lions' Den", "Briton Rivière, 1872",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/9/9c/Daniel_in_the_Lions%27_Den%2C_by_Briton_Rivi%C3%A8re.jpg/1280px-Daniel_in_the_Lions%27_Den%2C_by_Briton_Rivi%C3%A8re.jpg"),
    ("The Annunciation", "Fra Angelico, c. 1430s",
     "https://thumb.wikimedia.org/wikipedia/commons/thumb/b/bc/Fra_Angelico_-_Annunciation.jpg/1280px-Fra_Angelico_-_Annunciation.jpg"),
    ("The Transfiguration", "Raphael, 1516-1520",
     "https://upload.wikimedia.org/wikipedia/commons/5/51/Transfiguration_Raphael.jpg"),
]

VIDEOS = [
    # NOTE: archive.org's "FromTheMangerToTheCross1912" raw-upload MP4 has
    # its moov atom at the very end of a 454MB file (confirmed via byte
    # inspection: ftyp/mdat start at byte ~44, no moov in the first 20MB)
    # - a non-"faststart" container that many mobile progressive-download
    # players (incl. Android ExoPlayer) fail or refuse to stream. Both
    # entries below were verified to have moov within the first 64 bytes
    # (before mdat) and are used instead - no re-encode/upload needed,
    # same two films, just a different (streamable) upload of each.
    ("From the Manger to the Cross; or, Jesus of Nazareth", "Kalem Co., 1912, alternate restoration",
     "https://archive.org/download/silent-from-the-manger-to-the-cross-or-jesus-of-nazareth/From%20the%20Manger%20to%20the%20Cross%3B%20or%2C%20Jesus%20of%20Nazareth.mp4"),
    ("From the Manger to the Cross; or, Jesus of Nazareth", "Kalem Co., 1912, alternate restoration",
     "https://archive.org/download/silent-from-the-manger-to-the-cross-or-jesus-of-nazareth/From%20the%20Manger%20to%20the%20Cross%3B%20or%2C%20Jesus%20of%20Nazareth.mp4"),
    ("La Vie et la Passion de Jesus-Christ", "Pathe Freres, 1903 (public domain silent film)",
     "https://archive.org/download/LaVieEtLaPassionDeJsusChristpassionAndDeathOfChrist1903/LaVieEtLaPassionDeJsusChrist1903_512kb.mp4"),
]

# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

CHANNELS = [
    {"handle": "daily-devotionals", "display_name": "Daily Devotionals",
     "description": "A short word of Scripture and reflection for each day, from GO."},
    {"handle": "morning-evening-prayers", "display_name": "Morning & Evening Prayers",
     "description": "Simple prayers to open and close your day."},
    {"handle": "scripture-and-art", "display_name": "Scripture & Art",
     "description": "Scripture paired with classic public-domain religious art."},
    {"handle": "hymns-and-worship", "display_name": "Hymns & Worship",
     "description": "Worship, Gospel film, and devotional video content."},
    {"handle": "faith-encouragement", "display_name": "Faith Encouragement",
     "description": "Encouragement and hope for whatever today holds."},
]

# ---------------------------------------------------------------------------
# 10 standalone TEXT posts
# ---------------------------------------------------------------------------

TEXT_POSTS = [
    ("Morning Light", "\"The steadfast love of the LORD never ceases; his mercies never come to an end; "
     "they are new every morning.\" (Lamentations 3:22-23). Whatever burdened you yesterday does not "
     "have to follow you into today."),
    ("Strength for the Weary", "\"He giveth power to the faint; and to them that have no might he "
     "increaseth strength.\" (Isaiah 40:29). You don't need to manufacture strength today. Ask, and "
     "let it be given."),
    ("Unseen but Sure", "\"Now faith is the substance of things hoped for, the evidence of things not "
     "seen.\" (Hebrews 11:1). Faith moves while the outcome is still unseen — that's exactly what "
     "makes it faith."),
    ("New Creation", "\"Therefore if any man be in Christ, he is a new creature: old things are passed "
     "away; behold, all things are become new.\" (2 Corinthians 5:17). Your past does not get a vote "
     "in who you are becoming."),
    ("The Lord Fights for You", "\"The LORD shall fight for you, and ye shall hold your peace.\" "
     "(Exodus 14:14). Some battles are not yours to win by effort. They're yours to trust by faith."),
    ("Renewed Strength", "\"They that wait upon the LORD shall renew their strength; they shall mount "
     "up with wings as eagles.\" (Isaiah 40:31). Waiting isn't wasted time — it's where strength gets "
     "rebuilt."),
    ("You Are Not Disqualified", "Every person God used in Scripture had a reason to disqualify "
     "themselves — and He used them anyway. Your past is not a disqualification. It's the very place "
     "grace loves to work."),
    ("Small Beginnings", "\"Who hath despised the day of small things?\" (Zechariah 4:10). Don't be "
     "discouraged by how small your start looks. God has never needed a big beginning to do a big "
     "work."),
    ("He Finishes What He Starts", "\"Being confident of this very thing, that he which hath begun a "
     "good work in you will perform it until the day of Jesus Christ.\" (Philippians 1:6)."),
    ("A Living Hope", "\"Blessed be the God and Father of our Lord Jesus Christ, which... hath "
     "begotten us again unto a lively hope.\" (1 Peter 1:3). Your hope has a pulse — it isn't wishful "
     "thinking, it's resurrection power."),
]

# ---------------------------------------------------------------------------
# 50 IMAGE post captions (cycled across the 13 verified paintings)
# ---------------------------------------------------------------------------

IMAGE_CAPTIONS = [
    "\"I will arise and go to my father.\" (Luke 15:18) — grace is always closer than guilt.",
    "\"Let this cup pass from me: nevertheless not as I will, but as thou wilt.\" (Matthew 26:39)",
    "\"Blessed are the poor in spirit: for theirs is the kingdom of heaven.\" (Matthew 5:3)",
    "\"It is finished.\" (John 19:30) — the debt was paid in full.",
    "\"The waters prevailed, and were increased greatly upon the earth.\" (Genesis 7:18) — and still, God remembered Noah.",
    "\"Follow me.\" (Matthew 9:9) — He calls ordinary people to extraordinary purpose.",
    "\"Did not our heart burn within us... while he opened to us the scriptures?\" (Luke 24:32)",
    "\"This is my body, which is given for you.\" (Luke 22:19)",
    "\"I am the light of the world.\" (John 8:12)",
    "\"So God created man in his own image.\" (Genesis 1:27)",
    "\"My God hath sent his angel, and hath shut the lions' mouths.\" (Daniel 6:22)",
    "\"Behold, thou shalt conceive... and shalt call his name JESUS.\" (Luke 1:31)",
    "\"This is my beloved Son: hear him.\" (Mark 9:7)",
    "\"When he was yet a great way off, his father saw him, and had compassion.\" (Luke 15:20)",
    "\"Could ye not watch with me one hour?\" (Matthew 26:40) — He understands even your weariness.",
    "\"Blessed are they that mourn: for they shall be comforted.\" (Matthew 5:4)",
    "\"Father, forgive them; for they know not what they do.\" (Luke 23:34)",
    "\"The ark rested... upon the mountains of Ararat.\" (Genesis 8:4) — judgment always makes room for mercy.",
    "\"As Jesus passed by, he saw a man... he said unto him, Follow me.\" (Matthew 9:9)",
    "\"Then they knew him in breaking of bread.\" (Luke 24:35)",
    "\"Take, eat: this is my body.\" (Matthew 26:26)",
    "\"In him was life; and the life was the light of men.\" (John 1:4)",
    "\"The Spirit of God moved upon the face of the waters.\" (Genesis 1:2)",
    "\"Daniel... hath trusted in his God.\" (Daniel 6:23)",
    "\"Fear not, Mary: for thou hast found favour with God.\" (Luke 1:30)",
    "\"And his face did shine as the sun.\" (Matthew 17:2)",
    "\"There is joy in the presence of the angels of God over one sinner that repenteth.\" (Luke 15:10)",
    "\"Sleep on now, and take your rest.\" (Matthew 26:45) — grace even in the disciples' failure.",
    "\"Blessed are the meek: for they shall inherit the earth.\" (Matthew 5:5)",
    "\"Behold the Lamb of God, which taketh away the sin of the world.\" (John 1:29)",
    "\"The dove came in to him in the evening... and, lo, in her mouth was an olive leaf.\" (Genesis 8:11)",
    "\"Jesus... saw Levi... and said unto him, Follow me.\" (Mark 2:14)",
    "\"And he was known of them in breaking of bread.\" (Luke 24:35)",
    "\"This do in remembrance of me.\" (Luke 22:19)",
    "\"Ye are the light of the world.\" (Matthew 5:14)",
    "\"Let there be light: and there was light.\" (Genesis 1:3)",
    "\"The king... commanded that they should take Daniel up out of the den.\" (Daniel 6:23)",
    "\"My soul doth magnify the Lord.\" (Luke 1:46)",
    "\"Lord, it is good for us to be here.\" (Matthew 17:4)",
    "\"Likewise joy shall be in heaven over one sinner that repenteth.\" (Luke 15:7)",
    "\"Watch and pray, that ye enter not into temptation.\" (Matthew 26:41)",
    "\"Blessed are they which do hunger and thirst after righteousness.\" (Matthew 5:6)",
    "\"We beheld his glory, the glory as of the only begotten of the Father.\" (John 1:14)",
    "\"God saw that it was good.\" (Genesis 1:25)",
    "\"The king was exceeding glad for him.\" (Daniel 6:23)",
    "\"The Lord is with thee.\" (Luke 1:28)",
    "\"Behold, there appeared Moses and Elias talking with him.\" (Matthew 17:3)",
    "\"Bring hither the fatted calf, and kill it; and let us eat, and be merry.\" (Luke 15:23)",
    "\"Could ye not watch one hour?\" — grace meets us even at our most unwatchful.",
    "\"Blessed are the pure in heart: for they shall see God.\" (Matthew 5:8)",
]

# ---------------------------------------------------------------------------
# 20 VIDEO post captions (cycled across the 3 verified films, by Gospel scene)
# ---------------------------------------------------------------------------

VIDEO_SCENES = [
    "The Nativity", "The Baptism of Jesus", "Calling the First Disciples", "The Wedding at Cana",
    "The Sermon on the Mount", "Healing the Sick", "Feeding the Multitude", "Walking on Water",
    "The Transfiguration", "Raising of Lazarus", "The Triumphal Entry", "The Last Supper",
    "Gethsemane", "The Trial Before Pilate", "The Crucifixion", "The Resurrection",
    "The Road to Emmaus", "Doubting Thomas", "The Ascension", "The Great Commission",
]

# ---------------------------------------------------------------------------
# 20 FREESTYLE posts: 5 poll, 5 link, 5 rich_text, 5 gallery
# ---------------------------------------------------------------------------

POLLS = [
    ("Which Psalm brings you the most comfort?",
     ["Psalm 23", "Psalm 91", "Psalm 46", "Psalm 121"]),
    ("What's hardest to bring to God in prayer?",
     ["Worry", "Doubt", "Anger", "Asking for help"]),
    ("Which spiritual discipline do you want to grow in most?",
     ["Prayer", "Scripture reading", "Fasting", "Gratitude"]),
    ("Which parable speaks to you most right now?",
     ["The Prodigal Son", "The Good Samaritan", "The Lost Sheep", "The Sower"]),
    ("What time of day do you pray best?",
     ["Early morning", "Midday", "Evening", "Whenever I can"]),
]

LINKS = [
    ("The Sermon on the Mount", "https://en.wikipedia.org/wiki/Sermon_on_the_Mount",
     "A look at the heart of Jesus's teaching — well worth a slow read."),
    ("The Lord's Prayer", "https://en.wikipedia.org/wiki/Lord%27s_Prayer",
     "The pattern Jesus himself gave us for prayer."),
    ("The Ten Commandments", "https://en.wikipedia.org/wiki/Ten_Commandments",
     "Still the moral foundation so much of the world quietly stands on."),
    ("The Beatitudes", "https://en.wikipedia.org/wiki/Beatitudes",
     "Eight blessings that turn the world's definition of 'blessed' upside down."),
    ("The Parables of Jesus", "https://en.wikipedia.org/wiki/Parables_of_Jesus",
     "Simple stories carrying the deepest truths he ever told."),
]

RICH_TEXTS = [
    ("What Grace Actually Costs",
     "Grace is free to receive, but it was never free to give. Someone paid for every ounce of "
     "mercy you've ever been shown. That's why grace can never be casual — it's the most expensive "
     "gift you own."),
    ("The Patience of the Father",
     "The father in the parable didn't go looking for the son who left. He waited, watching the "
     "road, ready to run the moment he saw him coming. Some seasons of faith are not about chasing "
     "God down — they're about trusting He's already watching the road for you."),
    ("Faith in the Ordinary",
     "Most of the Bible's great men and women were doing ordinary things when God interrupted them — "
     "tending sheep, fishing, sweeping a floor. Don't wait for a dramatic moment to take your faith "
     "seriously. God shows up in the ordinary far more often than the extraordinary."),
    ("Why Lament Is Still Worship",
     "Nearly a third of the Psalms are complaints to God. Scripture doesn't ask you to fake peace — "
     "it invites you to bring the real ache straight to the throne. Honest lament, aimed at God "
     "instead of away from Him, is still worship."),
    ("The Long Obedience",
     "Nobody in Scripture was made faithful overnight. Abraham waited decades. Joseph waited years in "
     "a prison he didn't deserve. Faithfulness is rarely a single dramatic choice — it's a long, "
     "quiet obedience across many ordinary days."),
]

GALLERIES = [
    # Indices into IMAGES above: 0 Prodigal Son, 1 Gethsemane, 2 Sermon on
    # the Mount, 3 Crucifixion, 4 Deluge, 5 Calling of Matthew, 6 Emmaus,
    # 7 Last Supper, 8 Light of the World, 9 Creation of Adam,
    # 10 Daniel, 11 Annunciation, 12 Transfiguration.
    ("Holy Week in Art", "From the upper room to the empty tomb.", [7, 1, 3]),
    ("The Beginning", "Creation, and the promise that survives judgment.", [9, 4]),
    ("A Father's Love", "Mercy that goes looking for the one who left.", [0, 8]),
    ("Who Is Jesus?", "Three classic paintings answering that question.", [12, 8, 7]),
    ("The Gospel in Four Scenes", "Calling, teaching, dying, rising.", [5, 2, 3, 6]),
]


def _build_items() -> list[dict]:
    """Build the full 100-item content plan as plain dicts, each tagged
    with a `content_type` and enough info to materialize a ChannelContent
    (+ assets) row. Kept separate from DB writes so the mix/shuffle is
    easy to reason about and re-count."""
    items: list[dict] = []

    for title, body in TEXT_POSTS:
        items.append({
            "content_type": ChannelContentType.TEXT,
            "title": title,
            "text_plain": body,
            "description": "",
            "assets": [],
        })

    for i, caption in enumerate(IMAGE_CAPTIONS):
        art_title, art_credit, url = IMAGES[i % len(IMAGES)]
        items.append({
            "content_type": ChannelContentType.IMAGE,
            "title": art_title,
            "text_plain": caption,
            "description": f"{art_title} — {art_credit} (public domain).",
            "assets": [{"asset_type": "image", "url": url, "caption": caption}],
        })

    for i, scene in enumerate(VIDEO_SCENES):
        film_title, film_credit, url = VIDEOS[i % len(VIDEOS)]
        items.append({
            "content_type": ChannelContentType.VIDEO,
            "title": f"{scene} — {film_title}",
            "text_plain": f"{scene}, from \"{film_title}\" ({film_credit}).",
            "description": f"\"{film_title}\" — {film_credit}.",
            "assets": [{"asset_type": "video", "url": url, "caption": scene}],
        })

    for question, options in POLLS:
        items.append({
            "content_type": ChannelContentType.POLL,
            "title": question,
            "text_plain": question,
            "description": "",
            "assets": [],
            "poll": {
                "question": question,
                "options": [{"id": f"opt_{i+1}", "text": opt} for i, opt in enumerate(options)],
            },
        })

    for link_title, url, note in LINKS:
        items.append({
            "content_type": ChannelContentType.LINK,
            "title": link_title,
            "text_plain": note,
            "description": note,
            "assets": [],
            "link": url,
        })

    for title, body in RICH_TEXTS:
        items.append({
            "content_type": ChannelContentType.RICH_TEXT,
            "title": title,
            "text_plain": body,
            "description": "",
            "assets": [],
            "text_doc": {"blocks": [{"type": "paragraph", "text": body}]},
        })

    for gallery_title, note, image_indices in GALLERIES:
        assets = []
        for pos, idx in enumerate(image_indices):
            art_title, art_credit, url = IMAGES[idx]
            assets.append({
                "asset_type": "image",
                "url": url,
                "caption": f"{art_title} — {art_credit}",
                "sort_order": pos,
            })
        items.append({
            "content_type": ChannelContentType.GALLERY,
            "title": gallery_title,
            "text_plain": note,
            "description": note,
            "assets": assets,
        })

    return items


class Command(BaseCommand):
    help = "Seed GO-owned faith channels and channel-less feed posts (production content seed)."

    def add_arguments(self, parser):
        parser.add_argument("--channelless-count", type=int, default=15,
                             help="How many of the 100 items go to GO's personal/channel-less feed.")
        parser.add_argument("--dry-run", action="store_true",
                             help="Build and print the plan without writing to the database.")

    def handle(self, *args, **options):
        items = _build_items()
        if len(items) != 100:
            raise CommandError(f"Expected exactly 100 content items, built {len(items)}.")

        rng = random.Random(RANDOM_SEED)
        rng.shuffle(items)

        channelless_count = options["channelless_count"]
        channelless_items = items[:channelless_count]
        channel_items = items[channelless_count:]

        self.stdout.write(self.style.SUCCESS(
            f"Plan: {len(channelless_items)} channel-less feed posts, "
            f"{len(channel_items)} distributed across {len(CHANNELS)} channels."
        ))

        if options["dry_run"]:
            for item in items:
                self.stdout.write(f"  [{item['content_type']}] {item['title']}")
            return

        go_user = get_go_user()
        if not go_user:
            raise CommandError(
                "GO user not found (apps.partners.seed.get_go_user returned None). "
                "This command refuses to fabricate the GO identity — GO must have "
                "logged in at least once already."
            )

        # Channel-less posts MUST be seeded before any named channel is
        # created: _get_or_create_personal_channel (feed_entry_store.py)
        # resolves "no channel picked" by returning GO's *earliest*
        # existing owned channel, not a dedicated one. If the 5 named
        # channels already existed, every "channel-less" post would
        # silently land in whichever named channel happens to be oldest.
        self._seed_channelless_items(go_user, channelless_items)

        with transaction.atomic():
            channels = self._seed_channels(go_user)
            self._seed_channel_items(channels, channel_items)

        self.stdout.write(self.style.SUCCESS("Done."))

    # ---------- Channels ----------

    def _seed_channels(self, go_user) -> list[BroadcastChannel]:
        channels = []
        for cfg in CHANNELS:
            channel, created = BroadcastChannel.objects.get_or_create(
                handle=cfg["handle"],
                defaults={
                    "owner_type": BroadcastChannel.OwnerType.USER,
                    "owner_id": go_user.id,
                    "owner_user": go_user,
                    "display_name": cfg["display_name"],
                    "description": cfg["description"],
                    "category": "faith",
                    "is_public": True,
                    "is_verified": True,
                },
            )
            if not created and channel.owner_user_id != go_user.id:
                channel.owner_type = BroadcastChannel.OwnerType.USER
                channel.owner_id = go_user.id
                channel.owner_user = go_user
                channel.save(update_fields=["owner_type", "owner_id", "owner_user"])
            BroadcastChannelRole.objects.get_or_create(
                channel=channel, user=go_user, role=BroadcastChannelRole.Role.OWNER,
            )
            channels.append(channel)
            self.stdout.write(f"Channel ready: @{channel.handle} ({'created' if created else 'existing'})")
        return channels

    # ---------- Channel-scoped content ----------

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

            # Keyed on text_plain, not title: IMAGE_CAPTIONS cycles only 13
            # distinct artwork titles across 50 posts, so (channel, title)
            # collided and silently overwrote distinct posts via
            # update_or_create. Every item's text_plain is unique across
            # the full 100-item set, so this key can't collide.
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
                    "tags": ["faith", item["content_type"]],
                },
            )
            ChannelContentAsset.objects.filter(content=content).delete()
            for pos, asset in enumerate(item["assets"]):
                ChannelContentAsset.objects.create(
                    content=content,
                    asset_type=asset["asset_type"],
                    url=asset["url"],
                    caption=asset.get("caption", ""),
                    sort_order=asset.get("sort_order", pos),
                    processing_status="ready",
                    metadata={"seeded": True},
                )
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(items)} channel-scoped posts."))

    # ---------- Channel-less feed content ----------

    def _seed_channelless_items(self, go_user, items: list[dict]) -> None:
        now = timezone.now()
        for item in items:
            entry = {
                "id": str(uuid.uuid4()),
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
                    }
                    for asset in item["assets"]
                ],
            }
            if item.get("poll"):
                entry["poll"] = item["poll"]
            if item.get("link"):
                entry["link"] = item["link"]

            content = sync_channel_content_from_feed_entry(go_user, None, entry)
            if content is None:
                self.stdout.write(self.style.WARNING(f"Skipped channel-less item: {item['title']}"))
                continue
            # sync_channel_content_from_feed_entry doesn't set moderation
            # fields (they default to PENDING_REVIEW) — bring these in
            # line with the channel-scoped posts above so they're equally
            # eligible for public discovery, not just visible on GO's own
            # personal channel page.
            content.moderation_status = ContentModerationStatus.PASSED
            content.moderation_passed_at = now
            content.moderation_expires_at = now + timedelta(days=365)
            metadata = dict(content.metadata or {})
            metadata["seeded"] = True
            metadata["seed_source"] = SEED_TAG
            content.metadata = metadata
            content.save(update_fields=["moderation_status", "moderation_passed_at", "moderation_expires_at", "metadata"])
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(items)} channel-less feed posts."))
