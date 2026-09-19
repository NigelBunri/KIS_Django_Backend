"""
Tests for the security-event receiving endpoint — the Django side of
Phase 2 §16's cross-service audit unification.

Run:
  python3 manage.py test apps.kis_auth_bridge.test_security_event --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, User
from apps.chat.internal_signing import sign_internal_request

URL = "/api/v1/kis-auth/security-event/"
SECRET = "test-kisauth-secret"


@override_settings(SECURE_SSL_REDIRECT=False)
class SecurityEventViewTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.env_patcher = patch.dict("os.environ", {"KISAUTH_INTERNAL_HMAC_SECRET": SECRET})
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

    def _signed_post(self, body):
        headers = sign_internal_request("POST", URL, body=body, secret=SECRET)
        # Django test client expects header names as HTTP_X_...
        django_headers = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()}
        return self.client_api.post(URL, body, format="json", **django_headers)

    def test_rejects_a_request_with_no_signature_headers_at_all(self):
        resp = self.client_api.post(URL, {"event_type": "exchange.succeeded", "outcome": "success"}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_rejects_a_request_signed_with_the_wrong_secret(self):
        headers = sign_internal_request("POST", URL, body={"event_type": "exchange.succeeded", "outcome": "success"}, secret="wrong-secret")
        django_headers = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()}
        resp = self.client_api.post(URL, {"event_type": "exchange.succeeded", "outcome": "success"}, format="json", **django_headers)
        self.assertEqual(resp.status_code, 401)

    def test_accepts_a_correctly_signed_event_and_writes_to_audit_log(self):
        resp = self._signed_post({
            "event_type": "exchange.succeeded",
            "outcome": "success",
            "kis_user_id": None,
            "client_id": "kis-django",
            "reason": None,
            "ip": "203.0.113.5",
            "metadata": {"purpose": "recovery"},
        })
        self.assertEqual(resp.status_code, 201)
        entry = AuditLog.objects.filter(action="security.kis_auth.exchange.succeeded").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("outcome"), "success")
        self.assertEqual(entry.meta.get("client_id"), "kis-django")

    def test_associates_the_event_with_a_real_user_when_kis_user_id_is_provided(self):
        user = User.objects.create(phone="+237600000010", username="security_event_test_user", status="active", is_active=True)
        resp = self._signed_post({
            "event_type": "oauth.callback_succeeded",
            "outcome": "success",
            "kis_user_id": str(user.id),
        })
        self.assertEqual(resp.status_code, 201)
        entry = AuditLog.objects.filter(action="security.kis_auth.oauth.callback_succeeded").first()
        self.assertEqual(entry.actor_id, user.id)

    def test_an_unresolvable_kis_user_id_does_not_fail_the_request(self):
        resp = self._signed_post({
            "event_type": "oauth.callback_succeeded",
            "outcome": "success",
            "kis_user_id": "not-a-real-uuid",
        })
        self.assertEqual(resp.status_code, 201)

    def test_rejects_an_unknown_event_type_but_still_logs_it_generically(self):
        resp = self._signed_post({"event_type": "totally_made_up_event", "outcome": "success"})
        self.assertEqual(resp.status_code, 201)
        entry = AuditLog.objects.filter(action="security.kis_auth.unknown").first()
        self.assertIsNotNone(entry)

    def test_caps_metadata_fields_rather_than_storing_an_unbounded_payload(self):
        huge_metadata = {f"key_{i}": i for i in range(100)}
        resp = self._signed_post({"event_type": "exchange.failed", "outcome": "failure", "metadata": huge_metadata})
        self.assertEqual(resp.status_code, 201)
        entry = AuditLog.objects.filter(action="security.kis_auth.exchange.failed").first()
        meta_keys = [k for k in entry.meta.keys() if k.startswith("meta_")]
        self.assertLessEqual(len(meta_keys), 20)

    def test_rejects_a_replayed_request_nonce(self):
        body = {"event_type": "exchange.succeeded", "outcome": "success"}
        headers = sign_internal_request("POST", URL, body=body, secret=SECRET)
        django_headers = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()}
        first = self.client_api.post(URL, body, format="json", **django_headers)
        second = self.client_api.post(URL, body, format="json", **django_headers)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 401)

    def test_returns_503_when_the_secret_is_not_configured_rather_than_accepting_anything(self):
        self.env_patcher.stop()
        with patch.dict("os.environ", {"KISAUTH_INTERNAL_HMAC_SECRET": ""}):
            resp = self._signed_post({"event_type": "exchange.succeeded", "outcome": "success"})
            self.assertEqual(resp.status_code, 503)
        self.env_patcher.start()
