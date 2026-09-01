"""
AdminUnsupervisedMinorsListView - wires the previously-dead
User.is_under_13 property into a real, RBAC-gated staff queue. See
admin_control/views/child_safety.py's module docstring for the honest
scope (an engineering recommendation, not a compliance claim).

Run:
  python3 manage.py test admin_control.test_child_safety --keepdb -v 2
"""
from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from admin_control.roles import AdminRole, AdminRoleAssignment, AdminRolePermission
from apps.family.models import FamilyAccount, FamilyMember, MemberRole

User = get_user_model()

URL = "/control/admin/child-safety/unsupervised-minors/"


def _grant_role(user, *, permissions):
    role = AdminRole.objects.create(name=f"role-{user.id}")
    AdminRolePermission.objects.create(role=role, app_label="*", permissions=permissions)
    AdminRoleAssignment.objects.create(user=user, role=role, is_active=True)


def _dob_for_age(years: int) -> datetime.date:
    today = timezone.now().date()
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


class AccessControlTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_a_user_without_child_safety_permission_cannot_list(self):
        staff = User.objects.create_user(phone="+2349200000001", password="pw123456", country="NG", is_staff=True)
        self.client.force_authenticate(staff)

        res = self.client.get(URL)

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_user_with_child_safety_review_permission_can_list(self):
        admin = User.objects.create_user(phone="+2349200000002", password="pw123456", country="NG")
        _grant_role(admin, permissions=["child_safety.review"])
        self.client.force_authenticate(admin)

        res = self.client.get(URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)


class UnsupervisedMinorsQueueTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(phone="+2349200000003", password="pw123456", country="NG")
        _grant_role(self.admin, permissions=["child_safety.review"])
        self.client.force_authenticate(self.admin)

    def test_under_13_user_with_no_dob_is_not_listed(self):
        User.objects.create_user(phone="+2349200000010", password="pw123456", country="NG")

        res = self.client.get(URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 0)

    def test_under_13_user_with_no_guardian_link_is_listed(self):
        child = User.objects.create_user(
            phone="+2349200000011", password="pw123456", country="NG",
            date_of_birth=_dob_for_age(10),
        )

        res = self.client.get(URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 1)
        self.assertEqual(res.data["results"][0]["id"], str(child.id))
        self.assertEqual(res.data["results"][0]["age"], 10)

    def test_under_13_user_with_an_active_guardian_link_is_not_listed(self):
        guardian = User.objects.create_user(phone="+2349200000012", password="pw123456", country="NG")
        child = User.objects.create_user(
            phone="+2349200000013", password="pw123456", country="NG",
            date_of_birth=_dob_for_age(9),
        )
        family = FamilyAccount.objects.create(admin_user=guardian, name="Test Family", invite_code="TESTCODE01")
        FamilyMember.objects.create(family=family, user=child, role=MemberRole.CHILD)

        res = self.client.get(URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 0)

    def test_under_13_user_whose_family_is_inactive_is_still_listed(self):
        guardian = User.objects.create_user(phone="+2349200000014", password="pw123456", country="NG")
        child = User.objects.create_user(
            phone="+2349200000015", password="pw123456", country="NG",
            date_of_birth=_dob_for_age(11),
        )
        family = FamilyAccount.objects.create(
            admin_user=guardian, name="Inactive Family", invite_code="TESTCODE02", is_active=False,
        )
        FamilyMember.objects.create(family=family, user=child, role=MemberRole.CHILD)

        res = self.client.get(URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 1)

    def test_13_year_old_is_not_listed(self):
        User.objects.create_user(
            phone="+2349200000016", password="pw123456", country="NG",
            date_of_birth=_dob_for_age(13),
        )

        res = self.client.get(URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 0)

    def test_adult_is_not_listed(self):
        User.objects.create_user(
            phone="+2349200000017", password="pw123456", country="NG",
            date_of_birth=_dob_for_age(30),
        )

        res = self.client.get(URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 0)
