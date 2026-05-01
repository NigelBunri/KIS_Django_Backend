import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.bible.importers import scan_bible_translation_registry
from apps.bible.models import (
    BibleBook,
    BibleChapter,
    BibleTranslation,
    BibleTranslationCopyrightStatus,
    BibleTranslationMetadata,
    BibleTranslationValidationStatus,
    BibleVerse,
)
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
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
        self.kcan = Partner.objects.create(
            owner=self.admin_user,
            name="KCAN, Kingdom Citizens & Ambassadors Network",
            slug="kcan",
            main_conversation=conversation,
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
        self.assertEqual(response.data[0]["full_name"], "KING JAMES BIBLE")

    def _create_public_reader_fixture(self):
        translation = BibleTranslation.objects.create(code="EN_KJV", name="King James Bible", language="en", is_active=True)
        BibleTranslationMetadata.objects.create(
            translation=translation,
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
        book = BibleBook.objects.create(code="JOHN", name="John", testament="NT", order=43)
        chapter = BibleChapter.objects.create(book=book, number=3)
        verse_16 = BibleVerse.objects.create(translation=translation, chapter=chapter, number=16, text="For God so loved the world.")
        BibleVerse.objects.create(translation=translation, chapter=chapter, number=17, text="For God sent not his Son.")
        return translation, book, chapter, verse_16

    def test_reader_supports_passage_reference_ranges(self):
        self._create_public_reader_fixture()
        self.client.force_authenticate(self.reader)

        response = self.client.get("/api/v1/bible/reader/", {"translation": "EN_KJV", "reference": "John 3:16-17"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["reference"], "John 3:16-17")
        self.assertEqual([item["number"] for item in response.data["verses"]], [16, 17])
        self.assertIn("navigation", response.data)

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
