"""Covers the channel_content_video upload context added in
apps/media/upload_intent.py — confirm-only (like status_video/
education_material), used by website's channel creator studio to get a
real, S3-verified storage_path before calling
apps.broadcasts.views.ChannelContentAssetUploadView directly (that view
already accepts a client-supplied storage_path — see its own docstring/
comments — so unlike status_video/education_material's downstream
"attach via feature endpoint" pattern, there's no separate attach step to
test here; initiate+confirm succeeding with the right size/type
enforcement IS the full contract this context adds).
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import MediaUploadIntent
from .tests import _mock_s3_client

INITIATE_URL = "/api/v1/media/uploads/initiate/"


def _confirm_url(upload_id):
    return f"/api/v1/media/uploads/{upload_id}/confirm/"


@patch("apps.media.storage_backends.S3MediaStorage._client")
class ChannelContentVideoUploadContextTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="+237670103001", password="TestPass123!", country="CM")
        self.client.force_authenticate(self.owner)

    def test_initiate_returns_presigned_put_and_storage_key(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self.client.post(
            INITIATE_URL,
            {"context": "channel_content_video", "filename": "clip.mp4", "content_type": "video/mp4", "size_bytes": 5_000_000},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data.get("uploadUrl"))
        self.assertTrue(response.data.get("storageKey"))
        self.assertTrue(response.data["storageKey"].startswith("private/channel/video/"))

    def test_confirm_succeeds_and_returns_confirm_only_descriptor(self, mock_client):
        client = _mock_s3_client()
        client.head_object.return_value = {"ContentLength": 5_000_000, "ContentType": "video/mp4"}
        mock_client.return_value = client
        initiate = self.client.post(
            INITIATE_URL,
            {"context": "channel_content_video", "filename": "clip.mp4", "content_type": "video/mp4", "size_bytes": 5_000_000},
            format="json",
        )
        upload_id = initiate.data["uploadId"]
        confirm = self.client.post(_confirm_url(upload_id), {}, format="json")
        self.assertEqual(confirm.status_code, 200, confirm.data)
        self.assertEqual(confirm.data["mediaId"], upload_id)
        self.assertTrue(confirm.data.get("assetId"))
        intent = MediaUploadIntent.objects.get(id=upload_id)
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_CONFIRMED)

    def test_rejects_unsupported_content_type(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self.client.post(
            INITIATE_URL,
            {"context": "channel_content_video", "filename": "clip.exe", "content_type": "application/x-msdownload", "size_bytes": 5_000_000},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)

    @override_settings(CHANNEL_CONTENT_VIDEO_MAX_UPLOAD_BYTES=10_000_000)
    def test_rejects_oversized_upload_using_its_own_ceiling_not_status_videos(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        # Comfortably over this context's own (overridden) ceiling, but well
        # under status_video's real-world 50MB default - proves this
        # context enforces its own configured limit, not status_video's.
        response = self.client.post(
            INITIATE_URL,
            {"context": "channel_content_video", "filename": "clip.mp4", "content_type": "video/mp4", "size_bytes": 20_000_000},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("size_bytes", response.data)

    def test_default_ceiling_allows_a_large_file_status_video_would_reject(self, mock_client):
        """Confirms the *default* (no override) ceiling is meaningfully
        larger than status_video's 50MB - the whole point of not reusing
        that context."""
        mock_client.return_value = _mock_s3_client()
        response = self.client.post(
            INITIATE_URL,
            {"context": "channel_content_video", "filename": "clip.mp4", "content_type": "video/mp4", "size_bytes": 200_000_000},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
