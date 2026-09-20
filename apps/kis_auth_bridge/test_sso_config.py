"""
Tests for the internal SSO-config lookup endpoint kis-auth calls to
resolve which OIDC IdP a partner's enterprise SSO is configured against.

Run:
  python3 manage.py test apps.kis_auth_bridge.test_sso_config --keepdb -v 2
"""
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.internal_signing import sign_internal_request
from apps.partners.models import Partner, PartnerIntegration

URL = "/api/v1/kis-auth/sso-config/"
SECRET = "test-kisauth-secret"


@override_settings(SECURE_SSL_REDIRECT=False)
class SsoConfigViewTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.env_patcher = patch.dict("os.environ", {"KISAUTH_INTERNAL_HMAC_SECRET": SECRET})
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        owner = User.objects.create(phone="+237600000200", username="acme_owner", status="active", is_active=True)
        self.partner = Partner.objects.create(id=uuid.uuid4(), name="Acme Corp", slug="acme-corp", owner=owner)

    def _signed_get(self, query):
        full_url = f"{URL}?{query}"
        headers = sign_internal_request("GET", full_url, body=None, secret=SECRET)
        django_headers = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()}
        return self.client_api.get(full_url, **django_headers)

    def test_rejects_unsigned_requests(self):
        resp = self.client_api.get(f"{URL}?partner_slug=acme-corp")
        self.assertEqual(resp.status_code, 401)

    def test_rejects_wrong_signature(self):
        headers = sign_internal_request("GET", f"{URL}?partner_slug=acme-corp", secret="wrong-secret")
        django_headers = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()}
        resp = self.client_api.get(f"{URL}?partner_slug=acme-corp", **django_headers)
        self.assertEqual(resp.status_code, 401)

    def test_404s_for_unknown_partner(self):
        resp = self._signed_get("partner_slug=does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_404s_when_no_sso_integration_configured(self):
        resp = self._signed_get("partner_slug=acme-corp")
        self.assertEqual(resp.status_code, 404)

    def test_returns_full_unredacted_config_for_the_internal_caller(self):
        PartnerIntegration.objects.create(
            partner=self.partner,
            kind=PartnerIntegration.KIND_SSO,
            provider="oidc",
            is_enabled=True,
            config={
                "issuer": "https://acme.okta.com",
                "client_id": "abc123",
                "client_secret": "super-secret-value",
                "discovery_url": "https://acme.okta.com/.well-known/openid-configuration",
            },
        )
        resp = self._signed_get("partner_slug=acme-corp")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["client_secret"], "super-secret-value")  # unredacted - trusted internal channel
        self.assertEqual(body["partner_slug"], "acme-corp")

    def test_409s_when_enabled_but_missing_required_fields(self):
        # Should not normally happen given PartnerIntegrationSerializer's
        # own validation, but the direct-DB/legacy-row case must fail
        # loudly rather than handing kis-auth a broken config.
        PartnerIntegration.objects.create(
            partner=self.partner,
            kind=PartnerIntegration.KIND_SSO,
            provider="oidc",
            is_enabled=True,
            config={"issuer": "https://acme.okta.com"},
        )
        resp = self._signed_get("partner_slug=acme-corp")
        self.assertEqual(resp.status_code, 409)
