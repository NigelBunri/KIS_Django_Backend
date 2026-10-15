"""
AND-060 (live-tested regression, found 2026-10-15): ws_perms() used to
compute isBlocked from apps.moderation.UserBlock across EITHER direction,
and NestJS's assertMember() hard-rejects the socket.emit entirely whenever
isBlocked=True - called before every real-time action (send/edit/delete/
typing/receipts/reactions/calls). That made a block symmetric and
send-rejecting for BOTH participants, confirmed live via a real device
test: two unrelated accounts with a historical UserBlock row between them
(in either direction) could no longer exchange messages at all, for
either party. Same bug in direct() (DM creation), which outright rejected
starting/resuming a conversation between a blocked pair, in either
direction.

Required behavior (this app's actual block design): blocking is private
to the blocker's own client - the blocked person keeps sending normally
(so they're never tipped off), and the blocker simply never sees/receives
those messages. That's implemented as a receive-side filter in the mobile
app's useChatMessaging.ts (see ChatRoomHandlers.blockMutePin.test.ts),
NOT as a server-side membership/permission gate - a permission check has
no way to express "let A send, but don't let B receive," only "let both
act" or "let neither," which is why this was the wrong layer for it.

This file now locks in the CORRECTED behavior: a UserBlock relationship,
in either direction, must never affect isBlocked, canSend, or DM creation.

Run:
  python3 manage.py test apps.chat.test_userblock_enforcement --keepdb -v 2
"""
import os
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.moderation.models import UserBlock

from .models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from .services import get_or_create_direct_conversation


def _internal_headers():
    return {"HTTP_X_INTERNAL_AUTH": "test-internal-token"}


class WsPermsUserBlockTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+2348100000001", password="pw123456", country="NG")
        self.peer = User.objects.create_user(phone="+2348100000002", password="pw123456", country="NG")
        self.conversation = Conversation.objects.create(type=ConversationType.DIRECT, created_by=self.user)
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.user, base_role=BaseConversationRole.OWNER,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.peer, base_role=BaseConversationRole.MEMBER,
        )
        self.url = f"/api/v1/conversations/{self.conversation.id}/ws-perms/"
        self._env_patch = patch.dict(
            os.environ, {"DJANGO_INTERNAL_TOKEN": "test-internal-token", "INTERNAL_SIGNATURE_REQUIRED": "0"},
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_no_block_allows_the_conversation(self):
        res = self.client.get(self.url, {"userId": str(self.user.id)}, **_internal_headers())

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["isMember"])
        self.assertFalse(res.json()["isBlocked"])
        self.assertTrue(res.json()["canSend"])

    def test_the_blocker_can_still_send_in_a_direct_conversation_with_the_person_they_blocked(self):
        # Blocking must be private to the blocker's own client - the real
        # effect (never seeing the blocked person's messages) is a
        # receive-side filter elsewhere, not a send-side rejection here.
        UserBlock.objects.create(blocker=self.user, blocked=self.peer)

        res = self.client.get(self.url, {"userId": str(self.user.id)}, **_internal_headers())

        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["isBlocked"])
        self.assertTrue(res.json()["canSend"])

    def test_the_blocked_party_can_still_send_too_so_they_are_never_tipped_off(self):
        UserBlock.objects.create(blocker=self.user, blocked=self.peer)

        res = self.client.get(self.url, {"userId": str(self.peer.id)}, **_internal_headers())

        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["isBlocked"])
        self.assertTrue(res.json()["canSend"])

    def test_a_block_between_two_other_users_does_not_affect_this_conversation(self):
        stranger = User.objects.create_user(phone="+2348100000003", password="pw123456", country="NG")
        UserBlock.objects.create(blocker=self.user, blocked=stranger)

        res = self.client.get(self.url, {"userId": str(self.user.id)}, **_internal_headers())

        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["isBlocked"])

    def test_a_block_does_not_cascade_into_a_shared_group_conversation(self):
        UserBlock.objects.create(blocker=self.user, blocked=self.peer)
        group_conversation = Conversation.objects.create(type=ConversationType.GROUP, created_by=self.user)
        ConversationMember.objects.create(
            conversation=group_conversation, user=self.user, base_role=BaseConversationRole.OWNER,
        )
        ConversationMember.objects.create(
            conversation=group_conversation, user=self.peer, base_role=BaseConversationRole.MEMBER,
        )
        url = f"/api/v1/conversations/{group_conversation.id}/ws-perms/"

        res = self.client.get(url, {"userId": str(self.user.id)}, **_internal_headers())

        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["isBlocked"])


class DirectConversationCreationBlockTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+2348100000011", password="pw123456", country="NG")
        self.peer = User.objects.create_user(phone="+2348100000012", password="pw123456", country="NG")
        self.client.force_authenticate(self.user)

    def test_can_still_start_a_dm_with_someone_you_blocked(self):
        UserBlock.objects.create(blocker=self.user, blocked=self.peer)

        res = self.client.post(
            "/api/v1/conversations/direct/", {"peer_user_id": str(self.peer.id)}, format="json",
        )

        self.assertIn(res.status_code, (200, 201))

    def test_can_still_start_a_dm_with_someone_who_blocked_you(self):
        UserBlock.objects.create(blocker=self.peer, blocked=self.user)

        res = self.client.post(
            "/api/v1/conversations/direct/", {"peer_user_id": str(self.peer.id)}, format="json",
        )

        self.assertIn(res.status_code, (200, 201))

    def test_dm_creation_still_works_without_a_block(self):
        res = self.client.post(
            "/api/v1/conversations/direct/", {"peer_user_id": str(self.peer.id)}, format="json",
        )

        self.assertEqual(res.status_code, 201)

    def test_existing_conversation_lookup_is_unaffected_by_an_unrelated_block(self):
        stranger = User.objects.create_user(phone="+2348100000013", password="pw123456", country="NG")
        UserBlock.objects.create(blocker=self.user, blocked=stranger)
        get_or_create_direct_conversation(self.user, self.peer)

        res = self.client.post(
            "/api/v1/conversations/direct/", {"peer_user_id": str(self.peer.id)}, format="json",
        )

        self.assertEqual(res.status_code, 200)
