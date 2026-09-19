"""
Unit tests for the exchange_client module with the HTTP layer mocked —
the real cross-service path (real HMAC signing, real HTTP call to a real
kis-auth server, real JWT verification against a real JWKS endpoint) was
proven manually while building this. These tests cover what a live
server can't easily exercise in CI: malformed/hostile responses.

Run:
  python3 manage.py test apps.kis_auth_bridge.test_exchange_client --keepdb -v 2
"""
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from django.test import TestCase, override_settings
from unittest.mock import MagicMock, patch

from apps.kis_auth_bridge.exchange_client import ExchangeError, redeem_authorization_code

ENV = {
    "KISAUTH_BASE_URL": "http://kisauth.test",
    "KISAUTH_INTERNAL_HMAC_SECRET": "test-secret",
}


def _make_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_key, public_key, private_pem


class RedeemAuthorizationCodeTests(TestCase):
    def setUp(self):
        self.env_patcher = patch.dict("os.environ", ENV)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.private_key, self.public_key, _ = _make_rsa_keypair()

    def _token(self, **claims):
        defaults = dict(
            iss="kisauth.kingdomimpactventures.org",
            aud="kis-django",
            sub="user-1",
            purpose="recovery",
            auth_identity_id="identity-1",
            provider_email="a@example.com",
            provider_email_verified=True,
        )
        defaults.update(claims)
        return pyjwt.encode(defaults, self.private_key, algorithm="RS256", headers={"kid": "test-kid"})

    def _mock_signing_key(self):
        mock_key = MagicMock()
        mock_key.key = self.public_key
        return mock_key

    @patch("apps.kis_auth_bridge.exchange_client._get_jwk_client")
    @patch("apps.kis_auth_bridge.exchange_client.requests.post")
    def test_successful_exchange_returns_verified_claims(self, mock_post, mock_get_jwk_client):
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": self._token()})
        mock_get_jwk_client.return_value.get_signing_key_from_jwt.return_value = self._mock_signing_key()

        result = redeem_authorization_code(code="c1", client_id="kis-django", redirect_uri="https://kis.app/cb")
        self.assertEqual(result.kis_user_id, "user-1")
        self.assertEqual(result.purpose, "recovery")
        self.assertTrue(result.provider_email_verified)

    @patch("apps.kis_auth_bridge.exchange_client.requests.post")
    def test_non_201_response_raises(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400, json=lambda: {"detail": "bad"})
        with self.assertRaises(ExchangeError):
            redeem_authorization_code(code="c1", client_id="kis-django", redirect_uri="https://kis.app/cb")

    @patch("apps.kis_auth_bridge.exchange_client.requests.post")
    def test_missing_token_in_response_raises(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {})
        with self.assertRaises(ExchangeError):
            redeem_authorization_code(code="c1", client_id="kis-django", redirect_uri="https://kis.app/cb")

    @patch("apps.kis_auth_bridge.exchange_client._get_jwk_client")
    @patch("apps.kis_auth_bridge.exchange_client.requests.post")
    def test_token_signed_by_a_different_key_is_rejected(self, mock_post, mock_get_jwk_client):
        # Simulate the JWKS client resolving to a DIFFERENT public key than
        # the one that actually signed the token (e.g. a forged/mismatched kid).
        other_private_key, other_public_key, _ = _make_rsa_keypair()
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": self._token()})
        wrong_key = MagicMock()
        wrong_key.key = other_public_key
        mock_get_jwk_client.return_value.get_signing_key_from_jwt.return_value = wrong_key

        with self.assertRaises(ExchangeError):
            redeem_authorization_code(code="c1", client_id="kis-django", redirect_uri="https://kis.app/cb")

    @patch("apps.kis_auth_bridge.exchange_client._get_jwk_client")
    @patch("apps.kis_auth_bridge.exchange_client.requests.post")
    def test_wrong_audience_is_rejected(self, mock_post, mock_get_jwk_client):
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": self._token(aud="some-other-client")})
        mock_get_jwk_client.return_value.get_signing_key_from_jwt.return_value = self._mock_signing_key()
        with self.assertRaises(ExchangeError):
            redeem_authorization_code(code="c1", client_id="kis-django", redirect_uri="https://kis.app/cb")

    @patch("apps.kis_auth_bridge.exchange_client._get_jwk_client")
    @patch("apps.kis_auth_bridge.exchange_client.requests.post")
    def test_wrong_issuer_is_rejected(self, mock_post, mock_get_jwk_client):
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": self._token(iss="https://evil.example.com")})
        mock_get_jwk_client.return_value.get_signing_key_from_jwt.return_value = self._mock_signing_key()
        with self.assertRaises(ExchangeError):
            redeem_authorization_code(code="c1", client_id="kis-django", redirect_uri="https://kis.app/cb")

    @patch("apps.kis_auth_bridge.exchange_client._get_jwk_client")
    @patch("apps.kis_auth_bridge.exchange_client.requests.post")
    def test_expired_token_is_rejected(self, mock_post, mock_get_jwk_client):
        import time
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": self._token(exp=int(time.time()) - 60)})
        mock_get_jwk_client.return_value.get_signing_key_from_jwt.return_value = self._mock_signing_key()
        with self.assertRaises(ExchangeError):
            redeem_authorization_code(code="c1", client_id="kis-django", redirect_uri="https://kis.app/cb")

    @patch("apps.kis_auth_bridge.exchange_client.requests.post")
    def test_network_failure_raises_generic_exchange_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.ConnectionError("boom")
        with self.assertRaises(ExchangeError):
            redeem_authorization_code(code="c1", client_id="kis-django", redirect_uri="https://kis.app/cb")

    def test_missing_hmac_secret_raises_before_any_network_call(self):
        with patch.dict("os.environ", {"KISAUTH_INTERNAL_HMAC_SECRET": ""}):
            with patch("apps.kis_auth_bridge.exchange_client.requests.post") as mock_post:
                with self.assertRaises(ExchangeError):
                    redeem_authorization_code(code="c1", client_id="kis-django", redirect_uri="https://kis.app/cb")
                mock_post.assert_not_called()
