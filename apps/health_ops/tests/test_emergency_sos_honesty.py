from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.extended_models import EmergencyAlert

User = get_user_model()


def _create_user(phone: str, username: str):
    return User.objects.create_user(
        phone=phone,
        country="CM",
        password="pass1234",
        username=username,
        display_name=username.title(),
        phone_country_code="+237",
        phone_number=phone.replace("+237", ""),
    )


# Hospital-proximity-provider coverage (default/null provider, configured
# provider, no-fabrication guard) lives in test_hospital_proximity_boundary.py
# — not duplicated here. This file covers SOS alert persistence and the
# per-user access isolation that file doesn't exercise.
@override_settings(SECURE_SSL_REDIRECT=False)
class SOSAlertAuthorizationTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _create_user("+237690900001", "sos_test_user")
        self.other_user = _create_user("+237690900002", "sos_other_user")
        self.client.force_authenticate(self.user)
        self.url = reverse("emergency-sos")

    def test_sos_create_persists_alert_for_requesting_user(self):
        self.client.post(self.url, {"latitude": 4.05, "longitude": 9.70}, format="json")
        alert = EmergencyAlert.objects.get(user=self.user)
        self.assertEqual(alert.alert_type, EmergencyAlert.AlertType.SOS)
        self.assertEqual(alert.status, EmergencyAlert.AlertStatus.ACTIVE)

    def test_emergency_alert_list_is_scoped_to_requesting_user_only(self):
        self.client.post(self.url, {"latitude": 1.0, "longitude": 1.0}, format="json")

        self.client.force_authenticate(self.other_user)
        self.client.post(self.url, {"latitude": 2.0, "longitude": 2.0}, format="json")

        list_url = reverse("emergency-alerts-list")
        res = self.client.get(list_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("results", res.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user"], self.other_user.id)

        self.client.force_authenticate(self.user)
        res = self.client.get(list_url)
        results = res.data.get("results", res.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user"], self.user.id)

    def test_sos_create_requires_authentication(self):
        self.client.force_authenticate(None)
        res = self.client.post(self.url, {"latitude": 4.05, "longitude": 9.70}, format="json")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
