"""
Tests for the SCIM 2.0 Users MVP.

Run:
  python3 manage.py test apps.partners.scim --keepdb -v 2
"""
import uuid

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.partners.models import Partner, PartnerIntegration, PartnerMembership, PartnerMembershipStatus

TOKEN = "test-scim-bearer-token"


@override_settings(SECURE_SSL_REDIRECT=False)
class ScimUsersTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        owner = User.objects.create(phone="+237600000300", username="acme_owner2", status="active", is_active=True)
        self.partner = Partner.objects.create(id=uuid.uuid4(), name="Acme Corp", slug="acme-scim", owner=owner)
        PartnerIntegration.objects.create(
            partner=self.partner,
            kind=PartnerIntegration.KIND_SCIM,
            provider="okta",
            is_enabled=True,
            config={"base_url": "https://acme.okta.com/scim", "token": TOKEN},
        )
        self.auth_headers = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}
        self.base = "/scim/v2/acme-scim/Users"

    def test_rejects_missing_bearer_token(self):
        resp = self.client_api.get(self.base)
        self.assertEqual(resp.status_code, 401)

    def test_rejects_wrong_token(self):
        resp = self.client_api.get(self.base, HTTP_AUTHORIZATION="Bearer wrong-token")
        self.assertEqual(resp.status_code, 401)

    def test_create_user_provisions_a_kis_account_and_membership(self):
        resp = self.client_api.post(
            self.base,
            {
                "userName": "newhire@acme-corp.example",
                "name": {"formatted": "New Hire"},
                "emails": [{"value": "newhire@acme-corp.example", "primary": True}],
                "active": True,
            },
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertTrue(body["active"])

        user = User.objects.get(email="newhire@acme-corp.example")
        self.assertTrue(user.phone_is_placeholder)
        self.assertFalse(user.has_usable_password())
        membership = PartnerMembership.objects.get(partner=self.partner, user=user)
        self.assertEqual(membership.status, PartnerMembershipStatus.MEMBER)

    def test_create_reuses_an_existing_account_by_email(self):
        existing = User.objects.create(
            phone="+237600000301", email="already@acme-corp.example", username="already", status="active"
        )
        resp = self.client_api.post(
            self.base,
            {"userName": "already@acme-corp.example", "active": True},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, 200)  # 200, not 201 - no new account created
        self.assertEqual(User.objects.filter(email="already@acme-corp.example").count(), 1)
        self.assertTrue(PartnerMembership.objects.filter(partner=self.partner, user=existing).exists())

    def test_patch_active_false_deprovisions_without_deleting_the_account(self):
        create_resp = self.client_api.post(
            self.base,
            {"userName": "leaving@acme-corp.example", "active": True},
            format="json",
            **self.auth_headers,
        )
        user_id = create_resp.json()["id"]

        patch_resp = self.client_api.patch(
            f"{self.base}/{user_id}",
            {"Operations": [{"op": "replace", "path": "active", "value": False}]},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertFalse(patch_resp.json()["active"])

        membership = PartnerMembership.objects.get(partner=self.partner, user_id=user_id)
        self.assertEqual(membership.status, PartnerMembershipStatus.REMOVED)
        self.assertTrue(User.objects.filter(id=user_id).exists())  # account itself untouched

    def test_delete_deprovisions_rather_than_hard_deleting(self):
        create_resp = self.client_api.post(
            self.base,
            {"userName": "fired@acme-corp.example", "active": True},
            format="json",
            **self.auth_headers,
        )
        user_id = create_resp.json()["id"]

        del_resp = self.client_api.delete(f"{self.base}/{user_id}", **self.auth_headers)
        self.assertEqual(del_resp.status_code, 204)
        self.assertTrue(User.objects.filter(id=user_id).exists())
        membership = PartnerMembership.objects.get(partner=self.partner, user_id=user_id)
        self.assertEqual(membership.status, PartnerMembershipStatus.REMOVED)

    def test_list_filters_by_username(self):
        self.client_api.post(
            self.base, {"userName": "alice@acme-corp.example"}, format="json", **self.auth_headers
        )
        self.client_api.post(
            self.base, {"userName": "bob@acme-corp.example"}, format="json", **self.auth_headers
        )
        resp = self.client_api.get(
            f'{self.base}?filter=userName eq "alice@acme-corp.example"', **self.auth_headers
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["totalResults"], 1)
        self.assertEqual(body["Resources"][0]["userName"], "alice@acme-corp.example")

    def test_cannot_touch_another_tenants_membership(self):
        other_owner = User.objects.create(phone="+237600000302", username="other_owner", status="active")
        other_partner = Partner.objects.create(id=uuid.uuid4(), name="Other Co", slug="other-co", owner=other_owner)
        other_user = User.objects.create(phone="+237600000303", email="foreign@other.example", username="foreign")
        PartnerMembership.objects.create(partner=other_partner, user=other_user, status=PartnerMembershipStatus.MEMBER)

        resp = self.client_api.get(f"{self.base}/{other_user.id}", **self.auth_headers)
        self.assertEqual(resp.status_code, 404)
