from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.broadcasts.kisvideo_provider import (
    KisVideoProvider,
    KisVideoProviderError,
    sign_kisvideo_callback_token,
    verify_kisvideo_callback_token,
)
from apps.broadcasts.models import BroadcastChannel, ChannelContent, ChannelContentAsset, ChannelContentType


@override_settings(KIS_VIDEO_SERVICE_INTERNAL_TOKEN="test-shared-secret")
class CallbackTokenTests(SimpleTestCase):
    def test_round_trips(self):
        token = sign_kisvideo_callback_token("asset-123")
        self.assertTrue(verify_kisvideo_callback_token("asset-123", token))

    def test_rejects_wrong_asset_id(self):
        token = sign_kisvideo_callback_token("asset-123")
        self.assertFalse(verify_kisvideo_callback_token("asset-456", token))

    def test_rejects_tampered_token(self):
        token = sign_kisvideo_callback_token("asset-123")
        self.assertFalse(verify_kisvideo_callback_token("asset-123", token[:-1] + ("0" if token[-1] != "0" else "1")))

    def test_rejects_missing_token(self):
        self.assertFalse(verify_kisvideo_callback_token("asset-123", ""))


@override_settings(KIS_VIDEO_SERVICE_INTERNAL_TOKEN="", KIS_VIDEO_SERVICE_BASE_URL="")
class KisVideoProviderCredentialTests(SimpleTestCase):
    def test_raises_without_credentials(self):
        with self.assertRaises(KisVideoProviderError):
            KisVideoProvider().create_transcode_job(
                storage_path="videos/x.mp4",
                filename="x.mp4",
                content_type="video/mp4",
                owner_user_id="u1",
                callback_url="https://example.com/cb",
                caller_reference="ref-1",
            )


@override_settings(
    KIS_VIDEO_SERVICE_ENABLED=True,
    KIS_VIDEO_SERVICE_BASE_URL="https://kisvideo.internal",
    KIS_VIDEO_SERVICE_INTERNAL_TOKEN="test-shared-secret",
)
class KisVideoProviderUploadFlowTests(SimpleTestCase):
    @patch("apps.broadcasts.kisvideo_provider.default_storage")
    @patch("apps.broadcasts.kisvideo_provider._requests")
    def test_pushes_bytes_in_one_chunk_and_returns_upload_id(self, mock_requests, mock_storage):
        mock_storage.exists.return_value = True
        mock_storage.size.return_value = 5
        mock_storage.open.return_value.__enter__.return_value = io.BytesIO(b"hello")

        create_resp = MagicMock(ok=True, headers={"Location": "https://kisvideo.internal/uploads/up-1"})
        patch_resp = MagicMock(ok=True)
        mock_requests.post.return_value = create_resp
        mock_requests.patch.return_value = patch_resp

        result = KisVideoProvider().create_transcode_job(
            storage_path="videos/x.mp4",
            filename="x.mp4",
            content_type="video/mp4",
            owner_user_id="u1",
            callback_url="https://django.internal/cb?asset_id=a1&token=t",
            caller_reference="a1",
        )

        self.assertEqual(result, {"upload_id": "up-1"})
        post_kwargs = mock_requests.post.call_args.kwargs
        self.assertEqual(post_kwargs["headers"]["Upload-Length"], "5")
        self.assertEqual(post_kwargs["headers"]["X-Owner-User-Id"], "u1")
        self.assertEqual(post_kwargs["headers"]["X-Callback-Url"], "https://django.internal/cb?asset_id=a1&token=t")
        self.assertEqual(post_kwargs["headers"]["X-Caller-Reference"], "a1")
        patch_kwargs = mock_requests.patch.call_args.kwargs
        self.assertEqual(patch_kwargs["headers"]["Upload-Offset"], "0")
        self.assertEqual(patch_kwargs["data"], b"hello")

    @patch("apps.broadcasts.kisvideo_provider.default_storage")
    @patch("apps.broadcasts.kisvideo_provider._requests")
    def test_raises_on_failed_create(self, mock_requests, mock_storage):
        mock_storage.exists.return_value = True
        mock_storage.size.return_value = 5
        mock_requests.post.return_value = MagicMock(ok=False, status_code=500, text="boom")

        with self.assertRaises(KisVideoProviderError):
            KisVideoProvider().create_transcode_job(
                storage_path="videos/x.mp4",
                filename="x.mp4",
                content_type="video/mp4",
                owner_user_id="u1",
                callback_url="https://django.internal/cb",
                caller_reference="a1",
            )

    @patch("apps.broadcasts.kisvideo_provider.default_storage")
    @patch("apps.broadcasts.kisvideo_provider._requests")
    def test_network_failure_on_create_raises_provider_error(self, mock_requests, mock_storage):
        """A raw requests.exceptions.RequestException (timeout, connection
        reset, DNS blip) must funnel through KisVideoProviderError like
        every other failure here — push_asset_to_kisvideo's retry/failure
        handling only catches that one exception type, so anything else
        would bypass retries and strand the asset at 'queued' forever."""
        import requests

        mock_storage.exists.return_value = True
        mock_storage.size.return_value = 5
        mock_requests.post.side_effect = requests.exceptions.ConnectionError("connection reset")

        with self.assertRaises(KisVideoProviderError):
            KisVideoProvider().create_transcode_job(
                storage_path="videos/x.mp4",
                filename="x.mp4",
                content_type="video/mp4",
                owner_user_id="u1",
                callback_url="https://django.internal/cb",
                caller_reference="a1",
            )

    @patch("apps.broadcasts.kisvideo_provider.default_storage")
    @patch("apps.broadcasts.kisvideo_provider._requests")
    def test_network_failure_on_patch_raises_provider_error(self, mock_requests, mock_storage):
        import requests

        mock_storage.exists.return_value = True
        mock_storage.size.return_value = 5
        mock_storage.open.return_value.__enter__.return_value = io.BytesIO(b"hello")
        mock_requests.post.return_value = MagicMock(ok=True, headers={"Location": "https://kisvideo.internal/uploads/up-1"})
        mock_requests.patch.side_effect = requests.exceptions.Timeout("read timed out")

        with self.assertRaises(KisVideoProviderError):
            KisVideoProvider().create_transcode_job(
                storage_path="videos/x.mp4",
                filename="x.mp4",
                content_type="video/mp4",
                owner_user_id="u1",
                callback_url="https://django.internal/cb",
                caller_reference="a1",
            )

    @patch("apps.broadcasts.kisvideo_provider.default_storage")
    def test_raises_when_storage_path_missing(self, mock_storage):
        mock_storage.exists.return_value = False
        with self.assertRaises(KisVideoProviderError):
            KisVideoProvider().create_transcode_job(
                storage_path="videos/missing.mp4",
                filename="x.mp4",
                content_type="video/mp4",
                owner_user_id="u1",
                callback_url="https://django.internal/cb",
                caller_reference="a1",
            )


class _ChannelContentTestBase(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone="5557300001", username="kisvideo_owner", password="secret", country="NG",
        )
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle="kisvideo-channel",
            display_name="KisVideo Channel",
            is_public=True,
        )
        self.content = ChannelContent.objects.create(
            channel=self.channel,
            content_type=ChannelContentType.VIDEO,
            title="Some video",
            created_by=self.owner,
        )


class ChannelContentAssetUploadFlagGatingTests(_ChannelContentTestBase):
    @override_settings(KIS_VIDEO_SERVICE_ENABLED=False)
    def test_flag_off_does_not_queue_kisvideo_job(self):
        self.client.force_authenticate(user=self.owner)
        with patch("apps.broadcasts.tasks.push_asset_to_kisvideo.delay") as mock_delay:
            response = self.client.post(
                f"/api/v1/broadcasts/channel-contents/{self.content.id}/assets/",
                {"asset_type": "video", "storage_path": "videos/x.mp4", "mime_type": "video/mp4"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        mock_delay.assert_not_called()
        asset = ChannelContentAsset.objects.get(id=response.data["id"])
        self.assertEqual(asset.processing_status, "ready")
        self.content.refresh_from_db()
        self.assertNotEqual(self.content.status, ChannelContent.Status.PROCESSING)

    @override_settings(KIS_VIDEO_SERVICE_ENABLED=True)
    def test_flag_on_queues_kisvideo_job_for_video_asset(self):
        self.client.force_authenticate(user=self.owner)
        with patch("apps.broadcasts.tasks.push_asset_to_kisvideo.delay") as mock_delay:
            response = self.client.post(
                f"/api/v1/broadcasts/channel-contents/{self.content.id}/assets/",
                {"asset_type": "video", "storage_path": "videos/x.mp4", "mime_type": "video/mp4"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        asset = ChannelContentAsset.objects.get(id=response.data["id"])
        mock_delay.assert_called_once_with(str(asset.id))
        self.assertEqual(asset.processing_status, "queued")
        self.content.refresh_from_db()
        self.assertEqual(self.content.status, ChannelContent.Status.PROCESSING)

    @override_settings(KIS_VIDEO_SERVICE_ENABLED=True)
    def test_flag_on_ignores_non_video_asset(self):
        self.client.force_authenticate(user=self.owner)
        with patch("apps.broadcasts.tasks.push_asset_to_kisvideo.delay") as mock_delay:
            response = self.client.post(
                f"/api/v1/broadcasts/channel-contents/{self.content.id}/assets/",
                {"asset_type": "image", "url": "https://example.com/x.jpg"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        mock_delay.assert_not_called()

    @override_settings(KIS_VIDEO_SERVICE_ENABLED=True)
    def test_flag_on_ignores_video_asset_without_storage_path(self):
        self.client.force_authenticate(user=self.owner)
        with patch("apps.broadcasts.tasks.push_asset_to_kisvideo.delay") as mock_delay:
            response = self.client.post(
                f"/api/v1/broadcasts/channel-contents/{self.content.id}/assets/",
                {"asset_type": "video", "url": "https://example.com/x.mp4"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        mock_delay.assert_not_called()


class PushAssetToKisvideoTaskTests(_ChannelContentTestBase):
    """Covers the task-level flag re-check: if KIS_VIDEO_SERVICE_ENABLED
    is flipped off after a job is already enqueued in Redis but before a
    worker picks it up, this is the only remaining thing that can stop it
    from calling out to kisvideo — see push_asset_to_kisvideo's docstring
    and the kisvideo rollback runbook."""

    def setUp(self):
        super().setUp()
        self.asset = ChannelContentAsset.objects.create(
            content=self.content,
            asset_type="video",
            storage_path="videos/x.mp4",
            mime_type="video/mp4",
            processing_status="queued",
        )

    @override_settings(KIS_VIDEO_SERVICE_ENABLED=False)
    def test_skips_and_leaves_asset_untouched_when_flag_off_at_runtime(self):
        from apps.broadcasts.tasks import push_asset_to_kisvideo

        with patch("apps.broadcasts.kisvideo_provider.KisVideoProvider") as mock_provider_cls:
            result = push_asset_to_kisvideo(str(self.asset.id))

        self.assertEqual(result, {"status": "skipped_flag_disabled"})
        mock_provider_cls.assert_not_called()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.processing_status, "queued")


class KisVideoJobCallbackViewTests(_ChannelContentTestBase):
    def setUp(self):
        super().setUp()
        self.asset = ChannelContentAsset.objects.create(
            content=self.content,
            asset_type="video",
            storage_path="videos/x.mp4",
            mime_type="video/mp4",
            processing_status="queued",
        )
        self.content.status = ChannelContent.Status.PROCESSING
        self.content.save(update_fields=["status"])

    def _callback_url(self, *, token: str | None = None, asset_id: str | None = None) -> str:
        real_token = sign_kisvideo_callback_token(str(self.asset.id))
        return (
            "/api/v1/broadcasts/internal/kisvideo-callback/"
            f"?asset_id={asset_id if asset_id is not None else self.asset.id}"
            f"&token={token if token is not None else real_token}"
        )

    @override_settings(KIS_VIDEO_SERVICE_INTERNAL_TOKEN="test-shared-secret")
    def test_ready_callback_updates_asset_and_content(self):
        response = self.client.post(
            self._callback_url(),
            {
                "status": "ready",
                "master_playlist_url": "https://cdn.example.com/master.m3u8",
                "thumbnail_url": "https://cdn.example.com/thumb.jpg",
                "duration_seconds": 42.5,
                "renditions": [{"height": 720, "bitrate_kbps": 2800, "playlist_url": "https://cdn.example.com/720p.m3u8"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.asset.refresh_from_db()
        self.content.refresh_from_db()
        self.assertEqual(self.asset.processing_status, "ready")
        self.assertEqual(self.asset.url, "https://cdn.example.com/master.m3u8")
        self.assertEqual(self.asset.thumbnail_url, "https://cdn.example.com/thumb.jpg")
        self.assertEqual(self.asset.duration_seconds, 42)
        self.assertEqual(self.content.status, ChannelContent.Status.PUBLISHED)
        self.assertEqual(self.content.thumbnail_url, "https://cdn.example.com/thumb.jpg")

    @override_settings(KIS_VIDEO_SERVICE_INTERNAL_TOKEN="test-shared-secret")
    def test_failed_callback_marks_asset_and_content_failed(self):
        response = self.client.post(
            self._callback_url(),
            {"status": "failed", "error_message": "ffmpeg exited 1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.asset.refresh_from_db()
        self.content.refresh_from_db()
        self.assertEqual(self.asset.processing_status, "failed")
        self.assertEqual(self.content.status, ChannelContent.Status.FAILED)

    @override_settings(KIS_VIDEO_SERVICE_INTERNAL_TOKEN="test-shared-secret")
    def test_wrong_token_rejected(self):
        response = self.client.post(
            self._callback_url(token="wrong"),
            {"status": "ready", "master_playlist_url": "https://cdn.example.com/master.m3u8"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.processing_status, "queued")

    @override_settings(KIS_VIDEO_SERVICE_INTERNAL_TOKEN="test-shared-secret")
    def test_second_callback_after_ready_is_a_no_op(self):
        """A valid callback_url is single-use in effect: once resolved,
        a second call (real retry from kisvideo, or a forged/replayed
        payload from anyone who obtained the URL) cannot mutate the
        asset again — see KisVideoJobCallbackView's docstring."""
        first = self.client.post(
            self._callback_url(),
            {"status": "ready", "master_playlist_url": "https://cdn.example.com/master.m3u8"},
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)

        second = self.client.post(
            self._callback_url(),
            {"status": "ready", "master_playlist_url": "https://attacker.example.com/evil.m3u8"},
            format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual(second.data.get("status"), "already_resolved")

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.url, "https://cdn.example.com/master.m3u8")

    @override_settings(KIS_VIDEO_SERVICE_INTERNAL_TOKEN="test-shared-secret")
    def test_unknown_asset_id_rejected(self):
        import uuid

        missing_id = uuid.uuid4()
        response = self.client.post(
            self._callback_url(asset_id=missing_id, token=sign_kisvideo_callback_token(str(missing_id))),
            {"status": "ready"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(KIS_VIDEO_SERVICE_INTERNAL_TOKEN="test-shared-secret")
    def test_no_auth_header_required(self):
        """kisvideo's webhook sender adds no auth header at all (confirmed
        directly against app/workers/transcode.py::_send_webhook) — this
        endpoint must accept a plain unauthenticated POST as long as the
        query-string token is valid."""
        response = self.client.post(
            self._callback_url(),
            {"status": "ready", "master_playlist_url": "https://cdn.example.com/master.m3u8"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
