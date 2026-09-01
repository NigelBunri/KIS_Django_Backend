from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.models import HealthInstitution


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


@override_settings(SECURE_SSL_REDIRECT=False)
class HealthDiscoveryApiTests(APITestCase):
    """HealthInstitution had no public-visibility concept before KISTube's
    Health section needed a browse endpoint. Covers HealthDiscoveryView,
    which must only ever surface institutions with is_public=True and must
    never leak owner/payout fields via HealthInstitutionPublicSerializer."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237690830001", "hd_owner")
        self.public_clinic = HealthInstitution.objects.create(
            owner=self.owner, name="Open Clinic", institution_type="clinic",
            slug="hd-open-clinic", is_public=True, is_active=True,
        )
        self.private_clinic = HealthInstitution.objects.create(
            owner=self.owner, name="Private Clinic", institution_type="clinic",
            slug="hd-private-clinic", is_public=False, is_active=True,
        )
        self.public_inactive_hospital = HealthInstitution.objects.create(
            owner=self.owner, name="Closed Hospital", institution_type="hospital",
            slug="hd-closed-hospital", is_public=True, is_active=False,
        )

    def _url(self, **params):
        url = reverse("health-ops-discovery")
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"
        return url

    def test_anonymous_can_browse(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_only_public_and_active_institutions_are_listed(self):
        response = self.client.get(self._url())
        names = {row["name"] for row in response.data["results"]}
        self.assertIn("Open Clinic", names)
        self.assertNotIn("Private Clinic", names)
        self.assertNotIn("Closed Hospital", names)

    def test_public_serializer_excludes_owner_and_payout_fields(self):
        response = self.client.get(self._url())
        row = next(row for row in response.data["results"] if row["name"] == "Open Clinic")
        self.assertNotIn("owner", row)
        self.assertNotIn("payout_account_status", row)
        self.assertNotIn("payout_bank_last4", row)
        self.assertNotIn("settings", row)
        self.assertEqual(row["slug"], "hd-open-clinic")

    def test_search_filters_by_name(self):
        response = self.client.get(self._url(q="Open"))
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"Open Clinic"})

    def test_type_filter(self):
        HealthInstitution.objects.create(
            owner=self.owner, name="Open Pharmacy", institution_type="pharmacy",
            slug="hd-open-pharmacy", is_public=True, is_active=True,
        )
        response = self.client.get(self._url(type="pharmacy"))
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"Open Pharmacy"})

    def test_empty_when_no_public_institutions(self):
        HealthInstitution.objects.all().update(is_public=False)
        response = self.client.get(self._url())
        self.assertEqual(response.data["results"], [])
        self.assertIsNone(response.data["next_cursor"])
