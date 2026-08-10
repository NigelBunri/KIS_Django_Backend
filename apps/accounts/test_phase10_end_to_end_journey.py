"""
Phase 10 — end-to-end validation: exercises a realistic user journey that
crosses many of the previous 9 phases' fixes together, in the SAME test
run, since each phase's own tests only ever proved its own slice in
isolation. This is the one place that actually proves the fixes still
cooperate correctly:

  Phase 1  — device-bound JWT auth (baseline, exercised throughout)
  Phase 3  — apply_tier_upgrade / real billing_period_days
  Phase 4  — referral code capture + reward at registration
  Phase 5  — CELERY_TASK_ALWAYS_EAGER test wiring (sweep runs synchronously)
  Phase 6  — welcome-email failure is logged + audited, not silently lost
  Phase 7  — X-Internal-Auth bypass requires a real signed request
  Phase 8  — UserViewSet field-safety + write-ownership; the
             apps.communities routing collision is actually fixed
  Phase 9  — the request-log line is real at INFO in production settings

Run:
  python3 manage.py test apps.accounts.test_phase10_end_to_end_journey --keepdb -v 2
"""
from __future__ import annotations

from unittest.mock import patch

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import Resolver404, resolve
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, AuditLog, Device, Subscription, User
from apps.accounts.views import issue_tokens_for_user
from apps.billing.tasks import expire_subscriptions
from apps.chat.internal_signing import sign_internal_request
from apps.referrals.models import Referral, ReferralCode
from apps.referrals.services import REFERRAL_REWARD_POINTS
from apps.commerce.models import LoyaltyPoint

DEVICE_ID = "phase10-journey-device"


def _auth_client(user: User, device_id: str) -> APIClient:
    tokens = issue_tokens_for_user(user, device_id=device_id)
    Device.objects.get_or_create(
        user=user, device_id=device_id,
        defaults={"platform": "android", "is_parent": True, "token_version": 1, "last_seen_at": timezone.now()},
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID=device_id)
    return client


@override_settings(SECURE_SSL_REDIRECT=False, KIS_PHONE_VERIFICATION_ENABLED=False)
class FullUserJourneyAcrossPhasesTests(TestCase):
    def setUp(self):
        self.referrer = User.objects.create_user(phone="+237699900001", password="TestPass12!", country="CM")
        self.referrer.verification = {"phone": {"verified": True}}
        self.referrer.status = "active"
        self.referrer.is_active = True
        self.referrer.email = "referrer@example.com"
        self.referrer.save(update_fields=["verification", "status", "is_active", "email"])
        self.referral_code = ReferralCode.get_or_create_for_user(self.referrer)

        self.tier = AccountTier.objects.create(
            name="Phase10 Journey Pro", price_cents=1500, rank=1, billing_period_days=30,
        )

    def test_full_journey(self):
        # --- Step 1 (Phase 4): register a new user with the referral code.
        # Verification is suspended (the real production default), so the
        # account activates and the referral reward fires in the same
        # request.
        with self.assertLogs("common.middleware", level="INFO") as request_logs:
            register_res = self.client.post("/api/v1/auth/register/", {
                "phone_country_code": "+237", "phone_number": "699900002", "country": "CM",
                "password": "TestPass12!", "password2": "TestPass12!",
                "device_id": DEVICE_ID, "device_platform": "android",
                "referral_code": self.referral_code.code,
            }, format="json")
        self.assertEqual(register_res.status_code, 201)
        new_user = User.objects.get(phone_number="699900002")

        # Phase 9: the request actually produced a real access-log line.
        self.assertTrue(any("REQ END" in line and "/api/v1/auth/register/" in line for line in request_logs.output))

        # Phase 4: referral reward granted to the referrer.
        referral = Referral.objects.get(referred_user=new_user)
        self.assertEqual(referral.status, Referral.STATUS_REWARDED)
        points = LoyaltyPoint.objects.filter(user=self.referrer).aggregate(total=Sum("points"))["total"]
        self.assertEqual(points, REFERRAL_REWARD_POINTS)

        # Phase 6 note: UserCreateSerializer does not collect an `email`
        # field at registration (a known, separately-flagged gap — see
        # apps.accounts.test_welcome_email_hardening's module docstring),
        # so the real, current behavior is that the welcome-email branch
        # is never even attempted here. Confirmed explicitly rather than
        # assumed, so this test breaks loudly if that ever changes.
        self.assertIsNone(new_user.email)
        self.assertFalse(
            AuditLog.objects.filter(actor_id=new_user.id, action__startswith="email.").exists()
        )

        # --- Step 2 (Phase 3): the new user gets a real paid subscription,
        # using the tier's own billing_period_days rather than a hardcoded
        # 30 (Phase 3's actual fix), via the canonical service function.
        from apps.billing.services import apply_tier_upgrade
        apply_tier_upgrade(
            user=new_user, tier=self.tier, source="flutterwave",
            amount_cents=1500, reference="phase10-journey-ref",
        )
        new_user.refresh_from_db()
        self.assertEqual(new_user.tier, self.tier.name)
        sub = Subscription.objects.get(user=new_user, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(
            sub.ends_at.date(), (sub.started_at + timezone.timedelta(days=self.tier.billing_period_days)).date(),
        )

        # --- Step 3 (Phase 8): the new user cannot self-escalate — tier is
        # read-only on the generic User PATCH surface even for the owner,
        # regardless of what apply_tier_upgrade just legitimately set it to.
        new_user_client = _auth_client(new_user, DEVICE_ID)
        patch_res = new_user_client.patch(
            f"/api/v1/users/{new_user.id}/", {"tier": "Free"}, format="json",
        )
        self.assertEqual(patch_res.status_code, 200)
        new_user.refresh_from_db()
        self.assertEqual(new_user.tier, self.tier.name)  # unchanged by the PATCH

        # --- Step 4 (Phase 8): the referrer can read the new user's public
        # profile (discoverability is real, intended behavior) but never
        # sees their email/phone — and cannot write to their record at all.
        referrer_client = _auth_client(self.referrer, "phase10-referrer-device")
        cross_read = referrer_client.get(f"/api/v1/users/{new_user.id}/")
        self.assertEqual(cross_read.status_code, 200)
        self.assertNotIn("email", cross_read.data)
        self.assertNotIn("phone", cross_read.data)

        cross_write = referrer_client.patch(
            f"/api/v1/users/{new_user.id}/", {"display_name": "Hijacked"}, format="json",
        )
        self.assertEqual(cross_write.status_code, 403)

        # --- Step 5 (Phase 7): a forged X-Internal-Auth header no longer
        # bypasses device-binding for this same new user's own device-bound
        # session, but a genuinely signed one still does (the real Nest
        # proxy pattern).
        forged = new_user_client.get(
            "/api/v1/auth/devices/", HTTP_X_DEVICE_ID="some-other-device", HTTP_X_INTERNAL_AUTH="forged-value",
        )
        self.assertEqual(forged.status_code, 401)

        with patch.dict("os.environ", {"DJANGO_INTERNAL_TOKEN": "phase10-real-token", "INTERNAL_SIGNATURE_REQUIRED": "1"}):
            signed = sign_internal_request("GET", "/api/v1/auth/devices/", body=None, secret="phase10-real-token")
            extra = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in signed.items()}
            legit = new_user_client.get("/api/v1/auth/devices/", **extra)
        self.assertEqual(legit.status_code, 200)

        # --- Step 6 (Phase 8 routing fix): /api/v1/communities/ resolves to
        # the real apps.communities app, not apps.core's dead shadow — a
        # request against it from this same authenticated user succeeds.
        match = resolve("/api/v1/communities/")
        self.assertEqual(match.func.cls.__module__, "apps.communities.views")
        communities_res = new_user_client.get("/api/v1/communities/")
        self.assertEqual(communities_res.status_code, 200)

        # --- Step 7 (Phase 5): the subscription-expiry sweep task runs
        # synchronously under CELERY_TASK_ALWAYS_EAGER (test-mode wiring)
        # and correctly leaves this still-current subscription untouched.
        result = expire_subscriptions.delay()
        self.assertTrue(result.successful())
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)

    def test_bare_groups_and_channels_paths_are_gone(self):
        # Phase 8: confirmed dead surface removed, no live route depends on it.
        for path in ("/api/v1/groups/", "/api/v1/channels/"):
            with self.assertRaises(Resolver404):
                resolve(path)
