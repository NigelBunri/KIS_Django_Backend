"""
DataExportView had no test coverage before its body was extracted into
apps.accounts.views.collect_user_export_data (shared with admin_control's
AdminUserDataExportView) - this locks in that the refactor didn't change
self-service export behavior.

Run:
  python3 manage.py test apps.accounts.test_data_export --keepdb -v 2
"""
from __future__ import annotations

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User

URL = "/api/v1/auth/data-export/"


class DataExportViewTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.user = User.objects.create_user(
            phone="+237600000900", email="export-self@test.com", display_name="Self Exporter",
            password="test1234!", country="CM",
        )

    def test_requires_authentication(self):
        resp = self.client_api.get(URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_exports_only_the_authenticated_users_own_data(self):
        other = User.objects.create_user(
            phone="+237600000901", email="someone-else@test.com", password="test1234!", country="CM",
        )
        self.client_api.force_authenticate(user=self.user)
        resp = self.client_api.get(URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["user"]["id"], str(self.user.id))
        self.assertEqual(resp.data["user"]["display_name"], "Self Exporter")
        self.assertNotEqual(resp.data["user"]["id"], str(other.id))

    def test_placeholder_phone_is_never_exported(self):
        self.user.phone_is_placeholder = True
        self.user.save(update_fields=["phone_is_placeholder"])
        self.client_api.force_authenticate(user=self.user)
        resp = self.client_api.get(URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data["user"]["phone"])
