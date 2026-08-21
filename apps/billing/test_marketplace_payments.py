"""Tests for the marketplace-payments backend foundation added on top of
DirectPaymentIntent: the centralized eligibility service, the universal
create_direct_payment_intent backstop, Stripe Connect, and the health
billing price-trust fix. See apps/billing/eligibility.py and
apps/billing/stripe_connect.py for the implementations under test.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.billing.eligibility import EligibilityResult, PaymentSetupRequiredError, can_receive_payments
from apps.billing.models import DirectPaymentIntent
from apps.commerce.models import MarketplaceOrder, MarketplaceOrderStatus, Shop, ShopPayoutAccountStatus

User = get_user_model()


def _make_user(phone: str, username: str):
    return User.objects.create_user(phone=phone, username=username, password="secret", country="NG")


class PaymentEligibilityServiceTests(TestCase):
    """can_receive_payments is provider-agnostic — eligible if EITHER
    Flutterwave or Stripe is ready, with a reason distinguishing "never
    tried" from "started but incomplete" either way."""

    def test_not_connected_when_neither_provider_attempted(self):
        entity = SimpleNamespace(
            payout_account_status="not_connected", flutterwave_subaccount_id="",
            stripe_account_id="", stripe_charges_enabled=False,
        )
        result = can_receive_payments(entity)
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "NOT_CONNECTED")
        self.assertEqual(result.action, "COMPLETE_PAYMENT_SETUP")

    def test_eligible_via_flutterwave_active_subaccount(self):
        entity = SimpleNamespace(
            payout_account_status="active", flutterwave_subaccount_id="RS_ABC123",
            stripe_account_id="", stripe_charges_enabled=False,
        )
        result = can_receive_payments(entity)
        self.assertTrue(result.eligible)
        self.assertEqual(result.provider, "flutterwave")

    def test_eligible_via_stripe_charges_enabled(self):
        entity = SimpleNamespace(
            payout_account_status="not_connected", flutterwave_subaccount_id="",
            stripe_account_id="acct_123", stripe_charges_enabled=True,
        )
        result = can_receive_payments(entity)
        self.assertTrue(result.eligible)
        self.assertEqual(result.provider, "stripe")

    def test_onboarding_incomplete_when_flutterwave_pending(self):
        entity = SimpleNamespace(
            payout_account_status="pending", flutterwave_subaccount_id="RS_ABC123",
            stripe_account_id="", stripe_charges_enabled=False,
        )
        result = can_receive_payments(entity)
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "ONBOARDING_INCOMPLETE")

    def test_onboarding_incomplete_when_stripe_account_exists_but_charges_disabled(self):
        entity = SimpleNamespace(
            payout_account_status="not_connected", flutterwave_subaccount_id="",
            stripe_account_id="acct_123", stripe_charges_enabled=False,
        )
        result = can_receive_payments(entity)
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "ONBOARDING_INCOMPLETE")

    def test_payment_setup_required_error_shape(self):
        result = EligibilityResult(eligible=False, provider=None, reason="NOT_CONNECTED", action="COMPLETE_PAYMENT_SETUP")
        exc = PaymentSetupRequiredError(result)
        self.assertEqual(exc.status_code, 402)
        self.assertEqual(exc.detail["code"], "PAYMENT_SETUP_REQUIRED")
        # DRF wraps every value under APIException.detail in ErrorDetail
        # (a str subclass), so it stringifies rather than staying a bool.
        self.assertEqual(str(exc.detail["eligible"]), "False")
        self.assertEqual(exc.detail["reason"], "NOT_CONNECTED")
        self.assertEqual(exc.detail["action"], "COMPLETE_PAYMENT_SETUP")


class DirectPaymentIntentBackstopTests(TestCase):
    """create_direct_payment_intent is the one function every domain
    (Market/Education/Health/Broadcast) already calls — gating it there
    covers all of them with a single check. This test proves the gate
    fires generically (not hardcoded to one target_type) by driving it
    through a real MarketplaceOrder fixture while mocking
    resolve_payout_entity's return value directly, and confirms it never
    even attempts to create a provider payment link when blocked."""

    def setUp(self):
        self.buyer = _make_user("+2348030000001", "backstop_buyer")
        self.seller = _make_user("+2348030000002", "backstop_seller")
        self.shop = Shop.objects.create(
            owner=self.seller, name="Backstop Shop", slug="backstop-shop",
            payout_account_status=ShopPayoutAccountStatus.NOT_CONNECTED,
        )
        self.order = MarketplaceOrder.objects.create(
            buyer=self.buyer, shop=self.shop, total_amount="25.00", currency="USD",
            status=MarketplaceOrderStatus.TEMPORAL,
        )

    def test_blocked_when_seller_not_payment_ready(self):
        from apps.billing.direct_payments import create_direct_payment_intent

        with patch("apps.billing.direct_payments._ensure_provider_payment_link") as mocked_link:
            with self.assertRaises(PaymentSetupRequiredError) as ctx:
                create_direct_payment_intent(
                    user=self.buyer, target_type=DirectPaymentIntent.TARGET_MARKETPLACE_ORDER,
                    target_id=self.order.id, provider="flutterwave",
                )
        # The gate fires before any provider API call is even attempted.
        mocked_link.assert_not_called()
        self.assertEqual(ctx.exception.detail["reason"], "NOT_CONNECTED")
        self.assertFalse(DirectPaymentIntent.objects.filter(target_id=self.order.id).exists())

    def test_allowed_once_seller_connects_flutterwave(self):
        from apps.billing.direct_payments import create_direct_payment_intent

        self.shop.payout_account_status = ShopPayoutAccountStatus.ACTIVE
        self.shop.flutterwave_subaccount_id = "RS_BACKSTOP"
        self.shop.save(update_fields=["payout_account_status", "flutterwave_subaccount_id"])

        with patch("apps.billing.direct_payments._ensure_provider_payment_link", side_effect=lambda intent, actor=None: intent):
            intent = create_direct_payment_intent(
                user=self.buyer, target_type=DirectPaymentIntent.TARGET_MARKETPLACE_ORDER,
                target_id=self.order.id, provider="flutterwave",
            )
        self.assertEqual(intent.status, DirectPaymentIntent.STATUS_PENDING)
        self.assertEqual(intent.amount_cents, 2500)


class StripeConnectTests(TestCase):
    """create_stripe_express_account/create_account_onboarding_link/
    refresh_account_status wrap the Stripe SDK — mock the SDK itself
    rather than hit the network, mirroring how apps.billing.stripe_payments
    is tested elsewhere in this codebase."""

    def test_create_stripe_express_account_returns_id(self):
        from apps.billing import stripe_connect

        fake_stripe = MagicMock()
        fake_stripe.Account.create.return_value = SimpleNamespace(id="acct_new123")
        with patch.object(stripe_connect, "_stripe", return_value=fake_stripe):
            account_id = stripe_connect.create_stripe_express_account(email="seller@example.com", country="US")
        self.assertEqual(account_id, "acct_new123")
        fake_stripe.Account.create.assert_called_once()
        self.assertEqual(fake_stripe.Account.create.call_args.kwargs["type"], "express")

    def test_create_account_onboarding_link_returns_url(self):
        from apps.billing import stripe_connect

        fake_stripe = MagicMock()
        fake_stripe.AccountLink.create.return_value = SimpleNamespace(url="https://connect.stripe.com/setup/xyz")
        with patch.object(stripe_connect, "_stripe", return_value=fake_stripe):
            url = stripe_connect.create_account_onboarding_link(
                account_id="acct_new123", refresh_url="https://kis.app/refresh", return_url="https://kis.app/return",
            )
        self.assertEqual(url, "https://connect.stripe.com/setup/xyz")

    def test_refresh_account_status_maps_fields(self):
        from apps.billing import stripe_connect

        fake_stripe = MagicMock()
        fake_stripe.Account.retrieve.return_value = SimpleNamespace(
            charges_enabled=True, payouts_enabled=False, details_submitted=True,
        )
        with patch.object(stripe_connect, "_stripe", return_value=fake_stripe):
            fields = stripe_connect.refresh_account_status("acct_new123")
        self.assertEqual(
            fields,
            {"stripe_charges_enabled": True, "stripe_payouts_enabled": False, "stripe_details_submitted": True},
        )


class StripeConnectWebhookSyncTests(TestCase):
    """account.updated is the authoritative sync point — this proves it
    finds the right entity (across all four payout-holder tables) purely
    from the Stripe account id, with no target_type/target_id metadata to
    go on (Stripe doesn't attach ours to Account objects)."""

    def test_syncs_matching_shop_by_stripe_account_id(self):
        from apps.billing.views import _sync_stripe_connect_account_status

        owner = _make_user("+2348030000003", "webhook_owner")
        shop = Shop.objects.create(
            owner=owner, name="Webhook Shop", slug="webhook-shop", stripe_account_id="acct_webhook123",
        )
        _sync_stripe_connect_account_status(
            {"id": "acct_webhook123", "charges_enabled": True, "payouts_enabled": True, "details_submitted": True}
        )
        shop.refresh_from_db()
        self.assertTrue(shop.stripe_charges_enabled)
        self.assertTrue(shop.stripe_payouts_enabled)
        self.assertTrue(shop.stripe_details_submitted)

    def test_no_match_does_not_raise(self):
        from apps.billing.views import _sync_stripe_connect_account_status

        # No entity anywhere has this account id — should no-op silently,
        # not raise, since Stripe will send account.updated events for
        # accounts this platform doesn't recognize under some setups.
        _sync_stripe_connect_account_status({"id": "acct_unknown", "charges_enabled": True})


class StripeCommissionSplitTests(TestCase):
    """Mirrors FlutterwaveSplitCommissionTests in apps/billing/tests.py —
    same commission-rate resolver, but Stripe's application_fee_amount
    wants absolute cents, not a percentage fraction."""

    def test_commission_cents_uses_whole_number_percentage_as_cents_fraction(self):
        from apps.billing.direct_payments import _commission_cents_for_entity

        fake_entity = SimpleNamespace(owner=None, owner_user=None)
        fake_intent = SimpleNamespace(amount_cents=10000)
        with patch("apps.accounts.tiers.get_platform_commission_pct", return_value=10.0):
            cents = _commission_cents_for_entity(fake_intent, fake_entity)
        self.assertEqual(cents, 1000)  # 10% of $100.00

    def test_commission_cents_is_zero_when_entity_is_none(self):
        from apps.billing.direct_payments import _commission_cents_for_entity

        fake_intent = SimpleNamespace(amount_cents=10000)
        self.assertEqual(_commission_cents_for_entity(fake_intent, None), 0)


class HealthBillingPriceTrustTests(TestCase):
    """PaymentBillingSessionStartView must never let the patient set their
    own bill — only institution staff's client-supplied total is honored;
    a patient's is always overridden by the server-derived engine cost."""

    def setUp(self):
        from apps.health_ops.models import (
            EngineRegistry, HealthInstitution, HealthInstitutionMembership,
            HealthInstitutionPayoutAccountStatus, HealthService, MembershipRole, ServiceEngineMap,
            ServiceWorkflowSession, WorkflowStatus,
        )

        self.client = APIClient()
        self.owner = _make_user("+2348030000010", "health_owner")
        self.patient = _make_user("+2348030000011", "health_patient")
        self.institution = HealthInstitution.objects.create(
            owner=self.owner, name="Trust Test Hospital", slug="trust-test-hospital",
            institution_type="hospital", timezone="UTC", settings={}, is_active=True,
            payout_account_status=HealthInstitutionPayoutAccountStatus.ACTIVE,
            flutterwave_subaccount_id="RS_TEST_TRUST",
        )
        HealthInstitutionMembership.objects.create(user=self.owner, institution=self.institution, role=MembershipRole.OWNER)
        self.service = HealthService.objects.create(institution=self.institution, name="Billing Test Service", base_cost_micro=5_000_000)
        engine = EngineRegistry.objects.create(code="payment_billing", name="Payment Billing")
        self.engine_map = ServiceEngineMap.objects.create(service=self.service, engine=engine, execution_order=1, cost_micro=7_000_000)
        self.workflow = ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )
        from apps.health_ops.models import EngineSession
        EngineSession.objects.create(workflow_session=self.workflow, engine_map=self.engine_map, user=self.patient, is_unlocked=True)

    def _start(self, actor, **payload):
        self.client.force_authenticate(actor)
        return self.client.post(
            "/api/v1/health-ops/billing/sessions/start/",
            {"workflow_session_id": str(self.workflow.id), **payload},
            format="json",
        )

    def test_patient_supplied_amount_is_ignored(self):
        response = self._start(self.patient, total_amount_micro=1)  # attempt to set bill to near-zero
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        from apps.health_ops.models import PaymentBillingSession

        session = PaymentBillingSession.objects.get(workflow_session=self.workflow)
        # Must fall back to the server-derived engine cost (7_000_000),
        # never the patient-supplied 1.
        self.assertEqual(session.total_amount_micro, 7_000_000)

    def test_institution_staff_supplied_amount_is_honored(self):
        # create_direct_payment_intent's own ownership check (a real,
        # pre-existing constraint unrelated to this fix) requires the
        # caller to match the billing session's patient — an institution
        # staff member starting a session on a patient's behalf never
        # satisfies that today. Mocked out here since this test is about
        # whether the staff-supplied total is honored into the billing
        # session record, not about that separate authorization path.
        with patch("apps.health_ops.views.create_direct_payment_intent") as mocked_intent:
            mocked_intent.return_value = SimpleNamespace(id="00000000-0000-0000-0000-000000000000", payment_url="", provider="flutterwave", tx_ref="test")
            response = self._start(self.owner, total_amount_micro=12_000_000)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        from apps.health_ops.models import PaymentBillingSession

        session = PaymentBillingSession.objects.get(workflow_session=self.workflow)
        self.assertEqual(session.total_amount_micro, 12_000_000)


class ChannelTipChargingTests(TestCase):
    """ChannelContentTipView/ChannelLiveStreamTipView used to mark a tip
    COMPLETED immediately on POST with no payment call at all — this
    proves it now actually initiates a charge via DirectPaymentIntent and
    stays PENDING until that's confirmed."""

    def setUp(self):
        from apps.broadcasts.models import BroadcastChannel, ChannelContent, ChannelContentType

        self.client = APIClient()
        self.creator = _make_user("+2348030000020", "tip_creator")
        self.tipper = _make_user("+2348030000021", "tip_tipper")
        from apps.broadcasts.models import BroadcastChannelPayoutAccountStatus

        self.channel = BroadcastChannel.objects.create(
            owner_user=self.creator, owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.creator.id,
            display_name="Tip Channel", handle="tipchannel",
            payout_account_status=BroadcastChannelPayoutAccountStatus.ACTIVE,
            flutterwave_subaccount_id="RS_TEST_TIP",
        )
        self.content = ChannelContent.objects.create(
            channel=self.channel, title="Tip Video", content_type=ChannelContentType.VIDEO,
        )

    def test_tip_is_pending_and_creates_payment_intent_not_instant_complete(self):
        from apps.broadcasts.models import ChannelContentTip

        self.client.force_authenticate(self.tipper)
        with patch("apps.billing.direct_payments._ensure_provider_payment_link", side_effect=lambda intent, actor=None: intent):
            response = self.client.post(
                f"/api/v1/broadcasts/channel-contents/{self.content.id}/tips/",
                {"amount_cents": 500, "message": "Nice video!"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        tip = ChannelContentTip.objects.get(content=self.content, user=self.tipper)
        self.assertEqual(tip.status, ChannelContentTip.Status.PENDING)
        self.assertTrue(
            DirectPaymentIntent.objects.filter(
                target_type=DirectPaymentIntent.TARGET_CHANNEL_TIP, target_id=tip.id,
            ).exists()
        )

    def test_mark_target_paid_completes_the_tip(self):
        from apps.billing.direct_payments import _mark_target_paid
        from apps.broadcasts.models import ChannelContentTip

        tip = ChannelContentTip.objects.create(content=self.content, user=self.tipper, amount_cents=500, status=ChannelContentTip.Status.PENDING)
        intent = DirectPaymentIntent.objects.create(
            user=self.tipper, provider="flutterwave", target_type=DirectPaymentIntent.TARGET_CHANNEL_TIP,
            target_id=tip.id, amount_cents=500, currency="USD", status=DirectPaymentIntent.STATUS_PAID,
            tx_ref="kis_direct_channel_tip_test123",
        )
        _mark_target_paid(intent, {})
        tip.refresh_from_db()
        self.assertEqual(tip.status, ChannelContentTip.Status.COMPLETED)
        self.assertEqual(tip.payment_reference, intent.tx_ref)
