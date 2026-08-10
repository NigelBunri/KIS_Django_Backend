"""
Phase 6: confirms a failed welcome email is now logged + audited instead of
vanishing via a bare `except: pass` (apps/accounts/views.py RegisterView).

Note: UserCreateSerializer does not currently accept an `email` field at
all (confirmed via inspection — 'email' is not in its declared fields), so
the welcome-email branch in RegisterView.create() is unreachable through
the real public registration endpoint today. That data-collection gap is a
separate, pre-existing issue outside this phase's scope (hardening the
SEND path, not fixing what data registration collects) — flagged in the
Phase 6 report, not fixed here. To still faithfully exercise the real
try/except code this phase changed, these tests patch
UserCreateSerializer.save to additionally set .email on the created user,
i.e. simulating what happens once/if that gap is closed, rather than
bypassing RegisterView.create() itself.

Run:
  python3 manage.py test apps.accounts.test_welcome_email_hardening --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import AuditLog, User
from .serializers import UserCreateSerializer

DEVICE_ID = "welcome-email-test-device"


def _save_with_email(email):
    original_save = UserCreateSerializer.save

    def _save(self, **kwargs):
        user = original_save(self, **kwargs)
        user.email = email
        user.save(update_fields=["email"])
        return user

    return _save


@override_settings(SECURE_SSL_REDIRECT=False, KIS_PHONE_VERIFICATION_ENABLED=False)
class WelcomeEmailFailureVisibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _register(self, phone_number: str):
        payload = {
            "phone_country_code": "+237", "phone_number": phone_number, "country": "CM",
            "password": "TestPass12!", "password2": "TestPass12!",
            "device_id": DEVICE_ID, "device_platform": "android",
        }
        return self.client.post("/api/v1/auth/register/", payload, format="json")

    @patch("apps.notifications.email_service.send_welcome_email", return_value=False)
    def test_failed_welcome_email_is_logged_and_audited_without_blocking_registration(self, _mock_send):
        with patch.object(UserCreateSerializer, "save", _save_with_email("newuser1@example.com")):
            res = self._register("699200001")

        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["success"])
        user = User.objects.get(phone_number="699200001")
        self.assertTrue(
            AuditLog.objects.filter(actor_id=user.id, action="email.welcome.failed").exists()
        )

    @patch("apps.notifications.email_service.send_welcome_email", side_effect=RuntimeError("provider down"))
    def test_welcome_email_exception_is_caught_logged_and_audited(self, _mock_send):
        with patch.object(UserCreateSerializer, "save", _save_with_email("newuser2@example.com")):
            res = self._register("699200002")

        self.assertEqual(res.status_code, 201)
        user = User.objects.get(phone_number="699200002")
        entry = AuditLog.objects.filter(actor_id=user.id, action="email.welcome.failed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("error"), "RuntimeError")

    @patch("apps.notifications.email_service.send_welcome_email", return_value=True)
    def test_successful_welcome_email_does_not_create_a_failure_audit_entry(self, _mock_send):
        with patch.object(UserCreateSerializer, "save", _save_with_email("newuser3@example.com")):
            res = self._register("699200003")

        self.assertEqual(res.status_code, 201)
        user = User.objects.get(phone_number="699200003")
        self.assertFalse(
            AuditLog.objects.filter(actor_id=user.id, action="email.welcome.failed").exists()
        )

    @patch("apps.notifications.email_service.send_welcome_email")
    def test_registration_without_an_email_never_attempts_to_send(self, mock_send):
        res = self._register("699200004")

        self.assertEqual(res.status_code, 201)
        mock_send.assert_not_called()
