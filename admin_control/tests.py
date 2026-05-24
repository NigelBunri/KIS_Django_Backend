"""Tests for KCAN admin control: superadmin setup, access control, user management, content moderation."""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.partners.models import Partner, PartnerMembership, PartnerOrganizationApp, PartnerOrganizationAppTab
from apps.moderation.models import Flag


_counter = 0


def _make_user(email, *, is_superuser=False, is_staff=False, tier="Free", **kw):
    global _counter
    _counter += 1
    phone = kw.pop("phone", f"+2376540{_counter:05d}")
    country = kw.pop("country", "CM")
    if is_superuser:
        user = User.objects.create_superuser(
            email=email, password="test1234!", phone=phone, country=country,
            is_staff=True, is_superuser=True, **kw,
        )
    else:
        user = User.objects.create_user(
            phone=phone, email=email, password="test1234!", country=country, **kw,
        )
    # Post-save signals may reset tier; force it here
    if user.tier != tier:
        User.objects.filter(id=user.id).update(tier=tier)
        user.tier = tier
    if is_staff and not user.is_staff:
        User.objects.filter(id=user.id).update(is_staff=True)
        user.is_staff = True
    return user


def _make_partner(slug, owner):
    return Partner.objects.create(slug=slug, name=slug.upper(), owner=owner, is_active=True)


def _make_admin_role(user):
    from admin_control.roles import AdminRole, AdminRolePermission, AdminRoleAssignment
    role, _ = AdminRole.objects.get_or_create(name="super_admin", defaults={"is_super_role": True})
    AdminRolePermission.objects.get_or_create(
        role=role,
        app_label="*",
        defaults={"permissions": ["*"]},
    )
    AdminRoleAssignment.objects.get_or_create(user=user, role=role, defaults={"is_active": True})


# ─── Setup KCAN superadmin management command ─────────────────────────────────

class KcanSuperadminCommandTests(TestCase):
    def test_command_creates_superuser_and_kcan(self):
        out = StringIO()
        call_command("setup_kcan_superadmin", "--password", "testpass123!", stdout=out)
        user = User.objects.filter(email="nigelbunribah@gmail.com").first()
        self.assertIsNotNone(user, "Superadmin user was not created.")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertEqual(user.tier, "Partner Pro")

        partner = Partner.objects.filter(slug="kcan").first()
        self.assertIsNotNone(partner, "KCAN partner was not created.")
        self.assertEqual(str(partner.owner_id), str(user.id))

        membership = PartnerMembership.objects.filter(partner=partner, user=user).first()
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role, "admin")

    def test_command_is_idempotent(self):
        out = StringIO()
        call_command("setup_kcan_superadmin", "--password", "testpass123!", stdout=out)
        call_command("setup_kcan_superadmin", "--password", "testpass123!", stdout=out)
        self.assertEqual(User.objects.filter(email="nigelbunribah@gmail.com").count(), 1)
        self.assertEqual(Partner.objects.filter(slug="kcan").count(), 1)


# ─── Admin access control ─────────────────────────────────────────────────────

class AdminAccessControlTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.regular = _make_user("user@test.com", tier="Free")

    def test_admin_can_access_user_list(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/control/admin/users/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("users", resp.data)

    def test_regular_user_denied(self):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.get("/control/admin/users/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_denied(self):
        resp = self.client.get("/control/admin/users/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_platform_stats(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/control/admin/users/platform-stats/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("total_users", resp.data)
        self.assertIn("growth_series_30d", resp.data)


# ─── User management ─────────────────────────────────────────────────────────

class AdminUserManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.target = _make_user("target@test.com", tier="Free")
        self.client.force_authenticate(user=self.admin)

    def test_ban_user(self):
        resp = self.client.post(f"/control/admin/users/{self.target.id}/ban/", {"reason": "Test", "permanent": True})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "banned")

    def test_unban_user(self):
        self.target.status = "banned"
        self.target.save()
        resp = self.client.post(f"/control/admin/users/{self.target.id}/unban/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "active")

    def test_set_tier(self):
        resp = self.client.post(f"/control/admin/users/{self.target.id}/set-tier/", {"tier": "pro"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertEqual(self.target.tier, "pro")

    def test_set_tier_invalid(self):
        resp = self.client.post(f"/control/admin/users/{self.target.id}/set-tier/", {"tier": "SuperGold"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_search(self):
        resp = self.client.get("/control/admin/users/", {"q": "target"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        emails = [u["email"] for u in resp.data["users"]]
        self.assertIn("target@test.com", emails)


# ─── Content moderation ───────────────────────────────────────────────────────

class AdminContentModerationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.reporter = _make_user("reporter@test.com", tier="Free")
        self.flag = Flag.objects.create(
            target_type="POST",
            target_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            source="USER",
            reporter_id=self.reporter.id,
            reason="Spam content",
            severity="MEDIUM",
            status="PENDING",
        )
        self.client.force_authenticate(user=self.admin)

    def test_queue_returns_pending_flags(self):
        resp = self.client.get("/control/admin/content/queue/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        flag_ids = [f["id"] for f in resp.data["flags"]]
        self.assertIn(str(self.flag.id), flag_ids)

    def test_summary_counts(self):
        resp = self.client.get("/control/admin/content/summary/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["total_pending"], 1)

    def test_dismiss_flag(self):
        resp = self.client.post(
            f"/control/admin/content/flags/{self.flag.id}/action/",
            {"action": "dismiss"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.flag.refresh_from_db()
        self.assertEqual(self.flag.status, "DISMISSED")

    def test_action_flag(self):
        resp = self.client.post(
            f"/control/admin/content/flags/{self.flag.id}/action/",
            {"action": "warn", "notes": "First warning"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.flag.refresh_from_db()
        self.assertEqual(self.flag.status, "ACTIONED")

    def test_invalid_action_rejected(self):
        resp = self.client.post(
            f"/control/admin/content/flags/{self.flag.id}/action/",
            {"action": "nuke"},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_trends_returns_30_day_series(self):
        resp = self.client.get("/control/admin/content/trends/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["series_30d"]), 30)


# ─── Partner oversight ────────────────────────────────────────────────────────

class AdminPartnerOversightTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.owner = _make_user("owner@test.com", tier="Partner Pro")
        self.partner = _make_partner("testpartner", self.owner)
        self.client.force_authenticate(user=self.admin)

    def test_list_partners(self):
        resp = self.client.get("/control/admin/partners/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("partners", resp.data)

    def test_partner_detail(self):
        resp = self.client.get(f"/control/admin/partners/{self.partner.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["partner"]["slug"], "testpartner")

    def test_partner_stats(self):
        resp = self.client.get("/control/admin/partners/stats/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("total_partners", resp.data)


# ─── Organization App Builder ─────────────────────────────────────────────────

class OrgAppBuilderTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@test.com", tier="Partner Pro")
        self.partner = _make_partner("mypartner", self.owner)
        PartnerMembership.objects.update_or_create(
            partner=self.partner, user=self.owner,
            defaults={"status": "member", "role": "admin"},
        )
        self.client.force_authenticate(user=self.owner)

    def test_create_app(self):
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/organization-apps/",
            {"name": "Test App", "slug": "test-app", "type": "kis", "status": "draft"},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["app"]["name"], "Test App")

    def test_update_app(self):
        app = PartnerOrganizationApp.objects.create(
            partner=self.partner, name="Old Name", slug="old-app", type="kis",
        )
        resp = self.client.patch(
            f"/api/v1/partners/{self.partner.id}/organization-apps/{app.id}/",
            {"name": "New Name"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.name, "New Name")

    def test_delete_app(self):
        app = PartnerOrganizationApp.objects.create(
            partner=self.partner, name="To Delete", slug="to-delete", type="kis",
        )
        resp = self.client.delete(
            f"/api/v1/partners/{self.partner.id}/organization-apps/{app.id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerOrganizationApp.objects.filter(id=app.id).exists())

    def test_create_tab(self):
        app = PartnerOrganizationApp.objects.create(
            partner=self.partner, name="App", slug="myapp", type="kis",
        )
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/organization-apps/{app.id}/tabs/",
            {"title": "Home", "slug": "home"},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["tab"]["title"], "Home")

    def test_update_tab(self):
        app = PartnerOrganizationApp.objects.create(
            partner=self.partner, name="App", slug="myapp2", type="kis",
        )
        tab = PartnerOrganizationAppTab.objects.create(app=app, title="Old Tab", slug="old-tab")
        resp = self.client.patch(
            f"/api/v1/partners/{self.partner.id}/organization-apps/{app.id}/tabs/{tab.id}/",
            {"title": "New Tab"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        tab.refresh_from_db()
        self.assertEqual(tab.title, "New Tab")

    def test_delete_tab(self):
        app = PartnerOrganizationApp.objects.create(
            partner=self.partner, name="App", slug="myapp3", type="kis",
        )
        tab = PartnerOrganizationAppTab.objects.create(app=app, title="Tab", slug="tab1")
        resp = self.client.delete(
            f"/api/v1/partners/{self.partner.id}/organization-apps/{app.id}/tabs/{tab.id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerOrganizationAppTab.objects.filter(id=tab.id).exists())

    def test_non_admin_cannot_manage_apps(self):
        stranger = _make_user("stranger@test.com", tier="Free")
        self.client.force_authenticate(user=stranger)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/organization-apps/",
            {"name": "Sneaky App", "slug": "sneaky"},
        )
        self.assertIn(resp.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED, status.HTTP_404_NOT_FOUND])
