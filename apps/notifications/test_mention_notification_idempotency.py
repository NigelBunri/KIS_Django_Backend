from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Notification


class MentionNotificationIdempotencyTests(APITestCase):
    """Regression test: MentionNotificationView previously built the
    Notification row by hand via Notification.objects.create(), bypassing
    the canonical create_notification entrypoint entirely — no dedup_key, no
    deliveries scheduled (so no push was ever actually sent), and no
    protection against a client retrying this endpoint (e.g. after a network
    timeout on the response) creating a second mention notification for the
    same message. It's now routed through create_notification with a
    dedup_key derived from (conversation, message, mentioned user)."""

    def setUp(self):
        User = get_user_model()
        self.sender = User.objects.create_user(phone="+237670005401", password="TestPass123!", country="CM")
        self.mentioned = User.objects.create_user(phone="+237670005402", password="TestPass123!", country="CM")
        self.url = reverse("mention-notification")

    def _payload(self):
        return {
            "mentioned_user_ids": [str(self.mentioned.id)],
            "context": {
                "sender_name": "Sender",
                "preview": "hey @mentioned check this out",
                "conversation_id": "conv-1",
                "message_id": "msg-1",
            },
        }

    def test_creates_a_dedup_keyed_notification_with_deliveries(self):
        self.client.force_authenticate(self.sender)
        resp = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(resp.status_code, 200)

        notif = Notification.objects.filter(user_id=self.mentioned.id, type="mention").first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.dedup_key, "mention:conv-1:msg-1:" + str(self.mentioned.id))
        channels = set(notif.deliveries.values_list("channel", flat=True))
        self.assertIn("IN_APP", channels)
        self.assertIn("PUSH", channels)

    def test_retrying_the_same_mention_request_does_not_duplicate_the_notification(self):
        self.client.force_authenticate(self.sender)
        self.client.post(self.url, self._payload(), format="json")
        self.client.post(self.url, self._payload(), format="json")

        count = Notification.objects.filter(user_id=self.mentioned.id, type="mention").count()
        self.assertEqual(count, 1)

    def test_a_mention_with_no_message_id_still_creates_a_notification(self):
        # No stable identity to dedupe on in this case — dedup is skipped
        # rather than risking suppression of an unrelated, legitimate mention.
        self.client.force_authenticate(self.sender)
        payload = self._payload()
        payload["context"]["message_id"] = ""
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, 200)

        notif = Notification.objects.filter(user_id=self.mentioned.id, type="mention").first()
        self.assertIsNotNone(notif)
        self.assertIsNone(notif.dedup_key)
