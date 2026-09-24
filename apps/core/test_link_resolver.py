from __future__ import annotations

import secrets

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog
from apps.broadcasts.models import (
    BroadcastChannel, ChannelLiveStream, ChannelLiveStreamGuest,
    ChannelMembershipGift, ChannelMembershipTier,
)
from apps.chat.models import Conversation, ConversationType
from apps.chat.models import ContactShareLink
from apps.communities.models import Community
from apps.groups.models import Group
from apps.partners.models import Partner, PartnerInvite
from apps.referrals.models import ReferralCode

User = get_user_model()


def _make_user(phone, username):
    return User.objects.create_user(
        phone=phone,
        country="NG",
        password="pass1234",
        username=username,
        email=f"{username}@example.com",
    )


class PublicLinkResolveViewTests(TestCase):
    """Real HTTP requests through the actual URL router (not calling the
    view function directly) - the public-facing behavior this endpoint
    exists for is being unauthenticated and reachable at a stable path,
    both of which only a real routed request can verify."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+15550000101", "resolver_owner")

    def _url(self, link_type, token):
        return reverse("core:public-link-resolve", kwargs={"link_type": link_type, "token": token})

    def _make_group_conversation(self):
        return Conversation.objects.create(type=ConversationType.GROUP, title="x", created_by=self.owner)

    # --- unsupported / malformed --------------------------------------

    def test_unsupported_type_is_404_not_crash(self):
        resp = self.client.get(self._url("not-a-real-type", "whatever"))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data["status"], "invalid")

    def test_nonexistent_token_is_404(self):
        resp = self.client.get(self._url("group", "does-not-exist"))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data["status"], "invalid")

    # --- group -----------------------------------------------------------

    def test_valid_group_token_resolves_unauthenticated(self):
        token = secrets.token_urlsafe(24)
        Group.objects.create(
            name="Kingdom Youth", invite_token=token, owner=self.owner,
            conversation=self._make_group_conversation(),
        )
        resp = self.client.get(self._url("group", token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ok")
        self.assertEqual(resp.data["name"], "Kingdom Youth")

    def test_archived_group_token_is_revoked_not_ok(self):
        token = secrets.token_urlsafe(24)
        Group.objects.create(
            name="Old Group", invite_token=token, is_archived=True, owner=self.owner,
            conversation=self._make_group_conversation(),
        )
        resp = self.client.get(self._url("group", token))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "revoked")

    # --- community ---------------------------------------------------

    def test_valid_community_token_resolves(self):
        token = secrets.token_urlsafe(24)
        Community.objects.create(
            owner=self.owner, name="Bible Study", invite_token=token,
            is_active=True, allow_join_link=True,
        )
        resp = self.client.get(self._url("community", token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Bible Study")

    def test_inactive_community_is_revoked(self):
        token = secrets.token_urlsafe(24)
        Community.objects.create(
            owner=self.owner, name="Closed", invite_token=token,
            is_active=False, allow_join_link=True,
        )
        resp = self.client.get(self._url("community", token))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "revoked")

    def test_join_link_disabled_is_revoked_even_with_valid_token(self):
        token = secrets.token_urlsafe(24)
        Community.objects.create(
            owner=self.owner, name="Locked", invite_token=token,
            is_active=True, allow_join_link=False,
        )
        resp = self.client.get(self._url("community", token))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "revoked")

    # --- partner -------------------------------------------------------

    def test_valid_partner_invite_resolves(self):
        partner = Partner.objects.create(name="Grace Church", owner=self.owner, slug="grace-church-resolver")
        invite = PartnerInvite.objects.create(partner=partner)
        resp = self.client.get(self._url("partner", invite.code))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Grace Church")

    def test_inactive_partner_invite_is_revoked(self):
        partner = Partner.objects.create(name="Inactive Org", owner=self.owner, slug="inactive-org-resolver")
        invite = PartnerInvite.objects.create(partner=partner, is_active=False)
        resp = self.client.get(self._url("partner", invite.code))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "revoked")

    def test_exhausted_partner_invite_is_expired_status(self):
        partner = Partner.objects.create(name="Full Org", owner=self.owner, slug="full-org-resolver")
        invite = PartnerInvite.objects.create(partner=partner, max_uses=1, use_count=1)
        resp = self.client.get(self._url("partner", invite.code))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "expired")

    # --- call (no server-side validity check by design) ------------------

    def test_call_type_returns_generic_open_app_response(self):
        resp = self.client.get(self._url("call", "any-token-at-all"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ok")
        self.assertEqual(resp.data["type"], "call")

    def test_broadcast_call_type_returns_generic_open_app_response(self):
        resp = self.client.get(self._url("broadcast-call", "any-token-at-all"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["type"], "broadcast-call")

    # --- contact -----------------------------------------------------

    def test_valid_contact_link_resolves_without_exposing_owner_id_or_phone(self):
        link = ContactShareLink.objects.create(owner=self.owner, token="resolvertoken1")
        resp = self.client.get(self._url("contact", "resolvertoken1"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["type"], "contact")
        body_text = str(resp.data)
        self.assertNotIn(str(self.owner.id), body_text)
        self.assertNotIn("+15550000101", body_text)

    def test_inactive_contact_link_is_revoked(self):
        ContactShareLink.objects.create(owner=self.owner, token="resolvertoken2", is_active=False)
        resp = self.client.get(self._url("contact", "resolvertoken2"))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "revoked")

    def test_expired_contact_link_is_expired_status(self):
        from datetime import timedelta

        from django.utils import timezone

        ContactShareLink.objects.create(
            owner=self.owner, token="resolvertoken3",
            expires_at=timezone.now() - timedelta(hours=1),
        )
        resp = self.client.get(self._url("contact", "resolvertoken3"))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "expired")

    # --- referral ----------------------------------------------------------

    def test_valid_referral_code_resolves(self):
        code_record = ReferralCode.get_or_create_for_user(self.owner)
        resp = self.client.get(self._url("referral", code_record.code))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["type"], "referral")
        self.assertEqual(resp.data["referral_code"], code_record.code)

    def test_referral_code_is_case_insensitive(self):
        code_record = ReferralCode.get_or_create_for_user(self.owner)
        resp = self.client.get(self._url("referral", code_record.code.lower()))
        self.assertEqual(resp.status_code, 200)

    def test_nonexistent_referral_code_is_404_not_500(self):
        resp = self.client.get(self._url("referral", "NOTAREALCODE"))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data["status"], "invalid")

    # --- gift (share-link reachability fix, Sep 2026) -----------------------

    def _make_gift_channel_and_tier(self):
        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="resolver-gift-channel", display_name="Resolver Gift Channel",
        )
        tier = ChannelMembershipTier.objects.create(
            channel=channel, title="Supporter", price_cents=0, currency="USD", is_active=True,
        )
        return channel, tier

    def test_valid_pending_gift_resolves_without_exposing_recipient_email(self):
        _, tier = self._make_gift_channel_and_tier()
        gift = ChannelMembershipGift.objects.create(
            tier=tier, gifter=self.owner, recipient_email="secret-recipient@example.com",
            status=ChannelMembershipGift.Status.PENDING,
        )
        resp = self.client.get(self._url("gift", gift.redeem_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ok")
        self.assertEqual(resp.data["name"], "Resolver Gift Channel")
        body_text = str(resp.data)
        self.assertNotIn("secret-recipient@example.com", body_text)

    def test_awaiting_payment_gift_is_revoked(self):
        _, tier = self._make_gift_channel_and_tier()
        gift = ChannelMembershipGift.objects.create(
            tier=tier, gifter=self.owner, recipient_email="x@example.com",
            status=ChannelMembershipGift.Status.AWAITING_PAYMENT,
        )
        resp = self.client.get(self._url("gift", gift.redeem_token))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "revoked")

    def test_already_redeemed_gift_is_revoked(self):
        _, tier = self._make_gift_channel_and_tier()
        gift = ChannelMembershipGift.objects.create(
            tier=tier, gifter=self.owner, recipient_email="x@example.com",
            status=ChannelMembershipGift.Status.REDEEMED,
        )
        resp = self.client.get(self._url("gift", gift.redeem_token))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "revoked")

    def test_expired_gift_is_expired_status(self):
        from datetime import timedelta
        from django.utils import timezone

        _, tier = self._make_gift_channel_and_tier()
        gift = ChannelMembershipGift.objects.create(
            tier=tier, gifter=self.owner, recipient_email="x@example.com",
            status=ChannelMembershipGift.Status.PENDING,
            expires_at=timezone.now() - timedelta(days=1),
        )
        resp = self.client.get(self._url("gift", gift.redeem_token))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "expired")

    def test_resolving_a_gift_does_not_change_its_status_or_redeem_it(self):
        # This is a read-only preview - it must never touch redemption.
        _, tier = self._make_gift_channel_and_tier()
        gift = ChannelMembershipGift.objects.create(
            tier=tier, gifter=self.owner, recipient_email="x@example.com",
            status=ChannelMembershipGift.Status.PENDING,
        )
        self.client.get(self._url("gift", gift.redeem_token))
        self.client.get(self._url("gift", gift.redeem_token))
        gift.refresh_from_db()
        self.assertEqual(gift.status, ChannelMembershipGift.Status.PENDING)
        self.assertIsNone(gift.recipient_id)

    def test_nonexistent_gift_token_is_404(self):
        resp = self.client.get(self._url("gift", "does-not-exist"))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data["status"], "invalid")

    # --- live-guest (share-link reachability fix, Sep 2026) -----------------

    def _make_livestream(self, status="scheduled"):
        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle=f"resolver-guest-channel-{status}", display_name="Resolver Guest Channel",
        )
        return ChannelLiveStream.objects.create(channel=channel, title="Sunday Service", status=status)

    def test_valid_invited_guest_resolves_without_exposing_email(self):
        stream = self._make_livestream()
        guest = ChannelLiveStreamGuest.objects.create(
            live_stream=stream, email="secret-guest@example.com",
            role=ChannelLiveStreamGuest.Role.GUEST, invited_by=self.owner,
        )
        resp = self.client.get(self._url("live-guest", guest.invite_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ok")
        self.assertEqual(resp.data["name"], "Resolver Guest Channel")
        body_text = str(resp.data)
        self.assertNotIn("secret-guest@example.com", body_text)

    def test_declined_guest_is_revoked(self):
        stream = self._make_livestream()
        guest = ChannelLiveStreamGuest.objects.create(
            live_stream=stream, email="x@example.com",
            role=ChannelLiveStreamGuest.Role.GUEST, invited_by=self.owner,
            status=ChannelLiveStreamGuest.Status.DECLINED,
        )
        resp = self.client.get(self._url("live-guest", guest.invite_token))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "revoked")

    def test_ended_livestream_guest_link_is_expired_status(self):
        stream = self._make_livestream(status="ended")
        guest = ChannelLiveStreamGuest.objects.create(
            live_stream=stream, email="x@example.com",
            role=ChannelLiveStreamGuest.Role.GUEST, invited_by=self.owner,
        )
        resp = self.client.get(self._url("live-guest", guest.invite_token))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "expired")

    def test_guest_link_past_30_day_cap_is_expired_status(self):
        from datetime import timedelta
        from django.utils import timezone

        stream = self._make_livestream()
        guest = ChannelLiveStreamGuest.objects.create(
            live_stream=stream, email="x@example.com",
            role=ChannelLiveStreamGuest.Role.GUEST, invited_by=self.owner,
        )
        ChannelLiveStreamGuest.objects.filter(id=guest.id).update(created_at=timezone.now() - timedelta(days=31))
        resp = self.client.get(self._url("live-guest", guest.invite_token))
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data["status"], "expired")

    def test_resolving_a_guest_link_does_not_bind_it_to_anyone(self):
        # Read-only preview - must never touch redemption/claiming.
        stream = self._make_livestream()
        guest = ChannelLiveStreamGuest.objects.create(
            live_stream=stream, email="x@example.com",
            role=ChannelLiveStreamGuest.Role.GUEST, invited_by=self.owner,
        )
        self.client.get(self._url("live-guest", guest.invite_token))
        guest.refresh_from_db()
        self.assertIsNone(guest.user_id)
        self.assertEqual(guest.status, ChannelLiveStreamGuest.Status.INVITED)

    def test_nonexistent_guest_token_is_404(self):
        resp = self.client.get(self._url("live-guest", "does-not-exist"))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data["status"], "invalid")

    def test_referral_response_never_includes_owner_contact_info(self):
        code_record = ReferralCode.get_or_create_for_user(self.owner)
        resp = self.client.get(self._url("referral", code_record.code))
        body_text = str(resp.data)
        self.assertNotIn("+15550000101", body_text)
        self.assertNotIn("resolver_owner@example.com", body_text)

    def test_resolving_a_referral_link_logs_a_click_but_never_creates_a_referral(self):
        # Clicking must never itself grant or imply a reward - only real
        # registration (apps.referrals.services.register_referral) does.
        from apps.referrals.models import Referral

        code_record = ReferralCode.get_or_create_for_user(self.owner)
        self.client.get(self._url("referral", code_record.code))
        self.assertFalse(Referral.objects.filter(referrer=self.owner).exists())
        self.assertTrue(
            AuditLog.objects.filter(actor_id=self.owner.id, action="referral.link_clicked").exists()
        )

    def test_resolving_same_referral_link_twice_logs_two_separate_clicks(self):
        code_record = ReferralCode.get_or_create_for_user(self.owner)
        self.client.get(self._url("referral", code_record.code))
        self.client.get(self._url("referral", code_record.code))
        self.assertEqual(
            AuditLog.objects.filter(actor_id=self.owner.id, action="referral.link_clicked").count(), 2,
        )

    # --- no PII leakage --------------------------------------------------

    def test_partner_response_never_includes_owner_contact_info(self):
        partner = Partner.objects.create(name="Privacy Org", owner=self.owner, slug="privacy-org-resolver")
        invite = PartnerInvite.objects.create(partner=partner)
        resp = self.client.get(self._url("partner", invite.code))
        body_text = str(resp.data)
        self.assertNotIn("+15550000101", body_text)
        self.assertNotIn("resolver_owner@example.com", body_text)
