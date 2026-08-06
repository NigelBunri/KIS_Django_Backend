from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.notifications.models import Notification

from .models import Device
from .views import _send_push_to_device


class DeviceLinkedPushTests(TestCase):
    """Regression test for the QR-login "new device linked" notification.

    It previously constructed NotificationDeviceToken/Notification objects
    using field names that don't exist on either model (`user`/`token`/
    `notification_type` instead of the real `user_id`/`push_token`/`type`),
    so every call raised inside a bare `except Exception: pass` — this
    notification has never actually been created or delivered. Routed
    through the canonical apps.notifications.services.create_notification
    entrypoint instead; this test proves that no longer raises and that a
    real, correctly-shaped Notification row results.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670005101", password="TestPass123!", country="CM")
        self.parent_device = Device.objects.create(
            user=self.user, device_id="parent-device-1", platform="ios", is_parent=True,
        )

    def test_does_not_raise_and_creates_a_real_notification(self):
        _send_push_to_device(
            self.user, self.parent_device, title="New device linked", body="New device linked: Pixel 8",
        )

        notif = Notification.objects.filter(user_id=self.user.id, type="device.linked").first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.title, "New device linked")
        self.assertEqual(notif.body, "New device linked: Pixel 8")
        self.assertEqual(notif.target_type, "accounts.Device")
        self.assertEqual(notif.target_id, self.parent_device.id)

    def test_schedules_an_in_app_and_push_delivery(self):
        _send_push_to_device(
            self.user, self.parent_device, title="New device linked", body="New device linked: Pixel 8",
        )
        notif = Notification.objects.get(user_id=self.user.id, type="device.linked")
        channels = set(notif.deliveries.values_list("channel", flat=True))
        self.assertIn("IN_APP", channels)
        self.assertIn("PUSH", channels)

    def test_never_raises_even_if_notification_creation_fails(self):
        # Still a best-effort call site — a failure here must not break the
        # QR login flow that triggers it.
        try:
            _send_push_to_device(None, self.parent_device, title="x", body="y")
        except Exception as exc:  # pragma: no cover - the assertion below is the real check
            self.fail(f"_send_push_to_device raised: {exc}")

    def test_dedup_key_prevents_a_duplicate_push_for_the_same_login_event(self):
        # Regression test: _send_push_to_device previously called
        # create_notification with no dedup_key at all. DeviceQRToken.consume()
        # reads-then-writes `used_at` without row locking, so two near-
        # simultaneous requests for the same QR token (e.g. a client retry
        # racing the original request after a network timeout) could both
        # pass the "not yet used" check and both reach this call — producing
        # two "new device linked" pushes for what is really one login event.
        # The caller now passes a dedup_key derived from the QR token id;
        # simulating that race directly against this function proves a
        # second call for the same token no longer creates a second row.
        dedup_key = "device.linked:qr_token:11111111-1111-1111-1111-111111111111"
        _send_push_to_device(
            self.user, self.parent_device, title="New device linked", body="a", dedup_key=dedup_key,
        )
        _send_push_to_device(
            self.user, self.parent_device, title="New device linked", body="a", dedup_key=dedup_key,
        )

        count = Notification.objects.filter(user_id=self.user.id, type="device.linked").count()
        self.assertEqual(count, 1)
