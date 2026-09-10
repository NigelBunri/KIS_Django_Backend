# apps/media/test_external_image_url_safety.py
"""
Closes a real structural gap found while auditing AI-moderation coverage:
Community.avatar_url and Partner.avatar_url/logo_url are plain client-
writable URLField's with no owned file behind them at all - there was
nothing for the platform to have forgotten to scan, since a client can set
these to any arbitrary external URL directly. Fetching a client-supplied
URL server-side is itself an SSRF surface, so these tests specifically
cover the guards (scheme, DNS-resolved IP, size, content-type) in addition
to the scan-decision plumbing. No real or simulated explicit content is
used anywhere here - only synthetic (label, score) tuples and mocked HTTP
responses.
"""
import socket
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.media.safety import (
    fetch_and_scan_external_image_url,
    reject_external_image_url_if_unsafe,
)


def _fake_response(*, ok=True, content_type="image/jpeg", chunks=(b"fake-bytes",)):
    resp = MagicMock()
    resp.ok = ok
    resp.headers = {"Content-Type": content_type}
    resp.iter_content.return_value = iter(chunks)
    resp.close.return_value = None
    return resp


class NonHttpValuesAreNeverFetchedTests(SimpleTestCase):
    def test_blank_value_is_a_no_op(self):
        self.assertEqual(reject_external_image_url_if_unsafe(""), "")

    def test_relative_backend_path_is_never_fetched(self):
        # This is exactly what normalize_image_payload reduces a real
        # KIS-hosted URL to - must never attempt a network fetch.
        with patch("requests.get") as mock_get:
            result = reject_external_image_url_if_unsafe("media/community/avatar/abc.jpg")
        mock_get.assert_not_called()
        self.assertEqual(result, "media/community/avatar/abc.jpg")


class SsrfGuardTests(SimpleTestCase):
    def test_http_scheme_is_rejected_without_a_fetch(self):
        with patch("requests.get") as mock_get:
            decision = fetch_and_scan_external_image_url("http://example.com/a.jpg")
        mock_get.assert_not_called()
        self.assertEqual(decision.status, "pending_review")
        self.assertEqual(decision.reason, "external_url_scheme_rejected")

    @patch("socket.getaddrinfo")
    def test_private_ip_address_is_rejected_without_a_fetch(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("10.0.0.5", 0))]
        with patch("requests.get") as mock_get:
            decision = fetch_and_scan_external_image_url("https://internal.example.com/a.jpg")
        mock_get.assert_not_called()
        self.assertEqual(decision.reason, "external_url_private_address_rejected")

    @patch("socket.getaddrinfo")
    def test_loopback_address_is_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 0))]
        decision = fetch_and_scan_external_image_url("https://localhost.example.com/a.jpg")
        self.assertEqual(decision.reason, "external_url_private_address_rejected")

    @patch("socket.getaddrinfo")
    def test_cloud_metadata_link_local_address_is_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("169.254.169.254", 0))]
        decision = fetch_and_scan_external_image_url("https://metadata.example.com/a.jpg")
        self.assertEqual(decision.reason, "external_url_private_address_rejected")

    @patch("socket.getaddrinfo", side_effect=socket.gaierror("dns failure"))
    def test_unresolvable_hostname_is_rejected(self, mock_getaddrinfo):
        decision = fetch_and_scan_external_image_url("https://does-not-exist.invalid/a.jpg")
        self.assertEqual(decision.reason, "external_url_private_address_rejected")


@override_settings(MEDIA_SAFETY_SERVICE_ENABLED=True)
class FetchAndScanDecisionTests(SimpleTestCase):
    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
    @patch("apps.media.content_safety_provider.ContentSafetyProvider.scan")
    @patch("requests.get")
    def test_clearly_safe_external_image_passes(self, mock_get, mock_scan, mock_dns):
        mock_get.return_value = _fake_response()
        mock_scan.return_value = (None, 0.0)

        result = reject_external_image_url_if_unsafe("https://cdn.example.com/photo.jpg")

        self.assertEqual(result, "https://cdn.example.com/photo.jpg")

    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
    @patch("apps.media.content_safety_provider.ContentSafetyProvider.scan")
    @patch("requests.get")
    def test_clearly_prohibited_external_image_is_rejected(self, mock_get, mock_scan, mock_dns):
        mock_get.return_value = _fake_response()
        mock_scan.return_value = ("FEMALE_GENITALIA_EXPOSED", 0.95)

        with self.assertRaises(ValidationError):
            reject_external_image_url_if_unsafe("https://cdn.example.com/photo.jpg")

    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
    @patch("apps.media.content_safety_provider.ContentSafetyProvider.scan")
    @patch("requests.get")
    def test_ambiguous_external_image_is_rejected_not_saved_unreviewed(self, mock_get, mock_scan, mock_dns):
        # Unlike an upload with a quarantine slot, avatar_url has no
        # "pending" storage and is default publicly visible - anything
        # short of a clean pass must be rejected outright.
        mock_get.return_value = _fake_response()
        mock_scan.return_value = ("FEMALE_GENITALIA_EXPOSED", 0.3)

        with self.assertRaises(ValidationError):
            reject_external_image_url_if_unsafe("https://cdn.example.com/photo.jpg")

    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
    @patch("requests.get")
    def test_non_image_content_type_is_rejected(self, mock_get, mock_dns):
        mock_get.return_value = _fake_response(content_type="text/html")
        decision = fetch_and_scan_external_image_url("https://cdn.example.com/page.html")
        self.assertEqual(decision.reason, "external_url_not_an_image")

    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
    @patch("requests.get")
    def test_oversized_response_is_rejected(self, mock_get, mock_dns):
        big_chunk = b"x" * (16 * 1024 * 1024)
        mock_get.return_value = _fake_response(chunks=(big_chunk,))
        decision = fetch_and_scan_external_image_url("https://cdn.example.com/huge.jpg")
        self.assertEqual(decision.reason, "external_url_too_large")

    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
    @patch("requests.get")
    def test_fetch_failure_fails_closed(self, mock_get, mock_dns):
        mock_get.side_effect = Exception("connection refused")
        decision = fetch_and_scan_external_image_url("https://cdn.example.com/photo.jpg")
        self.assertEqual(decision.status, "pending_review")
        self.assertEqual(decision.reason, "external_url_fetch_failed")

    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
    @patch("apps.media.content_safety_provider.ContentSafetyProvider.scan", side_effect=Exception("boom"))
    @patch("requests.get")
    def test_scan_service_failure_fails_closed(self, mock_get, mock_scan, mock_dns):
        mock_get.return_value = _fake_response()
        decision = fetch_and_scan_external_image_url("https://cdn.example.com/photo.jpg")
        self.assertEqual(decision.status, "pending_review")


@override_settings(MEDIA_SAFETY_SERVICE_ENABLED=False)
class StubModeTests(SimpleTestCase):
    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
    @patch("requests.get")
    def test_falls_back_to_the_same_stub_path_every_other_call_site_uses(self, mock_get, mock_dns):
        mock_get.return_value = _fake_response()
        with patch("apps.media.safety.scan_upload_for_explicit_content") as mock_stub:
            from apps.media.safety import MediaSafetyDecision

            mock_stub.return_value = MediaSafetyDecision(
                status="not_configured", quarantine=False, provider="stub",
                reason="stub_provider_no_scanning", user_message="Upload accepted.", requires_review=False,
            )
            result = reject_external_image_url_if_unsafe("https://cdn.example.com/photo.jpg")

        self.assertEqual(result, "https://cdn.example.com/photo.jpg")
