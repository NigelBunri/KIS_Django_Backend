from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.hospital_proximity import (
    HospitalProximityResult,
    NullHospitalProximityProvider,
    get_hospital_proximity_provider,
    is_hospital_proximity_configured,
)

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


class HospitalProximityDefaultProviderTests(APITestCase):
    def test_default_provider_is_null_and_unconfigured(self):
        self.assertIsInstance(get_hospital_proximity_provider(), NullHospitalProximityProvider)
        self.assertFalse(is_hospital_proximity_configured())

    def test_null_provider_returns_no_fabricated_results(self):
        self.assertEqual(NullHospitalProximityProvider().find_nearby(1.0, 2.0), [])

    def test_sos_alert_reports_no_hospitals_and_unconfigured_flag_honestly(self):
        user = _create_user("+237600000401", "sos_patient_1")
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("emergency-sos"),
            {"latitude": 4.05, "longitude": 9.70, "message": "test"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["nearest_hospitals"], [])
        self.assertFalse(response.data["hospital_lookup_available"])
        # Must never fall back to a fake-looking placeholder entry.
        for hospital in response.data["nearest_hospitals"]:
            self.assertNotIn("scaffold", str(hospital).lower())


class _StubHospitalProvider:
    def find_nearby(self, latitude, longitude, limit: int = 5):
        return [
            HospitalProximityResult(
                name="Test General Hospital",
                distance_km=1.2,
                address="1 Test Ave",
                phone="+237600000000",
                latitude=latitude,
                longitude=longitude,
            )
        ]


@override_settings(
    HEALTH_HOSPITAL_PROXIMITY_PROVIDER=f"{__name__}._StubHospitalProvider",
)
class HospitalProximityConfiguredProviderTests(APITestCase):
    def test_sos_alert_uses_configured_provider_when_set(self):
        user = _create_user("+237600000402", "sos_patient_2")
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("emergency-sos"),
            {"latitude": 4.05, "longitude": 9.70, "message": "test"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["hospital_lookup_available"])
        self.assertEqual(len(response.data["nearest_hospitals"]), 1)
        self.assertEqual(response.data["nearest_hospitals"][0]["name"], "Test General Hospital")
