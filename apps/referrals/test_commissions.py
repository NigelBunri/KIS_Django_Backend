"""
Tests for the lifetime referral cash-commission system
(apps.referrals.commission_services / models.ReferralCommission /
ReferralPayoutAccount) — a separate reward track from the one-time
KIS-Coins engine covered by tests.py (see ReferralCommission's own
docstring for why the two cannot share a lifecycle, and why this file
does not touch or depend on tests.py).

Run:
  python3 manage.py test apps.referrals.test_commissions --keepdb -v 2
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription, User

from .commission_services import (
    REFERRAL_COMMISSION_RATE_PERCENT,
    REFERRAL_COMMISSION_SETTLEMENT_WINDOW_DAYS,
    approve_referral_commission,
    award_referral_commission,
    confirm_flutterwave_commission_payout,
    initiate_commission_payout,
    reverse_referral_commission,
    sweep_approvable_referral_commissions,
    sweep_payable_referral_commissions,
)
from .models import Referral, ReferralCode, ReferralCommission, ReferralPayoutAccount, ReferralRateConfig
from .services import register_referral


def _make_user(phone: str) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.verification = {"phone": {"verified": True, "verified_at": timezone.now().isoformat()}}
    user.status = "active"
    user.is_active = True
    user.save(update_fields=["verification", "status", "is_active"])
    return user


def _make_active_subscription(user, tier) -> Subscription:
    Subscription.objects.filter(user=user, status=Subscription.STATUS_ACTIVE).update(
        status=Subscription.STATUS_SUPERSEDED,
    )
    return Subscription.objects.create(
        user=user, tier=tier, status=Subscription.STATUS_ACTIVE,
        started_at=timezone.now(), ends_at=timezone.now() + timedelta(days=30),
    )


def _setup_referral(phone_prefix="699900"):
    referrer = _make_user(f"+237{phone_prefix}001")
    code = ReferralCode.get_or_create_for_user(referrer)
    referred = _make_user(f"+237{phone_prefix}002")
    referral = register_referral(referred_user=referred, referral_code=code.code)
    tier = AccountTier.objects.create(name=f"CommissionTier-{phone_prefix}", rank=3, price_cents=10000)

    # award_referral_commission (this module's subject) does not care
    # about the REFERRER's own tier/rate at all - it's a flat 24%,
    # unconditionally. qualify_referral (the pre-existing, separate
    # KIS-Coins engine - apps.referrals.services) DOES require the
    # referrer to have an active subscription on a tier with an active
    # ReferralRateConfig, or it correctly no-ops. Tests in this file that
    # only exercise the commission track don't need this, but
    # WebhookIntegrationTests asserts on BOTH systems through the real
    # webhook, so the referrer needs a real rate configured - same
    # convention tests.py's own _setup_referral already uses.
    referrer_tier = AccountTier.objects.create(name=f"ReferrerTier-{phone_prefix}", rank=1, price_cents=5000)
    ReferralRateConfig.objects.create(tier=referrer_tier, rate_percent=Decimal("8.00"), is_active=True)
    _make_active_subscription(referrer, referrer_tier)

    return referrer, referred, referral, tier


class AwardReferralCommissionTests(TestCase):
    def test_flat_24_percent_applied_when_no_rate_override_exists(self):
        referrer, referred, referral, tier = _setup_referral("699901")
        sub = _make_active_subscription(referred, tier)

        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-1",
        )

        self.assertIsNotNone(commission)
        self.assertEqual(commission.commission_rate_percent, REFERRAL_COMMISSION_RATE_PERCENT)
        self.assertEqual(commission.commission_amount_cents, 2400)
        self.assertEqual(commission.status, ReferralCommission.STATUS_PENDING)
        self.assertEqual(commission.referrer_id, referrer.id)

    def test_lifetime_multiple_upgrades_each_generate_their_own_commission(self):
        referrer, referred, referral, tier = _setup_referral("699902")
        sub1 = _make_active_subscription(referred, tier)

        first = award_referral_commission(
            referred_user=referred, subscription=sub1, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-year1",
        )
        self.assertIsNotNone(first)

        # Years later, the SAME referred user upgrades again - lifetime
        # means lifetime, no expiry, no "first payment only" limit.
        sub2 = _make_active_subscription(referred, tier)
        second = award_referral_commission(
            referred_user=referred, subscription=sub2, source="flutterwave",
            gross_amount_cents=20000, source_payment_reference="tx-ref-year3",
        )

        self.assertIsNotNone(second)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.commission_amount_cents, 4800)
        self.assertEqual(ReferralCommission.objects.filter(referred_user=referred).count(), 2)

    def test_webhook_retry_does_not_create_a_duplicate_commission(self):
        referrer, referred, referral, tier = _setup_referral("699903")
        sub = _make_active_subscription(referred, tier)

        award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-retry",
        )
        duplicate = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-retry",
        )

        self.assertIsNone(duplicate)
        self.assertEqual(ReferralCommission.objects.filter(source_payment_reference="tx-ref-retry").count(), 1)

    def test_unrelated_purchase_sources_never_qualify(self):
        referrer, referred, referral, tier = _setup_referral("699904")
        sub = _make_active_subscription(referred, tier)

        for source in ("wallet", "mock", "credits", "free", "admin_grant"):
            result = award_referral_commission(
                referred_user=referred, subscription=sub, source=source,
                gross_amount_cents=10000, source_payment_reference=f"tx-ref-{source}",
            )
            self.assertIsNone(result, f"source={source} must never qualify")

        self.assertEqual(ReferralCommission.objects.filter(referred_user=referred).count(), 0)

    def test_no_commission_without_a_referrer(self):
        lone = _make_user("+237699905001")
        tier = AccountTier.objects.create(name="LoneCommissionTier", rank=1, price_cents=10000)
        sub = _make_active_subscription(lone, tier)

        result = award_referral_commission(
            referred_user=lone, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-lone",
        )
        self.assertIsNone(result)

    def test_blocked_referral_never_earns_a_commission(self):
        referrer, referred, referral, tier = _setup_referral("699906")
        referral.status = Referral.STATUS_BLOCKED
        referral.block_reason = "referred_device_already_linked_to_referrer"
        referral.save(update_fields=["status", "block_reason"])
        sub = _make_active_subscription(referred, tier)

        result = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-blocked",
        )
        self.assertIsNone(result)


class ApproveAndSweepTests(TestCase):
    def test_commission_not_approvable_before_settlement_window(self):
        referrer, referred, referral, tier = _setup_referral("699910")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-window",
        )
        result = sweep_approvable_referral_commissions()
        self.assertEqual(result["candidates"], 0)
        commission.refresh_from_db()
        self.assertEqual(commission.status, ReferralCommission.STATUS_PENDING)

    def test_commission_approved_once_window_elapses(self):
        referrer, referred, referral, tier = _setup_referral("699911")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-elapsed",
        )
        old = timezone.now() - timedelta(days=REFERRAL_COMMISSION_SETTLEMENT_WINDOW_DAYS + 1)
        ReferralCommission.objects.filter(pk=commission.pk).update(created_at=old)

        result = sweep_approvable_referral_commissions()
        self.assertEqual(result["approved"], 1)
        commission.refresh_from_db()
        self.assertEqual(commission.status, ReferralCommission.STATUS_APPROVED)

    def test_approve_is_idempotent(self):
        referrer, referred, referral, tier = _setup_referral("699912")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-idempotent",
        )
        approve_referral_commission(commission)
        approve_referral_commission(commission)  # second call must no-op, not error
        commission.refresh_from_db()
        self.assertEqual(commission.status, ReferralCommission.STATUS_APPROVED)


class ReversalTests(TestCase):
    def test_reversal_by_subscription_marks_commission_reversed(self):
        referrer, referred, referral, tier = _setup_referral("699920")
        sub = _make_active_subscription(referred, tier)
        award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-refund",
        )

        reversed_commission = reverse_referral_commission(referred_user=referred, subscription=sub, reason="refund:test")
        self.assertIsNotNone(reversed_commission)
        self.assertEqual(reversed_commission.status, ReferralCommission.STATUS_REVERSED)

    def test_reversal_is_idempotent(self):
        referrer, referred, referral, tier = _setup_referral("699921")
        sub = _make_active_subscription(referred, tier)
        award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-refund2",
        )
        reverse_referral_commission(referred_user=referred, subscription=sub, reason="refund:test")
        twice = reverse_referral_commission(referred_user=referred, subscription=sub, reason="refund:test-again")
        self.assertEqual(twice.status, ReferralCommission.STATUS_REVERSED)

    def test_reversal_of_an_already_paid_commission_does_not_error(self):
        referrer, referred, referral, tier = _setup_referral("699922")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-paid-then-refund",
        )
        commission.status = ReferralCommission.STATUS_PAID
        commission.save(update_fields=["status"])

        result = reverse_referral_commission(referred_user=referred, subscription=sub, reason="chargeback:test")
        self.assertEqual(result.status, ReferralCommission.STATUS_REVERSED)

    def test_reversal_with_no_matching_commission_is_a_safe_noop(self):
        referrer, referred, referral, tier = _setup_referral("699923")
        sub = _make_active_subscription(referred, tier)
        result = reverse_referral_commission(referred_user=referred, subscription=sub, reason="refund:nothing-to-reverse")
        self.assertIsNone(result)


class PayoutExecutionTests(TestCase):
    def test_initiate_payout_skips_commission_without_active_payout_account(self):
        referrer, referred, referral, tier = _setup_referral("699930")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-no-account",
        )
        commission = approve_referral_commission(commission)

        result = initiate_commission_payout(commission)
        self.assertEqual(result.status, ReferralCommission.STATUS_APPROVED)
        self.assertEqual(result.payout_reference, "")

    @patch("apps.billing.referral_payouts.create_stripe_transfer", return_value="tr_test123")
    def test_stripe_payout_marks_commission_paid_synchronously(self, mock_transfer):
        referrer, referred, referral, tier = _setup_referral("699931")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-stripe-payout",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_STRIPE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, stripe_account_id="acct_test123",
        )

        result = initiate_commission_payout(commission)
        self.assertEqual(result.status, ReferralCommission.STATUS_PAID)
        self.assertEqual(result.payout_provider, "stripe")
        self.assertEqual(result.payout_reference, "tr_test123")
        self.assertIsNotNone(result.paid_at)
        mock_transfer.assert_called_once()

    @patch("apps.billing.referral_payouts.create_stripe_transfer", side_effect=ValidationError({"detail": "insufficient funds"}))
    def test_failed_stripe_payout_marks_failed_payout_without_crashing_the_sweep(self, mock_transfer):
        referrer, referred, referral, tier = _setup_referral("699932")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-stripe-fail",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_STRIPE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, stripe_account_id="acct_test456",
        )

        result = initiate_commission_payout(commission)
        self.assertEqual(result.status, ReferralCommission.STATUS_FAILED_PAYOUT)
        self.assertTrue(result.payout_failure_reason)

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None)
    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer", return_value="flw-transfer-1")
    def test_flutterwave_payout_stays_approved_until_webhook_confirms(self, mock_transfer, mock_lookup):
        referrer, referred, referral, tier = _setup_referral("699933")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-flw-payout",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-123",
        )

        result = initiate_commission_payout(commission)
        self.assertEqual(result.status, ReferralCommission.STATUS_APPROVED)
        self.assertEqual(result.payout_reference, "flw-transfer-1")
        self.assertTrue(result.payout_attempt_reference)

        confirmed = confirm_flutterwave_commission_payout(
            payout_reference="flw-transfer-1", merchant_reference=result.payout_attempt_reference, successful=True,
        )
        self.assertEqual(confirmed.status, ReferralCommission.STATUS_PAID)

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None)
    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer", return_value="flw-transfer-dup-guard")
    def test_flutterwave_payout_not_reattempted_while_already_in_flight(self, mock_transfer, mock_lookup):
        # Production-readiness audit finding: a Flutterwave transfer stays
        # APPROVED pending its own transfer.completed webhook. Before this
        # guard, calling initiate_commission_payout a second time on the
        # same still-APPROVED commission (exactly what happens if the
        # hourly sweep runs again before that webhook arrives) dispatched
        # a SECOND real transfer with a fresh reference — an actual
        # duplicate payout, not merely a duplicate DB write.
        referrer, referred, referral, tier = _setup_referral("699936")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-flw-no-dup",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-dup-guard",
        )

        first = initiate_commission_payout(commission)
        self.assertEqual(first.payout_reference, "flw-transfer-dup-guard")
        mock_transfer.assert_called_once()

        # Simulate the next hourly sweep tick, before the webhook confirms.
        second = initiate_commission_payout(first)
        self.assertEqual(second.status, ReferralCommission.STATUS_APPROVED)
        self.assertEqual(second.payout_reference, "flw-transfer-dup-guard")
        mock_transfer.assert_called_once()  # still exactly once — no second real transfer

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None)
    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer", return_value="flw-transfer-2")
    def test_flutterwave_webhook_failure_marks_failed_payout(self, mock_transfer, mock_lookup):
        referrer, referred, referral, tier = _setup_referral("699934")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-flw-fail",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-456",
        )
        commission = initiate_commission_payout(commission)

        confirmed = confirm_flutterwave_commission_payout(
            payout_reference="flw-transfer-2", merchant_reference=commission.payout_attempt_reference,
            successful=False, failure_reason="INVALID_ACCOUNT",
        )
        self.assertEqual(confirmed.status, ReferralCommission.STATUS_FAILED_PAYOUT)
        self.assertEqual(confirmed.payout_failure_reason, "INVALID_ACCOUNT")

    @patch("apps.billing.referral_payouts.create_stripe_transfer", return_value="tr_sweep1")
    def test_sweep_payable_commissions_processes_a_full_batch(self, mock_transfer):
        referrer, referred, referral, tier = _setup_referral("699935")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-sweep",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_STRIPE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, stripe_account_id="acct_sweep1",
        )

        result = sweep_payable_referral_commissions()
        self.assertEqual(result["paid"], 1)
        self.assertEqual(result["errors"], 0)


class PayoutCrashRecoveryTests(TestCase):
    """Scenario 1: the provider accepts a transfer, but the application
    crashes (or the HTTP response is simply lost) before payout_reference
    is ever saved. A local DB transaction cannot make the external
    request atomic with our own save — payout_attempt_reference is
    persisted BEFORE the provider is ever called specifically so this
    state is recoverable rather than silently duplicated."""

    def _commission_with_pending_attempt(self, suffix: str):
        referrer, referred, referral, tier = _setup_referral(suffix)
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference=f"tx-ref-crash-{suffix}",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-crash",
        )
        # Simulates the claim phase having already committed (the durable
        # part) before the process died — payout_attempt_reference and
        # payout_attempts are set, but payout_reference never was.
        commission.payout_attempts = 1
        commission.payout_attempt_reference = f"referral_commission:{commission.id}:1"
        commission.save(update_fields=["payout_attempts", "payout_attempt_reference"])
        return commission

    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer")
    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference")
    def test_retry_reuses_the_persisted_reference_instead_of_minting_a_new_one(self, mock_lookup, mock_initiate):
        # The crashed attempt genuinely never reached Flutterwave —
        # reconciliation finds nothing, so it's safe to actually initiate
        # now, but it MUST reuse the already-persisted reference, not a
        # fresh "attempt 2" one (which would be a different idempotency
        # key on a real retry of an in-flight request).
        commission = self._commission_with_pending_attempt("699940")
        mock_lookup.return_value = None
        mock_initiate.return_value = "flw-transfer-recovered"

        result = initiate_commission_payout(commission)

        mock_lookup.assert_called_once_with(commission.payout_attempt_reference)
        mock_initiate.assert_called_once()
        self.assertEqual(mock_initiate.call_args.kwargs["reference"], commission.payout_attempt_reference)
        self.assertEqual(result.payout_reference, "flw-transfer-recovered")
        self.assertEqual(commission.payout_attempts, 1)  # not incremented to 2

    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer")
    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference")
    def test_retry_adopts_the_already_completed_transfer_without_calling_the_provider_again(self, mock_lookup, mock_initiate):
        # The crashed attempt DID reach Flutterwave and succeeded there —
        # reconciliation finds it, and the real defect this scenario
        # guards against is calling initiate_flutterwave_transfer a
        # second time for money that was already sent.
        commission = self._commission_with_pending_attempt("699941")
        mock_lookup.return_value = {"id": "flw-transfer-already-sent", "status": "SUCCESSFUL"}

        result = initiate_commission_payout(commission)

        mock_initiate.assert_not_called()
        self.assertEqual(result.status, ReferralCommission.STATUS_PAID)
        self.assertEqual(result.payout_reference, "flw-transfer-already-sent")

    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer")
    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference")
    def test_retry_adopts_a_provider_confirmed_failure_without_calling_the_provider_again(self, mock_lookup, mock_initiate):
        commission = self._commission_with_pending_attempt("699942")
        mock_lookup.return_value = {"id": "flw-transfer-already-failed", "status": "FAILED"}

        result = initiate_commission_payout(commission)

        mock_initiate.assert_not_called()
        self.assertEqual(result.status, ReferralCommission.STATUS_FAILED_PAYOUT)
        self.assertEqual(result.payout_reference, "flw-transfer-already-failed")


class PayoutWebhookOrderingTests(TestCase):
    """Scenarios 3, 4, 7: duplicate deliveries, out-of-order delivery, and
    a success reported after this codebase had already given up and
    marked FAILED_PAYOUT."""

    def _approved_flutterwave_commission(self, suffix: str):
        referrer, referred, referral, tier = _setup_referral(suffix)
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference=f"tx-ref-order-{suffix}",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-order",
        )
        return commission

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None)
    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer", return_value="flw-dup-webhook")
    def test_duplicate_successful_webhook_delivery_is_a_safe_noop(self, mock_transfer, mock_lookup):
        commission = self._approved_flutterwave_commission("699950")
        commission = initiate_commission_payout(commission)
        ref = commission.payout_attempt_reference

        first = confirm_flutterwave_commission_payout(payout_reference="flw-dup-webhook", merchant_reference=ref, successful=True)
        paid_at_first = first.paid_at
        second = confirm_flutterwave_commission_payout(payout_reference="flw-dup-webhook", merchant_reference=ref, successful=True)

        self.assertEqual(second.status, ReferralCommission.STATUS_PAID)
        self.assertEqual(second.paid_at, paid_at_first)  # untouched by the duplicate delivery

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None)
    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer", return_value="flw-out-of-order")
    def test_a_failed_event_arriving_after_a_successful_one_never_downgrades_paid(self, mock_transfer, mock_lookup):
        commission = self._approved_flutterwave_commission("699951")
        commission = initiate_commission_payout(commission)
        ref = commission.payout_attempt_reference

        confirm_flutterwave_commission_payout(payout_reference="flw-out-of-order", merchant_reference=ref, successful=True)
        late_failure = confirm_flutterwave_commission_payout(
            payout_reference="flw-out-of-order", merchant_reference=ref, successful=False, failure_reason="stale",
        )

        self.assertEqual(late_failure.status, ReferralCommission.STATUS_PAID)  # never downgraded

    def test_a_successful_webhook_promotes_an_already_failed_payout_commission(self):
        # Scenario 7, exactly as specified: initiate_flutterwave_transfer
        # raised (a timeout, say) so this codebase marked FAILED_PAYOUT
        # with payout_reference still empty — but the request had
        # actually reached Flutterwave and succeeded there. The LATER
        # webhook must be able to correct this using the merchant
        # reference, since payout_reference alone was never captured.
        # Goes through the REAL failure path (initiate_commission_payout
        # with a raising mock), not a hand-constructed FAILED_PAYOUT row —
        # this is exactly what caught payout_provider never being
        # persisted on the failure path in the first place.
        commission = self._approved_flutterwave_commission("699952")
        with patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None), \
             patch("apps.billing.referral_payouts.initiate_flutterwave_transfer", side_effect=TimeoutError("timed out")):
            commission = initiate_commission_payout(commission)
        self.assertEqual(commission.status, ReferralCommission.STATUS_FAILED_PAYOUT)
        self.assertEqual(commission.payout_provider, "flutterwave")
        self.assertEqual(commission.payout_reference, "")

        confirmed = confirm_flutterwave_commission_payout(
            payout_reference="flw-actually-succeeded",
            merchant_reference=commission.payout_attempt_reference,
            successful=True,
        )

        self.assertEqual(confirmed.status, ReferralCommission.STATUS_PAID)
        self.assertEqual(confirmed.payout_reference, "flw-actually-succeeded")
        self.assertEqual(confirmed.payout_failure_reason, "")

    def test_a_second_failed_event_on_an_already_failed_commission_is_idempotent(self):
        commission = self._approved_flutterwave_commission("699953")
        with patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None), \
             patch("apps.billing.referral_payouts.initiate_flutterwave_transfer", side_effect=ValidationError({"detail": "INVALID_ACCOUNT"})):
            commission = initiate_commission_payout(commission)
        self.assertEqual(commission.status, ReferralCommission.STATUS_FAILED_PAYOUT)

        result = confirm_flutterwave_commission_payout(
            payout_reference="", merchant_reference=commission.payout_attempt_reference,
            successful=False, failure_reason="INVALID_ACCOUNT",
        )

        self.assertEqual(result.status, ReferralCommission.STATUS_FAILED_PAYOUT)
        self.assertEqual(result.payout_failure_reason, commission.payout_failure_reason)  # unchanged, not re-raised as a new failure


class PayoutAdminRecoveryTests(TestCase):
    """Scenario 8: an administrator manually investigates and recovers a
    failed/unresolved commission — always via a real provider lookup,
    never by letting staff flip the status field directly."""

    def _failed_flutterwave_commission(self, suffix: str):
        referrer, referred, referral, tier = _setup_referral(suffix)
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference=f"tx-ref-admin-{suffix}",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-admin",
        )
        commission.payout_attempts = 1
        commission.payout_attempt_reference = f"referral_commission:{commission.id}:1"
        commission.payout_provider = "flutterwave"
        commission.status = ReferralCommission.STATUS_FAILED_PAYOUT
        commission.payout_failure_reason = "Could not reach the payment provider"
        commission.save()
        return commission

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference")
    def test_reconcile_recovers_a_stuck_failed_payout_that_actually_succeeded(self, mock_lookup):
        from apps.referrals.commission_services import reconcile_commission_payout

        commission = self._failed_flutterwave_commission("699960")
        mock_lookup.return_value = {"id": "flw-recovered", "status": "SUCCESSFUL"}

        result = reconcile_commission_payout(commission)

        mock_lookup.assert_called_once_with(commission.payout_attempt_reference)
        self.assertEqual(result.status, ReferralCommission.STATUS_PAID)
        self.assertEqual(result.payout_reference, "flw-recovered")

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference")
    def test_reconcile_confirms_a_genuinely_failed_payout_stays_failed(self, mock_lookup):
        from apps.referrals.commission_services import reconcile_commission_payout

        commission = self._failed_flutterwave_commission("699961")
        mock_lookup.return_value = {"id": "flw-confirmed-dead", "status": "FAILED"}

        result = reconcile_commission_payout(commission)
        self.assertEqual(result.status, ReferralCommission.STATUS_FAILED_PAYOUT)

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference")
    def test_reconcile_never_touches_an_already_paid_commission(self, mock_lookup):
        from apps.referrals.commission_services import reconcile_commission_payout

        commission = self._failed_flutterwave_commission("699962")
        commission.status = ReferralCommission.STATUS_PAID
        commission.payout_reference = "flw-already-paid"
        commission.save()

        result = reconcile_commission_payout(commission)

        mock_lookup.assert_not_called()
        self.assertEqual(result.status, ReferralCommission.STATUS_PAID)

    def test_admin_action_calls_the_real_reconciliation_service_and_reports_outcome(self):
        from django.contrib.admin.sites import AdminSite
        from apps.referrals.admin import ReferralCommissionAdmin

        commission = self._failed_flutterwave_commission("699963")
        admin_instance = ReferralCommissionAdmin(ReferralCommission, AdminSite())

        class _FakeRequest:
            pass

        with patch(
            "apps.referrals.commission_services.reconcile_commission_payout",
        ) as mock_reconcile, patch.object(admin_instance, "message_user"):
            mock_reconcile.return_value = commission
            admin_instance.reconcile_payout_selected(_FakeRequest(), ReferralCommission.objects.filter(pk=commission.pk))

        mock_reconcile.assert_called_once()


class PayoutConcurrencyTests(TransactionTestCase):
    """Scenario 9: two settlement workers process the same APPROVED
    commission at the same time."""

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None)
    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer")
    def test_concurrent_workers_use_the_identical_reference_and_idempotency_key(self, mock_transfer, mock_lookup):
        # What this codebase alone can guarantee: both racing calls claim
        # and use the EXACT SAME reference (and therefore the same
        # provider idempotency key) — see _claim_payout_attempt's
        # docstring. Deduplicating a true millisecond-level race between
        # two simultaneous HTTP requests to Flutterwave is the
        # PROVIDER's job at that point (its own X-Idempotency-Key
        # handling), not something a mock can prove here — this test
        # proves our half of that contract: identical references.
        import threading

        mock_transfer.return_value = "flw-concurrent"
        referrer, referred, referral, tier = _setup_referral("699970")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-concurrent-payout",
        )
        commission = approve_referral_commission(commission)
        ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-concurrent",
        )

        results = []

        def _attempt():
            fresh = ReferralCommission.objects.get(pk=commission.pk)
            results.append(initiate_commission_payout(fresh))

        threads = [threading.Thread(target=_attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        references_used = {call.kwargs["reference"] for call in mock_transfer.call_args_list}
        self.assertEqual(len(references_used), 1, "every concurrent call must use the identical reference")

        final = ReferralCommission.objects.get(pk=commission.pk)
        self.assertEqual(final.payout_attempts, 1, "only one attempt number was ever claimed")


class PayoutAccountChangeMidFlightTests(TestCase):
    """Scenario 10: the payout account is disconnected or switched to a
    different provider while a commission is still awaiting webhook
    confirmation."""

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None)
    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer", return_value="flw-mid-flight")
    def test_webhook_still_resolves_correctly_after_the_payout_account_is_disconnected(self, mock_transfer, mock_lookup):
        referrer, referred, referral, tier = _setup_referral("699980")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-disconnect",
        )
        commission = approve_referral_commission(commission)
        account = ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-disconnect",
        )
        commission = initiate_commission_payout(commission)
        ref = commission.payout_attempt_reference

        # The referrer disconnects their payout account entirely before
        # the webhook arrives.
        account.status = ReferralPayoutAccount.STATUS_NOT_CONNECTED
        account.provider = ""
        account.flutterwave_beneficiary_id = ""
        account.save()

        confirmed = confirm_flutterwave_commission_payout(payout_reference="flw-mid-flight", merchant_reference=ref, successful=True)

        self.assertEqual(confirmed.status, ReferralCommission.STATUS_PAID)

    @patch("apps.billing.referral_payouts.find_flutterwave_transfer_by_reference", return_value=None)
    @patch("apps.billing.referral_payouts.initiate_flutterwave_transfer", return_value="flw-mid-flight-2")
    def test_webhook_still_resolves_correctly_after_switching_to_a_different_provider(self, mock_transfer, mock_lookup):
        referrer, referred, referral, tier = _setup_referral("699981")
        sub = _make_active_subscription(referred, tier)
        commission = award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-switch-provider",
        )
        commission = approve_referral_commission(commission)
        account = ReferralPayoutAccount.objects.create(
            user=referrer, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-switch",
        )
        commission = initiate_commission_payout(commission)
        ref = commission.payout_attempt_reference

        # The referrer switches to Stripe before the Flutterwave webhook
        # for the ALREADY-in-flight transfer arrives.
        account.provider = ReferralPayoutAccount.PROVIDER_STRIPE
        account.flutterwave_beneficiary_id = ""
        account.stripe_account_id = "acct_switched"
        account.save()

        confirmed = confirm_flutterwave_commission_payout(payout_reference="flw-mid-flight-2", merchant_reference=ref, successful=True)

        self.assertEqual(confirmed.status, ReferralCommission.STATUS_PAID)
        self.assertEqual(confirmed.payout_provider, "flutterwave")  # the transfer that actually happened, not the new setting


class ConcurrentCommissionCreationTests(TransactionTestCase):
    def test_concurrent_webhook_retries_create_exactly_one_commission(self):
        import threading

        referrer, referred, referral, tier = _setup_referral("699940")
        sub = _make_active_subscription(referred, tier)

        results = []

        def _attempt():
            results.append(
                award_referral_commission(
                    referred_user=referred, subscription=sub, source="flutterwave",
                    gross_amount_cents=10000, source_payment_reference="tx-ref-concurrent",
                )
            )

        threads = [threading.Thread(target=_attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 1, "exactly one concurrent award_referral_commission() should win")
        self.assertEqual(ReferralCommission.objects.filter(source_payment_reference="tx-ref-concurrent").count(), 1)


class CommissionDashboardViewTests(TestCase):
    def test_requires_authentication(self):
        client = APIClient()
        response = client.get("/api/v1/referrals/commissions/")
        self.assertEqual(response.status_code, 401)

    def test_dashboard_reports_totals_and_history_without_exposing_other_users_private_data(self):
        referrer, referred, referral, tier = _setup_referral("699950")
        sub = _make_active_subscription(referred, tier)
        award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-dashboard",
        )

        client = APIClient()
        client.force_authenticate(user=referrer)
        response = client.get("/api/v1/referrals/commissions/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(str(data["commission_rate_percent"]), "24.00")
        self.assertEqual(data["total_pending_cents"], 2400)
        self.assertEqual(len(data["history"]), 1)
        entry = data["history"][0]
        self.assertNotIn("phone", entry)
        self.assertIn("referred_display_name", entry)

    def test_a_user_only_sees_their_own_commissions(self):
        referrer, referred, referral, tier = _setup_referral("699951")
        sub = _make_active_subscription(referred, tier)
        award_referral_commission(
            referred_user=referred, subscription=sub, source="flutterwave",
            gross_amount_cents=10000, source_payment_reference="tx-ref-privacy",
        )

        stranger = _make_user("+237699951999")
        client = APIClient()
        client.force_authenticate(user=stranger)
        response = client.get("/api/v1/referrals/commissions/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["history"], [])


class PayoutAccountViewTests(TestCase):
    def test_requires_authentication(self):
        client = APIClient()
        response = client.post("/api/v1/referrals/payout-account/flutterwave/", {"account_bank": "044", "account_number": "1234567890"})
        self.assertEqual(response.status_code, 401)

    @patch("apps.billing.referral_payouts.create_flutterwave_beneficiary", return_value="ben-connect-1")
    def test_connect_flutterwave_activates_the_account_and_never_stores_the_raw_account_number(self, mock_create):
        user = _make_user("+237699960001")
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            "/api/v1/referrals/payout-account/flutterwave/",
            {"account_bank": "044", "account_number": "0123456789", "beneficiary_name": "Test Referrer"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["provider"], "flutterwave")
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["payout_bank_last4"], "6789")

        account = ReferralPayoutAccount.objects.get(user=user)
        self.assertEqual(account.flutterwave_beneficiary_id, "ben-connect-1")
        # The raw account number must never be persisted anywhere on this
        # model - only last4 (display) and the provider-issued id.
        for field in account._meta.fields:
            value = getattr(account, field.name)
            if isinstance(value, str):
                self.assertNotIn("0123456789", value)

    @patch(
        "apps.billing.referral_payouts.create_flutterwave_beneficiary",
        side_effect=ValidationError({"detail": "Unable to connect payout account."}),
    )
    def test_failed_flutterwave_connect_leaves_account_not_connected(self, mock_create):
        user = _make_user("+237699961001")
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            "/api/v1/referrals/payout-account/flutterwave/",
            {"account_bank": "044", "account_number": "0123456789"},
        )

        self.assertEqual(response.status_code, 400)
        account = ReferralPayoutAccount.objects.get(user=user)
        self.assertEqual(account.status, ReferralPayoutAccount.STATUS_NOT_CONNECTED)

    def test_disconnect_clears_the_connected_account(self):
        user = _make_user("+237699962001")
        ReferralPayoutAccount.objects.create(
            user=user, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-x",
            payout_account_name="Someone", payout_bank_last4="6789",
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete("/api/v1/referrals/payout-account/flutterwave/")

        self.assertEqual(response.status_code, 200)
        account = ReferralPayoutAccount.objects.get(user=user)
        self.assertEqual(account.status, ReferralPayoutAccount.STATUS_NOT_CONNECTED)
        self.assertEqual(account.flutterwave_beneficiary_id, "")

    @patch("apps.billing.stripe_connect.create_account_onboarding_link", return_value="https://connect.stripe.com/onboarding/abc")
    @patch("apps.billing.stripe_connect.create_stripe_express_account", return_value="acct_connecttest")
    def test_connect_stripe_creates_account_and_returns_onboarding_link(self, mock_create_account, mock_onboarding_link):
        user = _make_user("+237699963001")
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post("/api/v1/referrals/payout-account/stripe/", {"country": "US"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stripe_account_id"], "acct_connecttest")
        self.assertEqual(data["onboarding_url"], "https://connect.stripe.com/onboarding/abc")
        account = ReferralPayoutAccount.objects.get(user=user)
        self.assertEqual(account.provider, ReferralPayoutAccount.PROVIDER_STRIPE)

    def test_connecting_stripe_after_flutterwave_clears_flutterwave_fields(self):
        user = _make_user("+237699964001")
        ReferralPayoutAccount.objects.create(
            user=user, provider=ReferralPayoutAccount.PROVIDER_FLUTTERWAVE,
            status=ReferralPayoutAccount.STATUS_ACTIVE, flutterwave_beneficiary_id="ben-y",
            payout_account_name="Old Flutterwave Name", payout_bank_last4="4321",
        )
        client = APIClient()
        client.force_authenticate(user=user)

        with patch("apps.billing.stripe_connect.create_stripe_express_account", return_value="acct_switch1"), \
             patch("apps.billing.stripe_connect.create_account_onboarding_link", return_value="https://connect.stripe.com/x"):
            response = client.post("/api/v1/referrals/payout-account/stripe/", {"country": "US"})

        self.assertEqual(response.status_code, 200)
        account = ReferralPayoutAccount.objects.get(user=user)
        self.assertEqual(account.provider, ReferralPayoutAccount.PROVIDER_STRIPE)
        self.assertEqual(account.flutterwave_beneficiary_id, "")
        self.assertEqual(account.payout_account_name, "")


class WebhookIntegrationTests(TestCase):
    """End-to-end: the real Flutterwave tier_upgrade webhook success
    branch (apps.billing.views) must create exactly one ReferralCommission
    alongside the existing one-time Referral qualification, and a refund
    must reverse both independently."""

    def setUp(self):
        self.referrer, self.referred, self.referral, self.tier = _setup_referral("699970")

    def _wallet_transaction(self, tx_ref: str, amount_cents: int = 10000):
        from apps.billing.models import WalletTransaction
        return WalletTransaction.objects.create(
            user=self.referred, provider="flutterwave", method="card",
            amount_cents=amount_cents, currency="USD", status="pending", tx_ref=tx_ref,
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id), "tier_name": self.tier.name},
        )

    def test_webhook_success_creates_both_a_referral_qualification_and_a_commission(self):
        tx = self._wallet_transaction("flw-webhook-tx-1")
        client = APIClient()

        with override_settings(FLW_WEBHOOK_SECRET="test-secret"):
            response = client.post(
                "/api/v1/wallet/webhook/flutterwave/",
                {"data": {"tx_ref": tx.tx_ref, "status": "successful", "id": "flw-charge-1"}},
                format="json",
                HTTP_VERIF_HASH="test-secret",
            )

        self.assertEqual(response.status_code, 200)
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_QUALIFIED)
        commission = ReferralCommission.objects.get(source_payment_reference=tx.tx_ref)
        self.assertEqual(commission.commission_amount_cents, 2400)
        self.assertEqual(commission.status, ReferralCommission.STATUS_PENDING)

    def test_refund_reverses_the_commission_independently_of_the_kis_coins_reward(self):
        from apps.billing.services import reverse_tier_upgrade_payment

        tx = self._wallet_transaction("flw-webhook-tx-2")
        client = APIClient()
        with override_settings(FLW_WEBHOOK_SECRET="test-secret"):
            client.post(
                "/api/v1/wallet/webhook/flutterwave/",
                {"data": {"tx_ref": tx.tx_ref, "status": "successful", "id": "flw-charge-2"}},
                format="json",
                HTTP_VERIF_HASH="test-secret",
            )
        tx.refresh_from_db()

        reverse_tier_upgrade_payment(transaction_obj=tx, reason="user_requested", event_type="refund")

        commission = ReferralCommission.objects.get(source_payment_reference=tx.tx_ref)
        self.assertEqual(commission.status, ReferralCommission.STATUS_REVERSED)
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.STATUS_REVERSED)
