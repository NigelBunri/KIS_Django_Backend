"""
Regression tests for IntrospectView's device-bound revocation enforcement.

Previously this endpoint validated a token's signature/expiry only (via
DeviceBoundJWTAuthentication.get_validated_token/get_user, bypassing
.authenticate() entirely) and merely checked that a device_id CLAIM existed
on the token — never whether a live, non-revoked Device row still backed
it. Since this is the endpoint Nest.js calls to authorize every chat/call/
notification request, revoking a device had no effect on Nest.js access
until the token's own natural expiry. It now shares the exact same
validate_device_bound_token() check DeviceBoundJWTAuthentication itself uses.

Run:
  python3 manage.py test apps.chat.test_introspect_device_revocation --keepdb -v 2
"""
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Device
from apps.accounts.views import issue_tokens_for_user

URL = "/api/v1/auth/introspect/"
DEVICE_ID = "introspect-test-device-001"
INTERNAL_ENV = {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}


class IntrospectDeviceRevocationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone="+237699300101", country="CM", password="pass1234",
        )
        self.device = Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android", token_version=1,
        )
        self.client = APIClient()

    def _token(self) -> str:
        return issue_tokens_for_user(self.user, device_id=DEVICE_ID)["access"]

    def _introspect(self, token: str, device_id_header: str | None = None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_X_INTERNAL_AUTH": "real-token"}
        if device_id_header is not None:
            headers["HTTP_X_DEVICE_ID"] = device_id_header
        with patch.dict(os.environ, INTERNAL_ENV):
            return self.client.get(URL, {}, **headers)

    def test_valid_device_succeeds(self):
        res = self._introspect(self._token())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["id"], str(self.user.id))

    def test_revoked_device_is_rejected(self):
        token = self._token()
        self.device.revoked_at = timezone.now()
        self.device.save(update_fields=["revoked_at"])
        res = self._introspect(token)
        self.assertEqual(res.status_code, 401)

    def test_bumped_token_version_rejects_the_old_token(self):
        """The exact scenario a device revoke produces: token_version no
        longer matches what's embedded in an already-issued access token."""
        token = self._token()
        self.device.token_version = 2
        self.device.save(update_fields=["token_version"])
        res = self._introspect(token)
        self.assertEqual(res.status_code, 401)

    def test_missing_device_row_is_rejected(self):
        token = self._token()
        self.device.delete()
        res = self._introspect(token)
        self.assertEqual(res.status_code, 401)

    def test_forwarded_device_id_header_mismatch_is_rejected(self):
        res = self._introspect(self._token(), device_id_header="some-other-device")
        self.assertEqual(res.status_code, 401)

    def test_succeeds_without_a_device_id_header_forwarded(self):
        """Nest is relaying a client's token, not originating the request —
        it may have no X-Device-Id to forward. The claim alone is enough as
        long as it still resolves to a live, non-revoked device."""
        res = self._introspect(self._token(), device_id_header=None)
        self.assertEqual(res.status_code, 200)

    def test_matching_forwarded_device_id_header_succeeds(self):
        res = self._introspect(self._token(), device_id_header=DEVICE_ID)
        self.assertEqual(res.status_code, 200)

    def test_requires_internal_auth_header(self):
        token = self._token()
        res = self.client.get(URL, {}, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(res.status_code, 401)
