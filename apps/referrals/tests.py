"""
Tests for the Referral Rewards Program (Phase 4), plus the Phase 3
qualification-based referral engine (ReferralRateConfig, qualify_referral,
confirm_referral_reward, reverse_referral_reward) — additive to, and not a
replacement for, the tests above.

Run:
  python3 manage.py test apps.referrals --keepdb -v 2
"""
import importlib
import threading
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, AuditLog, Device, Subscription, User
from apps.accounts.tier_presets import TIER_PRESETS
from apps.commerce.models import LoyaltyPoint
from apps.rewards.models import RewardLedgerEntry
from apps.rewards.services import get_reward_balance

from .models import Referral, ReferralCode, ReferralRateConfig, generate_referral_code
from .services import (
    REFERRAL_SETTLEMENT_WINDOW_DAYS,
    apply_referral_reward_if_pending,
    confirm_referral_reward,
    ensure_referral_rate_configs,
    get_referral_rate_percent,
    qualify_referral,
    register_referral,
    reverse_referral_reward,
    sweep_settleable_referrals,
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
    """Pre-deployment hardening pass: apply_referral_reward_if_pending is
    retired — it must be a permanent, unconditional no-op, structurally
    incapable of paying a reward, so qualify_referral/confirm_referral_reward
    remain the single authoritative referral reward engine."""

    def setUp(self):
        self.referrer = _make_user("+237699100020")
        self.code = ReferralCode.get_or_create_for_user(self.referrer)
        self.referred = _make_user("+237699100021")
        self.referral = register_referral(referred_user=self.referred, referral_code=self.code.code)

    def test_grants_no_points_and_returns_none(self):
        result = apply_referral_reward_if_pending(self.referred)
        self.assertIsNone(result)
        self.assertEqual(_points_balance(self.referrer), 0)

    def test_does_not_change_the_referral_status(self):
        apply_referral_reward_if_pending(self.referred)
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_PENDING)
        self.assertEqual(self.referral.reward_points_awarded, 0)
        self.assertIsNone(self.referral.rewarded_at)

    def test_repeated_calls_remain_a_noop(self):
        apply_referral_reward_if_pending(self.referred)
        apply_referral_reward_if_pending(self.referred)
        self.assertEqual(_points_balance(self.referrer), 0)
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_PENDING)

    def test_no_pending_referral_is_also_a_no_op(self):
        lone_user = _make_user("+237699100022")
        result = apply_referral_reward_if_pending(lone_user)
        self.assertIsNone(result)

    def test_does_not_create_an_audit_log_entry(self):
        apply_referral_reward_if_pending(self.referred)
        self.assertFalse(
            AuditLog.objects.filter(actor_id=self.referrer.id, action="referral.reward_granted").exists()
        )

    def test_leaves_the_pending_referral_available_for_the_real_engine(self):
        """The whole point of retiring the legacy path: qualify_referral
        must still be able to qualify a referral that apply_referral_reward_
        if_pending was (harmlessly) called against first."""
        tier = AccountTier.objects.create(name="RetiredLegacyTier", rank=77, price_cents=5000)
        ReferralRateConfig.objects.create(tier=tier, rate_percent=Decimal("8.00"), is_active=True)
        _make_active_subscription(self.referrer, tier)
        sub = _make_active_subscription(self.referred, tier)

        apply_referral_reward_if_pending(self.referred)  # legacy call site, now harmless

        result = qualify_referral(self.referred, sub, net_amount_cents=10000)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, Referral.STATUS_QUALIFIED)


@override_settings(SECURE_SSL_REDIRECT=False, KIS_PHONE_VERIFICATION_ENABLED=False)
class RegistrationIntegrationVerificationSuspendedTests(TestCase):
    """Matches the current production default: registration activates the
    account immediately, with no separate OTP step. Pre-deployment
    hardening pass: registration must NOT grant the legacy reward anymore —
    the referral stays PENDING until a real qualifying payment."""

    def setUp(self):
        self.client = APIClient()
        self.referrer = _make_user("+237699100030")
        self.code = ReferralCode.get_or_create_for_user(self.referrer)

    def test_registering_with_a_valid_referral_code_creates_a_pending_referral_and_grants_no_reward(self):
        res = self.client.post("/api/v1/auth/register/", {
            "phone_country_code": "+237", "phone_number": "699100031", "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": "reg-device-1", "device_platform": "android",
            "referral_code": self.code.code,
        }, format="json")
        self.assertEqual(res.status_code, 201)
        new_user = User.objects.get(phone_number="699100031")
        self.assertTrue(Referral.objects.filter(referred_user=new_user, status=Referral.STATUS_PENDING).exists())
        self.assertEqual(_points_balance(self.referrer), 0)

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
    referral. Pre-deployment hardening pass: OTP verification completing
    must NOT grant the legacy reward either — the referral stays PENDING
    until a real qualifying payment, regardless of which activation path
    (immediate registration or OTP verify) the account took."""

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
    def test_completing_otp_verification_grants_no_reward_and_referral_stays_pending(self):
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
        self.assertEqual(_points_balance(self.referrer), 0)
        new_user = User.objects.get(phone_number="699100042")
        self.assertEqual(
            Referral.objects.get(referred_user=new_user).status, Referral.STATUS_PENDING,
        )

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


@override_settings(SECURE_SSL_REDIRECT=False, KIS_PHONE_VERIFICATION_ENABLED=False)
class SingleAuthoritativeReferralEngineEndToEndTests(TestCase):
    """Pre-deployment hardening pass, CRITICAL requirement: there must be
    exactly ONE authoritative referral reward engine, end to end through
    the real HTTP registration path plus a real qualifying payment — not
    just at the qualify_referral()/apply_referral_reward_if_pending()
    service-function level."""

    def setUp(self):
        self.client = APIClient()
        self.referrer = _make_user("+237699100060")
        self.tier = AccountTier.objects.create(name="E2ESingleEngineTier", rank=66, price_cents=10000)
        ReferralRateConfig.objects.create(tier=self.tier, rate_percent=Decimal("8.00"), is_active=True)
        _make_active_subscription(self.referrer, self.tier)
        self.code = ReferralCode.get_or_create_for_user(self.referrer)

    def test_registration_then_real_payment_pays_exactly_once_via_the_new_engine_only(self):
        # Registration (legacy call sites removed) creates a PENDING
        # referral and pays nothing.
        res = self.client.post("/api/v1/auth/register/", {
            "phone_country_code": "+237", "phone_number": "699100061", "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": "e2e-device-1", "device_platform": "android",
            "referral_code": self.code.code,
        }, format="json")
        self.assertEqual(res.status_code, 201)
        new_user = User.objects.get(phone_number="699100061")
        referral = Referral.objects.get(referred_user=new_user)
        self.assertEqual(referral.status, Referral.STATUS_PENDING)
        self.assertEqual(_points_balance(self.referrer), 0)
        self.assertEqual(get_reward_balance(self.referrer)["available"], 0)

        # A real qualifying payment (source="flutterwave") is the ONLY
        # thing that can now move this referral forward.
        sub = _make_active_subscription(new_user, self.tier)
        qualified = qualify_referral(new_user, sub, net_amount_cents=10000)
        self.assertIsNotNone(qualified)
        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_QUALIFIED)

        # Settlement (the scheduled sweep's job) pays the tier-aware amount.
        confirm_referral_reward(referral)
        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_REWARDED)
        self.assertEqual(referral.reward_points_awarded, 800)  # 10000 cents * 8%

        # Exactly one reward, via exactly one engine: the new ledger has
        # the tier-aware amount; the legacy LoyaltyPoint table has nothing.
        self.assertEqual(get_reward_balance(self.referrer)["available"], 800)
        self.assertEqual(_points_balance(self.referrer), 0)

        # Calling the retired legacy function directly (e.g. an old client
        # or a stray call somewhere) must not be able to add a second reward
        # on top of the one the new engine already paid.
        apply_referral_reward_if_pending(new_user)
        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_REWARDED)
        self.assertEqual(get_reward_balance(self.referrer)["available"], 800)
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
        self.assertNotIn("reward_points_per_referral", res.data, "retired legacy flat-rate field must no longer be exposed")
        self.assertEqual(res.data["total_referred"], 0)
        self.assertEqual(res.data["total_rewarded"], 0)
        self.assertEqual(res.data["total_points_earned"], 0)
        self.assertEqual(res.data["history"], [])

    def test_reflects_a_rewarded_referral_in_stats_and_history(self):
        """Rewarded via the real engine (qualify -> confirm), not the
        retired legacy path — this is now the only way a Referral ever
        reaches REWARDED for a fresh referral."""
        tier = AccountTier.objects.create(name="MyReferralsViewTier", rank=88, price_cents=5000)
        ReferralRateConfig.objects.create(tier=tier, rate_percent=Decimal("8.00"), is_active=True)
        _make_active_subscription(self.referrer, tier)
        referred = _make_user("+237699100052")
        register_referral(referred_user=referred, referral_code=self.code.code)
        sub = _make_active_subscription(referred, tier)
        referral = qualify_referral(referred, sub, net_amount_cents=10000)
        confirm_referral_reward(referral)

        res = self.client.get("/api/v1/referrals/me/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total_referred"], 1)
        self.assertEqual(res.data["total_rewarded"], 1)
        self.assertEqual(res.data["total_points_earned"], 800)  # 10000 cents * 8%
        self.assertEqual(len(res.data["history"]), 1)
        self.assertEqual(res.data["history"][0]["status"], Referral.STATUS_REWARDED)

    def test_anonymous_access_denied(self):
        anon = APIClient()
        res = anon.get("/api/v1/referrals/me/")
        self.assertEqual(res.status_code, 401)


# ---------------------------------------------------------------------
# Phase 3: qualification-based referral engine
# ---------------------------------------------------------------------

_seed_migration_module = importlib.import_module(
    "apps.referrals.migrations.0003_seed_referral_rate_config"
)
seed_referral_rates = _seed_migration_module.seed_referral_rates


def _make_active_subscription(user, tier):
    Subscription.objects.filter(user=user, status=Subscription.STATUS_ACTIVE).update(
        status=Subscription.STATUS_SUPERSEDED,
    )
    return Subscription.objects.create(
        user=user, tier=tier, status=Subscription.STATUS_ACTIVE,
        started_at=timezone.now(), ends_at=timezone.now() + timedelta(days=30),
    )


def _setup_referral(rate_percent=Decimal("8.00"), rank=2, phone_prefix="699200"):
    referrer = _make_user(f"+237{phone_prefix}001")
    tier = AccountTier.objects.create(name=f"TestTier-{phone_prefix}", rank=rank, price_cents=10000)
    ReferralRateConfig.objects.create(tier=tier, rate_percent=rate_percent, is_active=True)
    _make_active_subscription(referrer, tier)
    code = ReferralCode.get_or_create_for_user(referrer)
    referred = _make_user(f"+237{phone_prefix}002")
    referral = register_referral(referred_user=referred, referral_code=code.code)
    return referrer, referred, referral, tier


class ReferralRateConfigTests(TestCase):
    def test_ensure_referral_rate_configs_maps_rank_to_rate(self):
        business = AccountTier.objects.create(name="EnsureBusiness", rank=2, price_cents=5000)
        result = ensure_referral_rate_configs()
        self.assertGreaterEqual(result["created"], 1)
        config = ReferralRateConfig.objects.get(tier=business)
        self.assertEqual(config.rate_percent, Decimal("8.00"))

    def test_ensure_is_idempotent_and_self_healing(self):
        tier = AccountTier.objects.create(name="SelfHeal", rank=4, price_cents=5000)
        ensure_referral_rate_configs()
        config = ReferralRateConfig.objects.get(tier=tier)
        config.rate_percent = Decimal("1.00")
        config.is_active = False
        config.save(update_fields=["rate_percent", "is_active"])

        result = ensure_referral_rate_configs()
        config.refresh_from_db()
        self.assertEqual(config.rate_percent, Decimal("15.00"))
        self.assertTrue(config.is_active)
        self.assertGreaterEqual(result["updated"], 1)

    def test_get_referral_rate_percent_returns_zero_for_unconfigured_tier(self):
        weird = AccountTier.objects.create(name="Mystery", rank=99, price_cents=100)
        self.assertEqual(get_referral_rate_percent(weird), Decimal("0"))

    def test_get_referral_rate_percent_returns_zero_for_inactive_config(self):
        tier = AccountTier.objects.create(name="InactiveRate", rank=1, price_cents=2000)
        ReferralRateConfig.objects.create(tier=tier, rate_percent=Decimal("5.00"), is_active=False)
        self.assertEqual(get_referral_rate_percent(tier), Decimal("0"))

    def test_get_referral_rate_percent_returns_zero_for_none_tier(self):
        self.assertEqual(get_referral_rate_percent(None), Decimal("0"))


class QualifyReferralTests(TestCase):
    def test_qualifies_and_creates_pending_ledger_entry_with_snapshotted_rate(self):
        referrer, referred, referral, tier = _setup_referral(rate_percent=Decimal("8.00"), phone_prefix="699201")
        sub = _make_active_subscription(referred, tier)

        result = qualify_referral(referred, sub, net_amount_cents=10000)

        self.assertIsNotNone(result)
        self.assertEqual(result.status, Referral.STATUS_QUALIFIED)
        self.assertEqual(result.reward_rate_percent_snapshot, Decimal("8.00"))
        self.assertEqual(result.qualifying_net_amount_cents, 10000)

        entry = result.reward_ledger_entry
        self.assertEqual(entry.status, RewardLedgerEntry.STATUS_PENDING)
        self.assertEqual(entry.amount, 800)  # 10000 cents * 8%
        self.assertEqual(get_reward_balance(referrer), {"available": 0, "pending": 800})

    def test_is_idempotent_a_second_call_does_not_double_qualify(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699202")
        sub = _make_active_subscription(referred, tier)

        first = qualify_referral(referred, sub, net_amount_cents=10000)
        second = qualify_referral(referred, sub, net_amount_cents=10000)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(
            RewardLedgerEntry.objects.filter(reference_type="referral", reference_id=referral.id).count(), 1,
        )

    def test_no_pending_referral_is_a_noop(self):
        lone = _make_user("+237699203099")
        tier = AccountTier.objects.create(name="LoneTier", rank=9, price_cents=100)
        sub = _make_active_subscription(lone, tier)
        self.assertIsNone(qualify_referral(lone, sub, net_amount_cents=1000))

    def test_missing_rate_config_is_a_noop_and_is_audited(self):
        referrer = _make_user("+237699204001")
        tier = AccountTier.objects.create(name="NoRateTier", rank=50, price_cents=100)
        _make_active_subscription(referrer, tier)
        code = ReferralCode.get_or_create_for_user(referrer)
        referred = _make_user("+237699204002")
        register_referral(referred_user=referred, referral_code=code.code)
        sub = _make_active_subscription(referred, tier)

        result = qualify_referral(referred, sub, net_amount_cents=1000)

        self.assertIsNone(result)
        self.assertTrue(
            AuditLog.objects.filter(
                actor_id=referrer.id, action="referral.qualification_skipped_no_rate",
            ).exists()
        )


class QualifyReferralConcurrencyTests(TransactionTestCase):
    """Real threads + real Postgres row locking — TestCase's single wrapped
    transaction would make a race trivially "safe" for the wrong reason."""

    def test_concurrent_qualification_only_succeeds_once(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699205")
        sub = _make_active_subscription(referred, tier)
        results = []

        def worker():
            try:
                results.append(qualify_referral(referred, sub, net_amount_cents=10000))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 1, "exactly one concurrent qualify_referral() should win")
        self.assertEqual(
            RewardLedgerEntry.objects.filter(reference_type="referral", reference_id=referral.id).count(), 1,
        )


class ConfirmReferralRewardTests(TestCase):
    def test_confirms_a_qualified_referral(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699206")
        sub = _make_active_subscription(referred, tier)
        qualify_referral(referred, sub, net_amount_cents=10000)
        referral.refresh_from_db()

        result = confirm_referral_reward(referral)

        self.assertEqual(result.status, Referral.STATUS_REWARDED)
        self.assertEqual(result.reward_points_awarded, 800)
        self.assertIsNotNone(result.rewarded_at)
        self.assertEqual(get_reward_balance(referrer), {"available": 800, "pending": 0})

    def test_is_idempotent(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699207")
        sub = _make_active_subscription(referred, tier)
        qualify_referral(referred, sub, net_amount_cents=10000)
        referral.refresh_from_db()

        first = confirm_referral_reward(referral)
        second = confirm_referral_reward(referral)

        self.assertEqual(first.status, second.status, Referral.STATUS_REWARDED)
        self.assertEqual(get_reward_balance(referrer)["available"], 800)

    def test_noop_when_referral_never_qualified(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699208")
        result = confirm_referral_reward(referral)
        self.assertEqual(result.status, Referral.STATUS_PENDING)


class ReverseReferralRewardTests(TestCase):
    def test_reversing_a_qualified_referral_flips_the_pending_entry_in_place(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699209")
        sub = _make_active_subscription(referred, tier)
        qualify_referral(referred, sub, net_amount_cents=10000)
        referral.refresh_from_db()

        result = reverse_referral_reward(referral, reason="refund")

        self.assertEqual(result.status, Referral.STATUS_REVERSED)
        self.assertEqual(get_reward_balance(referrer), {"available": 0, "pending": 0})
        entry = RewardLedgerEntry.objects.get(reference_type="referral", reference_id=referral.id)
        self.assertEqual(entry.status, RewardLedgerEntry.STATUS_REVERSED)
        self.assertEqual(RewardLedgerEntry.objects.filter(reversal_of=entry).count(), 0)

    def test_reversing_a_rewarded_referral_creates_a_compensating_entry_original_untouched(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699210")
        sub = _make_active_subscription(referred, tier)
        qualify_referral(referred, sub, net_amount_cents=10000)
        referral.refresh_from_db()
        confirm_referral_reward(referral)
        referral.refresh_from_db()

        result = reverse_referral_reward(referral, reason="chargeback")

        self.assertEqual(result.status, Referral.STATUS_REVERSED)
        self.assertEqual(get_reward_balance(referrer)["available"], 0)

        original = referral.reward_ledger_entry
        original.refresh_from_db()
        self.assertEqual(original.status, RewardLedgerEntry.STATUS_CONFIRMED)
        self.assertEqual(original.amount, 800)
        self.assertTrue(RewardLedgerEntry.objects.filter(reversal_of=original, amount=-800).exists())

    def test_reversal_is_idempotent(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699211")
        sub = _make_active_subscription(referred, tier)
        qualify_referral(referred, sub, net_amount_cents=10000)
        referral.refresh_from_db()
        confirm_referral_reward(referral)
        referral.refresh_from_db()
        original = referral.reward_ledger_entry

        reverse_referral_reward(referral, reason="chargeback")
        referral.refresh_from_db()
        result2 = reverse_referral_reward(referral, reason="chargeback retried")

        self.assertEqual(result2.status, Referral.STATUS_REVERSED)
        self.assertEqual(RewardLedgerEntry.objects.filter(reversal_of_id=original.id).count(), 1)


class RateSnapshotNotRecalculatedTests(TestCase):
    def test_changing_the_rate_after_qualification_does_not_affect_the_reward(self):
        referrer, referred, referral, tier = _setup_referral(rate_percent=Decimal("8.00"), phone_prefix="699212")
        sub = _make_active_subscription(referred, tier)
        qualify_referral(referred, sub, net_amount_cents=10000)
        referral.refresh_from_db()
        self.assertEqual(referral.reward_rate_percent_snapshot, Decimal("8.00"))

        config = ReferralRateConfig.objects.get(tier=tier)
        config.rate_percent = Decimal("20.00")
        config.save(update_fields=["rate_percent"])

        confirm_referral_reward(referral)
        referral.refresh_from_db()
        self.assertEqual(referral.reward_points_awarded, 800)  # unchanged — not recalculated at 20%


class SeedReferralRateConfigMigrationTests(TestCase):
    def test_seeds_all_six_ranks_with_the_confirmed_starting_rates(self):
        expected = {
            0: Decimal("2.00"), 1: Decimal("5.00"), 2: Decimal("8.00"),
            3: Decimal("10.00"), 4: Decimal("15.00"), 5: Decimal("20.00"),
        }
        # Other tests in this suite create AccountTier rows at these same
        # ranks (e.g. via _setup_referral); the real seed_referral_rates
        # function deterministically picks the OLDEST tier per rank
        # (order_by("created_at").first()), so clearing rank 0-5 first makes
        # this test's own tiers unambiguously the ones it matches, the same
        # way a real deploy only ever has one tier per rank.
        AccountTier.objects.filter(rank__in=expected.keys()).delete()
        for rank in expected:
            AccountTier.objects.create(name=f"MigTier{rank}", rank=rank, price_cents=100)

        from django.apps import apps as live_apps
        seed_referral_rates(live_apps, None)

        for rank, rate in expected.items():
            tier = AccountTier.objects.get(name=f"MigTier{rank}")
            config = ReferralRateConfig.objects.get(tier=tier)
            self.assertEqual(config.rate_percent, rate)


# ---------------------------------------------------------------------
# Phase 11: referral settlement sweep.
# ---------------------------------------------------------------------

def _qualify_and_backdate(referred, sub, *, net_amount_cents=10000, days_ago=REFERRAL_SETTLEMENT_WINDOW_DAYS + 1):
    referral = qualify_referral(referred, sub, net_amount_cents=net_amount_cents)
    Referral.objects.filter(pk=referral.pk).update(
        qualified_at=timezone.now() - timedelta(days=days_ago),
    )
    referral.refresh_from_db()
    return referral


class SweepSettleableReferralsTests(TestCase):
    def test_empty_table_is_a_clean_noop(self):
        self.assertEqual(sweep_settleable_referrals(), {"candidates": 0, "settled": 0, "errors": 0})

    def test_settles_only_referrals_past_the_window(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699300")
        sub = _make_active_subscription(referred, tier)
        past_window = _qualify_and_backdate(referred, sub)

        result = sweep_settleable_referrals()

        self.assertEqual(result, {"candidates": 1, "settled": 1, "errors": 0})
        past_window.refresh_from_db()
        self.assertEqual(past_window.status, Referral.STATUS_REWARDED)
        self.assertEqual(get_reward_balance(referrer)["available"], 800)

    def test_does_not_settle_a_referral_still_within_the_window(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699301")
        sub = _make_active_subscription(referred, tier)
        qualify_referral(referred, sub, net_amount_cents=10000)
        referral.refresh_from_db()  # qualified_at = now(), well within the window

        result = sweep_settleable_referrals()

        self.assertEqual(result, {"candidates": 0, "settled": 0, "errors": 0})
        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_QUALIFIED)

    def test_skips_a_qualified_referral_with_no_qualified_at(self):
        """Defensive case: pre-Phase-11 data the migration backfill
        couldn't reach (should not exist after the migration ran, but the
        sweep must not treat NULL as "immediately settleable")."""
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699302")
        sub = _make_active_subscription(referred, tier)
        qualify_referral(referred, sub, net_amount_cents=10000)
        Referral.objects.filter(pk=referral.pk).update(qualified_at=None)

        result = sweep_settleable_referrals()
        self.assertEqual(result, {"candidates": 0, "settled": 0, "errors": 0})

    def test_respects_limit(self):
        for i in range(3):
            referrer, referred, referral, tier = _setup_referral(phone_prefix=f"69931{i}")
            sub = _make_active_subscription(referred, tier)
            _qualify_and_backdate(referred, sub)

        result = sweep_settleable_referrals(limit=2)
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["settled"], 2)

    def test_one_failure_does_not_abort_the_batch(self):
        referrer1, referred1, referral1, tier1 = _setup_referral(phone_prefix="699320")
        sub1 = _make_active_subscription(referred1, tier1)
        good = _qualify_and_backdate(referred1, sub1)

        referrer2, referred2, referral2, tier2 = _setup_referral(phone_prefix="699321")
        sub2 = _make_active_subscription(referred2, tier2)
        bad = _qualify_and_backdate(referred2, sub2)

        real_confirm = confirm_referral_reward

        def flaky(referral, **kwargs):
            if referral.id == bad.id:
                raise RuntimeError("boom")
            return real_confirm(referral, **kwargs)

        with patch("apps.referrals.services.confirm_referral_reward", side_effect=flaky):
            result = sweep_settleable_referrals()

        self.assertEqual(result, {"candidates": 2, "settled": 1, "errors": 1})
        good.refresh_from_db()
        self.assertEqual(good.status, Referral.STATUS_REWARDED)


class SweepSettleableReferralsConcurrencyTests(TransactionTestCase):
    """Real threads: two concurrent sweep runs must not double-settle (and
    thus double-pay) the same referral — matching the Phase 5/6
    concurrency-testing standard for this project."""

    def test_concurrent_sweeps_settle_each_referral_exactly_once(self):
        referrer, referred, referral, tier = _setup_referral(phone_prefix="699330")
        sub = _make_active_subscription(referred, tier)
        past_window = _qualify_and_backdate(referred, sub)
        results = []

        def worker():
            try:
                results.append(sweep_settleable_referrals())
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total_errors = sum(r["errors"] for r in results)
        total_settled = sum(r["settled"] for r in results)
        self.assertEqual(total_errors, 0, "confirm_referral_reward's own row lock + status re-check must never raise under contention")
        # NOT necessarily 8: a sweep whose own candidate SELECT runs after
        # another sweep has already committed the REWARDED transition
        # legitimately sees zero candidates (nothing left to settle) rather
        # than calling confirm_referral_reward at all — the real guarantee
        # is "settled at least once, never erroring, never double-paid".
        self.assertGreaterEqual(total_settled, 1)
        self.assertEqual(get_reward_balance(referrer)["available"], 800, "reward is only ever paid out once, regardless of how many sweeps ran")
        past_window.refresh_from_db()
        self.assertEqual(past_window.status, Referral.STATUS_REWARDED)
