"""
Family app permission tests.

Previously FamilyMemberViewSet/ParentalControlViewSet.get_queryset() only
checked "is this user a member of the family," not "is this user the
parent/admin" - so any family member, including one flagged
is_minor=True, could PATCH their own or another member's role/is_admin/
is_minor, or write ParentalControl (content filter level, allowed
contacts, screen time) on anyone in the family via the standard REST API.
See _user_is_family_guardian / _assert_guardian_for_privileged_write in
views.py.

Run:
  python3 manage.py test apps.family.tests --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import FamilyAccount, FamilyMember, MemberRole, ParentalControl

User = get_user_model()

MEMBERS_URL = "/api/v1/family/members/"
PARENTAL_CONTROLS_URL = "/api/v1/family/parental-controls/"


class FamilyPermissionTestsBase(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(phone="+2348600000001", password="pw123456", country="NG")
        self.parent = User.objects.create_user(phone="+2348600000002", password="pw123456", country="NG")
        self.child = User.objects.create_user(phone="+2348600000003", password="pw123456", country="NG")
        self.other_member = User.objects.create_user(phone="+2348600000004", password="pw123456", country="NG")

        self.family = FamilyAccount.objects.create(
            admin_user=self.admin, name="Test Family", invite_code="INV12345",
        )
        self.parent_member = FamilyMember.objects.create(
            family=self.family, user=self.parent, role=MemberRole.PARENT, added_by=self.admin,
        )
        self.child_member = FamilyMember.objects.create(
            family=self.family, user=self.child, role=MemberRole.CHILD, is_minor=True, added_by=self.admin,
        )
        self.other_member_row = FamilyMember.objects.create(
            family=self.family, user=self.other_member, role=MemberRole.EXTENDED, added_by=self.admin,
        )


class FamilyMemberPrivilegedFieldTests(FamilyPermissionTestsBase):
    def test_a_minor_cannot_unflag_themselves_as_a_minor(self):
        self.client.force_authenticate(self.child)

        res = self.client.patch(
            f"{MEMBERS_URL}{self.child_member.id}/", {"is_minor": False}, format="json",
        )

        self.assertEqual(res.status_code, 403)
        self.child_member.refresh_from_db()
        self.assertTrue(self.child_member.is_minor)

    def test_an_ordinary_member_cannot_grant_themselves_admin(self):
        self.client.force_authenticate(self.other_member)

        res = self.client.patch(
            f"{MEMBERS_URL}{self.other_member_row.id}/", {"is_admin": True}, format="json",
        )

        self.assertEqual(res.status_code, 403)
        self.other_member_row.refresh_from_db()
        self.assertFalse(self.other_member_row.is_admin)

    def test_an_ordinary_member_cannot_edit_another_members_row_at_all(self):
        self.client.force_authenticate(self.other_member)

        res = self.client.patch(
            f"{MEMBERS_URL}{self.child_member.id}/", {"nickname": "renamed"}, format="json",
        )

        self.assertEqual(res.status_code, 403)

    def test_a_member_can_still_edit_their_own_nickname(self):
        self.client.force_authenticate(self.other_member)

        res = self.client.patch(
            f"{MEMBERS_URL}{self.other_member_row.id}/", {"nickname": "New Nickname"}, format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.other_member_row.refresh_from_db()
        self.assertEqual(self.other_member_row.nickname, "New Nickname")

    def test_the_family_admin_can_change_a_members_minor_status(self):
        self.client.force_authenticate(self.admin)

        res = self.client.patch(
            f"{MEMBERS_URL}{self.child_member.id}/", {"is_minor": False}, format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.child_member.refresh_from_db()
        self.assertFalse(self.child_member.is_minor)

    def test_a_parent_role_member_can_change_another_members_role(self):
        self.client.force_authenticate(self.parent)

        res = self.client.patch(
            f"{MEMBERS_URL}{self.other_member_row.id}/", {"is_admin": True}, format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.other_member_row.refresh_from_db()
        self.assertTrue(self.other_member_row.is_admin)

    def test_an_ordinary_member_cannot_create_a_new_member_with_admin_rights(self):
        self.client.force_authenticate(self.other_member)
        newcomer = User.objects.create_user(phone="+2348600000005", password="pw123456", country="NG")

        res = self.client.post(
            MEMBERS_URL,
            {"family": str(self.family.id), "user": str(newcomer.id), "role": "extended", "is_admin": True},
            format="json",
        )

        self.assertEqual(res.status_code, 403)
        self.assertFalse(FamilyMember.objects.filter(user=newcomer).exists())

    def test_an_ordinary_member_cannot_create_a_new_member_with_parent_role(self):
        self.client.force_authenticate(self.other_member)
        newcomer = User.objects.create_user(phone="+2348600000006", password="pw123456", country="NG")

        res = self.client.post(
            MEMBERS_URL,
            {"family": str(self.family.id), "user": str(newcomer.id), "role": "parent"},
            format="json",
        )

        self.assertEqual(res.status_code, 403)

    def test_an_ordinary_member_can_still_add_a_non_privileged_relative(self):
        self.client.force_authenticate(self.other_member)
        newcomer = User.objects.create_user(phone="+2348600000007", password="pw123456", country="NG")

        res = self.client.post(
            MEMBERS_URL,
            {"family": str(self.family.id), "user": str(newcomer.id), "role": "extended"},
            format="json",
        )

        self.assertEqual(res.status_code, 201)


class ParentalControlPermissionTests(FamilyPermissionTestsBase):
    def test_a_minor_cannot_create_their_own_parental_control_row(self):
        self.client.force_authenticate(self.child)

        res = self.client.post(
            PARENTAL_CONTROLS_URL,
            {"family_member": str(self.child_member.id), "content_filter_level": "adult"},
            format="json",
        )

        self.assertEqual(res.status_code, 403)
        self.assertFalse(ParentalControl.objects.filter(family_member=self.child_member).exists())

    def test_the_admin_can_create_a_parental_control_row_for_the_child(self):
        self.client.force_authenticate(self.admin)

        res = self.client.post(
            PARENTAL_CONTROLS_URL,
            {"family_member": str(self.child_member.id), "content_filter_level": "child"},
            format="json",
        )

        self.assertEqual(res.status_code, 201)

    def test_the_restricted_child_cannot_weaken_their_own_content_filter(self):
        control = ParentalControl.objects.create(
            family_member=self.child_member, content_filter_level="child", screen_time_minutes_per_day=60,
        )
        self.client.force_authenticate(self.child)

        res = self.client.patch(
            f"{PARENTAL_CONTROLS_URL}{control.id}/",
            {"content_filter_level": "adult", "screen_time_minutes_per_day": 600},
            format="json",
        )

        self.assertEqual(res.status_code, 403)
        control.refresh_from_db()
        self.assertEqual(control.content_filter_level, "child")
        self.assertEqual(control.screen_time_minutes_per_day, 60)

    def test_a_parent_role_member_can_update_the_content_filter(self):
        control = ParentalControl.objects.create(family_member=self.child_member, content_filter_level="child")
        self.client.force_authenticate(self.parent)

        res = self.client.patch(
            f"{PARENTAL_CONTROLS_URL}{control.id}/", {"content_filter_level": "youth"}, format="json",
        )

        self.assertEqual(res.status_code, 200)
        control.refresh_from_db()
        self.assertEqual(control.content_filter_level, "youth")

    def test_the_restricted_child_cannot_delete_their_own_parental_control_row(self):
        control = ParentalControl.objects.create(family_member=self.child_member, content_filter_level="child")
        self.client.force_authenticate(self.child)

        res = self.client.delete(f"{PARENTAL_CONTROLS_URL}{control.id}/")

        self.assertEqual(res.status_code, 403)
        self.assertTrue(ParentalControl.objects.filter(id=control.id).exists())

    def test_any_family_member_can_still_read_parental_controls(self):
        ParentalControl.objects.create(family_member=self.child_member, content_filter_level="child")
        self.client.force_authenticate(self.other_member)

        res = self.client.get(PARENTAL_CONTROLS_URL)

        self.assertEqual(res.status_code, 200)


SOS_URL = "/api/v1/family/sos/"


class SOSAlertPermissionTests(FamilyPermissionTestsBase):
    """
    SOSAlertView previously had no check that the caller belongs to the
    named family at all - any authenticated user could fire a real-looking
    SOS alert at an arbitrary family's parents/guardians by supplying its
    family_id. See the SECURITY comment in views.py.
    """

    def test_a_stranger_cannot_trigger_an_sos_alert_for_a_family_they_are_not_in(self):
        stranger = User.objects.create_user(phone="+2348600000008", password="pw123456", country="NG")
        self.client.force_authenticate(stranger)

        res = self.client.post(SOS_URL, {"family_id": str(self.family.id), "message": "help"}, format="json")

        self.assertEqual(res.status_code, 404)

    def test_a_family_member_can_trigger_an_sos_alert_for_their_own_family(self):
        self.client.force_authenticate(self.child)

        res = self.client.post(SOS_URL, {"family_id": str(self.family.id), "message": "help"}, format="json")

        self.assertEqual(res.status_code, 200)

    def test_the_family_admin_can_trigger_an_sos_alert_even_without_a_membership_row(self):
        self.client.force_authenticate(self.admin)

        res = self.client.post(SOS_URL, {"family_id": str(self.family.id), "message": "help"}, format="json")

        self.assertEqual(res.status_code, 200)

    def test_a_nonexistent_family_id_returns_the_same_404_as_not_a_member(self):
        self.client.force_authenticate(self.child)

        res = self.client.post(
            SOS_URL, {"family_id": "00000000-0000-0000-0000-000000000000", "message": "help"}, format="json",
        )

        self.assertEqual(res.status_code, 404)
