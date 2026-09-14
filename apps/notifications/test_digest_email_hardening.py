"""
Phase 6: confirms compile_and_send_digests now logs a digest email failure
instead of silently ignoring send_notification_email's return value.

Email-system audit, Priority 2 (row 13, "Wired, minor bugs"), adds
coverage for two more real bugs in this same task:
- NotificationDigest.sent_at was never written, even on a successful
  send — every digest looked eternally unsent.
- The email's HTML body was a "\\n".join(...) plain-text blob passed
  straight into the generic "default" template's single <p>{body}</p>
  — HTML collapses literal newlines, so every digest rendered as one
  run-on line instead of a list. Fixed via a dedicated "digest"
  template + send_digest_email building a real <ul>.

Run:
  python3 manage.py test apps.notifications.test_digest_email_hardening --keepdb -v 2
"""
import logging
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.notifications import email_service
from apps.notifications.models import Notification, NotificationDigest
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

    @patch("apps.notifications.email_service.send_notification_email", return_value=True)
    def test_sent_at_is_now_recorded_on_a_successful_send(self, _mock_send):
        compile_and_send_digests(self.start.isoformat(), self.end.isoformat())

        digest = NotificationDigest.objects.get(user_id=self.user.id)
        self.assertIsNotNone(digest.sent_at)

    @patch("apps.notifications.email_service.send_notification_email", return_value=False)
    def test_sent_at_stays_null_when_the_send_fails(self, _mock_send):
        compile_and_send_digests(self.start.isoformat(), self.end.isoformat())

        digest = NotificationDigest.objects.get(user_id=self.user.id)
        self.assertIsNone(digest.sent_at)


class SendDigestEmailTemplateTests(TestCase):
    def test_multiple_items_render_as_a_real_list_not_a_run_on_line(self):
        sent = email_service.send_digest_email(
            to_email="user@example.com",
            items=[
                {"title": "First update", "summary": "Something happened."},
                {"title": "Second update", "summary": "Something else happened."},
            ],
        )

        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertEqual(html_body.count("<li>"), 2)
        self.assertIn("First update", html_body)
        self.assertIn("Second update", html_body)
        self.assertIn("2 update(s)", mail.outbox[0].subject)

    def test_item_title_and_summary_are_html_escaped(self):
        email_service.send_digest_email(
            to_email="user@example.com",
            items=[{"title": "<script>alert(1)</script>", "summary": "<b>hi</b>"}],
        )

        html_body = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<script>", html_body)
        self.assertNotIn("<b>hi</b>", html_body)

    def test_empty_items_still_sends_a_valid_email(self):
        sent = email_service.send_digest_email(to_email="user@example.com", items=[])

        self.assertTrue(sent)
        self.assertIn("0 update(s)", mail.outbox[0].subject)
