from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.broadcasts.models import BroadcastHealthInstitution, BroadcastHealthProfile
from apps.health_ops.models import HealthInstitution, HealthInstitutionMembership, MembershipRole
from apps.health_ops.serializers import HealthInstitutionSerializer
from apps.verification.constants import VerificationBadgeCode, VerificationSubjectType
from apps.verification.models import VerificationBadge, VerificationCase
from apps.verification.services import (
    current_health_institution_verification_status,
    review_health_institution_case,
    start_health_institution_verification_case,
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
class HealthInstitutionVerificationTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237690800001", "health_verify_owner")
        self.manager = _create_user("+237690800002", "health_verify_manager")
        self.staff = _create_user("+237690800003", "health_verify_staff")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])
        self.institution = HealthInstitution.objects.create(
            owner=self.owner,
            name="Verified Care Hospital",
            slug="verified-care-hospital",
            institution_type="hospital",
            timezone="UTC",
            settings={},
            is_active=True,
        )
        HealthInstitutionMembership.objects.create(
            institution=self.institution,
            user=self.manager,
            role=MembershipRole.MANAGER,
            is_active=True,
        )

    def test_manager_can_start_health_institution_verification_with_safe_metadata(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            reverse("health-ops-institution-verification-start", kwargs={"institution_id": self.institution.id}),
            {
                "provider": "dojah",
                "evidence_metadata": {
                    "legal_registration": [{"private_media_id": "private-reg-doc", "url": "https://example.com/reg.pdf"}],
                    "medical_license": [{"private_media_id": "private-license-doc", "expires_at": "2027-12-31"}],
                    "staff_authorization": [{"private_media_id": "private-auth-doc", "role": "medical_director"}],
                },
            },
            format="json",
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        case = VerificationCase.objects.get(id=response.data["case"]["id"])
        self.assertEqual(case.subject.subject_type, VerificationSubjectType.HEALTH_INSTITUTION)
        self.assertEqual(case.evidence_metadata["legal_registration"][0]["private_media_id"], "private-reg-doc")
        self.assertNotIn("url", case.evidence_metadata["legal_registration"][0])

    def test_start_rejects_raw_health_document_payload(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            reverse("health-ops-institution-verification-start", kwargs={"institution_id": self.institution.id}),
            {"evidence_metadata": {"medical_license": [{"document_base64": "data:image/png;base64,abc123"}]}},
            format="json",
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_can_approve_health_case_and_issue_badges(self):
        case = start_health_institution_verification_case(
            institution=self.institution,
            actor=self.owner,
            evidence_metadata={"medical_license": [{"private_media_id": "private-license-doc"}]},
        )
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            reverse(
                "health-ops-institution-verification-review",
                kwargs={"institution_id": self.institution.id, "case_id": case.id},
            ),
            {"action": "approve"},
            format="json",
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        codes = set(
            VerificationBadge.objects.filter(
                subject__subject_type=VerificationSubjectType.HEALTH_INSTITUTION,
                subject__subject_id=self.institution.id,
            ).values_list("code", flat=True)
        )
        self.assertIn(VerificationBadgeCode.VERIFIED_HEALTH_INSTITUTION, codes)
        self.assertIn(VerificationBadgeCode.LICENSED_PROVIDER, codes)
        self.assertTrue(current_health_institution_verification_status(self.institution)["verified"])

    def test_owner_without_membership_gets_manage_access_in_health_ops_payload(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(
            reverse("health-ops-institution-detail", kwargs={"institution_id": self.institution.id}),
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        institution = response.data["institution"]
        self.assertEqual(institution["owner_user_id"], str(self.owner.id))
        self.assertEqual(institution["viewer"]["role"], "owner")
        self.assertTrue(institution["can_manage"])

    def test_health_institution_serializer_exposes_verification_summary(self):
        case = start_health_institution_verification_case(institution=self.institution, actor=self.owner, evidence_metadata={})
        review_health_institution_case(case=case, actor=self.staff, action="approve")

        data = HealthInstitutionSerializer(self.institution).data

        self.assertTrue(data["verification_summary"]["verified"])

    def test_broadcast_health_institution_uses_centralized_summary(self):
        profile = BroadcastHealthProfile.objects.create(profile=self.owner.profile, payload={})
        broadcast_row = BroadcastHealthInstitution.objects.create(
            health_profile=profile,
            institution_uid="broadcast-health-verify",
            institution_type="clinic",
            name="Broadcast Clinic",
            owner_user=self.owner,
        )
        case = start_health_institution_verification_case(institution=broadcast_row, actor=self.owner, evidence_metadata={})
        review_health_institution_case(case=case, actor=self.staff, action="approve")

        self.assertTrue(current_health_institution_verification_status(broadcast_row)["verified"])
