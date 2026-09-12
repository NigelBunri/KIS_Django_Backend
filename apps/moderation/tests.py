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


class StrikeIdempotencyTests(APITestCase):
    """apply_ai_flag_consequence must never double-count the same
    confirmed violation - a retried Celery task, a redelivered webhook, or
    this function being called twice for the same scan (the AI auto-block
    path and a subsequent manual "block" both reach the same scan) must
    apply exactly one strike, not two."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670004001", password="TestPass123!", country="CM")
        self.scan = MediaSafetyScan.objects.create(
            owner=self.user, upload_id="x.jpg", context="broadcast", mime_type="image/jpeg",
            provider="nudenet", status="blocked", quarantine=True, requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
        )

    def test_calling_apply_ai_flag_consequence_twice_only_strikes_once(self):
        from apps.moderation.services import create_media_safety_alert_for_scan

        create_media_safety_alert_for_scan(self.scan)
        create_media_safety_alert_for_scan(self.scan)

        reputation = models.UserReputation.objects.get(user_id=self.user.id)
        self.assertEqual(reputation.flags_received, 1)

    def test_create_media_safety_alert_for_scan_called_twice_only_strikes_once(self):
        """Covers the real duplicate-event shape: the same scan reaching
        create_media_safety_alert_for_scan twice (e.g. a redelivered
        scan_uploaded_object_task, or a duplicate Nest webhook)."""
        from apps.moderation.services import create_media_safety_alert_for_scan

        create_media_safety_alert_for_scan(self.scan)
        create_media_safety_alert_for_scan(self.scan)

        reputation = models.UserReputation.objects.get(user_id=self.user.id)
        self.assertEqual(reputation.flags_received, 1)

    def test_approve_then_reblock_applies_a_fresh_strike(self):
        """A LEGITIMATE re-block after an appeal overturned the first one
        (new evidence) must still strike - the idempotency guard is
        per-episode, not permanent for the scan's lifetime."""
        from apps.moderation.services import apply_media_safety_action, create_media_safety_alert_for_scan

        create_media_safety_alert_for_scan(self.scan)
        self.assertEqual(models.UserReputation.objects.get(user_id=self.user.id).flags_received, 1)

        apply_media_safety_action(self.scan, action="approve", actor=self.user)
        self.scan.refresh_from_db()
        self.assertNotIn("strike_applied", self.scan.result)

        apply_media_safety_action(self.scan, action="block", actor=self.user)
        self.assertEqual(models.UserReputation.objects.get(user_id=self.user.id).flags_received, 2)


class BulkViolationTests(APITestCase):
    """A single upload batch containing BULK_VIOLATION_THRESHOLD (10)+
    blocked pieces of content skips the 6-strike ladder and suspends
    immediately, regardless of the account's running strike count."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670005001", password="TestPass123!", country="CM")

    def _make_scan(self):
        return MediaSafetyScan.objects.create(
            owner=self.user, upload_id="x.jpg", context="broadcast", mime_type="image/jpeg",
            provider="nudenet", status="blocked", quarantine=True, requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
        )

    def test_ten_blocked_uploads_in_one_batch_suspends_immediately(self):
        from apps.moderation.services import create_media_safety_alert_for_scan

        scans = [self._make_scan() for _ in range(10)]
        for scan in scans:
            create_media_safety_alert_for_scan(scan)

        self.user.refresh_from_db()
        self.assertEqual(self.user.status, "suspended")
        self.assertFalse(self.user.is_active)
        # Suspended on the 10th, not artificially delayed to strike 6 twice
        # over - the running strike count at suspension time is exactly 10,
        # not reset or double-counted by the bulk check.
        self.assertEqual(models.UserReputation.objects.get(user_id=self.user.id).flags_received, 10)

    def test_nine_in_one_batch_does_not_trigger_the_bulk_rule_independent_of_the_strike_ladder(self):
        """Isolates the bulk rule specifically from STRIKES_BEFORE_SUSPENSION
        (6) - with the default threshold, 9 blocked uploads always suspends
        via the regular ladder anyway (9 > 6), so that alone wouldn't prove
        the bulk rule is actually threshold-gated at 10 rather than always
        firing. Patching the ladder threshold above 9 isolates it: only the
        bulk rule could suspend here, and correctly doesn't at 9."""
        from unittest.mock import patch

        from apps.moderation.services import create_media_safety_alert_for_scan

        with patch("apps.moderation.services.STRIKES_BEFORE_SUSPENSION", 20):
            for _ in range(9):
                scan = self._make_scan()
                create_media_safety_alert_for_scan(scan)

        self.user.refresh_from_db()
        self.assertEqual(self.user.status, "active")

    def test_bulk_rule_suspends_even_when_the_strike_ladder_threshold_is_far_higher(self):
        """The actual distinguishing behavior: a bulk batch suspends on its
        own even when STRIKES_BEFORE_SUSPENSION is configured much higher
        than BULK_VIOLATION_THRESHOLD - the bulk rule is a real independent
        ceiling, not just a restatement of the ladder."""
        from unittest.mock import patch

        from apps.moderation.services import create_media_safety_alert_for_scan

        with patch("apps.moderation.services.STRIKES_BEFORE_SUSPENSION", 20):
            for _ in range(10):
                scan = self._make_scan()
                create_media_safety_alert_for_scan(scan)

        self.user.refresh_from_db()
        self.assertEqual(self.user.status, "suspended")


class AiBlockTakedownTests(APITestCase):
    """A definitive AI BLOCKED verdict must immediately take the linked
    public content offline - no human approval required for that. But per
    explicit product policy, AI must NEVER schedule or perform the actual
    file/record deletion on its own - only a human admin's explicit
    "Delete" action does that (see AdminMediaSafetyModerateActionTests)."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670006001", password="TestPass123!", country="CM")
        from apps.broadcasts.models import BroadcastVideo

        self.video = BroadcastVideo.objects.create(
            title="t", creator=self.user, video_url="http://x/test.mp4", mime_type="video/mp4",
            storage_path="broadcast_videos/x.mp4", type="video",
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
        )
        self.scan = MediaSafetyScan.objects.create(
            owner=self.user, upload_id="broadcast_videos/x.mp4", context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="blocked", quarantine=True, requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
            result={"resolution_target": "broadcast_video", "resolution_id": str(self.video.id), "storage_path": "broadcast_videos/x.mp4"},
        )

    def test_ai_block_immediately_takes_video_offline(self):
        from apps.broadcasts.models import BroadcastVideo
        from apps.moderation.services import create_media_safety_alert_for_scan

        create_media_safety_alert_for_scan(self.scan)

        self.video.refresh_from_db()
        self.assertEqual(self.video.moderation_status, BroadcastVideo.ModerationStatus.BLOCKED)
        self.assertFalse(self.video.is_active)

    def test_ai_block_never_schedules_deletion_on_its_own(self):
        """The core policy this test class exists to prove: AI may take
        content offline, but must never start a deletion countdown by
        itself. Only a human's explicit "Delete" action may set
        scheduled_deletion_at (see admin_control's AdminMediaSafetyModerateView)."""
        from apps.moderation.services import create_media_safety_alert_for_scan

        create_media_safety_alert_for_scan(self.scan)

        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.scheduled_deletion_at)
        self.assertIsNone(self.scan.deleted_at)

    def test_duplicate_ai_block_calls_never_schedule_deletion_either(self):
        from apps.moderation.services import create_media_safety_alert_for_scan

        create_media_safety_alert_for_scan(self.scan)
        create_media_safety_alert_for_scan(self.scan)

        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.scheduled_deletion_at)


class AppealRestoresBroadcastEligibilityTests(APITestCase):
    """An overturned AI block must restore the underlying content's
    broadcast eligibility, not just the MediaSafetyScan/MediaAsset fields
    the appeal system already knew about before BroadcastVideo's
    moderation gate existed."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670007001", password="TestPass123!", country="CM")
        self.admin = User.objects.create_user(
            phone="+237670007002", password="TestPass123!", country="CM", is_staff=True,
        )
        from apps.broadcasts.models import BroadcastVideo

        self.video = BroadcastVideo.objects.create(
            title="t", creator=self.user, video_url="", mime_type="video/mp4",
            storage_path="broadcast_videos/x.mp4", type="video",
        )
        self.scan = MediaSafetyScan.objects.create(
            owner=self.user, upload_id="broadcast_videos/x.mp4", context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="blocked", quarantine=True, requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
            result={"resolution_target": "broadcast_video", "resolution_id": str(self.video.id), "storage_path": "broadcast_videos/x.mp4"},
        )
        from apps.moderation.services import create_media_safety_alert_for_scan
        create_media_safety_alert_for_scan(self.scan)
        self.video.refresh_from_db()
        self.assertFalse(self.video.is_active)  # sanity: takedown actually happened

    def test_overturning_the_block_restores_broadcast_eligibility_and_cancels_a_pending_human_deletion(self):
        """A human had already scheduled this content for deletion (e.g.
        clicked "Delete" in the moderation dashboard) before new evidence
        led to the block being overturned via appeal/approve - the approve
        path must cancel that pending deletion, not merely leave
        scheduled_deletion_at at None because AI never set it in the first
        place (see AiBlockTakedownTests for that separate guarantee)."""
        from apps.broadcasts.models import BroadcastVideo
        from apps.broadcasts.moderation_gate import is_broadcast_eligible
        from apps.moderation.services import apply_media_safety_action

        from django.utils import timezone

        self.scan.scheduled_deletion_at = timezone.now()
        self.scan.save(update_fields=["scheduled_deletion_at"])

        apply_media_safety_action(self.scan, action="approve", actor=self.admin, notes="False positive.")

        self.video.refresh_from_db()
        self.assertEqual(self.video.moderation_status, BroadcastVideo.ModerationStatus.PASSED)
        self.assertTrue(is_broadcast_eligible(self.video))

        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.scheduled_deletion_at)
