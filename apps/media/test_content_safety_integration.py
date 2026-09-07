from __future__ import annotations

import io
import tempfile
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.media.content_safety_provider import (
    ContentSafetyProvider,
    ContentSafetyProviderError,
    content_safety_service_enabled,
)
from apps.media.safety import run_nudenet_scan_on_file


class ContentSafetyServiceEnabledTests(SimpleTestCase):
    @override_settings(MEDIA_SAFETY_SERVICE_ENABLED=False)
    def test_defaults_off(self):
        self.assertFalse(content_safety_service_enabled())

    @override_settings(MEDIA_SAFETY_SERVICE_ENABLED=True)
    def test_reads_flag(self):
        self.assertTrue(content_safety_service_enabled())


@override_settings(MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN="", MEDIA_SAFETY_SERVICE_BASE_URL="")
class ContentSafetyProviderCredentialTests(SimpleTestCase):
    def test_raises_without_credentials(self):
        with self.assertRaises(ContentSafetyProviderError):
            ContentSafetyProvider().scan(io.BytesIO(b"x"), filename="x.jpg", content_type="image/jpeg")


@override_settings(
    MEDIA_SAFETY_SERVICE_ENABLED=True,
    MEDIA_SAFETY_SERVICE_BASE_URL="https://content-safety.internal",
    MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN="test-shared-secret",
)
class ContentSafetyProviderScanTests(SimpleTestCase):
    @patch("apps.media.content_safety_provider._requests")
    def test_scan_image_returns_label_and_score(self, mock_requests):
        mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": "BUTTOCKS_EXPOSED", "score": 0.91})
        label, score = ContentSafetyProvider().scan(io.BytesIO(b"fake"), filename="x.jpg", content_type="image/jpeg")
        self.assertEqual((label, score), ("BUTTOCKS_EXPOSED", 0.91))
        kwargs = mock_requests.post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["X-Internal-Auth"], "test-shared-secret")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertIn("/scan/image", mock_requests.post.call_args.args[0])

    @patch("apps.media.content_safety_provider._requests")
    def test_scan_video_routes_and_uses_long_timeout(self, mock_requests):
        mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": "FEMALE_BREAST_EXPOSED", "score": 0.8})
        label, score = ContentSafetyProvider().scan(io.BytesIO(b"fake-video"), filename="x.mp4", content_type="video/mp4")
        self.assertEqual((label, score), ("FEMALE_BREAST_EXPOSED", 0.8))
        kwargs = mock_requests.post.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 300)
        self.assertIn("/scan/video", mock_requests.post.call_args.args[0])

    @patch("apps.media.content_safety_provider._requests")
    def test_scan_clean_result(self, mock_requests):
        mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": None, "score": 0.0})
        label, score = ContentSafetyProvider().scan(io.BytesIO(b"fake"), filename="x.jpg", content_type="image/jpeg")
        self.assertEqual((label, score), (None, 0.0))

    @patch("apps.media.content_safety_provider._requests")
    def test_raises_on_non_ok_response(self, mock_requests):
        mock_requests.post.return_value = MagicMock(ok=False, status_code=500, text="boom")
        with self.assertRaises(ContentSafetyProviderError):
            ContentSafetyProvider().scan(io.BytesIO(b"fake"), filename="x.jpg", content_type="image/jpeg")

    @patch("apps.media.content_safety_provider._requests")
    def test_network_failure_raises_provider_error(self, mock_requests):
        import requests

        mock_requests.post.side_effect = requests.exceptions.ConnectionError("reset")
        with self.assertRaises(ContentSafetyProviderError):
            ContentSafetyProvider().scan(io.BytesIO(b"fake"), filename="x.jpg", content_type="image/jpeg")


class RunNudenetScanOnFileRoutingTests(SimpleTestCase):
    """run_nudenet_scan_on_file is the one function every existing caller
    already goes through (scan_upload_for_explicit_content only reaches it
    when a real file_path is supplied) — confirming the flag routes it to
    the new service (or not) here covers every one of those callers, not
    just a new one."""

    @override_settings(MEDIA_SAFETY_SERVICE_ENABLED=False)
    @patch("apps.media.safety._scan_image_file", return_value=("BUTTOCKS_EXPOSED", 0.9))
    def test_flag_off_uses_in_process_scan(self, mock_scan):
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            decision = run_nudenet_scan_on_file(tmp.name, "image/jpeg")
        mock_scan.assert_called_once_with(tmp.name)
        self.assertEqual(decision.status, "blocked")
        self.assertEqual(decision.reason, "nudenet_explicit:BUTTOCKS_EXPOSED")

    @override_settings(
        MEDIA_SAFETY_SERVICE_ENABLED=True,
        MEDIA_SAFETY_SERVICE_BASE_URL="https://content-safety.internal",
        MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN="test-shared-secret",
    )
    @patch("apps.media.safety._scan_image_file")
    @patch("apps.media.content_safety_provider._requests")
    def test_flag_on_uses_new_service_not_in_process_scan(self, mock_requests, mock_local_scan):
        mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": "BUTTOCKS_EXPOSED", "score": 0.9})
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            decision = run_nudenet_scan_on_file(tmp.name, "image/jpeg")
        mock_local_scan.assert_not_called()
        self.assertEqual(decision.status, "blocked")
        self.assertEqual(decision.reason, "nudenet_explicit:BUTTOCKS_EXPOSED")

    @override_settings(
        MEDIA_SAFETY_SERVICE_ENABLED=True,
        MEDIA_SAFETY_SERVICE_BASE_URL="https://content-safety.internal",
        MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN="test-shared-secret",
    )
    @patch("apps.media.content_safety_provider._requests")
    def test_flag_on_service_failure_fails_closed(self, mock_requests):
        mock_requests.post.return_value = MagicMock(ok=False, status_code=500, text="boom")
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            decision = run_nudenet_scan_on_file(tmp.name, "image/jpeg")
        self.assertEqual(decision.status, "pending_review")
        self.assertTrue(decision.quarantine)
        self.assertTrue(decision.reason.startswith("nudenet_scan_error:"))
