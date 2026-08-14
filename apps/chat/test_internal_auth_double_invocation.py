"""
Regression tests for the production incident where every genuine,
correctly-signed internal call from Nest.js to Django's introspect
endpoint was rejected with 401.

Root cause: DeviceBoundJWTAuthentication.authenticate() (a DRF
DEFAULT_AUTHENTICATION_CLASSES entry — see config.settings.base) calls
require_internal_auth(request) whenever X-Internal-Auth is present. DRF
runs authentication automatically before the view body executes. But
IntrospectView.get() ALSO calls require_internal_auth(request) explicitly.
Since verify_internal_request() consumes a single-use nonce (anti-replay),
the second, redundant call always failed as a replay of the first — so
IntrospectView unconditionally 401'd for every real internal caller. This
was invisible in dev/CI because the pre-existing test suite runs with
INTERNAL_SIGNATURE_REQUIRED=0 (no real signature/nonce is checked in that
mode), while production enforces signatures (INTERNAL_SIGNATURE_REQUIRED=1).

require_internal_auth() is now idempotent per-request (caches its verified
outcome on the request object), so multiple call sites can safely check the
same request without re-consuming its nonce.

Run:
  python3 manage.py test apps.chat.test_internal_auth_double_invocation --keepdb -v 2
"""
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from apps.accounts.models import Device
from apps.accounts.views import issue_tokens_for_user
from apps.chat.internal_auth import require_internal_auth
from apps.chat.internal_signing import sign_internal_request

URL = "/api/v1/auth/introspect/"
DEVICE_ID = "double-invoke-test-device"
SECRET = "double-invoke-real-token"
STRICT_ENV = {"DJANGO_INTERNAL_TOKEN": SECRET, "INTERNAL_SIGNATURE_REQUIRED": "1"}


class RequireInternalAuthIdempotencyTests(TestCase):
    """Unit-level: exercises require_internal_auth() directly, isolating
    it from the view/DRF-authentication plumbing that triggers the bug in
    production."""

    def setUp(self):
        self.factory = RequestFactory()

    def _signed_headers(self):
        headers = sign_internal_request("GET", "/api/v1/chat/auth/introspect/", secret=SECRET)
        return {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()}

    def test_calling_twice_on_the_same_request_succeeds_both_times(self):
        with patch.dict(os.environ, STRICT_ENV):
            request = self.factory.get("/api/v1/chat/auth/introspect/", **self._signed_headers())
            require_internal_auth(request)  # first call: verifies + consumes the nonce
            require_internal_auth(request)  # second call: must not re-verify (would replay-fail)

    def test_a_genuinely_replayed_request_is_still_rejected(self):
        """The fix must not weaken real anti-replay protection: reusing the
        same signed headers on a DIFFERENT request object (e.g. an attacker
        capturing and resending a real request) must still fail."""
        with patch.dict(os.environ, STRICT_ENV):
            wsgi_headers = self._signed_headers()
            first = self.factory.get("/api/v1/chat/auth/introspect/", **wsgi_headers)
            second = self.factory.get("/api/v1/chat/auth/introspect/", **wsgi_headers)
            require_internal_auth(first)
            with self.assertRaises(AuthenticationFailed):
                require_internal_auth(second)


class IntrospectEndToEndDoubleInvocationTests(TestCase):
    """End-to-end: reproduces the exact production path — a real signed
    request through the full DRF pipeline (DeviceBoundJWTAuthentication's
    automatic authenticate() call, then IntrospectView.get()'s own
    explicit call) with signature enforcement on, matching production."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone="+237699300202", country="CM", password="pass1234",
        )
        self.device = Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android", token_version=1,
        )
        self.client = APIClient()

    def test_a_genuine_signed_internal_call_succeeds(self):
        token = issue_tokens_for_user(self.user, device_id=DEVICE_ID)["access"]

        with patch.dict(os.environ, STRICT_ENV):
            signed = sign_internal_request("GET", URL, secret=SECRET)
            wsgi_headers = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in signed.items()}
            wsgi_headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
            response = self.client.get(URL, {}, **wsgi_headers)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], str(self.user.id))
