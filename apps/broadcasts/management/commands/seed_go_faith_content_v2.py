"""Second production content batch for the platform GO account - same
pipeline as seed_go_faith_content.py (see that file's docstring for the
full architecture notes), different content: contemporary/everyday-life
reflections instead of historical Bible-scene framing, written in a
plain, conversational voice rather than devotional-formula style.

Lands in the SAME 5 channels + GO's same personal channel as the first
batch (looked up by handle, not recreated). Idempotent, keyed on
text_plain, tagged with a distinct SEED_TAG so this batch can be told
apart from the first if needed.

Usage (on the production box):
    python manage.py seed_go_faith_content_v2
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

SEED_TAG = "go_faith_content_2026_11_v2"
RANDOM_SEED = 20261118

# ---------------------------------------------------------------------------
# Same verified media as batch 1 (images: Wikimedia thumbnails; videos: our
# own S3 copies, not archive.org - see memory on why). Re-verified reachable
# before this file was written.
# ---------------------------------------------------------------------------

IMAGES = [
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

BUCKET = "kis-production-media-prod-387362988707-eu-west-2-an"
REGION = "eu-west-2"
VIDEOS = [
    ("From the Manger to the Cross", "Kalem Co., 1912 (public domain silent film)",
     f"https://{BUCKET}.s3.{REGION}.amazonaws.com/broadcast_videos/from_the_manger_to_the_cross_1912.mp4"),
    ("La Vie et la Passion de Jesus-Christ", "Pathe Freres, 1903 (public domain silent film)",
     f"https://{BUCKET}.s3.{REGION}.amazonaws.com/broadcast_videos/la_vie_et_la_passion_de_jesus_christ_1903.mp4"),
]

# ---------------------------------------------------------------------------
# Channels - SAME handles as batch 1, so this lands in the existing channels.
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
# 10 standalone TEXT posts - contemporary, plain-spoken, no verse-first
# formula on every single one.
# ---------------------------------------------------------------------------

TEXT_POSTS = [
    ("The scroll never ends and that's kind of the problem",
     "I caught myself last night scrolling at 1am looking for... nothing, "
     "really. Just more. Jesus talked about a peace the world can't give, "
     "and I think part of what He meant is that the world's version of "
     "peace is just distraction with better lighting. Put the phone down "
     "tonight. See what's left when the noise stops."),
    ("Nobody tells you adulthood is mostly just tired",
     "Some weeks faith doesn't feel like mountaintops, it feels like "
     "showing up exhausted and asking God to carry the part you can't. "
     "\"Come unto me, all ye that labour and are heavy laden\" (Matthew "
     "11:28) wasn't written for people who had it together. It was "
     "written for people like us."),
    ("You are allowed to rest without earning it first",
     "We live like rest is a reward for finishing everything, and it "
     "never comes because the list never ends. God rested on the seventh "
     "day not because He was tired, but because rest was built into the "
     "rhythm from the start. You don't have to earn Sabbath. You just "
     "have to take it."),
    ("On not having it figured out",
     "If you feel behind in life, like everyone else got a manual you "
     "never received — Abraham left home not knowing where he was going "
     "(Hebrews 11:8). Faith has never required a finished plan. Just a "
     "willingness to take the next step."),
    ("The group chat went quiet and so did I",
     "Loneliness in a hyper-connected world is a strange, specific kind "
     "of ache. A hundred contacts and still nobody to call. God saw "
     "Hagar alone in the wilderness and gave her a name for Him: \"Thou "
     "God seest me\" (Genesis 16:13). You are seen even when your phone "
     "is quiet."),
    ("Comparison is just grief wearing a disguise",
     "Every time I compare my life to someone's highlight reel, what "
     "I'm actually grieving is the life I thought I'd have by now. Paul "
     "said he'd \"learned, in whatsoever state I am, therewith to be "
     "content\" (Philippians 4:11) — learned, past tense, like it took "
     "practice. It's supposed to take practice."),
    ("Forgiving someone who isn't sorry",
     "Nobody talks about how hard it is to forgive someone who never "
     "apologized and never will. Jesus forgave the people nailing Him to "
     "the cross before they asked (Luke 23:34). Forgiveness was never "
     "about what they deserved. It's about what it frees in you."),
    ("Doubt isn't the opposite of faith, apathy is",
     "If you've ever felt guilty for having questions — Thomas asked for "
     "proof and got it, gently, without being shamed first (John "
     "20:27-28). A faith that can survive honest questions is sturdier "
     "than one that was never allowed to ask any."),
    ("Burnout and the myth of the hustle",
     "Somewhere along the way, busy became a stand-in for faithful. "
     "Elijah ran himself into the ground serving God and then just... "
     "collapsed under a tree (1 Kings 19:4-5). God's first move wasn't a "
     "pep talk. It was food, sleep, and more food. Sometimes the "
     "spiritual thing to do is take a nap."),
    ("Small, ordinary, faithful — in that order",
     "Nobody grows up dreaming of an ordinary life, but most of the "
     "Bible's heroes spent decades in one before anything happened. "
     "David was still tending sheep when he was already anointed king "
     "(1 Samuel 16:11-13). Whatever ordinary thing is in front of you "
     "today might be exactly where you're being prepared."),
]

# ---------------------------------------------------------------------------
# 50 IMAGE post captions - contemporary framing, varied openers, cycled
# across the 13 images.
# ---------------------------------------------------------------------------

IMAGE_CAPTIONS = [
    "Grace doesn't wait for you to clean up first. It meets you on the road home.",
    "\"Could ye not watch with me one hour?\" He asked for presence, not perfection.",
    "Most burnout isn't from doing too much. It's from doing it alone.",
    "The cross makes no sense as a transaction and complete sense as love.",
    "Every flood story is also a staying-power story. He remembers.",
    "Being called doesn't require being ready. It just requires standing up.",
    "Recognition often comes after the walk, not before it — stay on the road.",
    "This meal was never just about bread. Presence changes a table.",
    "Light doesn't argue with darkness. It just stays lit.",
    "You were made on purpose, by hands that don't make mistakes.",
    "Faith isn't the absence of danger. It's trust in the middle of it.",
    "Some yeses change everything and feel like nothing at the time.",
    "A mountaintop moment doesn't exempt you from the valley right after.",
    "Mercy running toward you looks a lot like this — before you even explain.",
    "He didn't ask for an explanation before He gave comfort.",
    "The world keeps score. Grace doesn't.",
    "Sometimes the hardest prayer is just \"not my will.\"",
    "Judgment and mercy showed up in the same story. They always do.",
    "He calls people in the middle of their actual job, not after they fix their life.",
    "Recognition is a kind of grace too — being truly seen.",
    "Communion was never about the plate. It was about who's at the table.",
    "A light that only shines when it's convenient isn't much of a light.",
    "You didn't make yourself. You don't have to hold yourself together alone either.",
    "Lions don't need to be tame for God to be faithful in the den.",
    "An ordinary yes, given in private, can carry a whole story forward.",
    "Glory doesn't cancel the valley. It just proves who was with you in it.",
    "Running toward someone before they finish their apology — that's the whole gospel in a gesture.",
    "Rest isn't weakness. Even this moment needed stillness first.",
    "Old judgment, same mercy underneath it.",
    "Following doesn't always look dramatic. Sometimes it looks like standing up and leaving the desk.",
    "Recognizing Him often happens in the most ordinary moment — breaking bread, not a vision.",
    "\"This is my body, given for you\" is still the most personal sentence ever spoken.",
    "Hope doesn't need the whole room lit. One light is enough to start.",
    "You are not an accident. You were formed, not assembled.",
    "Trust in the den looks like sleep, not strategy.",
    "Favor doesn't always come with fireworks. Sometimes it's a quiet word in an ordinary room.",
    "Transformation doesn't erase the story. It reveals what was always underneath.",
    "Compassion got there before the explanation did.",
    "Even surrounded by exhausted friends, He prayed alone and meant it.",
    "The same mercy that forgave centuries ago still covers today.",
    "Being chosen doesn't mean being impressive first. It means being available.",
    "Sometimes faith is recognized in hindsight — \"did not our heart burn.\"",
    "A shared meal is still one of the most sacred things humans do.",
    "Darkness has never once successfully argued a light out of shining.",
    "You are known before you're finished. That's the whole point of grace.",
    "Faith in a den of very real danger still counts as faith.",
    "A small private yes can carry the weight of a much bigger story.",
    "Glory on a mountain doesn't excuse you from Monday. It prepares you for it.",
    "He didn't wait at the door with His arms crossed. He ran.",
    "Rest first, THEN the mountain. That's a surprisingly biblical order.",
]

# ---------------------------------------------------------------------------
# 20 VIDEO post captions - framed as contemporary reflection on an old
# scene, cycled across the 2 S3-hosted films.
# ---------------------------------------------------------------------------

VIDEO_SCENES = [
    ("The Nativity", "Everyone was looking for a king in a palace. He showed up in the one place nobody checked."),
    ("The Baptism of Jesus", "He didn't need it, and did it anyway, just to stand where we'd stand."),
    ("Calling the First Disciples", "No resumes, no interviews. Just \"follow me\" to people mid-shift."),
    ("The Wedding at Cana", "His first miracle wasn't a healing. It was just making sure a party didn't run out of joy."),
    ("The Sermon on the Mount", "Still the most quoted, least practiced speech in history. Worth rewatching."),
    ("Healing the Sick", "He stopped for the one person in a crowd that was already moving on."),
    ("Feeding the Multitude", "Not enough became more than enough the moment it was actually given."),
    ("Walking on Water", "The storm didn't stop. He just walked straight through the middle of it."),
    ("The Transfiguration", "For one moment the curtain pulled back. Then it was back to the valley."),
    ("Raising of Lazarus", "He wept first. Then He acted. Grief and power were never opposites for Him."),
    ("The Triumphal Entry", "Cheering crowds on Sunday, an empty street by Friday. Public opinion was never the point."),
    ("The Last Supper", "He washed feet and broke bread in the same night He knew He'd be betrayed."),
    ("Gethsemane", "\"Not my will\" is maybe the hardest prayer ever prayed, and He prayed it first."),
    ("The Trial Before Pilate", "Silence, under pressure, when a single word could've changed everything."),
    ("The Crucifixion", "Love, when it's real, is willing to look like loss before it looks like victory."),
    ("The Resurrection", "The worst Friday in history couldn't out-write the best Sunday."),
    ("The Road to Emmaus", "They walked miles with Him and didn't know it until the bread broke."),
    ("Doubting Thomas", "He didn't get mad at the doubt. He just showed up with the evidence."),
    ("The Ascension", "He didn't disappear. He just finished the part that needed a body."),
    ("The Great Commission", "The last instruction wasn't \"stay safe.\" It was \"go.\""),
]

# ---------------------------------------------------------------------------
# 20 FREESTYLE posts: 5 poll, 5 link, 5 rich_text, 5 gallery
# ---------------------------------------------------------------------------

POLLS = [
    ("Honestly, what's draining your faith most right now?",
     ["Comparison / social media", "Busyness", "Doubt", "Loneliness"]),
    ("What actually helps you reconnect with God when you're flat?",
     ["Worship music", "A long walk", "Journaling", "Calling someone"]),
    ("Which is harder for you right now?",
     ["Forgiving someone", "Forgiving yourself", "Trusting God's timing", "Resting without guilt"]),
    ("If you could ask God one question today, what's it closer to?",
     ["Why?", "How long?", "What now?", "Are you there?"]),
    ("What's your actual Sabbath look like these days?",
     ["A real day off", "A few stolen hours", "Barely anything", "What's a Sabbath"]),
]

LINKS = [
    ("The Sabbath", "https://en.wikipedia.org/wiki/Sabbath_in_Christianity",
     "Rest as a command, not a suggestion — worth revisiting if your weeks feel endless."),
    ("Lectio Divina", "https://en.wikipedia.org/wiki/Lectio_Divina",
     "An old, slow way of reading Scripture built for a world that reads everything fast."),
    ("The Examen", "https://en.wikipedia.org/wiki/Spiritual_Exercises_of_Ignatius_of_Loyola",
     "A five-minute way to end the day noticing where God actually showed up in it."),
    ("Christian Contentment", "https://en.wikipedia.org/wiki/Contentment",
     "Paul said he learned it. That word \"learned\" still does a lot of work."),
    ("The Prodigal Son", "https://en.wikipedia.org/wiki/Parable_of_the_Prodigal_Son",
     "The parable that ruins the idea that grace has to be earned back."),
]

RICH_TEXTS = [
    ("Why \"I'm fine\" Is Usually a Lie We Tell At Church",
     "We've gotten so good at the performance of okay that we forget God never asked for "
     "the performance. David wrote entire psalms that are basically just complaints aimed "
     "at heaven. The Bible has room for your actual week, not just your highlight reel."),
    ("Scrolling Is the New Fasting Opportunity",
     "Fasting used to just mean food. For a lot of us now, the thing with the strongest "
     "grip isn't food, it's the phone. Try putting it down for a day and see what rises "
     "to the surface when the noise stops. It's usually worth looking at."),
    ("Nobody Warns You That Faith Gets Quieter",
     "The loud, emotional highs don't last forever, and that's not a sign something's "
     "wrong. A lot of mature faith is just quiet, stubborn faithfulness on a Tuesday when "
     "nothing dramatic is happening. That's not less than the mountaintop. It might be more."),
    ("The Lie That Busy Means Important",
     "Jesus had three years, twelve people, and said no to almost everyone who wanted "
     "more of His time. If the most important person who ever lived could leave things "
     "undone, we're probably allowed to as well."),
    ("What Deconstruction Gets Right (and What It Misses)",
     "Asking hard questions about what you were taught isn't the enemy of faith — a lot "
     "of the Psalms are hard questions aimed straight at God. The goal was never to stop "
     "asking. It's to keep asking Him instead of just walking away."),
]

GALLERIES = [
    # Indices into IMAGES above: 0 Prodigal Son, 1 Gethsemane, 2 Sermon on
    # the Mount, 3 Crucifixion, 4 Deluge, 5 Calling of Matthew, 6 Emmaus,
    # 7 Last Supper, 8 Light of the World, 9 Creation of Adam,
    # 10 Daniel, 11 Annunciation, 12 Transfiguration.
    ("What a Hard Week Looks Like in Scripture", "Pressure, prayer, and a mercy that shows up anyway.", [1, 3, 0]),
    ("Starting Over, Biblically Speaking", "Creation and a flood that still ended in a promise.", [9, 4]),
    ("Being Found Before You're Fixed", "Two paintings about grace that doesn't wait for you to qualify.", [0, 8]),
    ("Ordinary Rooms, Extraordinary Moments", "A calling, a meal, and a mountaintop — none of them staged.", [5, 6, 12]),
    ("The Whole Story in Four Frames", "Called, taught, lost, and found again.", [5, 2, 3, 6]),
]


def _build_items() -> list[dict]:
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

    for i, (scene, caption) in enumerate(VIDEO_SCENES):
        film_title, film_credit, url = VIDEOS[i % len(VIDEOS)]
        items.append({
            "content_type": ChannelContentType.VIDEO,
            "title": f"{scene} — {film_title}",
            "text_plain": caption,
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
    help = "Seed a second batch of GO-owned faith content (contemporary voice, same channels)."

    def add_arguments(self, parser):
        parser.add_argument("--channelless-count", type=int, default=15)
        parser.add_argument("--dry-run", action="store_true")

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
            raise CommandError("GO user not found - this command refuses to fabricate the identity.")

        # Channel-less first - see seed_go_faith_content.py's matching
        # comment for why order matters here.
        self._seed_channelless_items(go_user, channelless_items)

        with transaction.atomic():
            channels = self._seed_channels(go_user)
            self._seed_channel_items(channels, channel_items)

        self.stdout.write(self.style.SUCCESS("Done."))

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
            content.moderation_status = ContentModerationStatus.PASSED
            content.moderation_passed_at = now
            content.moderation_expires_at = now + timedelta(days=365)
            metadata = dict(content.metadata or {})
            metadata["seeded"] = True
            metadata["seed_source"] = SEED_TAG
            content.metadata = metadata
            content.save(update_fields=["moderation_status", "moderation_passed_at", "moderation_expires_at", "metadata"])
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(items)} channel-less feed posts."))
