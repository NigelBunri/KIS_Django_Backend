"""
Phase 2 tests: ledger correctness, achievement/repeatable dedup,
immutability, reversal, and the LoyaltyPoint backfill migration.

Phase 5 tests (added below): the redemption ceiling engine
(calculate_redemption) and coin reservation lifecycle
(reserve/confirm/release_redemption), including real-concurrency
double-spend protection.

Run:
  python3 manage.py test apps.rewards --keepdb -v 2
"""
import importlib
import threading
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.tests_qa_full import make_verified_user
from apps.commerce.models import LoyaltyPoint

from .models import (
    AchievementDefinition,
    RedemptionPolicy,
    RepeatableRewardRule,
    RewardLedgerEntry,
)
from .services import (
    InsufficientRewardBalance,
    RedemptionPolicyViolation,
    calculate_redemption,
    confirm_ledger_entry,
    confirm_redemption,
    create_pending_entry,
    expire_ledger_entry,
    expire_reward_entries,
    get_reward_balance,
    grant_achievement,
    grant_promo_bonus,
    grant_repeatable,
    reconcile_rewards_and_referrals,
    release_redemption,
    reserve_redemption,
    reverse_ledger_entry,
)

_backfill_module = importlib.import_module("apps.rewards.migrations.0002_backfill_loyalty_points")
backfill_loyalty_points = _backfill_module.backfill_loyalty_points


class BalanceCalculationTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700001001")

    def _make(self, amount, status, **kwargs):
        return RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ADMIN_ADJUSTMENT,
            source="test", amount=amount, status=status, **kwargs,
        )

    def test_balance_sums_only_confirmed_and_redeemed(self):
        self._make(100, RewardLedgerEntry.STATUS_CONFIRMED)
        self._make(50, RewardLedgerEntry.STATUS_REDEEMED)
        self._make(9999, RewardLedgerEntry.STATUS_PENDING)
        self._make(500, RewardLedgerEntry.STATUS_REVERSED)
        self._make(500, RewardLedgerEntry.STATUS_EXPIRED)
        self._make(500, RewardLedgerEntry.STATUS_CANCELLED)

        balance = get_reward_balance(self.user)
        self.assertEqual(balance["available"], 150)
        self.assertEqual(balance["pending"], 9999)

    def test_zero_balance_for_user_with_no_entries(self):
        other = make_verified_user("+237700001002")
        balance = get_reward_balance(other)
        self.assertEqual(balance, {"available": 0, "pending": 0})


class AchievementGrantTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700001003")
        self.definition = AchievementDefinition.objects.create(
            code="profile_completion", title="Complete your profile", coin_amount=100,
        )

    def test_grants_once(self):
        entry = grant_achievement(self.user, "profile_completion")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.amount, 100)
        self.assertEqual(entry.status, RewardLedgerEntry.STATUS_CONFIRMED)

    def test_second_grant_is_a_noop(self):
        first = grant_achievement(self.user, "profile_completion")
        second = grant_achievement(self.user, "profile_completion")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(
            RewardLedgerEntry.objects.filter(user=self.user, source="profile_completion").count(), 1,
        )

    def test_unknown_code_is_a_noop(self):
        self.assertIsNone(grant_achievement(self.user, "does_not_exist"))

    def test_inactive_definition_is_a_noop(self):
        self.definition.is_active = False
        self.definition.save(update_fields=["is_active"])
        self.assertIsNone(grant_achievement(self.user, "profile_completion"))

    def test_redeeming_coins_does_not_reset_the_achievement(self):
        # Spending the coins (a separate ledger entry, not touching this one)
        # must not make the achievement grantable again.
        entry = grant_achievement(self.user, "profile_completion")
        RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REDEMPTION, source="subscription_discount",
            amount=-100, status=RewardLedgerEntry.STATUS_REDEEMED,
        )
        self.assertEqual(get_reward_balance(self.user)["available"], 0)
        self.assertIsNone(grant_achievement(self.user, "profile_completion"))
        self.assertEqual(
            RewardLedgerEntry.objects.filter(user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT).count(), 1,
        )


class RepeatableGrantTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700001004")
        self.rule = RepeatableRewardRule.objects.create(
            code="daily_login", title="Daily login", coin_amount=10,
            frequency=RepeatableRewardRule.FREQUENCY_DAILY, max_per_period=2,
        )

    def test_grants_up_to_max_per_period(self):
        first = grant_repeatable(self.user, "daily_login")
        second = grant_repeatable(self.user, "daily_login")
        third = grant_repeatable(self.user, "daily_login")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(third)
        self.assertEqual(
            RewardLedgerEntry.objects.filter(user=self.user, source="daily_login").count(), 2,
        )

    def test_new_period_allows_new_grants(self):
        day1 = datetime(2026, 1, 1, 10, 0, tzinfo=dt_timezone.utc)
        day2 = datetime(2026, 1, 2, 10, 0, tzinfo=dt_timezone.utc)

        with patch("apps.rewards.services.timezone.now", return_value=day1):
            grant_repeatable(self.user, "daily_login")
            grant_repeatable(self.user, "daily_login")
            self.assertIsNone(grant_repeatable(self.user, "daily_login"))

        with patch("apps.rewards.services.timezone.now", return_value=day2):
            self.assertIsNotNone(grant_repeatable(self.user, "daily_login"))

        self.assertEqual(
            RewardLedgerEntry.objects.filter(user=self.user, source="daily_login").count(), 3,
        )

    def test_per_event_rule_requires_event_id_and_grants_once_per_event(self):
        RepeatableRewardRule.objects.create(
            code="booking_review", title="Leave a review", coin_amount=25,
            frequency=RepeatableRewardRule.FREQUENCY_PER_EVENT, max_per_period=1,
        )
        with self.assertRaises(ValueError):
            grant_repeatable(self.user, "booking_review")

        first = grant_repeatable(self.user, "booking_review", event_id="booking-abc")
        second_same_event = grant_repeatable(self.user, "booking_review", event_id="booking-abc")
        third_new_event = grant_repeatable(self.user, "booking_review", event_id="booking-xyz")
        self.assertIsNotNone(first)
        self.assertIsNone(second_same_event)
        self.assertIsNotNone(third_new_event)


class ImmutabilityTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700001005")

    def test_pending_entry_can_be_freely_updated(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REFERRAL, source="referral",
            amount=200, status=RewardLedgerEntry.STATUS_PENDING,
        )
        entry.amount = 999  # still PENDING — allowed
        entry.save()
        entry.refresh_from_db()
        self.assertEqual(entry.amount, 999)

    def test_confirmed_entry_rejects_amount_change(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="profile_completion",
            amount=100, status=RewardLedgerEntry.STATUS_CONFIRMED,
        )
        entry.amount = 5000
        with self.assertRaises(ValueError):
            entry.save()

    def test_confirmed_entry_allows_status_and_metadata_change(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="profile_completion",
            amount=100, status=RewardLedgerEntry.STATUS_CONFIRMED,
        )
        entry.metadata = {"note": "annotated after the fact"}
        entry.save()  # must not raise
        entry.refresh_from_db()
        self.assertEqual(entry.metadata["note"], "annotated after the fact")

    def test_confirmed_entry_rejects_type_and_reference_change(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="profile_completion",
            amount=100, status=RewardLedgerEntry.STATUS_CONFIRMED,
        )
        entry.type = RewardLedgerEntry.TYPE_REFERRAL
        with self.assertRaises(ValueError):
            entry.save()


class ReversalTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700001006")

    def test_reversing_a_confirmed_entry_creates_a_compensating_row_and_leaves_original_intact(self):
        original = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REFERRAL, source="referral",
            amount=6000, status=RewardLedgerEntry.STATUS_CONFIRMED,
        )
        reversal = reverse_ledger_entry(original, reason="chargeback")

        original.refresh_from_db()
        self.assertEqual(original.amount, 6000)
        self.assertEqual(original.status, RewardLedgerEntry.STATUS_CONFIRMED)

        self.assertEqual(reversal.amount, -6000)
        self.assertEqual(reversal.reversal_of_id, original.id)
        self.assertEqual(reversal.type, RewardLedgerEntry.TYPE_REVERSAL)
        self.assertEqual(get_reward_balance(self.user)["available"], 0)

    def test_reversing_twice_is_idempotent(self):
        original = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REFERRAL, source="referral",
            amount=6000, status=RewardLedgerEntry.STATUS_CONFIRMED,
        )
        first = reverse_ledger_entry(original, reason="chargeback")
        second = reverse_ledger_entry(original, reason="chargeback retried")
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            RewardLedgerEntry.objects.filter(reversal_of=original).count(), 1,
        )

    def test_reversing_a_pending_entry_flips_status_in_place(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REDEMPTION, source="subscription_discount",
            amount=-300, status=RewardLedgerEntry.STATUS_PENDING,
        )
        result = reverse_ledger_entry(entry, reason="payment failed")
        self.assertEqual(result.id, entry.id)
        self.assertEqual(result.status, RewardLedgerEntry.STATUS_REVERSED)
        self.assertEqual(RewardLedgerEntry.objects.filter(reversal_of=entry).count(), 0)

    def test_reversing_an_already_reversed_pending_entry_is_a_noop(self):
        # Phase 6: was a raised ValueError until a real concurrency test
        # showed that's the one reversal path in the project that DIDN'T
        # idempotently no-op on a repeat call — closed for consistency with
        # every other reversal path (reverse_tier_upgrade_payment, the
        # settled branch of this same function).
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REDEMPTION, source="subscription_discount",
            amount=-300, status=RewardLedgerEntry.STATUS_REVERSED,
        )
        result = reverse_ledger_entry(entry, reason="duplicate reversal attempt")
        self.assertEqual(result.id, entry.id)
        self.assertEqual(result.status, RewardLedgerEntry.STATUS_REVERSED)


# ---------------------------------------------------------------------
# Phase 13: direct coverage for two functions previously only exercised
# indirectly through higher-level callers (confirm_redemption/
# confirm_referral_reward for confirm_ledger_entry; reserve_redemption/
# qualify_referral for create_pending_entry).
# ---------------------------------------------------------------------

class ConfirmLedgerEntryTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700001007")

    def test_confirms_a_pending_achievement_entry(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="test",
            amount=100, status=RewardLedgerEntry.STATUS_PENDING,
        )
        result = confirm_ledger_entry(entry)
        self.assertEqual(result.status, RewardLedgerEntry.STATUS_CONFIRMED)

    def test_confirms_a_pending_redemption_entry_as_redeemed_not_confirmed(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REDEMPTION, source="test",
            amount=-100, status=RewardLedgerEntry.STATUS_PENDING,
        )
        result = confirm_ledger_entry(entry)
        self.assertEqual(result.status, RewardLedgerEntry.STATUS_REDEEMED)

    def test_is_idempotent_for_an_already_confirmed_entry(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="test",
            amount=100, status=RewardLedgerEntry.STATUS_CONFIRMED,
        )
        result = confirm_ledger_entry(entry)
        self.assertEqual(result.id, entry.id)
        self.assertEqual(result.status, RewardLedgerEntry.STATUS_CONFIRMED)

    def test_is_idempotent_for_an_already_redeemed_entry(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REDEMPTION, source="test",
            amount=-100, status=RewardLedgerEntry.STATUS_REDEEMED,
        )
        result = confirm_ledger_entry(entry)
        self.assertEqual(result.id, entry.id)
        self.assertEqual(result.status, RewardLedgerEntry.STATUS_REDEEMED)

    def test_raises_for_a_reversed_entry(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="test",
            amount=100, status=RewardLedgerEntry.STATUS_REVERSED,
        )
        with self.assertRaises(ValueError):
            confirm_ledger_entry(entry)

    def test_raises_for_an_expired_entry(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="test",
            amount=100, status=RewardLedgerEntry.STATUS_EXPIRED,
        )
        with self.assertRaises(ValueError):
            confirm_ledger_entry(entry)

    def test_raises_for_a_cancelled_entry(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="test",
            amount=100, status=RewardLedgerEntry.STATUS_CANCELLED,
        )
        with self.assertRaises(ValueError):
            confirm_ledger_entry(entry)


class CreatePendingEntryTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700001008")

    def test_creates_a_pending_entry(self):
        entry = create_pending_entry(
            user=self.user, type=RewardLedgerEntry.TYPE_REFERRAL, source="referral",
            amount=500, idempotency_key="cpe-test-1",
        )
        self.assertEqual(entry.status, RewardLedgerEntry.STATUS_PENDING)
        self.assertEqual(entry.amount, 500)

    def test_is_idempotent_for_a_repeated_call_with_the_same_key(self):
        first = create_pending_entry(
            user=self.user, type=RewardLedgerEntry.TYPE_REFERRAL, source="referral",
            amount=500, idempotency_key="cpe-test-2",
        )
        second = create_pending_entry(
            user=self.user, type=RewardLedgerEntry.TYPE_REFERRAL, source="referral",
            amount=500, idempotency_key="cpe-test-2",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            RewardLedgerEntry.objects.filter(idempotency_key="cpe-test-2").count(), 1,
        )


class LoyaltyPointBackfillTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700001007")

    def _run_backfill(self):
        from django.apps import apps as live_apps
        backfill_loyalty_points(live_apps, None)

    def test_referral_prefixed_reason_maps_to_referral_type(self):
        lp = LoyaltyPoint.objects.create(
            user=self.user, points=200, earned_at=timezone.now(),
            reason=f"referral:{self.user.id}",
        )
        self._run_backfill()

        entry = RewardLedgerEntry.objects.get(metadata__legacy_loyalty_point_id=str(lp.id))
        self.assertEqual(entry.type, RewardLedgerEntry.TYPE_REFERRAL)
        self.assertEqual(entry.amount, 200)
        self.assertEqual(entry.status, RewardLedgerEntry.STATUS_CONFIRMED)
        self.assertEqual(entry.reference_type, "legacy_referral")

        # Original untouched.
        lp.refresh_from_db()
        self.assertEqual(lp.points, 200)

    def test_redemption_reason_maps_to_redemption_type(self):
        lp = LoyaltyPoint.objects.create(
            user=self.user, points=-50, earned_at=timezone.now(), reason="User redemption",
        )
        self._run_backfill()
        entry = RewardLedgerEntry.objects.get(metadata__legacy_loyalty_point_id=str(lp.id))
        self.assertEqual(entry.type, RewardLedgerEntry.TYPE_REDEMPTION)
        self.assertEqual(entry.amount, -50)

    def test_unrecognized_reason_maps_to_admin_adjustment_and_preserves_text(self):
        lp = LoyaltyPoint.objects.create(
            user=self.user, points=50, earned_at=timezone.now(), reason="Some legacy bonus",
        )
        self._run_backfill()
        entry = RewardLedgerEntry.objects.get(metadata__legacy_loyalty_point_id=str(lp.id))
        self.assertEqual(entry.type, RewardLedgerEntry.TYPE_ADMIN_ADJUSTMENT)
        self.assertEqual(entry.metadata["legacy_reason"], "Some legacy bonus")
        self.assertEqual(entry.description, "Some legacy bonus")

    def test_expires_at_is_preserved(self):
        expiry = timezone.now() + timezone.timedelta(days=30)
        lp = LoyaltyPoint.objects.create(
            user=self.user, points=10, earned_at=timezone.now(), expires_at=expiry, reason="promo",
        )
        self._run_backfill()
        entry = RewardLedgerEntry.objects.get(metadata__legacy_loyalty_point_id=str(lp.id))
        self.assertEqual(entry.expires_at, expiry)

    def test_running_backfill_twice_does_not_duplicate(self):
        LoyaltyPoint.objects.create(user=self.user, points=100, earned_at=timezone.now(), reason="x")
        self._run_backfill()
        self._run_backfill()
        self.assertEqual(
            RewardLedgerEntry.objects.filter(source="legacy_loyalty_point_backfill").count(), 1,
        )


class RedemptionPolicyDefaultsTests(TestCase):
    def test_default_policy_values_match_the_business_rules(self):
        policy = RedemptionPolicy.objects.create()
        self.assertEqual(policy.normal_max_discount_percent, 40)
        self.assertEqual(policy.absolute_max_discount_percent, 60)
        self.assertEqual(policy.min_cash_contribution_percent, 20)


class RedemptionPolicyAdminFormTests(TestCase):
    """Phase 12: the admin form must catch a misconfigured policy at save
    time rather than letting it through to fail loudly later, at checkout,
    inside calculate_redemption's own RedemptionPolicyViolation raise."""

    def _base_data(self, **overrides):
        data = {
            "context": "subscription_upgrade",
            "normal_max_discount_percent": "40.00",
            "absolute_max_discount_percent": "60.00",
            "min_cash_contribution_percent": "20.00",
            "coin_value_cents": "1.0000",
            "is_active": True,
        }
        data.update(overrides)
        return data

    def test_default_values_are_valid(self):
        from .admin import RedemptionPolicyForm
        form = RedemptionPolicyForm(data=self._base_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_absolute_max_plus_min_cash_over_100(self):
        from .admin import RedemptionPolicyForm
        form = RedemptionPolicyForm(data=self._base_data(
            absolute_max_discount_percent="90.00", min_cash_contribution_percent="20.00",
        ))
        self.assertFalse(form.is_valid())
        self.assertIn("absolute_max_discount_percent", form.errors)

    def test_rejects_normal_max_exceeding_absolute_max(self):
        from .admin import RedemptionPolicyForm
        form = RedemptionPolicyForm(data=self._base_data(
            normal_max_discount_percent="70.00", absolute_max_discount_percent="60.00",
        ))
        self.assertFalse(form.is_valid())
        self.assertIn("normal_max_discount_percent", form.errors)

    def test_rejects_percent_out_of_range(self):
        from .admin import RedemptionPolicyForm
        form = RedemptionPolicyForm(data=self._base_data(min_cash_contribution_percent="150.00"))
        self.assertFalse(form.is_valid())
        self.assertIn("min_cash_contribution_percent", form.errors)

    def test_rejects_negative_coin_value(self):
        from .admin import RedemptionPolicyForm
        form = RedemptionPolicyForm(data=self._base_data(coin_value_cents="-1.0000"))
        self.assertFalse(form.is_valid())
        self.assertIn("coin_value_cents", form.errors)


# ---------------------------------------------------------------------
# Phase 5: redemption ceiling engine
# ---------------------------------------------------------------------

def _grant_confirmed(user, amount, source="test"):
    return RewardLedgerEntry.objects.create(
        user=user, type=RewardLedgerEntry.TYPE_ADMIN_ADJUSTMENT, source=source,
        amount=amount, status=RewardLedgerEntry.STATUS_CONFIRMED,
    )


class CalculateRedemptionTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700002001")

    def test_no_active_policy_returns_zero_discount(self):
        quote = calculate_redemption(self.user, 10000)
        self.assertEqual(quote.coins_to_spend, 0)
        self.assertEqual(quote.discount_cents, 0)
        self.assertEqual(quote.payable_cents, 10000)

    def test_zero_gross_amount_returns_zero(self):
        RedemptionPolicy.objects.create()
        quote = calculate_redemption(self.user, 0)
        self.assertEqual(quote.payable_cents, 0)
        self.assertEqual(quote.discount_cents, 0)

    def test_normal_ceiling_caps_discount_when_balance_is_large(self):
        RedemptionPolicy.objects.create()
        _grant_confirmed(self.user, 100000)  # far more than enough

        quote = calculate_redemption(self.user, 10000)  # $100.00 gross

        self.assertEqual(quote.coins_to_spend, 4000)      # 40% ceiling
        self.assertEqual(quote.discount_cents, 4000)
        self.assertEqual(quote.payable_cents, 6000)

    def test_insufficient_balance_caps_discount_to_what_the_user_has(self):
        RedemptionPolicy.objects.create()
        _grant_confirmed(self.user, 1000)  # far less than the 40% ceiling

        quote = calculate_redemption(self.user, 10000)

        self.assertEqual(quote.coins_to_spend, 1000)
        self.assertEqual(quote.discount_cents, 1000)
        self.assertEqual(quote.payable_cents, 9000)

    def test_zero_balance_gives_zero_discount(self):
        RedemptionPolicy.objects.create()
        quote = calculate_redemption(self.user, 10000)
        self.assertEqual(quote.coins_to_spend, 0)
        self.assertEqual(quote.payable_cents, 10000)

    def test_absolute_ceiling_shared_with_an_already_applied_promo(self):
        RedemptionPolicy.objects.create()
        _grant_confirmed(self.user, 100000)

        # A promo already took 30% (3000 of 10000) before coins are applied.
        quote = calculate_redemption(self.user, 10000, already_discounted_cents=3000)

        # Absolute ceiling is 60% (6000); 3000 of that room is already used
        # by the promo, leaving 3000 for coins — NOT the normal 40% (4000).
        self.assertEqual(quote.discount_cents, 3000)
        self.assertEqual(quote.payable_cents, 4000)  # 10000 - 3000 - 3000

    def test_payable_never_negative_total_discount_capped_at_absolute_ceiling(self):
        RedemptionPolicy.objects.create()
        _grant_confirmed(self.user, 100000)

        # Promo alone already consumed the entire absolute ceiling.
        quote = calculate_redemption(self.user, 10000, already_discounted_cents=6000)

        self.assertEqual(quote.discount_cents, 0)  # no room left for coins
        self.assertEqual(quote.payable_cents, 4000)

    def test_misconfigured_policy_violating_min_cash_floor_raises(self):
        RedemptionPolicy.objects.create(
            normal_max_discount_percent=Decimal("90.00"),
            absolute_max_discount_percent=Decimal("95.00"),
            min_cash_contribution_percent=Decimal("20.00"),
        )
        _grant_confirmed(self.user, 100000)

        with self.assertRaises(RedemptionPolicyViolation):
            calculate_redemption(self.user, 10000)

    def test_custom_coin_value_is_respected(self):
        RedemptionPolicy.objects.create(coin_value_cents=Decimal("5.0000"))  # 1 coin = 5 cents
        _grant_confirmed(self.user, 100000)

        quote = calculate_redemption(self.user, 10000)

        self.assertEqual(quote.discount_cents, 4000)  # still capped at the 40% ceiling
        self.assertEqual(quote.coins_to_spend, 800)    # but costs fewer coins at 5c/coin


class ReserveConfirmReleaseRedemptionTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700002002")
        _grant_confirmed(self.user, 1000)

    def test_reserve_creates_a_pending_redemption_entry(self):
        entry = reserve_redemption(
            self.user, 400, reference_type="wallet_transaction", idempotency_key="redemption:test-1",
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.type, RewardLedgerEntry.TYPE_REDEMPTION)
        self.assertEqual(entry.amount, -400)
        self.assertEqual(entry.status, RewardLedgerEntry.STATUS_PENDING)
        # Not yet settled — available balance (CONFIRMED/REDEEMED only) is untouched.
        self.assertEqual(get_reward_balance(self.user)["available"], 1000)

    def test_reserve_zero_or_negative_is_a_noop(self):
        self.assertIsNone(reserve_redemption(self.user, 0, reference_type="x", idempotency_key="k1"))
        self.assertIsNone(reserve_redemption(self.user, -5, reference_type="x", idempotency_key="k2"))

    def test_reserve_raises_when_balance_is_insufficient(self):
        with self.assertRaises(InsufficientRewardBalance):
            reserve_redemption(self.user, 5000, reference_type="x", idempotency_key="k3")

    def test_confirm_settles_the_reservation_as_redeemed(self):
        entry = reserve_redemption(self.user, 400, reference_type="x", idempotency_key="k4")
        confirmed = confirm_redemption(entry)
        self.assertEqual(confirmed.status, RewardLedgerEntry.STATUS_REDEEMED)
        self.assertEqual(get_reward_balance(self.user)["available"], 600)  # 1000 - 400

    def test_release_restores_full_spendability(self):
        entry = reserve_redemption(self.user, 400, reference_type="x", idempotency_key="k5")
        released = release_redemption(entry, reason="payment_failed")
        self.assertEqual(released.status, RewardLedgerEntry.STATUS_REVERSED)
        self.assertEqual(get_reward_balance(self.user)["available"], 1000)  # untouched

        # The released coins are genuinely available again — a fresh
        # reservation for the full original balance must succeed.
        second = reserve_redemption(self.user, 1000, reference_type="x", idempotency_key="k6")
        self.assertIsNotNone(second)


class ReservationConcurrencyTests(TransactionTestCase):
    """Real threads + real Postgres row locking — proves reserve_redemption
    can't be double-spent by concurrent requests racing a stale balance
    read, the same class of bug already closed elsewhere in this project
    (QR tokens, referral qualification, promo redemption)."""

    def test_concurrent_reservations_never_exceed_the_available_balance(self):
        user = make_verified_user("+237700002003")
        _grant_confirmed(user, 1000)
        results = []

        def worker(i):
            try:
                results.append(reserve_redemption(
                    user, 300, reference_type="race", idempotency_key=f"redemption:race-{i}",
                ))
            except InsufficientRewardBalance:
                results.append(None)
            finally:
                connection.close()

        # 1000 available, 300 per attempt -> at most 3 can succeed (900),
        # never 4 (1200, which would overdraw the balance).
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r is not None]
        self.assertLessEqual(len(successes), 3)
        self.assertGreaterEqual(len(successes), 1)
        total_reserved = sum(-e.amount for e in successes)
        self.assertLessEqual(total_reserved, 1000)


class GrantPromoBonusTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700002004")

    def test_grants_coins_and_is_idempotent_per_user_and_code(self):
        first = grant_promo_bonus(self.user, "WELCOME10", 100)
        second = grant_promo_bonus(self.user, "WELCOME10", 100)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(get_reward_balance(self.user)["available"], 100)

    def test_zero_coins_is_a_noop(self):
        self.assertIsNone(grant_promo_bonus(self.user, "NOOP", 0))


# ---------------------------------------------------------------------
# Phase 6: concurrency audit — grant_achievement / grant_repeatable /
# grant_promo_bonus under real concurrent load, not just sequential calls.
# ---------------------------------------------------------------------

class AchievementConcurrencyTests(TransactionTestCase):
    def test_concurrent_grants_for_the_same_achievement_only_succeed_once(self):
        user = make_verified_user("+237700003001")
        AchievementDefinition.objects.create(code="p6_achievement", title="P6", coin_amount=50)
        results = []

        def worker():
            try:
                results.append(grant_achievement(user, "p6_achievement"))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 1)
        self.assertEqual(
            RewardLedgerEntry.objects.filter(user=user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT).count(), 1,
        )
        self.assertEqual(get_reward_balance(user)["available"], 50)


class RepeatableConcurrencyTests(TransactionTestCase):
    def test_concurrent_grants_never_exceed_max_per_period(self):
        user = make_verified_user("+237700003002")
        RepeatableRewardRule.objects.create(
            code="p6_repeatable", title="P6", coin_amount=10,
            frequency=RepeatableRewardRule.FREQUENCY_DAILY, max_per_period=3,
        )
        results = []

        def worker():
            try:
                results.append(grant_repeatable(user, "p6_repeatable"))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 3, "max_per_period=3 must cap concurrent grants at exactly 3")
        self.assertEqual(
            RewardLedgerEntry.objects.filter(user=user, type=RewardLedgerEntry.TYPE_REPEATABLE).count(), 3,
        )

    def test_concurrent_grants_for_distinct_per_event_ids_all_succeed(self):
        user = make_verified_user("+237700003003")
        RepeatableRewardRule.objects.create(
            code="p6_per_event", title="P6 Event", coin_amount=5,
            frequency=RepeatableRewardRule.FREQUENCY_PER_EVENT, max_per_period=1,
        )
        results = []

        def worker(event_id):
            try:
                results.append(grant_repeatable(user, "p6_per_event", event_id=event_id))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(f"evt-{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 8, "distinct event_ids must not contend with each other")


class GrantPromoBonusConcurrencyTests(TransactionTestCase):
    def test_direct_concurrent_calls_for_the_same_user_and_code_only_grant_once(self):
        """Defense in depth: grant_promo_bonus's own idempotency_key must
        hold even if called directly and concurrently, not only when
        protected by redeem_promo_code's outer PromoCode row lock."""
        user = make_verified_user("+237700003004")
        results = []

        def worker():
            try:
                results.append(grant_promo_bonus(user, "P6PROMO", 20))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 1)
        self.assertEqual(get_reward_balance(user)["available"], 20)


# ---------------------------------------------------------------------
# Phase 11: reward expiration sweep + reconciliation.
# ---------------------------------------------------------------------

def _grant_confirmed_expiring(user, amount, *, expires_at, source="test"):
    return RewardLedgerEntry.objects.create(
        user=user, type=RewardLedgerEntry.TYPE_ADMIN_ADJUSTMENT, source=source,
        amount=amount, status=RewardLedgerEntry.STATUS_CONFIRMED, expires_at=expires_at,
    )


class ExpireLedgerEntryTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700004001")

    def test_expiring_a_confirmed_entry_creates_a_compensating_expiration_entry(self):
        entry = _grant_confirmed_expiring(self.user, 500, expires_at=timezone.now())
        expiration = expire_ledger_entry(entry)

        self.assertEqual(expiration.type, RewardLedgerEntry.TYPE_EXPIRATION)
        self.assertEqual(expiration.amount, -500)
        self.assertEqual(expiration.status, RewardLedgerEntry.STATUS_CONFIRMED)
        self.assertEqual(expiration.reversal_of_id, entry.id)

        entry.refresh_from_db()
        self.assertEqual(entry.status, RewardLedgerEntry.STATUS_CONFIRMED, "original settled row is never mutated")
        self.assertEqual(get_reward_balance(self.user)["available"], 0)

    def test_is_idempotent_for_an_already_confirmed_entry(self):
        entry = _grant_confirmed_expiring(self.user, 500, expires_at=timezone.now())
        first = expire_ledger_entry(entry)
        second = expire_ledger_entry(entry)
        self.assertEqual(first.id, second.id)
        self.assertEqual(get_reward_balance(self.user)["available"], 0)

    def test_noops_on_a_redeemed_entry(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REDEMPTION, source="test",
            amount=-100, status=RewardLedgerEntry.STATUS_REDEEMED,
        )
        result = expire_ledger_entry(entry)
        self.assertEqual(result.id, entry.id)
        self.assertEqual(result.status, RewardLedgerEntry.STATUS_REDEEMED)

    def test_noops_on_a_pending_entry(self):
        entry = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="test",
            amount=100, status=RewardLedgerEntry.STATUS_PENDING,
        )
        result = expire_ledger_entry(entry)
        self.assertEqual(result.id, entry.id)
        self.assertEqual(result.status, RewardLedgerEntry.STATUS_PENDING)


class ExpireRewardEntriesSweepTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700004002")

    def test_empty_ledger_is_a_clean_noop(self):
        self.assertEqual(expire_reward_entries(), {"candidates": 0, "expired": 0, "errors": 0})

    def test_expires_only_entries_past_their_expiry(self):
        past = _grant_confirmed_expiring(self.user, 100, expires_at=timezone.now() - timezone.timedelta(days=1))
        future = _grant_confirmed_expiring(self.user, 200, expires_at=timezone.now() + timezone.timedelta(days=1))
        never = _grant_confirmed_expiring(self.user, 300, expires_at=None)

        result = expire_reward_entries()
        self.assertEqual(result, {"candidates": 1, "expired": 1, "errors": 0})

        past.refresh_from_db()
        future.refresh_from_db()
        never.refresh_from_db()
        self.assertEqual(get_reward_balance(self.user)["available"], 500, "only the past-due 100 was expired (net 0)")

    def test_never_touches_a_pending_redemption_reservation_even_with_a_past_expires_at(self):
        """Phase 13 cross-subsystem check: a live redemption reservation
        (reserve_redemption's PENDING TYPE_REDEMPTION entry) must never be
        swept up by the expiration sweep, which would incorrectly release
        coins mid-checkout out from under an in-flight payment. The status
        filter (CONFIRMED only) is what actually guarantees this — not the
        fact that reserve_redemption happens to never set expires_at today
        — so this test sets expires_at explicitly to prove the real
        invariant instead of trusting that coincidence to hold forever."""
        reservation = RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_REDEMPTION, source="subscription_discount",
            amount=-100, status=RewardLedgerEntry.STATUS_PENDING,
            expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        result = expire_reward_entries()
        self.assertEqual(result, {"candidates": 0, "expired": 0, "errors": 0})
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, RewardLedgerEntry.STATUS_PENDING)

    def test_respects_limit(self):
        for i in range(3):
            _grant_confirmed_expiring(self.user, 10, expires_at=timezone.now() - timezone.timedelta(days=1), source=f"e{i}")
        result = expire_reward_entries(limit=2)
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["expired"], 2)

    def test_one_failure_does_not_abort_the_batch(self):
        good = _grant_confirmed_expiring(self.user, 10, expires_at=timezone.now() - timezone.timedelta(days=1))
        bad = _grant_confirmed_expiring(self.user, 20, expires_at=timezone.now() - timezone.timedelta(days=1))

        real_expire = expire_ledger_entry

        def flaky(entry, **kwargs):
            if entry.id == bad.id:
                raise RuntimeError("boom")
            return real_expire(entry, **kwargs)

        with patch("apps.rewards.services.expire_ledger_entry", side_effect=flaky):
            result = expire_reward_entries()

        self.assertEqual(result, {"candidates": 2, "expired": 1, "errors": 1})
        good.refresh_from_db()


class ExpireRewardEntriesConcurrencyTests(TransactionTestCase):
    """Real threads: two concurrent sweep runs must not double-expire (and
    thus double-compensate) the same entry — matching the Phase 5/6
    concurrency-testing standard for this project."""

    def test_concurrent_sweeps_expire_each_entry_exactly_once(self):
        user = make_verified_user("+237700004003")
        entry = _grant_confirmed_expiring(user, 500, expires_at=timezone.now() - timezone.timedelta(days=1))
        results = []

        def worker():
            try:
                results.append(expire_reward_entries())
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total_errors = sum(r["errors"] for r in results)
        total_expired = sum(r["expired"] for r in results)
        self.assertEqual(total_errors, 0, "expire_ledger_entry must never raise under contention")
        # NOT necessarily 8: a sweep whose own candidate SELECT runs after
        # another sweep has already committed the expiration legitimately
        # sees zero candidates rather than calling expire_ledger_entry at
        # all — the real guarantee is "expired at least once, never
        # erroring, never double-compensated".
        self.assertGreaterEqual(total_expired, 1)
        self.assertEqual(
            RewardLedgerEntry.objects.filter(reversal_of=entry).count(), 1,
            "only one compensating TYPE_EXPIRATION row must ever exist for this entry",
        )
        self.assertEqual(get_reward_balance(user)["available"], 0)


class ReconcileRewardsAndReferralsTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700004004")

    def test_clean_ledger_has_no_anomalies(self):
        _grant_confirmed_expiring(self.user, 100, expires_at=None)
        result = reconcile_rewards_and_referrals()
        self.assertEqual(result["anomalies"], [])

    def test_respects_limit_argument(self):
        result = reconcile_rewards_and_referrals(limit=1)
        self.assertIn("checked", result)
        self.assertIn("anomalies", result)


class ReconcileRewardsAndReferralsAnomalyTests(TestCase):
    """Phase 12: an anomaly must be both returned AND written to AuditLog
    (queryable/admin-visible via apps.accounts.admin.AuditLogAdmin), not
    only logged."""

    def test_flags_and_audits_a_rewarded_referral_with_unsettled_ledger_entry(self):
        from apps.accounts.models import AuditLog
        from apps.referrals.models import Referral

        referrer = make_verified_user("+237700004010")
        referred = make_verified_user("+237700004011")
        entry = RewardLedgerEntry.objects.create(
            user=referrer, type=RewardLedgerEntry.TYPE_REFERRAL, source="referral",
            amount=100, status=RewardLedgerEntry.STATUS_PENDING,
        )
        Referral.objects.create(
            referrer=referrer, referred_user=referred, referral_code_used="TESTCODE12",
            status=Referral.STATUS_REWARDED, reward_ledger_entry=entry,
        )

        result = reconcile_rewards_and_referrals()

        kinds = [a["kind"] for a in result["anomalies"]]
        self.assertIn("referral_rewarded_but_ledger_not_settled", kinds)
        self.assertTrue(
            AuditLog.objects.filter(
                action="reward.reconciliation_anomaly",
                meta__kind="referral_rewarded_but_ledger_not_settled",
            ).exists()
        )

    def test_does_not_flag_a_correctly_reversed_referral_as_an_anomaly(self):
        """Phase 13 cross-subsystem check: reverse_referral_reward's normal,
        correct output (original ledger entry left CONFIRMED, a compensating
        TYPE_REVERSAL entry created) must NOT itself look like check #3's
        anomaly pattern (REVERSED referral + still-CONFIRMED entry with no
        compensating row) — proving the reconciliation check has no false
        positive on the everyday-correct case, not just that it catches the
        broken one."""
        from decimal import Decimal

        from apps.accounts.models import AccountTier, Subscription
        from apps.referrals.models import ReferralCode, ReferralRateConfig
        from apps.referrals.services import (
            confirm_referral_reward,
            qualify_referral,
            register_referral,
            reverse_referral_reward,
        )

        referrer = make_verified_user("+237700004012")
        tier = AccountTier.objects.create(name="Phase13ReconcileTier", rank=42, price_cents=5000)
        ReferralRateConfig.objects.create(tier=tier, rate_percent=Decimal("8.00"), is_active=True)
        Subscription.objects.create(
            user=referrer, tier=tier, status=Subscription.STATUS_ACTIVE,
            started_at=timezone.now(), ends_at=timezone.now() + timezone.timedelta(days=30),
        )
        code = ReferralCode.get_or_create_for_user(referrer)
        referred = make_verified_user("+237700004013")
        register_referral(referred_user=referred, referral_code=code.code)
        sub = Subscription.objects.create(
            user=referred, tier=tier, status=Subscription.STATUS_ACTIVE,
            started_at=timezone.now(), ends_at=timezone.now() + timezone.timedelta(days=30),
        )
        referral = qualify_referral(referred, sub, net_amount_cents=10000)
        confirm_referral_reward(referral)
        referral.refresh_from_db()
        reverse_referral_reward(referral, reason="test_chargeback")

        result = reconcile_rewards_and_referrals()

        kinds = [a["kind"] for a in result["anomalies"]]
        self.assertNotIn("referral_reversed_but_ledger_not_reversed", kinds)
        self.assertEqual(
            [a for a in result["anomalies"] if a.get("referral_id") == str(referral.id)], [],
        )
