"""
ChatMessageReportView + the staff-queue/action wiring for
apps.moderation.models.ChatMessageReport.

Chat message reports previously only ever existed in NestJS's local Mongo
MessageReport collection (ModerationController.report(), see
/Users/nigel/dev/backend/Nestjs/src/chat/features/moderation/) - nothing
mirrored them into Django, so a GO/staff moderator reviewing the unified
moderation queue (StaffModerationOperationsQueueView) had no way to ever
see a chat message had been reported. This is the Django side of closing
that gap; see also apps/moderation/tests.py for the general staff-queue
pattern this follows.

Run:
  python3 manage.py test apps.chat.test_chat_message_report --keepdb -v 2
"""
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.moderation.models import ChatMessageReport

REPORT_URL = "/api/v1/chat/internal/message-reports/"
QUEUE_URL = "/api/v1/moderation/staff/operations-queue/"
ACTION_URL = "/api/v1/moderation/staff/operation-action/"


class ChatMessageReportViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.reporter = User.objects.create_user(phone="+2348300000001", password="pw123456", country="NG")
        self.client = APIClient()
        self._env_patch = patch.dict(
            os.environ, {"DJANGO_INTERNAL_TOKEN": "test-internal-token", "INTERNAL_SIGNATURE_REQUIRED": "0"},
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _headers(self):
        return {"HTTP_X_INTERNAL_AUTH": "test-internal-token"}

    def test_creates_a_report(self):
        res = self.client.post(
            REPORT_URL,
            {
                "conversationId": "conv-abc",
                "messageId": "64f1a2b3c4d5e6f7a8b9c0d1",
                "reportedBy": str(self.reporter.id),
                "reason": "spam",
                "note": "keeps sending links",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(res.status_code, 200)
        report = ChatMessageReport.objects.get()
        self.assertEqual(report.conversation_id, "conv-abc")
        self.assertEqual(report.message_id, "64f1a2b3c4d5e6f7a8b9c0d1")
        self.assertEqual(str(report.reported_by_id), str(self.reporter.id))
        self.assertEqual(report.status, "PENDING")

    def test_is_idempotent_for_the_same_reporter_and_message(self):
        payload = {
            "conversationId": "conv-abc",
            "messageId": "64f1a2b3c4d5e6f7a8b9c0d1",
            "reportedBy": str(self.reporter.id),
            "reason": "spam",
        }
        self.client.post(REPORT_URL, payload, format="json", **self._headers())
        self.client.post(REPORT_URL, payload, format="json", **self._headers())

        self.assertEqual(ChatMessageReport.objects.count(), 1)

    def test_missing_fields_are_rejected(self):
        res = self.client.post(
            REPORT_URL, {"conversationId": "conv-abc"}, format="json", **self._headers(),
        )

        self.assertEqual(res.status_code, 400)

    def test_requires_internal_auth(self):
        res = self.client.post(
            REPORT_URL,
            {"conversationId": "conv-abc", "messageId": "m1", "reportedBy": str(self.reporter.id)},
            format="json",
        )

        self.assertEqual(res.status_code, 401)


class ChatMessageReportStaffQueueTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.reporter = User.objects.create_user(phone="+2348300000002", password="pw123456", country="NG")
        self.admin = User.objects.create_user(
            phone="+2348300000003", password="pw123456", country="NG", is_staff=True,
        )
        self.client = APIClient()

    def test_pending_report_appears_in_the_staff_queue(self):
        ChatMessageReport.objects.create(
            conversation_id="conv-abc",
            message_id="64f1a2b3c4d5e6f7a8b9c0d1",
            reported_by_id=self.reporter.id,
            reason="spam",
        )
        self.client.force_authenticate(self.admin)

        res = self.client.get(QUEUE_URL, {"source": "chat"})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["summary"]["chat_messages"], 1)
        self.assertEqual(res.data["results"][0]["kind"], "chat_message_report")
        self.assertEqual(res.data["results"][0]["target_id"], "64f1a2b3c4d5e6f7a8b9c0d1")

    def test_dismissed_reports_do_not_appear_in_the_queue(self):
        ChatMessageReport.objects.create(
            conversation_id="conv-abc",
            message_id="m1",
            reported_by_id=self.reporter.id,
            status="DISMISSED",
        )
        self.client.force_authenticate(self.admin)

        res = self.client.get(QUEUE_URL, {"source": "chat"})

        self.assertEqual(res.data["summary"]["chat_messages"], 0)

    def test_non_staff_cannot_view_the_queue(self):
        self.client.force_authenticate(self.reporter)

        res = self.client.get(QUEUE_URL, {"source": "chat"})

        self.assertEqual(res.status_code, 403)

    def test_staff_dismiss_action_updates_status_without_calling_nest(self):
        report = ChatMessageReport.objects.create(
            conversation_id="conv-abc", message_id="m1", reported_by_id=self.reporter.id,
        )
        self.client.force_authenticate(self.admin)

        with patch("apps.chat.tasks._post_to_nest") as mock_post:
            res = self.client.post(
                ACTION_URL,
                {"target_type": "chat_message_report", "target_id": str(report.id), "action": "dismiss"},
                format="json",
            )

        self.assertEqual(res.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.status, "DISMISSED")
        mock_post.assert_not_called()

    def test_staff_block_action_updates_status_and_calls_nest_to_delete_the_message(self):
        report = ChatMessageReport.objects.create(
            conversation_id="conv-abc", message_id="m1", reported_by_id=self.reporter.id,
        )
        self.client.force_authenticate(self.admin)

        with patch("apps.chat.tasks._post_to_nest") as mock_post:
            res = self.client.post(
                ACTION_URL,
                {"target_type": "chat_message_report", "target_id": str(report.id), "action": "block"},
                format="json",
            )

        self.assertEqual(res.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.status, "ACTIONED")
        mock_post.assert_called_once_with("conversations/conv-abc/messages/m1/moderate-delete", {})

    def test_block_action_still_actions_the_report_even_if_nest_is_unreachable(self):
        report = ChatMessageReport.objects.create(
            conversation_id="conv-abc", message_id="m1", reported_by_id=self.reporter.id,
        )
        self.client.force_authenticate(self.admin)

        with patch("apps.chat.tasks._post_to_nest", side_effect=Exception("nest is down")):
            res = self.client.post(
                ACTION_URL,
                {"target_type": "chat_message_report", "target_id": str(report.id), "action": "block"},
                format="json",
            )

        self.assertEqual(res.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.status, "ACTIONED")
