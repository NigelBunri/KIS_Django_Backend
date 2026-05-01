
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
