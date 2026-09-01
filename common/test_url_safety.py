"""
is_safe_external_url is the shared SSRF guard used everywhere the server
makes an outbound request to a URL a client influenced (WHIP ingest,
link previews, partner/website webhooks). See url_safety.py's module
docstring for why this exists.

Run:
  python3 manage.py test common.test_url_safety --keepdb -v 2
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from common.url_safety import is_safe_external_url


class UrlSafetyTests(SimpleTestCase):
    def test_public_https_url_is_safe(self):
        self.assertTrue(is_safe_external_url("https://example.com/webhook"))

    def test_public_http_url_is_safe(self):
        self.assertTrue(is_safe_external_url("http://example.com/webhook"))

    def test_empty_or_non_string_is_unsafe(self):
        self.assertFalse(is_safe_external_url(""))
        self.assertFalse(is_safe_external_url(None))

    def test_disallowed_scheme_is_unsafe(self):
        self.assertFalse(is_safe_external_url("file:///etc/passwd"))
        self.assertFalse(is_safe_external_url("ftp://example.com/x"))

    def test_localhost_hostname_is_unsafe(self):
        self.assertFalse(is_safe_external_url("http://localhost/admin"))
        self.assertFalse(is_safe_external_url("http://localhost.localdomain/"))

    def test_google_metadata_hostname_is_unsafe(self):
        self.assertFalse(is_safe_external_url("http://metadata.google.internal/computeMetadata/v1/"))

    def test_cloud_metadata_literal_ip_is_unsafe(self):
        # 169.254.169.254 - the AWS/GCP/Azure instance metadata endpoint.
        self.assertFalse(is_safe_external_url("http://169.254.169.254/latest/meta-data/"))

    def test_loopback_literal_ip_is_unsafe(self):
        self.assertFalse(is_safe_external_url("http://127.0.0.1:8000/internal"))

    def test_private_range_literal_ips_are_unsafe(self):
        for ip in ("10.0.0.1", "172.16.0.5", "192.168.1.1"):
            self.assertFalse(is_safe_external_url(f"http://{ip}/"), f"{ip} should be unsafe")

    def test_public_literal_ip_is_safe(self):
        self.assertTrue(is_safe_external_url("http://8.8.8.8/"))

    def test_dns_resolving_to_a_private_address_is_unsafe(self):
        with patch("common.url_safety.socket.getaddrinfo") as mock_resolve:
            mock_resolve.return_value = [(2, 1, 6, "", ("10.0.0.5", 0))]
            self.assertFalse(is_safe_external_url("http://internal.example.com/"))

    def test_dns_rebinding_any_unsafe_address_among_several_fails_closed(self):
        # A hostname that resolves to BOTH a public and a private address -
        # every resolved address must be safe, not just the first one,
        # since the caller has no control over which the HTTP client picks.
        with patch("common.url_safety.socket.getaddrinfo") as mock_resolve:
            mock_resolve.return_value = [
                (2, 1, 6, "", ("8.8.8.8", 0)),
                (2, 1, 6, "", ("169.254.169.254", 0)),
            ]
            self.assertFalse(is_safe_external_url("http://rebinding.example.com/"))

    def test_dns_resolution_failure_fails_closed(self):
        with patch("common.url_safety.socket.getaddrinfo", side_effect=OSError("no such host")):
            self.assertFalse(is_safe_external_url("http://does-not-resolve.example.com/"))

    def test_unparseable_url_is_unsafe(self):
        self.assertFalse(is_safe_external_url("http://[::1"))
