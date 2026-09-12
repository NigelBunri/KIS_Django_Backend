"""
Safety net so a video content-safety scan can never sit "pending"/
incomplete indefinitely - apps.media.tasks.resolve_stuck_video_scans().
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.media.models import MediaSafetyScan
from apps.media.safety import NUDENET_SCAN_QUEUED_REASON
from apps.media.tasks import resolve_stuck_video_scans

User = get_user_model()


class ResolveStuckVideoScansTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670010001", password="TestPass123!", country="CM")

    def _make_queued_scan(self, *, created_at):
        scan = MediaSafetyScan.objects.create(
            owner=self.user, upload_id="broadcast_videos/x.mp4", context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="pending_review", quarantine=True, requires_review=True,
            reason=NUDENET_SCAN_QUEUED_REASON,
            result={"resolution_target": "broadcast_video", "resolution_id": "00000000-0000-0000-0000-000000000000"},
        )
        MediaSafetyScan.objects.filter(id=scan.id).update(created_at=created_at)
        scan.refresh_from_db()
        return scan

    @patch("apps.media.tasks.scan_video_and_resolve_task.delay")
    def test_a_scan_stuck_longer_than_the_threshold_is_requeued(self, mock_delay):
        scan = self._make_queued_scan(created_at=timezone.now() - timedelta(minutes=90))

        result = resolve_stuck_video_scans(stale_after_minutes=60)

        self.assertEqual(result, {"requeued": 1})
        mock_delay.assert_called_once_with(scan_id=str(scan.id))

    @patch("apps.media.tasks.scan_video_and_resolve_task.delay")
    def test_a_recently_queued_scan_is_left_alone(self, mock_delay):
        self._make_queued_scan(created_at=timezone.now() - timedelta(minutes=5))

        result = resolve_stuck_video_scans(stale_after_minutes=60)

        self.assertEqual(result, {"requeued": 0})
        mock_delay.assert_not_called()

    @patch("apps.media.tasks.scan_video_and_resolve_task.delay")
    def test_an_already_resolved_scan_is_not_requeued(self, mock_delay):
        scan = self._make_queued_scan(created_at=timezone.now() - timedelta(minutes=90))
        scan.status = "passed"
        scan.reason = "nudenet_clean"
        scan.save(update_fields=["status", "reason"])

        result = resolve_stuck_video_scans(stale_after_minutes=60)

        self.assertEqual(result, {"requeued": 0})
        mock_delay.assert_not_called()

    @patch("apps.media.tasks.scan_video_and_resolve_task.delay")
    def test_re_enqueuing_an_in_flight_scan_is_a_safe_no_op(self, mock_delay):
        """Simulates re-enqueuing a scan whose original task is still
        in-flight (not actually stuck, just slow) - scan_video_and_resolve_
        task's own idempotency guard (reason still == queued marker) is
        what makes a duplicate/redundant enqueue safe; this task's only
        job is deciding WHEN to re-enqueue, not guaranteeing the result is
        safe on its own. Confirmed here at the integration boundary: the
        stuck-scan sweep will re-fire .delay() for a still-genuinely-queued
        scan without erroring or double side-effects at this layer."""
        scan = self._make_queued_scan(created_at=timezone.now() - timedelta(minutes=90))

        resolve_stuck_video_scans(stale_after_minutes=60)
        resolve_stuck_video_scans(stale_after_minutes=60)

        self.assertEqual(mock_delay.call_count, 2)
        scan.refresh_from_db()
        self.assertEqual(scan.reason, NUDENET_SCAN_QUEUED_REASON)  # unchanged - this task never mutates the scan itself
