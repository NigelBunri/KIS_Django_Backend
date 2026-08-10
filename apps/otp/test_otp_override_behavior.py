"""
Regression tests for the OTP override activation contract:
OTP_OVERRIDE_ENABLED must be explicitly true AND OTP_OVERRIDE_CODE must be
set for the override to do anything — neither has a default value anymore.

Also proves the fix for a real latent bug this change surfaced: the override
code used to be captured in a module-level constant at import time
(`OVERRIDE_OTP_CODE = getattr(settings, "OTP_OVERRIDE_CODE", "676139")`), so
@override_settings(OTP_OVERRIDE_CODE=...) in existing tests never actually
took effect — those tests only ever passed because the override happened to
match the hardcoded default. _override_otp_active() now reads settings live
per-request, so override_settings works correctly and dynamically here.

Run:
  python3 manage.py test apps.otp.test_otp_override_behavior --keepdb -v 2
"""
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User

DEVICE_ID = "otp-override-test-device"


def _make_pending_user(phone: str) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.verification = {}
    user.status = "pending"
    user.is_active = True
    user.save(update_fields=["verification", "status", "is_active"])
    return user


@override_settings(SECURE_SSL_REDIRECT=False)
class OtpOverrideActivationContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _verify(self, phone: str, code: str):
        return self.client.post("/api/v1/auth/otp/verify/", {
            "phone": phone, "purpose": "register", "code": code,
            "device_id": DEVICE_ID, "device_platform": "android", "country": "CM",
        }, format="json")

    def test_override_code_rejected_by_default_settings(self):
        """Neither OTP_OVERRIDE_ENABLED nor OTP_OVERRIDE_CODE are set by
        default anymore — the historically well-known code 676139 must not
        bypass verification out of the box."""
        _make_pending_user("+237670099001")
        res = self._verify("+237670099001", "676139")
        self.assertEqual(res.status_code, 400)

    @override_settings(OTP_OVERRIDE_ENABLED=True, OTP_OVERRIDE_CODE="")
    def test_enabled_flag_alone_without_a_code_does_not_bypass(self):
        _make_pending_user("+237670099002")
        res = self._verify("+237670099002", "676139")
        self.assertEqual(res.status_code, 400)

    @override_settings(OTP_OVERRIDE_ENABLED=False, OTP_OVERRIDE_CODE="676139")
    def test_code_alone_without_the_enabled_flag_does_not_bypass(self):
        _make_pending_user("+237670099003")
        res = self._verify("+237670099003", "676139")
        self.assertEqual(res.status_code, 400)

    @override_settings(OTP_OVERRIDE_ENABLED=True, OTP_OVERRIDE_CODE="676139")
    def test_both_enabled_and_code_set_bypasses_as_designed(self):
        _make_pending_user("+237670099004")
        res = self._verify("+237670099004", "676139")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["success"])

    @override_settings(OTP_OVERRIDE_ENABLED=True, OTP_OVERRIDE_CODE="676139")
    def test_wrong_code_still_rejected_even_with_override_enabled(self):
        _make_pending_user("+237670099005")
        res = self._verify("+237670099005", "000000")
        self.assertEqual(res.status_code, 400)

    def test_override_setting_is_read_live_not_cached_at_import_time(self):
        """Regression proof for the module-level-caching bug: within a
        single test, flipping OTP_OVERRIDE_ENABLED off via a nested
        override_settings block must immediately take effect."""
        _make_pending_user("+237670099006")
        with override_settings(OTP_OVERRIDE_ENABLED=True, OTP_OVERRIDE_CODE="676139"):
            res_enabled = self._verify("+237670099006", "676139")
            self.assertEqual(res_enabled.status_code, 200)

        _make_pending_user("+237670099007")
        # Back to default settings (override cleared) — same code must now fail.
        res_disabled = self._verify("+237670099007", "676139")
        self.assertEqual(res_disabled.status_code, 400)
