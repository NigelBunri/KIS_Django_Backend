from __future__ import annotations

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.broadcasts.models import BroadcastVideo, EducationInstitution, EducationInstitutionMaterial
from apps.media.content_safety_provider import ContentSafetyProviderError
from apps.media.models import MediaAsset, MediaSafetyScan
from apps.media.safety import NUDENET_SCAN_QUEUED_REASON, queued_for_async_scan_decision
from apps.media.tasks import ContentSafetyResolutionTarget, scan_video_and_resolve_task


def _make_queued_scan(*, resolution_target: str, resolution_id: str, storage_path="videos/x.mp4", mime_type="video/mp4"):
    decision = queued_for_async_scan_decision()
    return MediaSafetyScan.objects.create(
        upload_id=uuid.uuid4().hex,
        context="broadcast",
        mime_type=mime_type,
        provider=decision.provider,
        status=decision.status,
        quarantine=decision.quarantine,
        requires_review=decision.requires_review,
        policy_version=decision.policy_version,
        reason=decision.reason,
        result={
            **decision.as_metadata(),
            "resolution_target": resolution_target,
            "resolution_id": resolution_id,
            "storage_path": storage_path,
            "mime_type": mime_type,
        },
    )


@override_settings(
    MEDIA_SAFETY_SERVICE_ENABLED=True,
    MEDIA_SAFETY_SERVICE_BASE_URL="https://content-safety.internal",
    MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN="test-shared-secret",
)
class ScanVideoAndResolveTaskTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5559100001", username="cs_owner", password="secret", country="NG")

    @patch("apps.media.tasks.default_storage")
    @patch("apps.media.content_safety_provider._requests")
    def test_resolves_broadcast_video_clean(self, mock_requests, mock_storage):
        from unittest.mock import MagicMock
        import io

        video = BroadcastVideo.objects.create(
            title="t", creator=self.owner, video_url="",
            mime_type="video/mp4", storage_path="videos/x.mp4", type="video",
        )
        scan = _make_queued_scan(resolution_target=ContentSafetyResolutionTarget.BROADCAST_VIDEO.value, resolution_id=str(video.id))

        mock_storage.exists.return_value = True
        mock_storage.open.return_value.__enter__.return_value = io.BytesIO(b"fake")
        mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": None, "score": 0.0})

        result = scan_video_and_resolve_task(scan_id=str(scan.id))

        self.assertEqual(result["status"], "resolved")
        scan.refresh_from_db()
        self.assertEqual(scan.status, "passed")
        video.refresh_from_db()
        # An AI-clean scan must NEVER be sufficient to make a video public
        # on its own (apps.broadcasts.moderation_gate) - only an explicit,
        # unexpired human PASS does. video_url/moderation_status are
        # untouched by the AI resolution regardless of its verdict.
        self.assertEqual(video.video_url, "")
        self.assertEqual(video.moderation_status, BroadcastVideo.ModerationStatus.PENDING_REVIEW)

    @patch("apps.media.tasks.default_storage")
    @patch("apps.media.content_safety_provider._requests")
    def test_resolves_broadcast_video_blocked_leaves_url_empty(self, mock_requests, mock_storage):
        from unittest.mock import MagicMock
        import io

        video = BroadcastVideo.objects.create(
            title="t", creator=self.owner, video_url="",
            mime_type="video/mp4", storage_path="videos/x.mp4", type="video",
        )
        scan = _make_queued_scan(resolution_target=ContentSafetyResolutionTarget.BROADCAST_VIDEO.value, resolution_id=str(video.id))

        mock_storage.exists.return_value = True
        mock_storage.open.return_value.__enter__.return_value = io.BytesIO(b"fake")
        mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": "BUTTOCKS_EXPOSED", "score": 0.9})

        scan_video_and_resolve_task(scan_id=str(scan.id))

        scan.refresh_from_db()
        self.assertEqual(scan.status, "blocked")
        video.refresh_from_db()
        self.assertEqual(video.video_url, "")

    @patch("apps.media.tasks.default_storage")
    @patch("apps.media.content_safety_provider._requests")
    def test_resolves_media_asset(self, mock_requests, mock_storage):
        from unittest.mock import MagicMock
        import io

        asset = MediaAsset.objects.create(
            owner=self.owner, type="video", bucket_key="uploads/x/video.mp4",
            status="pending", storage={"visibility": "public", "scan_status": "pending_review"},
        )
        scan = _make_queued_scan(resolution_target=ContentSafetyResolutionTarget.MEDIA_ASSET.value, resolution_id=str(asset.id))

        mock_storage.exists.return_value = True
        mock_storage.open.return_value.__enter__.return_value = io.BytesIO(b"fake")
        mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": None, "score": 0.0})

        scan_video_and_resolve_task(scan_id=str(scan.id))

        asset.refresh_from_db()
        self.assertEqual(asset.status, "ready")
        self.assertTrue(asset.canonical_url)
        self.assertEqual(asset.storage["scan_status"], "passed")

    @patch("apps.media.tasks.default_storage")
    @patch("apps.media.content_safety_provider._requests")
    def test_resolves_education_material(self, mock_requests, mock_storage):
        from unittest.mock import MagicMock
        import io

        institution = EducationInstitution.objects.create(owner=self.owner, name="Academy")
        material = EducationInstitutionMaterial.objects.create(
            institution=institution, title="Lecture", kind="video",
            # storage_path is deliberately NOT set here and never read by
            # the resolver - EducationInstitutionMaterial.storage_path is
            # never actually populated by the real creation path either
            # (the object key lives in resource_url instead, see
            # _education_material_media_payload); the resolver uses the
            # storage_path tracked on the scan row itself, asserted below.
            resource_url="",
            metadata={"media_safety": {"status": "pending_review", "quarantined": True, "requires_review": True}},
        )
        scan = _make_queued_scan(
            resolution_target=ContentSafetyResolutionTarget.EDUCATION_MATERIAL.value,
            resolution_id=str(material.id),
            storage_path="edu/x.mp4",
        )

        mock_storage.exists.return_value = True
        mock_storage.open.return_value.__enter__.return_value = io.BytesIO(b"fake")
        mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": None, "score": 0.0})

        scan_video_and_resolve_task(scan_id=str(scan.id))

        material.refresh_from_db()
        self.assertEqual(material.resource_url, "edu/x.mp4")
        self.assertEqual(material.metadata["media_safety"]["status"], "passed")
        self.assertFalse(material.metadata["media_safety"]["quarantined"])

    @patch("apps.media.tasks.default_storage")
    def test_idempotent_skips_already_resolved_scan(self, mock_storage):
        video = BroadcastVideo.objects.create(
            title="t", creator=self.owner, video_url="",
            mime_type="video/mp4", storage_path="videos/x.mp4", type="video",
        )
        scan = _make_queued_scan(resolution_target=ContentSafetyResolutionTarget.BROADCAST_VIDEO.value, resolution_id=str(video.id))
        scan.status = "passed"
        scan.reason = "nudenet_clean"
        scan.save(update_fields=["status", "reason"])

        result = scan_video_and_resolve_task(scan_id=str(scan.id))

        self.assertEqual(result["status"], "already_resolved")
        mock_storage.exists.assert_not_called()

    @patch("apps.media.tasks.default_storage")
    def test_second_run_after_resolution_is_also_a_no_op(self, mock_storage):
        """Simulates a redelivered/duplicate task after the first run
        already resolved it — must not re-scan or re-apply, even though
        nothing external changed."""
        import io
        from unittest.mock import MagicMock

        video = BroadcastVideo.objects.create(
            title="t", creator=self.owner, video_url="",
            mime_type="video/mp4", storage_path="videos/x.mp4", type="video",
        )
        scan = _make_queued_scan(resolution_target=ContentSafetyResolutionTarget.BROADCAST_VIDEO.value, resolution_id=str(video.id))

        mock_storage.exists.return_value = True
        mock_storage.open.return_value.__enter__.return_value = io.BytesIO(b"fake")
        with patch("apps.media.content_safety_provider._requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": "BUTTOCKS_EXPOSED", "score": 0.9})
            scan_video_and_resolve_task(scan_id=str(scan.id))

        video.refresh_from_db()
        self.assertEqual(video.video_url, "")

        # Second run: attacker/retry tries to resolve it clean this time.
        with patch("apps.media.content_safety_provider._requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(ok=True, json=lambda: {"label": None, "score": 0.0})
            result = scan_video_and_resolve_task(scan_id=str(scan.id))

        self.assertEqual(result["status"], "already_resolved")
        video.refresh_from_db()
        self.assertEqual(video.video_url, "")  # still blocked, not overwritten

    def test_missing_scan_returns_missing(self):
        result = scan_video_and_resolve_task(scan_id=str(uuid.uuid4()))
        self.assertEqual(result["status"], "missing")

    @patch("apps.media.tasks.default_storage")
    def test_storage_missing_marks_failed_not_stuck(self, mock_storage):
        video = BroadcastVideo.objects.create(
            title="t", creator=self.owner, video_url="",
            mime_type="video/mp4", storage_path="videos/x.mp4", type="video",
        )
        scan = _make_queued_scan(resolution_target=ContentSafetyResolutionTarget.BROADCAST_VIDEO.value, resolution_id=str(video.id))
        mock_storage.exists.return_value = False

        result = scan_video_and_resolve_task(scan_id=str(scan.id))

        self.assertEqual(result["status"], "failed")
        scan.refresh_from_db()
        self.assertEqual(scan.status, "failed")
        self.assertNotEqual(scan.reason, NUDENET_SCAN_QUEUED_REASON)

    @patch("apps.media.tasks.default_storage")
    @patch("apps.media.content_safety_provider._requests")
    def test_network_failure_retries_then_fails_visibly(self, mock_requests, mock_storage):
        """Celery's eager test mode has no real broker to hand a retry off
        to, so self.retry() just raises Retry once rather than looping -
        a known Celery testing limitation, not something specific to this
        task. Simulating retries-already-exhausted (self.retry raising
        MaxRetriesExceededError, which is what a real worker eventually
        does after 3 real attempts) is the standard way to test this
        branch without a real broker."""
        import io

        video = BroadcastVideo.objects.create(
            title="t", creator=self.owner, video_url="",
            mime_type="video/mp4", storage_path="videos/x.mp4", type="video",
        )
        scan = _make_queued_scan(resolution_target=ContentSafetyResolutionTarget.BROADCAST_VIDEO.value, resolution_id=str(video.id))

        mock_storage.exists.return_value = True
        mock_storage.open.return_value.__enter__.return_value = io.BytesIO(b"fake")
        mock_requests.post.side_effect = ContentSafetyProviderError("content-safety request failed: reset")

        with patch.object(
            scan_video_and_resolve_task, "retry",
            side_effect=scan_video_and_resolve_task.MaxRetriesExceededError(),
        ):
            result = scan_video_and_resolve_task(scan_id=str(scan.id))

        self.assertEqual(result["status"], "failed")
        scan.refresh_from_db()
        self.assertEqual(scan.status, "failed")
        self.assertTrue(scan.reason.startswith("content_safety_scan_error:"))
        video.refresh_from_db()
        self.assertEqual(video.video_url, "")
