"""
LinkPreviewView makes a server-side GET to whatever URL an authenticated
caller supplies. Without restricting it to public internet hosts, any
authenticated user could use it to probe or read from internal-only
services and the cloud metadata endpoint (169.254.169.254). See the SSRF
guard comment in views.py.

Run:
  python3 manage.py test apps.core.test_link_preview_ssrf_guard --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()

URL = "/api/v1/link-preview/"


class LinkPreviewSsrfGuardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+2348500000001", password="pw123456", country="NG")
        self.client.force_authenticate(self.user)

    def test_unauthenticated_request_is_rejected(self):
        self.client.force_authenticate(None)
        res = self.client.get(URL, {"url": "https://example.com"})
        self.assertEqual(res.status_code, 401)

    def test_missing_url_is_a_400(self):
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 400)

    def test_cloud_metadata_endpoint_is_blocked(self):
        res = self.client.get(URL, {"url": "http://169.254.169.254/latest/meta-data/"})
        self.assertEqual(res.status_code, 400)

    def test_loopback_target_is_blocked(self):
        res = self.client.get(URL, {"url": "http://127.0.0.1:8000/internal"})
        self.assertEqual(res.status_code, 400)

    def test_private_network_target_is_blocked(self):
        res = self.client.get(URL, {"url": "http://10.0.0.5/admin"})
        self.assertEqual(res.status_code, 400)

    def test_disallowed_scheme_is_blocked(self):
        res = self.client.get(URL, {"url": "file:///etc/passwd"})
        self.assertEqual(res.status_code, 400)
