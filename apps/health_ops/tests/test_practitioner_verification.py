from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.extended_models import HealthPractitioner, PractitionerProfessionType
from apps.health_ops.models import HealthInstitution, HealthInstitutionMembership, MembershipRole
from apps.verification.constants import VerificationBadgeCode

User = get_user_model()


def _create_user(phone: str, username: str, is_staff: bool = False):
    return User.objects.create_user(
        phone=phone,
        country="CM",
        password="pass1234",
        username=username,
        display_name=username.title(),
        phone_country_code="+237",
        phone_number=phone.replace("+237", ""),
        is_staff=is_staff,
    )


@override_settings(SECURE_SSL_REDIRECT=False)
class PractitionerSelfServiceProfileTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.practitioner_user = _create_user("+237691000001", "pv_practitioner")
        self.institution_owner = _create_user("+237691000002", "pv_inst_owner")
        self.stranger = _create_user("+237691000003", "pv_stranger")
        self.institution = HealthInstitution.objects.create(
            owner=self.institution_owner, name="Riverside Clinic",
        )

    def test_create_own_profile(self):
        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.post(
            reverse("health-practitioner-me"),
            {
                "legal_name": "Dr. Jane Doe",
                "profession_type": PractitionerProfessionType.DOCTOR,
                "specialty": "Cardiology",
                "license_number": "LIC-001",
                "registration_authority": "Cameroon Medical Board",
                "jurisdiction": "CM",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        practitioner = HealthPractitioner.objects.get(user=self.practitioner_user)
        self.assertEqual(practitioner.legal_name, "Dr. Jane Doe")

    def test_cannot_create_second_profile(self):
        HealthPractitioner.objects.create(
            user=self.practitioner_user, legal_name="Dr. Jane Doe",
            profession_type=PractitionerProfessionType.DOCTOR,
        )
        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.post(
            reverse("health-practitioner-me"),
            {"legal_name": "Dr. Jane Doe 2", "profession_type": PractitionerProfessionType.DOCTOR},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_claim_institution_without_membership(self):
        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.post(
            reverse("health-practitioner-me"),
            {
                "legal_name": "Dr. Jane Doe",
                "profession_type": PractitionerProfessionType.DOCTOR,
                "institution": str(self.institution.id),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(HealthPractitioner.objects.filter(user=self.practitioner_user).exists())

    def test_can_claim_institution_when_a_real_member(self):
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.practitioner_user,
            role=MembershipRole.STAFF, is_active=True,
        )
        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.post(
            reverse("health-practitioner-me"),
            {
                "legal_name": "Dr. Jane Doe",
                "profession_type": PractitionerProfessionType.DOCTOR,
                "institution": str(self.institution.id),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)

    def test_update_own_profile(self):
        HealthPractitioner.objects.create(
            user=self.practitioner_user, legal_name="Dr. Jane Doe",
            profession_type=PractitionerProfessionType.DOCTOR,
        )
        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.patch(
            reverse("health-practitioner-me"), {"specialty": "Neurology"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["specialty"], "Neurology")


@override_settings(SECURE_SSL_REDIRECT=False)
class PractitionerDirectoryPrivacyTests(APITestCase):
    """The patient-facing directory must never leak license_number,
    registration_authority, or raw verification evidence."""

    def setUp(self):
        self.client = APIClient()
        self.patient = _create_user("+237691000010", "pv_patient")
        self.doctor_user = _create_user("+237691000011", "pv_directory_doc")
        self.practitioner = HealthPractitioner.objects.create(
            user=self.doctor_user,
            legal_name="Dr. John Smith",
            profession_type=PractitionerProfessionType.DOCTOR,
            specialty="Pediatrics",
            license_number="SECRET-LICENSE-999",
            registration_authority="Secret Medical Board",
        )

    def test_directory_excludes_sensitive_fields(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("health-doctors"), {"specialty": "Pediatrics"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["results"]), 1)
        entry = resp.data["results"][0]
        self.assertNotIn("license_number", entry)
        self.assertNotIn("registration_authority", entry)
        self.assertNotIn("qualifications", entry)
        self.assertEqual(entry["legal_name"], "Dr. John Smith")
        self.assertIn("verification_status", entry)
        self.assertFalse(entry["verification_status"]["is_verified"])

    def test_directory_excludes_inactive_practitioners(self):
        self.practitioner.is_active = False
        self.practitioner.save(update_fields=["is_active"])
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("health-doctors"))
        self.assertEqual(resp.data["results"], [])


@override_settings(SECURE_SSL_REDIRECT=False)
class PractitionerVerificationLifecycleTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.practitioner_user = _create_user("+237691000020", "pv_lifecycle_doc")
        self.stranger = _create_user("+237691000021", "pv_lifecycle_stranger")
        self.admin = _create_user("+237691000022", "pv_lifecycle_admin", is_staff=True)
        self.practitioner = HealthPractitioner.objects.create(
            user=self.practitioner_user,
            legal_name="Dr. Amaka Obi",
            profession_type=PractitionerProfessionType.DOCTOR,
            specialty="General Practice",
            license_number="LIC-777",
        )

    def _start_url(self):
        return reverse("health-practitioner-verification-start", kwargs={"practitioner_id": self.practitioner.id})

    def _status_url(self):
        return reverse("health-practitioner-verification-status", kwargs={"practitioner_id": self.practitioner.id})

    def test_stranger_cannot_start_verification(self):
        self.client.force_authenticate(self.stranger)
        resp = self.client.post(self._start_url(), {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_admin_cannot_review(self):
        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.post(self._start_url(), {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        case_id = resp.data["case"]["id"]
        review_url = reverse(
            "health-practitioner-verification-review",
            kwargs={"practitioner_id": self.practitioner.id, "case_id": case_id},
        )
        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.post(review_url, {"action": "approve"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_full_submit_approve_badge_lifecycle(self):
        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.post(self._start_url(), {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        case_id = resp.data["case"]["id"]
        self.assertFalse(resp.data["status"]["badges"])

        review_url = reverse(
            "health-practitioner-verification-review",
            kwargs={"practitioner_id": self.practitioner.id, "case_id": case_id},
        )
        self.client.force_authenticate(self.admin)
        resp = self.client.post(review_url, {"action": "approve"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        badge_codes = {b["code"] for b in resp.data["badges"]}
        self.assertEqual(badge_codes, {VerificationBadgeCode.LICENSED_PROVIDER})

        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.get(self._status_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(b["code"] == VerificationBadgeCode.LICENSED_PROVIDER for b in resp.data["badges"]))

        directory_resp = self.client.get(reverse("health-doctors"))
        entry = next(e for e in directory_resp.data["results"] if e["id"] == str(self.practitioner.id))
        self.assertTrue(entry["verification_status"]["is_verified"])

    def test_rejected_case_never_issues_badge(self):
        self.client.force_authenticate(self.practitioner_user)
        resp = self.client.post(self._start_url(), {}, format="json")
        case_id = resp.data["case"]["id"]
        review_url = reverse(
            "health-practitioner-verification-review",
            kwargs={"practitioner_id": self.practitioner.id, "case_id": case_id},
        )
        self.client.force_authenticate(self.admin)
        resp = self.client.post(review_url, {"action": "reject", "notes": "Expired license"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["badges"], [])
        status_resp = self.client.get(self._status_url())
        self.assertEqual(status_resp.data["badges"], [])
