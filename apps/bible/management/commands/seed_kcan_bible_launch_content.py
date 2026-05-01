from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.bible.models import (
    BibleCourse,
    BibleCourseModule,
    BibleDailyPassage,
    BibleLesson,
    BibleMeditationPost,
    BiblePrayerDay,
    BiblePrayerMonth,
    BiblePublishStatus,
    BibleTranslation,
)
from apps.partners.seed import ensure_kis_partner, KCAN_PARTNER_SLUG
from apps.partners.models import Partner


class Command(BaseCommand):
    help = "Seed starter KCAN manual Bible content for staging/manual QA."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14, help="Number of daily passages to seed.")
        parser.add_argument("--publish", action="store_true", help="Publish seeded content immediately.")

    def handle(self, *args, **options):
        ensure_kis_partner()
        partner = Partner.objects.get(slug=KCAN_PARTNER_SLUG)
        today = timezone.localdate()
        now = timezone.now()
        status = BiblePublishStatus.PUBLISHED if options["publish"] else BiblePublishStatus.DRAFT
        published_at = now if status == BiblePublishStatus.PUBLISHED else None
        translation = (
            BibleTranslation.objects.filter(code="EN_KING_JAMES_BIBLE").first()
            or BibleTranslation.objects.filter(language="en", is_active=True).order_by("sort_order", "name").first()
        )

        daily_refs = [
            ("John 3:16", "God's Love Revealed", "Receive the love of God as the foundation of your walk today."),
            ("Psalm 23:1", "The Lord Our Shepherd", "Let the Lord lead your decisions, rest, and confidence."),
            ("Matthew 6:33", "Seek First The Kingdom", "Place God's kingdom first and let every other pursuit take its proper place."),
            ("Romans 8:1", "No Condemnation", "Walk free from condemnation and respond to grace with obedience."),
            ("Philippians 4:6-7", "Prayer And Peace", "Bring every concern to God and receive His peace as your guard."),
            ("Isaiah 40:31", "Renewed Strength", "Wait on the Lord and exchange weariness for His strength."),
            ("Proverbs 3:5-6", "Trust And Direction", "Trust the Lord beyond your understanding and follow His direction."),
        ]
        daily_count = 0
        for offset in range(options["days"]):
            passage_ref, title, exhortation = daily_refs[offset % len(daily_refs)]
            obj, _ = BibleDailyPassage.objects.update_or_create(
                partner=partner,
                date=today + timedelta(days=offset),
                language="en",
                defaults={
                    "translation": translation,
                    "title": title,
                    "passage_ref": passage_ref,
                    "scripture_refs": [passage_ref],
                    "exhortation": exhortation,
                    "prayer_text": "Father, help me live this word faithfully today, in Jesus' name.",
                    "status": status,
                    "published_at": published_at,
                },
            )
            daily_count += 1 if obj else 0

        meditations = [
            ("message", "Walking In Kingdom Identity", "You are called to live as a citizen and ambassador of God's kingdom.", ["2 Corinthians 5:20"], ["identity", "kingdom"]),
            ("message", "A Life Built On The Word", "Let Scripture govern your thoughts, speech, plans, and relationships.", ["Psalm 119:105"], ["word", "discipleship"]),
            ("message", "Prayer As Daily Alignment", "Prayer is not only asking; it is returning the heart to God's will.", ["1 Thessalonians 5:17"], ["prayer"]),
        ]
        meditation_count = 0
        for content_type, title, body, refs, tags in meditations:
            BibleMeditationPost.objects.update_or_create(
                partner=partner,
                title=title,
                language="en",
                defaults={
                    "content_type": content_type,
                    "body": body,
                    "scripture_refs": refs,
                    "tags": tags,
                    "status": status,
                    "published_at": published_at,
                },
            )
            meditation_count += 1

        prayer_month, _ = BiblePrayerMonth.objects.update_or_create(
            partner=partner,
            year=today.year,
            month=today.month,
            language="en",
            defaults={
                "title": f"KCAN Prayer Calendar - {today.strftime('%B %Y')}",
                "theme": "Kingdom alignment, growth, and faithful witness",
                "status": status,
                "published_at": published_at,
            },
        )
        month_last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        for day in range(1, month_last_day.day + 1):
            BiblePrayerDay.objects.update_or_create(
                prayer_month=prayer_month,
                day=day,
                defaults={
                    "scripture_refs": ["Matthew 6:10"],
                    "exhortation": "Pray with faith, humility, and readiness to obey the Lord.",
                    "prayer_points": [
                        "Pray for a heart fully yielded to God's kingdom.",
                        "Pray for wisdom, purity, and courage in daily decisions.",
                        "Pray for KCAN families, leaders, and communities to bear lasting fruit.",
                    ],
                },
            )

        course, _ = BibleCourse.objects.update_or_create(
            partner=partner,
            title="KCAN Foundations",
            defaults={
                "subtitle": "Foundational lessons for Kingdom citizens and ambassadors",
                "description": "A manual KCAN starter course for grounding believers in Scripture, identity, prayer, and kingdom service.",
                "level": "foundational",
                "duration_minutes": 60,
                "is_bible_course": True,
                "is_free": True,
                "is_public": True,
                "published": status == BiblePublishStatus.PUBLISHED,
            },
        )
        module, _ = BibleCourseModule.objects.update_or_create(
            course=course,
            order=1,
            defaults={
                "title": "Foundations Of Kingdom Life",
                "summary": "Core convictions for living as a follower of Christ.",
            },
        )
        lesson_payloads = [
            ("Kingdom Identity", "Understand your identity in Christ before trying to represent Him.", "Read 2 Corinthians 5:17-20 and write down what God says about your new life."),
            ("The Word As Foundation", "Build daily life on Scripture, not pressure, fear, or culture.", "Read Matthew 7:24-27 and identify one area where obedience must become practical."),
            ("Prayer And Obedience", "Let prayer move you into alignment and action.", "Read Luke 11:1-4 and pray through each line slowly."),
        ]
        for order, (title, summary, content) in enumerate(lesson_payloads, start=1):
            BibleLesson.objects.update_or_create(
                course=course,
                order=order,
                defaults={
                    "module": module,
                    "title": title,
                    "summary": summary,
                    "content": content,
                    "language": "en",
                    "duration_minutes": 15,
                    "is_free": True,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded KCAN Bible content: {daily_count} daily passages, {meditation_count} meditations, "
                f"{month_last_day.day} prayer days, and {len(lesson_payloads)} lessons."
            )
        )
