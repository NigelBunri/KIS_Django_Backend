"""
Phase 8 — read-only API for the mobile Rewards UI: balance, history,
achievement catalog.

Run:
  python3 manage.py test apps.rewards.test_api --keepdb -v 2
"""
from __future__ import annotations

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tests_qa_full import make_verified_user

from .models import AchievementDefinition, RewardLedgerEntry


def _grant(user, amount, status=RewardLedgerEntry.STATUS_CONFIRMED, effective_at=None, type_=RewardLedgerEntry.TYPE_ADMIN_ADJUSTMENT):
    return RewardLedgerEntry.objects.create(
        user=user, type=type_, source="test", amount=amount, status=status,
        effective_at=effective_at or timezone.now(),
    )


@override_settings(SECURE_SSL_REDIRECT=False)
class RewardBalanceViewTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700004001")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_returns_available_and_pending(self):
        _grant(self.user, 500, status=RewardLedgerEntry.STATUS_CONFIRMED)
        _grant(self.user, 200, status=RewardLedgerEntry.STATUS_PENDING)

        res = self.client.get("/api/v1/rewards/balance/", secure=True)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["available"], 500)
        self.assertEqual(res.data["pending"], 200)

    def test_this_period_earned_and_spent(self):
        now = timezone.now()
        _grant(self.user, 300, effective_at=now)
        _grant(self.user, -100, effective_at=now, type_=RewardLedgerEntry.TYPE_REDEMPTION)
        # Outside the current month — must not be counted.
        last_month = now.replace(day=1) - timezone.timedelta(days=1)
        _grant(self.user, 9999, effective_at=last_month)

        res = self.client.get("/api/v1/rewards/balance/", secure=True)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["this_period_earned"], 300)
        self.assertEqual(res.data["this_period_spent"], 100)

    def test_unauthenticated_rejected(self):
        anon = APIClient()
        res = anon.get("/api/v1/rewards/balance/", secure=True)
        self.assertEqual(res.status_code, 401)

    def test_zero_state_for_new_user(self):
        res = self.client.get("/api/v1/rewards/balance/", secure=True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {
            "available": 0, "pending": 0, "this_period_earned": 0, "this_period_spent": 0,
        })


@override_settings(SECURE_SSL_REDIRECT=False)
class RewardHistoryViewTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700004002")
        self.other = make_verified_user("+237700004003")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_returns_only_this_users_entries_newest_first(self):
        older = _grant(self.user, 100, effective_at=timezone.now() - timezone.timedelta(days=1))
        newer = _grant(self.user, 50, effective_at=timezone.now())
        _grant(self.other, 999)  # must not appear

        res = self.client.get("/api/v1/rewards/history/", secure=True)

        self.assertEqual(res.status_code, 200)
        ids = [row["id"] for row in res.data["results"]]
        self.assertEqual(ids, [str(newer.id), str(older.id)])

    def test_empty_for_new_user_no_fabricated_rows(self):
        res = self.client.get("/api/v1/rewards/history/", secure=True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"], [])


@override_settings(SECURE_SSL_REDIRECT=False)
class AchievementCatalogViewTests(TestCase):
    def setUp(self):
        self.user = make_verified_user("+237700004004")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_reports_completion_state(self):
        AchievementDefinition.objects.create(code="profile_completion", title="Complete your profile", coin_amount=100)
        AchievementDefinition.objects.create(code="email_verified", title="Verify your email", coin_amount=50)
        RewardLedgerEntry.objects.create(
            user=self.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT, source="profile_completion",
            amount=100, status=RewardLedgerEntry.STATUS_CONFIRMED, idempotency_key=f"achievement:{self.user.id}:profile_completion",
        )

        res = self.client.get("/api/v1/rewards/achievements/", secure=True)

        self.assertEqual(res.status_code, 200)
        by_code = {row["code"]: row for row in res.data["results"]}
        self.assertTrue(by_code["profile_completion"]["completed"])
        self.assertIsNotNone(by_code["profile_completion"]["completed_at"])
        self.assertFalse(by_code["email_verified"]["completed"])

    def test_inactive_achievements_excluded_no_fabricated_data(self):
        AchievementDefinition.objects.create(code="retired", title="Retired", coin_amount=1, is_active=False)
        res = self.client.get("/api/v1/rewards/achievements/", secure=True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"], [])
