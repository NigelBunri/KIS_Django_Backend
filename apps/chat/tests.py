import os
from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User

from .models import (
    BaseConversationRole,
    Conversation,
    ConversationMember,
    ConversationRequestState,
    ConversationType,
)


class ConversationUnreadContractTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+2348000000001",
            password="password123",
            country="NG",
            display_name="Owner",
        )
        self.peer = User.objects.create_user(
            phone="+2348000000002",
            password="password123",
            country="NG",
            display_name="Peer",
        )
        self.conversation = Conversation.objects.create(
            type=ConversationType.DIRECT,
            created_by=self.user,
            last_message_seq=10,
            last_message_preview="Latest",
        )
        ConversationMember.objects.create(
            conversation=self.conversation,
            user=self.user,
            base_role=BaseConversationRole.OWNER,
            last_read_seq=3,
        )
        ConversationMember.objects.create(
            conversation=self.conversation,
            user=self.peer,
            base_role=BaseConversationRole.MEMBER,
        )

    def test_list_exposes_authoritative_unread_fields(self):
        self.client.force_authenticate(self.user)

        res = self.client.get(reverse("conversation-list"))

        self.assertEqual(res.status_code, 200)
        payload = res.json()
        row = payload["results"][0] if isinstance(payload, dict) and "results" in payload else payload[0]
        self.assertEqual(str(row["id"]), str(self.conversation.id))
        self.assertEqual(row["unread_count"], 7)
        self.assertEqual(row["last_read_seq"], 3)
        self.assertIs(row["read_state_authoritative"], True)
        self.assertIs(row["has_mention"], False)

    def test_internal_update_read_state_advances_monotonically(self):
        url = reverse("conversation-update-read-state", kwargs={"pk": self.conversation.id})

        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "test-internal-token"}):
            res = self.client.patch(
                url,
                {
                    "user_id": self.user.id,
                    "last_read_seq": 8,
                    "last_read_at": "2026-04-24T05:30:00Z",
                },
                format="json",
                HTTP_X_INTERNAL_AUTH="test-internal-token",
            )
            self.assertEqual(res.status_code, 200)
            self.conversation.refresh_from_db()
            member = ConversationMember.objects.get(conversation=self.conversation, user=self.user)
            self.assertEqual(member.last_read_seq, 8)

            res = self.client.patch(
                url,
                {
                    "user_id": self.user.id,
                    "last_read_seq": 4,
                },
                format="json",
                HTTP_X_INTERNAL_AUTH="test-internal-token",
            )
            self.assertEqual(res.status_code, 200)
            member.refresh_from_db()
            self.assertEqual(member.last_read_seq, 8)

    def test_pending_direct_recipient_cannot_send_via_ws_perms(self):
        self.conversation.request_state = ConversationRequestState.PENDING
        self.conversation.request_initiator = self.user
        self.conversation.request_recipient = self.peer
        self.conversation.save(
            update_fields=["request_state", "request_initiator", "request_recipient"]
        )
        url = reverse("conversation-ws-perms", kwargs={"pk": self.conversation.id})

        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "test-internal-token"}):
            res = self.client.get(
                url,
                {"userId": str(self.peer.id)},
                HTTP_X_INTERNAL_AUTH="test-internal-token",
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["isMember"], True)
        self.assertEqual(res.json()["canSend"], False)

    def test_search_returns_matching_conversation(self):
        self.client.force_authenticate(self.user)

        res = self.client.get(reverse("conversation-search"), {"q": "Latest"})

        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(str(payload["results"][0]["id"]), str(self.conversation.id))

    def test_participant_search_returns_visible_members(self):
        self.client.force_authenticate(self.user)

        res = self.client.get(reverse("conversation-participant-search"), {"q": "Peer"})

        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["conversation_id"], str(self.conversation.id))
        self.assertEqual(payload["results"][0]["user"]["id"], str(self.peer.id))
