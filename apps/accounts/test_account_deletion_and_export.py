"""
AccountDeletionView (authenticated) and the widened DataExportView.

AccountDeletionView now schedules a grace-period deletion (see
apps.accounts.views.schedule_account_deletion) instead of deleting
immediately - same behavior change as the public/logged-out path, covered
separately in test_public_account_deletion.py.

Run:
  python3 manage.py test apps.accounts.test_account_deletion_and_export --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Device, GDPRRequest

User = get_user_model()

DELETE_URL = "/api/v1/auth/account/"
EXPORT_URL = "/api/v1/auth/data-export/"


class AuthenticatedAccountDeletionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+237670009999", password="CorrectPass123!", country="CM",
        )
        self.client.force_authenticate(self.user)

    def test_correct_password_schedules_deletion_without_deleting_the_row(self):
        response = self.client.delete(DELETE_URL, {"password": "CorrectPass123!"}, format="json")

        self.assertEqual(response.status_code, 202)
        self.assertIn("scheduled_for", response.data)
        self.user.refresh_from_db()
        self.assertTrue(User.objects.filter(id=self.user.id).exists())
        self.assertFalse(self.user.is_active)
        self.assertTrue(
            GDPRRequest.objects.filter(user=self.user, type="account_deletion", status="pending").exists()
        )

    def test_wrong_password_does_not_schedule_deletion(self):
        response = self.client.delete(DELETE_URL, {"password": "WrongPassword!"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertFalse(GDPRRequest.objects.filter(user=self.user).exists())

    def test_all_active_devices_are_revoked_on_scheduled_deletion(self):
        device = Device.objects.create(
            user=self.user, device_id="dev-1", platform="ios",
        )

        self.client.delete(DELETE_URL, {"password": "CorrectPass123!"}, format="json")

        device.refresh_from_db()
        self.assertIsNotNone(device.revoked_at)


class DataExportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+237670008888", password="CorrectPass123!", country="CM",
        )
        self.client.force_authenticate(self.user)

    def test_export_includes_testimonies_and_broadcasts_keys(self):
        response = self.client.get(EXPORT_URL)

        self.assertEqual(response.status_code, 200)
        self.assertIn("testimonies", response.data)
        self.assertIn("broadcasts", response.data)
        self.assertEqual(response.data["testimonies"], [])
        self.assertEqual(response.data["broadcasts"], [])

    def test_export_includes_the_users_own_testimony(self):
        from apps.testimony.models import UserTestimony

        UserTestimony.objects.create(
            user=self.user, category="faith", title="My story", story="...",
        )

        response = self.client.get(EXPORT_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["testimonies"]), 1)
        self.assertEqual(response.data["testimonies"][0]["title"], "My story")

    def test_export_does_not_include_another_users_testimony(self):
        from apps.testimony.models import UserTestimony

        other_user = User.objects.create_user(
            phone="+237670007777", password="Whatever123!", country="CM",
        )
        UserTestimony.objects.create(
            user=other_user, category="faith", title="Not mine", story="...",
        )

        response = self.client.get(EXPORT_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["testimonies"], [])
