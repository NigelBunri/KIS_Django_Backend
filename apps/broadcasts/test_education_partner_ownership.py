from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.broadcasts.models import EducationInstitution
from apps.partners.models import Partner, PartnerMembership, PartnerMembershipStatus


class EducationInstitutionPartnerOwnershipTests(APITestCase):
    """Regression coverage for connecting an EducationInstitution to a
    Partner organization - the institution previously had no Partner
    relationship at all. A partner manager must get the same manage rights
    as the institution owner once attached, and must lose them again on
    detach, without ever affecting the real owner's own access.

    Also covers the specific gap found during implementation:
    _education_institution_qs_for_user (the queryset behind
    _get_education_institution_or_404) previously only scoped to
    owner-or-membership, so a partner manager who is neither would 404
    before ever reaching a permission check."""

    def setUp(self):
        User = get_user_model()
        self.institution_owner = User.objects.create_user(
            phone='5559810001', username='epo_institution_owner', password='secret', country='NG',
        )
        self.partner_manager = User.objects.create_user(
            phone='5559810002', username='epo_partner_manager', password='secret', country='NG',
        )
        self.stranger = User.objects.create_user(
            phone='5559810003', username='epo_stranger', password='secret', country='NG',
        )
        self.institution = EducationInstitution.objects.create(
            owner=self.institution_owner, name='Riverside Academy',
        )
        self.partner = Partner.objects.create(
            owner=self.institution_owner, name='Riverside Group', slug='riverside-group-epo',
        )
        PartnerMembership.objects.create(
            partner=self.partner, user=self.partner_manager,
            status=PartnerMembershipStatus.MEMBER, role='manager',
        )

    def _detail_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/'

    def _partner_connect_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/partner/'

    def test_partner_manager_gets_404_before_attach(self):
        # Regression test for the queryset gap: a partner manager who is
        # neither the owner nor a membership row must 404 (not 403) before
        # attach, exactly like a total stranger would - _get_education_institution_or_404
        # scopes the queryset itself, so an unattached institution is
        # invisible, not just unmanageable.
        self.client.force_authenticate(self.partner_manager)
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)

    def test_institution_owner_can_attach_to_a_partner_they_manage(self):
        self.client.force_authenticate(self.institution_owner)
        response = self.client.post(self._partner_connect_url(), {'partner_id': str(self.partner.id)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertEqual(self.institution.partner_id, self.partner.id)

    def test_attach_requires_manage_rights_on_the_partner_too(self):
        unrelated_partner = Partner.objects.create(owner=self.stranger, name='Unrelated Partner', slug='unrelated-epo')
        self.client.force_authenticate(self.institution_owner)
        response = self.client.post(self._partner_connect_url(), {'partner_id': str(unrelated_partner.id)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.institution.refresh_from_db()
        self.assertIsNone(self.institution.partner_id)

    def test_attach_requires_manage_rights_on_the_institution_too(self):
        # partner_manager manages self.partner but has no rights on the
        # institution itself (not owner, no membership) - must 404, since
        # _get_education_institution_or_404 (used by the connect view too)
        # can't resolve the institution for them at all before attach.
        self.client.force_authenticate(self.partner_manager)
        response = self.client.post(self._partner_connect_url(), {'partner_id': str(self.partner.id)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)

    def test_partner_manager_can_manage_after_attach_owner_unaffected(self):
        self.institution.partner = self.partner
        self.institution.save(update_fields=['partner'])

        self.client.force_authenticate(self.partner_manager)
        get_response = self.client.get(self._detail_url())
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        self.assertTrue(get_response.data['institution']['can_manage'])

        self.client.force_authenticate(self.institution_owner)
        owner_response = self.client.get(self._detail_url())
        self.assertEqual(owner_response.status_code, status.HTTP_200_OK, owner_response.data)
        self.assertTrue(owner_response.data['institution']['can_manage'])

    def test_detach_removes_partner_manager_access_owner_unaffected(self):
        self.institution.partner = self.partner
        self.institution.save(update_fields=['partner'])

        self.client.force_authenticate(self.partner_manager)
        response = self.client.delete(self._partner_connect_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertIsNone(self.institution.partner_id)

        gone = self.client.get(self._detail_url())
        self.assertEqual(gone.status_code, status.HTTP_404_NOT_FOUND, gone.data)

        self.client.force_authenticate(self.institution_owner)
        still_ok = self.client.get(self._detail_url())
        self.assertEqual(still_ok.status_code, status.HTTP_200_OK, still_ok.data)
