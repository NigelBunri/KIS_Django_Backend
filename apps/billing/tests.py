from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription
from apps.billing.models import WalletLedgerEntry, WalletTransaction
from apps.billing.serializers import WalletAccountSerializer, WalletLedgerEntrySerializer
from apps.billing.services import get_credit_account, get_wallet_account, transfer_balance, upgrade_with_credits
from apps.billing.views import _parse_frontend_money_to_cents


User = get_user_model()
BACKEND_BILLING_ROOT = Path(__file__).resolve().parent
REACT_NATIVE_ROOT = Path("/Users/nigel/dev/KIS")


def _api_url(route_name: str) -> str:
    url = reverse(route_name)
    return url if url.endswith("/") else f"{url}/"


@override_settings(SECURE_SSL_REDIRECT=False)
class BillingWalletFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sender = User.objects.create_user(
            phone="+237676139884",
            country="CM",
            password="pass1234",
            username="sender",
            display_name="Sender User",
            phone_country_code="+237",
            phone_number="676139884",
        )
        self.recipient = User.objects.create_user(
            phone="+237677000111",
            country="CM",
            password="pass1234",
            username="recipient",
            display_name="Recipient User",
            phone_country_code="+237",
            phone_number="677000111",
        )

    @override_settings(KIS_LEGACY_WALLET_TRANSFER_ENABLED=True)
    def test_transfer_service_moves_value_one_way(self):
        sender_wallet = get_wallet_account(self.sender)
        sender_wallet.balance_cents = 10_000
        sender_wallet.save(update_fields=["balance_cents", "updated_at"])

        outbound, inbound = transfer_balance(
            sender=self.sender,
            recipient=self.recipient,
            amount_cents=2_500,
        )

        sender_wallet.refresh_from_db()
        recipient_wallet = get_wallet_account(self.recipient)

        self.assertEqual(sender_wallet.balance_cents, 7_500)
        self.assertEqual(recipient_wallet.balance_cents, 2_500)
        self.assertEqual(outbound.kind, "transfer_out")
        self.assertEqual(outbound.amount_cents, -2_500)
        self.assertEqual(inbound.kind, "transfer_in")
        self.assertEqual(inbound.amount_cents, 2_500)

    @override_settings(KIS_LEGACY_WALLET_TRANSFER_ENABLED=True)
    def test_transfer_endpoint_accepts_local_phone_without_country_code(self):
        sender_wallet = get_wallet_account(self.sender)
        sender_wallet.balance_cents = 5_000
        sender_wallet.save(update_fields=["balance_cents", "updated_at"])

        self.client.force_authenticate(self.sender)
        res = self.client.post(
            _api_url("wallet-transfer"),
            {
                "recipient_phone": "677000111",
                "country": "CM",
                "amount_cents": 1_200,
            },
            format="json",
            secure=True,
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sender_wallet.refresh_from_db()
        recipient_wallet = get_wallet_account(self.recipient)
        self.assertEqual(sender_wallet.balance_cents, 3_800)
        self.assertEqual(recipient_wallet.balance_cents, 1_200)
        self.assertEqual(res.data["outbound"]["amount_cents"], -1_200)
        self.assertEqual(res.data["inbound"]["amount_cents"], 1_200)
        self.assertEqual(res.data["outbound"]["counterparty_name"], self.recipient.display_name)
        self.assertEqual(res.data["outbound"]["counterparty_phone"], self.recipient.phone)
        self.assertEqual(res.data["inbound"]["counterparty_name"], self.sender.display_name)
        self.assertEqual(res.data["inbound"]["counterparty_phone"], self.sender.phone)

    def test_frontend_kisc_major_amount_is_normalized_to_cents(self):
        self.assertEqual(_parse_frontend_money_to_cents({"amount_kisc": "100"}), 1_000_000)

    def test_amount_cents_passthrough_stays_unchanged(self):
        self.assertEqual(_parse_frontend_money_to_cents({"amount_cents": 1250}), 1250)

    def test_transfer_endpoint_disabled_by_default(self):
        self.client.force_authenticate(self.sender)
        res = self.client.post(
            _api_url("wallet-transfer"),
            {
                "recipient_id": str(self.recipient.id),
                "amount_cents": 500,
            },
            format="json",
            secure=True,
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data.get("code"), "legacy_financial_flow_disabled")
        self.assertEqual(WalletLedgerEntry.objects.filter(kind__in=["transfer_in", "transfer_out"]).count(), 0)

    def test_deposit_endpoint_disabled_by_default(self):
        self.client.force_authenticate(self.sender)
        res = self.client.post(
            _api_url("wallet-deposit"),
            {"amount_cents": 1000, "provider": "flutterwave"},
            format="json",
            secure=True,
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data.get("code"), "legacy_financial_flow_disabled")
        self.assertEqual(WalletTransaction.objects.filter(meta__intent="wallet_topup").count(), 0)

    def test_cash_credit_conversion_endpoint_disabled_by_default(self):
        self.client.force_authenticate(self.sender)
        res = self.client.post(
            _api_url("wallet-convert"),
            {"direction": "cash_to_credits", "amount_cents": 1000},
            format="json",
            secure=True,
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data.get("code"), "legacy_financial_flow_disabled")

    def test_wallet_serializer_reframes_legacy_kisc_fields_as_promotional_credits(self):
        wallet = get_wallet_account(self.sender)
        wallet.balance_cents = 10_000
        wallet.save(update_fields=["balance_cents", "updated_at"])

        data = WalletAccountSerializer(wallet).data

        self.assertEqual(data["balance_kisc_label"], "1.00 promotional credits")
        self.assertEqual(data["promotional_credit_label"], "1.00 promotional credits")
        self.assertIsNone(data["balance_usd_label"])
        self.assertFalse(data["can_buy_promotional_credits"])
        self.assertFalse(data["can_transfer_promotional_credits"])
        self.assertFalse(data["can_convert_promotional_credits_to_cash"])
        serialized = str(dict(data))
        self.assertNotIn("KISC", serialized)
        self.assertNotIn("KIS Coin", serialized)

    def test_ledger_serializer_exposes_promotional_credit_labels_without_exchange_copy(self):
        entry = WalletLedgerEntry.objects.create(
            user=self.sender,
            kind="promo",
            amount_cents=0,
            credits_delta=25,
            balance_after_cents=0,
            credits_after=25,
            reference="promo:PHASE2",
        )

        data = WalletLedgerEntrySerializer(entry).data

        self.assertEqual(data["amount_promotional_credit_label"], "0.00 promotional credits")
        self.assertEqual(data["credits_delta_label"], "+25 promotional credits")
        serialized = str(dict(data))
        self.assertNotIn("KISC", serialized)
        self.assertNotIn("KIS Coin", serialized)

    def test_phase2_public_wallet_copy_does_not_reintroduce_exchange_or_transfer_language(self):
        backend_files = [
            BACKEND_BILLING_ROOT / "serializers.py",
            BACKEND_BILLING_ROOT / "promotional_credits.py",
        ]
        frontend_files = [
            REACT_NATIVE_ROOT / "src/screens/tabs/profile-screen/WalletModal.tsx",
            REACT_NATIVE_ROOT / "src/screens/tabs/profile/useProfileController.ts",
            REACT_NATIVE_ROOT / "src/screens/tabs/ProfileScreen.tsx",
            REACT_NATIVE_ROOT / "src/screens/tabs/profile/profile/sheets/UpgradeSheet.tsx",
            REACT_NATIVE_ROOT / "src/screens/tabs/profile/components/dashboard/ProfileDashboardBlocks.tsx",
        ]
        files = backend_files + [path for path in frontend_files if path.exists()]
        disallowed = [
            "1 KISC",
            "Add KIS Coins",
            "Send KIS Coins",
            "Manage your KIS Coin wallet",
            "KIS Coin confirmation",
            "Not enough KIS Coins",
            "Verify the recipient first before sending KIS Coins",
        ]

        matches: list[str] = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            for needle in disallowed:
                if needle in text:
                    matches.append(f"{path}: {needle}")

        self.assertEqual(matches, [])

    @override_settings(KIS_LEGACY_WALLET_TRANSFER_ENABLED=True)
    def test_transfer_endpoint_rejects_self_transfer(self):
        self.client.force_authenticate(self.sender)
        res = self.client.post(
            _api_url("wallet-transfer"),
            {
                "recipient_id": str(self.sender.id),
                "amount_cents": 500,
            },
            format="json",
            secure=True,
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("detail"), "You cannot transfer to your own account.")

    @override_settings(KIS_LEGACY_WALLET_TRANSFER_ENABLED=True)
    def test_transfer_endpoint_rejects_unverified_phone(self):
        self.client.force_authenticate(self.sender)
        res = self.client.post(
            _api_url("wallet-transfer"),
            {
                "recipient_phone": "699999999",
                "country": "CM",
                "amount_cents": 500,
            },
            format="json",
            secure=True,
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("detail"), "Recipient phone is not registered.")


class BillingTierUpgradeFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+237699000222",
            country="CM",
            password="pass1234",
            username="tieruser",
            display_name="Tier User",
            phone_country_code="+237",
            phone_number="699000222",
            tier="Free",
        )

    def test_upgrade_with_credits_deducts_balance_and_creates_active_subscription(self):
        tier, _ = AccountTier.objects.get_or_create(name="Phase5-Pro", defaults={"price_cents": 200})
        tier.price_cents = 200
        tier.save(update_fields=["price_cents", "updated_at"])

        credits = get_credit_account(self.user)
        credits.credits = 100
        credits.save(update_fields=["credits", "updated_at"])

        result = upgrade_with_credits(self.user, tier)

        self.assertEqual(result["tier"], tier.name)
        self.assertEqual(result["required_credits"], 40)

        credits.refresh_from_db()
        self.assertEqual(credits.credits, 60)

        sub = Subscription.objects.filter(user=self.user, status="active").select_related("tier").latest("created_at")
        self.assertEqual(sub.tier.name, tier.name)

        ledger = WalletLedgerEntry.objects.filter(user=self.user, kind="tier_upgrade").latest("created_at")
        self.assertEqual(ledger.credits_delta, -40)
        self.assertEqual(ledger.meta.get("tier"), tier.name)

    def test_upgrade_with_credits_rejects_when_balance_insufficient(self):
        tier, _ = AccountTier.objects.get_or_create(name="Phase5-Business", defaults={"price_cents": 600})
        tier.price_cents = 600
        tier.save(update_fields=["price_cents", "updated_at"])

        credits = get_credit_account(self.user)
        credits.credits = 1
        credits.save(update_fields=["credits", "updated_at"])

        with self.assertRaisesMessage(ValueError, "Insufficient credits for upgrade."):
            upgrade_with_credits(self.user, tier)


@override_settings(SECURE_SSL_REDIRECT=False)
class CheckContactApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.requester = User.objects.create_user(
            phone="+237690111111",
            country="CM",
            password="pass1234",
            username="requester",
            phone_country_code="+237",
            phone_number="690111111",
        )
        self.target = User.objects.create_user(
            phone="+237690222222",
            country="CM",
            password="pass1234",
            username="target",
            display_name="Target User",
            phone_country_code="+237",
            phone_number="690222222",
        )

    def test_check_contact_finds_user_with_local_phone_digits(self):
        self.client.force_authenticate(self.requester)
        res = self.client.get(
            reverse("check_contact"),
            {"phone": "690222222", "country": "CM"},
            secure=True,
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data.get("registered"))
        self.assertEqual(str(res.data.get("userId")), str(self.target.id))

    def test_check_contact_finds_user_with_e164_phone(self):
        self.client.force_authenticate(self.requester)
        res = self.client.get(
            reverse("check_contact"),
            {"phone": self.target.phone, "country": "CM"},
            secure=True,
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data.get("registered"))
        self.assertEqual(str(res.data.get("userId")), str(self.target.id))


@override_settings(SECURE_SSL_REDIRECT=False, KIS_LEGACY_WALLET_TRANSFER_ENABLED=True)
class WalletTransferPayloadValidationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sender = User.objects.create_user(
            phone="+237691111111",
            country="CM",
            password="pass1234",
            username="payload_sender",
            phone_country_code="+237",
            phone_number="691111111",
        )
        self.recipient = User.objects.create_user(
            phone="+237692222222",
            country="CM",
            password="pass1234",
            username="payload_recipient",
            phone_country_code="+237",
            phone_number="692222222",
        )
        self.other_user = User.objects.create_user(
            phone="+237693333333",
            country="CM",
            password="pass1234",
            username="payload_other",
            phone_country_code="+237",
            phone_number="693333333",
        )
        self.client.force_authenticate(self.sender)

    def test_transfer_rejects_amount_and_credits_together(self):
        res = self.client.post(
            _api_url("wallet-transfer"),
            {
                "recipient_id": str(self.recipient.id),
                "amount_cents": 100,
                "credits": 1,
            },
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            res.data.get("detail"),
            "Provide either amount_cents or credits, not both.",
        )

    def test_transfer_rejects_invalid_numeric_field(self):
        res = self.client.post(
            _api_url("wallet-transfer"),
            {
                "recipient_id": str(self.recipient.id),
                "amount_cents": "abc",
            },
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("detail"), "Invalid amount_cents")

    def test_transfer_rejects_mismatched_recipient_id_and_phone(self):
        res = self.client.post(
            _api_url("wallet-transfer"),
            {
                "recipient_id": str(self.recipient.id),
                "recipient_phone": self.other_user.phone_number,
                "country": "CM",
                "amount_cents": 250,
            },
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            res.data.get("detail"),
            "Recipient phone does not match recipient_id.",
        )

    def test_transfer_requires_recipient_reference(self):
        res = self.client.post(
            _api_url("wallet-transfer"),
            {"amount_cents": 250},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            res.data.get("detail"),
            "recipient_id or recipient_phone is required",
        )


@override_settings(SECURE_SSL_REDIRECT=False)
class WalletSubscriptionLifecycleApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+237694444444",
            country="CM",
            password="pass1234",
            username="sub_user",
            phone_country_code="+237",
            phone_number="694444444",
            tier="Phase5 Business Pro",
        )
        self.client.force_authenticate(self.user)

        self.free_tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Free",
            defaults={"price_cents": 0},
        )
        self.pro_tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Pro",
            defaults={"price_cents": 500},
        )
        self.business_tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Business",
            defaults={"price_cents": 2000},
        )
        self.business_pro_tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Business Pro",
            defaults={"price_cents": 4000},
        )

    def _make_active_subscription(self, tier: AccountTier) -> Subscription:
        Subscription.objects.filter(user=self.user, status="active").update(status="superseded")
        self.user.tier = tier.name
        self.user.save(update_fields=["tier", "updated_at"])
        return Subscription.objects.create(
            user=self.user,
            tier=tier,
            status="active",
            started_at=timezone.now() - timedelta(days=10),
            ends_at=timezone.now() + timedelta(days=20),
        )

    def test_subscription_cancel_sets_period_end_flags(self):
        sub = self._make_active_subscription(self.business_tier)
        res = self.client.post(
            _api_url("wallet-subscription-cancel"),
            {"immediate": False},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertTrue(sub.cancel_at_period_end)
        self.assertIsNotNone(sub.canceled_at)
        self.assertIsNotNone(sub.grace_ends_at)

    def test_subscription_resume_clears_cancellation_flags(self):
        sub = self._make_active_subscription(self.business_tier)
        sub.cancel_at_period_end = True
        sub.canceled_at = timezone.now()
        sub.grace_ends_at = timezone.now() + timedelta(days=7)
        sub.save(
            update_fields=[
                "cancel_at_period_end",
                "canceled_at",
                "grace_ends_at",
                "updated_at",
            ]
        )

        res = self.client.post(
            _api_url("wallet-subscription-resume"),
            {},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertFalse(sub.cancel_at_period_end)
        self.assertIsNone(sub.canceled_at)
        self.assertIsNone(sub.grace_ends_at)

    def test_subscription_cancel_immediate_marks_cancelled_and_sets_user_tier_free(self):
        sub = self._make_active_subscription(self.business_tier)
        res = self.client.post(
            _api_url("wallet-subscription-cancel"),
            {"immediate": True},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(sub.status, "cancelled")
        self.assertFalse(sub.cancel_at_period_end)
        expected_free = AccountTier.objects.filter(name__iexact="Free").first()
        if expected_free:
            self.assertEqual(self.user.tier, expected_free.name)
        else:
            self.assertEqual(self.user.tier, self.free_tier.name)

    def test_subscription_downgrade_sets_pending_tier(self):
        sub = self._make_active_subscription(self.business_pro_tier)
        res = self.client.post(
            _api_url("wallet-subscription-downgrade"),
            {"tier": str(self.pro_tier.id)},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.pending_tier_id, self.pro_tier.id)
        self.assertTrue(sub.cancel_at_period_end)
        self.assertGreaterEqual(int(res.data.get("proration_credit_cents", 0)), 0)

    def test_subscription_downgrade_rejects_non_lower_target(self):
        self._make_active_subscription(self.pro_tier)
        res = self.client.post(
            _api_url("wallet-subscription-downgrade"),
            {"tier": str(self.business_tier.id)},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("detail"), "Downgrade requires a lower tier.")

    def test_subscription_cancel_rejects_without_active_subscription(self):
        res = self.client.post(
            _api_url("wallet-subscription-cancel"),
            {"immediate": False},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("detail"), "No active subscription.")

    def test_subscription_resume_rejects_without_active_subscription(self):
        res = self.client.post(
            _api_url("wallet-subscription-resume"),
            {},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("detail"), "No active subscription.")

    def test_subscription_downgrade_rejects_without_active_subscription(self):
        res = self.client.post(
            _api_url("wallet-subscription-downgrade"),
            {"tier": str(self.pro_tier.id)},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("detail"), "No active subscription.")

    def test_subscription_downgrade_rejects_when_current_tier_missing(self):
        self.user.tier = "Phase5 Unknown"
        self.user.save(update_fields=["tier", "updated_at"])
        Subscription.objects.create(
            user=self.user,
            tier=None,
            status="active",
            started_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=29),
        )
        res = self.client.post(
            _api_url("wallet-subscription-downgrade"),
            {"tier": str(self.pro_tier.id)},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("detail"), "Current tier missing.")

    def test_subscription_downgrade_requires_tier_id(self):
        self._make_active_subscription(self.business_tier)
        res = self.client.post(
            _api_url("wallet-subscription-downgrade"),
            {},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_subscription_downgrade_rejects_unknown_tier_id(self):
        self._make_active_subscription(self.business_tier)
        res = self.client.post(
            _api_url("wallet-subscription-downgrade"),
            {"tier": "11111111-1111-1111-1111-111111111111"},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(SECURE_SSL_REDIRECT=False)
class WalletUpgradeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+237695555555",
            country="CM",
            password="pass1234",
            username="upgrade_user",
            phone_country_code="+237",
            phone_number="695555555",
            tier="Phase5 Basic",
        )
        self.client.force_authenticate(self.user)

    def test_upgrade_endpoint_succeeds_with_credits(self):
        tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Pro Upgrade",
            defaults={"price_cents": 100},
        )
        tier.price_cents = 100
        tier.save(update_fields=["price_cents", "updated_at"])

        credit = get_credit_account(self.user)
        credit.credits = 25
        credit.save(update_fields=["credits", "updated_at"])

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "credits"},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get("tier"), tier.name)
        self.assertEqual(int(res.data.get("required_credits", 0)), 20)

        active = Subscription.objects.filter(user=self.user, status="active").select_related("tier").first()
        self.assertIsNotNone(active)
        self.assertEqual(active.tier_id, tier.id)

    def test_upgrade_endpoint_rejects_wallet_payment_by_default(self):
        tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Pro Wallet Disabled",
            defaults={"price_cents": 100},
        )
        tier.price_cents = 100
        tier.save(update_fields=["price_cents", "updated_at"])

        wallet = get_wallet_account(self.user)
        wallet.balance_cents = 500
        wallet.save(update_fields=["balance_cents", "updated_at"])

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "kisc"},
            format="json",
            secure=True,
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data.get("code"), "legacy_financial_flow_disabled")
        self.assertFalse(Subscription.objects.filter(user=self.user, status="active").exists())

    def test_upgrade_endpoint_rejects_same_or_lower_tier(self):
        current_tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Business Current",
            defaults={"price_cents": 1000},
        )
        current_tier.price_cents = 1000
        current_tier.save(update_fields=["price_cents", "updated_at"])

        target_tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Pro Lower",
            defaults={"price_cents": 200},
        )
        target_tier.price_cents = 200
        target_tier.save(update_fields=["price_cents", "updated_at"])

        Subscription.objects.create(
            user=self.user,
            tier=current_tier,
            status="active",
            started_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=29),
        )
        self.user.tier = current_tier.name
        self.user.save(update_fields=["tier", "updated_at"])

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(target_tier.id), "payment_method": "credits"},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            res.data.get("detail"),
            "Downgrades or same-tier upgrades are not supported yet.",
        )

    def test_upgrade_endpoint_free_tier_branch_applies_upgrade_without_payment(self):
        Subscription.objects.filter(user=self.user, status="active").update(status="ended")
        tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Pro Free Upgrade",
            defaults={"price_cents": 0},
        )
        tier.price_cents = 0
        tier.save(update_fields=["price_cents", "updated_at"])

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "card"},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get("status"), "success")
        self.assertEqual(res.data.get("tier"), tier.name)

        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, tier.name)
        active = Subscription.objects.filter(user=self.user, status="active").select_related("tier").first()
        self.assertIsNotNone(active)
        self.assertEqual(active.tier_id, tier.id)
        ledger = WalletLedgerEntry.objects.filter(user=self.user, kind="tier_upgrade").latest("created_at")
        self.assertEqual(ledger.meta.get("source"), "free")

    def test_upgrade_endpoint_card_mock_marks_transaction_success(self):
        tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Business Upgrade Paid",
            defaults={"price_cents": 350},
        )
        tier.price_cents = 350
        tier.save(update_fields=["price_cents", "updated_at"])

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "card", "mock": True},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get("status"), "success")
        tx_ref = str(res.data.get("tx_ref") or "")
        self.assertTrue(tx_ref.startswith("kis_upgrade_"))

        tx = WalletTransaction.objects.get(tx_ref=tx_ref)
        self.assertEqual(tx.status, "success")
        self.assertEqual(tx.method, "card")
        self.assertEqual(tx.amount_cents, tier.price_cents)

        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, tier.name)
        active = Subscription.objects.filter(user=self.user, status="active").select_related("tier").first()
        self.assertIsNotNone(active)
        self.assertEqual(active.tier_id, tier.id)
        ledger = WalletLedgerEntry.objects.filter(user=self.user, kind="tier_upgrade").latest("created_at")
        self.assertEqual(ledger.meta.get("source"), "mock")
        self.assertEqual(ledger.amount_cents, tier.price_cents)

    @patch("apps.billing.views._flutterwave_payment_link")
    @patch("apps.billing.views._ensure_payments_ready")
    def test_upgrade_endpoint_card_creates_pending_transaction_with_payment_url(
        self,
        ensure_payments_ready_mock,
        flutterwave_payment_link_mock,
    ):
        ensure_payments_ready_mock.return_value = None
        flutterwave_payment_link_mock.return_value = {"data": {"link": "https://pay.kis.test/checkout"}}

        tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Partner Upgrade Paid",
            defaults={"price_cents": 500},
        )
        tier.price_cents = 500
        tier.save(update_fields=["price_cents", "updated_at"])

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "card"},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get("status"), "pending")
        self.assertEqual(res.data.get("payment_url"), "https://pay.kis.test/checkout")

        tx = WalletTransaction.objects.get(tx_ref=res.data["tx_ref"])
        self.assertEqual(tx.status, "pending")
        self.assertEqual(tx.payment_url, "https://pay.kis.test/checkout")
        self.assertEqual(tx.meta.get("intent"), "tier_upgrade")
        self.assertFalse(Subscription.objects.filter(user=self.user, status="active").exists())

    @override_settings(FLW_SECRET_KEY="")
    def test_upgrade_endpoint_card_marks_transaction_failed_when_payments_not_configured(self):
        tier, _ = AccountTier.objects.get_or_create(
            name="Phase5 Partner Pro Upgrade Paid",
            defaults={"price_cents": 900},
        )
        tier.price_cents = 900
        tier.save(update_fields=["price_cents", "updated_at"])

        res = self.client.post(
            _api_url("wallet-upgrade"),
            {"tier": str(tier.id), "payment_method": "card"},
            format="json",
            secure=True,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("detail"), "FLW_SECRET_KEY is not configured")

        tx = WalletTransaction.objects.filter(user=self.user, method="card").latest("created_at")
        self.assertEqual(tx.status, "failed")
        self.assertEqual(tx.meta.get("intent"), "tier_upgrade")


@override_settings(SECURE_SSL_REDIRECT=False, KIS_LEGACY_WALLET_TRANSFER_ENABLED=True)
class WalletHistoryManagementApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+237696666666",
            country="CM",
            password="pass1234",
            username="history_user",
            display_name="History User",
            phone_country_code="+237",
            phone_number="696666666",
        )
        self.peer = User.objects.create_user(
            phone="+237697777777",
            country="CM",
            password="pass1234",
            username="history_peer",
            display_name="History Peer",
            phone_country_code="+237",
            phone_number="697777777",
        )
        self.client.force_authenticate(self.user)

        wallet = get_wallet_account(self.user)
        wallet.balance_cents = 5_000
        wallet.save(update_fields=["balance_cents", "updated_at"])
        transfer_balance(sender=self.user, recipient=self.peer, amount_cents=500)
        self.ledger_entry = WalletLedgerEntry.objects.filter(user=self.user, is_deleted=False).latest("created_at")

    def test_delete_ledger_entry_soft_deletes_and_hides_entry(self):
        url = f"/api/v1/wallet/ledger/{self.ledger_entry.id}/"
        delete_res = self.client.delete(url, secure=True)
        self.assertEqual(delete_res.status_code, status.HTTP_200_OK)

        self.ledger_entry.refresh_from_db()
        self.assertTrue(self.ledger_entry.is_deleted)

        ledger_res = self.client.get(_api_url("wallet-ledger"), secure=True)
        self.assertEqual(ledger_res.status_code, status.HTTP_200_OK)
        ids = {str(item.get("id")) for item in ledger_res.data.get("results", [])}
        self.assertNotIn(str(self.ledger_entry.id), ids)

    def test_delete_transaction_soft_deletes_and_hides_entry(self):
        tx = WalletTransaction.objects.create(
            user=self.user,
            provider="flutterwave",
            method="card",
            amount_cents=2_500,
            currency="USD",
            status="failed",
            tx_ref="kis_history_tx_ref_001",
            meta={"intent": "wallet_topup"},
        )

        url = f"/api/v1/wallet/transactions/{tx.id}/"
        delete_res = self.client.delete(url, secure=True)
        self.assertEqual(delete_res.status_code, status.HTTP_200_OK)

        tx.refresh_from_db()
        self.assertTrue(tx.is_deleted)

        transactions_res = self.client.get(_api_url("wallet-transactions"), secure=True)
        self.assertEqual(transactions_res.status_code, status.HTTP_200_OK)
        ids = {str(item.get("id")) for item in transactions_res.data.get("results", [])}
        self.assertNotIn(str(tx.id), ids)
