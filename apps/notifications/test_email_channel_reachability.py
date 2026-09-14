"""
Email-system audit, Priority 2 (row 12, "Unreachable"): the EMAIL delivery
channel in process_notification_delivery (this file's sibling tasks.py) is
fully engineered — Celery, retries, backoff, a real email_service call —
but nothing anywhere ever requested it. create_notification's channel
selection (services.py) only ever uses a NotificationRule's channels_json
when it's non-empty, and nothing in the app ever wrote "EMAIL" into it:
NotificationRuleSerializer already accepts it (fields = "__all__", not
read-only), but the RN settings screen (ProfileNotificationsScreen) never
surfaced a way to set it — every rule was created with the model's default
(channels_json=[]).

Fixed on the frontend (an "Email" toggle per notification-type rule,
PATCHing channels_json) — these tests cover the backend half: that the API
genuinely persists channels_json, that create_notification genuinely
creates an EMAIL delivery row once a rule requests it, and that the
already-engineered delivery task genuinely calls email_service for it.
None of that needed to change; it was already correct and simply never
exercised.

Run:
  python3 manage.py test apps.notifications.test_email_channel_reachability --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import Device, User
from apps.accounts.views import issue_tokens_for_user
from apps.notifications import models, services

DEVICE_ID = "email-channel-reachability-test-device"


def _make_user(phone: str) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.email = f"{phone.lstrip('+')}@example.com"
    user.status = "active"
    user.is_active = True
    user.save(update_fields=["email", "status", "is_active"])
    Device.objects.create(user=user, device_id=DEVICE_ID, platform="android", is_parent=True, token_version=1)
    return user


@override_settings(SECURE_SSL_REDIRECT=False)
class NotificationRuleChannelsApiTests(TestCase):
    """Confirms the existing API can actually persist channels_json — the
    other half of "no UI ever puts EMAIL into channels_json" is that
    nothing had ever exercised whether it would even take."""

    def setUp(self):
        self.user = _make_user("+237699700001")
        tokens = issue_tokens_for_user(self.user, device_id=DEVICE_ID)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID=DEVICE_ID)

    def test_creating_a_rule_with_email_channel_persists_it(self):
        res = self.client.post(
            "/api/v1/notification-rules/",
            {"type": "message", "enabled": True, "channels_json": ["IN_APP", "PUSH", "EMAIL"]},
            format="json",
        )

        self.assertEqual(res.status_code, 201, res.data)
        rule = models.NotificationRule.objects.get(id=res.data["id"])
        self.assertEqual(rule.channels_json, ["IN_APP", "PUSH", "EMAIL"])

    def test_patching_channels_json_onto_an_existing_rule_persists_it(self):
        rule = models.NotificationRule.objects.create(user_id=self.user.id, type="community", enabled=True)
        self.assertEqual(rule.channels_json, [])

        res = self.client.patch(
            f"/api/v1/notification-rules/{rule.id}/",
            {"channels_json": ["IN_APP", "PUSH", "EMAIL"]},
            format="json",
        )

        self.assertEqual(res.status_code, 200, res.data)
        rule.refresh_from_db()
        self.assertEqual(rule.channels_json, ["IN_APP", "PUSH", "EMAIL"])

    def test_removing_email_from_channels_json_persists_it(self):
        rule = models.NotificationRule.objects.create(
            user_id=self.user.id, type="community", enabled=True, channels_json=["IN_APP", "PUSH", "EMAIL"],
        )

        res = self.client.patch(
            f"/api/v1/notification-rules/{rule.id}/",
            {"channels_json": ["IN_APP", "PUSH"]},
            format="json",
        )

        self.assertEqual(res.status_code, 200, res.data)
        rule.refresh_from_db()
        self.assertEqual(rule.channels_json, ["IN_APP", "PUSH"])


class EmailChannelEndToEndReachabilityTests(TestCase):
    """The actual "is it reachable" proof: a user with channels_json
    containing EMAIL gets a real EMAIL NotificationDelivery row, and the
    already-engineered delivery task genuinely calls email_service for it —
    a path that, before the frontend fix, no user could ever reach because
    channels_json was always empty."""

    def setUp(self):
        self.user = _make_user("+237699700002")

    def test_no_rule_never_creates_an_email_delivery(self):
        # Baseline: today's default behavior (no rule at all, or an empty
        # channels_json) must keep working exactly as before — this fix is
        # additive, not a default-on change for every user.
        notif = services.create_notification(
            user_id=self.user.id, type="message", title="Hi", body="Hello",
        )

        channels = set(notif.deliveries.values_list("channel", flat=True))
        self.assertNotIn("EMAIL", channels)

    def test_rule_with_email_in_channels_json_creates_an_email_delivery(self):
        models.NotificationRule.objects.create(
            user_id=self.user.id, type="message", enabled=True,
            channels_json=["IN_APP", "PUSH", "EMAIL"],
        )

        notif = services.create_notification(
            user_id=self.user.id, type="message", title="Hi", body="Hello",
        )

        channels = set(notif.deliveries.values_list("channel", flat=True))
        self.assertIn("EMAIL", channels)
        self.assertIn("IN_APP", channels)
        self.assertIn("PUSH", channels)

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_email_delivery_actually_invokes_email_service_via_the_celery_task(self, mock_send):
        # CELERY_TASK_ALWAYS_EAGER is on for test runs (see config/settings/
        # local.py), so create_notification's process_notification_delivery
        # .delay(...) call runs synchronously right here — this exercises
        # the real, previously-never-triggered send path end to end.
        models.NotificationRule.objects.create(
            user_id=self.user.id, type="message", enabled=True,
            channels_json=["IN_APP", "PUSH", "EMAIL"],
        )

        services.create_notification(
            user_id=self.user.id, type="message", title="Hi there", body="Hello body",
        )

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["to_email"], self.user.email)
        self.assertEqual(kwargs["title"], "Hi there")
        self.assertEqual(kwargs["body"], "Hello body")

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_user_with_no_email_address_gets_a_pending_delivery_not_a_crash(self, mock_send):
        no_email_user = User.objects.create_user(phone="+237699700003", password="TestPass12!", country="CM")
        models.NotificationRule.objects.create(
            user_id=no_email_user.id, type="message", enabled=True,
            channels_json=["IN_APP", "PUSH", "EMAIL"],
        )

        notif = services.create_notification(
            user_id=no_email_user.id, type="message", title="Hi", body="Hello",
        )

        mock_send.assert_not_called()
        delivery = notif.deliveries.get(channel="EMAIL")
        self.assertEqual(delivery.status, "PENDING")
        self.assertIn("email", delivery.last_error.lower())
