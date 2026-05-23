import json
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.db import models
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.bible.importers import scan_bible_translation_registry
from apps.bible.models import (
    BibleBook,
    BibleBookmark,
    BibleChapter,
    BibleHighlight,
    BibleNote,
    BiblePreference,
    BibleReadingPlanEvent,
    BiblePublishStatus,
    BibleCourse,
    BibleDailyPassage,
    BibleLesson,
    BibleMeditationPost,
    ReadingHistory,
    BibleTranslation,
    BibleTranslationCopyrightStatus,
    BibleTranslationMetadata,
    BibleTranslationValidationStatus,
    BibleVerse,
)
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.notifications.models import Notification
from apps.partners.models import Partner


class BibleTranslationRegistryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = self._create_user("admin", "+237670010001")
        self.reader = self._create_user("reader", "+237670010002")
        conversation = Conversation.objects.create(
            type=ConversationType.CHANNEL,
            title="KCAN",
            created_by=self.admin_user,
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=self.admin_user,
            base_role=BaseConversationRole.OWNER,
        )
        self.kcan, _ = Partner.objects.get_or_create(
            slug="kcan",
            defaults={
                "owner": self.admin_user,
                "name": "KCAN, Kingdom Citizens & Ambassadors Network",
                "main_conversation": conversation,
            },
        )

    def _create_user(self, username: str, phone: str) -> User:
        return User.objects.create_user(
            phone=phone,
            country="CM",
            password="pass1234",
            email=f"{username}@example.com",
            username=username,
            display_name=username.title(),
            phone_country_code="+237",
            phone_number=phone[-9:],
        )

    def _write_translation(self, root: Path, language: str, filename: str):
        language_dir = root / language
        language_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "Genesis": {"1": {"1": "In the beginning God created the heaven and the earth."}},
            "John": {"3": {"16": "For God so loved the world."}},
        }
        (language_dir / filename).write_text(json.dumps(payload), encoding="utf-8")

    def test_scan_registry_keeps_modern_translations_private_by_default(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_translation(root, "en", "KING JAMES BIBLE.json")
            self._write_translation(root, "en", "NEW INTERNATIONAL VERSION.json")

            scanned = scan_bible_translation_registry(root_dir=root)

        self.assertEqual(len(scanned), 2)
        kjv = BibleTranslationMetadata.objects.get(full_name="KING JAMES BIBLE")
        niv = BibleTranslationMetadata.objects.get(full_name="NEW INTERNATIONAL VERSION")
        self.assertEqual(kjv.copyright_status, BibleTranslationCopyrightStatus.PUBLIC_DOMAIN)
        self.assertTrue(kjv.is_licensed)
        self.assertTrue(kjv.is_public)
        self.assertTrue(kjv.import_enabled)
        self.assertEqual(kjv.validation_status, BibleTranslationValidationStatus.VALID)
        self.assertEqual(niv.copyright_status, BibleTranslationCopyrightStatus.RESTRICTED)
        self.assertFalse(niv.is_licensed)
        self.assertFalse(niv.is_public)
        self.assertFalse(niv.import_enabled)

    def test_public_translation_list_excludes_unlicensed_metadata(self):
        public_translation = BibleTranslation.objects.create(code="EN_KJV", name="King James Bible", language="en")
        restricted_translation = BibleTranslation.objects.create(code="EN_NIV", name="New International Version", language="en")
        BibleTranslationMetadata.objects.create(
            translation=public_translation,
            code="EN_KJV",
            language="en",
            full_name="King James Bible",
            source_path="en/KING JAMES BIBLE.json",
            source_filename="KING JAMES BIBLE.json",
            copyright_status=BibleTranslationCopyrightStatus.PUBLIC_DOMAIN,
            is_licensed=True,
            is_public=True,
            validation_status=BibleTranslationValidationStatus.VALID,
        )
        BibleTranslationMetadata.objects.create(
            translation=restricted_translation,
            code="EN_NIV",
            language="en",
            full_name="New International Version",
            source_path="en/NEW INTERNATIONAL VERSION.json",
            source_filename="NEW INTERNATIONAL VERSION.json",
            copyright_status=BibleTranslationCopyrightStatus.RESTRICTED,
            is_licensed=False,
            is_public=False,
            validation_status=BibleTranslationValidationStatus.VALID,
        )

        self.client.force_authenticate(self.reader)
        response = self.client.get("/api/v1/bible/translations/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        codes = {item["code"] for item in response.data}
        self.assertIn("EN_KJV", codes)
        self.assertNotIn("EN_NIV", codes)

    def test_translation_registry_scan_endpoint_requires_kcan_admin(self):
        self.client.force_authenticate(self.reader)
        denied = self.client.post("/api/v1/bible/translation-registry/scan/", {}, format="json")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_translation(root, "en", "KING JAMES BIBLE.json")
            # The endpoint scans the configured root, so use the direct scanner here to avoid
            # changing process-wide settings in the test.
            scan_bible_translation_registry(root_dir=root)

        self.client.force_authenticate(self.admin_user)
        response = self.client.get("/api/v1/bible/translation-registry/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        registry_items = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual(registry_items[0]["full_name"], "KING JAMES BIBLE")

    def _create_public_reader_fixture(self):
        BibleBook.objects.filter(models.Q(code="JOHN") | models.Q(name="John")).delete()
        translation, _ = BibleTranslation.objects.update_or_create(
            code="EN_KJV",
            defaults={"name": "King James Bible", "language": "en", "is_active": True},
        )
        BibleTranslationMetadata.objects.update_or_create(
            code="EN_KJV",
            defaults={
                "translation": translation,
                "language": "en",
                "full_name": "King James Bible",
                "source_path": "en/KING JAMES BIBLE.json",
                "source_filename": "KING JAMES BIBLE.json",
                "copyright_status": BibleTranslationCopyrightStatus.PUBLIC_DOMAIN,
                "is_licensed": True,
                "is_public": True,
                "validation_status": BibleTranslationValidationStatus.VALID,
            },
        )
        book, _ = BibleBook.objects.update_or_create(
            code="JOHN",
            defaults={"name": "John", "testament": "NT", "order": 43},
        )
        chapter, _ = BibleChapter.objects.get_or_create(book=book, number=3)
        verse_16, _ = BibleVerse.objects.update_or_create(
            translation=translation,
            chapter=chapter,
            number=16,
            defaults={"text": "For God so loved the world."},
        )
        BibleVerse.objects.update_or_create(
            translation=translation,
            chapter=chapter,
            number=17,
            defaults={"text": "For God sent not his Son."},
        )
        return translation, book, chapter, verse_16

    def test_reader_supports_passage_reference_ranges(self):
        self._create_public_reader_fixture()
        self.client.force_authenticate(self.reader)

        response = self.client.get("/api/v1/bible/reader/", {"translation": "EN_KJV", "reference": "John 3:16-17"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["reference"], "John 3:16-17")
        self.assertEqual([item["number"] for item in response.data["verses"]], [16, 17])
        self.assertIn("navigation", response.data)

    def test_reader_uses_bundled_kjv_when_database_import_is_missing(self):
        BibleVerse.objects.all().delete()
        BibleChapter.objects.all().delete()
        BibleBook.objects.all().delete()
        BibleTranslationMetadata.objects.all().delete()
        BibleTranslation.objects.all().delete()

        response = self.client.get(
            "/api/v1/bible/reader/",
            {"translation": "EN_KING_JAMES_BIBLE", "book": "GENESIS", "chapter": "1"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["translation"]["code"], "EN_KING_JAMES_BIBLE")
        self.assertEqual(response.data["book"]["code"], "GENESIS")
        self.assertEqual(response.data["chapter"]["number"], 1)
        self.assertEqual(len(response.data["verses"]), 31)
        self.assertEqual(response.data["verses"][0]["text"], "In the beginning God created the heaven and the earth.")
        self.assertEqual(response.data["source"], "bundled_kjv_fallback")

    def test_reader_uses_bundled_kjv_for_reference_when_database_import_is_missing(self):
        BibleVerse.objects.all().delete()
        BibleChapter.objects.all().delete()
        BibleBook.objects.all().delete()
        BibleTranslationMetadata.objects.all().delete()
        BibleTranslation.objects.all().delete()

        response = self.client.get(
            "/api/v1/bible/reader/",
            {"translation": "EN_KING_JAMES_BIBLE", "reference": "John 3:16-17"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["reference"], "John 3:16-17")
        self.assertEqual([item["number"] for item in response.data["verses"]], [16, 17])
        self.assertTrue(response.data["verses"][0]["text"].startswith("For God so loved"))

    def test_reader_rejects_restricted_translation_direct_access(self):
        self._create_public_reader_fixture()
        restricted = BibleTranslation.objects.create(code="EN_NIV", name="New International Version", language="en", is_active=True)
        BibleTranslationMetadata.objects.create(
            translation=restricted,
            code="EN_NIV",
            language="en",
            full_name="New International Version",
            source_path="en/NEW INTERNATIONAL VERSION.json",
            source_filename="NEW INTERNATIONAL VERSION.json",
            copyright_status=BibleTranslationCopyrightStatus.RESTRICTED,
            is_licensed=False,
            is_public=False,
            validation_status=BibleTranslationValidationStatus.VALID,
        )
        self.client.force_authenticate(self.reader)

        response = self.client.get("/api/v1/bible/reader/", {"translation": "EN_NIV", "reference": "John 3:16"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reading_event_can_be_created_from_selected_verse(self):
        translation, _, _, verse = self._create_public_reader_fixture()
        self.client.force_authenticate(self.reader)

        response = self.client.post(
            "/api/v1/bible/reading-events/from-selection/",
            {
                "translation": translation.code,
                "verses": [verse.id],
                "start_at": "2026-05-01T08:00:00Z",
                "reminder_offsets": [15],
                "reminder_channels": ["in_app", "push"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["passage_ref"], "John 3:16")

    def test_spiritual_growth_summary_exposes_reader_journey_safety_and_publishing(self):
        translation, _, chapter, verse = self._create_public_reader_fixture()
        BiblePreference.objects.create(user=self.reader, default_translation=translation, enable_offline_cache=True)
        ReadingHistory.objects.create(user=self.reader, translation=translation, chapter=chapter, last_verse=16)
        BibleBookmark.objects.create(user=self.reader, verse=verse)
        BibleHighlight.objects.create(user=self.reader, verse=verse, color="#D4AF37")
        BibleNote.objects.create(user=self.reader, verse=verse, text="God loves the world.")
        BibleReadingPlanEvent.objects.create(
            user=self.reader,
            translation=translation,
            passage_ref="John 3",
            start_at=timezone.now() - timedelta(hours=1),
            status="scheduled",
        )
        BibleDailyPassage.objects.create(
            partner=self.kcan,
            date=timezone.now().date(),
            language="en",
            translation=translation,
            title="Love of God",
            passage_ref="John 3:16",
            status=BiblePublishStatus.PUBLISHED,
            published_at=timezone.now(),
        )
        BibleMeditationPost.objects.create(
            partner=self.kcan,
            title="Meditate on love",
            body="God's love forms us.",
            status=BiblePublishStatus.PUBLISHED,
            published_at=timezone.now(),
        )
        self.client.force_authenticate(self.reader)

        response = self.client.get("/api/v1/bible/spiritual-growth-summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["counts"]["bookmarks"], 1)
        self.assertEqual(response.data["counts"]["highlights"], 1)
        self.assertEqual(response.data["counts"]["notes"], 1)
        self.assertGreaterEqual(response.data["counts"]["missed_reading_events"], 1)
        self.assertTrue(response.data["readiness"]["offline_scripture_ready"])
        self.assertTrue(response.data["readiness"]["family_safe_journey"])
        self.assertEqual(response.data["safety"]["media_gate"], "enabled")
        self.assertFalse(response.data["safety"]["explicit_content_provider_live_calls"])

    @override_settings(
        MEDIA_SAFETY_ENABLED=True,
        MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED=False,
        KIS_AI_LIVE_PROVIDER_CALLS_ENABLED=False,
        KIS_PUBLIC_WEB_INDEXING_ENABLED=False,
    )
    def test_verify_bible_launch_command_passes_safe_defaults(self):
        output = StringIO()

        call_command("verify_bible_launch", stdout=output)

        rendered = output.getvalue()
        self.assertIn("Bible/KCAN launch guardrails ready: True", rendered)
        self.assertIn("PASS: bible_urls_present", rendered)
        self.assertIn("PASS: bible_attachment_public_serialization", rendered)
        self.assertIn("PASS: bible_media_safety_gate", rendered)
        self.assertNotIn("private/bible/raw", rendered)

    def test_bible_lesson_serializer_redacts_private_attachment_fields(self):
        _, _, _, _ = self._create_public_reader_fixture()
        course = BibleCourse.objects.create(title="Safe Bible Course", is_bible_course=True, is_public=True, published=True)
        lesson = BibleLesson.objects.create(
            course=course,
            title="Safe lesson",
            attachments=[
                {
                    "url": "https://cdn.example.com/lesson.pdf",
                    "storage_path": "private/bible/raw/lesson.pdf",
                    "token": "secret-token",
                    "metadata": {"path": "/private/raw/lesson.pdf", "safe": "ok"},
                }
            ],
        )
        self.client.force_authenticate(self.reader)

        response = self.client.get(f"/api/v1/bible/lessons/{lesson.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        rendered = json.dumps(response.data)
        self.assertIn("https://cdn.example.com/lesson.pdf", rendered)
        self.assertNotIn("storage_path", rendered)
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("/private/raw/lesson.pdf", rendered)

    def test_bible_reminder_notifications_have_exact_badge_source_and_target(self):
        translation, _, _, _ = self._create_public_reader_fixture()
        event = BibleReadingPlanEvent.objects.create(
            user=self.reader,
            translation=translation,
            passage_ref="John 3:16",
            start_at=timezone.now(),
            status="scheduled",
            reminder_offsets=[0],
            reminder_channels=["in_app"],
        )

        with patch("apps.notifications.tasks.process_notification_delivery.delay"):
            call_command("dispatch_bible_reading_reminders", lookback_minutes=5, stdout=StringIO())

        notification = Notification.objects.get(type="BIBLE_READING_REMINDER")
        self.assertEqual(notification.target_type, "bible_reading_event")
        self.assertEqual(notification.context_data.get("source"), "bible")
        self.assertEqual(notification.context_data.get("badge_source"), "bible")
        self.assertEqual(notification.context_data.get("target_id"), str(event.id))
