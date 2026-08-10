"""
Phase 8: AccountTierViewSet previously had IsAuthenticatedOrReadOnly — any
authenticated user could write here, even though AccountTier rows are
shared, platform-wide pricing/feature definitions (not per-user data).
That meant any registered account could rewrite another tier's
price_cents/features_json/rank, affecting the whole platform's billing.

Run:
  python3 manage.py test apps.accounts.test_account_tier_viewset_admin_write_hardening --keepdb -v 2
"""
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AccountTier, Device, User
from .views import issue_tokens_for_user

DEVICE_A = "tier-hardening-device-a"
DEVICE_STAFF = "tier-hardening-device-staff"


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
class AccountTierWriteRestrictedToStaffTests(TestCase):
    def setUp(self):
        # public_account_tiers_qs() (the base get_queryset for this
        # viewset) only includes the canonical preset tier names — an
        # arbitrary name would 404 even for staff, unrelated to the
        # permission fix under test here.
        self.tier, _ = AccountTier.objects.get_or_create(
            name="Pro", defaults={"price_cents": 1000, "rank": 1},
        )
        self.regular = _make_active_user("+237699820001")
        self.staff = _make_active_user("+237699820002", is_staff=True)
        self.regular_client = _auth_client(self.regular, DEVICE_A)
        self.staff_client = _auth_client(self.staff, DEVICE_STAFF)

    def test_anonymous_read_is_still_allowed(self):
        anon = APIClient()
        res = anon.get("/api/v1/tiers/")
        self.assertEqual(res.status_code, 200)

    def test_non_staff_cannot_patch_a_tiers_price(self):
        res = self.regular_client.patch(
            f"/api/v1/tiers/{self.tier.id}/", {"price_cents": 0}, format="json",
        )
        self.assertEqual(res.status_code, 403)
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.price_cents, 1000)

    def test_non_staff_cannot_delete_a_tier(self):
        res = self.regular_client.delete(f"/api/v1/tiers/{self.tier.id}/")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(AccountTier.objects.filter(id=self.tier.id).exists())

    def test_non_staff_cannot_create_a_tier(self):
        res = self.regular_client.post("/api/v1/tiers/", {"name": "Rogue Tier", "price_cents": 0}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_staff_can_patch_a_tiers_price(self):
        res = self.staff_client.patch(
            f"/api/v1/tiers/{self.tier.id}/", {"price_cents": 2000}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.price_cents, 2000)
