from __future__ import annotations

import tempfile
import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.broadcasts.models import BroadcastVideo
from apps.media.models import MediaSafetyScan
from apps.media.tasks import ContentSafetyResolutionTarget


class _FeedUploadTestBase(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone="5551014040", username="cs_wiring_user", password="secret", country="NG",
        )
        self.client.force_authenticate(user=self.user)
        self.temp_media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_media_dir.cleanup)


class BroadcastVideoUploadViewContentSafetyTests(_FeedUploadTestBase):
    @override_settings(MEDIA_ROOT=None, MEDIA_SAFETY_SERVICE_ENABLED=False)
    def test_flag_off_does_not_enqueue_scan_task(self):
        with override_settings(MEDIA_ROOT=self.temp_media_dir.name):
            upload = SimpleUploadedFile("clip.mp4", b"fake-video-bytes", content_type="video/mp4")
            with patch("apps.broadcasts.views._probe_video_duration", return_value=12.4), \
                 patch("apps.media.tasks.scan_video_and_resolve_task.delay") as mock_delay:
                response = self.client.post(
                    "/api/v1/broadcasts/videos/upload/",
                    {"file": upload, "title": "Clip"},
                    format="multipart",
                )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        mock_delay.assert_not_called()

    @override_settings(
        MEDIA_ROOT=None,
        MEDIA_SAFETY_SERVICE_ENABLED=True,
        MEDIA_SAFETY_SERVICE_BASE_URL="https://content-safety.internal",
        MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN="test-shared-secret",
    )
    def test_flag_on_queues_video_scan_and_enqueues_task(self):
        with override_settings(MEDIA_ROOT=self.temp_media_dir.name):
            upload = SimpleUploadedFile("clip.mp4", b"fake-video-bytes", content_type="video/mp4")
            with patch("apps.broadcasts.views._probe_video_duration", return_value=12.4), \
                 patch("apps.media.tasks.scan_video_and_resolve_task.delay") as mock_delay:
                response = self.client.post(
                    "/api/v1/broadcasts/videos/upload/",
                    {"file": upload, "title": "Clip"},
                    format="multipart",
                )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data.get("video_url"), "")
        video = BroadcastVideo.objects.get(id=response.data["id"])
        self.assertEqual(video.video_url, "")
        mock_delay.assert_called_once()
        scan_id = mock_delay.call_args.kwargs["scan_id"]
        scan = MediaSafetyScan.objects.get(id=scan_id)
        self.assertEqual(scan.result["resolution_target"], ContentSafetyResolutionTarget.BROADCAST_VIDEO.value)
        self.assertEqual(scan.result["resolution_id"], str(video.id))


class FeedAttachmentContentSafetyTests(_FeedUploadTestBase):
    @override_settings(MEDIA_ROOT=None, MEDIA_SAFETY_SERVICE_ENABLED=False)
    def test_flag_off_does_not_enqueue_scan_task(self):
        with override_settings(MEDIA_ROOT=self.temp_media_dir.name):
            upload = SimpleUploadedFile("clip.mp4", b"fake-video-bytes", content_type="video/mp4")
            with patch("apps.broadcasts.views._probe_video_duration", return_value=12.4), \
                 patch("apps.media.tasks.scan_video_and_resolve_task.delay") as mock_delay:
                response = self.client.post(
                    "/api/v1/broadcasts/profiles/feeds/",
                    {"title": "Video feed post", "media_type": "video", "attachments": [upload]},
                )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        mock_delay.assert_not_called()

    @override_settings(
        MEDIA_ROOT=None,
        MEDIA_SAFETY_SERVICE_ENABLED=True,
        MEDIA_SAFETY_SERVICE_BASE_URL="https://content-safety.internal",
        MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN="test-shared-secret",
    )
    def test_flag_on_still_creates_video_row_while_queued(self):
        """Before this fix, the video-row-creation condition required
        `not quarantine` — the queued-for-async-scan placeholder has
        quarantine=True, so without the is_queued_scan carve-out this
        would have silently stopped creating a BroadcastVideo row at all
        once the flag was on.

        Note: unlike BroadcastVideoUploadView (which returns video_url
        read directly off the BroadcastVideo row - correctly empty until
        resolved, see the sibling test class), a feed attachment's
        response goes through _normalize_feed_attachment, which recomputes
        `url` from the stored `path` unconditionally on every read/response
        - a pre-existing characteristic unrelated to this fix (it does the
        same regardless of quarantine reason), not something introduced or
        fixed here. So `url` here IS populated even while queued; the
        underlying BroadcastVideo.video_url field itself (what actually
        gets served/played) stays "" until scan_video_and_resolve_task
        resolves it - confirmed below."""
        with override_settings(MEDIA_ROOT=self.temp_media_dir.name):
            upload = SimpleUploadedFile("clip.mp4", b"fake-video-bytes", content_type="video/mp4")
            with patch("apps.broadcasts.views._probe_video_duration", return_value=12.4), \
                 patch("apps.media.tasks.scan_video_and_resolve_task.delay") as mock_delay:
                response = self.client.post(
                    "/api/v1/broadcasts/profiles/feeds/",
                    {"title": "Video feed post", "media_type": "video", "attachments": [upload]},
                )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        feed = response.data.get("feed") or {}
        attachments = feed.get("attachments") or []
        self.assertEqual(len(attachments), 1)
        attachment = attachments[0]
        self.assertTrue(attachment.get("video_id"))
        video = BroadcastVideo.objects.get(id=attachment["video_id"])
        self.assertEqual(video.video_url, "")
        mock_delay.assert_called_once()
        scan_id = mock_delay.call_args.kwargs["scan_id"]
        scan = MediaSafetyScan.objects.get(id=scan_id)
        self.assertEqual(scan.result["resolution_target"], ContentSafetyResolutionTarget.BROADCAST_VIDEO.value)
        self.assertEqual(scan.result["resolution_id"], str(video.id))
