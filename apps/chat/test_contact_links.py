from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.chat.models import ContactShareLink, Conversation, ConversationRequestState
from apps.moderation.models import UserBlock

User = get_user_model()


def _make_user(phone, username):
    return User.objects.create_user(phone=phone, country="NG", password="pass1234", username=username)


class ContactShareLinkMeViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+15550002101", "contactlink_owner")
        self.client.force_authenticate(self.owner)

    def test_get_creates_a_link_on_first_call(self):
        self.assertFalse(ContactShareLink.objects.filter(owner=self.owner).exists())
        resp = self.client.get(reverse("chat:contact-link-me"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["token"])
        self.assertTrue(resp.data["is_active"])
        self.assertIn(resp.data["token"], resp.data["invite_link"])

    def test_get_is_idempotent(self):
        first = self.client.get(reverse("chat:contact-link-me")).data["token"]
        second = self.client.get(reverse("chat:contact-link-me")).data["token"]
        self.assertEqual(first, second)

    def test_post_regenerates_and_invalidates_old_token(self):
        old_token = self.client.get(reverse("chat:contact-link-me")).data["token"]
        new_token = self.client.post(reverse("chat:contact-link-me")).data["token"]
        self.assertNotEqual(old_token, new_token)
        # The old token must no longer resolve at all.
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": old_token})
        self.assertEqual(resp.status_code, 404)

    def test_delete_deactivates_without_new_token(self):
        token = self.client.get(reverse("chat:contact-link-me")).data["token"]
        resp = self.client.delete(reverse("chat:contact-link-me"))
        self.assertEqual(resp.status_code, 204)
        link = ContactShareLink.objects.get(owner=self.owner)
        self.assertEqual(link.token, token)
        self.assertFalse(link.is_active)

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        resp = self.client.get(reverse("chat:contact-link-me"))
        self.assertEqual(resp.status_code, 401)


class RedeemContactLinkViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+15550002102", "contactlink_owner2")
        self.visitor = _make_user("+15550002103", "contactlink_visitor")
        self.link = ContactShareLink.objects.create(owner=self.owner, token="redeemtoken123")

    def test_redeeming_creates_a_pending_dm_request_not_a_normal_conversation(self):
        self.client.force_authenticate(self.visitor)
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["request_state"], ConversationRequestState.PENDING)
        conv = Conversation.objects.get(id=resp.data["conversation_id"])
        self.assertEqual(conv.request_state, ConversationRequestState.PENDING)
        self.assertEqual(conv.request_initiator_id, self.visitor.id)
        self.assertEqual(conv.request_recipient_id, self.owner.id)
        self.link.refresh_from_db()
        self.assertEqual(self.link.use_count, 1)

    def test_redeeming_never_exposes_owner_phone_in_the_response(self):
        self.client.force_authenticate(self.visitor)
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertNotIn("+15550002102", str(resp.data))

    def test_reusable_by_a_second_visitor(self):
        other_visitor = _make_user("+15550002104", "contactlink_visitor2")
        self.client.force_authenticate(self.visitor)
        self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.client.force_authenticate(other_visitor)
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertEqual(resp.status_code, 201)
        self.link.refresh_from_db()
        self.assertEqual(self.link.use_count, 2)

    def test_inactive_link_is_410(self):
        self.link.is_active = False
        self.link.save(update_fields=["is_active"])
        self.client.force_authenticate(self.visitor)
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertEqual(resp.status_code, 410)

    def test_expired_link_is_410(self):
        self.link.expires_at = timezone.now() - timedelta(hours=1)
        self.link.save(update_fields=["expires_at"])
        self.client.force_authenticate(self.visitor)
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertEqual(resp.status_code, 410)

    def test_nonexistent_token_is_404(self):
        self.client.force_authenticate(self.visitor)
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "not-a-real-token"})
        self.assertEqual(resp.status_code, 404)

    def test_cannot_redeem_your_own_link(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertEqual(resp.status_code, 400)

    def test_blocked_user_cannot_redeem(self):
        UserBlock.objects.create(blocker=self.owner, blocked=self.visitor)
        self.client.force_authenticate(self.visitor)
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertEqual(resp.status_code, 403)

    def test_blocked_in_reverse_direction_also_cannot_redeem(self):
        UserBlock.objects.create(blocker=self.visitor, blocked=self.owner)
        self.client.force_authenticate(self.visitor)
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertEqual(resp.status_code, 403)

    def test_requires_authentication(self):
        resp = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertEqual(resp.status_code, 401)

    def test_second_redeem_by_same_visitor_reuses_existing_conversation(self):
        self.client.force_authenticate(self.visitor)
        first = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        second = self.client.post(reverse("chat:contact-link-redeem"), {"token": "redeemtoken123"})
        self.assertEqual(first.data["conversation_id"], second.data["conversation_id"])
        self.assertEqual(second.status_code, 200)
