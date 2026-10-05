from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.models import (
    HealthInstitution,
    HealthInstitutionMembership,
    HealthInstitutionPayoutAccountStatus,
    MembershipRole,
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
class PayoutFieldPrivacyTests(APITestCase):
    """Payout banking details are only relevant to someone who can manage
    billing for an institution — a plain staff/member shouldn't see them
    just because they can read the institution record."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237693000001", "payout_owner")
        self.staff_member = _create_user("+237693000002", "payout_staff")
        self.institution = HealthInstitution.objects.create(
            owner=self.owner, name="Payout Test Clinic",
            payout_account_status=HealthInstitutionPayoutAccountStatus.ACTIVE,
            payout_account_name="Owner Bank Account",
            payout_bank_last4="4321",
        )
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.staff_member,
            role=MembershipRole.STAFF, is_active=True,
        )

    def _detail_url(self):
        return reverse("health-ops-institution-detail", kwargs={"institution_id": str(self.institution.id)})

    def test_owner_sees_payout_fields(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self._detail_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["institution"]["payout_account_name"], "Owner Bank Account")
        self.assertEqual(resp.data["institution"]["payout_bank_last4"], "4321")

    def test_plain_staff_does_not_see_payout_fields(self):
        self.client.force_authenticate(self.staff_member)
        resp = self.client.get(self._detail_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data["institution"]["payout_account_name"])
        self.assertIsNone(resp.data["institution"]["payout_bank_last4"])
        # Status itself is low-sensitivity and still useful to staff (e.g.
        # "is billing even connected") so it is not masked.
        self.assertEqual(
            resp.data["institution"]["payout_account_status"],
            HealthInstitutionPayoutAccountStatus.ACTIVE,
        )
