from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.extended_models import (
    Allergy,
    AllergySeverity,
    ClinicalRecordSource,
    Condition,
    ConditionStatus,
    Immunization,
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


@override_settings(SECURE_SSL_REDIRECT=False)
class ConditionAllergyImmunizationTests(APITestCase):
    """Self-service patient clinical record (Condition/Allergy/Immunization).
    Each is strictly scoped to the authenticated patient — no cross-patient
    visibility, and `source`/`recorded_by`/`administered_by` can never be
    set by the client (always server-controlled)."""

    def setUp(self):
        self.client = APIClient()
        self.patient_a = _create_user("+237694000001", "pcr_patient_a")
        self.patient_b = _create_user("+237694000002", "pcr_patient_b")

    def test_create_and_list_own_condition(self):
        self.client.force_authenticate(self.patient_a)
        resp = self.client.post(
            reverse("conditions-list"),
            {"name": "Hypertension", "status": ConditionStatus.ACTIVE},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertEqual(resp.data["source"], ClinicalRecordSource.SELF_REPORTED)
        self.assertIsNone(resp.data["recorded_by"])

        resp = self.client.get(reverse("conditions-list"))
        self.assertEqual(len(resp.data["results"] if "results" in resp.data else resp.data), 1)

    def test_cannot_see_other_patients_condition(self):
        Condition.objects.create(patient=self.patient_b, name="Diabetes")
        self.client.force_authenticate(self.patient_a)
        resp = self.client.get(reverse("conditions-list"))
        rows = resp.data["results"] if "results" in resp.data else resp.data
        self.assertEqual(len(rows), 0)

    def test_source_field_is_server_controlled(self):
        self.client.force_authenticate(self.patient_a)
        resp = self.client.post(
            reverse("conditions-list"),
            {"name": "Asthma", "source": ClinicalRecordSource.CLINICIAN_RECORDED},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["source"], ClinicalRecordSource.SELF_REPORTED)
        condition = Condition.objects.get(id=resp.data["id"])
        self.assertEqual(condition.source, ClinicalRecordSource.SELF_REPORTED)

    def test_allergy_create_and_isolation(self):
        self.client.force_authenticate(self.patient_a)
        resp = self.client.post(
            reverse("allergies-list"),
            {"allergen": "Penicillin", "severity": AllergySeverity.SEVERE, "reaction": "Hives"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        Allergy.objects.create(patient=self.patient_b, allergen="Peanuts")
        resp = self.client.get(reverse("allergies-list"))
        rows = resp.data["results"] if "results" in resp.data else resp.data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["allergen"], "Penicillin")

    def test_immunization_create_and_isolation(self):
        self.client.force_authenticate(self.patient_a)
        resp = self.client.post(
            reverse("immunizations-list"),
            {"vaccine_name": "Yellow Fever", "dose_number": 1},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        Immunization.objects.create(patient=self.patient_b, vaccine_name="Tetanus")
        resp = self.client.get(reverse("immunizations-list"))
        rows = resp.data["results"] if "results" in resp.data else resp.data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vaccine_name"], "Yellow Fever")

    def test_cannot_update_another_patients_condition(self):
        condition = Condition.objects.create(patient=self.patient_b, name="Diabetes")
        self.client.force_authenticate(self.patient_a)
        url = reverse("conditions-detail", kwargs={"pk": condition.id})
        resp = self.client.patch(url, {"name": "Hijacked"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        condition.refresh_from_db()
        self.assertEqual(condition.name, "Diabetes")

    def test_unauthenticated_cannot_access(self):
        resp = self.client.get(reverse("conditions-list"))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
