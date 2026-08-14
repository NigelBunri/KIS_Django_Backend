"""
Regression tests for Quick Lock PIN multi-device synchronization.

Root cause under test: QuickLockPinView only exposed POST/DELETE, so a PIN
created on Device A was invisible to Device B — Device B's local
isPINEnabled() check (EncryptedStorage-only) had no way to learn the
account-level fact. UserSerializer.has_pin (surfaced via /api/v1/users/me/,
already polled by the frontend on every foreground/session check) and the
same field on LoginView's response are the fix: a safe boolean, never the
PIN or its hash, that every device converges on.

Run:
  python3 manage.py test apps.accounts.test_quicklock_pin_multi_device_sync --keepdb -v 2
"""
from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Device, ProfilePreferences, User
from .views import issue_tokens_for_user

DEVICE_A = "test-device-a"
DEVICE_B = "test-device-b"


def make_verified_user(phone, password="TestPass12!", country="CM"):
    user = User.objects.create_user(phone=phone, password=password, country=country)
    user.verification = {"phone": {"verified": True, "verified_at": timezone.now().isoformat()}}
    user.status = "active"
    user.is_active = True
    user.save(update_fields=["verification", "status", "is_active"])
    return user


def auth_client(user, device_id=DEVICE_A):
    tokens = issue_tokens_for_user(user, device_id=device_id)
    # DeviceBoundJWTAuthentication requires a live, non-revoked Device row
    # matching the token's device_id claim — without one every request 401s.
    Device.objects.get_or_create(
        user=user, device_id=device_id,
        defaults={"platform": "android", "is_parent": True, "token_version": 1, "last_seen_at": timezone.now()},
    )
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {tokens['access']}",
        HTTP_X_DEVICE_ID=device_id,
    )
    return client


class HasPinFieldTests(TestCase):
    """UserSerializer.has_pin via GET /api/v1/users/me/ — the endpoint the
    frontend already refreshes on boot, foreground, and session-expiry."""

    def setUp(self):
        self.user = make_verified_user("+237670001101")
        self.client = auth_client(self.user)

    def test_has_pin_false_by_default(self):
        # A ProfilePreferences row already exists (auto-created by a
        # post_save signal on User) but with no PIN set.
        res = self.client.get("/api/v1/users/me/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["has_pin"])

    def test_has_pin_true_after_creating_pin(self):
        res = self.client.post("/api/v1/auth/quicklock-pin/", {"pin": "123456"}, format="json")
        self.assertEqual(res.status_code, 200)

        res = self.client.get("/api/v1/users/me/")
        self.assertTrue(res.data["has_pin"])

    def test_has_pin_false_after_deleting_pin(self):
        self.client.post("/api/v1/auth/quicklock-pin/", {"pin": "123456"}, format="json")
        del_res = self.client.delete("/api/v1/auth/quicklock-pin/")
        self.assertEqual(del_res.status_code, 200)

        res = self.client.get("/api/v1/users/me/")
        self.assertFalse(res.data["has_pin"])

    def test_has_pin_true_preserved_across_pin_change(self):
        self.client.post("/api/v1/auth/quicklock-pin/", {"pin": "111111"}, format="json")
        prefs = ProfilePreferences.objects.get(user=self.user)
        first_hash = prefs.quicklock_pin_hash

        change_res = self.client.post("/api/v1/auth/quicklock-pin/", {"pin": "222222"}, format="json")
        self.assertEqual(change_res.status_code, 200)

        res = self.client.get("/api/v1/users/me/")
        self.assertTrue(res.data["has_pin"])
        prefs.refresh_from_db()
        self.assertNotEqual(prefs.quicklock_pin_hash, first_hash)

    def test_pin_and_hash_never_serialized(self):
        self.client.post("/api/v1/auth/quicklock-pin/", {"pin": "654321"}, format="json")
        res = self.client.get("/api/v1/users/me/")
        body = res.json()
        self.assertNotIn("quicklock_pin_hash", body)
        self.assertNotIn("pin", body)
        serialized = str(body)
        self.assertNotIn("654321", serialized)

    def test_unauthenticated_request_denied(self):
        res = APIClient().get("/api/v1/users/me/")
        self.assertEqual(res.status_code, 401)


class HasPinCrossAccountIsolationTests(TestCase):
    """Another (non-staff) user must never learn whether someone else has a
    PIN configured — PublicUserSerializer is the safe view for that case."""

    def setUp(self):
        self.owner = make_verified_user("+237670001102")
        self.other = make_verified_user("+237670001103")
        self.owner_client = auth_client(self.owner, device_id=DEVICE_A)
        self.other_client = auth_client(self.other, device_id=DEVICE_B)
        self.owner_client.post("/api/v1/auth/quicklock-pin/", {"pin": "999999"}, format="json")

    def test_other_user_retrieval_omits_has_pin(self):
        res = self.other_client.get(f"/api/v1/users/{self.owner.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("has_pin", res.json())
        self.assertNotIn("quicklock_pin_hash", res.json())

    def test_owner_still_sees_own_has_pin(self):
        res = self.owner_client.get("/api/v1/users/me/")
        self.assertTrue(res.data["has_pin"])


class LoginResponseHasPinTests(TestCase):
    """LoginView's response carries has_pin directly so a fresh login on a
    brand-new device recognizes an existing PIN without waiting for a
    second /users/me/ round trip."""

    def _login(self, phone, password="TestPass12!", device_id=DEVICE_B):
        return self.client.post("/api/v1/auth/login/", {
            "phone": phone,
            "password": password,
            "device_id": device_id,
        }, format="json")

    def test_login_reports_has_pin_true_for_account_with_pin(self):
        # PIN was created "on Device A" at some earlier point (modeled
        # directly at the ORM layer — equivalent to a prior authenticated
        # POST to /api/v1/auth/quicklock-pin/, without needing Device A's
        # own session still active here). No device is pre-registered, so
        # this login is the account's very first device: unrestricted.
        user = make_verified_user("+237670001104")
        ProfilePreferences.objects.update_or_create(
            user=user, defaults={"quicklock_pin_hash": make_password("121212")},
        )

        res = self._login("+237670001104", device_id=DEVICE_B)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["user"]["has_pin"])

    def test_login_reports_has_pin_false_for_account_without_pin(self):
        make_verified_user("+237670001105")
        res = self._login("+237670001105", device_id=DEVICE_A)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["user"]["has_pin"])

    def test_login_response_never_carries_pin_or_hash(self):
        user = make_verified_user("+237670001106")
        ProfilePreferences.objects.update_or_create(
            user=user, defaults={"quicklock_pin_hash": make_password("343434")},
        )
        res = self._login("+237670001106", device_id=DEVICE_B)
        body = res.json()
        self.assertNotIn("quicklock_pin_hash", str(body))
        self.assertNotIn("343434", str(body))
