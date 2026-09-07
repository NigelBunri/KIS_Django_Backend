"""Regression tests for a 2026-09-07 privilege-escalation finding.

A scoped, non-super admin holding only crud.update on app_label=
"admin_control" (a plausible, innocuous-sounding grant for e.g. an ops/
support role) could reach the generic CRUD engine's own RBAC tables and:
  1. PATCH their own AdminRole row's plain is_super_role BooleanField to
     true, instantly becoming a full super-admin.
  2. Create a brand-new role with is_super_role: true directly via
     AdminRoleView.post (the serializer exposed it as a writable field
     with no guard).
  3. Assign any existing role - including a super role - to any user via
     AdminRoleAssignmentView.post, or activate one via
     AdminRoleAssignmentDetailView.patch, with no check that the assigner
     was themself already a super-admin.

Fixed by: a hard blocklist in crud_engine/operations.py's resolve_model()
covering admin_control's own RBAC models (plus auth.User/Group/Permission
and sessions.Session as defense-in-depth), and an is_super_admin() guard
on the two direct role-creation/assignment write paths.
"""
from __future__ import annotations

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from admin_control.roles import AdminRole, AdminRoleAssignment, AdminRolePermission

_counter = 0


def _make_user(email, **kw):
    global _counter
    _counter += 1
    phone = kw.pop("phone", f"+2376550{_counter:05d}")
    country = kw.pop("country", "CM")
    return User.objects.create_user(phone=phone, email=email, password="test1234!", country=country, **kw)


def _make_scoped_admin_role(user, *, app_label="admin_control", permissions=("crud.update", "roles.manage", "roles.assign")):
    """A NON-super role scoped to exactly what the exploit needs -
    mirrors the "innocuous-sounding permission grant" from the finding,
    not a super-admin."""
    global _counter
    _counter += 1
    role = AdminRole.objects.create(name=f"scoped-{user.id}-{_counter}", is_super_role=False)
    AdminRolePermission.objects.create(role=role, app_label=app_label, permissions=list(permissions))
    AdminRoleAssignment.objects.create(user=user, role=role, is_active=True)
    return role


def _make_super_admin_role(user):
    role, _ = AdminRole.objects.get_or_create(name="super_admin", defaults={"is_super_role": True})
    AdminRolePermission.objects.get_or_create(role=role, app_label="*", defaults={"permissions": ["*"]})
    AdminRoleAssignment.objects.get_or_create(user=user, role=role, defaults={"is_active": True})
    return role


class CrudEngineBlocklistTests(TestCase):
    """Vector 1: editing admin_control's own RBAC tables through the
    generic 'edit any model' endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.attacker = _make_user("scoped-attacker@test.com")
        self.own_role = _make_scoped_admin_role(self.attacker)

    def test_cannot_patch_own_role_to_super_via_crud_engine(self):
        self.client.force_authenticate(user=self.attacker)
        resp = self.client.patch(
            f"/control/admin/crud/admin_control/AdminRole/{self.own_role.id}/",
            {"is_super_role": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.own_role.refresh_from_db()
        self.assertFalse(self.own_role.is_super_role)

    def test_cannot_read_role_assignments_via_crud_engine_either(self):
        # Read access to the RBAC tables via this generic engine is also
        # blocked, not just writes - same blocklist covers ModelDataView.
        self.client.force_authenticate(user=self.attacker)
        resp = self.client.get("/control/admin/crud/admin_control/AdminRoleAssignment/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)

    def test_cannot_hard_delete_roles_via_crud_engine_bulk_action(self):
        victim_role = AdminRole.objects.create(name="victim-role")
        self.client.force_authenticate(user=self.attacker)
        resp = self.client.post(
            "/control/admin/crud/admin_control/AdminRole/",
            {"action": "hard_delete", "ids": [victim_role.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.assertTrue(AdminRole.objects.filter(id=victim_role.id).exists())

    def test_ordinary_model_is_still_reachable_through_the_same_engine(self):
        # The blocklist must be narrow - it should not accidentally lock
        # scoped admins out of the legitimate models they're meant to
        # manage. Uses a second, "accounts"-scoped role (in addition to the
        # admin_control-scoped one from setUp) specifically to isolate this
        # from IsAdminControlUser's own pre-existing per-app_label scoping,
        # which would otherwise 403 this request for an unrelated reason
        # (no accounts-app permission at all) and produce a false pass.
        _make_scoped_admin_role(self.attacker, app_label="accounts", permissions=["crud.read"])
        self.client.force_authenticate(user=self.attacker)
        resp = self.client.get("/control/admin/crud/accounts/User/")
        self.assertNotEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_super_admin_is_also_blocked_from_the_generic_engine_for_rbac_models(self):
        # The blocklist is unconditional at the engine level (defense in
        # depth) - a super-admin still has every OTHER avenue (they already
        # pass every permission check everywhere, including on the
        # dedicated role views), so this doesn't remove real capability.
        super_admin = _make_user("real-super@test.com")
        _make_super_admin_role(super_admin)
        self.client.force_authenticate(user=super_admin)
        resp = self.client.get("/control/admin/crud/admin_control/AdminRole/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)


class RoleCreationEscalationTests(TestCase):
    """Vector 2: creating a brand-new super role directly."""

    def setUp(self):
        self.client = APIClient()
        self.attacker = _make_user("role-creator-attacker@test.com")
        _make_scoped_admin_role(self.attacker, permissions=("roles.manage",))

    def test_non_super_admin_cannot_create_a_super_role(self):
        self.client.force_authenticate(user=self.attacker)
        resp = self.client.post(
            "/control/admin/roles/",
            {"name": "self-granted-super", "is_super_role": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.assertFalse(AdminRole.objects.filter(name="self-granted-super").exists())

    def test_non_super_admin_can_still_create_an_ordinary_role(self):
        self.client.force_authenticate(user=self.attacker)
        resp = self.client.post(
            "/control/admin/roles/",
            {"name": "ordinary-role", "is_super_role": False},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_real_super_admin_can_still_create_a_super_role(self):
        super_admin = _make_user("real-super-2@test.com")
        _make_super_admin_role(super_admin)
        self.client.force_authenticate(user=super_admin)
        resp = self.client.post(
            "/control/admin/roles/",
            {"name": "legit-new-super-role", "is_super_role": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)


class RoleAssignmentEscalationTests(TestCase):
    """Vector 3: assigning an existing super role to yourself (or anyone)."""

    def setUp(self):
        self.client = APIClient()
        self.attacker = _make_user("assign-attacker@test.com")
        _make_scoped_admin_role(self.attacker, permissions=("roles.assign",))
        self.super_role = AdminRole.objects.create(name="pre-existing-super", is_super_role=True)
        self.ordinary_role = AdminRole.objects.create(name="pre-existing-ordinary", is_super_role=False)

    def test_non_super_admin_cannot_assign_a_super_role_to_self(self):
        self.client.force_authenticate(user=self.attacker)
        resp = self.client.post(
            "/control/admin/roles/assignments/",
            {"user": self.attacker.id, "role": self.super_role.id, "is_active": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.assertFalse(
            AdminRoleAssignment.objects.filter(user=self.attacker, role=self.super_role, is_active=True).exists()
        )

    def test_non_super_admin_can_still_assign_an_ordinary_role(self):
        victim = _make_user("some-user@test.com")
        self.client.force_authenticate(user=self.attacker)
        resp = self.client.post(
            "/control/admin/roles/assignments/",
            {"user": victim.id, "role": self.ordinary_role.id, "is_active": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_non_super_admin_cannot_reactivate_an_inactive_super_assignment(self):
        dormant = AdminRoleAssignment.objects.create(user=self.attacker, role=self.super_role, is_active=False)
        self.client.force_authenticate(user=self.attacker)
        resp = self.client.patch(
            f"/control/admin/roles/assignments/{dormant.id}/",
            {"is_active": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        dormant.refresh_from_db()
        self.assertFalse(dormant.is_active)

    def test_real_super_admin_can_still_assign_a_super_role(self):
        super_admin = _make_user("real-super-3@test.com")
        _make_super_admin_role(super_admin)
        victim = _make_user("promotable-user@test.com")
        self.client.force_authenticate(user=super_admin)
        resp = self.client.post(
            "/control/admin/roles/assignments/",
            {"user": victim.id, "role": self.super_role.id, "is_active": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
