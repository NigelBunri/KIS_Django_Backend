from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.models import HealthInstitution, HealthService


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
class HealthInstitutionEditTests(APITestCase):
    """HealthInstitutionDetailView was previously GET-only — the mobile
    app's own "edit institution" flow only ever wrote to a display-layer
    JSON profile blob, never this real relational row. Covers the new
    patch()."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237690820001", "hie_owner")
        self.stranger = _create_user("+237690820002", "hie_stranger")
        self.institution = HealthInstitution.objects.create(
            owner=self.owner, name="Original Name", institution_type="clinic", slug="hie-original-clinic",
        )

    def _detail_url(self):
        return reverse("health-ops-institution-detail", kwargs={"institution_id": str(self.institution.id)})

    def test_owner_can_edit_name_and_type(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            self._detail_url(),
            {"name": "Renamed Clinic", "institution_type": "hospital", "timezone": "Africa/Douala"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertEqual(self.institution.name, "Renamed Clinic")
        self.assertEqual(self.institution.institution_type, "hospital")
        self.assertEqual(self.institution.timezone, "Africa/Douala")

    def test_non_manager_cannot_edit(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.patch(self._detail_url(), {"name": "Hijacked"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.institution.refresh_from_db()
        self.assertEqual(self.institution.name, "Original Name")

    def test_invalid_institution_type_is_ignored_not_errored(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(self._detail_url(), {"institution_type": "not-a-real-type"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertEqual(self.institution.institution_type, "clinic")

    def test_can_deactivate_institution(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(self._detail_url(), {"is_active": False}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertFalse(self.institution.is_active)


@override_settings(SECURE_SSL_REDIRECT=False)
class HealthServiceDetailTests(APITestCase):
    """HealthService previously had list/create only — no way to edit or
    remove a service once created short of a direct DB write. Covers the
    new HealthServiceDetailView."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237690820011", "hsd_owner")
        self.stranger = _create_user("+237690820012", "hsd_stranger")
        self.institution = HealthInstitution.objects.create(owner=self.owner, name="Wellness Clinic", slug="hsd-wellness-clinic")
        self.service = HealthService.objects.create(
            institution=self.institution, name="General Consultation", base_cost_micro=5_000_000,
        )

    def _detail_url(self):
        return reverse("health-ops-service-detail", kwargs={"service_id": str(self.service.id)})

    def test_owner_can_update_service(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            self._detail_url(), {"name": "General Consultation (Updated)", "is_active": False}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.service.refresh_from_db()
        self.assertEqual(self.service.name, "General Consultation (Updated)")
        self.assertFalse(self.service.is_active)

    def test_stranger_gets_404_not_403_leaking_existence(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)

    def test_owner_can_delete_service(self):
        self.client.force_authenticate(self.owner)
        response = self.client.delete(self._detail_url())
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, response.data)
        self.assertFalse(HealthService.objects.filter(id=self.service.id).exists())

    def test_cannot_reassign_institution_via_payload(self):
        other_institution = HealthInstitution.objects.create(owner=self.owner, name="Other Clinic", slug="hsd-other-clinic")
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            self._detail_url(), {"institution": str(other_institution.id), "name": "Still original institution"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.service.refresh_from_db()
        self.assertEqual(self.service.institution_id, self.institution.id)
