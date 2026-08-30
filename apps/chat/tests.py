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
    MessageThreadLink,
    ConversationRequestState,
    ConversationType,
)
from .services import get_or_create_direct_conversation
from apps.channels.models import Channel
from apps.partners.models import Partner, PartnerChannelPermissionOverwrite


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

        res = self.client.get("/api/v1/conversations/")

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

        res = self.client.get("/api/v1/conversations/search/", {"q": "Latest"})

        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(str(payload["results"][0]["id"]), str(self.conversation.id))

    def test_participant_search_returns_visible_members(self):
        self.client.force_authenticate(self.user)

        res = self.client.get("/api/v1/conversations/participant-search/", {"q": "Peer"})

        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertGreaterEqual(payload["count"], 1)
        self.assertTrue(
            any(
                row["conversation_id"] == str(self.conversation.id)
                and row["user"]["id"] == str(self.peer.id)
                for row in payload["results"]
            )
        )

    def test_direct_conversation_creation_is_canonical_and_restores_visibility(self):
        user_a = User.objects.create_user(
            phone="+2348000000003",
            password="password123",
            country="NG",
            display_name="Direct A",
        )
        user_b = User.objects.create_user(
            phone="+2348000000004",
            password="password123",
            country="NG",
            display_name="Direct B",
        )
        first, created_first = get_or_create_direct_conversation(
            user_a,
            user_b,
            initiator=user_a,
            use_request_flow=True,
        )
        ConversationMember.objects.filter(conversation=first, user=user_b).update(is_hidden=True)

        second, created_second = get_or_create_direct_conversation(
            user_b,
            user_a,
            initiator=user_b,
            use_request_flow=True,
        )

        self.assertIs(created_first, True)
        self.assertIs(created_second, False)
        self.assertEqual(first.id, second.id)
        self.assertTrue(first.direct_key)
        self.assertEqual(
            Conversation.objects.filter(type=ConversationType.DIRECT, direct_key=first.direct_key).count(),
            1,
        )
        self.assertFalse(
            ConversationMember.objects.get(conversation=first, user=user_b).is_hidden
        )

    def test_internal_last_message_update_restores_hidden_direct_chat(self):
        ConversationMember.objects.filter(conversation=self.conversation, user=self.peer).update(is_hidden=True)
        url = f"/api/v1/conversations/{self.conversation.id}/update-last-message/"

        with patch.dict(
            os.environ,
            {"DJANGO_INTERNAL_TOKEN": "test-internal-token", "INTERNAL_SIGNATURE_REQUIRED": "0"},
        ):
            res = self.client.patch(
                url,
                {
                    "last_message_at": "2026-05-14T10:00:00Z",
                    "last_message_preview": "New message",
                },
                format="json",
                HTTP_X_INTERNAL_AUTH="test-internal-token",
            )

        self.assertEqual(res.status_code, 200)
        self.assertFalse(
            ConversationMember.objects.get(conversation=self.conversation, user=self.peer).is_hidden
        )

    def test_generic_direct_create_uses_canonical_direct_identity(self):
        self.client.force_authenticate(self.user)
        url = "/api/v1/conversations/"

        first = self.client.post(
            url,
            {"type": "direct", "participants": [self.peer.phone]},
            format="json",
        )
        second = self.client.post(
            url,
            {"type": "direct", "participants": [self.peer.phone]},
            format="json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        first_id = first.json()["id"]
        second_id = second.json()["id"]
        self.assertEqual(first_id, second_id)
        conversation = Conversation.objects.get(id=first_id)
        self.assertEqual(conversation.type, ConversationType.DIRECT)
        self.assertTrue(conversation.direct_key)
        self.assertEqual(
            Conversation.objects.filter(type=ConversationType.DIRECT, direct_key=conversation.direct_key).count(),
            1,
        )

    def test_subroom_creation_is_idempotent_for_same_parent_message(self):
        self.client.force_authenticate(self.user)
        url = "/api/v1/chats/threads/"
        payload = {
            "parent_conversation": str(self.conversation.id),
            "parent_message_key": "mongo-message-1",
        }

        first = self.client.post(url, payload, format="json")
        second = self.client.post(url, payload, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["child_conversation_id"], second.json()["child_conversation_id"])
        self.assertEqual(
            MessageThreadLink.objects.filter(
                parent_conversation=self.conversation,
                parent_message_key="mongo-message-1",
            ).count(),
            1,
        )


class MentionEveryonePolicyCheckTests(APITestCase):
    """policy-check is the hook Nest calls (djangoConversationClient.policyCheck
    in messages.ts) before persisting every message — it already enforced DLP
    and legal-hold, but "@everyone"/"@here" mentions in a partner channel had
    no gate at all regardless of PartnerChannelPermissionOverwrite rows."""

    def setUp(self):
        self.owner = User.objects.create_user(phone="+2348000000101", country="NG", password="pass1234")
        self.member = User.objects.create_user(phone="+2348000000102", country="NG", password="pass1234")
        self.partner = Partner.objects.create(owner=self.owner, name="Mention Partner", slug="mention-partner")
        conversation = Conversation.objects.create(type=ConversationType.CHANNEL, created_by=self.owner)
        self.channel = Channel.objects.create(
            partner=self.partner,
            name="general",
            slug="general",
            owner=self.owner,
            conversation=conversation,
        )
        self.url = f"/api/v1/conversations/{conversation.id}/policy-check/"

    def _post(self, user, text):
        with patch.dict(
            os.environ,
            {"DJANGO_INTERNAL_TOKEN": "test-internal-token", "INTERNAL_SIGNATURE_REQUIRED": "0"},
        ):
            return self.client.post(
                self.url,
                {"action": "send", "userId": str(user.id), "text": text},
                format="json",
                HTTP_X_INTERNAL_AUTH="test-internal-token",
            )

    def test_plain_member_blocked_from_mentioning_everyone(self):
        response = self._post(self.member, "hey @everyone check this out")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["reason"], "mention_everyone_not_allowed")

    def test_owner_can_mention_everyone(self):
        response = self._post(self.owner, "hey @everyone check this out")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["allowed"])

    def test_member_with_channel_overwrite_can_mention_everyone(self):
        PartnerChannelPermissionOverwrite.objects.create(
            partner=self.partner,
            channel=self.channel,
            subject_type=PartnerChannelPermissionOverwrite.SubjectType.MEMBER,
            user=self.member,
            allow_permissions=[PartnerChannelPermissionOverwrite.PermissionCode.MENTION_EVERYONE],
        )
        response = self._post(self.member, "hey @everyone check this out")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["allowed"])

    def test_message_without_mention_is_unaffected(self):
        response = self._post(self.member, "hey team check this out")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["allowed"])
