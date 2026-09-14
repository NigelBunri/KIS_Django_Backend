"""
Email-system audit, Priority 2 discovery #2b: StaffModerationOperationActionView
(flag dismiss/action/escalate, channel-content block/approve/dismiss/escalate)
previously called record_moderation_audit but never create_notification at
all — a human moderator taking real action on someone's content or account
told them nothing, unlike the automated AI-strike pipeline's equivalent
WARN/SUSPEND notifications (apps/moderation/services.py).

Scope note: Flag.target_type is genuinely polymorphic across producers with
no single safe way to resolve "the owner" without per-type verification
(POST is even used for MediaSafetyScan ids from one caller, ChannelContent
ids from others). Only target_type == "USER" (target_id IS the user,
unambiguous) is wired up here; other flag target types are a documented,
deliberate gap, not an oversight — covered by
test_non_user_flag_target_types_do_not_attempt_a_notification below.

Run:
  python3 manage.py test apps.Moderation.test_staff_action_notifications --keepdb -v 2
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog
from apps.broadcasts.models import (
    BroadcastChannel,
    ChannelContent,
    ChannelContentComment,
    ChannelContentType,
    ChannelModerationRecord,
)
from apps.notifications.models import Notification
from . import models

User = get_user_model()

URL = "/api/v1/moderation/staff/operation-action/"


@override_settings(SECURE_SSL_REDIRECT=False)
class StaffFlagActionNotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(phone="+237670004001", password="TestPass123!", country="CM", is_staff=True)
        self.flagged_user = User.objects.create_user(
            phone="+237670004002", password="TestPass123!", country="CM", email="flagged@example.com",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _make_flag(self, **overrides):
        defaults = dict(source="USER", target_type="USER", target_id=self.flagged_user.id, reason="test", severity="MEDIUM")
        defaults.update(overrides)
        return models.Flag.objects.create(**defaults)

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_actioning_a_user_target_flag_notifies_with_email(self, mock_send):
        flag = self._make_flag()

        res = self.client.post(URL, {"target_type": "flag", "target_id": str(flag.id), "action": "block"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        notif = Notification.objects.get(user_id=self.flagged_user.id, type="MODERATION_STAFF_ACTION")
        channels = set(notif.deliveries.values_list("channel", flat=True))
        self.assertIn("EMAIL", channels)
        mock_send.assert_called_once()

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_escalating_a_user_target_flag_also_notifies(self, mock_send):
        flag = self._make_flag()

        res = self.client.post(URL, {"target_type": "flag", "target_id": str(flag.id), "action": "escalate"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(Notification.objects.filter(user_id=self.flagged_user.id, type="MODERATION_STAFF_ACTION").exists())

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_dismissing_a_flag_does_not_notify(self, mock_send):
        flag = self._make_flag()

        res = self.client.post(URL, {"target_type": "flag", "target_id": str(flag.id), "action": "dismiss"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(Notification.objects.filter(user_id=self.flagged_user.id, type="MODERATION_STAFF_ACTION").exists())
        mock_send.assert_not_called()

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_reviewing_a_flag_does_not_notify(self, mock_send):
        flag = self._make_flag()

        res = self.client.post(URL, {"target_type": "flag", "target_id": str(flag.id), "action": "review"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        mock_send.assert_not_called()

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_non_user_flag_target_types_do_not_attempt_a_notification(self, mock_send):
        # Documented scope boundary, not an oversight — see module docstring.
        import uuid
        flag = self._make_flag(target_type="POST", target_id=uuid.uuid4())

        res = self.client.post(URL, {"target_type": "flag", "target_id": str(flag.id), "action": "block"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        mock_send.assert_not_called()
        self.assertFalse(Notification.objects.filter(type="MODERATION_STAFF_ACTION").exists())

    @patch("apps.notifications.services.create_notification", side_effect=RuntimeError("boom"))
    def test_notification_creation_failure_is_audited_without_failing_the_moderation_action(self, _mock_create):
        # _notify_moderation_target's except only ever sees a failure to
        # CREATE the notification (a bug, bad args, DB error) — a
        # downstream email-send failure is a different, async failure mode
        # entirely: it happens inside process_notification_delivery
        # (Celery), well after create_notification has already returned
        # successfully, so it can never reach this call site's except block.
        flag = self._make_flag()

        res = self.client.post(URL, {"target_type": "flag", "target_id": str(flag.id), "action": "block"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        flag.refresh_from_db()
        self.assertEqual(flag.status, "ACTIONED")
        self.assertTrue(
            AuditLog.objects.filter(actor_id=self.admin.id, action="email.moderation_staff_action.failed").exists()
        )


@override_settings(SECURE_SSL_REDIRECT=False)
class StaffChannelModerationRecordActionNotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(phone="+237670004003", password="TestPass123!", country="CM", is_staff=True)
        self.owner = User.objects.create_user(
            phone="+237670004004", password="TestPass123!", country="CM", email="channelowner@example.com",
        )
        self.commenter = User.objects.create_user(
            phone="+237670004005", password="TestPass123!", country="CM", email="commenter@example.com",
        )
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="staff-mod-test-channel", display_name="Staff Mod Test Channel",
        )
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Test video",
        )
        self.comment = ChannelContentComment.objects.create(content=self.content, user=self.commenter, body="hi")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _make_record(self, **overrides):
        defaults = dict(channel=self.channel, target_type=ChannelModerationRecord.TargetType.CONTENT, target_id=self.content.id, content=self.content)
        defaults.update(overrides)
        return ChannelModerationRecord.objects.create(**defaults)

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_blocking_content_notifies_the_channel_owner_with_email(self, mock_send):
        record = self._make_record()

        res = self.client.post(URL, {"target_type": "channel_moderation_record", "target_id": str(record.id), "action": "block"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        notif = Notification.objects.get(user_id=self.owner.id, type="MODERATION_STAFF_ACTION")
        channels = set(notif.deliveries.values_list("channel", flat=True))
        self.assertIn("EMAIL", channels)
        mock_send.assert_called_once()

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_blocking_a_comment_notifies_the_commenter_not_the_channel_owner(self, mock_send):
        record = self._make_record(
            target_type=ChannelModerationRecord.TargetType.COMMENT, target_id=self.comment.id,
            content=None, comment=self.comment,
        )

        res = self.client.post(URL, {"target_type": "channel_moderation_record", "target_id": str(record.id), "action": "block"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(Notification.objects.filter(user_id=self.commenter.id, type="MODERATION_STAFF_ACTION").exists())
        self.assertFalse(Notification.objects.filter(user_id=self.owner.id, type="MODERATION_STAFF_ACTION").exists())

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_approving_content_does_not_notify(self, mock_send):
        record = self._make_record()

        res = self.client.post(URL, {"target_type": "channel_moderation_record", "target_id": str(record.id), "action": "approve"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        mock_send.assert_not_called()
        self.assertFalse(Notification.objects.filter(user_id=self.owner.id, type="MODERATION_STAFF_ACTION").exists())

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_escalating_content_does_not_notify_yet(self, mock_send):
        record = self._make_record()

        res = self.client.post(URL, {"target_type": "channel_moderation_record", "target_id": str(record.id), "action": "escalate"}, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        mock_send.assert_not_called()
