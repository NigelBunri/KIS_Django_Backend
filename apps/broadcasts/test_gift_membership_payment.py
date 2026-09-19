"""
Gift-membership payment leg: previously ChannelMembershipGiftView.post()
created a redeemable, fully-emailed gift for ANY tier regardless of price -
a "gift" that never actually charged the gifter for a paid tier, and
emailed the recipient before any money had changed hands. This tests the
completed flow:

  - Free tier: unchanged - gift is immediately PENDING (redeemable) and the
    recipient is emailed right away (see test_gift_membership_email.py).
  - Paid tier: the gift is created AWAITING_PAYMENT (not redeemable, no
    recipient email yet) and a Stripe checkout session / Flutterwave
    payment link is returned to the gifter, mirroring
    ChannelMembershipView.post()'s own paid-tier branch.
  - Only once the corresponding webhook (apps.billing.views) confirms
    payment does the gift flip to PENDING and the recipient finally get
    emailed - never before.
  - Redeeming an AWAITING_PAYMENT gift is rejected with a clear message
    instead of a bare 404, since "the gifter hasn't paid yet" is a real,
    expected state a recipient could hit if they get the link early.

Run:
  python3 manage.py test apps.broadcasts.test_gift_membership_payment --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import Device, User
from apps.accounts.views import issue_tokens_for_user
from apps.billing.models import WalletTransaction
from apps.broadcasts.models import BroadcastChannel, ChannelMembership, ChannelMembershipGift, ChannelMembershipTier

DEVICE_ID = "gift-membership-payment-test-device"


def _make_user(phone: str, display_name: str = "") -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.email = f"{phone.lstrip('+')}@example.com"
    user.status = "active"
    user.is_active = True
    if display_name:
        user.display_name = display_name
    user.save(update_fields=["email", "status", "is_active", "display_name"])
    Device.objects.create(user=user, device_id=phone, platform="android", is_parent=True, token_version=1)
    return user


def _authed_client(user: User) -> APIClient:
    tokens = issue_tokens_for_user(user, device_id=user.phone)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID=user.phone)
    return client


@override_settings(SECURE_SSL_REDIRECT=False)
class PaidGiftCreationTests(TestCase):
    def setUp(self):
        self.gifter = _make_user("+237699700001", display_name="Aisha K.")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.gifter.id, owner_user=self.gifter,
            handle="paid-gift-test-channel", display_name="Paid Gift Test Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Supporter", price_cents=500, currency="USD", is_active=True,
        )
        self.client = _authed_client(self.gifter)

    def _gift(self, provider="flutterwave", recipient_email="giftee@example.com"):
        return self.client.post(
            "/api/v1/broadcasts/memberships/gift/",
            {"tier_id": str(self.tier.id), "recipient_email": recipient_email, "payment_provider": provider},
            format="json",
        )

    @patch("apps.notifications.email_service.send_gift_membership_email")
    @patch("apps.billing.views._flutterwave_payment_link", return_value={"data": {"link": "https://flw.example/pay/abc"}})
    def test_paid_tier_via_flutterwave_returns_payment_required_and_does_not_email_yet(self, mock_link, mock_send):
        res = self._gift(provider="flutterwave")

        self.assertEqual(res.status_code, 202, res.data)
        self.assertTrue(res.data["payment_required"])
        self.assertEqual(res.data["payment_provider"], "flutterwave")
        self.assertEqual(res.data["payment_url"], "https://flw.example/pay/abc")
        mock_send.assert_not_called()

        gift = ChannelMembershipGift.objects.get(recipient_email="giftee@example.com")
        self.assertEqual(gift.status, ChannelMembershipGift.Status.AWAITING_PAYMENT)
        mock_link.assert_called_once()

    @patch("apps.notifications.email_service.send_gift_membership_email")
    @patch("apps.billing.views._flutterwave_payment_link", return_value={"data": {"link": "https://flw.example/pay/abc"}})
    def test_paid_tier_via_flutterwave_creates_a_matching_wallet_transaction(self, _mock_link, _mock_send):
        self._gift(provider="flutterwave")

        gift = ChannelMembershipGift.objects.get(recipient_email="giftee@example.com")
        tx = WalletTransaction.objects.get(tx_ref=f"KIS-GIFT-{gift.id}")
        self.assertEqual(tx.status, "pending")
        self.assertEqual(tx.meta.get("target_type"), "channel_membership_gift")
        self.assertEqual(tx.meta.get("target_id"), str(gift.id))
        self.assertEqual(tx.meta.get("user_id"), str(self.gifter.id))

    @patch("apps.notifications.email_service.send_gift_membership_email")
    @patch("apps.billing.stripe_payments.create_checkout_session", return_value={"checkout_url": "https://checkout.stripe.example/cs_test_1"})
    @patch("apps.billing.stripe_payments.is_configured", return_value=True)
    def test_paid_tier_via_stripe_returns_payment_required_and_does_not_email_yet(self, _mock_configured, mock_checkout, mock_send):
        res = self._gift(provider="stripe")

        self.assertEqual(res.status_code, 202, res.data)
        self.assertTrue(res.data["payment_required"])
        self.assertEqual(res.data["payment_provider"], "stripe")
        self.assertEqual(res.data["checkout_url"], "https://checkout.stripe.example/cs_test_1")
        mock_send.assert_not_called()

        gift = ChannelMembershipGift.objects.get(recipient_email="giftee@example.com")
        self.assertEqual(gift.status, ChannelMembershipGift.Status.AWAITING_PAYMENT)
        checkout_kwargs = mock_checkout.call_args.kwargs
        self.assertEqual(checkout_kwargs["target_type"], "channel_membership_gift")
        self.assertEqual(checkout_kwargs["target_id"], str(gift.id))

    @patch("apps.billing.stripe_payments.is_configured", return_value=False)
    def test_stripe_unconfigured_returns_503_and_does_not_leave_a_dangling_gift(self, _mock_configured):
        res = self._gift(provider="stripe")

        self.assertEqual(res.status_code, 503)
        self.assertFalse(ChannelMembershipGift.objects.filter(recipient_email="giftee@example.com").exists())


@override_settings(SECURE_SSL_REDIRECT=False)
class RedeemBeforePaymentTests(TestCase):
    def setUp(self):
        self.gifter = _make_user("+237699700002")
        self.recipient = _make_user("+237699700003")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.gifter.id, owner_user=self.gifter,
            handle="redeem-before-payment-channel", display_name="Redeem Before Payment Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Supporter", price_cents=500, currency="USD", is_active=True,
        )
        self.gift = ChannelMembershipGift.objects.create(
            tier=self.tier, gifter=self.gifter, recipient_email="giftee@example.com",
            status=ChannelMembershipGift.Status.AWAITING_PAYMENT,
        )
        self.client = _authed_client(self.recipient)

    def test_redeeming_before_payment_is_rejected_with_a_clear_message(self):
        res = self.client.post(f"/api/v1/broadcasts/memberships/gift/{self.gift.redeem_token}/redeem/", {}, format="json")

        self.assertEqual(res.status_code, 400)
        self.assertIn("payment", str(res.data).lower())
        self.assertFalse(ChannelMembership.objects.filter(user=self.recipient, tier=self.tier).exists())
        self.gift.refresh_from_db()
        self.assertEqual(self.gift.status, ChannelMembershipGift.Status.AWAITING_PAYMENT)


@override_settings(SECURE_SSL_REDIRECT=False, FLW_WEBHOOK_SECRET="test-webhook-secret")
class FlutterwaveGiftPaymentWebhookTests(TestCase):
    def setUp(self):
        self.gifter = _make_user("+237699700004", display_name="Aisha K.")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.gifter.id, owner_user=self.gifter,
            handle="flw-gift-webhook-channel", display_name="FLW Gift Webhook Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Supporter", price_cents=500, currency="USD", is_active=True,
        )
        self.gift = ChannelMembershipGift.objects.create(
            tier=self.tier, gifter=self.gifter, recipient_email="giftee@example.com",
            status=ChannelMembershipGift.Status.AWAITING_PAYMENT,
        )
        self.tx = WalletTransaction.objects.create(
            user=self.gifter, provider="flutterwave", method="card",
            amount_cents=500, currency="USD", status="pending",
            tx_ref=f"KIS-GIFT-{self.gift.id}",
            meta={"target_type": "channel_membership_gift", "target_id": str(self.gift.id), "user_id": str(self.gifter.id)},
        )
        self.client = APIClient()

    def _post_webhook(self):
        return self.client.post(
            "/api/v1/wallet/webhook/flutterwave/",
            {"data": {"tx_ref": self.tx.tx_ref, "status": "successful", "id": "flw-evt-gift-1", "currency": "USD"}},
            format="json",
            HTTP_VERIF_HASH="test-webhook-secret",
            secure=True,
        )

    @patch("apps.notifications.email_service.send_gift_membership_email", return_value=True)
    def test_successful_payment_activates_gift_and_sends_recipient_email(self, mock_send):
        res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        self.gift.refresh_from_db()
        self.assertEqual(self.gift.status, ChannelMembershipGift.Status.PENDING)
        self.assertEqual(self.gift.payment_reference, self.tx.tx_ref)
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["to_email"], "giftee@example.com")
        self.assertEqual(kwargs["gifter_name"], "Aisha K.")
        self.assertEqual(kwargs["redeem_code"], self.gift.redeem_token)

    @patch("apps.notifications.email_service.send_gift_membership_email", return_value=True)
    def test_gift_is_redeemable_after_payment_confirms(self, _mock_send):
        self._post_webhook()
        self.gift.refresh_from_db()

        recipient = _make_user("+237699700005")
        client = _authed_client(recipient)
        res = client.post(f"/api/v1/broadcasts/memberships/gift/{self.gift.redeem_token}/redeem/", {}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["channel_name"], "FLW Gift Webhook Channel")
        self.assertTrue(
            ChannelMembership.objects.filter(user=recipient, tier=self.tier, status=ChannelMembership.Status.ACTIVE).exists()
        )
        self.gift.refresh_from_db()
        self.assertEqual(self.gift.status, ChannelMembershipGift.Status.REDEEMED)


@override_settings(SECURE_SSL_REDIRECT=False)
class StripeGiftPaymentWebhookTests(TestCase):
    def setUp(self):
        self.gifter = _make_user("+237699700006", display_name="Zoe M.")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.gifter.id, owner_user=self.gifter,
            handle="stripe-gift-webhook-channel", display_name="Stripe Gift Webhook Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Supporter", price_cents=500, currency="USD", is_active=True,
        )
        self.gift = ChannelMembershipGift.objects.create(
            tier=self.tier, gifter=self.gifter, recipient_email="giftee2@example.com",
            status=ChannelMembershipGift.Status.AWAITING_PAYMENT,
        )
        self.client = APIClient()

    def _fake_event(self):
        return {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test_gift_1",
                    "amount": 500,
                    "currency": "usd",
                    "metadata": {
                        "target_type": "channel_membership_gift",
                        "target_id": str(self.gift.id),
                        "user_id": str(self.gifter.id),
                    },
                }
            },
        }

    @patch("apps.notifications.email_service.send_gift_membership_email", return_value=True)
    def test_successful_payment_activates_gift_and_sends_recipient_email(self, mock_send):
        with patch("apps.billing.stripe_payments.verify_webhook", return_value=self._fake_event()):
            res = self.client.post(
                "/api/v1/billing/stripe/webhook/", {}, format="json",
                HTTP_STRIPE_SIGNATURE="test-sig", secure=True,
            )

        self.assertEqual(res.status_code, 200)
        self.gift.refresh_from_db()
        self.assertEqual(self.gift.status, ChannelMembershipGift.Status.PENDING)
        self.assertEqual(self.gift.payment_reference, "pi_test_gift_1")
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to_email"], "giftee2@example.com")
