# Open to Work / Hiring spec, "notifications/activity items" avatar
# context: Notification.actor_user_id (who performed the action) and
# NotificationSerializer.get_actor (the gated, enriched view of it a
# client renders an avatar+WorkStatusRing from). Covers:
#   - create_notification persists actor_user_id when passed, None when not
#   - the mention endpoint and ConnectionViewSet's request/accept flows
#     actually pass it (regression coverage for the real call sites wired
#     up, not just the plumbing)
#   - the serializer's actor payload is gated by the SAME
#     ProfileFieldVisibility rule as every other work_status exposure
#     (apps.accounts.test_work_status.py already covers get_visible_work_status
#     itself exhaustively - this only confirms the serializer actually
#     calls through to it with the right owner/viewer)
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import Profile, ProfileFieldVisibility, WorkStatus

from .models import Notification
from .serializers import NotificationSerializer
from .services import create_notification


class CreateNotificationActorTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.recipient = User.objects.create_user(phone="+237670005410", password="TestPass123!", country="CM")
        self.actor = User.objects.create_user(phone="+237670005411", password="TestPass123!", country="CM")

    def test_actor_user_id_persisted_when_passed(self):
        notif = create_notification(
            user_id=self.recipient.id,
            actor_user_id=self.actor.id,
            type="test.actor",
            title="t",
            body="b",
        )
        notif.refresh_from_db()
        self.assertEqual(notif.actor_user_id, self.actor.id)

    def test_actor_user_id_none_when_omitted(self):
        notif = create_notification(
            user_id=self.recipient.id,
            type="test.no_actor",
            title="t",
            body="b",
        )
        notif.refresh_from_db()
        self.assertIsNone(notif.actor_user_id)


class NotificationSerializerActorGatingTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.recipient = User.objects.create_user(phone="+237670005412", password="TestPass123!", country="CM")
        self.actor = User.objects.create_user(phone="+237670005413", password="TestPass123!", country="CM")
        profile, _ = Profile.objects.get_or_create(user=self.actor)
        profile.work_status = WorkStatus.OPEN_FOR_WORK
        profile.avatar_url = "https://example.com/avatar.png"
        profile.save(update_fields=["work_status", "avatar_url"])
        # Reverse one-to-one cache gotcha (see apps.accounts.test_work_status's
        # own setUp comment) - the User.post_save signal already cached a
        # pre-update Profile on self.actor; re-fetch so get_actor's own
        # getattr(actor, "profile", None) sees the fresh row.
        self.actor = User.objects.get(pk=self.actor.pk)

    def _notif(self):
        return Notification.objects.create(
            user_id=self.recipient.id,
            actor_user_id=self.actor.id,
            type="test.actor_gating",
            title="t",
            body="b",
        )

    def test_no_actor_user_id_returns_none(self):
        notif = Notification.objects.create(user_id=self.recipient.id, type="test.none", title="t", body="b")
        data = NotificationSerializer(notif, context={"request": None}).data
        self.assertIsNone(data["actor"])

    def test_public_status_actor_is_fully_visible_to_any_viewer(self):
        notif = self._notif()

        class _Req:
            user = self.recipient

        data = NotificationSerializer(notif, context={"request": _Req()}).data
        self.assertIsNotNone(data["actor"])
        self.assertEqual(data["actor"]["id"], str(self.actor.id))
        self.assertEqual(data["actor"]["work_status"], "open_for_work")
        self.assertEqual(data["actor"]["avatar_url"], "https://example.com/avatar.png")

    def test_private_status_actor_hides_work_status_from_a_stranger(self):
        ProfileFieldVisibility.objects.create(user=self.actor, field_key="work_status", visibility="private")
        notif = self._notif()

        class _Req:
            user = self.recipient

        data = NotificationSerializer(notif, context={"request": _Req()}).data
        # Identity/avatar still shown (same tradeoff as every other actor
        # context - a stranger who can see the notification can already see
        # who performed the action from the title text itself), only the
        # status ring's data is gated.
        self.assertIsNotNone(data["actor"])
        self.assertIsNone(data["actor"]["work_status"])

    def test_deleted_actor_user_returns_none(self):
        notif = self._notif()
        bogus_id = self.actor.id
        self.actor.delete()
        notif.actor_user_id = bogus_id
        notif.save(update_fields=["actor_user_id"])

        class _Req:
            user = self.recipient

        data = NotificationSerializer(notif, context={"request": _Req()}).data
        self.assertIsNone(data["actor"])


class MentionEndpointActorWiringTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.sender = User.objects.create_user(phone="+237670005414", password="TestPass123!", country="CM")
        self.mentioned = User.objects.create_user(phone="+237670005415", password="TestPass123!", country="CM")
        self.client = APIClient()
        self.url = reverse("mention-notification")

    def test_mention_notification_carries_the_mentioner_as_actor(self):
        self.client.force_authenticate(self.sender)
        resp = self.client.post(
            self.url,
            {
                "mentioned_user_ids": [str(self.mentioned.id)],
                "context": {"sender_name": "Sender", "preview": "hi", "conversation_id": "c1", "message_id": "m1"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        notif = Notification.objects.get(user_id=self.mentioned.id, type="mention")
        self.assertEqual(notif.actor_user_id, self.sender.id)


class ConnectionNotificationActorWiringTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.sender = User.objects.create_user(phone="+237670005416", password="TestPass123!", country="CM")
        self.target = User.objects.create_user(phone="+237670005417", password="TestPass123!", country="CM")
        self.client = APIClient()

    def test_connection_request_carries_the_requester_as_actor(self):
        self.client.force_authenticate(self.sender)
        resp = self.client.post(reverse("connections-list"), {"user_id": str(self.target.id)}, format="json")
        self.assertEqual(resp.status_code, 201)
        notif = Notification.objects.get(user_id=self.target.id, type="connection_request")
        self.assertEqual(notif.actor_user_id, self.sender.id)

    def test_connection_accepted_carries_the_accepter_as_actor(self):
        self.client.force_authenticate(self.sender)
        create_resp = self.client.post(reverse("connections-list"), {"user_id": str(self.target.id)}, format="json")
        conn_id = create_resp.data["id"]

        self.client.force_authenticate(self.target)
        accept_resp = self.client.patch(reverse("connections-detail", args=[conn_id]), {"status": "accepted"}, format="json")
        self.assertEqual(accept_resp.status_code, 200)

        notif = Notification.objects.get(user_id=self.sender.id, type="connection_accepted")
        self.assertEqual(notif.actor_user_id, self.target.id)
