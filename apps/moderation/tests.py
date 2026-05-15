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
        self.assertTrue(models.AuditLog.objects.filter(action="media_safety.scan.approve", target_id=scan.id).exists())
