"""
Regression coverage for ChatInfoPage.tsx's "Group settings" toggles
actually persisting+being enforced server-side via ws_perms (the single
endpoint NestJS's assertMember()/wsPerms() calls before every send/react):

- "Only admins can send" (ConversationSettings.send_policy) was already
  enforced here, but the mobile toggle sent a key ("messaging_restricted")
  that doesn't exist on the model, so it silently never actually changed
  send_policy at all.
- "Only admins can react" had no server field or enforcement whatsoever -
  it only ever lived in AsyncStorage + a malformed socket.emit payload.
  reactions_admin_only + canReact here is the real, persisted/enforced
  version.

Run:
  python3 manage.py test apps.chat.test_ws_perms_policies --keepdb -v 2
"""
import contextlib
import os

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    BaseConversationRole,
    Conversation,
    ConversationMember,
    ConversationSendPolicy,
    ConversationSettings,
    ConversationType,
)

User = get_user_model()

SECRET = "ws-perms-policies-test-token"
UNSIGNED_ENV = {"DJANGO_INTERNAL_TOKEN": SECRET, "INTERNAL_SIGNATURE_REQUIRED": "0"}


class WsPermsPolicyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+2348400000001", password="pw123456", country="NG")
        self.member = User.objects.create_user(phone="+2348400000002", password="pw123456", country="NG")
        self.conversation = Conversation.objects.create(type=ConversationType.GROUP, created_by=self.owner)
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.owner, base_role=BaseConversationRole.OWNER,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.member, base_role=BaseConversationRole.MEMBER,
        )

    def _ws_perms(self, user_id):
        url = f"/api/v1/chat/conversations/{self.conversation.id}/ws-perms/?userId={user_id}"
        with patch_env():
            return self.client.get(
                url,
                HTTP_X_INTERNAL_AUTH=SECRET,
            )

    def test_member_can_react_by_default(self):
        response = self._ws_perms(self.member.id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["canReact"])

    def test_member_cannot_react_when_reactions_admin_only_is_set(self):
        ConversationSettings.objects.create(conversation=self.conversation, reactions_admin_only=True)

        member_response = self._ws_perms(self.member.id)
        owner_response = self._ws_perms(self.owner.id)

        self.assertFalse(member_response.data["canReact"])
        self.assertTrue(owner_response.data["canReact"])

    def test_send_policy_admins_only_blocks_a_plain_member_from_sending(self):
        ConversationSettings.objects.create(
            conversation=self.conversation, send_policy=ConversationSendPolicy.ADMINS_ONLY,
        )

        member_response = self._ws_perms(self.member.id)
        owner_response = self._ws_perms(self.owner.id)

        self.assertFalse(member_response.data["canSend"])
        self.assertTrue(owner_response.data["canSend"])


@contextlib.contextmanager
def patch_env():
    old = {k: os.environ.get(k) for k in UNSIGNED_ENV}
    os.environ.update(UNSIGNED_ENV)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
