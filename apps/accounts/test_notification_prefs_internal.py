import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.chat.internal_signing import sign_internal_request

from .models import ProfilePreferences

URL = "/api/v1/profile-preferences/internal/notification-prefs/"


def _signed_internal_headers(method: str, path: str, body=None, secret: str = "real-token"):
    headers = sign_internal_request(method, path, body, secret=secret)
    return {f"HTTP_{key.upper().replace('-', '_')}": value for key, value in headers.items()}


class NotificationPreferencesInternalViewTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670005001", password="TestPass123!", country="CM")
        # A ProfilePreferences row may already exist for a freshly created
        # user (auto-provisioned elsewhere) — update_or_create avoids a
        # duplicate-key error on the user OneToOneField either way.
        ProfilePreferences.objects.update_or_create(
            user=self.user,
            defaults={
                "notification_preferences": {
                    "notif_calls": False,
                    "notif_messages": True,
                    "dnd_quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"},
                },
            },
        )

    def test_missing_internal_token_is_rejected(self):
        res = self.client.get(URL, {"user_id": str(self.user.id)})
        self.assertEqual(res.status_code, 401)

    def test_invalid_internal_token_is_rejected(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(
                URL, {"user_id": str(self.user.id)}, HTTP_X_INTERNAL_AUTH="wrong-token",
            )
        self.assertEqual(res.status_code, 401)

    def test_no_django_user_session_is_required(self):
        # The whole point: Nest is calling as a trusted service, not as the
        # user whose prefs are being read — this must succeed with zero
        # user authentication on this request.
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(
                URL, {"user_id": str(self.user.id)}, HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.json()["notification_preferences"],
            {
                "notif_calls": False,
                "notif_messages": True,
                "dnd_quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"},
            },
        )

    def test_requires_user_id(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(URL, {}, HTTP_X_INTERNAL_AUTH="real-token")
        self.assertEqual(res.status_code, 400)

    def test_accepts_user_id_via_header_instead_of_query_param(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(
                URL, {}, HTTP_X_INTERNAL_AUTH="real-token", HTTP_X_INTERNAL_USER_ID=str(self.user.id),
            )
        self.assertEqual(res.status_code, 200)

    def test_unknown_user_returns_empty_preferences_not_an_error(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(
                URL, {"user_id": "00000000-0000-0000-0000-000000000000"}, HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["notification_preferences"], {})

    def test_production_mode_rejects_a_token_only_request_without_a_signature(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "1"}):
            res = self.client.get(
                URL, {"user_id": str(self.user.id)}, HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 401)

    def test_production_mode_accepts_a_correctly_signed_request(self):
        path = f"{URL}?user_id={self.user.id}"
        headers = _signed_internal_headers("GET", path)
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "1"}):
            res = self.client.get(path, **headers)
        self.assertEqual(res.status_code, 200)
