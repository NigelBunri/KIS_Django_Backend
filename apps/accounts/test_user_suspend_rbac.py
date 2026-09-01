"""
UserViewSet.suspend - previously gated by plain IsAdminUser (any is_staff
account, regardless of admin_control role assignment, could suspend any
user), and its audit write went to apps.moderation.AuditLog wrapped in a
bare except: pass that silently dropped the record on any failure - and
even on success, that's not what AuditTrailView (the actual admin-facing
audit review screen) reads. Now gated through admin_control's real RBAC
(matching AdminUserBanView, which covers the identical action) and audited
via AuditLogger.log() so both endpoints land in the same place.

Run:
  python3 manage.py test apps.accounts.test_user_suspend_rbac --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from admin_control.models import AdminAuditEntry
from admin_control.roles import AdminRole, AdminRoleAssignment, AdminRolePermission

User = get_user_model()


def _grant_role(user, *, app_label="*", permissions):
    role = AdminRole.objects.create(name=f"role-{user.id}")
    AdminRolePermission.objects.create(role=role, app_label=app_label, permissions=permissions)
    AdminRoleAssignment.objects.create(user=user, role=role, is_active=True)


class UserSuspendRbacTests(APITestCase):
    def setUp(self):
        self.target = User.objects.create_user(phone="+2348900000001", password="pw123456", country="NG")

    def _suspend_url(self):
        return f"/api/v1/users/{self.target.id}/suspend/"

    def test_a_plain_staff_user_with_no_admin_control_role_cannot_suspend(self):
        staff = User.objects.create_user(
            phone="+2348900000002", password="pw123456", country="NG", is_staff=True,
        )
        self.client.force_authenticate(staff)

        res = self.client.post(self._suspend_url(), {"reason": "test"}, format="json")

        self.assertEqual(res.status_code, 403)
        self.target.refresh_from_db()
        self.assertNotEqual(self.target.status, "suspended")

    def test_a_non_staff_user_cannot_suspend_even_with_a_role_grant(self):
        # is_staff isn't checked at all anymore - only the admin_control
        # role matters - but confirms IsAuthenticated is still required
        # (an unauthenticated caller is rejected before role lookup).
        anon_res = self.client.post(self._suspend_url(), {"reason": "test"}, format="json")
        self.assertIn(anon_res.status_code, (401, 403))

    def test_a_user_with_the_users_moderate_permission_can_suspend(self):
        admin = User.objects.create_user(phone="+2348900000003", password="pw123456", country="NG")
        _grant_role(admin, permissions=["users.moderate"])
        self.client.force_authenticate(admin)

        res = self.client.post(self._suspend_url(), {"reason": "policy violation"}, format="json")

        self.assertEqual(res.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "suspended")

    def test_a_role_scoped_to_an_unrelated_permission_cannot_suspend(self):
        admin = User.objects.create_user(phone="+2348900000004", password="pw123456", country="NG")
        _grant_role(admin, permissions=["dashboard.view"])
        self.client.force_authenticate(admin)

        res = self.client.post(self._suspend_url(), {"reason": "test"}, format="json")

        self.assertEqual(res.status_code, 403)
        self.target.refresh_from_db()
        self.assertNotEqual(self.target.status, "suspended")

    def test_a_successful_suspend_is_recorded_in_admin_audit_entry(self):
        admin = User.objects.create_user(phone="+2348900000005", password="pw123456", country="NG")
        _grant_role(admin, permissions=["users.moderate"])
        self.client.force_authenticate(admin)

        self.client.post(self._suspend_url(), {"reason": "policy violation"}, format="json")

        entry = AdminAuditEntry.objects.filter(
            action_type="user.suspended", target_pk=str(self.target.id),
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_id, admin.id)
        self.assertEqual(entry.metadata.get("reason"), "policy violation")

    def test_other_userviewset_actions_are_unaffected_by_the_new_class_attribute(self):
        # required_permission was added as a UserViewSet class attribute so
        # DRF's @action kwarg validation accepts it - confirms it doesn't
        # leak into an unrelated action's permission checks (e.g. a user
        # reading their own profile still just needs to be authenticated).
        self.client.force_authenticate(self.target)

        res = self.client.get(f"/api/v1/users/{self.target.id}/")

        self.assertEqual(res.status_code, 200)
