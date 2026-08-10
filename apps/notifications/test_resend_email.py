"""
Tests for Phase 6's Resend email integration: the ResendEmailBackend
itself, and the verify_email_launch guardrail command.

Run:
  python3 manage.py test apps.notifications.test_resend_email --keepdb -v 2
"""
import json
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.core.mail import EmailMultiAlternatives
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.notifications.resend_backend import RESEND_API_URL, ResendAPIError, ResendEmailBackend


def _message(**overrides) -> EmailMultiAlternatives:
    defaults = dict(
        subject="Hello", body="Plain text body",
        from_email="KIS <no-reply@kis.app>", to=["user@example.com"],
    )
    defaults.update(overrides)
    return EmailMultiAlternatives(**defaults)


class ResendEmailBackendTests(SimpleTestCase):
    def test_missing_api_key_raises_when_not_fail_silently(self):
        backend = ResendEmailBackend(api_key="")
        with self.assertRaises(ResendAPIError):
            backend.send_messages([_message()])

    def test_missing_api_key_returns_zero_when_fail_silently(self):
        backend = ResendEmailBackend(api_key="", fail_silently=True)
        self.assertEqual(backend.send_messages([_message()]), 0)

    def test_empty_message_list_sends_nothing(self):
        backend = ResendEmailBackend(api_key="test-key")
        self.assertEqual(backend.send_messages([]), 0)

    @patch("apps.notifications.resend_backend.requests.post")
    def test_successful_send_posts_expected_payload(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="{}")
        backend = ResendEmailBackend(api_key="test-key")
        msg = _message()
        msg.attach_alternative("<p>HTML body</p>", "text/html")

        sent = backend.send_messages([msg])

        self.assertEqual(sent, 1)
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["from"], "KIS <no-reply@kis.app>")
        self.assertEqual(kwargs["json"]["to"], ["user@example.com"])
        self.assertEqual(kwargs["json"]["subject"], "Hello")
        self.assertEqual(kwargs["json"]["text"], "Plain text body")
        self.assertEqual(kwargs["json"]["html"], "<p>HTML body</p>")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(mock_post.call_args.args[0], RESEND_API_URL)

    @patch("apps.notifications.resend_backend.requests.post")
    def test_message_without_html_alternative_omits_html_key(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="{}")
        backend = ResendEmailBackend(api_key="test-key")

        backend.send_messages([_message()])

        _, kwargs = mock_post.call_args
        self.assertNotIn("html", kwargs["json"])

    @patch("apps.notifications.resend_backend.requests.post")
    def test_provider_error_response_raises_and_is_not_fail_silent_by_default(self, mock_post):
        mock_post.return_value = MagicMock(status_code=422, text='{"message": "invalid from address"}')
        backend = ResendEmailBackend(api_key="test-key")

        with self.assertRaises(ResendAPIError):
            backend.send_messages([_message()])

    @patch("apps.notifications.resend_backend.requests.post")
    def test_provider_error_is_swallowed_when_fail_silently(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text="server error")
        backend = ResendEmailBackend(api_key="test-key", fail_silently=True)

        sent = backend.send_messages([_message()])

        self.assertEqual(sent, 0)

    @patch("apps.notifications.resend_backend.requests.post")
    def test_network_failure_raises_resend_api_error_not_the_raw_requests_exception(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("boom")
        backend = ResendEmailBackend(api_key="test-key")

        with self.assertRaises(ResendAPIError):
            backend.send_messages([_message()])

    @patch("apps.notifications.resend_backend.requests.post")
    def test_bounded_timeout_is_always_passed_to_the_http_call(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="{}")
        backend = ResendEmailBackend(api_key="test-key")

        backend.send_messages([_message()])

        _, kwargs = mock_post.call_args
        self.assertIsNotNone(kwargs.get("timeout"))
        self.assertLessEqual(kwargs["timeout"], 10)

    @patch("apps.notifications.resend_backend.requests.post")
    def test_cc_bcc_reply_to_are_forwarded_only_when_present(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="{}")
        backend = ResendEmailBackend(api_key="test-key")

        backend.send_messages([_message(cc=["cc@example.com"], reply_to=["reply@example.com"])])

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["cc"], ["cc@example.com"])
        self.assertEqual(kwargs["json"]["reply_to"], ["reply@example.com"])
        self.assertNotIn("bcc", kwargs["json"])

    def test_reads_api_key_from_django_settings_when_not_passed_explicitly(self):
        with override_settings(RESEND_API_KEY="from-settings-key"):
            backend = ResendEmailBackend()
            self.assertEqual(backend.api_key, "from-settings-key")


@override_settings(SECURE_SSL_REDIRECT=False)
class VerifyEmailLaunchCommandTests(TestCase):
    def test_local_console_backend_reports_ready_with_only_the_resend_warning(self):
        out = StringIO()
        call_command("verify_email_launch", "--json", stdout=out)
        payload = json.loads(out.getvalue())

        self.assertTrue(payload["ready"])
        resend_check = next(c for c in payload["checks"] if c["name"] == "RESEND_API_KEY")
        self.assertEqual(resend_check["state"], "warn")
        # console backend is active in tests — SMTP-specific checks must not fire
        names = [c["name"] for c in payload["checks"]]
        self.assertNotIn("EMAIL_HOST_USER", names)

    def test_smtp_backend_with_missing_credentials_fails(self):
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"), \
             patch.dict("os.environ", {"EMAIL_HOST": "", "EMAIL_HOST_USER": "", "EMAIL_HOST_PASSWORD": ""}):
            out = StringIO()
            call_command("verify_email_launch", "--json", stdout=out)
            payload = json.loads(out.getvalue())

        self.assertFalse(payload["ready"])
        host_check = next(c for c in payload["checks"] if c["name"] == "EMAIL_HOST_USER")
        self.assertEqual(host_check["state"], "fail")

    def test_smtp_backend_with_full_credentials_passes(self):
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"), \
             patch.dict("os.environ", {"EMAIL_HOST": "smtp.example.com", "EMAIL_HOST_USER": "u", "EMAIL_HOST_PASSWORD": "p"}):
            out = StringIO()
            call_command("verify_email_launch", "--json", stdout=out)
            payload = json.loads(out.getvalue())

        self.assertTrue(payload["ready"])

    def test_resend_key_present_but_wrong_backend_fails_consistency_check(self):
        with override_settings(RESEND_API_KEY="a-key", EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"):
            out = StringIO()
            call_command("verify_email_launch", "--json", stdout=out)
            payload = json.loads(out.getvalue())

        self.assertFalse(payload["ready"])
        consistency_check = next(c for c in payload["checks"] if c["name"] == "resend_backend_selected")
        self.assertEqual(consistency_check["state"], "fail")

    def test_resend_key_present_with_matching_backend_passes(self):
        with override_settings(
            RESEND_API_KEY="a-key",
            EMAIL_BACKEND="apps.notifications.resend_backend.ResendEmailBackend",
        ):
            out = StringIO()
            call_command("verify_email_launch", "--json", stdout=out)
            payload = json.loads(out.getvalue())

        self.assertTrue(payload["ready"])

    def test_placeholder_default_from_email_fails(self):
        with override_settings(DEFAULT_FROM_EMAIL="no-reply@example.com"):
            out = StringIO()
            call_command("verify_email_launch", "--json", stdout=out)
            payload = json.loads(out.getvalue())

        self.assertFalse(payload["ready"])

    def test_strict_flag_raises_when_a_blocker_is_present(self):
        with override_settings(DEFAULT_FROM_EMAIL="no-reply@example.com"):
            with self.assertRaises(CommandError):
                call_command("verify_email_launch", "--strict", stdout=StringIO())

    def test_never_prints_the_resend_api_key_value(self):
        with override_settings(RESEND_API_KEY="super-secret-resend-key-value"):
            out = StringIO()
            call_command("verify_email_launch", stdout=out)
            output = out.getvalue()

        self.assertNotIn("super-secret-resend-key-value", output)
