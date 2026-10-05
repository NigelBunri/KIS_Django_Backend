from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.extended_models import (
    Allergy,
    ClinicalRecordSource,
    ClinicalReferral,
    Condition,
    Encounter,
    EncounterStatus,
    HealthPractitioner,
    Immunization,
    LabOrder,
    LabOrderStatus,
    LabTestCatalogItem,
    PractitionerProfessionType,
    ReferralStatus,
)
from apps.health_ops.models import (
    HealthInstitution,
    HealthInstitutionMembership,
    HealthService,
    MembershipRole,
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
        user=user, legal_name=legal_name, profession_type=PractitionerProfessionType.DOCTOR,
        institution=institution,
    )
    case = start_practitioner_verification_case(practitioner=practitioner, actor=user)
    review_practitioner_case(case=case, actor=user, action="approve")
    return practitioner


@override_settings(SECURE_SSL_REDIRECT=False)
class EncounterTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.institution_owner = _create_user("+237695000001", "enc_owner")
        self.doctor_user = _create_user("+237695000002", "enc_doctor")
        self.patient = _create_user("+237695000003", "enc_patient")
        self.stranger_doctor_user = _create_user("+237695000004", "enc_stranger_doc")
        self.institution = HealthInstitution.objects.create(owner=self.institution_owner, name="Encounter Test Clinic")
        self.service = HealthService.objects.create(institution=self.institution, name="General Consult")
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.doctor_user, role=MembershipRole.STAFF, is_active=True,
        )
        self.practitioner = _verified_practitioner(self.doctor_user, institution=self.institution)
        ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )

    def _create_url(self):
        return reverse("health-encounter-list-create")

    def _payload(self):
        return {
            "patient": str(self.patient.id),
            "institution": str(self.institution.id),
            "encounter_type": "in_person",
            "reason": "Routine checkup",
        }

    def test_unverified_practitioner_cannot_create_encounter(self):
        unverified_user = _create_user("+237695000005", "enc_unverified")
        HealthPractitioner.objects.create(
            user=unverified_user, legal_name="Dr. Unverified", profession_type=PractitionerProfessionType.DOCTOR,
            institution=self.institution,
        )
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=unverified_user, role=MembershipRole.STAFF, is_active=True,
        )
        self.client.force_authenticate(unverified_user)
        resp = self.client.post(self._create_url(), self._payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_verified_non_member_cannot_create_encounter(self):
        outside_practitioner_user = _create_user("+237695000006", "enc_outside_doc")
        _verified_practitioner(outside_practitioner_user, institution=None, legal_name="Dr. Outside")
        self.client.force_authenticate(outside_practitioner_user)
        resp = self.client.post(self._create_url(), self._payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_create_encounter_for_patient_with_no_institution_contact(self):
        other_patient = _create_user("+237695000007", "enc_other_patient")
        self.client.force_authenticate(self.doctor_user)
        payload = self._payload()
        payload["patient"] = str(other_patient.id)
        resp = self.client.post(self._create_url(), payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_valid_encounter_creation_and_lifecycle(self):
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(self._create_url(), self._payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        encounter_id = resp.data["id"]
        self.assertEqual(resp.data["status"], EncounterStatus.SCHEDULED)

        def transition(action):
            return self.client.post(
                reverse("health-encounter-transition", kwargs={"encounter_id": encounter_id, "action": action}),
                {}, format="json",
            )

        resp = transition("start")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.data["status"], EncounterStatus.IN_PROGRESS)

        patch_resp = self.client.patch(
            reverse("health-encounter-detail", kwargs={"encounter_id": encounter_id}),
            {"notes": "Patient reports mild headache.", "assessment": "Tension headache"},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK, patch_resp.content)
        self.assertEqual(patch_resp.data["assessment"], "Tension headache")

        resp = transition("complete")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        resp = transition("close")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], EncounterStatus.CLOSED)

        # Closed encounters are immutable.
        patch_resp = self.client.patch(
            reverse("health-encounter-detail", kwargs={"encounter_id": encounter_id}),
            {"notes": "Trying to edit after close"},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_transition_rejected(self):
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(self._create_url(), self._payload(), format="json")
        encounter_id = resp.data["id"]
        resp = self.client.post(
            reverse("health-encounter-transition", kwargs={"encounter_id": encounter_id, "action": "complete"}),
            {}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stranger_practitioner_cannot_transition_or_view(self):
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(self._create_url(), self._payload(), format="json")
        encounter_id = resp.data["id"]

        _verified_practitioner(self.stranger_doctor_user, institution=None, legal_name="Dr. Stranger")
        self.client.force_authenticate(self.stranger_doctor_user)
        resp = self.client.get(reverse("health-encounter-detail", kwargs={"encounter_id": encounter_id}))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = self.client.post(
            reverse("health-encounter-transition", kwargs={"encounter_id": encounter_id, "action": "start"}),
            {}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_patient_can_view_own_encounter(self):
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(self._create_url(), self._payload(), format="json")
        encounter_id = resp.data["id"]
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("health-encounter-detail", kwargs={"encounter_id": encounter_id}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


@override_settings(SECURE_SSL_REDIRECT=False)
class ReferralTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        owner = _create_user("+237695100001", "ref_owner")
        self.referring_doctor_user = _create_user("+237695100002", "ref_referring_doc")
        self.receiving_doctor_user = _create_user("+237695100003", "ref_receiving_doc")
        self.patient = _create_user("+237695100004", "ref_patient")
        self.stranger_user = _create_user("+237695100005", "ref_stranger")

        self.institution = HealthInstitution.objects.create(owner=owner, name="Referral Test Clinic")
        self.service = HealthService.objects.create(institution=self.institution, name="Consult")
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.referring_doctor_user, role=MembershipRole.STAFF, is_active=True,
        )
        self.referring_practitioner = _verified_practitioner(
            self.referring_doctor_user, institution=self.institution, legal_name="Dr. Referrer",
        )
        self.receiving_practitioner = _verified_practitioner(
            self.receiving_doctor_user, institution=None, legal_name="Dr. Receiver",
        )
        ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )
        self.encounter = Encounter.objects.create(
            patient=self.patient, practitioner=self.referring_practitioner, institution=self.institution,
            status=EncounterStatus.IN_PROGRESS,
        )

    def _create_referral(self):
        self.client.force_authenticate(self.referring_doctor_user)
        return self.client.post(
            reverse("health-referral-list-create"),
            {
                "encounter": str(self.encounter.id),
                "receiving_practitioner": str(self.receiving_practitioner.id),
                "reason": "Needs cardiology follow-up",
                "priority": "urgent",
            },
            format="json",
        )

    def test_create_referral_success(self):
        resp = self._create_referral()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertEqual(resp.data["status"], ReferralStatus.PENDING)

    def test_non_owning_practitioner_cannot_create_referral(self):
        self.client.force_authenticate(self.receiving_doctor_user)
        resp = self.client.post(
            reverse("health-referral-list-create"),
            {"encounter": str(self.encounter.id), "receiving_practitioner": str(self.receiving_practitioner.id), "reason": "x"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_stranger_cannot_view_referral(self):
        resp = self._create_referral()
        referral_id = resp.data["id"]
        self.client.force_authenticate(self.stranger_user)
        resp = self.client.get(reverse("health-referral-detail", kwargs={"referral_id": referral_id}))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_referring_practitioner_cannot_accept_own_referral(self):
        resp = self._create_referral()
        referral_id = resp.data["id"]
        resp = self.client.post(
            reverse("health-referral-action", kwargs={"referral_id": referral_id, "action": "accept"}),
            {}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_full_accept_complete_lifecycle(self):
        resp = self._create_referral()
        referral_id = resp.data["id"]

        self.client.force_authenticate(self.receiving_doctor_user)
        resp = self.client.post(
            reverse("health-referral-action", kwargs={"referral_id": referral_id, "action": "accept"}),
            {}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.data["status"], ReferralStatus.ACCEPTED)

        resp = self.client.post(
            reverse("health-referral-action", kwargs={"referral_id": referral_id, "action": "complete"}),
            {}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], ReferralStatus.COMPLETED)

    def test_cancel_only_by_referring_practitioner(self):
        resp = self._create_referral()
        referral_id = resp.data["id"]

        self.client.force_authenticate(self.receiving_doctor_user)
        resp = self.client.post(
            reverse("health-referral-action", kwargs={"referral_id": referral_id, "action": "cancel"}),
            {}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.referring_doctor_user)
        resp = self.client.post(
            reverse("health-referral-action", kwargs={"referral_id": referral_id, "action": "cancel"}),
            {}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], ReferralStatus.CANCELLED)


@override_settings(SECURE_SSL_REDIRECT=False)
class LaboratoryTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        owner = _create_user("+237695200001", "lab_owner")
        self.doctor_user = _create_user("+237695200002", "lab_doctor")
        self.lab_tech_user = _create_user("+237695200003", "lab_tech")
        self.patient = _create_user("+237695200004", "lab_patient")
        self.stranger_user = _create_user("+237695200005", "lab_stranger")

        self.institution = HealthInstitution.objects.create(owner=owner, name="Lab Test Clinic")
        self.service = HealthService.objects.create(institution=self.institution, name="Consult")
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.doctor_user, role=MembershipRole.STAFF, is_active=True,
        )
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.lab_tech_user, role=MembershipRole.STAFF, is_active=True,
        )
        self.practitioner = _verified_practitioner(self.doctor_user, institution=self.institution, legal_name="Dr. Lab Orderer")
        self.lab_tech_practitioner = _verified_practitioner(
            self.lab_tech_user, institution=self.institution, legal_name="Lab Tech One",
        )
        ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )
        self.encounter = Encounter.objects.create(
            patient=self.patient, practitioner=self.practitioner, institution=self.institution,
            status=EncounterStatus.IN_PROGRESS,
        )
        self.test_item = LabTestCatalogItem.objects.create(
            institution=self.institution, code="CBC", name="Complete Blood Count", unit="cells/uL",
        )

    def _order_labs(self):
        self.client.force_authenticate(self.doctor_user)
        return self.client.post(
            reverse("health-lab-order-list-create"),
            {"encounter": str(self.encounter.id), "test": str(self.test_item.id), "clinical_notes": "rule out anemia"},
            format="json",
        )

    def test_order_creation_requires_owning_practitioner(self):
        stranger_doctor = _create_user("+237695200006", "lab_stranger_doc")
        _verified_practitioner(stranger_doctor, institution=None, legal_name="Dr. Stranger")
        self.client.force_authenticate(stranger_doctor)
        resp = self.client.post(
            reverse("health-lab-order-list-create"),
            {"encounter": str(self.encounter.id), "test": str(self.test_item.id)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_full_lab_lifecycle_and_patient_cannot_see_unverified_result(self):
        resp = self._order_labs()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        order_id = resp.data["id"]

        self.client.force_authenticate(self.lab_tech_user)
        resp = self.client.post(
            reverse("health-lab-order-collect", kwargs={"order_id": order_id}),
            {"specimen_type": "blood", "barcode": "BC123"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertEqual(resp.data["status"], LabOrderStatus.SPECIMEN_COLLECTED)

        resp = self.client.post(
            reverse("health-lab-order-enter-result", kwargs={"order_id": order_id}),
            {"value": "13.5", "unit": "g/dL", "flag": "normal"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.data["status"], LabOrderStatus.RESULT_PENDING_VERIFICATION)

        # Patient cannot see the result while pending verification.
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("health-lab-order-detail", kwargs={"order_id": order_id}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data["result"])

        # The same lab tech who entered cannot also verify (four-eyes).
        self.client.force_authenticate(self.lab_tech_user)
        resp = self.client.post(reverse("health-lab-order-verify-result", kwargs={"order_id": order_id}), {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # A different institution member can verify.
        second_verifier = _create_user("+237695200007", "lab_verifier")
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=second_verifier, role=MembershipRole.STAFF, is_active=True,
        )
        _verified_practitioner(second_verifier, institution=self.institution, legal_name="Dr. Verifier")
        self.client.force_authenticate(second_verifier)
        resp = self.client.post(reverse("health-lab-order-verify-result", kwargs={"order_id": order_id}), {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.data["status"], LabOrderStatus.RESULTED)

        # Now the patient can see it, and delivered_at gets stamped.
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("health-lab-order-detail", kwargs={"order_id": order_id}))
        self.assertIsNotNone(resp.data["result"])
        self.assertEqual(resp.data["result"]["value"], "13.5")

    def test_unverified_institution_member_cannot_enter_or_verify_result(self):
        receptionist = _create_user("+237695200008", "lab_receptionist")
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=receptionist, role=MembershipRole.STAFF, is_active=True,
        )
        resp = self._order_labs()
        order_id = resp.data["id"]
        self.client.force_authenticate(receptionist)
        resp = self.client.post(
            reverse("health-lab-order-enter-result", kwargs={"order_id": order_id}), {"value": "x"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_stranger_cannot_collect_specimen_or_enter_result(self):
        resp = self._order_labs()
        order_id = resp.data["id"]
        self.client.force_authenticate(self.stranger_user)
        resp = self.client.post(
            reverse("health-lab-order-collect", kwargs={"order_id": order_id}), {}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = self.client.post(
            reverse("health-lab-order-enter-result", kwargs={"order_id": order_id}), {"value": "x"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


@override_settings(SECURE_SSL_REDIRECT=False)
class ClinicianAuthoredRecordTests(APITestCase):
    """The clinician-write path is entirely separate from the patient's own
    self-service Condition/Allergy/Immunization endpoints (see
    test_patient_clinical_record.py) — this suite only covers the
    encounter-anchored clinician path."""

    def setUp(self):
        self.client = APIClient()
        owner = _create_user("+237695300001", "car_owner")
        self.doctor_user = _create_user("+237695300002", "car_doctor")
        self.patient = _create_user("+237695300003", "car_patient")
        self.stranger_doctor_user = _create_user("+237695300004", "car_stranger_doc")

        self.institution = HealthInstitution.objects.create(owner=owner, name="Clinician Record Test Clinic")
        self.service = HealthService.objects.create(institution=self.institution, name="Consult")
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.doctor_user, role=MembershipRole.STAFF, is_active=True,
        )
        self.practitioner = _verified_practitioner(self.doctor_user, institution=self.institution, legal_name="Dr. Recorder")
        ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )
        self.encounter = Encounter.objects.create(
            patient=self.patient, practitioner=self.practitioner, institution=self.institution,
            status=EncounterStatus.IN_PROGRESS,
        )

    def test_practitioner_can_record_condition_during_open_encounter(self):
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(
            reverse("health-encounter-conditions", kwargs={"encounter_id": self.encounter.id}),
            {"name": "Type 2 Diabetes", "icd_code": "E11"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertEqual(resp.data["source"], ClinicalRecordSource.CLINICIAN_RECORDED)
        condition = Condition.objects.get(id=resp.data["id"])
        self.assertEqual(condition.recorded_by_id, self.practitioner.id)
        self.assertEqual(condition.patient_id, self.patient.id)

    def test_practitioner_can_record_allergy_and_immunization(self):
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(
            reverse("health-encounter-allergies", kwargs={"encounter_id": self.encounter.id}),
            {"allergen": "Latex", "severity": "severe"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertEqual(resp.data["source"], ClinicalRecordSource.CLINICIAN_RECORDED)

        resp = self.client.post(
            reverse("health-encounter-immunizations", kwargs={"encounter_id": self.encounter.id}),
            {"vaccine_name": "Tetanus", "dose_number": 1}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        immunization = Immunization.objects.get(id=resp.data["id"])
        self.assertEqual(immunization.administered_by_id, self.practitioner.id)

    def test_patient_cannot_write_through_clinician_path(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("health-encounter-conditions", kwargs={"encounter_id": self.encounter.id}),
            {"name": "Self-forged condition"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_stranger_practitioner_cannot_write(self):
        _verified_practitioner(self.stranger_doctor_user, institution=None, legal_name="Dr. Stranger")
        self.client.force_authenticate(self.stranger_doctor_user)
        resp = self.client.post(
            reverse("health-encounter-allergies", kwargs={"encounter_id": self.encounter.id}),
            {"allergen": "Peanuts"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_write_before_encounter_starts(self):
        scheduled_encounter = Encounter.objects.create(
            patient=self.patient, practitioner=self.practitioner, institution=self.institution,
            status=EncounterStatus.SCHEDULED,
        )
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(
            reverse("health-encounter-conditions", kwargs={"encounter_id": scheduled_encounter.id}),
            {"name": "Too early"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_write_after_encounter_closed(self):
        self.encounter.status = EncounterStatus.CLOSED
        self.encounter.save(update_fields=["status"])
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.post(
            reverse("health-encounter-conditions", kwargs={"encounter_id": self.encounter.id}),
            {"name": "Too late"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_practitioner_view_includes_self_reported_entries_too(self):
        Allergy.objects.create(patient=self.patient, allergen="Self-reported shellfish", source=ClinicalRecordSource.SELF_REPORTED)
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.get(reverse("health-encounter-allergies", kwargs={"encounter_id": self.encounter.id}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        allergens = {row["allergen"] for row in resp.data["results"]}
        self.assertIn("Self-reported shellfish", allergens)
