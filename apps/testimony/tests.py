from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.media.models import MediaUploadIntent
from .models import UserTestimony


User = get_user_model()


def _create_user(phone: str, username: str):
    return User.objects.create_user(
        phone=phone,
        country="CM",
        password="pass1234",
        username=username,
        display_name=username.title(),
        phone_country_code="+237",
        phone_number=phone.replace("+237", ""),
    )


def _confirmed_intent(user, *, context="testimony_media", content_type="video/mp4"):
    return MediaUploadIntent.objects.create(
        owner=user,
        context=context,
        original_filename="story.mp4",
        object_key=f"private/testimony/media/{user.id}/story.mp4",
        content_type=content_type,
        size_bytes=1024,
        status=MediaUploadIntent.STATUS_CONFIRMED,
        expires_at=timezone.now() + datetime.timedelta(hours=1),
        confirmed_at=timezone.now(),
    )


@override_settings(SECURE_SSL_REDIRECT=False)
class TestimonyMediaAttachmentTests(APITestCase):
    """UserTestimony was text-only — no video/file attachment support
    existed anywhere. Covers the new resource_attachment flow."""

    def setUp(self):
        self.client = APIClient()
        self.user = _create_user("+237690830001", "tma_user")
        self.other_user = _create_user("+237690830002", "tma_other")
        self.client.force_authenticate(self.user)

    def _list_create_url(self):
        return reverse("testimonies-list")

    def test_create_with_video_attachment(self):
        intent = _confirmed_intent(self.user, content_type="video/mp4")
        response = self.client.post(
            self._list_create_url(),
            {
                "category": "faith",
                "title": "Overcame a season",
                "story": "Here is what happened.",
                "resource_attachment": {"media_id": str(intent.id)},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["media_kind"], "video")
        self.assertTrue(response.data["safe_resource_url"])

        testimony = UserTestimony.objects.get(id=response.data["id"])
        self.assertEqual(testimony.media_kind, "video")
        self.assertEqual(testimony.resource_mime_type, "video/mp4")
        intent.refresh_from_db()
        self.assertIsNotNone(intent.attached_at)

    def test_create_with_file_attachment(self):
        intent = _confirmed_intent(self.user, content_type="application/pdf")
        response = self.client.post(
            self._list_create_url(),
            {"category": "finances", "title": "Document", "resource_attachment": {"media_id": str(intent.id)}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["media_kind"], "file")

    def test_text_only_testimony_still_works(self):
        response = self.client.post(
            self._list_create_url(),
            {"category": "grief", "title": "Just words", "story": "No media needed."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["media_kind"], "none")
        self.assertEqual(response.data["safe_resource_url"], "")

    def test_cannot_attach_another_users_media(self):
        intent = _confirmed_intent(self.other_user)
        response = self.client.post(
            self._list_create_url(),
            {"category": "faith", "title": "Hijack attempt", "resource_attachment": {"media_id": str(intent.id)}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)
        self.assertFalse(UserTestimony.objects.filter(title="Hijack attempt").exists())

    def test_cannot_attach_media_from_a_different_context(self):
        intent = _confirmed_intent(self.user, context="commerce_product_main_image")
        response = self.client.post(
            self._list_create_url(),
            {"category": "faith", "title": "Wrong context", "resource_attachment": {"media_id": str(intent.id)}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

    def test_cannot_reuse_an_already_attached_media_id(self):
        intent = _confirmed_intent(self.user)
        intent.mark_attached()
        response = self.client.post(
            self._list_create_url(),
            {"category": "faith", "title": "Reuse attempt", "resource_attachment": {"media_id": str(intent.id)}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

    def test_unconfirmed_media_is_rejected(self):
        intent = _confirmed_intent(self.user)
        intent.status = MediaUploadIntent.STATUS_PENDING
        intent.save(update_fields=["status"])
        response = self.client.post(
            self._list_create_url(),
            {"category": "faith", "title": "Not confirmed yet", "resource_attachment": {"media_id": str(intent.id)}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
