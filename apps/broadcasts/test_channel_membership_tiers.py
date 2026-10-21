"""
Channel membership tiers had zero test coverage, and the per-tier detail
endpoint (PATCH to edit perks/price, DELETE to retire) didn't exist at all
despite the RN frontend's MembershipPerksEditor.tsx already calling it -
found during the P1.1 Creator Studio tab audit. Added ChannelMembershipTierDetailView
to close that gap; this covers list/create (pre-existing, unverified) plus
the new detail view.

Run:
  python3 manage.py test apps.broadcasts.test_channel_membership_tiers --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import BroadcastChannel, BroadcastChannelRole, ChannelMembership, ChannelMembershipTier

User = get_user_model()


class MembershipTierTestBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349140000001", password="pw123456", country="NG")
        self.other = User.objects.create_user(phone="+2349140000002", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="tier-channel", display_name="Tier Channel", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.client = APIClient()


class MembershipTierListCreateTests(MembershipTierTestBase):
    def test_manager_can_create_tier(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/membership-tiers/",
            {"title": "Gold", "price_cents": 500, "currency": "usd", "perks": ["Shoutout"]},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        tier = ChannelMembershipTier.objects.get(id=res.data["id"])
        self.assertEqual(tier.currency, "USD")
        self.assertEqual(tier.perks, ["Shoutout"])

    def test_non_manager_cannot_create_tier(self):
        self.client.force_authenticate(self.other)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/membership-tiers/",
            {"title": "Gold", "price_cents": 500}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_only_returns_active_tiers(self):
        ChannelMembershipTier.objects.create(channel=self.channel, title="Active", is_active=True)
        ChannelMembershipTier.objects.create(channel=self.channel, title="Retired", is_active=False)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/membership-tiers/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in res.data}
        self.assertEqual(titles, {"Active"})


class MembershipTierDetailTests(MembershipTierTestBase):
    def setUp(self):
        super().setUp()
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Gold", price_cents=500, currency="USD", perks=["Shoutout"],
        )

    def test_manager_can_edit_perks(self):
        self.client.force_authenticate(self.owner)

        res = self.client.patch(
            f"/api/v1/broadcasts/membership-tiers/{self.tier.id}/",
            {"perks": ["Shoutout", "Early access"]}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.perks, ["Shoutout", "Early access"])

    def test_non_manager_cannot_edit_tier(self):
        self.client.force_authenticate(self.other)

        res = self.client.patch(
            f"/api/v1/broadcasts/membership-tiers/{self.tier.id}/",
            {"perks": ["Hijacked"]}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.perks, ["Shoutout"])

    def test_anonymous_cannot_edit_tier(self):
        res = self.client.patch(
            f"/api/v1/broadcasts/membership-tiers/{self.tier.id}/",
            {"perks": ["Hijacked"]}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_delete_retires_rather_than_hard_deletes(self):
        self.client.force_authenticate(self.owner)

        res = self.client.delete(f"/api/v1/broadcasts/membership-tiers/{self.tier.id}/")

        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.tier.refresh_from_db()
        self.assertFalse(self.tier.is_active)

        list_res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/membership-tiers/")
        self.assertEqual(list_res.data, [])


class ChannelMembershipCancelTests(MembershipTierTestBase):
    # Regression for a dead "cancel membership" button: MembershipScreen.tsx
    # used to cancel by POSTing {tier_id, _method: 'DELETE'} to this same
    # URL - nothing ever read `_method`, so it landed in
    # ChannelMembershipView.post's join branch instead of .delete's cancel
    # branch. The fix was changing the mobile side to issue a real DELETE;
    # these tests lock down the backend half of that contract so a future
    # regression there fails a test instead of only failing on-device.
    def setUp(self):
        super().setUp()
        self.free_tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Supporter", price_cents=0,
        )
        self.paid_tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Gold", price_cents=500, currency="USD",
        )

    def test_delete_cancels_a_free_membership(self):
        self.client.force_authenticate(self.other)
        self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/membership/",
            {"tier_id": str(self.free_tier.id)}, format="json",
        )

        res = self.client.delete(
            f"/api/v1/broadcasts/channels/{self.channel.id}/membership/",
            {"tier_id": str(self.free_tier.id)}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        membership = ChannelMembership.objects.get(user=self.other, tier=self.free_tier)
        self.assertEqual(membership.status, ChannelMembership.Status.CANCELLED)

    def test_delete_cancels_an_active_paid_membership_without_a_new_charge(self):
        from apps.billing.models import WalletTransaction

        membership = ChannelMembership.objects.create(
            user=self.other, tier=self.paid_tier, status=ChannelMembership.Status.ACTIVE,
        )
        self.client.force_authenticate(self.other)

        res = self.client.delete(
            f"/api/v1/broadcasts/channels/{self.channel.id}/membership/",
            {"tier_id": str(self.paid_tier.id)}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        membership.refresh_from_db()
        self.assertEqual(membership.status, ChannelMembership.Status.CANCELLED)
        self.assertFalse(WalletTransaction.objects.filter(meta__target_id=str(membership.id)).exists())

    def test_cancelling_an_already_cancelled_paid_membership_does_not_500(self):
        # The original bug's crash came from POST reusing the membership's
        # stable id as a WalletTransaction.tx_ref on every "cancel" tap -
        # DELETE never touches WalletTransaction at all, so repeating it
        # must be a plain idempotent no-op.
        ChannelMembership.objects.create(
            user=self.other, tier=self.paid_tier, status=ChannelMembership.Status.ACTIVE,
        )
        self.client.force_authenticate(self.other)
        url = f"/api/v1/broadcasts/channels/{self.channel.id}/membership/"

        first = self.client.delete(url, {"tier_id": str(self.paid_tier.id)}, format="json")
        second = self.client.delete(url, {"tier_id": str(self.paid_tier.id)}, format="json")

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)


class ChannelMonetizationSettingsFieldNameTests(MembershipTierTestBase):
    # Regression for MonetizationPanel.tsx reading/writing
    # memberships_enabled/creator_share/platform_share/payout_threshold -
    # none of which exist on ChannelMonetizationSettingsSerializer (the real
    # fields are membership_enabled/revenue_share_pct/payout_threshold_cents),
    # so every PATCH from that panel was a silent no-op (DRF drops unknown
    # keys) and every GET rendered hardcoded JS defaults instead of the
    # real settings.
    def test_patch_with_the_real_field_names_actually_persists(self):
        self.client.force_authenticate(self.owner)

        res = self.client.patch(
            f"/api/v1/broadcasts/channels/{self.channel.id}/monetization/",
            {"membership_enabled": True, "payout_threshold_cents": 2500}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["membership_enabled"], True)
        self.assertEqual(res.data["payout_threshold_cents"], 2500)

        get_res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/monetization/")
        self.assertEqual(get_res.data["membership_enabled"], True)
        self.assertEqual(get_res.data["payout_threshold_cents"], 2500)
