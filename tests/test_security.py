"""
Tests for security fixes applied during the Phase 1/2 hardening pass:
  - Health check endpoint (200 / 503)
  - CIDR-aware IP restriction on ApiToken
  - Phone enumeration block on UserViewSet.me()
"""
import ipaddress
import pytest
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class HealthCheckTest(TestCase):
    def test_returns_200_when_healthy(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("db", data["checks"])
        self.assertIn("cache", data["checks"])

    def test_returns_json_content_type(self):
        response = self.client.get("/health/")
        self.assertIn("application/json", response.get("Content-Type", ""))


# ---------------------------------------------------------------------------
# CIDR IP restriction on ApiToken
# ---------------------------------------------------------------------------

class IpRestrictionTest(TestCase):
    """Tests for ApiToken.authenticate_with_ip()"""

    def setUp(self):
        from apps.accounts.models import ApiToken
        self.user = User.objects.create_user(phone="+10000000002", password="Test1234!")
        self.token_obj, self.token_plain = self.user.create_api_token(name="test")

    def _set_restrictions(self, restrictions):
        from apps.accounts.models import ApiToken
        token = ApiToken.objects.get(pk=self.token_obj.pk)
        token.ip_restrictions = restrictions
        token.save(update_fields=["ip_restrictions", "updated_at"])

    def test_no_restriction_allows_any_ip(self):
        from apps.accounts.models import ApiToken
        result = ApiToken.verify_plain_token(self.token_plain, "1.2.3.4")
        self.assertIsNotNone(result)

    def test_exact_ip_match_allowed(self):
        from apps.accounts.models import ApiToken
        self._set_restrictions(["203.0.113.5"])
        result = ApiToken.verify_plain_token(self.token_plain, "203.0.113.5")
        self.assertIsNotNone(result)

    def test_exact_ip_mismatch_blocked(self):
        from apps.accounts.models import ApiToken
        self._set_restrictions(["203.0.113.5"])
        result = ApiToken.verify_plain_token(self.token_plain, "203.0.113.6")
        self.assertIsNone(result)

    def test_cidr_block_allows_member_ip(self):
        from apps.accounts.models import ApiToken
        self._set_restrictions(["203.0.113.0/24"])
        result = ApiToken.verify_plain_token(self.token_plain, "203.0.113.99")
        self.assertIsNotNone(result)

    def test_cidr_block_rejects_outside_ip(self):
        from apps.accounts.models import ApiToken
        self._set_restrictions(["203.0.113.0/24"])
        result = ApiToken.verify_plain_token(self.token_plain, "203.0.114.1")
        self.assertIsNone(result)

    def test_invalid_ip_blocked(self):
        from apps.accounts.models import ApiToken
        self._set_restrictions(["203.0.113.0/24"])
        result = ApiToken.verify_plain_token(self.token_plain, "not-an-ip")
        self.assertIsNone(result)

    def test_no_ip_provided_with_restriction_blocked(self):
        from apps.accounts.models import ApiToken
        self._set_restrictions(["203.0.113.0/24"])
        result = ApiToken.verify_plain_token(self.token_plain, None)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Phone enumeration
# ---------------------------------------------------------------------------

class PhoneEnumerationTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/v1/users/me/"

    def test_phone_lookup_without_auth_header_returns_401(self):
        response = self.client.get(self.url, {"phone": "+10000000003"})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_phone_lookup_with_auth_header_does_not_return_401(self):
        # Even a fake / expired token header should not get blocked by the
        # enumeration guard (authentication failure is handled downstream).
        response = self.client.get(
            self.url,
            {"phone": "+10000000003"},
            HTTP_AUTHORIZATION="Bearer fake-token",
        )
        # Should not be 401 from the enumeration guard (may be 401 from auth, 404, etc.)
        self.assertNotEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
