"""
Phase 4 — promo code redemption hardening. Updated in Phase 5: credit_bonus
now grants KIS Coins via apps.rewards (RewardLedgerEntry) instead of the
legacy LoyaltyPoint model — see apps.billing.services.redeem_promo_code and
apps.rewards.services.grant_promo_bonus.

Covers: the TOCTOU race previously present in WalletViewSet.redeem (unguarded
duplicate-check -> credit -> create sequence), the previously-dropped
cash_bonus_cents application in PromoCodeViewSet.redeem_code (claimed in the
response, never actually granted), and real concurrent-request behavior for
both entry points now that they share one atomic, row-locked implementation
(apps.billing.services.redeem_promo_code).

Run:
  python3 manage.py test apps.billing.test_promo_redemption --keepdb -v 2
"""
from __future__ import annotations

import threading
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import CreditAccount, PromoCode, PromoRedemption, WalletAccount
from apps.billing.services import (
    PromoCodeAlreadyRedeemed,
    PromoCodeExpired,
    PromoCodeNotFound,
    PromoCodeUsageLimitReached,
    redeem_promo_code,
)
from apps.rewards.services import get_reward_balance

User = get_user_model()


def _api_url(route_name: str) -> str:
    url = reverse(route_name)
    return url if url.endswith("/") else f"{url}/"


def _make_user(phone: str) -> User:
    return User.objects.create_user(phone=phone, country="CM", password="pass1234")


def _make_promo(code: str, cash_bonus_cents=0, credit_bonus=0, usage_limit=None, is_active=True) -> PromoCode:
    return PromoCode.objects.create(
        code=code, cash_bonus_cents=cash_bonus_cents, credit_bonus=credit_bonus,
        usage_limit=usage_limit, is_active=is_active,
    )


def _points_balance(user) -> int:
    return get_reward_balance(user)["available"]


class RedeemPromoCodeServiceTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699300001")

    def test_credit_bonus_is_actually_applied(self):
        _make_promo("WELCOME10", credit_bonus=100)
        result = redeem_promo_code(self.user, "welcome10")  # case-insensitive
        self.assertEqual(result.credit_bonus, 100)
        self.assertEqual(_points_balance(self.user), 100)

    def test_cash_bonus_blocked_by_default_flag(self):
        _make_promo("CASH50", cash_bonus_cents=5000, credit_bonus=0)
        with self.assertRaises(Exception):
            redeem_promo_code(self.user, "CASH50")

    def test_cash_bonus_blocked_but_credit_bonus_still_applies(self):
        _make_promo("MIXED", cash_bonus_cents=5000, credit_bonus=50)
        result = redeem_promo_code(self.user, "MIXED")
        self.assertEqual(result.cash_bonus_cents, 0)
        self.assertTrue(result.legacy_cash_bonus_blocked)
        self.assertEqual(result.credit_bonus, 50)
        self.assertEqual(_points_balance(self.user), 50)

    def test_unknown_code_raises_not_found(self):
        with self.assertRaises(PromoCodeNotFound):
            redeem_promo_code(self.user, "DOESNOTEXIST")

    def test_expired_code_raises(self):
        promo = _make_promo("OLD", credit_bonus=10)
        promo.ends_at = timezone.now() - timedelta(days=1)
        promo.save(update_fields=["ends_at"])
        with self.assertRaises(PromoCodeExpired):
            redeem_promo_code(self.user, "OLD")

    def test_usage_limit_reached_raises(self):
        promo = _make_promo("LIMITED", credit_bonus=10, usage_limit=1)
        other = _make_user("+237699300002")
        redeem_promo_code(other, "LIMITED")
        with self.assertRaises(PromoCodeUsageLimitReached):
            redeem_promo_code(self.user, "LIMITED")

    def test_inactive_code_raises_not_found(self):
        _make_promo("DISABLED", credit_bonus=10, is_active=False)
        with self.assertRaises(PromoCodeNotFound):
            redeem_promo_code(self.user, "DISABLED")

    def test_second_redemption_by_same_user_raises(self):
        _make_promo("ONCE", credit_bonus=10)
        redeem_promo_code(self.user, "ONCE")
        with self.assertRaises(PromoCodeAlreadyRedeemed):
            redeem_promo_code(self.user, "ONCE")
        # Only one grant, not two.
        self.assertEqual(_points_balance(self.user), 10)

    def test_used_count_increments_exactly_once_per_redemption(self):
        promo = _make_promo("COUNT", credit_bonus=10)
        redeem_promo_code(self.user, "COUNT")
        promo.refresh_from_db()
        self.assertEqual(promo.used_count, 1)


class RedeemPromoCodeApiTests(TestCase):
    """Both live endpoints now share the same underlying implementation."""

    def setUp(self):
        self.user = _make_user("+237699300010")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_wallet_redeem_endpoint_applies_credit_bonus(self):
        _make_promo("WEND", credit_bonus=25)
        res = self.client.post(_api_url("wallet-redeem"), {"code": "WEND"}, format="json", secure=True)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["credit_bonus"], 25)
        self.assertEqual(_points_balance(self.user), 25)

    def test_redeem_code_endpoint_now_actually_applies_cash_bonus_when_enabled(self):
        with self.settings(KIS_LEGACY_PROMO_CASH_BONUS_ENABLED=True):
            _make_promo("PCEND", cash_bonus_cents=1000, credit_bonus=0)
            res = self.client.post(_api_url("promo-codes-redeem-code"), {"code": "PCEND"}, format="json", secure=True)
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(res.data["cash_bonus_cents"], 1000)
            wallet = WalletAccount.objects.get(user=self.user)
            self.assertEqual(wallet.balance_cents, 1000)

    def test_redeem_code_endpoint_blocks_cash_only_bonus_by_default(self):
        _make_promo("PCBLOCKED", cash_bonus_cents=1000, credit_bonus=0)
        res = self.client.post(_api_url("promo-codes-redeem-code"), {"code": "PCBLOCKED"}, format="json", secure=True)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data["code"], "legacy_financial_flow_disabled")
        self.assertFalse(PromoRedemption.objects.filter(user=self.user, promo__code="PCBLOCKED").exists())

    def test_redeem_code_endpoint_returns_409_on_duplicate(self):
        _make_promo("DUPE", credit_bonus=10)
        first = self.client.post(_api_url("promo-codes-redeem-code"), {"code": "DUPE"}, format="json", secure=True)
        second = self.client.post(_api_url("promo-codes-redeem-code"), {"code": "DUPE"}, format="json", secure=True)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(_points_balance(self.user), 10)

    def test_wallet_redeem_endpoint_returns_400_on_duplicate(self):
        _make_promo("DUPE2", credit_bonus=10)
        first = self.client.post(_api_url("wallet-redeem"), {"code": "DUPE2"}, format="json", secure=True)
        second = self.client.post(_api_url("wallet-redeem"), {"code": "DUPE2"}, format="json", secure=True)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)

    def test_unauthenticated_request_is_rejected(self):
        anon = APIClient()
        res = anon.post(_api_url("promo-codes-redeem-code"), {"code": "ANY"}, format="json", secure=True)
        self.assertEqual(res.status_code, 401)


class RedeemPromoCodeConcurrencyTests(TransactionTestCase):
    """Real threads + real Postgres row locking — TestCase's single wrapped
    transaction would make a race trivially "safe" for the wrong reason."""

    def test_concurrent_redemption_by_the_same_user_only_grants_once(self):
        user = _make_user("+237699300020")
        _make_promo("RACE1", credit_bonus=100)
        results = []

        def worker():
            try:
                results.append(redeem_promo_code(user, "RACE1"))
            except Exception as exc:
                results.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if not isinstance(r, Exception)]
        self.assertEqual(len(successes), 1, "exactly one concurrent redemption should succeed")
        self.assertEqual(PromoRedemption.objects.filter(user=user, promo__code="RACE1").count(), 1)
        self.assertEqual(_points_balance(user), 100, "credit_bonus must be granted exactly once, not per attempt")

    def test_concurrent_redemption_never_exceeds_usage_limit(self):
        _make_promo("RACE2", credit_bonus=50, usage_limit=3)
        users = [_make_user(f"+23769930003{i}") for i in range(8)]
        results = []

        def worker(u):
            try:
                results.append(redeem_promo_code(u, "RACE2"))
            except Exception as exc:
                results.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if not isinstance(r, Exception)]
        self.assertEqual(len(successes), 3, "usage_limit=3 must cap successful redemptions at exactly 3")
        promo = PromoCode.objects.get(code="RACE2")
        self.assertEqual(promo.used_count, 3)
        self.assertEqual(PromoRedemption.objects.filter(promo=promo).count(), 3)
