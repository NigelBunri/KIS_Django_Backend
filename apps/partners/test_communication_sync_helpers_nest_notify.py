from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.chat.models import BaseConversationRole, ConversationMember
from apps.groups.services import create_group_with_conversation
from apps.partners.communication_sync_helpers import leave_conversation, leave_group

User = get_user_model()


class LeaveConversationNestNotifyTests(TestCase):
    """Defect D5 (KIS_E2EE_KEY_LIFECYCLE_AUDIT.md): the generic
    join/leave helpers shared by every Partner-Account-gated domain
    (education, health, ...) bulk-UPDATE ConversationMember directly, so
    they bypass any Django signal a naive fix might have relied on.
    leave_conversation (and leave_group, which calls it) must push a
    Nest cache-invalidation push after every real removal - and skip it
    entirely when there was nothing to remove."""

    def setUp(self):
        self.owner = User.objects.create_user(
            phone="+237670009101", password="TestPass12!", country="CM",
        )
        self.member = User.objects.create_user(
            phone="+237670009102", password="TestPass12!", country="CM",
        )
        self.group = create_group_with_conversation(
            owner=self.owner, name="Nest notify group", slug="nest-notify-group",
        )
        ConversationMember.objects.create(
            conversation=self.group.conversation, user=self.member, base_role=BaseConversationRole.MEMBER,
        )

    def test_leave_conversation_notifies_nest_when_a_member_actually_left(self):
        with patch(
            "apps.partners.communication_sync_helpers.notify_nest_conversation_members_removed"
        ) as notify:
            leave_conversation(self.group.conversation, self.member)

        notify.assert_called_once_with(str(self.group.conversation.id), [str(self.member.id)])

    def test_leave_conversation_is_a_no_op_notify_when_the_user_was_never_an_active_member(self):
        stranger = User.objects.create_user(
            phone="+237670009103", password="TestPass12!", country="CM",
        )
        with patch(
            "apps.partners.communication_sync_helpers.notify_nest_conversation_members_removed"
        ) as notify:
            leave_conversation(self.group.conversation, stranger)

        notify.assert_not_called()

    def test_leave_group_notifies_nest_via_the_underlying_leave_conversation_call(self):
        with patch(
            "apps.partners.communication_sync_helpers.notify_nest_conversation_members_removed"
        ) as notify:
            leave_group(self.group, self.member)

        notify.assert_called_once_with(str(self.group.conversation.id), [str(self.member.id)])
