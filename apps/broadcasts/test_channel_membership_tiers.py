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

from .models import BroadcastChannel, BroadcastChannelRole, ChannelMembershipTier

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
