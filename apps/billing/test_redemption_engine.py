"""
Phase 5 — wiring the redemption ceiling engine and referral qualification
into the real upgrade flow (WalletViewSet.upgrade, FlutterwaveWebhookView,
reverse_tier_upgrade_payment).

Run:
  python3 manage.py test apps.billing.test_redemption_engine --keepdb -v 2
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription
from apps.billing.models import WalletTransaction
from apps.billing.services import apply_tier_upgrade, reverse_tier_upgrade_payment
from apps.referrals.models import Referral, ReferralCode, ReferralRateConfig
from apps.referrals.services import register_referral
from apps.rewards.models import RewardLedgerEntry, RedemptionPolicy
from apps.rewards.services import get_reward_balance

User = get_user_model()


def _api_url(route_name: str) -> str:
    url = reverse(route_name)
    return url if url.endswith("/") else f"{url}/"


def _make_user(phone: str) -> User:
    return User.objects.create_user(phone=phone, country="CM", password="pass1234")


def _make_tier(name: str, price_cents: int, rank: int) -> AccountTier:
    return AccountTier.objects.create(name=name, price_cents=price_cents, rank=rank, billing_period_days=30)


def _grant_confirmed(user, amount):
    return RewardLedgerEntry.objects.create(
        user=user, type=RewardLedgerEntry.TYPE_ADMIN_ADJUSTMENT, source="test",
        amount=amount, status=RewardLedgerEntry.STATUS_CONFIRMED,
    )


@override_settings(SECURE_SSL_REDIRECT=False)
class MockUpgradeWithRewardsTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699400001")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        RedemptionPolicy.objects.create()

    def test_apply_rewards_omitted_charges_full_price_no_reservation(self):
        """Backward compatibility: omitting apply_rewards must behave
        exactly as before this phase."""
        tier = _make_tier("RE Mock Full", 10000, 1)
        _grant_confirmed(self.user, 100000)

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "card", "mock": True},
            format="json", secure=True,
        )
        self.assertEqual(res.status_code, 200, res.data)
        tx = WalletTransaction.objects.get(tx_ref=res.data["tx_ref"])
        self.assertEqual(tx.amount_cents, 10000)
        self.assertNotIn("redemption_entry_id", tx.meta)
        self.assertEqual(get_reward_balance(self.user)["available"], 100000)  # untouched

    def test_apply_rewards_discounts_the_charge_and_confirms_the_redemption(self):
        tier = _make_tier("RE Mock Discount", 10000, 1)
        _grant_confirmed(self.user, 100000)

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "card", "mock": True, "apply_rewards": True},
            format="json", secure=True,
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["payable_cents"], 6000)   # 40% ceiling
        self.assertEqual(res.data["discount_cents"], 4000)
        self.assertEqual(res.data["coins_applied"], 4000)

        tx = WalletTransaction.objects.get(tx_ref=res.data["tx_ref"])
        self.assertEqual(tx.amount_cents, 6000)

        reservation = RewardLedgerEntry.objects.get(id=tx.meta["redemption_entry_id"])
        self.assertEqual(reservation.status, RewardLedgerEntry.STATUS_REDEEMED)
        self.assertEqual(reservation.amount, -4000)
        self.assertEqual(get_reward_balance(self.user)["available"], 96000)  # 100000 - 4000

    def test_apply_rewards_with_insufficient_coins_caps_the_discount_not_an_error(self):
        tier = _make_tier("RE Mock Small Balance", 10000, 1)
        _grant_confirmed(self.user, 500)  # far less than the 40% ceiling (4000)

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "card", "mock": True, "apply_rewards": True},
            format="json", secure=True,
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["coins_applied"], 500)
        self.assertEqual(res.data["payable_cents"], 9500)
        self.assertEqual(get_reward_balance(self.user)["available"], 0)

    def test_apply_rewards_with_zero_balance_charges_full_price(self):
        tier = _make_tier("RE Mock Zero Balance", 10000, 1)
        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "card", "mock": True, "apply_rewards": True},
            format="json", secure=True,
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["coins_applied"], 0)
        self.assertEqual(res.data["payable_cents"], 10000)


@override_settings(SECURE_SSL_REDIRECT=False, FLW_WEBHOOK_SECRET="test-webhook-secret")
class RealFlutterwaveUpgradeWebhookWiringTests(TestCase):
    """Non-mock path: upgrade creates a pending transaction + reservation;
    the webhook (not the upgrade call) is what actually applies the tier
    and settles the reservation — matches how a real Flutterwave payment
    completes asynchronously."""

    def setUp(self):
        self.referrer = _make_user("+237699400010")
        self.referred = _make_user("+237699400011")
        self.tier = _make_tier("RE Webhook Business", 10000, 2)

        # The referral rate is looked up against the REFERRER's own current
        # tier, not the tier the referred user is purchasing — a separate
        # AccountTier/Subscription/ReferralRateConfig from self.tier.
        self.referrer_tier = _make_tier("RE Webhook Referrer Tier", 5000, 1)
        ReferralRateConfig.objects.create(tier=self.referrer_tier, rate_percent=Decimal("8.00"))
        Subscription.objects.create(
            user=self.referrer, tier=self.referrer_tier, status=Subscription.STATUS_ACTIVE,
        )

        code = ReferralCode.get_or_create_for_user(self.referrer)
        self.referral = register_referral(referred_user=self.referred, referral_code=code.code)
        RedemptionPolicy.objects.create()
        _grant_confirmed(self.referred, 100000)

        self.client = APIClient()
        self.client.force_authenticate(self.referred)

    def _post_webhook(self, tx_ref, status_flag="successful"):
        return self.client.post(
            "/api/v1/wallet/webhook/flutterwave/",
            {"data": {"tx_ref": tx_ref, "status": status_flag, "id": "flw-evt-re-1", "currency": "USD"}},
            format="json", HTTP_VERIF_HASH="test-webhook-secret", secure=True,
        )

    def _start_upgrade(self, apply_rewards=True):
        with patch("apps.billing.views._ensure_payments_ready"), \
             patch(
                 "apps.billing.views._flutterwave_payment_link",
                 return_value={"data": {"link": "https://pay.example/xyz"}},
             ):
            res = self.client.post(
                _api_url("wallet-upgrade"),
                {"tier": str(self.tier.id), "payment_method": "card", "apply_rewards": apply_rewards},
                format="json", secure=True,
            )
        return res

    def test_reservation_is_pending_until_webhook_confirms(self):
        res = self._start_upgrade()
        self.assertEqual(res.status_code, 200, res.data)
        tx = WalletTransaction.objects.get(tx_ref=res.data["tx_ref"])
        # Redemption discount is driven by the REFERRED user's own coin
        # balance/ceiling (40% of 10000 = 4000), unrelated to the
        # referrer's 8% referral rate — two independent numbers.
        self.assertEqual(tx.amount_cents, 6000)

        reservation = RewardLedgerEntry.objects.get(id=tx.meta["redemption_entry_id"])
        self.assertEqual(reservation.status, RewardLedgerEntry.STATUS_PENDING)
        self.assertFalse(WalletTransaction.objects.get(tx_ref=tx.tx_ref).status == "success")

        self._post_webhook(tx.tx_ref)

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, RewardLedgerEntry.STATUS_REDEEMED)
        self.assertEqual(get_reward_balance(self.referred)["available"], 100000 + reservation.amount)

    def test_referral_qualifies_on_webhook_success_with_net_discounted_amount(self):
        res = self._start_upgrade()
        tx_ref = res.data["tx_ref"]

        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_PENDING)

        self._post_webhook(tx_ref)

        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_QUALIFIED)
        self.assertEqual(self.referral.qualifying_net_amount_cents, 6000)  # discounted, not gross 10000
        self.assertEqual(self.referral.reward_rate_percent_snapshot, Decimal("8.00"))
        self.assertEqual(self.referral.reward_ledger_entry.amount, 480)  # 6000 * 8%

    def test_webhook_failure_releases_the_reservation(self):
        res = self._start_upgrade()
        tx = WalletTransaction.objects.get(tx_ref=res.data["tx_ref"])
        reservation = RewardLedgerEntry.objects.get(id=tx.meta["redemption_entry_id"])

        self._post_webhook(tx.tx_ref, status_flag="failed")

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, RewardLedgerEntry.STATUS_REVERSED)
        self.assertEqual(get_reward_balance(self.referred)["available"], 100000)  # fully restored

        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_PENDING)  # never qualified


class DirectReversalWiringTests(TestCase):
    """Service-layer tests for reverse_tier_upgrade_payment's Phase 5
    additions — the referral fixtures in test_subscription_lifecycle.py
    already cover the pre-existing tier-revert behavior (including its
    known name__iexact="Free" bug, unrelated to this phase); these tests
    isolate just the new redemption/referral reversal wiring."""

    def setUp(self):
        self.free = AccountTier.objects.create(name="RE Reversal Free", price_cents=0, rank=0)
        self.referrer = _make_user("+237699400020")
        self.referred = _make_user("+237699400021")
        self.tier = _make_tier("RE Reversal Business", 10000, 2)

        self.referrer_tier = _make_tier("RE Reversal Referrer Tier", 5000, 1)
        ReferralRateConfig.objects.create(tier=self.referrer_tier, rate_percent=Decimal("8.00"))
        Subscription.objects.create(
            user=self.referrer, tier=self.referrer_tier, status=Subscription.STATUS_ACTIVE,
        )

        code = ReferralCode.get_or_create_for_user(self.referrer)
        self.referral = register_referral(referred_user=self.referred, referral_code=code.code)
        RedemptionPolicy.objects.create()
        _grant_confirmed(self.referred, 100000)

    def _pay_with_rewards(self):
        from apps.rewards.services import calculate_redemption, reserve_redemption, confirm_redemption
        from apps.referrals.services import qualify_referral

        tx_ref = "kis_upgrade_re_reversal_test"
        quote = calculate_redemption(self.referred, self.tier.price_cents)
        tx = WalletTransaction.objects.create(
            user=self.referred, provider="flutterwave", method="card",
            amount_cents=quote.payable_cents, currency="USD", status="success",
            tx_ref=tx_ref,
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id)},
        )
        reservation = reserve_redemption(
            self.referred, quote.coins_to_spend, reference_type="wallet_transaction",
            reference_id=tx.id, idempotency_key=f"redemption:{tx_ref}",
        )
        tx.meta = {**tx.meta, "redemption_entry_id": str(reservation.id)}
        tx.save(update_fields=["meta"])
        confirm_redemption(reservation)

        new_sub = apply_tier_upgrade(
            user=self.referred, tier=self.tier, source="flutterwave",
            amount_cents=quote.payable_cents, reference=tx_ref,
        )
        qualify_referral(self.referred, new_sub, net_amount_cents=quote.payable_cents)
        return tx

    def test_reversal_restores_coins_and_reverses_a_qualified_referral(self):
        tx = self._pay_with_rewards()
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_QUALIFIED)
        balance_after_payment = get_reward_balance(self.referred)["available"]

        reverse_tier_upgrade_payment(transaction_obj=tx, reason="customer requested", event_type="refund")

        self.assertEqual(
            get_reward_balance(self.referred)["available"], balance_after_payment + 4000,
            "the redemption discount coins must come back",
        )
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_REVERSED)

    def test_reversal_reverses_a_rewarded_referral_via_compensating_entry(self):
        from apps.referrals.services import confirm_referral_reward

        tx = self._pay_with_rewards()
        self.referral.refresh_from_db()
        confirm_referral_reward(self.referral)
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_REWARDED)

        referrer_balance_before = get_reward_balance(self.referrer)["available"]
        self.assertGreater(referrer_balance_before, 0)

        reverse_tier_upgrade_payment(transaction_obj=tx, reason="chargeback", event_type="chargeback")

        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_REVERSED)
        self.assertEqual(get_reward_balance(self.referrer)["available"], 0)
        # Original reward entry is untouched — a compensating row did the work.
        original = self.referral.reward_ledger_entry
        original.refresh_from_db()
        self.assertEqual(original.status, RewardLedgerEntry.STATUS_CONFIRMED)
        self.assertTrue(RewardLedgerEntry.objects.filter(reversal_of=original).exists())

    def test_reversal_is_idempotent_with_redemption_and_referral_present(self):
        tx = self._pay_with_rewards()
        reverse_tier_upgrade_payment(transaction_obj=tx, reason="x", event_type="refund")
        balance_after_first = get_reward_balance(self.referred)["available"]

        result = reverse_tier_upgrade_payment(transaction_obj=tx, reason="x retried", event_type="refund")

        self.assertTrue(result.get("already_reversed"))
        self.assertEqual(get_reward_balance(self.referred)["available"], balance_after_first)
        # Exactly the original reservation (now REDEEMED) plus its ONE
        # compensating reversal row (which copies reference_type/
        # reference_id from the original by design) — not a third row from
        # the retried reversal call.
        self.assertEqual(
            RewardLedgerEntry.objects.filter(reference_type="wallet_transaction", reference_id=tx.id).count(), 2,
        )
        self.assertEqual(
            RewardLedgerEntry.objects.filter(
                reference_type="wallet_transaction", reference_id=tx.id, type=RewardLedgerEntry.TYPE_REVERSAL,
            ).count(),
            1,
            "no duplicate reversal of the redemption",
        )
