"""
Pure housekeeping sweep - apps.media.tasks.sync_orphaned_media_records().
Distinct from delete_blocked_media(): this never makes a moderation
decision, it only ever reacts to storage state that is already true (the
file is gone), so the database never keeps a dangling reference.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from apps.broadcasts.models import BroadcastVideo
from apps.media.models import MediaSafetyScan
from apps.media.tasks import sync_orphaned_media_records

User = get_user_model()


class SyncOrphanedMediaRecordsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670009001", password="TestPass123!", country="CM")

    def test_scan_with_a_missing_file_is_marked_deleted_and_its_content_row_removed(self):
        video = BroadcastVideo.objects.create(
            title="orphan test", creator=self.user, video_url="", mime_type="video/mp4",
            storage_path="broadcast_videos/never-existed.mp4", type="video",
            moderation_status=BroadcastVideo.ModerationStatus.BLOCKED, is_active=False,
        )
        scan = MediaSafetyScan.objects.create(
            owner=self.user, upload_id="broadcast_videos/never-existed.mp4", context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="blocked", quarantine=True, requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
            result={
                "resolution_target": "broadcast_video", "resolution_id": str(video.id),
                "storage_path": "broadcast_videos/never-existed.mp4",
            },
        )
        # Deliberately never write anything to this storage_path - the file
        # was never there / is already gone.

        result = sync_orphaned_media_records()

        self.assertEqual(result, {"synced": 1, "errors": 0})
        scan.refresh_from_db()
        self.assertIsNotNone(scan.deleted_at)
        self.assertFalse(BroadcastVideo.objects.filter(id=video.id).exists())

    def test_scan_whose_file_genuinely_exists_is_left_completely_alone(self):
        storage_path = "broadcast_videos/still-here.mp4"
        default_storage.save(storage_path, ContentFile(b"real content"))
        self.addCleanup(lambda: default_storage.exists(storage_path) and default_storage.delete(storage_path))

        video = BroadcastVideo.objects.create(
            title="not orphaned", creator=self.user, video_url="", mime_type="video/mp4",
            storage_path=storage_path, type="video",
        )
        scan = MediaSafetyScan.objects.create(
            owner=self.user, upload_id=storage_path, context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="passed", quarantine=False, requires_review=False,
            result={"resolution_target": "broadcast_video", "resolution_id": str(video.id), "storage_path": storage_path},
        )

        result = sync_orphaned_media_records()

        self.assertEqual(result, {"synced": 0, "errors": 0})
        scan.refresh_from_db()
        self.assertIsNone(scan.deleted_at)
        self.assertTrue(BroadcastVideo.objects.filter(id=video.id).exists())

    def test_already_deleted_scan_is_never_reprocessed(self):
        from django.utils import timezone

        scan = MediaSafetyScan.objects.create(
            owner=self.user, upload_id="broadcast_videos/x.mp4", context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="blocked", quarantine=True, requires_review=False,
            reason="nudenet_explicit:FEMALE_BREAST_EXPOSED",
            deleted_at=timezone.now(),
            result={"storage_path": "broadcast_videos/x.mp4"},
        )
        result = sync_orphaned_media_records()
        self.assertEqual(result, {"synced": 0, "errors": 0})

    def test_scan_with_no_storage_path_is_skipped_not_errored(self):
        MediaSafetyScan.objects.create(
            owner=self.user, upload_id="", context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="not_configured", quarantine=False, requires_review=False,
            result={},
        )
        result = sync_orphaned_media_records()
        self.assertEqual(result, {"synced": 0, "errors": 0})

    def test_applies_regardless_of_scan_status_not_only_blocked(self):
        """This is deliberate housekeeping, not a moderation judgment - a
        passed scan whose file went missing (e.g. deleted out-of-band) is
        just as orphaned as a blocked one."""
        video = BroadcastVideo.objects.create(
            title="passed but orphaned", creator=self.user, video_url="", mime_type="video/mp4",
            storage_path="broadcast_videos/passed-orphan.mp4", type="video",
        )
        scan = MediaSafetyScan.objects.create(
            owner=self.user, upload_id="broadcast_videos/passed-orphan.mp4", context="broadcast", mime_type="video/mp4",
            provider="nudenet", status="passed", quarantine=False, requires_review=False,
            result={
                "resolution_target": "broadcast_video", "resolution_id": str(video.id),
                "storage_path": "broadcast_videos/passed-orphan.mp4",
            },
        )
        result = sync_orphaned_media_records()
        self.assertEqual(result, {"synced": 1, "errors": 0})
        scan.refresh_from_db()
        self.assertIsNotNone(scan.deleted_at)
