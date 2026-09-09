from __future__ import annotations

import secrets

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.chat.models import Conversation, ConversationType
from apps.communities.models import Community
from apps.groups.models import Group
from apps.partners.models import Partner, PartnerInvite

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

    # --- no PII leakage --------------------------------------------------

    def test_partner_response_never_includes_owner_contact_info(self):
        partner = Partner.objects.create(name="Privacy Org", owner=self.owner, slug="privacy-org-resolver")
        invite = PartnerInvite.objects.create(partner=partner)
        resp = self.client.get(self._url("partner", invite.code))
        body_text = str(resp.data)
        self.assertNotIn("+15550000101", body_text)
        self.assertNotIn("resolver_owner@example.com", body_text)
