"""
Phase 6: confirms compile_and_send_digests now logs a digest email failure
instead of silently ignoring send_notification_email's return value.

Run:
  python3 manage.py test apps.notifications.test_digest_email_hardening --keepdb -v 2
"""
import logging
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.notifications.tasks import compile_and_send_digests


def _make_user(phone: str) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.email = f"{phone.lstrip('+')}@example.com"
    user.save(update_fields=["email"])
    return user


@override_settings(SECURE_SSL_REDIRECT=False)
class DigestEmailFailureVisibilityTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699600001")
        now = timezone.now()
        self.start = now - timezone.timedelta(hours=1)
        self.end = now + timezone.timedelta(minutes=1)
        Notification.objects.create(
            user_id=self.user.id, type="test.digest", title="Test update", body="Something happened.",
        )

    @patch("apps.notifications.email_service.send_notification_email", return_value=False)
    def test_failed_digest_email_is_logged(self, _mock_send):
        with self.assertLogs("apps.notifications.tasks", level="WARNING") as captured:
            result = compile_and_send_digests(self.start.isoformat(), self.end.isoformat())

        self.assertTrue(result)
        self.assertTrue(any("Digest email failed" in line for line in captured.output))

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_successful_digest_email_does_not_log_a_failure(self, _mock_send):
        logger = logging.getLogger("apps.notifications.tasks")
        with patch.object(logger, "warning") as mock_warning:
            compile_and_send_digests(self.start.isoformat(), self.end.isoformat())

        mock_warning.assert_not_called()
