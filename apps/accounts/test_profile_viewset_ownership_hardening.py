"""
Phase 8: ProfileViewSet's base update/partial_update/destroy previously had
IsAuthenticatedOrReadOnly with no object-level check — any authenticated
user could PATCH or DELETE another user's Profile (bio, headline,
industry, visibility, avatar/cover) via /api/v1/profiles/<id>/. The
dedicated me/view/discover/set_open_to_work actions already had their own
explicit permission_classes and are unaffected by this fix.

Run:
  python3 manage.py test apps.accounts.test_profile_viewset_ownership_hardening --keepdb -v 2
"""
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Device, Profile, User
from .views import issue_tokens_for_user

DEVICE_A = "profile-hardening-device-a"
DEVICE_STAFF = "profile-hardening-device-staff"


def _make_active_user(phone: str, is_staff: bool = False) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.verification = {"phone": {"verified": True}}
    user.status = "active"
    user.is_active = True
    user.is_staff = is_staff
    user.save(update_fields=["verification", "status", "is_active", "is_staff"])
    return user


def _auth_client(user: User, device_id: str) -> APIClient:
    tokens = issue_tokens_for_user(user, device_id=device_id)
    Device.objects.get_or_create(
        user=user, device_id=device_id,
        defaults={"platform": "android", "is_parent": True, "token_version": 1, "last_seen_at": timezone.now()},
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID=device_id)
    return client


@override_settings(SECURE_SSL_REDIRECT=False)
class ProfileWriteOwnershipTests(TestCase):
    def setUp(self):
        self.victim = _make_active_user("+237699810001")
        self.victim_profile, _ = Profile.objects.get_or_create(user=self.victim, defaults={"bio": "Original bio"})
        if not self.victim_profile.bio:
            self.victim_profile.bio = "Original bio"
            self.victim_profile.save(update_fields=["bio"])

        self.attacker = _make_active_user("+237699810002")
        self.attacker_client = _auth_client(self.attacker, DEVICE_A)

    def test_cannot_patch_another_users_profile(self):
        res = self.attacker_client.patch(
            f"/api/v1/profiles/{self.victim_profile.id}/", {"bio": "Vandalized"}, format="json",
        )
        self.assertEqual(res.status_code, 403)
        self.victim_profile.refresh_from_db()
        self.assertEqual(self.victim_profile.bio, "Original bio")

    def test_cannot_delete_another_users_profile(self):
        res = self.attacker_client.delete(f"/api/v1/profiles/{self.victim_profile.id}/")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Profile.objects.filter(id=self.victim_profile.id).exists())

    def test_can_patch_own_profile(self):
        own_profile, _ = Profile.objects.get_or_create(user=self.attacker)
        res = self.attacker_client.patch(
            f"/api/v1/profiles/{own_profile.id}/", {"bio": "My new bio"}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        own_profile.refresh_from_db()
        self.assertEqual(own_profile.bio, "My new bio")

    def test_list_and_retrieve_remain_accessible_to_any_authenticated_user(self):
        res = self.attacker_client.get(f"/api/v1/profiles/{self.victim_profile.id}/")
        self.assertEqual(res.status_code, 200)
        res_list = self.attacker_client.get("/api/v1/profiles/")
        self.assertEqual(res_list.status_code, 200)

    def test_anonymous_access_is_denied(self):
        anon = APIClient()
        res = anon.get(f"/api/v1/profiles/{self.victim_profile.id}/")
        self.assertEqual(res.status_code, 401)


@override_settings(SECURE_SSL_REDIRECT=False)
class ProfileStaffWriteAccessTests(TestCase):
    def setUp(self):
        self.victim = _make_active_user("+237699810003")
        self.victim_profile, _ = Profile.objects.get_or_create(user=self.victim)
        self.staff = _make_active_user("+237699810004", is_staff=True)
        self.staff_client = _auth_client(self.staff, DEVICE_STAFF)

    def test_staff_can_patch_another_users_profile(self):
        res = self.staff_client.patch(
            f"/api/v1/profiles/{self.victim_profile.id}/", {"bio": "Moderated"}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.victim_profile.refresh_from_db()
        self.assertEqual(self.victim_profile.bio, "Moderated")
