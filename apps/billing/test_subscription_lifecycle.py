"""
Phase 3 — subscription lifecycle and billing integrity regression tests.

Covers: the consolidated Subscription state machine, the DB-backed
billing-period policy, finalize_expired_subscription (both the explicit
cancel/downgrade path and the previously-unhandled natural-lapse path),
the scheduled sweep, real downgrade proration via the ledger,
reverse_tier_upgrade_payment (refund + admin/chargeback), Flutterwave
webhook idempotency under a simulated race, and the reconcile_subscriptions
management command.

Frozen-time technique: no time-freezing library is installed and this repo
has a single shared requirements.txt with no dev/test split, so adding one
would make a test-only tool a production dependency. `_frozen_at()` below
patches django.utils.timezone.now() directly (standard library only) to
deterministically place a test at, before, or after a specific instant, and
to advance time between steps within a single test.

Run:
  python3 manage.py test apps.billing.test_subscription_lifecycle --keepdb -v 2
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, AuditLog, Subscription
from apps.billing.models import WalletLedgerEntry, WalletTransaction
from apps.billing.services import (
    apply_tier_upgrade,
    finalize_expired_subscription,
    reverse_tier_upgrade_payment,
    sweep_expired_subscriptions,
)

User = get_user_model()


@contextmanager
def _frozen_at(instant):
    with patch("django.utils.timezone.now", return_value=instant):
        yield instant


def _api_url(route_name: str) -> str:
    url = reverse(route_name)
    return url if url.endswith("/") else f"{url}/"


def _make_user(phone: str) -> User:
    return User.objects.create_user(phone=phone, country="CM", password="pass1234")


def _make_tier(name: str, price_cents: int, rank: int, billing_period_days: int = 30) -> AccountTier:
    return AccountTier.objects.create(
        name=name, price_cents=price_cents, rank=rank, billing_period_days=billing_period_days,
    )


def _free_tier() -> AccountTier:
    # finalize_expired_subscription / reverse_tier_upgrade_payment both look
    # up the revert-to target by the literal name "Free" (not by rank 0),
    # so tests that exercise that fallback must reuse whatever "Free" row
    # already exists (AccountTier.name is unique) rather than creating a
    # differently-named "free-ish" tier of their own.
    tier, _ = AccountTier.objects.get_or_create(
        name="Free", defaults={"price_cents": 0, "rank": 0, "billing_period_days": 30},
    )
    return tier


@override_settings(SECURE_SSL_REDIRECT=False)
class SubscriptionStateMachineTests(TestCase):
    def test_status_choices_are_the_consolidated_five(self):
        values = {v for v, _ in Subscription.STATUS_CHOICES}
        self.assertEqual(
            values, {"active", "cancelled", "expired", "superseded", "refunded"},
        )

    def test_unique_active_subscription_constraint_blocks_a_second_active_row(self):
        user = _make_user("+237699500001")
        tier = _make_tier("Lifecycle Free A", 0, 0)
        Subscription.objects.create(user=user, tier=tier, status=Subscription.STATUS_ACTIVE)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subscription.objects.create(user=user, tier=tier, status=Subscription.STATUS_ACTIVE)

    def test_constraint_allows_multiple_non_active_rows(self):
        user = _make_user("+237699500002")
        tier = _make_tier("Lifecycle Free B", 0, 0)
        Subscription.objects.create(user=user, tier=tier, status=Subscription.STATUS_EXPIRED)
        Subscription.objects.create(user=user, tier=tier, status=Subscription.STATUS_SUPERSEDED)
        Subscription.objects.create(user=user, tier=tier, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(Subscription.objects.filter(user=user).count(), 3)


@override_settings(SECURE_SSL_REDIRECT=False)
class ApplyTierUpgradeBillingPeriodTests(TestCase):
    def test_uses_the_tiers_own_billing_period_days_not_a_hardcoded_30(self):
        user = _make_user("+237699500010")
        tier = _make_tier("Lifecycle Annual", 10000, 1, billing_period_days=365)

        now = timezone.now()
        with _frozen_at(now):
            apply_tier_upgrade(user=user, tier=tier, source="test")

        sub = Subscription.objects.get(user=user, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(sub.ends_at, now + timedelta(days=365))

    def test_indefinite_grant_has_no_ends_at(self):
        user = _make_user("+237699500011")
        tier = _make_tier("Lifecycle Indefinite", 0, 5)
        apply_tier_upgrade(user=user, tier=tier, source="admin_grant", indefinite=True)
        sub = Subscription.objects.get(user=user, status=Subscription.STATUS_ACTIVE)
        self.assertIsNone(sub.ends_at)

    def test_second_upgrade_supersedes_the_first_not_duplicates_it(self):
        user = _make_user("+237699500012")
        tier_a = _make_tier("Lifecycle A", 1000, 1)
        tier_b = _make_tier("Lifecycle B", 2000, 2)
        apply_tier_upgrade(user=user, tier=tier_a, source="test")
        apply_tier_upgrade(user=user, tier=tier_b, source="test")

        active = Subscription.objects.filter(user=user, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.first().tier_id, tier_b.id)
        superseded = Subscription.objects.filter(user=user, status=Subscription.STATUS_SUPERSEDED)
        self.assertEqual(superseded.count(), 1)
        self.assertEqual(superseded.first().tier_id, tier_a.id)

    def test_records_the_payment_reference_for_later_reversal_lookup(self):
        user = _make_user("+237699500013")
        tier = _make_tier("Lifecycle Ref", 1000, 1)
        apply_tier_upgrade(user=user, tier=tier, source="flutterwave", reference="kis_upgrade_abc123")
        sub = Subscription.objects.get(user=user, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(sub.billing_meta.get("reference"), "kis_upgrade_abc123")


@override_settings(SECURE_SSL_REDIRECT=False)
class FinalizeExpiredSubscriptionTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699500020")
        self.free = _free_tier()
        self.pro = _make_tier("Lifecycle FZ Pro", 1000, 1)
        self.business = _make_tier("Lifecycle FZ Business", 2500, 2)

    def test_not_yet_at_ends_at_is_left_unchanged(self):
        sub = Subscription.objects.create(
            user=self.user, tier=self.pro, status=Subscription.STATUS_ACTIVE,
            started_at=timezone.now(), ends_at=timezone.now() + timedelta(days=1),
        )
        result = finalize_expired_subscription(sub)
        self.assertEqual(result.status, Subscription.STATUS_ACTIVE)

    def test_indefinite_ends_at_none_is_never_finalized(self):
        started = timezone.now() - timedelta(days=3650)
        sub = Subscription.objects.create(
            user=self.user, tier=self.pro, status=Subscription.STATUS_ACTIVE,
            started_at=started, ends_at=None,
        )
        result = finalize_expired_subscription(sub)
        self.assertEqual(result.status, Subscription.STATUS_ACTIVE)

    def test_natural_lapse_with_no_action_taken_now_reverts_to_free(self):
        """THE core Phase 3 gap: previously a subscription that simply
        passed ends_at with cancel_at_period_end=False was never processed
        at all — status stayed "active" and the paid tier stayed in effect
        forever."""
        now = timezone.now()
        with _frozen_at(now - timedelta(days=31)):
            sub = Subscription.objects.create(
                user=self.user, tier=self.pro, status=Subscription.STATUS_ACTIVE,
                started_at=timezone.now(), ends_at=timezone.now() + timedelta(days=30),
                cancel_at_period_end=False,
            )
        self.user.tier = self.pro.name
        self.user.save(update_fields=["tier"])

        with _frozen_at(now):
            finalize_expired_subscription(sub)

        sub.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_EXPIRED)
        self.assertEqual(self.user.tier, "Free")
        new_active = Subscription.objects.get(user=self.user, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(new_active.tier_id, self.free.id)
        self.assertEqual(new_active.billing_meta.get("source"), "expiry")

    def test_explicit_cancel_at_period_end_with_no_downgrade_target_reverts_to_free(self):
        now = timezone.now()
        sub = Subscription.objects.create(
            user=self.user, tier=self.pro, status=Subscription.STATUS_ACTIVE,
            started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
            cancel_at_period_end=True,
        )
        finalize_expired_subscription(sub)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_EXPIRED)
        new_active = Subscription.objects.get(user=self.user, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(new_active.tier_id, self.free.id)

    def test_scheduled_downgrade_transitions_to_the_pending_tier(self):
        now = timezone.now()
        sub = Subscription.objects.create(
            user=self.user, tier=self.business, status=Subscription.STATUS_ACTIVE,
            started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
            cancel_at_period_end=True, pending_tier=self.pro,
        )
        finalize_expired_subscription(sub)
        sub.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_EXPIRED)
        self.assertEqual(self.user.tier, self.pro.name)
        new_active = Subscription.objects.get(user=self.user, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(new_active.tier_id, self.pro.id)
        self.assertEqual(new_active.billing_meta.get("source"), "downgrade")

    def test_downgrade_proration_credit_is_not_granted_until_finalize_time(self):
        now = timezone.now()
        sub = Subscription.objects.create(
            user=self.user, tier=self.business, status=Subscription.STATUS_ACTIVE,
            started_at=now - timedelta(days=10), ends_at=now + timedelta(days=20),
            cancel_at_period_end=True, pending_tier=self.pro,
            billing_meta={"proration_credit_cents": 1000, "downgrade_to": self.pro.name},
        )
        # Not yet expired — no credit should exist yet.
        finalize_expired_subscription(sub)
        self.assertFalse(WalletLedgerEntry.objects.filter(user=self.user, kind="downgrade_credit").exists())

    def test_downgrade_proration_credit_is_granted_exactly_once_at_finalize(self):
        now = timezone.now()
        sub = Subscription.objects.create(
            user=self.user, tier=self.business, status=Subscription.STATUS_ACTIVE,
            started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
            cancel_at_period_end=True, pending_tier=self.pro,
            billing_meta={"proration_credit_cents": 1234, "downgrade_to": self.pro.name},
        )
        finalize_expired_subscription(sub)

        entries = WalletLedgerEntry.objects.filter(user=self.user, kind="downgrade_credit")
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().amount_cents, 1234)

    def test_natural_lapse_grants_no_proration_credit(self):
        now = timezone.now()
        sub = Subscription.objects.create(
            user=self.user, tier=self.business, status=Subscription.STATUS_ACTIVE,
            started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
            cancel_at_period_end=False,
        )
        finalize_expired_subscription(sub)
        self.assertFalse(WalletLedgerEntry.objects.filter(user=self.user, kind="downgrade_credit").exists())

    def test_is_idempotent_a_second_call_is_a_no_op(self):
        now = timezone.now()
        sub = Subscription.objects.create(
            user=self.user, tier=self.pro, status=Subscription.STATUS_ACTIVE,
            started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
            cancel_at_period_end=True, pending_tier=None,
            billing_meta={"proration_credit_cents": 500, "downgrade_to": "Free"},
        )
        finalize_expired_subscription(sub)
        first_active_count = Subscription.objects.filter(user=self.user, status=Subscription.STATUS_ACTIVE).count()
        first_ledger_count = WalletLedgerEntry.objects.filter(user=self.user, kind="downgrade_credit").count()

        sub.refresh_from_db()
        finalize_expired_subscription(sub)  # already STATUS_EXPIRED now — must no-op

        self.assertEqual(
            Subscription.objects.filter(user=self.user, status=Subscription.STATUS_ACTIVE).count(),
            first_active_count,
        )
        self.assertEqual(
            WalletLedgerEntry.objects.filter(user=self.user, kind="downgrade_credit").count(),
            first_ledger_count,
        )


@override_settings(SECURE_SSL_REDIRECT=False)
class SweepExpiredSubscriptionsTests(TestCase):
    def test_sweeps_multiple_users_expired_subscriptions(self):
        now = timezone.now()
        free = _free_tier()
        pro = _make_tier("Sweep Pro", 1000, 1)
        users = [_make_user(f"+23769960{i:04d}") for i in range(3)]
        for u in users:
            Subscription.objects.create(
                user=u, tier=pro, status=Subscription.STATUS_ACTIVE,
                started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
            )

        result = sweep_expired_subscriptions()

        self.assertEqual(result["candidates"], 3)
        self.assertEqual(result["finalized"], 3)
        self.assertEqual(result["errors"], 0)
        for u in users:
            self.assertEqual(
                Subscription.objects.get(user=u, status=Subscription.STATUS_ACTIVE).tier_id, free.id,
            )

    def test_does_not_touch_a_subscription_that_is_not_yet_expired(self):
        now = timezone.now()
        pro = _make_tier("Sweep Not Yet Pro", 1000, 1)
        user = _make_user("+237699610001")
        sub = Subscription.objects.create(
            user=user, tier=pro, status=Subscription.STATUS_ACTIVE,
            started_at=now, ends_at=now + timedelta(days=29),
        )
        result = sweep_expired_subscriptions()
        self.assertEqual(result["candidates"], 0)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)

    def test_respects_the_limit_parameter(self):
        now = timezone.now()
        pro = _make_tier("Sweep Limit Pro", 1000, 1)
        for i in range(5):
            u = _make_user(f"+23769962{i:04d}")
            Subscription.objects.create(
                user=u, tier=pro, status=Subscription.STATUS_ACTIVE,
                started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
            )
        result = sweep_expired_subscriptions(limit=2)
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["finalized"], 2)

    def test_one_failure_does_not_block_the_rest_of_the_batch(self):
        now = timezone.now()
        pro = _make_tier("Sweep Error Pro", 1000, 1)
        users = [_make_user(f"+23769963{i:04d}") for i in range(3)]
        subs = [
            Subscription.objects.create(
                user=u, tier=pro, status=Subscription.STATUS_ACTIVE,
                started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
            )
            for u in users
        ]

        real_finalize = finalize_expired_subscription

        def flaky_finalize(sub):
            if sub.id == subs[1].id:
                raise RuntimeError("simulated failure")
            return real_finalize(sub)

        with patch("apps.billing.services.finalize_expired_subscription", side_effect=flaky_finalize):
            result = sweep_expired_subscriptions()

        self.assertEqual(result["candidates"], 3)
        self.assertEqual(result["finalized"], 2)
        self.assertEqual(result["errors"], 1)


@override_settings(SECURE_SSL_REDIRECT=False)
class ReverseTierUpgradePaymentTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699700001")
        self.tier = _make_tier("Reversal Pro", 1000, 1)
        apply_tier_upgrade(
            user=self.user, tier=self.tier, source="flutterwave",
            amount_cents=1000, reference="kis_upgrade_reversal_test",
        )
        self.tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card",
            amount_cents=1000, currency="USD", status="success",
            tx_ref="kis_upgrade_reversal_test",
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id)},
        )

    def test_ends_the_subscription_and_reverts_the_current_tier_to_free(self):
        result = reverse_tier_upgrade_payment(transaction_obj=self.tx, reason="Customer request", event_type="refund")

        self.assertTrue(result["subscription_found"])
        self.assertEqual(result["reverted_to_tier"], "Free")
        sub = Subscription.objects.get(user=self.user, billing_meta__reference=self.tx.tx_ref)
        self.assertEqual(sub.status, Subscription.STATUS_REFUNDED)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, "Free")

    def test_writes_a_compensating_negative_ledger_entry_not_editing_the_original(self):
        original_entries = list(WalletLedgerEntry.objects.filter(user=self.user, kind="tier_upgrade"))
        self.assertEqual(len(original_entries), 1)
        original_amount = original_entries[0].amount_cents

        reverse_tier_upgrade_payment(transaction_obj=self.tx, reason="test", event_type="refund")

        # Original entry untouched.
        original_entries[0].refresh_from_db()
        self.assertEqual(original_entries[0].amount_cents, original_amount)
        # New compensating entry.
        reversal_entries = WalletLedgerEntry.objects.filter(user=self.user, kind="subscription_reversal")
        self.assertEqual(reversal_entries.count(), 1)
        self.assertEqual(reversal_entries.first().amount_cents, -1000)

    def test_is_idempotent_a_second_call_does_not_double_reverse(self):
        reverse_tier_upgrade_payment(transaction_obj=self.tx, reason="first", event_type="refund")
        result2 = reverse_tier_upgrade_payment(transaction_obj=self.tx, reason="second", event_type="chargeback")

        self.assertTrue(result2.get("already_reversed"))
        self.assertEqual(WalletLedgerEntry.objects.filter(user=self.user, kind="subscription_reversal").count(), 1)

    def test_reversing_an_already_superseded_subscription_does_not_touch_the_users_current_tier(self):
        # User upgraded AGAIN after the original payment — the original
        # subscription is now superseded, not current.
        other_tier = _make_tier("Reversal Business", 2500, 2)
        apply_tier_upgrade(user=self.user, tier=other_tier, source="flutterwave")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, other_tier.name)

        result = reverse_tier_upgrade_payment(transaction_obj=self.tx, reason="late refund", event_type="refund")

        self.assertTrue(result["subscription_found"])
        self.assertIsNone(result["reverted_to_tier"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, other_tier.name)  # unaffected

    def test_creates_an_audit_log_entry(self):
        reverse_tier_upgrade_payment(transaction_obj=self.tx, reason="test reason", event_type="chargeback")
        entry = AuditLog.objects.filter(actor_id=self.user.id, action="billing.subscription.reversed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("event_type"), "chargeback")

    def test_no_matching_subscription_still_marks_the_transaction_reversed(self):
        orphan_tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card",
            amount_cents=500, currency="USD", status="success",
            tx_ref="kis_orphan_ref_no_subscription",
            meta={"intent": "tier_upgrade"},
        )
        result = reverse_tier_upgrade_payment(transaction_obj=orphan_tx, reason="test", event_type="refund")
        self.assertFalse(result["subscription_found"])
        orphan_tx.refresh_from_db()
        self.assertTrue(orphan_tx.meta.get("reversed"))


@override_settings(SECURE_SSL_REDIRECT=False)
class SelfServiceRefundReversalIntegrationTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699710001")
        self.tier = _make_tier("Refund Integration Pro", 1000, 1)
        apply_tier_upgrade(
            user=self.user, tier=self.tier, source="flutterwave",
            amount_cents=1000, reference="kis_refund_integration_ref",
        )
        self.tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card",
            amount_cents=1000, currency="USD", status="success", provider_ref="flw-provider-ref-1",
            tx_ref="kis_refund_integration_ref",
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id)},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch("apps.billing.views.requests.post")
    @patch("apps.billing.views._ensure_payments_ready")
    def test_self_service_refund_reverses_the_subscription(self, ensure_ready_mock, post_mock):
        ensure_ready_mock.return_value = None
        post_mock.return_value.status_code = 200
        post_mock.return_value.content = b'{"status":"success"}'
        post_mock.return_value.json.return_value = {"status": "success"}

        res = self.client.post(_api_url("wallet-refund"), {"tx_ref": self.tx.tx_ref}, format="json", secure=True)

        self.assertEqual(res.status_code, 200)
        self.assertIn("subscription_reversal", res.data)
        self.assertTrue(res.data["subscription_reversal"]["subscription_found"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, "Free")

    @patch("apps.billing.views.requests.post")
    @patch("apps.billing.views._ensure_payments_ready")
    def test_a_non_tier_upgrade_refund_does_not_touch_any_subscription(self, ensure_ready_mock, post_mock):
        ensure_ready_mock.return_value = None
        post_mock.return_value.status_code = 200
        post_mock.return_value.content = b'{"status":"success"}'
        post_mock.return_value.json.return_value = {"status": "success"}

        deposit_tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card",
            amount_cents=500, currency="USD", status="success", provider_ref="flw-provider-ref-2",
            tx_ref="kis_deposit_not_a_tier_upgrade", meta={"intent": "deposit"},
        )
        res = self.client.post(_api_url("wallet-refund"), {"tx_ref": deposit_tx.tx_ref}, format="json", secure=True)
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("subscription_reversal", res.data)


@override_settings(SECURE_SSL_REDIRECT=False)
class AdminReversePaymentActionTests(TestCase):
    def setUp(self):
        self.staff = _make_user("+237699720001")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])
        self.normal_user = _make_user("+237699720002")
        self.target_user = _make_user("+237699720003")
        self.tier = _make_tier("Admin Reversal Pro", 1000, 1)
        apply_tier_upgrade(
            user=self.target_user, tier=self.tier, source="flutterwave",
            amount_cents=1000, reference="kis_admin_reversal_ref",
        )
        self.tx = WalletTransaction.objects.create(
            user=self.target_user, provider="flutterwave", method="card",
            amount_cents=1000, currency="USD", status="success",
            tx_ref="kis_admin_reversal_ref", meta={"intent": "tier_upgrade"},
        )

    def test_non_staff_is_denied(self):
        client = APIClient()
        client.force_authenticate(self.normal_user)
        res = client.post(
            _api_url("wallet-admin-reverse-payment"),
            {"tx_ref": self.tx.tx_ref, "event_type": "chargeback"}, format="json", secure=True,
        )
        self.assertEqual(res.status_code, 403)

    def test_staff_can_reverse_another_users_payment_without_calling_the_payment_provider(self):
        client = APIClient()
        client.force_authenticate(self.staff)
        with patch("apps.billing.views.requests.post") as post_mock:
            res = client.post(
                _api_url("wallet-admin-reverse-payment"),
                {"tx_ref": self.tx.tx_ref, "event_type": "chargeback", "reason": "Bank dispute"},
                format="json", secure=True,
            )
            post_mock.assert_not_called()

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["reversal"]["subscription_found"])
        self.target_user.refresh_from_db()
        self.assertEqual(self.target_user.tier, "Free")

    def test_rejects_an_invalid_event_type(self):
        client = APIClient()
        client.force_authenticate(self.staff)
        res = client.post(
            _api_url("wallet-admin-reverse-payment"),
            {"tx_ref": self.tx.tx_ref, "event_type": "not_a_real_type"}, format="json", secure=True,
        )
        self.assertEqual(res.status_code, 400)


@override_settings(SECURE_SSL_REDIRECT=False)
class FlutterwaveWebhookIdempotencyTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699730001")
        self.tier = _make_tier("Webhook Idem Pro", 1000, 1)
        self.tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card",
            amount_cents=1000, currency="USD", status="pending",
            tx_ref="kis_webhook_idem_ref",
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id)},
        )
        self.client = APIClient()

    def _post_webhook(self):
        return self.client.post(
            "/api/v1/wallet/webhook/flutterwave/",
            {"data": {"tx_ref": self.tx.tx_ref, "status": "successful", "id": "flw-evt-1", "currency": "USD"}},
            format="json",
            HTTP_VERIF_HASH="test-webhook-secret",
            secure=True,
        )

    @override_settings(FLW_WEBHOOK_SECRET="test-webhook-secret")
    def test_redelivering_the_same_successful_webhook_does_not_duplicate_the_subscription(self):
        res1 = self._post_webhook()
        self.assertEqual(res1.status_code, 200)
        res2 = self._post_webhook()
        self.assertEqual(res2.status_code, 200)

        active_subs = Subscription.objects.filter(user=self.user, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(active_subs.count(), 1)
        ledger_entries = WalletLedgerEntry.objects.filter(user=self.user, kind="tier_upgrade")
        self.assertEqual(ledger_entries.count(), 1)


@override_settings(SECURE_SSL_REDIRECT=False)
class ReconcileSubscriptionsCommandTests(TestCase):
    def test_dry_run_reports_candidates_without_writing(self):
        from io import StringIO
        from django.core.management import call_command

        now = timezone.now()
        pro = _make_tier("Reconcile Dry Pro", 1000, 1)
        user = _make_user("+237699740001")
        sub = Subscription.objects.create(
            user=user, tier=pro, status=Subscription.STATUS_ACTIVE,
            started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
        )

        out = StringIO()
        call_command("reconcile_subscriptions", "--dry-run", stdout=out)

        self.assertIn("WOULD EXPIRE", out.getvalue())
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)

    def test_real_run_finalizes_expired_subscriptions(self):
        from io import StringIO
        from django.core.management import call_command

        now = timezone.now()
        pro = _make_tier("Reconcile Real Pro", 1000, 1)
        user = _make_user("+237699740002")
        sub = Subscription.objects.create(
            user=user, tier=pro, status=Subscription.STATUS_ACTIVE,
            started_at=now - timedelta(days=31), ends_at=now - timedelta(hours=1),
        )

        out = StringIO()
        call_command("reconcile_subscriptions", stdout=out)

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_EXPIRED)
        self.assertIn("1 finalized", out.getvalue())
