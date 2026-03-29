from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.models import (
    HealthInstitution,
    HealthInstitutionMembership,
    InstitutionEngineManagedItem,
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
class ManagedItemsApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237690700211", "managed_owner")
        self.manager = _create_user("+237690700212", "managed_manager")
        self.member = _create_user("+237690700213", "managed_member")
        self.outsider = _create_user("+237690700214", "managed_outsider")
        self.legacy_institution_id = "mt9y7ox3O7Hms2J_sWPIL"

        self.institution = HealthInstitution.objects.create(
            owner=self.owner,
            name="Managed Items Hospital",
            slug="managed-items-hospital",
            institution_type="hospital",
            timezone="UTC",
            settings={"legacy_institution_id": self.legacy_institution_id},
            is_active=True,
        )
        HealthInstitutionMembership.objects.create(
            institution=self.institution,
            user=self.manager,
            role=MembershipRole.MANAGER,
            is_active=True,
        )
        HealthInstitutionMembership.objects.create(
            institution=self.institution,
            user=self.member,
            role=MembershipRole.MEMBER,
            is_active=True,
        )

    def test_manager_can_crud_managed_item(self):
        self.client.force_authenticate(self.manager)
        list_url = reverse(
            "health-ops-institution-engine-managed-item-list-create",
            kwargs={
                "institution_id": self.institution.id,
                "engine_key": "admission-bed-management-engine",
            },
        )
        create_response = self.client.post(
            list_url,
            {
                "item_kind": "room",
                "name": "Ward A",
                "amount_micro": 250000,
                "quantity": 3,
                "status": "active",
            },
            format="json",
            secure=True,
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        item_id = str(create_response.data["item"]["id"])

        detail_url = reverse(
            "health-ops-institution-engine-managed-item-detail",
            kwargs={
                "institution_id": self.institution.id,
                "engine_key": "admission-bed-management-engine",
                "item_id": item_id,
            },
        )
        patch_response = self.client.patch(
            detail_url,
            {
                "name": "Ward A Premium",
                "quantity": 5,
                "image_url": "file:///var/mobile/Containers/Data/Application/sample-room.png",
            },
            format="json",
            secure=True,
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data["item"]["name"], "Ward A Premium")
        self.assertEqual(patch_response.data["item"]["quantity"], 5)
        self.assertEqual(
            patch_response.data["item"]["image_url"],
            "file:///var/mobile/Containers/Data/Application/sample-room.png",
        )

        get_response = self.client.get(list_url, {"item_kind": "room"}, secure=True)
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(get_response.data["results"]), 1)

        delete_response = self.client.delete(detail_url, secure=True)
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(InstitutionEngineManagedItem.objects.filter(id=item_id).exists())

    def test_member_cannot_create_managed_item(self):
        self.client.force_authenticate(self.member)
        list_url = reverse(
            "health-ops-institution-engine-managed-item-list-create",
            kwargs={
                "institution_id": self.institution.id,
                "engine_key": "lab-order-engine",
            },
        )
        create_response = self.client.post(
            list_url,
            {
                "item_kind": "lab_slot",
                "name": "General Lab Slot",
            },
            format="json",
            secure=True,
        )
        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_can_use_legacy_institution_route(self):
        self.client.force_authenticate(self.manager)
        list_url = reverse(
            "health-ops-institution-engine-managed-item-list-create",
            kwargs={
                "institution_id": self.legacy_institution_id,
                "engine_key": "admission-bed-management-engine",
            },
        )
        create_response = self.client.post(
            list_url,
            {
                "item_kind": "room",
                "name": "Ward Legacy",
                "amount_micro": 100000,
                "quantity": 1,
                "status": "active",
            },
            format="json",
            secure=True,
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            str(create_response.data["item"]["institution"]),
            str(self.institution.id),
        )

    def test_outsider_cannot_view_managed_items(self):
        self.client.force_authenticate(self.outsider)
        list_url = reverse(
            "health-ops-institution-engine-managed-item-list-create",
            kwargs={
                "institution_id": self.institution.id,
                "engine_key": "lab-order-engine",
            },
        )
        response = self.client.get(list_url, secure=True)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
