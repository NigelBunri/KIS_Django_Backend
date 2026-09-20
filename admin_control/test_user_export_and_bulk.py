"""
Tests for the two admin/compliance gaps closed here:
  1. AdminUserDataExportView - an admin console path to run a GDPR
     export on a user's behalf (previously self-service only).
  2. AdminUserBulkActionView - bulk ban/suspend/unban/block/restore/
     set_tier, since every existing moderation view only ever took one
     user_id at a time.

Run:
  python3 manage.py test admin_control.test_user_export_and_bulk --keepdb -v 2
"""
from __future__ import annotations

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import GDPRRequest, User
from admin_control.roles import AdminRole, AdminRoleAssignment, AdminRolePermission

_counter = 0


def _make_user(email, **kw):
    global _counter
    _counter += 1
    phone = kw.pop("phone", f"+2376552{_counter:05d}")
    country = kw.pop("country", "CM")
    return User.objects.create_user(phone=phone, email=email, password="test1234!", country=country, **kw)


def _grant(user, *, app_label="*", permissions):
    # These views resolve no "app_label" URL kwarg (unlike the generic
    # CRUD engine), so AdminAccessService.has_permission only matches a
    # permission row whose app_label is the literal wildcard "*" - see
    # admin_control/roles.py:has_permission.
    global _counter
    _counter += 1
    role = AdminRole.objects.create(name=f"scoped-{user.id}-{_counter}", is_super_role=False)
    AdminRolePermission.objects.create(role=role, app_label=app_label, permissions=list(permissions))
    AdminRoleAssignment.objects.create(user=user, role=role, is_active=True)
    return role


class AdminUserDataExportViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.target = _make_user("export-target@test.com", display_name="Export Target")

    def test_requires_users_export_permission(self):
        admin = _make_user("export-admin-noperm@test.com")
        _grant(admin, permissions=["users.view"])  # read access, NOT export
        self.client.force_authenticate(user=admin)
        resp = self.client.get(f"/control/admin/users/{self.target.id}/data-export/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_exports_target_users_data_and_records_a_gdpr_receipt(self):
        admin = _make_user("export-admin@test.com")
        _grant(admin, permissions=["users.export"])
        self.client.force_authenticate(user=admin)

        resp = self.client.get(f"/control/admin/users/{self.target.id}/data-export/?reason=legal+hold")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["user"]["id"], str(self.target.id))
        self.assertEqual(resp.data["user"]["display_name"], "Export Target")

        receipt = GDPRRequest.objects.filter(user=self.target, type="data_export").first()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, "completed")

    def test_placeholder_phone_never_appears_in_an_export(self):
        admin = _make_user("export-admin2@test.com")
        _grant(admin, permissions=["users.export"])
        self.client.force_authenticate(user=admin)

        self.target.phone_is_placeholder = True
        self.target.save(update_fields=["phone_is_placeholder"])

        resp = self.client.get(f"/control/admin/users/{self.target.id}/data-export/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data["user"]["phone"])


class AdminUserBulkActionViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_user("bulk-admin@test.com")
        _grant(self.admin, permissions=["users.moderate"])
        self.client.force_authenticate(user=self.admin)
        self.user_a = _make_user("bulk-a@test.com")
        self.user_b = _make_user("bulk-b@test.com")

    def test_bulk_suspend_two_users(self):
        resp = self.client.post(
            "/control/admin/users/bulk/",
            {"action": "suspend", "user_ids": [str(self.user_a.id), str(self.user_b.id)], "reason": "spam wave"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(set(resp.data["succeeded"]), {str(self.user_a.id), str(self.user_b.id)})
        self.assertEqual(resp.data["failed"], [])
        self.user_a.refresh_from_db()
        self.user_b.refresh_from_db()
        self.assertEqual(self.user_a.status, "suspended")
        self.assertEqual(self.user_b.status, "suspended")

    def test_one_bad_id_does_not_sink_the_whole_batch(self):
        resp = self.client.post(
            "/control/admin/users/bulk/",
            {"action": "ban", "user_ids": [str(self.user_a.id), "00000000-0000-0000-0000-000000000000"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["succeeded"], [str(self.user_a.id)])
        self.assertEqual(len(resp.data["failed"]), 1)
        self.user_a.refresh_from_db()
        self.assertEqual(self.user_a.status, "banned")

    def test_bulk_set_tier(self):
        resp = self.client.post(
            "/control/admin/users/bulk/",
            {"action": "set_tier", "user_ids": [str(self.user_a.id)], "tier": "pro"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.user_a.refresh_from_db()
        self.assertEqual(self.user_a.tier.lower(), "pro")

    def test_rejects_unknown_action(self):
        resp = self.client.post(
            "/control/admin/users/bulk/",
            {"action": "detonate", "user_ids": [str(self.user_a.id)]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_requires_users_moderate_permission(self):
        weak_admin = _make_user("bulk-weak@test.com")
        _grant(weak_admin, permissions=["users.view"])
        self.client.force_authenticate(user=weak_admin)
        resp = self.client.post(
            "/control/admin/users/bulk/",
            {"action": "ban", "user_ids": [str(self.user_a.id)]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
