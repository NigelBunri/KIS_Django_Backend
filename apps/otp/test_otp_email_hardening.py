"""
Email-system audit, Priority 1: OTP emails now route through the single
email_service.send_otp_email() path instead of apps.otp.views' own
hand-rolled SendGrid HTTP call (deleted). Confirms each OTP purpose gets
its own branded copy instead of one generic template for every purpose,
and that no old SendGrid path remains reachable.

Run:
  python3 manage.py test apps.otp.test_otp_email_hardening --keepdb -v 2
"""
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.notifications import email_service
from apps.otp import views as otp_views

OVERRIDE_CODE = "676139"
# email_configured() checks RESEND_API_KEY or (EMAIL_HOST and
# EMAIL_HOST_USER) — neither is set in local/test settings (only
# production.py defines EMAIL_HOST/EMAIL_HOST_USER), so the email channel
# is otherwise unavailable and every request below would 400 before
# reaching the code this file actually tests.
_EMAIL_AVAILABLE = override_settings(EMAIL_HOST="smtp.example.com", EMAIL_HOST_USER="test-user")


@_EMAIL_AVAILABLE
@override_settings(SECURE_SSL_REDIRECT=False, OTP_OVERRIDE_ENABLED=True, OTP_OVERRIDE_CODE=OVERRIDE_CODE)
class OtpEmailRoutingTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _make_user(self, phone: str, email: str) -> User:
        user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
        User.objects.filter(pk=user.pk).update(email=email)
        return user

    def test_send_email_otp_no_longer_exists_on_the_otp_views_module(self):
        # Regression guard for the audit finding: OTP emails used to bypass
        # email_service entirely via apps.otp.views.send_email_otp's own
        # SendGrid HTTP call. That function must stay gone.
        self.assertFalse(hasattr(otp_views, "send_email_otp"))

    @patch("apps.otp.views.send_otp_email")
    def test_initiate_register_email_channel_uses_register_purpose(self, mock_send):
        self._make_user("+237670099001", "reguser@example.com")
        mock_send.return_value = True

        res = self.client.post("/api/v1/auth/otp/initiate/", {
            "phone": "+237670099001", "country": "CM", "purpose": "register", "channel": "email",
        }, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(args[0], "reguser@example.com")
        self.assertEqual(kwargs.get("purpose"), "register")

    @patch("apps.otp.views.send_otp_email")
    def test_initiate_login_email_channel_uses_login_purpose(self, mock_send):
        self._make_user("+237670099002", "loginuser@example.com")
        mock_send.return_value = True

        res = self.client.post("/api/v1/auth/otp/initiate/", {
            "phone": "+237670099002", "country": "CM", "purpose": "login", "channel": "email",
        }, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs.get("purpose"), "login")

    @patch("apps.otp.views.send_otp_email")
    def test_initiate_email_verify_channel_uses_email_verify_purpose(self, mock_send):
        self._make_user("+237670099003", "verifyuser@example.com")
        mock_send.return_value = True

        res = self.client.post("/api/v1/auth/otp/initiate/", {
            "phone": "+237670099003", "country": "CM", "purpose": "email_verify", "channel": "email",
        }, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(mock_send.call_args.kwargs.get("purpose"), "email_verify")

    @patch("apps.otp.views.send_otp_email")
    def test_password_reset_initiate_email_channel_uses_reset_purpose(self, mock_send):
        self._make_user("+237670099004", "resetuser@example.com")
        mock_send.return_value = True

        res = self.client.post("/api/v1/auth/password/forgot/", {
            "phone": "+237670099004", "country": "CM", "channel": "email",
        }, format="json")

        self.assertEqual(res.status_code, 200, res.data)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(args[0], "resetuser@example.com")
        self.assertEqual(kwargs.get("purpose"), "reset")

    def test_no_sendgrid_http_call_is_ever_made_for_a_real_email_send(self):
        # End-to-end through the real email_service path (send_otp_email is
        # NOT mocked here), with urllib patched to blow up if anything still
        # tries to reach an external HTTP API directly the way the deleted
        # SendGrid path used to.
        self._make_user("+237670099005", "nosg@example.com")
        with patch("urllib.request.urlopen", side_effect=AssertionError("must not call an external HTTP API directly")):
            res = self.client.post("/api/v1/auth/otp/initiate/", {
                "phone": "+237670099005", "country": "CM", "purpose": "login", "channel": "email",
            }, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("sign-in code", mail.outbox[0].subject.lower())


class SendOtpEmailTemplateTests(TestCase):
    def test_each_purpose_gets_its_own_branded_subject(self):
        seen_subjects = set()
        for purpose in ("register", "login", "web_login", "email_verify", "reset"):
            mail.outbox.clear()
            sent = email_service.send_otp_email("user@example.com", "123456", 5, purpose=purpose)
            self.assertTrue(sent, f"purpose={purpose} failed to send")
            self.assertEqual(len(mail.outbox), 1)
            msg = mail.outbox[0]
            self.assertNotIn(msg.subject, seen_subjects, f"purpose={purpose} reused another purpose's subject")
            seen_subjects.add(msg.subject)
            html_body = msg.alternatives[0][0]
            self.assertIn("123456", html_body)

    def test_reset_purpose_never_reuses_login_or_register_copy(self):
        email_service.send_otp_email("user@example.com", "111111", 5, purpose="reset")
        reset_html = mail.outbox[-1].alternatives[0][0]
        mail.outbox.clear()
        email_service.send_otp_email("user@example.com", "111111", 5, purpose="login")
        login_html = mail.outbox[-1].alternatives[0][0]

        self.assertNotEqual(reset_html, login_html)
        self.assertIn("password", reset_html.lower())
        self.assertNotIn("password", login_html.lower())

    def test_unknown_purpose_falls_back_to_generic_template_instead_of_crashing(self):
        sent = email_service.send_otp_email("user@example.com", "654321", 5, purpose="totally-unknown-purpose")
        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("654321", mail.outbox[0].subject)


class EmailConfiguredTests(TestCase):
    @override_settings(RESEND_API_KEY="a-key", EMAIL_HOST="", EMAIL_HOST_USER="")
    def test_true_when_resend_key_set_even_without_smtp_creds(self):
        self.assertTrue(otp_views.email_configured())

    @override_settings(RESEND_API_KEY="", EMAIL_HOST="smtp.example.com", EMAIL_HOST_USER="u")
    def test_true_when_smtp_host_and_user_set(self):
        self.assertTrue(otp_views.email_configured())

    @override_settings(RESEND_API_KEY="", EMAIL_HOST="", EMAIL_HOST_USER="")
    def test_false_when_nothing_configured(self):
        self.assertFalse(otp_views.email_configured())
