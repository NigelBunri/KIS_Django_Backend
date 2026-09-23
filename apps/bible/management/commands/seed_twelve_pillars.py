"""
Seed "The 12 Pillars of the Christian Faith" as a locked, sequential,
day-by-day discipleship course: 12 modules (one per doctrine), each split
into 6 "day" lessons (Day 1 = Overview + Biblical Foundation, Day 2 =
Biblical Development, Day 3 = Christ Revealed, Day 4 = Kingdom
Implications, Day 5 = Practical Implications, Day 6 = Summary), each day
ending in its own 5-question MCQ quiz gating access to the next day —
and, once all 6 days of a doctrine are passed, the next doctrine.

Source content: ~/dev/12_Pillars_Christian_Faith/books/*/book*.html
(sibling repo checkout, not part of this Django project).

Usage:
    python manage.py seed_twelve_pillars
    python manage.py seed_twelve_pillars --content-root /path/to/12_Pillars_Christian_Faith

Idempotent: safe to re-run. No real user progress exists on this course's
lessons/quizzes as of this rewrite, so each run fully drops and rebuilds
this course's modules/lessons/quizzes/questions/choices rather than trying
to reconcile a changed day-count against the old 1-lesson-per-module shape.
"""
import os
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.bible.models import (
    BibleCourse,
    BibleCourseModule,
    BibleLesson,
    BibleQuiz,
    BibleQuizChoice,
    BibleQuizQuestion,
)
from apps.partners.seed import ensure_kis_partner, KCAN_PARTNER_SLUG
from apps.partners.models import Partner

from ._twelve_pillars_books import BOOK_FILES
from ._twelve_pillars_quiz_bank import QUIZ_BANK

COURSE_TITLE = "The 12 Pillars of the Christian Faith"
# Marker stored in BibleCourse.level (a free-text field) so views.py can opt
# this course into strict sequential day/module locking without a schema change.
SEQUENTIAL_LOCK_LEVEL = "sequential-locked"

# Day N maps to these source block label(s), in order. Day 1 folds the
# intro "Overview" paragraph (not one of the six named sections) together
# with "Biblical Foundation" so the count comes out to a clean 6 days.
DAY_LABELS = [
    ("Overview & Biblical Foundation", ["Overview", "Biblical Foundation"]),
    ("Biblical Development", ["Biblical Development"]),
    ("Christ Revealed", ["Christ Revealed"]),
    ("Kingdom Implications", ["Kingdom Implications"]),
    ("Practical Implications", ["Practical Implications"]),
    ("Summary", ["Summary"]),
]


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def _extract_book(path):
    from lxml import html as lhtml

    tree = lhtml.parse(path).getroot()
    sections = tree.xpath('//section[contains(@class,"chapter") and starts-with(@id,"doc")]')
    doctrines = []
    for sec in sections:
        doctnum_el = sec.xpath('.//p[@class="doctnum"]')
        title_el = sec.xpath('.//h3[@class="doctrine-title"]')
        sub_el = sec.xpath('.//p[@class="doctrine-sub"]')
        if not doctnum_el or not title_el:
            continue
        m = re.search(r"\d+", doctnum_el[0].text_content())
        if not m:
            continue
        num = int(m.group())
        title = _clean(title_el[0].text_content())
        sub = _clean(sub_el[0].text_content()) if sub_el else ""

        blocks = []  # list of (label, [text, ...])
        current_label = "Overview"
        current_texts = []
        in_head = False
        for child in sec:
            cls = child.get("class", "") or ""
            tag = child.tag
            if tag == "div" and "doctrine-head" in cls:
                in_head = True
                continue
            in_head = False
            if tag == "h4" and "section-label" in cls:
                if current_texts:
                    blocks.append((current_label, current_texts))
                current_label = _clean(child.text_content())
                current_texts = []
                continue
            if in_head:
                continue
            if tag == "h3" and "doctrine-title" not in cls:
                # Sub-subheading inside a section (e.g. the canonical
                # Genesis-to-Revelation walkthrough within "Biblical
                # Development") — keep as an inline sub-header, not a new
                # top-level section, so nothing gets dropped or misfiled.
                sub_text = _clean(child.text_content())
                if sub_text:
                    current_texts.append(f"### {sub_text}")
                continue
            text = _clean(" ".join(t for t in child.itertext() if t and t.strip()))
            if text:
                current_texts.append(text)
        if current_texts:
            blocks.append((current_label, current_texts))

        doctrines.append({"num": num, "title": title, "sub": sub, "blocks": blocks})
    return doctrines


def _render_day_content(blocks_by_label, labels) -> str:
    parts = []
    for i, label in enumerate(labels):
        texts = blocks_by_label.get(label, [])
        # Only add an explicit "## " header when folding more than one
        # source section into a single day (Day 1) — a single-section day
        # doesn't need a header restating what the lesson title already says.
        if len(labels) > 1 and i > 0:
            parts.append(f"## {label}")
            parts.append("")
        for t in texts:
            parts.append(t)
            parts.append("")
    return "\n".join(parts).strip() + "\n"


class Command(BaseCommand):
    help = (
        'Seed "The 12 Pillars of the Christian Faith" discipleship course '
        "(12 doctrines x 6 days each) from 12_Pillars_Christian_Faith HTML books."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--content-root",
            default=os.path.expanduser("~/dev/12_Pillars_Christian_Faith"),
            help="Path to the 12_Pillars_Christian_Faith checkout.",
        )

    def handle(self, *args, **options):
        content_root = options["content_root"]
        books_dir = os.path.join(content_root, "books")
        if not os.path.isdir(books_dir):
            raise CommandError(f"books dir not found: {books_dir}")

        all_doctrines = {}
        for rel_path in BOOK_FILES:
            full_path = os.path.join(books_dir, rel_path)
            if not os.path.isfile(full_path):
                raise CommandError(f"book file not found: {full_path}")
            for d in _extract_book(full_path):
                all_doctrines[d["num"]] = d

        missing = [n for n in range(1, 13) if n not in all_doctrines]
        if missing:
            raise CommandError(f"missing doctrines after extraction: {missing}")

        for num in range(1, 13):
            bank = QUIZ_BANK.get(num)
            if not bank or set(bank.keys()) != set(range(1, 7)):
                raise CommandError(f"quiz bank for doctrine {num} missing or incomplete (expected days 1-6)")
            for day_num, questions in bank.items():
                if len(questions) != 5:
                    raise CommandError(f"doctrine {num} day {day_num}: expected 5 questions, got {len(questions)}")

        ensure_kis_partner()
        partner = Partner.objects.filter(slug=KCAN_PARTNER_SLUG).first()

        with transaction.atomic():
            course, _ = BibleCourse.objects.update_or_create(
                title=COURSE_TITLE,
                defaults={
                    "partner": partner,
                    "subtitle": "A twelve-step foundation in sound doctrine",
                    "description": (
                        "A sequential, Scripture-grounded walk through the twelve foundational "
                        "doctrines of the Christian faith — God, Christ, the Spirit, Man, Sin, "
                        "Salvation, Creation, the Church, the End Times, Angels, the Enemy, and "
                        "Victory in Christ. Each doctrine unfolds over six days; each day ends "
                        "in a short test, and 70% or higher unlocks the next day — complete all "
                        "six days of a doctrine to unlock the next doctrine."
                    ),
                    "level": SEQUENTIAL_LOCK_LEVEL,
                    "is_bible_course": True,
                    "is_free": True,
                    "is_public": True,
                    "published": True,
                    "duration_minutes": 12 * 6 * 15,
                },
            )

            # Full rebuild of this course's lessons/quizzes/questions/choices —
            # no real user progress exists on the old 1-lesson-per-module shape,
            # so reconciling is unnecessary complexity. Quizzes aren't CASCADE
            # from lesson (lesson FK is SET_NULL), so delete them explicitly.
            BibleQuiz.objects.filter(course=course).delete()
            BibleLesson.objects.filter(course=course).delete()

            lesson_order = 0
            for num in range(1, 13):
                d = all_doctrines[num]
                blocks_by_label = {label: texts for label, texts in d["blocks"]}

                module, _ = BibleCourseModule.objects.update_or_create(
                    course=course,
                    order=num,
                    defaults={"title": d["title"], "summary": d["sub"]},
                )

                bank = QUIZ_BANK[num]
                for day_num, (day_title, source_labels) in enumerate(DAY_LABELS, start=1):
                    lesson_order += 1
                    content = _render_day_content(blocks_by_label, source_labels)
                    lesson = BibleLesson.objects.create(
                        course=course,
                        module=module,
                        order=lesson_order,
                        title=f"Doctrine {num}, Day {day_num}: {day_title}",
                        summary=d["sub"] if day_num == 1 else f"{d['title']} — {day_title}",
                        content=content,
                        is_free=True,
                        duration_minutes=15,
                    )
                    quiz = BibleQuiz.objects.create(
                        course=course,
                        lesson=lesson,
                        order=lesson_order,
                        title=f"Doctrine {num}, Day {day_num} Test: {day_title}",
                        description="Score 70% or higher to unlock the next day.",
                        pass_score=70,
                        time_limit_minutes=0,
                        attempts_allowed=0,
                        is_exam=False,
                        is_active=True,
                    )
                    for qi, q in enumerate(bank[day_num], start=1):
                        question = BibleQuizQuestion.objects.create(
                            quiz=quiz,
                            prompt=q["prompt"],
                            kind="single_choice",
                            points=1,
                            order=qi,
                        )
                        for choice_text, is_correct in q["choices"]:
                            BibleQuizChoice.objects.create(
                                question=question, text=choice_text, is_correct=is_correct
                            )

        self.stdout.write(self.style.SUCCESS(
            f"Seeded course '{COURSE_TITLE}' (id={course.id}) with 12 doctrines x 6 days "
            f"({lesson_order} lessons, {lesson_order} quizzes, {lesson_order * 5} questions)."
        ))
