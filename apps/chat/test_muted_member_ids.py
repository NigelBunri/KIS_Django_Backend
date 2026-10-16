"""
Regression coverage for the Nest push pipeline's per-conversation mute
check: notifyNewMessage previously only consulted a recipient's GLOBAL
"message notifications" preference and DND hours, never whether they'd
muted this SPECIFIC conversation (ConversationMember.is_muted, set by the
mute/ action below via the mobile app's handleMuteConversation). This
internal-only endpoint is what Nest's DjangoConversationClient.
getMutedMemberIds() calls so runPostSendSideEffects can skip the push for
anyone who muted the chat.

Run:
  python3 manage.py test apps.chat.test_muted_member_ids --keepdb -v 2
"""
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import BaseConversationRole, Conversation, ConversationMember, ConversationType

User = get_user_model()

SECRET = "muted-member-ids-test-token"
UNSIGNED_ENV = {"DJANGO_INTERNAL_TOKEN": SECRET, "INTERNAL_SIGNATURE_REQUIRED": "0"}


class MutedMemberIdsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(phone="+2348300000001", password="pw123456", country="NG")
        self.bob = User.objects.create_user(phone="+2348300000002", password="pw123456", country="NG")
        self.carol = User.objects.create_user(phone="+2348300000003", password="pw123456", country="NG")
        self.conversation = Conversation.objects.create(type=ConversationType.DIRECT, created_by=self.alice)
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice, base_role=BaseConversationRole.OWNER,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.bob, base_role=BaseConversationRole.MEMBER,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.carol, base_role=BaseConversationRole.MEMBER,
        )

    def _url(self):
        return f"/api/v1/chat/conversations/{self.conversation.id}/muted-member-ids/"

    def _headers(self):
        return {"HTTP_X_INTERNAL_AUTH": SECRET}

    def test_returns_only_members_who_muted_this_conversation(self):
        ConversationMember.objects.filter(conversation=self.conversation, user=self.bob).update(is_muted=True)

        with patch.dict(os.environ, UNSIGNED_ENV):
            res = self.client.get(self._url(), **self._headers())

        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.data["user_ids"]), {str(self.bob.id)})

    def test_returns_empty_list_when_nobody_has_muted(self):
        with patch.dict(os.environ, UNSIGNED_ENV):
            res = self.client.get(self._url(), **self._headers())

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["user_ids"], [])

    def test_a_member_who_left_is_excluded_even_if_still_marked_muted(self):
        ConversationMember.objects.filter(conversation=self.conversation, user=self.carol).update(
            is_muted=True, left_at=self.conversation.created_at,
        )

        with patch.dict(os.environ, UNSIGNED_ENV):
            res = self.client.get(self._url(), **self._headers())

        self.assertEqual(res.status_code, 200)
        self.assertNotIn(str(self.carol.id), res.data["user_ids"])

    def test_unmuting_removes_the_member_from_the_list(self):
        member = ConversationMember.objects.get(conversation=self.conversation, user=self.bob)
        member.is_muted = True
        member.save(update_fields=["is_muted"])

        with patch.dict(os.environ, UNSIGNED_ENV):
            res = self.client.get(self._url(), **self._headers())
        self.assertEqual(set(res.data["user_ids"]), {str(self.bob.id)})

        member.is_muted = False
        member.save(update_fields=["is_muted"])

        with patch.dict(os.environ, UNSIGNED_ENV):
            res = self.client.get(self._url(), **self._headers())
        self.assertEqual(res.data["user_ids"], [])

    def test_rejects_a_request_without_valid_internal_auth(self):
        res = self.client.get(self._url())
        self.assertEqual(res.status_code, 403)

    def test_unknown_conversation_returns_404_with_empty_list(self):
        with patch.dict(os.environ, UNSIGNED_ENV):
            res = self.client.get(
                "/api/v1/chat/conversations/00000000-0000-0000-0000-000000000000/muted-member-ids/",
                **self._headers(),
            )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.data["user_ids"], [])


class MuteActionEndToEndTests(TestCase):
    """The mobile app's own entry point (ChatRoomHandlers.tsx's
    handleMuteConversation) - confirms toggling it is what actually makes a
    member show up in / drop out of muted-member-ids above, end to end."""

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(phone="+2348300000011", password="pw123456", country="NG")
        self.bob = User.objects.create_user(phone="+2348300000012", password="pw123456", country="NG")
        self.conversation = Conversation.objects.create(type=ConversationType.DIRECT, created_by=self.alice)
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.alice, base_role=BaseConversationRole.OWNER,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.bob, base_role=BaseConversationRole.MEMBER,
        )

    def test_muting_then_fetching_muted_member_ids_reflects_the_toggle(self):
        self.client.force_authenticate(self.bob)
        mute_res = self.client.post(
            f"/api/v1/chat/conversations/{self.conversation.id}/mute/", {"muted": True}, format="json",
        )
        self.assertEqual(mute_res.status_code, 200)
        self.assertTrue(mute_res.data["is_muted"])

        self.client.force_authenticate(user=None)
        with patch.dict(os.environ, UNSIGNED_ENV):
            res = self.client.get(
                f"/api/v1/chat/conversations/{self.conversation.id}/muted-member-ids/",
                HTTP_X_INTERNAL_AUTH=SECRET,
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["user_ids"], [str(self.bob.id)])
