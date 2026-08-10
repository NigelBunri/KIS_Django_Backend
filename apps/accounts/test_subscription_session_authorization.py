"""
Regression tests for the SubscriptionViewSet / SessionViewSet authorization
fix. Both viewsets previously used an unrestricted queryset combined with
IsAuthenticatedOrReadOnly — any authenticated user could list, and DRF's
default handlers would permit modifying, every user's subscription/session
record via /api/v1/subscriptions/ and /api/v1/sessions/.

Run:
  python3 manage.py test apps.accounts.test_subscription_session_authorization --keepdb -v 2
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import AccountTier, Session, Subscription, User


@override_settings(SECURE_SSL_REDIRECT=False)
class SubscriptionAuthorizationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            phone="+237699000101", country="CM", password="pass1234",
        )
        self.other = User.objects.create_user(
            phone="+237699000102", country="CM", password="pass1234",
        )
        self.staff = User.objects.create_user(
            phone="+237699000103", country="CM", password="pass1234", is_staff=True,
        )
        self.tier, _ = AccountTier.objects.get_or_create(
            name="Phase1AuthTestTier", defaults={"price_cents": 1000, "features_json": {}},
        )
        self.owner_sub = Subscription.objects.create(
            user=self.owner, tier=self.tier, status="active",
            started_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=29),
            billing_meta={"source": "flutterwave", "provider_ref": "secret-ref-123"},
        )
        self.other_sub = Subscription.objects.create(
            user=self.other, tier=self.tier, status="active",
            started_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=29),
        )
        self.owner_client = APIClient()
        self.owner_client.force_authenticate(self.owner)
        self.other_client = APIClient()
        self.other_client.force_authenticate(self.other)
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.staff)
        self.anon_client = APIClient()

    # ---- list ----
    def test_list_returns_only_own_subscription(self):
        res = self.owner_client.get("/api/v1/subscriptions/")
        self.assertEqual(res.status_code, 200)
        ids = [row["id"] for row in res.data.get("results", res.data)]
        self.assertIn(str(self.owner_sub.id), ids)
        self.assertNotIn(str(self.other_sub.id), ids)

    def test_list_excludes_billing_meta_for_normal_user(self):
        res = self.owner_client.get("/api/v1/subscriptions/")
        self.assertEqual(res.status_code, 200)
        rows = res.data.get("results", res.data)
        self.assertTrue(rows)
        self.assertNotIn("billing_meta", rows[0])

    def test_staff_list_sees_all_subscriptions(self):
        res = self.staff_client.get("/api/v1/subscriptions/")
        self.assertEqual(res.status_code, 200)
        ids = [row["id"] for row in res.data.get("results", res.data)]
        self.assertIn(str(self.owner_sub.id), ids)
        self.assertIn(str(self.other_sub.id), ids)

    def test_anonymous_list_denied(self):
        res = self.anon_client.get("/api/v1/subscriptions/")
        self.assertEqual(res.status_code, 401)

    # ---- retrieve ----
    def test_retrieve_own_subscription_succeeds(self):
        res = self.owner_client.get(f"/api/v1/subscriptions/{self.owner_sub.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["id"], str(self.owner_sub.id))

    def test_retrieve_other_users_subscription_returns_404(self):
        """Queryset-filtered — a cross-user id must not even reveal existence."""
        res = self.owner_client.get(f"/api/v1/subscriptions/{self.other_sub.id}/")
        self.assertEqual(res.status_code, 404)

    def test_staff_retrieve_any_subscription_succeeds(self):
        res = self.staff_client.get(f"/api/v1/subscriptions/{self.other_sub.id}/")
        self.assertEqual(res.status_code, 200)

    # ---- create ----
    def test_normal_user_create_denied(self):
        res = self.owner_client.post("/api/v1/subscriptions/", {
            "user": str(self.owner.id), "tier": str(self.tier.id), "status": "active",
            "started_at": timezone.now().isoformat(), "ends_at": (timezone.now() + timedelta(days=30)).isoformat(),
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_normal_user_cannot_create_subscription_for_another_user(self):
        res = self.owner_client.post("/api/v1/subscriptions/", {
            "user": str(self.other.id), "tier": str(self.tier.id), "status": "active",
            "started_at": timezone.now().isoformat(), "ends_at": (timezone.now() + timedelta(days=30)).isoformat(),
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_create_allowed(self):
        # Must target a user with no existing active subscription — the
        # owner/other fixtures already have one, and creating a second
        # would now correctly collide with uniq_active_subscription_per_user.
        fresh_user = User.objects.create_user(
            phone="+237699000109", country="CM", password="pass1234",
        )
        res = self.staff_client.post("/api/v1/subscriptions/", {
            "user": str(fresh_user.id), "tier": str(self.tier.id), "status": "active",
            "started_at": timezone.now().isoformat(), "ends_at": (timezone.now() + timedelta(days=30)).isoformat(),
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_staff_create_for_user_with_existing_active_subscription_returns_400_not_500(self):
        res = self.staff_client.post("/api/v1/subscriptions/", {
            "user": str(self.owner.id), "tier": str(self.tier.id), "status": "active",
            "started_at": timezone.now().isoformat(), "ends_at": (timezone.now() + timedelta(days=30)).isoformat(),
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ---- update / partial_update ----
    def test_normal_user_update_own_subscription_denied(self):
        res = self.owner_client.put(f"/api/v1/subscriptions/{self.owner_sub.id}/", {
            "user": str(self.owner.id), "tier": str(self.tier.id), "status": "cancelled",
            "started_at": self.owner_sub.started_at.isoformat(), "ends_at": self.owner_sub.ends_at.isoformat(),
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_normal_user_partial_update_own_subscription_denied(self):
        res = self.owner_client.patch(
            f"/api/v1/subscriptions/{self.owner_sub.id}/", {"status": "cancelled"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.owner_sub.refresh_from_db()
        self.assertEqual(self.owner_sub.status, "active")

    def test_normal_user_cannot_alter_other_users_subscription(self):
        res = self.owner_client.patch(
            f"/api/v1/subscriptions/{self.other_sub.id}/", {"status": "cancelled"}, format="json",
        )
        self.assertIn(res.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))
        self.other_sub.refresh_from_db()
        self.assertEqual(self.other_sub.status, "active")

    def test_staff_partial_update_allowed(self):
        res = self.staff_client.patch(
            f"/api/v1/subscriptions/{self.owner_sub.id}/", {"status": "cancelled"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    # ---- delete ----
    def test_normal_user_delete_own_subscription_denied(self):
        res = self.owner_client.delete(f"/api/v1/subscriptions/{self.owner_sub.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Subscription.objects.filter(id=self.owner_sub.id).exists())

    def test_normal_user_delete_other_users_subscription_denied(self):
        res = self.owner_client.delete(f"/api/v1/subscriptions/{self.other_sub.id}/")
        self.assertIn(res.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))
        self.assertTrue(Subscription.objects.filter(id=self.other_sub.id).exists())

    def test_staff_delete_allowed(self):
        res = self.staff_client.delete(f"/api/v1/subscriptions/{self.other_sub.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Subscription.objects.filter(id=self.other_sub.id).exists())


@override_settings(SECURE_SSL_REDIRECT=False)
class SessionAuthorizationTests(TestCase):
    """Session shared the exact same unrestricted-queryset bug as
    Subscription. The model is currently never populated by any real code
    path in the app (login uses Device, not this model) — these tests lock
    down the same fix regardless, since the endpoint is live and exposed."""

    def setUp(self):
        self.owner = User.objects.create_user(phone="+237699000201", country="CM", password="pass1234")
        self.other = User.objects.create_user(phone="+237699000202", country="CM", password="pass1234")
        self.staff = User.objects.create_user(
            phone="+237699000203", country="CM", password="pass1234", is_staff=True,
        )
        self.owner_session = Session.objects.create(
            user=self.owner, expires_at=timezone.now() + timedelta(days=1),
            ip_address="10.0.0.1", user_agent="owner-agent",
        )
        self.other_session = Session.objects.create(
            user=self.other, expires_at=timezone.now() + timedelta(days=1),
            ip_address="10.0.0.2", user_agent="other-agent",
        )
        self.owner_client = APIClient()
        self.owner_client.force_authenticate(self.owner)
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.staff)

    def test_list_returns_only_own_session(self):
        res = self.owner_client.get("/api/v1/sessions/")
        self.assertEqual(res.status_code, 200)
        ids = [row["id"] for row in res.data.get("results", res.data)]
        self.assertIn(str(self.owner_session.id), ids)
        self.assertNotIn(str(self.other_session.id), ids)

    def test_retrieve_other_users_session_returns_404(self):
        res = self.owner_client.get(f"/api/v1/sessions/{self.other_session.id}/")
        self.assertEqual(res.status_code, 404)

    def test_normal_user_create_denied(self):
        res = self.owner_client.post("/api/v1/sessions/", {
            "user": str(self.owner.id), "expires_at": (timezone.now() + timedelta(days=1)).isoformat(),
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_normal_user_delete_other_users_session_denied(self):
        res = self.owner_client.delete(f"/api/v1/sessions/{self.other_session.id}/")
        self.assertIn(res.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))
        self.assertTrue(Session.objects.filter(id=self.other_session.id).exists())

    def test_staff_list_sees_all_sessions(self):
        res = self.staff_client.get("/api/v1/sessions/")
        self.assertEqual(res.status_code, 200)
        ids = [row["id"] for row in res.data.get("results", res.data)]
        self.assertIn(str(self.owner_session.id), ids)
        self.assertIn(str(self.other_session.id), ids)
