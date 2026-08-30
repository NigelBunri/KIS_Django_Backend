from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.media.models import MediaSafetyScan

from . import models


class ModerationAccessBoundaryTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670002001", password="TestPass123!", country="CM")
        self.admin = User.objects.create_user(
            phone="+237670002002",
            password="TestPass123!",
            country="CM",
            is_staff=True,
        )
        self.client.force_authenticate(self.user)

    def test_non_staff_cannot_list_moderation_audit_logs(self):
        response = self.client.get("/api/v1/audit-logs/")

        self.assertEqual(response.status_code, 403)

    def test_non_staff_flag_create_cannot_spoof_reporter_or_source(self):
        other_id = self.admin.id
        response = self.client.post(
            "/api/v1/flags/",
            {
                "source": "SYSTEM",
                "target_type": "USER",
                "target_id": str(other_id),
                "reporter_id": str(other_id),
                "reason": "abuse",
                "severity": "LOW",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        flag = models.Flag.objects.get(id=response.data["id"])
        self.assertEqual(flag.source, "USER")
        self.assertEqual(str(flag.reporter_id), str(self.user.id))

    def test_staff_operations_queue_includes_media_safety_scan(self):
        MediaSafetyScan.objects.create(
            owner=self.user,
            context="channel",
            original_name="clip.mp4",
            mime_type="video/mp4",
            status="pending_review",
            quarantine=True,
            requires_review=True,
            reason="explicit_scan_provider_not_configured",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/moderation/staff/operations-queue/?source=media")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["media_safety"], 1)
        self.assertEqual(response.data["results"][0]["kind"], "media_safety_scan")

    def test_staff_can_approve_media_safety_scan_with_audit(self):
        scan = MediaSafetyScan.objects.create(
            owner=self.user,
            context="channel",
            original_name="clip.mp4",
            mime_type="video/mp4",
            status="pending_review",
            quarantine=True,
            requires_review=True,
            reason="explicit_scan_provider_not_configured",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/moderation/staff/operation-action/",
            {
                "target_type": "media_safety_scan",
                "target_id": str(scan.id),
                "action": "approve",
                "notes": "Reviewed manually.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        scan.refresh_from_db()
        self.assertEqual(scan.status, "passed")
        self.assertFalse(scan.quarantine)
        self.assertFalse(scan.requires_review)


class AiFlagConsequenceTests(APITestCase):
    """apply_ai_flag_consequence — the warn(1-5)/auto-suspend(6) escalation
    for CONFIRMED explicit-content violations. Covers both trigger paths
    (high-confidence AI auto-block, and a staff member manually confirming
    a low-confidence flag) and confirms an unconfirmed pending_review scan
    never costs a strike on its own."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670003001", password="TestPass123!", country="CM")

    def _make_scan(self, **overrides):
        defaults = dict(
            owner=self.user,
            upload_id="2026-01-01/uuid-photo.jpg",
            context="chat",
            original_name="photo.jpg",
            mime_type="image/jpeg",
            bytes=1234,
            provider="nudenet",
            status="blocked",
            quarantine=True,
            requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
        )
        defaults.update(overrides)
        return MediaSafetyScan.objects.create(**defaults)

    def test_first_violation_warns_without_suspending(self):
        from apps.moderation.services import create_media_safety_alert_for_scan

        scan = self._make_scan()
        create_media_safety_alert_for_scan(scan)

        reputation = models.UserReputation.objects.get(user_id=self.user.id)
        self.assertEqual(reputation.flags_received, 1)

        action = models.ModerationAction.objects.filter(
            flag__target_id=scan.id, action="WARN",
        ).first()
        self.assertIsNotNone(action)
        self.assertTrue(action.auto_generated)

        self.user.refresh_from_db()
        self.assertEqual(self.user.status, "active")
        self.assertTrue(self.user.is_active)

    def test_sixth_violation_auto_suspends_and_flips_is_active_false(self):
        from apps.moderation.services import create_media_safety_alert_for_scan

        for _ in range(6):
            scan = self._make_scan()
            create_media_safety_alert_for_scan(scan)

        reputation = models.UserReputation.objects.get(user_id=self.user.id)
        self.assertEqual(reputation.flags_received, 6)

        self.user.refresh_from_db()
        self.assertEqual(self.user.status, "suspended")
        # This is the field JWT auth actually enforces (SimpleJWT rejects
        # inactive users) — status alone, without this, would suspend in
        # name only. See DeviceBoundJWTAuthentication.
        self.assertFalse(self.user.is_active)

        suspend_action = models.ModerationAction.objects.filter(
            performed_by_id="00000000-0000-0000-0000-000000000000", action="SUSPEND",
        ).first()
        self.assertIsNotNone(suspend_action)

        flag = models.Flag.objects.filter(target_id=scan.id).first()
        self.assertEqual(flag.escalation_level, "ADMIN")

    def test_uncertain_pending_review_scan_never_costs_a_strike_on_its_own(self):
        from apps.moderation.services import create_media_safety_alert_for_scan

        scan = self._make_scan(
            status="pending_review",
            quarantine=True,
            requires_review=True,
            reason="nudenet_low_confidence:FEMALE_BREAST_EXPOSED",
        )
        create_media_safety_alert_for_scan(scan)

        self.assertFalse(models.UserReputation.objects.filter(user_id=self.user.id).exists())
        self.assertFalse(models.ModerationAction.objects.filter(flag__target_id=scan.id).exists())

    def test_staff_manually_confirming_a_pending_review_flag_applies_the_strike(self):
        from apps.moderation.services import apply_media_safety_action, create_media_safety_alert_for_scan

        scan = self._make_scan(
            status="pending_review", quarantine=True, requires_review=True,
            reason="nudenet_low_confidence:FEMALE_BREAST_EXPOSED",
        )
        create_media_safety_alert_for_scan(scan)
        self.assertFalse(models.UserReputation.objects.filter(user_id=self.user.id).exists())

        apply_media_safety_action(scan, action="block", actor=self.user, notes="Confirmed on review.")

        reputation = models.UserReputation.objects.get(user_id=self.user.id)
        self.assertEqual(reputation.flags_received, 1)
