"""
KIS Auth bridge tests. Mocks apps.kis_auth_bridge.exchange_client's
redeem_authorization_code() — the real cross-service HMAC signing, HTTP
call, and JWT verification against a real kis-auth server were proven
manually against a live instance while building this (real Postgres,
real Redis, real Django test user, real device-promotion transaction,
including a genuine replay-rejection check) rather than assumed; these
tests cover the Django-side logic in isolation: purpose binding, user
resolution, the device-promotion transaction, and error shaping.

Run:
  python3 manage.py test apps.kis_auth_bridge --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import Device, User
from apps.kis_auth_bridge.exchange_client import ExchangeError, VerifiedAuthorization

URL = "/api/v1/kis-auth/recovery/complete/"


def _verified(**overrides):
    defaults = dict(
        kis_user_id="",
        purpose="recovery",
        auth_identity_id="identity-1",
        provider_email="person@example.com",
        provider_email_verified=True,
    )
    defaults.update(overrides)
    return VerifiedAuthorization(**defaults)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    KIS_AUTH_ENABLED=True,
    KIS_AUTH_RECOVERY_ENABLED=True,
)
class KisAuthRecoveryCompleteTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.user = User.objects.create(phone="+237600000001", username="recovery_test_user", status="active", is_active=True)
        self.old_device = Device.objects.create(
            user=self.user,
            device_id="old-parent-device",
            is_parent=True,
            token_version=1,
        )
        self.body = {
            "authorization_code": "some-code",
            "redirect_uri": "https://kis.app/auth/callback",
            "device_id": "new-recovery-device",
            "device_name": "New Phone",
            "platform": "ios",
        }

    def _post(self, **overrides):
        body = {**self.body, **overrides}
        return self.client_api.post(URL, body, format="json")

    def test_rejects_missing_fields_without_calling_exchange(self):
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code") as mock_redeem:
            resp = self._post(authorization_code="")
            self.assertEqual(resp.status_code, 400)
            mock_redeem.assert_not_called()

    def test_generic_error_when_exchange_fails(self):
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", side_effect=ExchangeError("whatever")):
            resp = self._post()
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["detail"], "We could not complete this authentication request.")

    def test_rejects_a_non_recovery_purpose_even_if_exchange_succeeded(self):
        verified = _verified(kis_user_id=str(self.user.id), purpose="registration")
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            resp = self._post()
            self.assertEqual(resp.status_code, 400)
        # No device change should have happened — purpose binding fails closed.
        self.old_device.refresh_from_db()
        self.assertTrue(self.old_device.is_parent)

    def test_rejects_an_unknown_kis_user_id_with_the_same_generic_message(self):
        import uuid
        verified = _verified(kis_user_id=str(uuid.uuid4()))
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            resp = self._post()
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["detail"], "We could not complete this authentication request.")

    def test_rejects_recovery_for_an_inactive_user(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        verified = _verified(kis_user_id=str(self.user.id))
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            resp = self._post()
            self.assertEqual(resp.status_code, 400)

    def test_successful_recovery_promotes_new_device_and_revokes_old_parent(self):
        verified = _verified(kis_user_id=str(self.user.id))
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            resp = self._post()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("access", body)
        self.assertIn("refresh", body)
        self.assertEqual(body["user"]["id"], str(self.user.id))

        self.old_device.refresh_from_db()
        self.assertFalse(self.old_device.is_parent)
        self.assertIsNotNone(self.old_device.revoked_at)

        new_device = Device.objects.get(user=self.user, device_id="new-recovery-device")
        self.assertTrue(new_device.is_parent)
        self.assertIsNone(new_device.revoked_at)

    def test_successful_recovery_writes_an_audit_log_entry(self):
        from apps.accounts.models import AuditLog

        verified = _verified(kis_user_id=str(self.user.id))
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            self._post()

        entry = AuditLog.objects.filter(actor_id=self.user.id, action="device.kis_auth_recovery").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("new_parent_device_id"), "new-recovery-device")

    def test_recovering_onto_an_already_registered_device_does_not_duplicate_it(self):
        # Recovering back onto a device that was already registered (e.g.
        # previously revoked) should update it in place, not create a
        # second Device row for the same (user, device_id).
        Device.objects.create(user=self.user, device_id="new-recovery-device", is_parent=False, revoked_at=self.old_device.created_at)
        verified = _verified(kis_user_id=str(self.user.id))
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Device.objects.filter(user=self.user, device_id="new-recovery-device").count(), 1)

    def test_does_not_touch_a_different_users_devices(self):
        other_user = User.objects.create(phone="+237600000002", username="unrelated_user", status="active", is_active=True)
        other_device = Device.objects.create(user=other_user, device_id="unrelated-device", is_parent=True, token_version=1)

        verified = _verified(kis_user_id=str(self.user.id))
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            self._post()

        other_device.refresh_from_db()
        self.assertTrue(other_device.is_parent)
        self.assertIsNone(other_device.revoked_at)
