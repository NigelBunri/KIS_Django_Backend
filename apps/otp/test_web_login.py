"""
Regression tests for the "web_login" OTP purpose — website sign-in for an
EXISTING KIS account only. Unlike register/login, this purpose must never
auto-activate a dormant/half-registered account, and must reject phones
with no matching account rather than silently registering one.

Uses the OTP_OVERRIDE_ENABLED/OTP_OVERRIDE_CODE bypass (see
test_otp_override_behavior.py) to avoid needing a real generated code.

Run:
  python3 manage.py test apps.otp.test_web_login --keepdb -v 2
"""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User

DEVICE_ID = "web-login-test-device"
OVERRIDE_CODE = "676139"


def _verify(client, phone: str, code: str = OVERRIDE_CODE):
    return client.post("/api/v1/auth/otp/verify/", {
        "phone": phone, "purpose": "web_login", "code": code,
        "device_id": DEVICE_ID, "country": "CM",
    }, format="json")


@override_settings(SECURE_SSL_REDIRECT=False, OTP_OVERRIDE_ENABLED=True, OTP_OVERRIDE_CODE=OVERRIDE_CODE)
class WebLoginOtpPurposeTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_existing_active_user_gets_tokens(self):
        User.objects.create_user(phone="+237670088001", password="TestPass12!", country="CM", status="active", is_active=True)
        res = _verify(self.client, "+237670088001")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["success"])
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)

    def test_unknown_phone_returns_404_not_a_silent_registration(self):
        res = _verify(self.client, "+237670088002")
        self.assertEqual(res.status_code, 404)
        self.assertFalse(User.objects.filter(phone="+237670088002").exists())

    def test_inactive_account_is_rejected_not_activated(self):
        user = User.objects.create_user(phone="+237670088003", password="TestPass12!", country="CM")
        user.status = "pending"
        user.is_active = False
        user.verification = {}
        user.save(update_fields=["status", "is_active", "verification"])

        res = _verify(self.client, "+237670088003")

        self.assertEqual(res.status_code, 403)
        user.refresh_from_db()
        self.assertEqual(user.status, "pending")
        self.assertFalse(user.is_active)

    def test_does_not_mutate_verification_dict_unlike_register_login(self):
        user = User.objects.create_user(phone="+237670088004", password="TestPass12!", country="CM", status="active", is_active=True)
        user.verification = {}
        user.save(update_fields=["verification"])

        res = _verify(self.client, "+237670088004")

        self.assertEqual(res.status_code, 200, res.data)
        user.refresh_from_db()
        self.assertEqual(user.verification, {}, "web_login must not silently mark phone verified the way register/login does")

    def test_wrong_purpose_string_is_rejected_up_front(self):
        res = self.client.post("/api/v1/auth/otp/verify/", {
            "phone": "+237670088005", "purpose": "not_a_real_purpose", "code": OVERRIDE_CODE,
            "device_id": DEVICE_ID, "country": "CM",
        }, format="json")
        self.assertEqual(res.status_code, 400)
