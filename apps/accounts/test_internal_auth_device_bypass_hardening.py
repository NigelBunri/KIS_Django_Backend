"""
Phase 7: DeviceBoundJWTAuthentication's X-Internal-Auth bypass previously
only checked that the header was PRESENT, not that its value matched
DJANGO_INTERNAL_TOKEN or carried a valid HMAC signature — any caller
holding a valid-but-otherwise-rejectable JWT (wrong device, or a REVOKED
device) could skip device-binding/revocation enforcement entirely by
sending any string in that header. It now delegates to
apps.chat.internal_auth.require_internal_auth, the same real HMAC-verified
mechanism apps/chat's introspect/conversation endpoints already use.

Uses GET /api/v1/auth/devices/ (DeviceSessionsView) as the real, live
DeviceBoundJWTAuthentication-protected endpoint under test — this is the
exact endpoint Nest.js's getDevices()/removeDevice() call this way in
production (see Nestjs src/chat/integrations/django/
django-conversation.client.ts), confirming the fix doesn't break that
legitimate flow.

Run:
  python3 manage.py test apps.accounts.test_internal_auth_device_bypass_hardening --keepdb -v 2
"""
import os
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.chat.internal_signing import sign_internal_request

from .models import Device, User
from .views import issue_tokens_for_user

REAL_DEVICE_ID = "hardening-test-real-device"
OTHER_DEVICE_ID = "hardening-test-other-device"
URL = "/api/v1/auth/devices/"
REAL_TOKEN = "a-real-strong-internal-token-for-tests"

_STRICT_ENV = {"DJANGO_INTERNAL_TOKEN": REAL_TOKEN, "INTERNAL_SIGNATURE_REQUIRED": "1"}
_LEGACY_ENV = {"DJANGO_INTERNAL_TOKEN": REAL_TOKEN, "INTERNAL_SIGNATURE_REQUIRED": "0"}


def _make_active_user(phone: str) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.verification = {"phone": {"verified": True}}
    user.status = "active"
    user.is_active = True
    user.save(update_fields=["verification", "status", "is_active"])
    return user


def _signed_headers(secret: str) -> dict[str, str]:
    signed = sign_internal_request("GET", URL, body=None, secret=secret)
    return {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in signed.items()}


@override_settings(SECURE_SSL_REDIRECT=False)
class ForgedInternalAuthHeaderNoLongerBypassesDeviceBindingTests(TestCase):
    def setUp(self):
        self.user = _make_active_user("+237699700001")
        self.device = Device.objects.create(
            user=self.user, device_id=REAL_DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.tokens = issue_tokens_for_user(self.user, device_id=REAL_DEVICE_ID)

    def _client_with_access_token(self, extra_headers=None) -> APIClient:
        client = APIClient()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.tokens['access']}"}
        headers.update(extra_headers or {})
        client.credentials(**headers)
        return client

    def test_wrong_device_id_with_no_internal_auth_header_is_rejected(self):
        # Baseline: normal device-binding enforcement, unaffected by this fix.
        client = self._client_with_access_token({"HTTP_X_DEVICE_ID": OTHER_DEVICE_ID})
        res = client.get(URL)
        self.assertEqual(res.status_code, 401)

    def test_forged_internal_auth_header_no_longer_bypasses_missing_device_header(self):
        # THE bug: previously any non-empty value here skipped device-binding
        # entirely, with no X-Device-Id at all.
        client = self._client_with_access_token({"HTTP_X_INTERNAL_AUTH": "totally-made-up-value"})
        res = client.get(URL)
        self.assertEqual(res.status_code, 401)

    def test_forged_internal_auth_header_does_not_bypass_revocation_for_a_revoked_device(self):
        # The security-critical scenario: an already-issued, not-yet-expired
        # access token for a device that has since been REVOKED must not
        # become usable again just by adding a fake internal-auth header.
        self.device.revoked_at = timezone.now()
        self.device.save(update_fields=["revoked_at"])

        client = self._client_with_access_token({
            "HTTP_X_DEVICE_ID": REAL_DEVICE_ID,
            "HTTP_X_INTERNAL_AUTH": "forged",
        })
        res = client.get(URL)
        self.assertEqual(res.status_code, 401)

    def test_normal_device_bound_request_without_internal_auth_header_still_works(self):
        client = self._client_with_access_token({"HTTP_X_DEVICE_ID": REAL_DEVICE_ID})
        res = client.get(URL)
        self.assertEqual(res.status_code, 200)

    @patch.dict(os.environ, _STRICT_ENV)
    def test_correct_secret_alone_without_a_valid_signature_is_rejected_when_signatures_required(self):
        client = self._client_with_access_token({"HTTP_X_INTERNAL_AUTH": REAL_TOKEN})
        res = client.get(URL)
        self.assertEqual(res.status_code, 401)

    @patch.dict(os.environ, _STRICT_ENV)
    def test_legitimate_signed_internal_auth_still_bypasses_device_binding(self):
        # The real, intended flow: Nest proxying a user's own JWT with a
        # genuine HMAC-signed internal request, no X-Device-Id at all —
        # exactly how django-conversation.client.ts's getDevices() calls
        # this same endpoint in production.
        client = self._client_with_access_token(_signed_headers(REAL_TOKEN))
        res = client.get(URL)
        self.assertEqual(res.status_code, 200)

    @patch.dict(os.environ, _STRICT_ENV)
    def test_legitimate_signed_internal_auth_bypasses_even_for_a_revoked_device(self):
        # By design: a genuinely-signed internal call is trusted regardless
        # of this specific device's revocation state (matches the existing,
        # already-established apps.chat introspect/require_internal_auth
        # trust model — Nest is the one enforcing session validity for its
        # own proxied device-management actions here).
        self.device.revoked_at = timezone.now()
        self.device.save(update_fields=["revoked_at"])

        client = self._client_with_access_token(_signed_headers(REAL_TOKEN))
        res = client.get(URL)
        self.assertEqual(res.status_code, 200)

    @patch.dict(os.environ, _LEGACY_ENV)
    def test_correct_bare_token_without_signature_is_allowed_in_legacy_mode(self):
        # INTERNAL_SIGNATURE_REQUIRED=0 (legacy/dev mode): the bare correct
        # token alone is enough, matching require_internal_auth's existing
        # "legacy_token_allowed" behavior elsewhere in the codebase.
        client = self._client_with_access_token({"HTTP_X_INTERNAL_AUTH": REAL_TOKEN})
        res = client.get(URL)
        self.assertEqual(res.status_code, 200)

    @patch.dict(os.environ, _LEGACY_ENV)
    def test_wrong_secret_is_still_rejected_even_in_legacy_mode(self):
        client = self._client_with_access_token({"HTTP_X_INTERNAL_AUTH": "not-the-real-token"})
        res = client.get(URL)
        self.assertEqual(res.status_code, 401)
