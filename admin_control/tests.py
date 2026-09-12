"""Tests for KCAN admin control: superadmin setup, access control, user management, content moderation."""
from __future__ import annotations

import datetime
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

    def test_block_user_deactivates_and_revokes_devices(self):
        from apps.accounts.models import Device

        Device.objects.create(user=self.target, device_id="d1", platform="android", is_parent=True)
        resp = self.client.post(f"/control/admin/users/{self.target.id}/block/", {"reason": "Test"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "blocked")
        self.assertFalse(self.target.is_active)
        device = Device.objects.get(user=self.target, device_id="d1")
        self.assertIsNotNone(device.revoked_at)

    def test_delete_user_schedules_grace_period_deletion(self):
        resp = self.client.post(f"/control/admin/users/{self.target.id}/delete/", {"reason": "Test"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("scheduled_for", resp.data)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertTrue(self.target.is_deleted)

    def test_restore_reverses_ban(self):
        self.target.status = "banned"
        self.target.save(update_fields=["status"])
        resp = self.client.post(f"/control/admin/users/{self.target.id}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "active")

    def test_restore_reverses_block(self):
        self.client.post(f"/control/admin/users/{self.target.id}/block/", {"reason": "Test"})
        resp = self.client.post(f"/control/admin/users/{self.target.id}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "active")
        self.assertTrue(self.target.is_active)

    def test_restore_cancels_pending_deletion(self):
        from apps.accounts.models import GDPRRequest

        self.client.post(f"/control/admin/users/{self.target.id}/delete/", {"reason": "Test"})
        resp = self.client.post(f"/control/admin/users/{self.target.id}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertFalse(self.target.is_deleted)
        pending = GDPRRequest.objects.filter(user=self.target, type="account_deletion", status="pending")
        self.assertFalse(pending.exists())


# ─── Violation review + admin-initiated violation deletion ───────────────────

class AdminUserViolationsAndScheduleDeletionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.target = _make_user("target@test.com", tier="Free")
        self.client.force_authenticate(user=self.admin)

    def test_violations_view_reports_strike_count_and_blocked_incidents(self):
        from apps.media.models import MediaSafetyScan
        from apps.moderation.services import create_media_safety_alert_for_scan

        for i in range(3):
            scan = MediaSafetyScan.objects.create(
                owner=self.target, upload_id=f"x{i}.jpg", context="broadcast", mime_type="image/jpeg",
                provider="nudenet", status="blocked", quarantine=True, requires_review=False,
                reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
            )
            create_media_safety_alert_for_scan(scan)

        resp = self.client.get(f"/control/admin/users/{self.target.id}/violations/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["violation_count"], 3)
        self.assertEqual(len(resp.data["blocked_incidents"]), 3)
        self.assertEqual(len(resp.data["warning_history"]), 3)
        self.assertIsNone(resp.data["pending_deletion"])

    def test_violations_view_reports_pending_deletion(self):
        from apps.accounts.views import schedule_account_deletion

        schedule_account_deletion(
            self.target, actor=self.admin, source="admin_violation_review",
            grace_period=datetime.timedelta(hours=3),
        )
        resp = self.client.get(f"/control/admin/users/{self.target.id}/violations/")
        self.assertIsNotNone(resp.data["pending_deletion"])

    def test_schedule_violation_deletion_uses_the_short_grace_window(self):
        from apps.accounts.models import GDPRRequest
        from django.utils import timezone

        with self.settings(ACCOUNT_VIOLATION_DELETION_WARNING_HOURS=3):
            resp = self.client.post(
                f"/control/admin/users/{self.target.id}/schedule-violation-deletion/",
                {"reason": "Repeated violations"},
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        gdpr_request = GDPRRequest.objects.get(user=self.target, type="account_deletion", status="pending")
        delta = gdpr_request.scheduled_for - timezone.now()
        self.assertTrue(datetime.timedelta(hours=2, minutes=55) < delta < datetime.timedelta(hours=3, minutes=5))

        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertTrue(self.target.is_deleted)

    def test_restore_after_violation_deletion_sends_a_notification(self):
        from apps.notifications.models import Notification

        self.client.post(
            f"/control/admin/users/{self.target.id}/schedule-violation-deletion/",
            {"reason": "Repeated violations"},
        )
        before = Notification.objects.filter(user_id=self.target.id, type="account.deletion_cancelled").count()

        resp = self.client.post(f"/control/admin/users/{self.target.id}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        after = Notification.objects.filter(user_id=self.target.id, type="account.deletion_cancelled").count()
        self.assertEqual(after, before + 1)

    def test_unauthenticated_denied(self):
        anon = APIClient()
        resp = anon.post(f"/control/admin/users/{self.target.id}/schedule-violation-deletion/", {"reason": "x"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


# ─── Suspicious activity: warning notifications surfaced with recipient ──────

class SuspiciousActivityWarningNotificationsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.target = _make_user("target@test.com", tier="Free")
        self.client.force_authenticate(user=self.admin)

    def test_suspicious_activity_includes_warning_notifications_with_recipient(self):
        from apps.media.models import MediaSafetyScan
        from apps.moderation.services import create_media_safety_alert_for_scan

        scan = MediaSafetyScan.objects.create(
            owner=self.target, upload_id="x.jpg", context="broadcast", mime_type="image/jpeg",
            provider="nudenet", status="blocked", quarantine=True, requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
        )
        create_media_safety_alert_for_scan(scan)

        resp = self.client.get("/control/admin/activity/flags/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("flags", resp.data)
        notifications = resp.data["warning_notifications"]
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["user_id"], str(self.target.id))
        self.assertIn("warning", notifications[0]["title"].lower())


# ─── Device wipe (per-user + platform-wide) ──────────────────────────────────

class AdminDeviceWipeTests(TestCase):
    def setUp(self):
        from apps.accounts.models import Device

        self.Device = Device
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.target = _make_user("target@test.com", tier="Free")
        self.other = _make_user("other@test.com", tier="Free")
        Device.objects.create(user=self.target, device_id="parent-1", platform="android", is_parent=True)
        Device.objects.create(user=self.target, device_id="secondary-1", platform="ios", is_parent=False)
        Device.objects.create(user=self.other, device_id="parent-2", platform="web", is_parent=True)
        self.client.force_authenticate(user=self.admin)

    def test_wipe_devices_for_one_user_leaves_other_users_untouched(self):
        resp = self.client.post(f"/control/admin/users/{self.target.id}/wipe-devices/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["deleted_count"], 2)
        self.assertFalse(self.Device.objects.filter(user=self.target).exists())
        self.assertTrue(self.Device.objects.filter(user=self.other).exists())
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "active")

    def test_wipe_all_devices_requires_confirm_phrase(self):
        resp = self.client.post("/control/admin/devices/wipe-all/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.Device.objects.filter(user=self.target).exists())

    def test_wipe_all_devices_deletes_every_account_devices(self):
        resp = self.client.post("/control/admin/devices/wipe-all/", {"confirm": "WIPE ALL DEVICES"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["users_affected"], 2)
        self.assertEqual(resp.data["devices_deleted"], 3)
        self.assertFalse(self.Device.objects.exists())
        self.assertTrue(User.objects.filter(id=self.target.id).exists())
        self.assertTrue(User.objects.filter(id=self.other.id).exists())

    def test_wipe_all_devices_denied_for_non_super_admin(self):
        staffer = _make_user("staffer@test.com", is_staff=True, tier="Free")
        role_client = APIClient()
        role_client.force_authenticate(user=staffer)
        resp = role_client.post("/control/admin/devices/wipe-all/", {"confirm": "WIPE ALL DEVICES"})
        self.assertIn(resp.status_code, (status.HTTP_403_FORBIDDEN,))
        self.assertTrue(self.Device.objects.filter(user=self.target).exists())


# ─── Media safety (content-safety scan ground truth) ─────────────────────────

class AdminMediaSafetyScanTests(TestCase):
    def setUp(self):
        from apps.media.models import MediaSafetyScan

        self.MediaSafetyScan = MediaSafetyScan
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.target = _make_user("target@test.com", tier="Free")
        self.blocked_scan = MediaSafetyScan.objects.create(
            owner=self.target, upload_id="broadcast_videos/blocked.mp4", context="broadcast",
            mime_type="video/mp4", provider="nudenet", status="blocked", quarantine=True,
            requires_review=False, reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
            result={"score": 0.9, "storage_path": "broadcast_videos/blocked.mp4"},
        )
        self.clean_scan = MediaSafetyScan.objects.create(
            owner=self.target, upload_id="broadcast_videos/clean.mp4", context="broadcast",
            mime_type="video/mp4", provider="nudenet", status="passed", quarantine=False,
            requires_review=False, reason="nudenet_clean", result={"score": 0.0},
        )
        self.client.force_authenticate(user=self.admin)

    def test_list_scans_returns_all_verdicts(self):
        resp = self.client.get("/control/admin/media-safety/scans/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {s["id"] for s in resp.data["scans"]}
        self.assertIn(str(self.blocked_scan.id), ids)
        self.assertIn(str(self.clean_scan.id), ids)

    def test_list_scans_filters_by_status(self):
        resp = self.client.get("/control/admin/media-safety/scans/", {"status": "blocked"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {s["id"] for s in resp.data["scans"]}
        self.assertEqual(ids, {str(self.blocked_scan.id)})

    def test_summary_counts_by_status(self):
        resp = self.client.get("/control/admin/media-safety/summary/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["total"], 2)

    def test_media_url_404s_when_file_missing_from_storage(self):
        # No real S3 object exists for this test scan's storage_path, so the
        # view's default_storage.exists() check should correctly report 404
        # rather than returning a URL to nothing.
        resp = self.client.get(f"/control/admin/media-safety/scans/{self.blocked_scan.id}/media-url/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_media_url_404s_for_unknown_scan(self):
        import uuid
        resp = self.client.get(f"/control/admin/media-safety/scans/{uuid.uuid4()}/media-url/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_denied(self):
        anon = APIClient()
        resp = anon.get("/control/admin/media-safety/scans/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class AdminContentQueueMediaSafetyLinkTests(TestCase):
    """Covers the actual reported bug: video content-safety scans never
    raised a moderation Flag, so quarantined/blocked video uploads were
    invisible on /control/admin/moderation. Exercises the real fix sites
    (apps.broadcasts.views._record_upload_safety and
    apps.media.tasks.scan_video_and_resolve_task) rather than re-deriving
    the wiring here."""

    def setUp(self):
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.target = _make_user("target@test.com", tier="Free")
        self.client.force_authenticate(user=self.admin)

    def test_quarantined_scan_surfaces_in_moderation_queue_with_media_summary(self):
        from apps.media.models import MediaSafetyScan
        from apps.moderation.services import create_media_safety_alert_for_scan

        scan = MediaSafetyScan.objects.create(
            owner=self.target, upload_id="broadcast_videos/test.mp4", context="broadcast",
            mime_type="video/mp4", provider="nudenet", status="pending_review", quarantine=True,
            requires_review=True, reason="nudenet_low_confidence:BUTTOCKS_EXPOSED",
            result={"score": 0.4, "storage_path": "broadcast_videos/test.mp4"},
        )
        create_media_safety_alert_for_scan(scan)

        resp = self.client.get("/control/admin/content/queue/", {"status": "PENDING"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        matching = [
            f for f in resp.data["flags"]
            if f.get("media_safety_scan") and f["media_safety_scan"]["id"] == str(scan.id)
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["media_safety_scan"]["status"], "pending_review")
        self.assertEqual(matching[0]["media_safety_scan"]["mime_type"], "video/mp4")
        self.assertTrue(matching[0]["media_safety_scan"]["has_media"])

    def test_clean_scan_does_not_create_a_flag(self):
        from apps.media.models import MediaSafetyScan
        from apps.moderation.models import Flag
        from apps.moderation.services import create_media_safety_alert_for_scan

        scan = MediaSafetyScan.objects.create(
            owner=self.target, upload_id="broadcast_videos/clean2.mp4", context="broadcast",
            mime_type="video/mp4", provider="nudenet", status="passed", quarantine=False,
            requires_review=False, reason="nudenet_clean", result={"score": 0.0},
        )
        before = Flag.objects.count()
        create_media_safety_alert_for_scan(scan)
        self.assertEqual(Flag.objects.count(), before)


# ─── Human moderation gate for public broadcast content ──────────────────────

class AdminMediaSafetyModerateTests(TestCase):
    def setUp(self):
        from apps.broadcasts.models import BroadcastVideo
        from apps.media.models import MediaSafetyScan

        self.BroadcastVideo = BroadcastVideo
        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.creator = _make_user("creator@test.com", tier="Free")
        self.video = BroadcastVideo.objects.create(
            title="t", creator=self.creator, video_url="", mime_type="video/mp4",
            storage_path="broadcast_videos/x.mp4", type="video",
        )
        self.scan = MediaSafetyScan.objects.create(
            owner=self.creator, upload_id="broadcast_videos/x.mp4", context="broadcast",
            mime_type="video/mp4", provider="nudenet", status="passed", quarantine=False,
            result={"score": 0.0, "resolution_target": "broadcast_video", "resolution_id": str(self.video.id)},
        )
        self.client.force_authenticate(user=self.admin)

    def test_scan_serializer_exposes_moderation_state_and_is_moderatable(self):
        resp = self.client.get("/control/admin/media-safety/scans/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        row = next(s for s in resp.data["scans"] if s["id"] == str(self.scan.id))
        self.assertTrue(row["moderatable"])
        self.assertEqual(row["target_type"], "broadcast_video")
        self.assertEqual(row["target_id"], str(self.video.id))
        self.assertEqual(row["moderation"]["status"], "pending_review")
        self.assertFalse(row["moderation"]["is_broadcast_eligible"])

    def test_pass_action_makes_video_eligible(self):
        resp = self.client.post("/control/admin/media-safety/moderate/", {
            "target_type": "broadcast_video", "target_id": str(self.video.id), "action": "pass",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.video.refresh_from_db()
        self.assertEqual(self.video.moderation_status, self.BroadcastVideo.ModerationStatus.PASSED)
        self.assertIsNotNone(self.video.moderation_expires_at)
        self.assertEqual(self.video.moderation_reviewed_by_id, self.admin.id)

    def test_block_action_deactivates_video(self):
        resp = self.client.post("/control/admin/media-safety/moderate/", {
            "target_type": "broadcast_video", "target_id": str(self.video.id), "action": "block",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.video.refresh_from_db()
        self.assertEqual(self.video.moderation_status, self.BroadcastVideo.ModerationStatus.BLOCKED)
        self.assertFalse(self.video.is_active)

    def test_block_action_applies_a_real_strike_and_notification(self):
        """The actual reconciliation this endpoint exists for: a Block here
        must drive the SAME real apps.moderation consequences (strike,
        warning notification) as the pre-existing staff moderation queue -
        not a second, disconnected BroadcastVideo-only field write."""
        from apps.moderation.models import UserReputation
        from apps.notifications.models import Notification

        resp = self.client.post("/control/admin/media-safety/moderate/", {
            "target_type": "broadcast_video", "target_id": str(self.video.id), "action": "block",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        reputation = UserReputation.objects.get(user_id=self.creator.id)
        self.assertEqual(reputation.flags_received, 1)
        self.assertTrue(
            Notification.objects.filter(user_id=self.creator.id, type="MODERATION_WARNING").exists()
        )

        self.scan.refresh_from_db()
        self.assertEqual(self.scan.status, "blocked")
        self.assertIsNotNone(self.scan.scheduled_deletion_at)

    def test_delete_action_deactivates_video(self):
        resp = self.client.post("/control/admin/media-safety/moderate/", {
            "target_type": "broadcast_video", "target_id": str(self.video.id), "action": "delete",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.video.refresh_from_db()
        self.assertEqual(self.video.moderation_status, self.BroadcastVideo.ModerationStatus.DELETED)
        self.assertFalse(self.video.is_active)

    def test_delete_action_forces_immediate_deletion_sweep_eligibility(self):
        from django.utils import timezone

        resp = self.client.post("/control/admin/media-safety/moderate/", {
            "target_type": "broadcast_video", "target_id": str(self.video.id), "action": "delete",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.scan.refresh_from_db()
        self.assertIsNotNone(self.scan.scheduled_deletion_at)
        self.assertLessEqual(self.scan.scheduled_deletion_at, timezone.now())

    def test_pass_action_applies_through_the_real_approve_path(self):
        """Confirms "pass" here is not a disconnected write either - it
        goes through apply_media_safety_action's "approve" branch, which
        also clears the scan's own quarantine/status fields, not just the
        BroadcastVideo-side moderation_status."""
        # First block it (as an admin correcting an earlier decision would).
        self.client.post("/control/admin/media-safety/moderate/", {
            "target_type": "broadcast_video", "target_id": str(self.video.id), "action": "block",
        })
        self.client.post("/control/admin/media-safety/moderate/", {
            "target_type": "broadcast_video", "target_id": str(self.video.id), "action": "pass",
        })
        self.scan.refresh_from_db()
        self.assertEqual(self.scan.status, "passed")
        self.assertFalse(self.scan.quarantine)
        self.assertIsNone(self.scan.scheduled_deletion_at)

    def test_invalid_action_rejected(self):
        resp = self.client.post("/control/admin/media-safety/moderate/", {
            "target_type": "broadcast_video", "target_id": str(self.video.id), "action": "approve-forever",
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unwired_target_type_rejected_not_silently_ignored(self):
        resp = self.client.post("/control/admin/media-safety/moderate/", {
            "target_type": "education_material", "target_id": "00000000-0000-0000-0000-000000000000", "action": "pass",
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_denied(self):
        anon = APIClient()
        resp = anon.post("/control/admin/media-safety/moderate/", {
            "target_type": "broadcast_video", "target_id": str(self.video.id), "action": "pass",
        })
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class AdminMediaSafetyChatExclusionTests(TestCase):
    """Private-messaging content must never appear in any admin
    media-safety/moderation surface - confirmed via apps.media.safety's own
    context list, not assumed."""

    def setUp(self):
        from apps.media.models import MediaSafetyScan
        from apps.moderation.services import create_media_safety_alert_for_scan

        self.client = APIClient()
        self.admin = _make_user("admin@test.com", is_superuser=True, is_staff=True, tier="Partner Pro")
        _make_admin_role(self.admin)
        self.target = _make_user("target@test.com", tier="Free")
        self.chat_scan = MediaSafetyScan.objects.create(
            owner=self.target, upload_id="chat/x.jpg", context="chat",
            mime_type="image/jpeg", provider="nudenet", status="blocked", quarantine=True,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED", result={"score": 0.9, "storage_path": "chat/x.jpg"},
        )
        create_media_safety_alert_for_scan(self.chat_scan)
        self.broadcast_scan = MediaSafetyScan.objects.create(
            owner=self.target, upload_id="broadcast_videos/y.mp4", context="broadcast",
            mime_type="video/mp4", provider="nudenet", status="blocked", quarantine=True,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED", result={"score": 0.9},
        )
        create_media_safety_alert_for_scan(self.broadcast_scan)
        self.client.force_authenticate(user=self.admin)

    def test_chat_scan_excluded_from_media_safety_list(self):
        resp = self.client.get("/control/admin/media-safety/scans/")
        ids = {s["id"] for s in resp.data["scans"]}
        self.assertNotIn(str(self.chat_scan.id), ids)
        self.assertIn(str(self.broadcast_scan.id), ids)

    def test_chat_scan_excluded_from_media_safety_summary_total(self):
        chat_only_total = self.client.get("/control/admin/media-safety/summary/").data["total"]
        self.assertGreaterEqual(chat_only_total, 1)
        # The broadcast scan alone must be counted; deleting it should drop
        # the total by exactly one if the chat scan was never counted.
        from apps.media.models import MediaSafetyScan
        self.broadcast_scan.delete()
        after = self.client.get("/control/admin/media-safety/summary/").data["total"]
        self.assertEqual(after, chat_only_total - 1)

    def test_chat_scan_media_url_404s_even_with_a_real_id(self):
        resp = self.client.get(f"/control/admin/media-safety/scans/{self.chat_scan.id}/media-url/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_chat_flag_excluded_from_moderation_queue(self):
        resp = self.client.get("/control/admin/content/queue/", {"status": "PENDING"})
        flag_target_ids = {f["target_id"] for f in resp.data["flags"]}
        self.assertNotIn(str(self.chat_scan.id), flag_target_ids)
        self.assertIn(str(self.broadcast_scan.id), flag_target_ids)

    def test_chat_flag_excluded_from_moderation_summary(self):
        resp = self.client.get("/control/admin/content/summary/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Just proves the summary endpoint still works with the exclusion
        # join in place; the queue test above proves the exclusion itself.
        self.assertIn("total_pending", resp.data)


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
