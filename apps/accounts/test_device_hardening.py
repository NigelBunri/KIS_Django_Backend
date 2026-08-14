"""
Regression tests for the device/auth production-hardening pass:
  - DB-level invariant: at most one active parent device per user
  - DB-level invariant: no duplicate (user, device_id) rows
  - upsert_device() promotes only the first active device, normalizes
    device_id whitespace
  - DeviceQRToken.consume() cannot be double-consumed by a concurrent request
  - Password change revokes every OTHER device, leaves the current one alone
  - Password reset revokes EVERY device (including the one used to reset)
  - Parent-device recovery revokes the old parent through the same
    token_version-bump + E2EE-wipe path as every other revocation, and is
    gated on a verified email
  - The new email_verify OTP purpose sets User.email_verified

Run:
  python3 manage.py test apps.accounts.test_device_hardening --keepdb -v 2
"""
import threading
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.otp.models import PhoneOTP
from apps.otp.views import make_code_hash

from .models import Device, DeviceQRToken, E2EDeviceKey
from .tests_qa_full import make_verified_user, auth_client
from .views import upsert_device, issue_tokens_for_user


@override_settings(SECURE_SSL_REDIRECT=False)
class DeviceConstraintTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700000101")

    def test_one_active_parent_per_user_enforced_at_db_level(self):
        Device.objects.create(user=self.user, device_id="dev-1", platform="ios", is_parent=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Device.objects.create(user=self.user, device_id="dev-2", platform="ios", is_parent=True)

    def test_revoked_parent_does_not_block_a_new_active_parent(self):
        Device.objects.create(
            user=self.user, device_id="dev-1", platform="ios",
            is_parent=True, revoked_at=timezone.now(),
        )
        # Must not raise — the revoked row falls outside the partial index.
        Device.objects.create(user=self.user, device_id="dev-2", platform="ios", is_parent=True)

    def test_duplicate_user_device_id_rejected(self):
        Device.objects.create(user=self.user, device_id="dup-id", platform="ios")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Device.objects.create(user=self.user, device_id="dup-id", platform="android")


@override_settings(SECURE_SSL_REDIRECT=False)
class UpsertDeviceTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700000102")

    def test_first_device_becomes_parent(self):
        device = upsert_device(self.user, "dev-1", "ios", None, None)
        self.assertTrue(device.is_parent)

    def test_second_device_does_not_become_parent(self):
        upsert_device(self.user, "dev-1", "ios", None, None)
        device2 = upsert_device(self.user, "dev-2", "android", None, None)
        self.assertFalse(device2.is_parent)
        self.assertEqual(
            Device.objects.filter(user=self.user, is_parent=True, revoked_at__isnull=True).count(),
            1,
        )

    def test_whitespace_around_device_id_resolves_to_the_same_device(self):
        upsert_device(self.user, "  dev-1  ", "ios", None, None)
        upsert_device(self.user, "dev-1", "ios", None, None)
        self.assertEqual(Device.objects.filter(user=self.user, device_id="dev-1").count(), 1)


class QRTokenDoubleConsumptionTests(TransactionTestCase):
    """Uses TransactionTestCase (real commits, real Postgres row locking)
    since the regular TestCase wraps each test in one outer transaction,
    which would make a two-thread race trivially "safe" for the wrong
    reason (both threads sharing one connection/transaction)."""

    def setUp(self):
        self.user = make_verified_user("+237700000103")
        self.parent = Device.objects.create(
            user=self.user, device_id="dev-parent", platform="ios",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )

    def test_sequential_reuse_is_rejected(self):
        _, token_plain = DeviceQRToken.generate_for_device(self.user, self.parent)
        first = DeviceQRToken.consume(token_plain)
        second = DeviceQRToken.consume(token_plain)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_concurrent_consume_only_succeeds_once(self):
        _, token_plain = DeviceQRToken.generate_for_device(self.user, self.parent)
        results = []

        def worker():
            try:
                results.append(DeviceQRToken.consume(token_plain))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 1, "exactly one concurrent consume() should win")


@override_settings(SECURE_SSL_REDIRECT=False)
class PasswordChangeDeviceInvalidationTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700000104")
        self.client_a = auth_client(self.user, device_id="dev-a")  # becomes parent
        self.dev_b = Device.objects.create(
            user=self.user, device_id="dev-b", platform="android",
            is_parent=False, token_version=1, last_seen_at=timezone.now(),
        )

    def test_current_device_stays_valid_other_device_is_revoked(self):
        res = self.client_a.post("/api/v1/auth/password/change/", {
            "current_password": "TestPass12!",
            "new_password": "NewTestPass12!",
        }, format="json")
        self.assertEqual(res.status_code, 200, res.data)

        dev_a = Device.objects.get(user=self.user, device_id="dev-a")
        dev_b = Device.objects.get(user=self.user, device_id="dev-b")
        self.assertIsNone(dev_a.revoked_at)
        self.assertIsNotNone(dev_b.revoked_at)
        self.assertEqual(dev_b.token_version, 2)

    def test_other_devices_e2ee_keys_are_wiped_current_devices_are_not(self):
        dev_a = Device.objects.get(user=self.user, device_id="dev-a")
        E2EDeviceKey.objects.create(
            user=self.user, device=dev_a, identity_key="x",
            signed_prekey_id=1, signed_prekey="y", signed_prekey_signature="z",
        )
        E2EDeviceKey.objects.create(
            user=self.user, device=self.dev_b, identity_key="x",
            signed_prekey_id=1, signed_prekey="y", signed_prekey_signature="z",
        )

        self.client_a.post("/api/v1/auth/password/change/", {
            "current_password": "TestPass12!",
            "new_password": "NewTestPass12!",
        }, format="json")

        self.assertTrue(E2EDeviceKey.objects.filter(device=dev_a).exists())
        self.assertFalse(E2EDeviceKey.objects.filter(device=self.dev_b).exists())

    def test_old_access_token_is_rejected_after_password_change(self):
        # A token minted for dev-b before the change embeds token_version=1.
        stale_tokens = issue_tokens_for_user(self.user, device_id="dev-b")
        stale_client = APIClient()
        stale_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {stale_tokens['access']}",
            HTTP_X_DEVICE_ID="dev-b",
        )

        self.client_a.post("/api/v1/auth/password/change/", {
            "current_password": "TestPass12!",
            "new_password": "NewTestPass12!",
        }, format="json")

        res = stale_client.get("/api/v1/auth/devices/")
        self.assertEqual(res.status_code, 401)


@override_settings(SECURE_SSL_REDIRECT=False)
class PasswordResetDeviceInvalidationTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700000105")
        self.dev_a = Device.objects.create(
            user=self.user, device_id="dev-a", platform="ios",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.dev_b = Device.objects.create(
            user=self.user, device_id="dev-b", platform="android",
            is_parent=False, token_version=1, last_seen_at=timezone.now(),
        )

    def _seed_reset_otp(self, code="123456"):
        PhoneOTP.objects.create(
            phone=self.user.phone,
            purpose="reset",
            code_hash=make_code_hash(self.user.phone, "reset", code),
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )

    def test_reset_revokes_every_device_including_the_resetting_one(self):
        self._seed_reset_otp()
        client = APIClient()
        res = client.post("/api/v1/auth/password/reset/", {
            "phone": self.user.phone,
            "code": "123456",
            "new_password": "AnotherNewPass12!",
        }, format="json")
        self.assertEqual(res.status_code, 200, res.data)

        dev_a = Device.objects.get(user=self.user, device_id="dev-a")
        dev_b = Device.objects.get(user=self.user, device_id="dev-b")
        self.assertIsNotNone(dev_a.revoked_at)
        self.assertIsNotNone(dev_b.revoked_at)


@override_settings(SECURE_SSL_REDIRECT=False)
class ParentRecoveryConfirmTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700000106")
        self.user.email = "recovery-target@example.com"
        self.user.email_verified = True
        self.user.save(update_fields=["email", "email_verified"])
        self.old_parent = Device.objects.create(
            user=self.user, device_id="dev-old", platform="ios",
            is_parent=True, token_version=3, last_seen_at=timezone.now(),
        )
        E2EDeviceKey.objects.create(
            user=self.user, device=self.old_parent, identity_key="x",
            signed_prekey_id=1, signed_prekey="y", signed_prekey_signature="z",
        )

    def _get_recovery_token(self):
        from django.core.cache import cache
        with patch("apps.accounts.views._send_recovery_email") as mock_send:
            client = APIClient()
            res = client.post("/api/v1/auth/recovery/initiate/", {
                "email": self.user.email,
            }, format="json")
            self.assertEqual(res.status_code, 200)
            self.assertTrue(mock_send.called)
            return mock_send.call_args[0][1]  # (user, recovery_code)

    def test_confirm_revokes_old_parent_via_token_version_bump_and_e2ee_wipe(self):
        recovery_code = self._get_recovery_token()
        client = APIClient()
        res = client.post("/api/v1/auth/recovery/confirm/", {
            "recovery_token": recovery_code,
            "device_id": "dev-new",
            "platform": "android",
        }, format="json")
        self.assertEqual(res.status_code, 200, res.data)

        old = Device.objects.get(user=self.user, device_id="dev-old")
        new = Device.objects.get(user=self.user, device_id="dev-new")
        self.assertIsNotNone(old.revoked_at)
        self.assertFalse(old.is_parent)
        self.assertEqual(old.token_version, 4)
        self.assertFalse(E2EDeviceKey.objects.filter(device=old).exists())
        self.assertTrue(new.is_parent)
        self.assertIsNone(new.revoked_at)

    def test_old_parents_access_token_stops_working_after_recovery(self):
        stale_tokens = issue_tokens_for_user(self.user, device_id="dev-old")
        stale_client = APIClient()
        stale_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {stale_tokens['access']}",
            HTTP_X_DEVICE_ID="dev-old",
        )

        recovery_code = self._get_recovery_token()
        APIClient().post("/api/v1/auth/recovery/confirm/", {
            "recovery_token": recovery_code,
            "device_id": "dev-new",
            "platform": "android",
        }, format="json")

        res = stale_client.get("/api/v1/auth/devices/")
        self.assertEqual(res.status_code, 401)

    def test_unverified_email_cannot_initiate_recovery(self):
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        with patch("apps.accounts.views._send_recovery_email") as mock_send:
            client = APIClient()
            res = client.post("/api/v1/auth/recovery/initiate/", {
                "email": self.user.email,
            }, format="json")
            # Anti-enumeration: still 200, but nothing is actually sent.
            self.assertEqual(res.status_code, 200)
            self.assertFalse(mock_send.called)


@override_settings(SECURE_SSL_REDIRECT=False)
class EmailVerifyOtpPurposeTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700000107")
        self.user.email = "verify-me@example.com"
        self.user.save(update_fields=["email"])
        self.assertFalse(self.user.email_verified)

    def test_correct_code_marks_email_verified(self):
        PhoneOTP.objects.create(
            phone=self.user.phone,
            purpose="email_verify",
            code_hash=make_code_hash(self.user.phone, "email_verify", "654321"),
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        client = APIClient()
        res = client.post("/api/v1/auth/otp/verify/", {
            "phone": self.user.phone,
            "purpose": "email_verify",
            "code": "654321",
            "country": "CM",
        }, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_initiate_rejects_non_email_channel_for_this_purpose(self):
        client = APIClient()
        res = client.post("/api/v1/auth/otp/initiate/", {
            "phone": self.user.phone,
            "purpose": "email_verify",
            "channel": "sms",
            "country": "CM",
        }, format="json")
        self.assertEqual(res.status_code, 400)
