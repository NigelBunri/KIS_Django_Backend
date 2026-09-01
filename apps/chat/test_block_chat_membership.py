"""
ConversationViewSet.block_chat previously fetched the conversation via a
bare Conversation.objects.get(pk=pk) instead of the membership-scoped
self.get_object(), so any authenticated user could lock ANY other pair's
DM by guessing/reusing a conversation id, regardless of membership. See
the SECURITY comment on block_chat in views.py.

Run:
  python3 manage.py test apps.chat.test_block_chat_membership --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from django.test import TestCase

from .models import BaseConversationRole, Conversation, ConversationMember, ConversationType

User = get_user_model()


class BlockChatMembershipTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.member_a = User.objects.create_user(phone="+2348200000001", password="pw123456", country="NG")
        self.member_b = User.objects.create_user(phone="+2348200000002", password="pw123456", country="NG")
        self.stranger = User.objects.create_user(phone="+2348200000003", password="pw123456", country="NG")
        self.conversation = Conversation.objects.create(type=ConversationType.DIRECT, created_by=self.member_a)
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.member_a, base_role=BaseConversationRole.OWNER,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.member_b, base_role=BaseConversationRole.MEMBER,
        )

    def _block_url(self):
        return f"/api/v1/conversations/{self.conversation.id}/block_chat/"

    def test_a_stranger_cannot_lock_a_dm_they_are_not_part_of(self):
        self.client.force_authenticate(self.stranger)

        res = self.client.post(self._block_url())

        self.assertIn(res.status_code, (403, 404))
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.is_locked)

    def test_a_real_member_can_lock_their_own_dm(self):
        self.client.force_authenticate(self.member_a)

        res = self.client.post(self._block_url())

        self.assertEqual(res.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.is_locked)
        self.assertEqual(self.conversation.locked_by_id, self.member_a.id)
