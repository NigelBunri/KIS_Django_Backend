"""
AccountReactivationView — cancels a pending grace-period deletion
(see apps.accounts.views.schedule_account_deletion). Deliberately does its
own phone+password check rather than reusing LoginSerializer, which
explicitly rejects is_active=False users - exactly the state a
scheduled-for-deletion account is in.

Run:
  python3 manage.py test apps.accounts.test_account_reactivation --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import GDPRRequest

User = get_user_model()

DELETE_URL = "/api/v1/auth/account/delete-request/"
REACTIVATE_URL = "/api/v1/auth/account/reactivate/"


class AccountReactivationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+237670001234", password="CorrectPass123!", country="CM",
        )

    def _schedule_deletion(self):
        response = self.client.post(
            DELETE_URL,
            {"phone": "+237670001234", "password": "CorrectPass123!", "confirm": "DELETE"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()

    def test_correct_credentials_within_grace_period_reactivates_the_account(self):
        self._schedule_deletion()

        response = self.client.post(
            REACTIVATE_URL,
            {"phone": "+237670001234", "password": "CorrectPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertFalse(self.user.is_deleted)
        gdpr_request = GDPRRequest.objects.get(user=self.user, type="account_deletion")
        self.assertEqual(gdpr_request.status, "cancelled")

    def test_wrong_password_does_not_reactivate(self):
        self._schedule_deletion()

        response = self.client.post(
            REACTIVATE_URL,
            {"phone": "+237670001234", "password": "WrongPassword!"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_reactivation_fails_once_grace_period_has_expired(self):
        self._schedule_deletion()
        pending = GDPRRequest.objects.get(user=self.user, type="account_deletion", status="pending")
        pending.scheduled_for = timezone.now() - datetime.timedelta(seconds=1)
        pending.save(update_fields=["scheduled_for"])

        response = self.client.post(
            REACTIVATE_URL,
            {"phone": "+237670001234", "password": "CorrectPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_reactivation_with_no_pending_deletion_fails(self):
        response = self.client.post(
            REACTIVATE_URL,
            {"phone": "+237670001234", "password": "CorrectPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_reactivated_user_can_log_in_again(self):
        self._schedule_deletion()
        self.client.post(
            REACTIVATE_URL,
            {"phone": "+237670001234", "password": "CorrectPass123!"},
            format="json",
        )

        response = self.client.post(
            "/api/v1/auth/login/",
            {"phone": "+237670001234", "password": "CorrectPass123!", "device_id": "reactivation-test-device"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
