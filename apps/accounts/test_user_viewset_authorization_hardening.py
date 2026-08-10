"""
Phase 8: UserViewSet previously had permission_classes =
IsAuthenticatedOrReadOnly with no object-level check at all, combined with
UserSerializer built via exclude=(...) (everything except password/
is_superuser/is_staff/user_permissions/groups). That meant:
  - ANY anonymous request could read every user's email, full phone
    number, verification detail, and preferences via list/retrieve/search.
  - ANY authenticated user could PATCH/DELETE any OTHER user's record —
    including tier and status, which weren't read-only, making this a
    free self-service tier upgrade / account-tampering path bypassing
    apps.billing (Phase 3) entirely.

Now: authentication is required for everything; only the owner or staff
ever see the full UserSerializer (everyone else gets PublicUserSerializer,
which excludes email/phone/verification/preferences); only the owner or
staff may write; tier/status/is_active are read-only on the full
serializer too, so even the owner can't self-grant a tier via this path.

Run:
  python3 manage.py test apps.accounts.test_user_viewset_authorization_hardening --keepdb -v 2
"""
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Device, User
from .views import issue_tokens_for_user

DEVICE_A = "uvs-hardening-device-a"
DEVICE_B = "uvs-hardening-device-b"
DEVICE_STAFF = "uvs-hardening-device-staff"


def _make_active_user(phone: str, display_name: str = "", is_staff: bool = False) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.verification = {"phone": {"verified": True}}
    user.status = "active"
    user.is_active = True
    user.is_staff = is_staff
    user.email = f"{phone.lstrip('+')}@example.com"
    if display_name:
        user.display_name = display_name
    user.save(update_fields=["verification", "status", "is_active", "is_staff", "email", "display_name"])
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
class AnonymousAccessDeniedTests(TestCase):
    def setUp(self):
        self.victim = _make_active_user("+237699800001", display_name="Victim User")
        self.anon = APIClient()

    def test_anonymous_list_is_denied(self):
        res = self.anon.get("/api/v1/users/")
        self.assertEqual(res.status_code, 401)

    def test_anonymous_retrieve_is_denied(self):
        res = self.anon.get(f"/api/v1/users/{self.victim.id}/")
        self.assertEqual(res.status_code, 401)

    def test_anonymous_write_is_denied(self):
        res = self.anon.patch(f"/api/v1/users/{self.victim.id}/", {"display_name": "Hacked"}, format="json")
        self.assertEqual(res.status_code, 401)


@override_settings(SECURE_SSL_REDIRECT=False)
class CrossUserReadIsRestrictedToSafeFieldsTests(TestCase):
    def setUp(self):
        self.victim = _make_active_user("+237699800002", display_name="Victim Two")
        self.attacker = _make_active_user("+237699800003", display_name="Attacker")
        self.attacker_client = _auth_client(self.attacker, DEVICE_A)

    def test_retrieving_another_user_omits_email_and_phone(self):
        res = self.attacker_client.get(f"/api/v1/users/{self.victim.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("email", res.data)
        self.assertNotIn("phone", res.data)
        self.assertNotIn("phone_number", res.data)
        self.assertNotIn("verification", res.data)
        self.assertNotIn("preferences", res.data)

    def test_retrieving_another_user_still_returns_safe_public_fields(self):
        res = self.attacker_client.get(f"/api/v1/users/{self.victim.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["display_name"], "Victim Two")
        self.assertIn("trust_score", res.data)

    def test_list_search_by_display_name_omits_email_and_phone_for_results(self):
        # Matches the existing, intentional ProfileDiscoverabilityTests
        # behavior — search must keep working for normal users, just with
        # safe fields only.
        res = self.attacker_client.get("/api/v1/users/?search=Victim")
        self.assertEqual(res.status_code, 200)
        rows = res.data.get("results", res.data)
        self.assertTrue(rows)
        self.assertNotIn("email", rows[0])
        self.assertNotIn("phone", rows[0])

    def test_retrieving_own_record_still_returns_full_fields(self):
        res = self.attacker_client.get(f"/api/v1/users/{self.attacker.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("email", res.data)
        self.assertEqual(res.data["email"], self.attacker.email)


@override_settings(SECURE_SSL_REDIRECT=False)
class CrossUserWriteIsBlockedTests(TestCase):
    def setUp(self):
        self.victim = _make_active_user("+237699800004", display_name="Victim Four")
        self.attacker = _make_active_user("+237699800005", display_name="Attacker Five")
        self.attacker_client = _auth_client(self.attacker, DEVICE_A)

    def test_cannot_patch_another_users_display_name(self):
        res = self.attacker_client.patch(
            f"/api/v1/users/{self.victim.id}/", {"display_name": "Pwned"}, format="json",
        )
        self.assertEqual(res.status_code, 403)
        self.victim.refresh_from_db()
        self.assertEqual(self.victim.display_name, "Victim Four")

    def test_cannot_self_upgrade_tier_via_patch_on_own_record(self):
        # THE core bypass: tier must never be settable through this
        # generic serializer, even for the owner's own record — real tier
        # changes go exclusively through apps.billing.apply_tier_upgrade.
        original_tier = self.attacker.tier
        res = self.attacker_client.patch(
            f"/api/v1/users/{self.attacker.id}/", {"tier": "Partner Pro"}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.attacker.refresh_from_db()
        self.assertEqual(self.attacker.tier, original_tier)

    def test_cannot_self_unsuspend_via_patch(self):
        self.attacker.status = "suspended"
        self.attacker.save(update_fields=["status"])
        res = self.attacker_client.patch(
            f"/api/v1/users/{self.attacker.id}/", {"status": "active"}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.attacker.refresh_from_db()
        self.assertEqual(self.attacker.status, "suspended")

    def test_cannot_delete_another_users_account(self):
        res = self.attacker_client.delete(f"/api/v1/users/{self.victim.id}/")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(User.objects.filter(id=self.victim.id).exists())

    def test_can_update_own_display_name(self):
        res = self.attacker_client.patch(
            f"/api/v1/users/{self.attacker.id}/", {"display_name": "New Name"}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.attacker.refresh_from_db()
        self.assertEqual(self.attacker.display_name, "New Name")

    def test_non_staff_cannot_create_a_user_via_this_endpoint(self):
        res = self.attacker_client.post("/api/v1/users/", {
            "phone": "+237699800099", "phone_country_code": "+237", "phone_number": "699800099", "country": "CM",
        }, format="json")
        self.assertEqual(res.status_code, 403)


@override_settings(SECURE_SSL_REDIRECT=False)
class StaffRetainsFullAccessTests(TestCase):
    def setUp(self):
        self.victim = _make_active_user("+237699800006", display_name="Victim Six")
        self.staff = _make_active_user("+237699800007", display_name="Staff Member", is_staff=True)
        self.staff_client = _auth_client(self.staff, DEVICE_STAFF)

    def test_staff_retrieve_sees_full_fields(self):
        res = self.staff_client.get(f"/api/v1/users/{self.victim.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("email", res.data)

    def test_staff_can_patch_another_users_record(self):
        res = self.staff_client.patch(
            f"/api/v1/users/{self.victim.id}/", {"display_name": "Moderated"}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.victim.refresh_from_db()
        self.assertEqual(self.victim.display_name, "Moderated")

    def test_staff_list_gets_full_serializer(self):
        res = self.staff_client.get("/api/v1/users/?search=Victim")
        self.assertEqual(res.status_code, 200)
        rows = res.data.get("results", res.data)
        self.assertTrue(rows)
        self.assertIn("email", rows[0])
