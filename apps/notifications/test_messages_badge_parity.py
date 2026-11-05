"""
Regression test for the bug where the Messages tab-bar badge could show a
non-zero unread count forever even after every conversation visible in the
Messages tab had been read. Root cause: apps.notifications.badge_counts'
_conversation_unread_counts() kept its own, independently-maintained and
incomplete copy of which conversations ConversationViewSet.get_queryset()
hides from the Messages tab list - it only knew about Partner.
main_conversation and a partner-owned Channel's conversation, so an unread
message in a partner-owned Group/Community conversation (or a course
classroom chat, or a Prayer Watch room) leaked into the badge's
message_unread total while never appearing anywhere in the Messages list
for the user to open and clear it.

The fix makes both sides import the exact same Q-object logic from
apps.chat.services (hidden_from_messages_tab_q / partner_conversation_q /
non_partner_hidden_conversation_q), so this can't drift apart again. This
test asserts that parity directly: for each conversation category, an
unread message there either shows up in the Messages list AND counts
toward the Messages badge, or appears in neither.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.groups.services import create_group_with_conversation
from apps.notifications.badge_counts import get_main_tab_badge_counts
from apps.partners.models import Partner

User = get_user_model()


def _bump_unread(conversation: Conversation, member: ConversationMember, by: int = 3) -> None:
    conversation.last_message_seq = (conversation.last_message_seq or 0) + by
    conversation.save(update_fields=["last_message_seq"])
    member.last_read_seq = 0
    member.save(update_fields=["last_read_seq"])


class MessagesBadgeParityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create(phone="+1000000101", display_name="Badge Parity User")

    def _list_conversation_ids(self):
        self.client.force_authenticate(self.user)
        res = self.client.get("/api/v1/conversations/")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        rows = payload.get("results", payload) if isinstance(payload, dict) else payload
        return {str(row["id"]) for row in rows}

    def test_ordinary_group_counts_toward_messages_badge_and_list(self):
        group = create_group_with_conversation(owner=self.user, name="Ordinary Group", slug="ordinary-group")
        member = ConversationMember.objects.get(conversation=group.conversation, user=self.user)
        _bump_unread(group.conversation, member)

        self.assertIn(str(group.conversation_id), self._list_conversation_ids())
        counts = get_main_tab_badge_counts(self.user)
        self.assertEqual(counts.messages, 3)
        self.assertEqual(counts.partners, 0)

    def test_partner_owned_group_counts_toward_partners_not_messages(self):
        partner = Partner.objects.create(owner=self.user, name="Badge Partner", slug="badge-partner")
        group = create_group_with_conversation(
            owner=self.user, name="Partner Group", slug="partner-group", partner=partner,
        )
        member = ConversationMember.objects.get(conversation=group.conversation, user=self.user)
        _bump_unread(group.conversation, member)

        # Not shown in the Messages tab list...
        self.assertNotIn(str(group.conversation_id), self._list_conversation_ids())
        # ...and therefore must not count toward the Messages badge either -
        # this is exactly the bug: before the fix, messages == 3 here too.
        counts = get_main_tab_badge_counts(self.user)
        self.assertEqual(counts.messages, 0)
        self.assertEqual(counts.partners, 3)

    def test_partner_main_conversation_counts_toward_partners_not_messages(self):
        main_conversation = Conversation.objects.create(
            type=ConversationType.GROUP, title="Partner Main", created_by=self.user,
        )
        ConversationMember.objects.create(
            conversation=main_conversation, user=self.user, base_role=BaseConversationRole.OWNER,
        )
        Partner.objects.create(
            owner=self.user, name="Main Conv Partner", slug="main-conv-partner", main_conversation=main_conversation,
        )
        member = ConversationMember.objects.get(conversation=main_conversation, user=self.user)
        _bump_unread(main_conversation, member)

        self.assertNotIn(str(main_conversation.id), self._list_conversation_ids())
        counts = get_main_tab_badge_counts(self.user)
        self.assertEqual(counts.messages, 0)
        self.assertEqual(counts.partners, 3)

    def test_course_classroom_chat_counts_toward_neither_badge(self):
        group = create_group_with_conversation(
            owner=self.user, name="Course Classroom", slug="edu-course-abc123",
        )
        member = ConversationMember.objects.get(conversation=group.conversation, user=self.user)
        _bump_unread(group.conversation, member)

        self.assertNotIn(str(group.conversation_id), self._list_conversation_ids())
        counts = get_main_tab_badge_counts(self.user)
        self.assertEqual(counts.messages, 0)
        self.assertEqual(counts.partners, 0)

    def test_prayer_watch_room_counts_toward_neither_badge(self):
        group = create_group_with_conversation(
            owner=self.user, name="Prayer Watch 07", slug="prayer-watch-07",
        )
        member = ConversationMember.objects.get(conversation=group.conversation, user=self.user)
        _bump_unread(group.conversation, member)

        self.assertNotIn(str(group.conversation_id), self._list_conversation_ids())
        counts = get_main_tab_badge_counts(self.user)
        self.assertEqual(counts.messages, 0)
        self.assertEqual(counts.partners, 0)

    def test_badge_returns_to_zero_once_every_visible_conversation_is_read(self):
        """The exact scenario reported: a mix of visible and hidden
        conversations, all with unread messages. Once every conversation
        that actually shows up in the Messages tab is marked read, the
        Messages badge must drop to zero - even though a hidden
        conversation (partner-owned, in this case) still has unread
        messages of its own that the user has no way to see or act on
        from the Messages tab."""
        visible = create_group_with_conversation(owner=self.user, name="Visible", slug="visible-group")
        visible_member = ConversationMember.objects.get(conversation=visible.conversation, user=self.user)
        _bump_unread(visible.conversation, visible_member)

        partner = Partner.objects.create(owner=self.user, name="Hidden Partner", slug="hidden-partner")
        hidden = create_group_with_conversation(
            owner=self.user, name="Hidden", slug="hidden-group", partner=partner,
        )
        hidden_member = ConversationMember.objects.get(conversation=hidden.conversation, user=self.user)
        _bump_unread(hidden.conversation, hidden_member)

        counts_before = get_main_tab_badge_counts(self.user)
        self.assertEqual(counts_before.messages, 3)

        # Mark only the visible one read, exactly as opening it in the
        # Messages tab would.
        visible_member.last_read_seq = visible.conversation.last_message_seq
        visible_member.save(update_fields=["last_read_seq"])

        counts_after = get_main_tab_badge_counts(self.user)
        self.assertEqual(counts_after.messages, 0)
