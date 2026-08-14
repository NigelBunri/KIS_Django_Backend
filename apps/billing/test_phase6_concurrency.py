"""
Phase 6 — concurrency/idempotency audit of Phase 2-5 code under real
concurrent load (real threads, real Postgres locking), not just sequential
calls. Only closes gaps that a real test proves exist.

Run:
  python3 manage.py test apps.billing.test_phase6_concurrency --keepdb -v 2
"""
from __future__ import annotations

import threading
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.accounts.models import AccountTier, Subscription
from apps.billing.models import WalletTransaction
from apps.billing.services import reverse_tier_upgrade_payment
from apps.referrals.models import Referral, ReferralCode, ReferralRateConfig
from apps.referrals.services import register_referral
from apps.rewards.models import RewardLedgerEntry, RedemptionPolicy
from apps.rewards.services import calculate_redemption, confirm_redemption, get_reward_balance, reserve_redemption
from apps.billing.services import apply_tier_upgrade
from apps.referrals.services import qualify_referral, confirm_referral_reward

User = get_user_model()


def _make_user(phone: str) -> User:
    return User.objects.create_user(phone=phone, country="CM", password="pass1234")


def _make_tier(name: str, price_cents: int, rank: int) -> AccountTier:
    return AccountTier.objects.create(name=name, price_cents=price_cents, rank=rank, billing_period_days=30)


def _grant_confirmed(user, amount):
    return RewardLedgerEntry.objects.create(
        user=user, type=RewardLedgerEntry.TYPE_ADMIN_ADJUSTMENT, source="test",
        amount=amount, status=RewardLedgerEntry.STATUS_CONFIRMED,
    )


@override_settings(SECURE_SSL_REDIRECT=False, FLW_WEBHOOK_SECRET="p6-webhook-secret")
class ConcurrentWebhookRedeliveryTests(TransactionTestCase):
    """Item 1: does a redelivered/duplicated "successful" webhook event,
    arriving genuinely concurrently, ever double-process the redemption
    reservation or double-qualify the referral it's tied to?"""

    def _webhook_payload(self, tx_ref):
        return {"data": {"tx_ref": tx_ref, "status": "successful", "id": "flw-evt-p6", "currency": "USD"}}

    def test_concurrent_redelivery_confirms_the_redemption_exactly_once(self):
        from rest_framework.test import APIClient

        user = _make_user("+237699500001")
        tier = _make_tier("P6 Webhook Tier", 10000, 1)
        RedemptionPolicy.objects.create()
        _grant_confirmed(user, 100000)

        quote = calculate_redemption(user, tier.price_cents)
        tx_ref = "kis_upgrade_p6_webhook_race"
        tx = WalletTransaction.objects.create(
            user=user, provider="flutterwave", method="card", amount_cents=quote.payable_cents,
            currency="USD", status="pending", tx_ref=tx_ref,
            meta={"intent": "tier_upgrade", "tier_id": str(tier.id)},
        )
        reservation = reserve_redemption(
            user, quote.coins_to_spend, reference_type="wallet_transaction",
            reference_id=tx.id, idempotency_key=f"redemption:{tx_ref}",
        )
        tx.meta = {**tx.meta, "redemption_entry_id": str(reservation.id)}
        tx.save(update_fields=["meta"])

        results = []

        def worker():
            client = APIClient()
            try:
                res = client.post(
                    "/api/v1/wallet/webhook/flutterwave/", self._webhook_payload(tx_ref),
                    format="json", HTTP_VERIF_HASH="p6-webhook-secret", secure=True,
                )
                results.append(res.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(code == 200 for code in results), results)

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, RewardLedgerEntry.STATUS_REDEEMED)
        # No compensating/duplicate ledger rows from a double-confirm.
        self.assertEqual(
            RewardLedgerEntry.objects.filter(reference_type="wallet_transaction", reference_id=tx.id).count(), 1,
        )
        self.assertEqual(
            Subscription.objects.filter(user=user, status=Subscription.STATUS_ACTIVE).count(), 1,
        )

    def test_concurrent_redelivery_qualifies_the_referral_exactly_once(self):
        from rest_framework.test import APIClient

        referrer = _make_user("+237699500002")
        referred = _make_user("+237699500003")
        referrer_tier = _make_tier("P6 Webhook Referrer Tier", 5000, 1)
        ReferralRateConfig.objects.create(tier=referrer_tier, rate_percent=Decimal("8.00"))
        Subscription.objects.create(user=referrer, tier=referrer_tier, status=Subscription.STATUS_ACTIVE)
        code = ReferralCode.get_or_create_for_user(referrer)
        referral = register_referral(referred_user=referred, referral_code=code.code)

        tier = _make_tier("P6 Webhook Purchase Tier", 10000, 2)
        tx_ref = "kis_upgrade_p6_referral_race"
        WalletTransaction.objects.create(
            user=referred, provider="flutterwave", method="card", amount_cents=10000,
            currency="USD", status="pending", tx_ref=tx_ref,
            meta={"intent": "tier_upgrade", "tier_id": str(tier.id)},
        )

        results = []

        def worker():
            client = APIClient()
            try:
                res = client.post(
                    "/api/v1/wallet/webhook/flutterwave/", self._webhook_payload(tx_ref),
                    format="json", HTTP_VERIF_HASH="p6-webhook-secret", secure=True,
                )
                results.append(res.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(code == 200 for code in results), results)

        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_QUALIFIED)
        self.assertEqual(
            RewardLedgerEntry.objects.filter(user=referrer, type=RewardLedgerEntry.TYPE_REFERRAL).count(), 1,
        )
        self.assertEqual(
            Subscription.objects.filter(user=referred, status=Subscription.STATUS_ACTIVE).count(), 1,
        )


class ConcurrentReversalTests(TransactionTestCase):
    """Item 2: two simultaneous reversal attempts for the same payment must
    reverse the redemption and the referral reward exactly once each."""

    def test_concurrent_reversal_only_reverses_once(self):
        free = AccountTier.objects.create(name="P6 Reversal Free", price_cents=0, rank=0)
        referrer = _make_user("+237699500010")
        referred = _make_user("+237699500011")
        referrer_tier = _make_tier("P6 Reversal Referrer Tier", 5000, 1)
        ReferralRateConfig.objects.create(tier=referrer_tier, rate_percent=Decimal("8.00"))
        Subscription.objects.create(user=referrer, tier=referrer_tier, status=Subscription.STATUS_ACTIVE)
        code = ReferralCode.get_or_create_for_user(referrer)
        referral = register_referral(referred_user=referred, referral_code=code.code)

        tier = _make_tier("P6 Reversal Purchase Tier", 10000, 2)
        RedemptionPolicy.objects.create()
        _grant_confirmed(referred, 100000)

        quote = calculate_redemption(referred, tier.price_cents)
        tx_ref = "kis_upgrade_p6_reversal_race"
        tx = WalletTransaction.objects.create(
            user=referred, provider="flutterwave", method="card", amount_cents=quote.payable_cents,
            currency="USD", status="success", tx_ref=tx_ref,
            meta={"intent": "tier_upgrade", "tier_id": str(tier.id)},
        )
        reservation = reserve_redemption(
            referred, quote.coins_to_spend, reference_type="wallet_transaction",
            reference_id=tx.id, idempotency_key=f"redemption:{tx_ref}",
        )
        tx.meta = {**tx.meta, "redemption_entry_id": str(reservation.id)}
        tx.save(update_fields=["meta"])
        confirm_redemption(reservation)
        new_sub = apply_tier_upgrade(
            user=referred, tier=tier, source="flutterwave",
            amount_cents=quote.payable_cents, reference=tx_ref,
        )
        referral_row = qualify_referral(referred, new_sub, net_amount_cents=quote.payable_cents)
        confirm_referral_reward(referral_row)

        results = []

        def worker():
            try:
                results.append(reverse_tier_upgrade_payment(
                    transaction_obj=tx, reason="concurrent test", event_type="refund",
                ))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one worker did the real reversal; the rest observed
        # already_reversed.
        already_reversed_count = sum(1 for r in results if r and r.get("already_reversed"))
        real_reversal_count = sum(1 for r in results if r and not r.get("already_reversed"))
        self.assertEqual(real_reversal_count, 1)
        self.assertEqual(already_reversed_count, 7)

        # Exactly one compensating reversal row for the redemption.
        self.assertEqual(
            RewardLedgerEntry.objects.filter(
                reference_type="wallet_transaction", reference_id=tx.id, type=RewardLedgerEntry.TYPE_REVERSAL,
            ).count(),
            1,
        )
        # Exactly one compensating reversal row for the referral reward.
        original_referral_entry = referral_row.reward_ledger_entry
        self.assertEqual(
            RewardLedgerEntry.objects.filter(reversal_of=original_referral_entry).count(), 1,
        )
        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_REVERSED)


@override_settings(SECURE_SSL_REDIRECT=False, FLW_WEBHOOK_SECRET="p6-webhook-secret")
class OutOfOrderWebhookDeliveryTests(TransactionTestCase):
    """Item 5 finding: the failed/cancelled webhook branch had no lock, no
    idempotency guard, and no check against an already-"success" status —
    unlike the successful branch a few lines above it. A late/out-of-order
    "failed" event for a tx_ref that ALREADY processed successfully would
    incorrectly release the (already-settled) redemption reservation,
    clawing back coins from a genuinely completed upgrade. Real payment
    providers explicitly do not guarantee webhook delivery order."""

    def test_late_failed_event_after_a_successful_one_does_not_reverse_the_completed_redemption(self):
        from rest_framework.test import APIClient

        user = _make_user("+237699500020")
        tier = _make_tier("P6 Out Of Order Tier", 10000, 1)
        RedemptionPolicy.objects.create()
        _grant_confirmed(user, 100000)

        quote = calculate_redemption(user, tier.price_cents)
        tx_ref = "kis_upgrade_p6_out_of_order"
        tx = WalletTransaction.objects.create(
            user=user, provider="flutterwave", method="card", amount_cents=quote.payable_cents,
            currency="USD", status="pending", tx_ref=tx_ref,
            meta={"intent": "tier_upgrade", "tier_id": str(tier.id)},
        )
        reservation = reserve_redemption(
            user, quote.coins_to_spend, reference_type="wallet_transaction",
            reference_id=tx.id, idempotency_key=f"redemption:{tx_ref}",
        )
        tx.meta = {**tx.meta, "redemption_entry_id": str(reservation.id)}
        tx.save(update_fields=["meta"])

        client = APIClient()
        res_success = client.post(
            "/api/v1/wallet/webhook/flutterwave/",
            {"data": {"tx_ref": tx_ref, "status": "successful", "id": "flw-evt-1", "currency": "USD"}},
            format="json", HTTP_VERIF_HASH="p6-webhook-secret", secure=True,
        )
        self.assertEqual(res_success.status_code, 200)

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, RewardLedgerEntry.STATUS_REDEEMED)
        balance_after_success = get_reward_balance(user)["available"]

        # A stale/duplicate/out-of-order "failed" event for the SAME
        # tx_ref arrives after the successful one already completed.
        res_failed = client.post(
            "/api/v1/wallet/webhook/flutterwave/",
            {"data": {"tx_ref": tx_ref, "status": "failed", "id": "flw-evt-2-late", "currency": "USD"}},
            format="json", HTTP_VERIF_HASH="p6-webhook-secret", secure=True,
        )
        self.assertEqual(res_failed.status_code, 200)

        reservation.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(
            reservation.status, RewardLedgerEntry.STATUS_REDEEMED,
            "a late failed/cancelled event must never reverse an already-successful redemption",
        )
        self.assertEqual(get_reward_balance(user)["available"], balance_after_success)
        self.assertEqual(tx.status, "success", "the transaction's own success status must not be overwritten")

    def test_concurrent_duplicate_failed_events_release_the_reservation_exactly_once(self):
        from rest_framework.test import APIClient

        user = _make_user("+237699500021")
        tier = _make_tier("P6 Duplicate Failed Tier", 10000, 1)
        RedemptionPolicy.objects.create()
        _grant_confirmed(user, 100000)

        quote = calculate_redemption(user, tier.price_cents)
        tx_ref = "kis_upgrade_p6_dup_failed"
        tx = WalletTransaction.objects.create(
            user=user, provider="flutterwave", method="card", amount_cents=quote.payable_cents,
            currency="USD", status="pending", tx_ref=tx_ref,
            meta={"intent": "tier_upgrade", "tier_id": str(tier.id)},
        )
        reservation = reserve_redemption(
            user, quote.coins_to_spend, reference_type="wallet_transaction",
            reference_id=tx.id, idempotency_key=f"redemption:{tx_ref}",
        )
        tx.meta = {**tx.meta, "redemption_entry_id": str(reservation.id)}
        tx.save(update_fields=["meta"])

        results = []

        def worker():
            client = APIClient()
            try:
                res = client.post(
                    "/api/v1/wallet/webhook/flutterwave/",
                    {"data": {"tx_ref": tx_ref, "status": "failed", "id": "flw-evt-dup", "currency": "USD"}},
                    format="json", HTTP_VERIF_HASH="p6-webhook-secret", secure=True,
                )
                results.append(res.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(code == 200 for code in results), results)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, RewardLedgerEntry.STATUS_REVERSED)
        self.assertEqual(get_reward_balance(user)["available"], 100000)
        # No duplicate reversal bookkeeping (retry_count must not have been
        # incremented 8 times either — proves the whole branch is guarded,
        # not just the redemption release call).
        tx.refresh_from_db()
        self.assertEqual(tx.meta.get("retry_count"), 1)
