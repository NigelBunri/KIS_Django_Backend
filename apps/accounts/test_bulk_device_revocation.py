"""
Regression tests for RevokeAllSecondaryView using the same canonical
revoke_device_session() path as single-device revoke/logout.

Previously this endpoint did a bare `.update(revoked_at=..., revoke_reason=...)`
— it never bumped token_version and never deleted E2EE key material, unlike
the single-device revoke path. Since DeviceBoundJWTAuthentication only
rejects a token once its embedded token_version claim stops matching the
live Device row, an already-issued access token for a "bulk revoked"
secondary device stayed valid until it naturally expired.

Run:
  python3 manage.py test apps.accounts.test_bulk_device_revocation --keepdb -v 2
"""
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AuditLog, Device, E2EDeviceKey, E2EPreKey, User
from .views import issue_tokens_for_user

PARENT_DEVICE_ID = "bulk-revoke-parent-001"
SECONDARY_DEVICE_ID = "bulk-revoke-secondary-001"
SECONDARY_DEVICE_ID_2 = "bulk-revoke-secondary-002"


def _auth_client(user: User, device_id: str) -> APIClient:
    tokens = issue_tokens_for_user(user, device_id=device_id)
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {tokens['access']}",
        HTTP_X_DEVICE_ID=device_id,
    )
    return client


@override_settings(SECURE_SSL_REDIRECT=False)
class BulkDeviceRevocationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+237699100101", country="CM", password="pass1234",
        )
        self.user.verification = {"phone": {"verified": True}}
        self.user.status = "active"
        self.user.is_active = True
        self.user.save(update_fields=["verification", "status", "is_active"])

        self.parent = Device.objects.create(
            user=self.user, device_id=PARENT_DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.secondary1 = Device.objects.create(
            user=self.user, device_id=SECONDARY_DEVICE_ID, platform="ios",
            is_parent=False, linked_via_qr=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.secondary2 = Device.objects.create(
            user=self.user, device_id=SECONDARY_DEVICE_ID_2, platform="web",
            is_parent=False, linked_via_qr=True, token_version=1, last_seen_at=timezone.now(),
        )
        for device in (self.secondary1, self.secondary2):
            E2EDeviceKey.objects.create(
                user=self.user, device=device, identity_key="ik", signed_prekey_id=1,
                signed_prekey="spk", signed_prekey_signature="sig",
            )
            E2EPreKey.objects.create(user=self.user, device=device, prekey_id=1, prekey="pk")

        self.parent_client = _auth_client(self.user, PARENT_DEVICE_ID)

    def test_bumps_token_version_for_every_secondary_device(self):
        res = self.parent_client.delete("/api/v1/auth/devices/revoke-all-secondary/")
        self.assertEqual(res.status_code, 200)
        self.secondary1.refresh_from_db()
        self.secondary2.refresh_from_db()
        self.assertEqual(self.secondary1.token_version, 2)
        self.assertEqual(self.secondary2.token_version, 2)
        self.assertIsNotNone(self.secondary1.revoked_at)
        self.assertIsNotNone(self.secondary2.revoked_at)

    def test_deletes_e2ee_key_material_for_every_secondary_device(self):
        self.parent_client.delete("/api/v1/auth/devices/revoke-all-secondary/")
        self.assertFalse(E2EDeviceKey.objects.filter(device=self.secondary1).exists())
        self.assertFalse(E2EDeviceKey.objects.filter(device=self.secondary2).exists())
        self.assertFalse(E2EPreKey.objects.filter(device=self.secondary1).exists())
        self.assertFalse(E2EPreKey.objects.filter(device=self.secondary2).exists())

    def test_parent_device_is_not_touched(self):
        self.parent_client.delete("/api/v1/auth/devices/revoke-all-secondary/")
        self.parent.refresh_from_db()
        self.assertIsNone(self.parent.revoked_at)
        self.assertEqual(self.parent.token_version, 1)

    def test_creates_one_security_audit_event_per_revoked_device(self):
        self.parent_client.delete("/api/v1/auth/devices/revoke-all-secondary/")
        events = AuditLog.objects.filter(action="security.device.revoked", actor_id=self.user.id)
        self.assertEqual(events.count(), 2)
        revoked_device_ids = sorted(e.meta.get("device_id") for e in events)
        self.assertEqual(revoked_device_ids, sorted([SECONDARY_DEVICE_ID, SECONDARY_DEVICE_ID_2]))

    def test_creates_summary_audit_event(self):
        res = self.parent_client.delete("/api/v1/auth/devices/revoke-all-secondary/")
        self.assertEqual(res.data["revoked_count"], 2)
        summary = AuditLog.objects.filter(action="device.revoke_all_secondary", actor_id=self.user.id).first()
        self.assertIsNotNone(summary)
        self.assertEqual(summary.meta["revoked_count"], 2)
        self.assertEqual(sorted(summary.meta["revoked_device_ids"]), sorted([SECONDARY_DEVICE_ID, SECONDARY_DEVICE_ID_2]))

    def test_non_parent_device_cannot_bulk_revoke(self):
        secondary_client = _auth_client(self.user, SECONDARY_DEVICE_ID)
        res = secondary_client.delete("/api/v1/auth/devices/revoke-all-secondary/")
        self.assertEqual(res.status_code, 403)
        self.secondary2.refresh_from_db()
        self.assertIsNone(self.secondary2.revoked_at)

    def test_a_token_already_issued_to_a_bulk_revoked_device_is_rejected_afterward(self):
        """End-to-end proof that the fix actually invalidates live access —
        not just database flags. A token minted for secondary1 BEFORE the
        bulk revoke must be rejected on its NEXT authenticated request."""
        secondary_client = _auth_client(self.user, SECONDARY_DEVICE_ID)

        self.parent_client.delete("/api/v1/auth/devices/revoke-all-secondary/")

        res = secondary_client.get("/api/v1/auth/devices/")
        self.assertEqual(res.status_code, 401)
