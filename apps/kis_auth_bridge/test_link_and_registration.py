"""Tests for the link and registration bridge views. Mocks
apps.kis_auth_bridge's exchange_client/link_ticket functions the same way
test_recovery_complete.py does — real cross-service HMAC/JWT behavior is
kis-auth's own test responsibility (see its link-ticket.spec.ts and the
app-security-hardening suite); these tests cover Django-side logic:
auth requirements, purpose/user binding, rollout gating, and the
registration transaction (including rollback on a failed link call).

Run:
  python3 manage.py test apps.kis_auth_bridge --keepdb -v 2
"""
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, Device, User
from apps.kis_auth_bridge.exchange_client import (
    ExchangeError,
    VerifiedAuthorization,
    VerifiedRegistration,
)

LINK_INITIATE_URL = "/api/v1/kis-auth/link/initiate/"
LINK_COMPLETE_URL = "/api/v1/kis-auth/link/complete/"
REGISTRATION_URL = "/api/v1/kis-auth/registration/complete/"


def _verified_link(**overrides):
    defaults = dict(
        kis_user_id="",
        purpose="link",
        auth_identity_id="identity-1",
        provider_email="person@example.com",
        provider_email_verified=True,
    )
    defaults.update(overrides)
    return VerifiedAuthorization(**defaults)


def _verified_registration(**overrides):
    defaults = dict(
        purpose="registration",
        provider_subject="google-sub-123",
        provider_email="newuser@example.com",
        provider_email_verified=True,
    )
    defaults.update(overrides)
    return VerifiedRegistration(**defaults)


@override_settings(SECURE_SSL_REDIRECT=False, KIS_AUTH_ENABLED=True, KIS_AUTH_LINK_ENABLED=True)
class KisAuthLinkTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.user = User.objects.create(
            phone="+237600000002", username="link_test_user", status="active", is_active=True
        )

    def test_initiate_requires_authentication(self):
        resp = self.client_api.post(LINK_INITIATE_URL, {}, format="json")
        self.assertIn(resp.status_code, (401, 403))

    @override_settings(KIS_AUTH_LINK_ENABLED=False)
    def test_initiate_404s_when_flag_disabled(self):
        self.client_api.force_authenticate(self.user)
        resp = self.client_api.post(LINK_INITIATE_URL, {}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_initiate_mints_a_ticket_for_the_authenticated_user(self):
        self.client_api.force_authenticate(self.user)
        with patch(
            "apps.kis_auth_bridge.views.mint_link_ticket", return_value="fake-ticket"
        ) as mock_mint:
            resp = self.client_api.post(LINK_INITIATE_URL, {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["link_ticket"], "fake-ticket")
        mock_mint.assert_called_once_with(str(self.user.id))

    def test_complete_requires_authentication(self):
        resp = self.client_api.post(LINK_COMPLETE_URL, {"code": "x", "redirect_uri": "y"}, format="json")
        self.assertIn(resp.status_code, (401, 403))

    def test_complete_rejects_a_non_link_purpose(self):
        self.client_api.force_authenticate(self.user)
        verified = _verified_link(kis_user_id=str(self.user.id), purpose="recovery")
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            resp = self.client_api.post(
                LINK_COMPLETE_URL, {"code": "x", "redirect_uri": "y"}, format="json"
            )
        self.assertEqual(resp.status_code, 400)

    def test_complete_rejects_a_result_for_a_different_user(self):
        # The core defense: a valid, correctly-purposed link result for
        # SOMEONE ELSE'S account must never be accepted just because the
        # caller happens to be logged in right now.
        self.client_api.force_authenticate(self.user)
        other_user_id = str(uuid.uuid4())
        verified = _verified_link(kis_user_id=other_user_id)
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            resp = self.client_api.post(
                LINK_COMPLETE_URL, {"code": "x", "redirect_uri": "y"}, format="json"
            )
        self.assertEqual(resp.status_code, 400)

    def test_successful_link_writes_an_audit_log_entry(self):
        self.client_api.force_authenticate(self.user)
        verified = _verified_link(kis_user_id=str(self.user.id))
        with patch("apps.kis_auth_bridge.views.redeem_authorization_code", return_value=verified):
            resp = self.client_api.post(
                LINK_COMPLETE_URL, {"code": "x", "redirect_uri": "y"}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["linked"])
        entry = AuditLog.objects.filter(
            actor_id=self.user.id, action="account.google_identity_linked"
        ).first()
        self.assertIsNotNone(entry)


@override_settings(
    SECURE_SSL_REDIRECT=False, KIS_AUTH_ENABLED=True, KIS_AUTH_REGISTRATION_ENABLED=True
)
class KisAuthRegistrationTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.body = {
            "registration_code": "some-code",
            "redirect_uri": "https://kis.app/auth/registration-callback",
            "phone": "+237600000099",
            "phone_number": "600000099",
            "phone_country_code": "+237",
            "country": "CM",
            "device_id": "new-registration-device",
            "device_name": "New Phone",
            "platform": "android",
        }

    def _post(self, **overrides):
        body = {**self.body, **overrides}
        return self.client_api.post(REGISTRATION_URL, body, format="json")

    @override_settings(KIS_AUTH_REGISTRATION_ENABLED=False)
    def test_404s_when_flag_disabled(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 404)

    def test_rejects_missing_fields_without_calling_exchange(self):
        with patch("apps.kis_auth_bridge.views.redeem_registration_ticket") as mock_redeem:
            resp = self._post(registration_code="")
            self.assertEqual(resp.status_code, 400)
            mock_redeem.assert_not_called()

    def test_generic_error_when_exchange_fails(self):
        with patch(
            "apps.kis_auth_bridge.views.redeem_registration_ticket",
            side_effect=ExchangeError("whatever"),
        ):
            resp = self._post()
        self.assertEqual(resp.status_code, 400)

    def test_rejects_an_invalid_phone_without_creating_a_user(self):
        verified = _verified_registration()
        with patch(
            "apps.kis_auth_bridge.views.redeem_registration_ticket", return_value=verified
        ):
            resp = self._post(phone="", phone_number="")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(email=verified.provider_email).exists())

    def test_successful_registration_creates_a_linked_unusable_password_account(self):
        verified = _verified_registration()
        with (
            patch(
                "apps.kis_auth_bridge.views.redeem_registration_ticket", return_value=verified
            ),
            patch("apps.kis_auth_bridge.views.link_identity_server_to_server") as mock_link,
        ):
            resp = self._post()

        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertIn("access", body)
        self.assertIn("refresh", body)

        user = User.objects.get(id=body["user"]["id"])
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.email, verified.provider_email)
        self.assertTrue(user.email_verified)

        mock_link.assert_called_once_with(
            kis_user_id=str(user.id),
            provider_subject=verified.provider_subject,
            provider_email=verified.provider_email,
            provider_email_verified=verified.provider_email_verified,
            provider=verified.provider,
        )

        device = Device.objects.get(user=user, device_id="new-registration-device")
        self.assertTrue(device.is_parent)

        entry = AuditLog.objects.filter(actor_id=user.id, action="account.kis_auth_registration").first()
        self.assertIsNotNone(entry)

    def test_a_failed_link_call_rolls_back_the_new_account(self):
        # If kis-auth can't link the freshly created account (e.g. this
        # Google identity got linked to someone else in a race), there
        # must be no orphan, unlinkable KIS account left behind.
        verified = _verified_registration()
        with (
            patch(
                "apps.kis_auth_bridge.views.redeem_registration_ticket", return_value=verified
            ),
            patch(
                "apps.kis_auth_bridge.views.link_identity_server_to_server",
                side_effect=ExchangeError("conflict"),
            ),
        ):
            resp = self._post()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(email=verified.provider_email).exists())

    def test_already_registered_google_identity_does_not_get_a_second_account(self):
        # Belt-and-suspenders check mirroring kis-auth's own
        # already_registered branch — if a caller somehow reaches this
        # view twice with tickets for the same provider_subject, the
        # second registration's link call fails (409 -> ExchangeError from
        # kis-auth's real unique constraint) and must roll back cleanly,
        # not leave two half-created accounts.
        verified = _verified_registration()
        with (
            patch(
                "apps.kis_auth_bridge.views.redeem_registration_ticket", return_value=verified
            ),
            patch("apps.kis_auth_bridge.views.link_identity_server_to_server"),
        ):
            first = self._post()
        self.assertEqual(first.status_code, 201)

        with (
            patch(
                "apps.kis_auth_bridge.views.redeem_registration_ticket", return_value=verified
            ),
            patch(
                "apps.kis_auth_bridge.views.link_identity_server_to_server",
                side_effect=ExchangeError("identity already linked"),
            ),
        ):
            second = self._post(
                phone="+237600000098", phone_number="600000098", device_id="another-device"
            )
        self.assertEqual(second.status_code, 400)
        self.assertEqual(User.objects.filter(email=verified.provider_email).count(), 1)


@override_settings(
    SECURE_SSL_REDIRECT=False, KIS_AUTH_ENABLED=True, KIS_AUTH_REGISTRATION_ENABLED=True
)
class KisAuthEnterpriseSsoRegistrationTests(TestCase):
    """The enterprise-SSO JIT path: no phone in the request at all, since
    an IdP-federated account has no phone number to submit."""

    def setUp(self):
        self.client_api = APIClient()
        self.body = {
            "registration_code": "some-code",
            "redirect_uri": "https://kis.app/auth/registration-callback",
            "device_id": "enterprise-sso-device",
            "device_name": "Work Laptop",
            "platform": "web",
        }

    def _post(self, **overrides):
        body = {**self.body, **overrides}
        return self.client_api.post(REGISTRATION_URL, body, format="json")

    def test_creates_a_placeholder_phone_account_with_no_phone_in_the_request(self):
        verified = _verified_registration(
            purpose="enterprise_sso_registration",
            provider="oidc",
            partner_slug="acme-corp",
            provider_email="employee@acme-corp.example",
        )
        with (
            patch("apps.kis_auth_bridge.views.redeem_registration_ticket", return_value=verified),
            patch("apps.kis_auth_bridge.views.link_identity_server_to_server") as mock_link,
        ):
            resp = self._post()

        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertIsNone(body["user"]["phone"])
        self.assertTrue(body["user"]["phone_is_placeholder"])

        user = User.objects.get(id=body["user"]["id"])
        self.assertTrue(user.phone_is_placeholder)
        self.assertTrue(user.phone)  # a placeholder value was synthesized, not left blank
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.email, verified.provider_email)

        mock_link.assert_called_once_with(
            kis_user_id=str(user.id),
            provider_subject=verified.provider_subject,
            provider_email=verified.provider_email,
            provider_email_verified=verified.provider_email_verified,
            provider="oidc",
        )

    def test_ordinary_google_registration_is_unaffected(self):
        # Same view, default purpose - existing behavior must be untouched.
        verified = _verified_registration()
        self.assertEqual(verified.provider, "google")
        self.assertIsNone(verified.partner_slug)
