from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.commerce.models import Shop
from apps.partners.models import Partner, PartnerMembership, PartnerMembershipStatus


class ShopPartnerOwnershipTests(APITestCase):
    """Regression coverage for connecting a Shop to a Partner organization -
    the shop previously had no Partner relationship at all. A partner
    manager must get the same manage rights as the shop owner once
    attached, and must lose them again on detach, without ever affecting
    the real owner's own access."""

    def setUp(self):
        User = get_user_model()
        self.shop_owner = User.objects.create_user(
            phone='5559820001', username='cpo_shop_owner', password='secret', country='NG',
        )
        self.partner_manager = User.objects.create_user(
            phone='5559820002', username='cpo_partner_manager', password='secret', country='NG',
        )
        self.stranger = User.objects.create_user(
            phone='5559820003', username='cpo_stranger', password='secret', country='NG',
        )
        self.shop = Shop.objects.create(owner=self.shop_owner, name='Market Stall', slug='market-stall-cpo')
        self.partner = Partner.objects.create(
            owner=self.shop_owner, name='Market Group', slug='market-group-cpo',
        )
        PartnerMembership.objects.create(
            partner=self.partner, user=self.partner_manager,
            status=PartnerMembershipStatus.MEMBER, role='manager',
        )

    def _detail_url(self):
        return f'/api/v1/commerce/shops/{self.shop.id}/'

    def _partner_connect_url(self):
        return f'/api/v1/commerce/shops/{self.shop.id}/partner/'

    def test_partner_manager_cannot_edit_shop_before_attach(self):
        self.client.force_authenticate(self.partner_manager)
        response = self.client.patch(self._detail_url(), {'description': 'hijack'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_shop_owner_can_attach_to_a_partner_they_manage(self):
        self.client.force_authenticate(self.shop_owner)
        response = self.client.post(self._partner_connect_url(), {'partner_id': str(self.partner.id)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.partner_id, self.partner.id)

    def test_attach_requires_manage_rights_on_the_partner_too(self):
        unrelated_partner = Partner.objects.create(owner=self.stranger, name='Unrelated Partner', slug='unrelated-cpo')
        self.client.force_authenticate(self.shop_owner)
        response = self.client.post(self._partner_connect_url(), {'partner_id': str(unrelated_partner.id)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.partner_id)

    def test_attach_requires_manage_rights_on_the_shop_too(self):
        self.client.force_authenticate(self.partner_manager)
        response = self.client.post(self._partner_connect_url(), {'partner_id': str(self.partner.id)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_partner_manager_can_edit_shop_after_attach_owner_unaffected(self):
        self.shop.partner = self.partner
        self.shop.save(update_fields=['partner'])

        self.client.force_authenticate(self.partner_manager)
        response = self.client.patch(self._detail_url(), {'description': 'updated by partner manager'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.description, 'updated by partner manager')

        self.client.force_authenticate(self.shop_owner)
        owner_response = self.client.patch(self._detail_url(), {'description': 'updated by owner'}, format='json')
        self.assertEqual(owner_response.status_code, status.HTTP_200_OK, owner_response.data)

    def test_cannot_write_partner_field_via_plain_patch(self):
        # partner must only ever change through ShopPartnerConnectView -
        # ShopSerializer uses fields='__all__' so 'partner' must be locked
        # down explicitly in read_only_fields, or this would silently let
        # any shop owner self-attach to an arbitrary partner id.
        other_partner = Partner.objects.create(owner=self.stranger, name='Sneaky Partner', slug='sneaky-cpo')
        self.client.force_authenticate(self.shop_owner)
        response = self.client.patch(self._detail_url(), {'partner': str(other_partner.id)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.partner_id)

    def test_detach_removes_partner_manager_access_owner_unaffected(self):
        self.shop.partner = self.partner
        self.shop.save(update_fields=['partner'])

        self.client.force_authenticate(self.partner_manager)
        response = self.client.delete(self._partner_connect_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.partner_id)

        forbidden = self.client.patch(self._detail_url(), {'description': 'no longer allowed'}, format='json')
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN, forbidden.data)

        self.client.force_authenticate(self.shop_owner)
        still_ok = self.client.patch(self._detail_url(), {'description': 'owner still fine'}, format='json')
        self.assertEqual(still_ok.status_code, status.HTTP_200_OK, still_ok.data)
