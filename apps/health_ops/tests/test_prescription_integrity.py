from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.extended_models import EMedication, Encounter, EncounterStatus, HealthPractitioner, PractitionerProfessionType
from apps.health_ops.models import (
    EngineRegistry,
    EngineSession,
    HealthInstitution,
    HealthInstitutionMembership,
    HealthService,
    MembershipRole,
    PharmacyFulfillmentSession,
    PharmacyFulfillmentStatus,
    ServiceEngineMap,
    ServiceWorkflowSession,
    WorkflowStatus,
)
from apps.verification.services import review_practitioner_case, start_practitioner_verification_case

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


def _verified_practitioner(user, institution=None, legal_name="Dr. Test"):
    practitioner = HealthPractitioner.objects.create(
        user=user, legal_name=legal_name, profession_type=PractitionerProfessionType.DOCTOR, institution=institution,
    )
    case = start_practitioner_verification_case(practitioner=practitioner, actor=user)
    review_practitioner_case(case=case, actor=user, action="approve")
    return practitioner


@override_settings(SECURE_SSL_REDIRECT=False)
class EncounterPrescriptionWriteTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        owner = _create_user("+237696000001", "rx_owner")
        self.doctor_user = _create_user("+237696000002", "rx_doctor")
        self.patient = _create_user("+237696000003", "rx_patient")
        self.stranger_doctor_user = _create_user("+237696000004", "rx_stranger_doc")

        self.institution = HealthInstitution.objects.create(owner=owner, name="Prescription Test Clinic")
        self.service = HealthService.objects.create(institution=self.institution, name="Consult")
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.doctor_user, role=MembershipRole.STAFF, is_active=True,
        )
        self.practitioner = _verified_practitioner(self.doctor_user, institution=self.institution, legal_name="Dr. Prescriber")
        ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )
        self.encounter = Encounter.objects.create(
            patient=self.patient, practitioner=self.practitioner, institution=self.institution,
            status=EncounterStatus.IN_PROGRESS,
        )

    def test_practitioner_can_prescribe_during_open_encounter(self):
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(
            reverse("health-encounter-prescriptions", kwargs={"encounter_id": self.encounter.id}),
            {"name": "Amoxicillin", "dosage": "500mg", "frequency": "3x daily"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertTrue(resp.data["is_verified_prescription"])
        prescription = EMedication.objects.get(id=resp.data["id"])
        self.assertEqual(prescription.prescribing_practitioner_id, self.practitioner.id)
        self.assertTrue(prescription.is_verified_prescription)

    def test_patient_cannot_prescribe_to_self(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("health-encounter-prescriptions", kwargs={"encounter_id": self.encounter.id}),
            {"name": "Self-prescribed", "dosage": "999mg", "frequency": "always"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_stranger_practitioner_cannot_prescribe(self):
        _verified_practitioner(self.stranger_doctor_user, institution=None, legal_name="Dr. Stranger")
        self.client.force_authenticate(self.stranger_doctor_user)
        resp = self.client.post(
            reverse("health-encounter-prescriptions", kwargs={"encounter_id": self.encounter.id}),
            {"name": "Hijacked", "dosage": "1mg", "frequency": "once"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_legacy_self_tracked_entry_is_never_verified(self):
        entry = EMedication.objects.create(
            patient=self.patient, name="Vitamin D", dosage="1000IU", frequency="daily",
            prescribed_by="Some doctor I saw once",
        )
        self.assertFalse(entry.is_verified_prescription)

    def test_revoke_only_by_prescriber_or_institution_manager(self):
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(
            reverse("health-encounter-prescriptions", kwargs={"encounter_id": self.encounter.id}),
            {"name": "Amoxicillin", "dosage": "500mg", "frequency": "3x daily"}, format="json",
        )
        prescription_id = resp.data["id"]

        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("health-prescription-revoke", kwargs={"prescription_id": prescription_id}), {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(reverse("health-prescription-revoke", kwargs={"prescription_id": prescription_id}), {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertTrue(resp.data["is_revoked"])

        entry = EMedication.objects.get(id=prescription_id)
        self.assertFalse(entry.is_verified_prescription)


@override_settings(SECURE_SSL_REDIRECT=False)
class PharmacyVerifyPrescriptionStepTests(APITestCase):
    """Regression coverage for the fix: completing the pharmacy workflow's
    verify_prescription step previously trusted an arbitrary client-supplied
    payload with no server-side check — a patient could self-certify their
    own prescription as verified. Now it requires (a) institution staff,
    not the patient, and (b) a real, unrevoked EMedication prescription."""

    def setUp(self):
        self.client = APIClient()
        owner = _create_user("+237696100001", "pharm_owner")
        self.doctor_user = _create_user("+237696100002", "pharm_doctor")
        self.pharmacist_user = _create_user("+237696100003", "pharm_pharmacist")
        self.patient = _create_user("+237696100004", "pharm_patient")

        self.institution = HealthInstitution.objects.create(owner=owner, name="Pharmacy Test Clinic")
        self.service = HealthService.objects.create(institution=self.institution, name="Pharmacy")
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.doctor_user, role=MembershipRole.STAFF, is_active=True,
        )
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.pharmacist_user, role=MembershipRole.STAFF, is_active=True,
        )
        self.practitioner = _verified_practitioner(self.doctor_user, institution=self.institution, legal_name="Dr. Prescriber")

        self.workflow = ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )
        self.encounter = Encounter.objects.create(
            patient=self.patient, practitioner=self.practitioner, institution=self.institution,
            status=EncounterStatus.IN_PROGRESS,
        )
        self.prescription = EMedication.objects.create(
            patient=self.patient, prescribing_practitioner=self.practitioner, encounter=self.encounter,
            name="Amoxicillin", dosage="500mg", frequency="3x daily",
        )

        engine = EngineRegistry.objects.create(code="pharmacy", name="Pharmacy", category="pharmacy")
        engine_map = ServiceEngineMap.objects.create(service=self.service, engine=engine, execution_order=1)
        engine_session = EngineSession.objects.create(workflow_session=self.workflow, engine_map=engine_map, user=self.patient)
        self.pharmacy_session = PharmacyFulfillmentSession.objects.create(
            workflow_session=self.workflow, engine_session=engine_session, institution=self.institution,
            service=self.service, user=self.patient,
        )

    def _step_url(self):
        return reverse("health-ops-pharmacy-session-step", kwargs={"pharmacy_session_id": self.pharmacy_session.id})

    def test_patient_cannot_self_verify_prescription(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.patch(
            self._step_url(),
            {"step_key": "verify_prescription", "is_completed": True, "payload": {"prescription_id": str(self.prescription.id)}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.pharmacy_session.refresh_from_db()
        self.assertEqual(self.pharmacy_session.status, PharmacyFulfillmentStatus.WAITING)

    def test_cannot_verify_without_a_real_prescription_reference(self):
        self.client.force_authenticate(self.pharmacist_user)
        resp = self.client.patch(
            self._step_url(),
            {"step_key": "verify_prescription", "is_completed": True, "payload": {}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_verify_with_a_revoked_prescription(self):
        self.prescription.is_revoked = True
        self.prescription.save(update_fields=["is_revoked"])
        self.client.force_authenticate(self.pharmacist_user)
        resp = self.client.patch(
            self._step_url(),
            {"step_key": "verify_prescription", "is_completed": True, "payload": {"prescription_id": str(self.prescription.id)}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_verify_with_another_patients_prescription(self):
        other_patient = _create_user("+237696100005", "pharm_other_patient")
        other_prescription = EMedication.objects.create(
            patient=other_patient, prescribing_practitioner=self.practitioner,
            name="Someone else's drug", dosage="1mg", frequency="once",
        )
        self.client.force_authenticate(self.pharmacist_user)
        resp = self.client.patch(
            self._step_url(),
            {"step_key": "verify_prescription", "is_completed": True, "payload": {"prescription_id": str(other_prescription.id)}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_institution_staff_can_verify_with_real_prescription(self):
        self.client.force_authenticate(self.pharmacist_user)
        resp = self.client.patch(
            self._step_url(),
            {"step_key": "verify_prescription", "is_completed": True, "payload": {"prescription_id": str(self.prescription.id)}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.pharmacy_session.refresh_from_db()
        self.assertEqual(self.pharmacy_session.status, PharmacyFulfillmentStatus.VERIFYING)
