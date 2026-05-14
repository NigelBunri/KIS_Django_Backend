import os
from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User

from .internal_signing import sign_internal_request
from .models import (
    BaseConversationRole,
    Conversation,
    ConversationMember,
    ConversationRequestState,
    ConversationType,
)


def _signed_internal_headers(method: str, path: str, body=None, secret: str = "test-internal-token"):
    headers = sign_internal_request(method, path, body, secret=secret)
    return {f"HTTP_{key.upper().replace('-', '_')}": value for key, value in headers.items()}


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
        url = f"/api/v1/conversations/{self.conversation.id}/update-read-state/"

        with patch.dict(
            os.environ,
            {"DJANGO_INTERNAL_TOKEN": "test-internal-token", "INTERNAL_SIGNATURE_REQUIRED": "0"},
        ), patch("apps.chat.views.notify_main_tab_badges_updated") as notify:
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
            notify.assert_called_once()
            self.assertEqual(notify.call_args.kwargs["source"], "messages")
            self.assertEqual(notify.call_args.kwargs["reason"], "read_state_updated")

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

    def test_strict_internal_auth_accepts_signed_request_and_rejects_replay(self):
        url = f"/api/v1/conversations/{self.conversation.id}/update-read-state/"
        payload = {
            "user_id": str(self.user.id),
            "last_read_seq": 9,
        }
        headers = _signed_internal_headers("PATCH", url, payload)

        with patch.dict(
            os.environ,
            {
                "DJANGO_INTERNAL_TOKEN": "test-internal-token",
                "INTERNAL_SIGNATURE_REQUIRED": "1",
            },
        ):
            res = self.client.patch(url, payload, format="json", **headers)
            self.assertEqual(res.status_code, 200)

            replay = self.client.patch(url, payload, format="json", **headers)
            self.assertEqual(replay.status_code, 403)

    def test_strict_internal_auth_rejects_legacy_token_only_request(self):
        url = f"/api/v1/conversations/{self.conversation.id}/update-read-state/"

        with patch.dict(
            os.environ,
            {
                "DJANGO_INTERNAL_TOKEN": "test-internal-token",
                "INTERNAL_SIGNATURE_REQUIRED": "1",
            },
        ):
            res = self.client.patch(
                url,
                {
                    "user_id": self.user.id,
                    "last_read_seq": 8,
                },
                format="json",
                HTTP_X_INTERNAL_AUTH="test-internal-token",
            )

        self.assertEqual(res.status_code, 403)

    def test_pending_direct_recipient_can_reply_via_ws_perms(self):
        self.conversation.request_state = ConversationRequestState.PENDING
        self.conversation.request_initiator = self.user
        self.conversation.request_recipient = self.peer
        self.conversation.save(
            update_fields=["request_state", "request_initiator", "request_recipient"]
        )
        url = f"/api/v1/conversations/{self.conversation.id}/ws-perms/"

        with patch.dict(
            os.environ,
            {"DJANGO_INTERNAL_TOKEN": "test-internal-token", "INTERNAL_SIGNATURE_REQUIRED": "0"},
        ):
            res = self.client.get(
                url,
                {"userId": str(self.peer.id)},
                HTTP_X_INTERNAL_AUTH="test-internal-token",
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["isMember"], True)
        self.assertEqual(res.json()["canSend"], True)
        self.assertIn("chat:direct_pending_reply", res.json()["scopes"])

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
