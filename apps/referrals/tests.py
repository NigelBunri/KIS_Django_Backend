"""
Tests for the Referral Rewards Program (Phase 4).

Run:
  python3 manage.py test apps.referrals --keepdb -v 2
"""
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, Device, User
from apps.commerce.models import LoyaltyPoint

from .models import Referral, ReferralCode, generate_referral_code
from .services import (
    REFERRAL_REWARD_POINTS,
    apply_referral_reward_if_pending,
    register_referral,
)

DEVICE_ID_A = "device-A"
DEVICE_ID_B = "device-B"


@contextmanager
def _frozen_at(instant):
    with patch("django.utils.timezone.now", return_value=instant):
        yield instant


def _points_balance(user) -> int:
    return LoyaltyPoint.objects.filter(user=user).aggregate(total=Sum("points"))["total"] or 0


def _make_user(phone: str, active: bool = True) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    if active:
        user.verification = {"phone": {"verified": True, "verified_at": timezone.now().isoformat()}}
        user.status = "active"
        user.is_active = True
        user.save(update_fields=["verification", "status", "is_active"])
    return user


class ReferralCodeGenerationTests(TestCase):
    def test_generated_code_has_expected_length_and_alphabet(self):
        code = generate_referral_code()
        self.assertEqual(len(code), 8)
        self.assertTrue(set(code).issubset(set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")))

    def test_get_or_create_for_user_is_idempotent(self):
        user = _make_user("+237699100001")
        first = ReferralCode.get_or_create_for_user(user)
        second = ReferralCode.get_or_create_for_user(user)
        self.assertEqual(first.id, second.id)
        self.assertEqual(ReferralCode.objects.filter(user=user).count(), 1)

    def test_codes_are_unique_across_users(self):
        u1 = _make_user("+237699100002")
        u2 = _make_user("+237699100003")
        c1 = ReferralCode.get_or_create_for_user(u1)
        c2 = ReferralCode.get_or_create_for_user(u2)
        self.assertNotEqual(c1.code, c2.code)


class RegisterReferralServiceTests(TestCase):
    def setUp(self):
        self.referrer = _make_user("+237699100010")
        self.code = ReferralCode.get_or_create_for_user(self.referrer)

    def test_valid_code_creates_pending_referral(self):
        referred = _make_user("+237699100011")
        referral = register_referral(referred_user=referred, referral_code=self.code.code)
        self.assertIsNotNone(referral)
        self.assertEqual(referral.status, Referral.STATUS_PENDING)
        self.assertEqual(referral.referrer_id, self.referrer.id)

    def test_code_is_matched_case_and_whitespace_insensitively(self):
        referred = _make_user("+237699100012")
        referral = register_referral(referred_user=referred, referral_code=f"  {self.code.code.lower()}  ")
        self.assertIsNotNone(referral)

    def test_blank_code_is_a_no_op(self):
        referred = _make_user("+237699100013")
        self.assertIsNone(register_referral(referred_user=referred, referral_code=""))
        self.assertFalse(Referral.objects.filter(referred_user=referred).exists())

    def test_unknown_code_is_a_no_op_and_never_blocks_registration(self):
        referred = _make_user("+237699100014")
        self.assertIsNone(register_referral(referred_user=referred, referral_code="NOTREAL1"))
        self.assertFalse(Referral.objects.filter(referred_user=referred).exists())

    def test_self_referral_is_a_no_op(self):
        result = register_referral(referred_user=self.referrer, referral_code=self.code.code)
        self.assertIsNone(result)

    def test_referred_user_can_only_ever_be_referred_once(self):
        referred = _make_user("+237699100015")
        register_referral(referred_user=referred, referral_code=self.code.code)
        other_referrer = _make_user("+237699100016")
        other_code = ReferralCode.get_or_create_for_user(other_referrer)
        with self.assertRaises(Exception):
            register_referral(referred_user=referred, referral_code=other_code.code)

    def test_same_device_as_referrer_is_blocked_not_rewarded(self):
        Device.objects.create(
            user=self.referrer, device_id=DEVICE_ID_A, platform="android",
            last_seen_at=timezone.now(),
        )
        referred = _make_user("+237699100017")
        referral = register_referral(
            referred_user=referred, referral_code=self.code.code, device_id=DEVICE_ID_A,
        )
        self.assertEqual(referral.status, Referral.STATUS_BLOCKED)
        self.assertTrue(referral.block_reason)

    def test_different_device_is_left_pending(self):
        Device.objects.create(
            user=self.referrer, device_id=DEVICE_ID_A, platform="android",
            last_seen_at=timezone.now(),
        )
        referred = _make_user("+237699100018")
        referral = register_referral(
            referred_user=referred, referral_code=self.code.code, device_id=DEVICE_ID_B,
        )
        self.assertEqual(referral.status, Referral.STATUS_PENDING)

    def test_creates_an_audit_log_entry(self):
        referred = _make_user("+237699100019")
        register_referral(referred_user=referred, referral_code=self.code.code)
        self.assertTrue(
            AuditLog.objects.filter(actor_id=referred.id, action="referral.created").exists()
        )


class ApplyReferralRewardIfPendingTests(TestCase):
    def setUp(self):
        self.referrer = _make_user("+237699100020")
        self.code = ReferralCode.get_or_create_for_user(self.referrer)
        self.referred = _make_user("+237699100021")
        self.referral = register_referral(referred_user=self.referred, referral_code=self.code.code)

    def test_grants_points_to_the_referrer(self):
        with _frozen_at(timezone.now()):
            apply_referral_reward_if_pending(self.referred)
        self.assertEqual(_points_balance(self.referrer), REFERRAL_REWARD_POINTS)

    def test_marks_the_referral_rewarded_with_a_timestamp(self):
        instant = timezone.now() + timedelta(days=1)
        with _frozen_at(instant):
            apply_referral_reward_if_pending(self.referred)
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_REWARDED)
        self.assertEqual(self.referral.reward_points_awarded, REFERRAL_REWARD_POINTS)
        self.assertEqual(self.referral.rewarded_at, instant)

    def test_is_idempotent_a_second_call_does_not_double_reward(self):
        apply_referral_reward_if_pending(self.referred)
        apply_referral_reward_if_pending(self.referred)
        self.assertEqual(_points_balance(self.referrer), REFERRAL_REWARD_POINTS)

    def test_no_pending_referral_is_a_no_op(self):
        lone_user = _make_user("+237699100022")
        result = apply_referral_reward_if_pending(lone_user)
        self.assertIsNone(result)

    def test_blocked_referral_is_never_rewarded(self):
        self.referral.status = Referral.STATUS_BLOCKED
        self.referral.save(update_fields=["status"])
        apply_referral_reward_if_pending(self.referred)
        self.assertEqual(_points_balance(self.referrer), 0)

    def test_creates_an_audit_log_entry(self):
        apply_referral_reward_if_pending(self.referred)
        self.assertTrue(
            AuditLog.objects.filter(actor_id=self.referrer.id, action="referral.reward_granted").exists()
        )


@override_settings(SECURE_SSL_REDIRECT=False, KIS_PHONE_VERIFICATION_ENABLED=False)
class RegistrationIntegrationVerificationSuspendedTests(TestCase):
    """Matches the current production default: registration activates the
    account (and should grant the referral reward) immediately, with no
    separate OTP step."""

    def setUp(self):
        self.client = APIClient()
        self.referrer = _make_user("+237699100030")
        self.code = ReferralCode.get_or_create_for_user(self.referrer)

    def test_registering_with_a_valid_referral_code_rewards_the_referrer_immediately(self):
        res = self.client.post("/api/v1/auth/register/", {
            "phone_country_code": "+237", "phone_number": "699100031", "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": "reg-device-1", "device_platform": "android",
            "referral_code": self.code.code,
        }, format="json")
        self.assertEqual(res.status_code, 201)
        new_user = User.objects.get(phone_number="699100031")
        self.assertTrue(Referral.objects.filter(referred_user=new_user, status=Referral.STATUS_REWARDED).exists())
        self.assertEqual(_points_balance(self.referrer), REFERRAL_REWARD_POINTS)

    def test_registering_with_no_referral_code_grants_nothing(self):
        res = self.client.post("/api/v1/auth/register/", {
            "phone_country_code": "+237", "phone_number": "699100032", "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": "reg-device-2", "device_platform": "android",
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Referral.objects.count(), 0)

    def test_registering_with_an_invalid_referral_code_still_succeeds(self):
        res = self.client.post("/api/v1/auth/register/", {
            "phone_country_code": "+237", "phone_number": "699100033", "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": "reg-device-3", "device_platform": "android",
            "referral_code": "BOGUS999",
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Referral.objects.count(), 0)

    def test_same_device_as_referrer_registers_successfully_but_is_not_rewarded(self):
        Device.objects.create(
            user=self.referrer, device_id="shared-device", platform="android", last_seen_at=timezone.now(),
        )
        res = self.client.post("/api/v1/auth/register/", {
            "phone_country_code": "+237", "phone_number": "699100034", "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": "shared-device", "device_platform": "android",
            "referral_code": self.code.code,
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(_points_balance(self.referrer), 0)
        new_user = User.objects.get(phone_number="699100034")
        self.assertEqual(
            Referral.objects.get(referred_user=new_user).status, Referral.STATUS_BLOCKED,
        )


@override_settings(SECURE_SSL_REDIRECT=False, KIS_PHONE_VERIFICATION_ENABLED=True)
class OtpVerifyIntegrationTests(TestCase):
    """When verification is live, registration only creates a PENDING
    referral — the reward must wait for real OTP verification, closing the
    burner-number farming vector."""

    def setUp(self):
        self.client = APIClient()
        self.referrer = _make_user("+237699100040")
        self.code = ReferralCode.get_or_create_for_user(self.referrer)

    def test_registration_creates_pending_referral_without_granting_reward_yet(self):
        res = self.client.post("/api/v1/auth/register/", {
            "phone_country_code": "+237", "phone_number": "699100041", "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": "otp-device-1", "device_platform": "android",
            "referral_code": self.code.code,
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["pending_verification"])
        new_user = User.objects.get(phone_number="699100041")
        self.assertEqual(
            Referral.objects.get(referred_user=new_user).status, Referral.STATUS_PENDING,
        )
        self.assertEqual(_points_balance(self.referrer), 0)

    @override_settings(OTP_OVERRIDE_ENABLED=True, OTP_OVERRIDE_CODE="676139")
    def test_completing_otp_verification_grants_the_reward(self):
        self.client.post("/api/v1/auth/register/", {
            "phone_country_code": "+237", "phone_number": "699100042", "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": "otp-device-2", "device_platform": "android",
            "referral_code": self.code.code,
        }, format="json")
        res = self.client.post("/api/v1/auth/otp/verify/", {
            "country": "CM", "phone": "+237699100042", "purpose": "register",
            "code": "676139", "device_id": "otp-device-2",
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_points_balance(self.referrer), REFERRAL_REWARD_POINTS)

    @override_settings(OTP_OVERRIDE_ENABLED=True, OTP_OVERRIDE_CODE="676139")
    def test_a_login_purpose_otp_verify_does_not_grant_or_double_grant(self):
        self.client.post("/api/v1/auth/register/", {
            "phone_country_code": "+237", "phone_number": "699100043", "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": "otp-device-3", "device_platform": "android",
            "referral_code": self.code.code,
        }, format="json")
        res = self.client.post("/api/v1/auth/otp/verify/", {
            "country": "CM", "phone": "+237699100043", "purpose": "login",
            "code": "676139", "device_id": "otp-device-3",
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_points_balance(self.referrer), 0)


class MyReferralsViewTests(TestCase):
    def setUp(self):
        self.referrer = _make_user("+237699100050")
        self.other = _make_user("+237699100051")
        self.code = ReferralCode.get_or_create_for_user(self.referrer)
        Device.objects.create(
            user=self.referrer, device_id="view-device", platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        from apps.accounts.views import issue_tokens_for_user
        tokens = issue_tokens_for_user(self.referrer, device_id="view-device")
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID="view-device",
        )

    def test_returns_own_code_and_zeroed_stats_with_no_referrals(self):
        res = self.client.get("/api/v1/referrals/me/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["code"], self.code.code)
        self.assertEqual(res.data["reward_points_per_referral"], REFERRAL_REWARD_POINTS)
        self.assertEqual(res.data["total_referred"], 0)
        self.assertEqual(res.data["total_rewarded"], 0)
        self.assertEqual(res.data["total_points_earned"], 0)
        self.assertEqual(res.data["history"], [])

    def test_reflects_a_rewarded_referral_in_stats_and_history(self):
        referred = _make_user("+237699100052")
        register_referral(referred_user=referred, referral_code=self.code.code)
        apply_referral_reward_if_pending(referred)

        res = self.client.get("/api/v1/referrals/me/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total_referred"], 1)
        self.assertEqual(res.data["total_rewarded"], 1)
        self.assertEqual(res.data["total_points_earned"], REFERRAL_REWARD_POINTS)
        self.assertEqual(len(res.data["history"]), 1)
        self.assertEqual(res.data["history"][0]["status"], Referral.STATUS_REWARDED)

    def test_anonymous_access_denied(self):
        anon = APIClient()
        res = anon.get("/api/v1/referrals/me/")
        self.assertEqual(res.status_code, 401)
