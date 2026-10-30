"""Regression test for the referral-commission audit's admin requirement:
"Do not allow administrators to silently modify historical commission
records." Django admin already enforces this for ReferralCommission
(ReferralCommissionAdmin.has_change_permission returns False), but
admin_control's generic CRUD engine (/control/admin/crud/<app>/<Model>/<pk>/)
is a second, independent write path keyed only on a scoped admin's
crud.update permission for an app_label — without its own blocklist entry
it would bypass Django admin's protection entirely. See
admin_control/crud_engine/operations.py's _CRUD_ENGINE_BLOCKLIST and
docs/referrals/REFERRAL_COMMISSION_SYSTEM.md.

ReferralPayoutAccount is deliberately NOT blocked — it's a payout
destination, not a record of a transaction that already happened; support
staff may legitimately need to correct one via this engine.
"""
from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from admin_control.roles import AdminRole, AdminRoleAssignment, AdminRolePermission
from apps.referrals.models import Referral, ReferralCode, ReferralCommission
from apps.referrals.services import register_referral

_counter = 0


def _make_user(email, **kw):
    global _counter
    _counter += 1
    phone = kw.pop("phone", f"+2376551{_counter:05d}")
    country = kw.pop("country", "CM")
    return User.objects.create_user(phone=phone, email=email, password="test1234!", country=country, **kw)


def _make_scoped_admin(app_label: str, permissions=("crud.update", "crud.read")):
    global _counter
    _counter += 1
    user = _make_user(f"scoped-admin-{_counter}@test.com")
    role = AdminRole.objects.create(name=f"scoped-{user.id}-{_counter}", is_super_role=False)
    AdminRolePermission.objects.create(role=role, app_label=app_label, permissions=list(permissions))
    AdminRoleAssignment.objects.create(user=user, role=role, is_active=True)
    return user


def _make_commission() -> ReferralCommission:
    referrer = _make_user("commission-referrer@test.com")
    code = ReferralCode.get_or_create_for_user(referrer)
    referred = _make_user("commission-referred@test.com")
    referral = register_referral(referred_user=referred, referral_code=code.code)
    return ReferralCommission.objects.create(
        referrer=referrer,
        referred_user=referred,
        referral=referral,
        source_payment_reference="admin-blocklist-test-tx",
        source_provider="flutterwave",
        gross_amount_cents=10000,
        commission_rate_percent=Decimal("24.00"),
        commission_amount_cents=2400,
        status=ReferralCommission.STATUS_PENDING,
    )


class ReferralCommissionCrudBlocklistTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_scoped_admin("referrals")
        self.commission = _make_commission()

    def test_cannot_patch_commission_status_via_generic_crud_engine(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f"/control/admin/crud/referrals/ReferralCommission/{self.commission.id}/",
            {"status": ReferralCommission.STATUS_PAID, "commission_amount_cents": 999999},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.commission.refresh_from_db()
        self.assertEqual(self.commission.status, ReferralCommission.STATUS_PENDING)
        self.assertEqual(self.commission.commission_amount_cents, 2400)

    def test_cannot_read_commission_via_generic_crud_engine_either(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(f"/control/admin/crud/referrals/ReferralCommission/{self.commission.id}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)

    def test_cannot_delete_commission_via_generic_crud_engine(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(f"/control/admin/crud/referrals/ReferralCommission/{self.commission.id}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.assertTrue(ReferralCommission.objects.filter(pk=self.commission.pk).exists())
