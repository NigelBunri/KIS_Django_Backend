
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from . import models, services
import uuid
from unittest.mock import patch


class NotificationTest(TestCase):
    def test_create_and_dedupe(self):
        uid = uuid.uuid4()
        with patch("apps.notifications.tasks.process_notification_delivery.delay"):
            notif1 = services.create_notification(user_id=uid, type="EVENT_ALERT", title="Hello", body="World", dedup_key="evt-1")
            notif2 = services.create_notification(user_id=uid, type="EVENT_ALERT", title="Hello", body="World", dedup_key="evt-1")
        self.assertEqual(str(notif1.id), str(notif2.id))
        self.assertEqual(notif1.channel, "IN_APP")
        self.assertTrue(notif1.deliveries.filter(channel="IN_APP").exists())
        self.assertTrue(notif1.deliveries.filter(channel="PUSH").exists())

    def test_template_render(self):
        tpl = models.NotificationTemplate.objects.create(key="test.tpl", title_template="Hi {{name}}", body_template="Welcome {{name}}")
        title, body = tpl.render({"name": "Nigel"})
        self.assertIn("Nigel", title)


class NotificationAPITest(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+15550001000", country="US", password="password12345")
        self.client.force_authenticate(self.user)

    def test_register_and_unregister_device_token(self):
        response = self.client.post(
            "/api/v1/notification-device-tokens/register/",
            {
                "device_id": "device-1",
                "platform": "ios",
                "push_token": "fcm-token-1",
                "token_type": "fcm",
                "metadata": {"source": "test"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("push_token", response.data)
        self.assertEqual(response.data["masked_push_token"], "fcm-to...en-1")
        self.assertTrue(models.NotificationDeviceToken.objects.filter(user_id=self.user.id, enabled=True).exists())

        response = self.client.post(
            "/api/v1/notification-device-tokens/unregister/",
            {"device_id": "device-1", "push_token": "fcm-token-1"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], 1)
        self.assertFalse(models.NotificationDeviceToken.objects.filter(user_id=self.user.id, enabled=True, is_deleted=False).exists())

    def test_mark_read_emits_main_tab_badge_refresh(self):
        notification = models.Notification.objects.create(
            user_id=self.user.id,
            type="GENERAL_ALERT",
            title="Unread",
            body="Unread notification",
            is_read=False,
        )

        with patch("apps.notifications.views.notify_main_tab_badges_updated") as notify:
            response = self.client.post(f"/api/v1/notifications/{notification.id}/mark_read/")

        self.assertEqual(response.status_code, 200)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["source"], "notifications")
        self.assertEqual(notify.call_args.kwargs["reason"], "mark_read")

    def test_mark_source_read_marks_matching_notifications_only(self):
        bible = models.Notification.objects.create(
            user_id=self.user.id,
            type="BIBLE_DAILY_MEDITATION",
            title="Daily meditation",
            body="Read today",
            target_type="bible_meditation_post",
            is_read=False,
        )
        market = models.Notification.objects.create(
            user_id=self.user.id,
            type="MARKET_PRODUCT_UPDATE",
            title="Product update",
            body="Shop update",
            target_type="product",
            is_read=False,
        )

        with patch("apps.notifications.views.notify_main_tab_badges_updated") as notify:
            response = self.client.post(
                "/api/v1/notifications/mark-source-read/",
                {"source": "bible"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], 1)
        bible.refresh_from_db()
        market.refresh_from_db()
        self.assertTrue(bible.is_read)
        self.assertFalse(market.is_read)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["source"], "bible")

    def test_attention_summary_and_filters_use_source_priority_and_urgency(self):
        models.Notification.objects.create(
            user_id=self.user.id,
            type="BIBLE_DAILY_MEDITATION",
            title="Morning prayer",
            body="Read and pray",
            priority="HIGH",
            is_read=False,
            context_data={"source": "bible", "badge_source": "bible", "urgency_label": "spiritual"},
        )
        models.Notification.objects.create(
            user_id=self.user.id,
            type="MARKET_PRODUCT_UPDATE",
            title="Market update",
            body="New product",
            priority="LOW",
            is_read=False,
            context_data={"source": "broadcast", "badge_source": "broadcast", "urgency_label": "commerce"},
        )

        summary = self.client.get("/api/v1/notifications/attention-summary/")
        filtered = self.client.get("/api/v1/notifications/", {"source": "bible", "urgency": "spiritual", "priority": "HIGH"})

        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.data["unread_count"], 2)
        self.assertEqual(summary.data["urgent_now"], 1)
        self.assertEqual(filtered.status_code, 200)
        rows = filtered.data.get("results", filtered.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Morning prayer")

    def test_attention_preferences_store_quiet_hours_and_source_rule(self):
        with patch("apps.notifications.views.notify_main_tab_badges_updated") as notify:
            response = self.client.post(
                "/api/v1/notifications/attention-preferences/",
                {
                    "quiet_hours": {"start": "21:00", "end": "06:00"},
                    "digest": {"frequency": "daily", "time": "07:30"},
                    "source": "broadcast",
                    "source_enabled": False,
                    "source_priority": "LOW",
                    "source_channels": ["IN_APP"],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["quiet_hours"]["start"], "21:00")
        self.assertEqual(response.data["digest"]["frequency"], "daily")
        self.assertEqual(response.data["source_rules"][0]["source"], "broadcast")
        self.assertFalse(response.data["source_rules"][0]["enabled"])
        notify.assert_called_once()

class MainTabBadgeCountsAPITest(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+15550001010", country="US", password="password12345")
        self.other = User.objects.create_user(phone="+15550001011", country="US", password="password12345")
        self.client.force_authenticate(self.user)

    def test_main_tab_badge_counts_include_profile_and_messages(self):
        from apps.chat.models import Conversation, ConversationMember, ConversationType, BaseConversationRole

        models.Notification.objects.create(
            user_id=self.user.id,
            type="GENERAL_ALERT",
            title="Unread profile notification",
            body="Hello",
            is_read=False,
        )
        conversation = Conversation.objects.create(
            type=ConversationType.DIRECT,
            created_by=self.other,
            last_message_seq=4,
            last_message_preview="Hello",
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=self.user,
            base_role=BaseConversationRole.MEMBER,
            last_read_seq=1,
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=self.other,
            base_role=BaseConversationRole.MEMBER,
            last_read_seq=4,
        )

        response = self.client.get("/api/v1/notifications/main-tab-badge-counts/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["counts"]["Profile"], 1)
        self.assertEqual(response.data["counts"]["Messages"], 3)
        self.assertIn("sources", response.data)

    def test_source_notifications_increment_and_decrement_exact_badge_tabs(self):
        cases = [
            ("BIBLE_DAILY_MEDITATION", "bible_meditation_post", "bible", "Bible"),
            ("CHANNEL_CONTENT_PUBLISHED", "channel_content", "broadcast", "Broadcast"),
            ("PARTNER_GROUP_MESSAGE", "partner_group", "partners", "Partners"),
            ("ACCOUNT_SECURITY_ALERT", "account", "profile", "Profile"),
        ]

        with patch("apps.notifications.tasks.process_notification_delivery.delay"):
            for notification_type, target_type, expected_source, tab_name in cases:
                target_id = uuid.uuid4()
                notification = services.create_notification(
                    user_id=self.user.id,
                    type=notification_type,
                    title=f"{tab_name} unread",
                    body="Unread",
                    target_type=target_type,
                    target_id=target_id,
                )
                self.assertEqual(notification.context_data.get("source"), expected_source)
                self.assertEqual(notification.context_data.get("badge_source"), expected_source)

                response = self.client.get("/api/v1/notifications/main-tab-badge-counts/")
                self.assertEqual(response.status_code, 200)
                self.assertGreaterEqual(response.data["counts"][tab_name], 1)

                mark_response = self.client.post(
                    "/api/v1/notifications/mark-source-read/",
                    {
                        "source": expected_source,
                        "target_type": target_type,
                        "target_id": str(target_id),
                    },
                    format="json",
                )
                self.assertEqual(mark_response.status_code, 200)
                self.assertEqual(mark_response.data["updated"], 1)

                response = self.client.get("/api/v1/notifications/main-tab-badge-counts/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["counts"][tab_name], 0)

    def test_message_badge_decrements_after_mark_read(self):
        from apps.chat.models import Conversation, ConversationMember, ConversationType, BaseConversationRole

        conversation = Conversation.objects.create(
            type=ConversationType.DIRECT,
            created_by=self.other,
            last_message_seq=6,
            last_message_preview="Hello again",
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=self.user,
            base_role=BaseConversationRole.MEMBER,
            last_read_seq=2,
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=self.other,
            base_role=BaseConversationRole.MEMBER,
            last_read_seq=6,
        )

        response = self.client.get("/api/v1/notifications/main-tab-badge-counts/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["counts"]["Messages"], 4)

        mark_response = self.client.post(f"/api/v1/conversations/{conversation.id}/mark-read/")
        self.assertEqual(mark_response.status_code, 200)

        response = self.client.get("/api/v1/notifications/main-tab-badge-counts/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["counts"]["Messages"], 0)
