from datetime import date, timedelta
from django.db import migrations


def seed_bible_data(apps, schema_editor):
    BibleTranslation = apps.get_model("bible", "BibleTranslation")
    BibleBook = apps.get_model("bible", "BibleBook")
    BibleChapter = apps.get_model("bible", "BibleChapter")
    BibleVerse = apps.get_model("bible", "BibleVerse")
    BibleAudio = apps.get_model("bible", "BibleAudio")
    BibleAudioSegment = apps.get_model("bible", "BibleAudioSegment")
    DailyDevotional = apps.get_model("bible", "DailyDevotional")
    MeditationTopic = apps.get_model("bible", "MeditationTopic")
    ReadingPlan = apps.get_model("bible", "ReadingPlan")
    ReadingPlanItem = apps.get_model("bible", "ReadingPlanItem")

    translations = [
        ("KJV", "King James Version"),
        ("NKJV", "New King James Version"),
        ("NIV", "New International Version"),
        ("ESV", "English Standard Version"),
        ("NLT", "New Living Translation"),
        ("CSB", "Christian Standard Bible"),
        ("AMP", "Amplified Bible"),
    ]

    translation_objs = []
    for idx, (code, name) in enumerate(translations):
        translation_objs.append(
            BibleTranslation.objects.create(
                code=code,
                name=name,
                language="en",
                sort_order=idx,
                is_active=True,
            )
        )

    books = [
        ("GEN", "Genesis", "OT", 1),
        ("PSA", "Psalms", "OT", 19),
        ("JHN", "John", "NT", 43),
    ]
    book_objs = {}
    for code, name, testament, order in books:
        book = BibleBook.objects.create(code=code, name=name, testament=testament, order=order)
        book_objs[code] = book

    chapters = []
    for book in book_objs.values():
        for number in range(1, 4):
            chapters.append(BibleChapter.objects.create(book=book, number=number))

    for translation in translation_objs:
        for chapter in chapters:
            for verse_number in range(1, 11):
                text = (
                    f"{translation.code} {chapter.book.name} {chapter.number}:{verse_number} "
                    "placeholder verse for testing. Walk in faith and wisdom today."
                )
                BibleVerse.objects.create(
                    translation=translation,
                    chapter=chapter,
                    number=verse_number,
                    text=text,
                )

            audio = BibleAudio.objects.create(
                translation=translation,
                chapter=chapter,
                duration_ms=10 * 6000,
            )
            verses = BibleVerse.objects.filter(translation=translation, chapter=chapter).order_by("number")
            start = 0
            for verse in verses:
                end = start + 6000
                BibleAudioSegment.objects.create(audio=audio, verse=verse, start_ms=start, end_ms=end)
                start = end

    today = date.today()
    for i in range(7):
        devotional_date = today - timedelta(days=i)
        DailyDevotional.objects.create(
            date=devotional_date,
            translation=translation_objs[0],
            passage_ref="Psalm 23",
            title=f"Daily Meditation {i + 1}",
            content=(
                "Meditate on the shepherding care of God. "
                "Consider where you need guidance, rest, and courage today."
            ),
            prayer_text="Lord, lead me beside still waters and restore my soul.",
        )

    topics = [
        "Faith",
        "Hope",
        "Love",
        "Peace",
        "Courage",
        "Forgiveness",
        "Wisdom",
        "Obedience",
        "Joy",
        "Discipline",
        "Purpose",
        "Healing",
    ]
    for topic in topics:
        MeditationTopic.objects.create(name=topic, description=f"Daily focus on {topic.lower()}.")

    plans = [
        ("One Year Bible", "Read the Bible in 365 days.", 365),
        ("90-Day New Testament", "Read the New Testament in 90 days.", 90),
        ("Psalms & Proverbs", "Daily wisdom from Psalms and Proverbs.", 60),
    ]
    for name, description, days in plans:
        plan = ReadingPlan.objects.create(name=name, description=description, days_count=days, is_system=True)
        for day in range(1, min(days, 10) + 1):
            ReadingPlanItem.objects.create(plan=plan, day_index=day, passage_ref=f"Day {day}: Psalm {day}")


def reverse_seed(apps, schema_editor):
    apps.get_model("bible", "ReadingPlanItem").objects.all().delete()
    apps.get_model("bible", "ReadingPlan").objects.all().delete()
    apps.get_model("bible", "MeditationTopic").objects.all().delete()
    apps.get_model("bible", "DailyDevotional").objects.all().delete()
    apps.get_model("bible", "BibleAudioSegment").objects.all().delete()
    apps.get_model("bible", "BibleAudio").objects.all().delete()
    apps.get_model("bible", "BibleVerse").objects.all().delete()
    apps.get_model("bible", "BibleChapter").objects.all().delete()
    apps.get_model("bible", "BibleBook").objects.all().delete()
    apps.get_model("bible", "BibleTranslation").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_bible_data, reverse_seed),
    ]
