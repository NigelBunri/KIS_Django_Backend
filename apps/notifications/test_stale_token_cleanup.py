from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import firebase, models, services, tasks


class IsStaleTokenErrorTests(TestCase):
    def test_unregistered_and_invalid_argument_are_stale(self):
        class UnregisteredError(Exception):
            pass

        class InvalidArgumentError(Exception):
            pass

        self.assertTrue(firebase.is_stale_token_error(UnregisteredError("gone")))
        self.assertTrue(firebase.is_stale_token_error(InvalidArgumentError("bad")))

    def test_transient_errors_are_not_stale(self):
        class UnavailableError(Exception):
            pass

        class QuotaExceededError(Exception):
            pass

        self.assertFalse(firebase.is_stale_token_error(UnavailableError("retry me")))
        self.assertFalse(firebase.is_stale_token_error(QuotaExceededError("slow down")))
        self.assertFalse(firebase.is_stale_token_error(Exception("generic")))


class ProcessNotificationDeliveryStaleTokenTests(TestCase):
    """Regression test: a permanently-invalid push token previously stayed
    `enabled=True` forever — every future notification to that user kept
    re-attempting (and re-failing) the same dead token indefinitely, with
    no cleanup. process_notification_delivery must now deactivate a token
    exactly when firebase.send_push reports it as stale, and leave
    non-stale-failure tokens untouched so a transient error doesn't wipe a
    perfectly good token."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670005201", password="TestPass123!", country="CM")
        self.stale_token = models.NotificationDeviceToken.objects.create(
            user_id=self.user.id, device_id="device-stale", push_token="stale-token", enabled=True,
        )
        self.good_token = models.NotificationDeviceToken.objects.create(
            user_id=self.user.id, device_id="device-good", push_token="good-token", enabled=True,
        )

    def _create_notification(self):
        with patch("apps.notifications.tasks.process_notification_delivery.delay"):
            return services.create_notification(
                user_id=self.user.id, type="EVENT_ALERT", title="Hello", body="World",
            )

    def test_deactivates_only_the_stale_token(self):
        notif = self._create_notification()

        def fake_send_push(token, _notification):
            if token == "stale-token":
                return False, "UnregisteredError", True
            return True, "message-id", False

        with patch.object(firebase, "send_push", side_effect=fake_send_push):
            tasks.process_notification_delivery(notif.id)

        self.stale_token.refresh_from_db()
        self.good_token.refresh_from_db()
        self.assertFalse(self.stale_token.enabled)
        self.assertTrue(self.good_token.enabled)

    def test_delivery_still_marked_sent_when_at_least_one_token_succeeds(self):
        notif = self._create_notification()

        def fake_send_push(token, _notification):
            if token == "stale-token":
                return False, "UnregisteredError", True
            return True, "message-id", False

        with patch.object(firebase, "send_push", side_effect=fake_send_push):
            tasks.process_notification_delivery(notif.id)

        delivery = notif.deliveries.get(channel="PUSH")
        self.assertEqual(delivery.status, "SENT")

    def test_a_transient_failure_does_not_deactivate_the_token(self):
        notif = self._create_notification()

        with patch.object(firebase, "send_push", return_value=(False, "UnavailableError", False)):
            tasks.process_notification_delivery(notif.id)

        self.stale_token.refresh_from_db()
        self.good_token.refresh_from_db()
        self.assertTrue(self.stale_token.enabled)
        self.assertTrue(self.good_token.enabled)
        delivery = notif.deliveries.get(channel="PUSH")
        self.assertEqual(delivery.status, "PENDING")
