"""
The permanent-deletion sweep - apps.media.tasks.delete_blocked_media().
Covers idempotency/retry-safety, the actual file + public-content-record
deletion, and that a not-yet-due or already-deleted scan is correctly left
alone. This sweep only ever acts on a scan whose scheduled_deletion_at a
HUMAN admin explicitly set (see AdminMediaSafetyModerateView's "delete"
action) - AI never sets it, so these tests set it directly rather than
going through the AI-block path.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from django.utils import timezone

from apps.broadcasts.models import BroadcastVideo
from apps.media.models import MediaSafetyScan
from apps.media.tasks import delete_blocked_media

User = get_user_model()


class DeleteBlockedMediaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670008001", password="TestPass123!", country="CM")
        self.storage_path = "broadcast_videos/blocked-delete-test.bin"
        default_storage.save(self.storage_path, ContentFile(b"fake blocked content"))
        self.addCleanup(lambda: default_storage.exists(self.storage_path) and default_storage.delete(self.storage_path))

        self.video = BroadcastVideo.objects.create(
            title="t", creator=self.user, video_url="", mime_type="video/mp4",
            storage_path=self.storage_path, type="video",
            moderation_status=BroadcastVideo.ModerationStatus.BLOCKED, is_active=False,
        )
        self.scan = MediaSafetyScan.objects.create(
            owner=self.user, upload_id=self.storage_path, context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="blocked", quarantine=True, requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
            result={
                "resolution_target": "broadcast_video", "resolution_id": str(self.video.id),
                "storage_path": self.storage_path,
            },
            scheduled_deletion_at=timezone.now() - timedelta(minutes=1),  # already due
        )

    def test_due_scan_deletes_the_file_and_the_video_row(self):
        result = delete_blocked_media()

        self.assertEqual(result, {"deleted": 1, "errors": 0})
        self.assertFalse(default_storage.exists(self.storage_path))
        self.assertFalse(BroadcastVideo.objects.filter(id=self.video.id).exists())

        self.scan.refresh_from_db()
        self.assertIsNotNone(self.scan.deleted_at)

    def test_not_yet_due_scan_is_left_alone(self):
        self.scan.scheduled_deletion_at = timezone.now() + timedelta(hours=1)
        self.scan.save(update_fields=["scheduled_deletion_at"])

        result = delete_blocked_media()

        self.assertEqual(result, {"deleted": 0, "errors": 0})
        self.assertTrue(default_storage.exists(self.storage_path))
        self.assertTrue(BroadcastVideo.objects.filter(id=self.video.id).exists())

    def test_already_deleted_scan_is_not_reprocessed(self):
        """Idempotent/retry-safe: a redelivered task run for a scan whose
        deletion already completed must be a no-op, not an error (the file
        and row are already gone) and never double-processed."""
        self.scan.deleted_at = timezone.now()
        self.scan.save(update_fields=["deleted_at"])

        result = delete_blocked_media()

        self.assertEqual(result, {"deleted": 0, "errors": 0})
        # Untouched - the guard skipped it before ever looking at the file.
        self.assertTrue(default_storage.exists(self.storage_path))

    def test_pending_review_scan_is_never_deleted(self):
        """Only a definitive block schedules deletion - a pending_review
        scan must never be swept even if scheduled_deletion_at were
        somehow set on it."""
        self.scan.status = "pending_review"
        self.scan.save(update_fields=["status"])

        result = delete_blocked_media()

        self.assertEqual(result, {"deleted": 0, "errors": 0})
        self.assertTrue(default_storage.exists(self.storage_path))

    def test_missing_file_does_not_prevent_marking_deleted(self):
        """The file may already be gone (manually removed, a prior partial
        failure) - the sweep must still mark the scan resolved rather than
        erroring forever on a file that will never come back."""
        default_storage.delete(self.storage_path)

        result = delete_blocked_media()

        self.assertEqual(result, {"deleted": 1, "errors": 0})
        self.scan.refresh_from_db()
        self.assertIsNotNone(self.scan.deleted_at)

    def test_a_second_sweep_run_does_not_redelete_or_error(self):
        """Simulates a redelivered/duplicate Celery task run."""
        first = delete_blocked_media()
        second = delete_blocked_media()

        self.assertEqual(first, {"deleted": 1, "errors": 0})
        self.assertEqual(second, {"deleted": 0, "errors": 0})

    def test_a_failure_deleting_one_scan_does_not_block_the_others(self):
        other_path = "broadcast_videos/blocked-delete-test-2.bin"
        default_storage.save(other_path, ContentFile(b"more fake content"))
        self.addCleanup(lambda: default_storage.exists(other_path) and default_storage.delete(other_path))
        other_video = BroadcastVideo.objects.create(
            title="t2", creator=self.user, video_url="", mime_type="video/mp4",
            storage_path=other_path, type="video",
            moderation_status=BroadcastVideo.ModerationStatus.BLOCKED, is_active=False,
        )
        MediaSafetyScan.objects.create(
            owner=self.user, upload_id=other_path, context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="blocked", quarantine=True, requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
            result={"resolution_target": "broadcast_video", "resolution_id": str(other_video.id), "storage_path": other_path},
            scheduled_deletion_at=timezone.now() - timedelta(minutes=1),
        )

        real_exists = default_storage.exists

        def flaky_exists(path):
            if path == self.storage_path:
                raise RuntimeError("simulated storage failure")
            return real_exists(path)

        with patch.object(default_storage, "exists", side_effect=flaky_exists):
            result = delete_blocked_media()

        self.assertEqual(result, {"deleted": 1, "errors": 1})
        # The failing one's file is untouched (its own deleted_at marker
        # was still set before the failure per the documented tradeoff,
        # matching apps.accounts.tasks.purge_accounts_past_grace_period).
        self.scan.refresh_from_db()
        self.assertIsNotNone(self.scan.deleted_at)
        # The other, unrelated scan succeeded independently.
        self.assertFalse(BroadcastVideo.objects.filter(id=other_video.id).exists())
