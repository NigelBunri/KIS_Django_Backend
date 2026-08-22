from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.models import HealthInstitution
from apps.partners.models import Partner, PartnerMembership, PartnerMembershipStatus


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
class HealthInstitutionPartnerOwnershipTests(APITestCase):
    """Regression coverage for connecting a HealthInstitution to a Partner
    organization - the institution previously had no Partner relationship
    at all. A partner manager must get the same manage rights as the
    institution owner once attached, and must lose them again on detach,
    without ever affecting the real owner's own access."""

    def setUp(self):
        self.client = APIClient()
        self.institution_owner = _create_user("+237690810001", "hpo_institution_owner")
        self.partner_manager = _create_user("+237690810002", "hpo_partner_manager")
        self.stranger = _create_user("+237690810003", "hpo_stranger")

        self.institution = HealthInstitution.objects.create(
            owner=self.institution_owner, name="Sunrise Clinic",
        )
        # Owned by institution_owner (so they can freely attach their own
        # institution to it), with partner_manager holding a real
        # manager-tier PartnerMembership (not ownership) - this is the
        # more realistic shape of "a partner org manager who isn't the
        # partner's own owner".
        self.partner = Partner.objects.create(
            owner=self.institution_owner, name="Sunrise Group", slug="sunrise-group-hpo",
        )
        PartnerMembership.objects.create(
            partner=self.partner, user=self.partner_manager,
            status=PartnerMembershipStatus.MEMBER, role="manager",
        )

    def _detail_url(self):
        return reverse("health-ops-institution-detail", kwargs={"institution_id": str(self.institution.id)})

    def _partner_connect_url(self):
        return reverse("health-ops-institution-partner-connect", kwargs={"institution_id": str(self.institution.id)})

    def test_partner_manager_cannot_manage_before_attach(self):
        self.client.force_authenticate(self.partner_manager)
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_institution_owner_can_attach_to_a_partner_they_manage(self):
        self.client.force_authenticate(self.institution_owner)
        response = self.client.post(self._partner_connect_url(), {"partner_id": str(self.partner.id)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertEqual(self.institution.partner_id, self.partner.id)

    def test_attach_requires_manage_rights_on_the_partner_too(self):
        # institution_owner can manage the institution but has no rights at
        # all on this unrelated partner - the connect must fail, not just
        # silently trust institution-side permission.
        unrelated_partner = Partner.objects.create(owner=self.stranger, name="Unrelated Partner", slug="unrelated-hpo")
        self.client.force_authenticate(self.institution_owner)
        response = self.client.post(self._partner_connect_url(), {"partner_id": str(unrelated_partner.id)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.institution.refresh_from_db()
        self.assertIsNone(self.institution.partner_id)

    def test_attach_requires_manage_rights_on_the_institution_too(self):
        # partner_manager manages the target partner but has no rights on
        # this institution - must not be able to self-attach it.
        self.client.force_authenticate(self.partner_manager)
        response = self.client.post(self._partner_connect_url(), {"partner_id": str(self.partner.id)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_partner_manager_can_manage_after_attach_owner_unaffected(self):
        self.institution.partner = self.partner
        self.institution.save(update_fields=["partner"])

        self.client.force_authenticate(self.partner_manager)
        get_response = self.client.get(self._detail_url())
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        self.assertTrue(get_response.data["institution"]["can_manage"])

        self.client.force_authenticate(self.institution_owner)
        owner_response = self.client.get(self._detail_url())
        self.assertEqual(owner_response.status_code, status.HTTP_200_OK, owner_response.data)
        self.assertTrue(owner_response.data["institution"]["can_manage"])

    def test_low_privilege_partner_member_can_view_but_not_manage(self):
        # partner_ids_user_can_access (view/visibility) intentionally
        # includes any active PartnerMembership regardless of role, while
        # partner_user_can_manage (the actual write gate) only counts
        # owner/admin/manager roles - a low-privilege "member" role should
        # therefore be able to see this institution (matching how any
        # regular institution member can already view it today) but must
        # not be reported as able to manage it.
        self.institution.partner = self.partner
        self.institution.save(update_fields=["partner"])
        PartnerMembership.objects.create(
            partner=self.partner, user=self.stranger,
            status=PartnerMembershipStatus.MEMBER, role="member",
        )
        self.client.force_authenticate(self.stranger)
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(response.data["institution"]["can_manage"])

    def test_detach_removes_partner_manager_access_owner_unaffected(self):
        self.institution.partner = self.partner
        self.institution.save(update_fields=["partner"])

        self.client.force_authenticate(self.partner_manager)
        response = self.client.delete(self._partner_connect_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertIsNone(self.institution.partner_id)

        forbidden = self.client.get(self._detail_url())
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN, forbidden.data)

        self.client.force_authenticate(self.institution_owner)
        still_ok = self.client.get(self._detail_url())
        self.assertEqual(still_ok.status_code, status.HTTP_200_OK, still_ok.data)
