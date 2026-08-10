"""
Regression tests for IntrospectView's tier/isPremium/entitlements resolution.

Previously this endpoint extracted tier via an ad-hoc getattr and computed
isPremium via `tier.lower() != "basic"` — stale since the free tier was
renamed to "Free" well before this endpoint existed, so every free-tier
user was reported as isPremium: true to Nest.js. entitlements was also
always a hardcoded {}. Both now go through the same canonical
apps.accounts.tiers resolution the rest of the app uses.

Run:
  python3 manage.py test apps.chat.test_introspect_tier_entitlements --keepdb -v 2
"""
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Device, Subscription
from apps.accounts.tiers import ensure_default_account_tiers
from apps.accounts.views import issue_tokens_for_user

URL = "/api/v1/auth/introspect/"
DEVICE_ID = "introspect-tier-test-device"
INTERNAL_ENV = {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}


class IntrospectTierEntitlementsTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        User = get_user_model()
        self.user = User.objects.create_user(
            phone="+237699400101", country="CM", password="pass1234",
        )
        Device.objects.create(user=self.user, device_id=DEVICE_ID, platform="android", token_version=1)
        self.client = APIClient()

    def _introspect(self):
        token = issue_tokens_for_user(self.user, device_id=DEVICE_ID)["access"]
        with patch.dict(os.environ, INTERNAL_ENV):
            return self.client.get(
                URL, {}, HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_INTERNAL_AUTH="real-token",
            )

    def test_free_tier_user_is_not_premium(self):
        self.user.tier = "Free"
        self.user.save(update_fields=["tier"])
        res = self._introspect()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["tier"], "Free")
        self.assertFalse(res.data["isPremium"])

    def test_user_with_no_tier_at_all_is_not_premium(self):
        self.user.tier = ""
        self.user.save(update_fields=["tier"])
        res = self._introspect()
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["isPremium"])

    def test_paid_tier_user_is_premium(self):
        self.user.tier = "Pro"
        self.user.save(update_fields=["tier"])
        res = self._introspect()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["tier"], "Pro")
        self.assertTrue(res.data["isPremium"])

    def test_highest_tier_user_is_premium(self):
        self.user.tier = "Partner Pro"
        self.user.save(update_fields=["tier"])
        res = self._introspect()
        self.assertTrue(res.data["isPremium"])

    def test_entitlements_are_not_empty_for_a_paid_tier(self):
        self.user.tier = "Business"
        self.user.save(update_fields=["tier"])
        res = self._introspect()
        self.assertNotEqual(res.data["entitlements"], {})
        # Business includes Free's baseline features (cumulative aggregation).
        self.assertIn("communities", res.data["entitlements"])

    def test_active_subscription_takes_priority_over_the_denormalized_tier_string(self):
        pro = AccountTier.objects.get(name="Pro")
        self.user.tier = "Free"
        self.user.save(update_fields=["tier"])
        Subscription.objects.create(
            user=self.user, tier=pro, status="active",
            started_at=timezone.now(), ends_at=timezone.now() + timezone.timedelta(days=30),
        )
        res = self._introspect()
        self.assertEqual(res.data["tier"], "Pro")
        self.assertTrue(res.data["isPremium"])

    def test_basic_alias_still_resolves_correctly_as_not_premium(self):
        # Legacy accounts may still carry the pre-rename "Basic" string.
        self.user.tier = "Basic"
        self.user.save(update_fields=["tier"])
        res = self._introspect()
        self.assertFalse(res.data["isPremium"])
