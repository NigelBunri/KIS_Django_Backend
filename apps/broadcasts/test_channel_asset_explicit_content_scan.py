# apps/broadcasts/test_channel_asset_explicit_content_scan.py
"""
Closes a real gap found while auditing AI-moderation coverage: channel
content assets (KISTube-style video/image posts) have no MediaUploadIntent/
MediaAsset behind them - storage_path/url come straight from whatever the
client posts. The pipeline's own existing safety gates
(attachment_requires_safety_review / validate_asset_ready_for_publish)
already gated on processing_status, but nothing ever set that status from
a REAL scan - only a client's own self-reported status was ever checked,
which an honest client that simply omits the field bypassed entirely.

These tests mock the scan boundary directly (ContentSafetyProvider.scan /
scan_upload_for_explicit_content) with canned verdicts - no real or
simulated explicit content is used anywhere here.
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.media.safety import MediaSafetyDecision

from .media_pipeline import scan_channel_asset_payload_for_explicit_content


def _payload(**overrides):
    base = {
        "asset_type": "image",
        "storage_path": "private/channel/content/abc/photo.jpg",
        "mime_type": "image/jpeg",
        "processing_status": "ready",
        "metadata": {},
    }
    base.update(overrides)
    return base


class NonScannableAssetTypesTests(SimpleTestCase):
    def test_document_asset_is_never_scanned(self):
        payload = _payload(asset_type="document", storage_path="private/channel/content/abc/report.pdf")
        result = scan_channel_asset_payload_for_explicit_content(dict(payload))
        self.assertEqual(result, payload)

    def test_missing_storage_path_is_a_no_op(self):
        payload = _payload(storage_path="")
        result = scan_channel_asset_payload_for_explicit_content(dict(payload))
        self.assertEqual(result["storage_path"], "")


@override_settings(MEDIA_SAFETY_SERVICE_ENABLED=True)
class LiveServiceScanTests(SimpleTestCase):
    @patch("apps.media.content_safety_provider.ContentSafetyProvider.scan")
    @patch("django.core.files.storage.default_storage.open")
    def test_clearly_safe_image_is_marked_ready(self, mock_open, mock_scan):
        mock_open.return_value.__enter__.return_value = MagicMock()
        mock_scan.return_value = (None, 0.0)

        result = scan_channel_asset_payload_for_explicit_content(_payload())

        self.assertEqual(result["processing_status"], "ready")
        self.assertEqual(result["metadata"]["explicit_content_scan"]["status"], "passed")

    @patch("apps.media.content_safety_provider.ContentSafetyProvider.scan")
    @patch("django.core.files.storage.default_storage.open")
    def test_clearly_prohibited_image_is_rejected_before_persisting(self, mock_open, mock_scan):
        mock_open.return_value.__enter__.return_value = MagicMock()
        mock_scan.return_value = ("FEMALE_GENITALIA_EXPOSED", 0.95)

        with self.assertRaises(ValidationError):
            scan_channel_asset_payload_for_explicit_content(_payload())

    @patch("apps.media.content_safety_provider.ContentSafetyProvider.scan")
    @patch("django.core.files.storage.default_storage.open")
    def test_ambiguous_video_is_marked_pending_review_not_rejected(self, mock_open, mock_scan):
        mock_open.return_value.__enter__.return_value = MagicMock()
        mock_scan.return_value = ("FEMALE_GENITALIA_EXPOSED", 0.3)

        result = scan_channel_asset_payload_for_explicit_content(
            _payload(asset_type="short_video", storage_path="private/channel/content/abc/clip.mp4", mime_type="video/mp4"),
        )

        self.assertEqual(result["processing_status"], "pending_review")

    @patch(
        "apps.media.content_safety_provider.ContentSafetyProvider.scan",
        side_effect=Exception("service unreachable"),
    )
    @patch("django.core.files.storage.default_storage.open")
    def test_service_failure_fails_closed_to_pending_review_not_silent_pass(self, mock_open, mock_scan):
        mock_open.return_value.__enter__.return_value = MagicMock()

        result = scan_channel_asset_payload_for_explicit_content(_payload())

        self.assertEqual(result["processing_status"], "pending_review")

    @patch("django.core.files.storage.default_storage.open", side_effect=FileNotFoundError("no such object"))
    def test_storage_path_that_does_not_resolve_to_a_real_object_fails_closed(self, mock_open):
        # A client-supplied storage_path is never trusted merely because
        # it looks well-formed - if it doesn't resolve to something this
        # scan can actually read, that is a failure to verify, not a pass.
        result = scan_channel_asset_payload_for_explicit_content(_payload())
        self.assertEqual(result["processing_status"], "pending_review")


@override_settings(MEDIA_SAFETY_SERVICE_ENABLED=False)
class StubModeTests(SimpleTestCase):
    def test_falls_back_to_the_same_stub_path_every_other_call_site_uses(self):
        with patch("apps.media.safety.scan_upload_for_explicit_content") as mock_stub:
            mock_stub.return_value = MediaSafetyDecision(
                status="not_configured", quarantine=False, provider="stub",
                reason="stub_provider_no_scanning", user_message="Upload accepted.", requires_review=False,
            )
            result = scan_channel_asset_payload_for_explicit_content(_payload())

        self.assertEqual(result["processing_status"], "ready")
