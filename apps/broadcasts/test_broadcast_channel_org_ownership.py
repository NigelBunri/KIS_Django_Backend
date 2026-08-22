from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.broadcasts.models import BroadcastChannel, EducationInstitution
from apps.commerce.models import Shop
from apps.health_ops.models import HealthInstitution
from apps.partners.models import Partner


CHANNEL_CREATE_URL = '/api/v1/broadcasts/channels/'


class BroadcastChannelOrgOwnershipTests(APITestCase):
    """Regression coverage for unblocking BroadcastChannel.OwnerType.SHOP/
    HEALTH/EDUCATION/PARTNER creation - previously the create endpoint
    hard-rejected anything but owner_type=user with "Organization channel
    creation will be connected in a later phase." even though the model's
    OwnerType enum and owner_id field already supported it."""

    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(
            phone='5559830001', username='bco_manager', password='secret', country='NG',
        )
        self.stranger = User.objects.create_user(
            phone='5559830002', username='bco_stranger', password='secret', country='NG',
        )
        self.shop = Shop.objects.create(owner=self.manager, name='Org Shop', slug='org-shop-bco')
        self.health_institution = HealthInstitution.objects.create(owner=self.manager, name='Org Clinic')
        self.education_institution = EducationInstitution.objects.create(owner=self.manager, name='Org Academy')
        self.partner = Partner.objects.create(owner=self.manager, name='Org Partner', slug='org-partner-bco')

    def _create_payload(self, owner_type: str, owner_id: str, handle: str):
        return {
            'owner_type': owner_type,
            'owner_id': owner_id,
            'handle': handle,
            'display_name': f'{owner_type} channel',
        }

    def test_authorized_manager_can_create_shop_channel(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            CHANNEL_CREATE_URL, self._create_payload('shop', str(self.shop.id), 'bco-shop-channel'), format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['owner_type'], 'shop')
        self.assertEqual(response.data['owner_id'], str(self.shop.id))
        channel = BroadcastChannel.objects.get(handle='bco-shop-channel')
        self.assertEqual(channel.owner_type, 'shop')
        self.assertIsNone(channel.owner_user_id)

    def test_authorized_manager_can_create_health_channel(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            CHANNEL_CREATE_URL,
            self._create_payload('health', str(self.health_institution.id), 'bco-health-channel'),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['owner_type'], 'health')

    def test_authorized_manager_can_create_education_channel(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            CHANNEL_CREATE_URL,
            self._create_payload('education', str(self.education_institution.id), 'bco-education-channel'),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['owner_type'], 'education')

    def test_authorized_manager_can_create_partner_channel(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            CHANNEL_CREATE_URL,
            self._create_payload('partner', str(self.partner.id), 'bco-partner-channel'),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['owner_type'], 'partner')
        self.assertEqual(response.data['owner_id'], str(self.partner.id))

    def test_unauthorized_user_cannot_create_shop_channel(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            CHANNEL_CREATE_URL, self._create_payload('shop', str(self.shop.id), 'bco-shop-channel-2'), format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_unauthorized_user_cannot_create_partner_channel(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            CHANNEL_CREATE_URL, self._create_payload('partner', str(self.partner.id), 'bco-partner-channel-2'), format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_nonexistent_owner_id_is_404(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            CHANNEL_CREATE_URL,
            self._create_payload('shop', '00000000-0000-0000-0000-000000000000', 'bco-missing-shop'),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)

    def test_unsupported_owner_type_is_rejected(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            CHANNEL_CREATE_URL,
            self._create_payload('galaxy', str(self.shop.id), 'bco-bad-owner-type'),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

    def test_creator_can_manage_org_channel_immediately(self):
        self.client.force_authenticate(self.manager)
        create_response = self.client.post(
            CHANNEL_CREATE_URL, self._create_payload('shop', str(self.shop.id), 'bco-manage-channel'), format='json',
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        handle = create_response.data['handle']

        patch_response = self.client.patch(
            f'/api/v1/broadcasts/channels/{handle}/', {'description': 'updated'}, format='json',
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK, patch_response.data)

    def test_org_channel_owner_id_is_public_but_user_channel_owner_id_is_not(self):
        self.client.force_authenticate(self.manager)
        org_response = self.client.post(
            CHANNEL_CREATE_URL, self._create_payload('shop', str(self.shop.id), 'bco-visible-owner'), format='json',
        )
        self.assertIn('owner_id', org_response.data)
        self.assertEqual(org_response.data['owner_id'], str(self.shop.id))

        user_channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.manager.id,
            owner_user=self.manager,
            handle='bco-user-owned',
            display_name='Personal channel',
        )
        self.client.logout()
        get_response = self.client.get(f'/api/v1/broadcasts/channels/{user_channel.handle}/')
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        self.assertNotIn('owner_id', get_response.data)
