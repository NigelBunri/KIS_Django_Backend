"""
PublicAccountDeletionRequestView — Google Play / Apple review both require a
way to request account and data deletion that doesn't depend on having the
app installed. Password-gated (reuses LoginSerializer's own credential
check) so an unauthenticated request naming an arbitrary phone number can't
delete someone else's account.

Deletion is a grace-period soft-delete (see apps.accounts.views.
schedule_account_deletion), not an immediate hard delete: the row still
exists right after this call, just deactivated, with a pending GDPRRequest
scheduled for ACCOUNT_DELETION_GRACE_DAYS later. Reactivation is covered in
test_account_reactivation.py; the actual purge in test_account_purge.py.

Run:
  python3 manage.py test apps.accounts.test_public_account_deletion --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import GDPRRequest

User = get_user_model()

URL = "/api/v1/auth/account/delete-request/"


class PublicAccountDeletionRequestTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+237670001234", password="CorrectPass123!", country="CM",
        )

    def test_correct_credentials_and_confirmation_schedules_the_account_for_deletion(self):
        response = self.client.post(
            URL,
            {"phone": "+237670001234", "password": "CorrectPass123!", "confirm": "DELETE"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("scheduled_for", response.data)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertTrue(self.user.is_deleted)
        self.assertTrue(
            GDPRRequest.objects.filter(
                user=self.user, type="account_deletion", status="pending",
            ).exists()
        )

    def test_confirm_is_case_insensitive(self):
        response = self.client.post(
            URL,
            {"phone": "+237670001234", "password": "CorrectPass123!", "confirm": "delete"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_missing_confirmation_does_not_deactivate_the_account(self):
        response = self.client.post(
            URL,
            {"phone": "+237670001234", "password": "CorrectPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertFalse(GDPRRequest.objects.filter(user=self.user).exists())

    def test_wrong_password_does_not_deactivate_the_account(self):
        response = self.client.post(
            URL,
            {"phone": "+237670001234", "password": "WrongPassword!", "confirm": "DELETE"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_nonexistent_phone_gets_the_same_generic_error_as_wrong_password(self):
        wrong_password_response = self.client.post(
            URL,
            {"phone": "+237670001234", "password": "WrongPassword!", "confirm": "DELETE"},
            format="json",
        )
        nonexistent_phone_response = self.client.post(
            URL,
            {"phone": "+237699999999", "password": "WrongPassword!", "confirm": "DELETE"},
            format="json",
        )

        # No account enumeration: both failure modes must look identical.
        self.assertEqual(wrong_password_response.status_code, nonexistent_phone_response.status_code)
        self.assertEqual(wrong_password_response.data, nonexistent_phone_response.data)

    def test_a_different_users_account_is_unaffected(self):
        other_user = User.objects.create_user(
            phone="+237670005678", password="OtherPass123!", country="CM",
        )

        response = self.client.post(
            URL,
            {"phone": "+237670001234", "password": "CorrectPass123!", "confirm": "DELETE"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        other_user.refresh_from_db()
        self.assertTrue(other_user.is_active)
