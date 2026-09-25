"""
Proves the single authoritative "may this go public" rule: an AI content-
safety verdict alone is never sufficient, only an explicit, unexpired
human PASS is. Every other moderation_status, and every expired PASS, must
be rejected by both the public listing queryset and the playback endpoint
- fail closed, not fail open.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.broadcasts.models import BroadcastVideo, ChannelContent, ChannelContentAsset
from apps.broadcasts.moderation_gate import (
    apply_moderation_decision,
    filter_broadcast_eligible,
    is_broadcast_eligible,
    resolve_and_apply_moderation_decision,
)

User = get_user_model()


def _make_user(phone_suffix: str):
    return User.objects.create_user(phone=f"555910{phone_suffix}", username=f"u{phone_suffix}", password="secret", country="NG")


def _make_video(creator, **overrides):
    defaults = dict(
        title="A sermon", creator=creator, video_url="", mime_type="video/mp4",
        storage_path="broadcast_videos/x.mp4", type="video",
    )
    defaults.update(overrides)
    return BroadcastVideo.objects.create(**defaults)


class IsBroadcastEligibleUnitTests(TestCase):
    """Every non-PASSED state, and every expired PASSED, must be rejected."""

    def setUp(self):
        self.owner = _make_user("0001")

    def test_pending_review_default_is_not_eligible(self):
        video = _make_video(self.owner)
        self.assertEqual(video.moderation_status, BroadcastVideo.ModerationStatus.PENDING_REVIEW)
        self.assertFalse(is_broadcast_eligible(video))

    def test_blocked_is_not_eligible(self):
        video = _make_video(self.owner, moderation_status=BroadcastVideo.ModerationStatus.BLOCKED)
        self.assertFalse(is_broadcast_eligible(video))

    def test_deleted_is_not_eligible(self):
        video = _make_video(self.owner, moderation_status=BroadcastVideo.ModerationStatus.DELETED)
        self.assertFalse(is_broadcast_eligible(video))

    def test_passed_with_no_expiry_set_is_not_eligible(self):
        # Should never happen via apply_moderation_decision(), but if a
        # PASSED row somehow has no expiry, it must NOT be trusted as
        # permanently valid - fail closed on missing data, not fail open.
        video = _make_video(self.owner, moderation_status=BroadcastVideo.ModerationStatus.PASSED, moderation_expires_at=None)
        self.assertFalse(is_broadcast_eligible(video))

    def test_passed_but_expired_is_not_eligible(self):
        video = _make_video(
            self.owner,
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_passed_at=timezone.now() - timedelta(days=200),
            moderation_expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertFalse(is_broadcast_eligible(video))

    def test_passed_and_not_yet_expired_is_eligible(self):
        video = _make_video(
            self.owner,
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + timedelta(days=90),
        )
        self.assertTrue(is_broadcast_eligible(video))


class ApplyModerationDecisionTests(TestCase):
    def setUp(self):
        self.owner = _make_user("0002")
        self.moderator = _make_user("0003")

    def test_pass_sets_expiry_in_the_future_per_settings_window(self):
        video = _make_video(self.owner)
        with self.settings(BROADCAST_MODERATION_REVALIDATION_DAYS=30):
            apply_moderation_decision(video, action="pass", actor=self.moderator)
        video.refresh_from_db()
        self.assertEqual(video.moderation_status, BroadcastVideo.ModerationStatus.PASSED)
        self.assertIsNotNone(video.moderation_passed_at)
        self.assertAlmostEqual(
            (video.moderation_expires_at - timezone.now()).total_seconds(),
            timedelta(days=30).total_seconds(),
            delta=5,
        )
        self.assertEqual(video.moderation_reviewed_by_id, self.moderator.id)
        self.assertTrue(is_broadcast_eligible(video))

    def test_multiple_passes_over_lifetime_each_extend_a_fresh_window(self):
        """Supports repeated re-validation, not just one initial check."""
        video = _make_video(self.owner)
        with self.settings(BROADCAST_MODERATION_REVALIDATION_DAYS=1):
            apply_moderation_decision(video, action="pass", actor=self.moderator)
        video.refresh_from_db()
        first_expiry = video.moderation_expires_at

        # Simulate the approval having expired.
        video.moderation_expires_at = timezone.now() - timedelta(hours=1)
        video.save(update_fields=["moderation_expires_at"])
        self.assertFalse(is_broadcast_eligible(video))

        # A second, later pass re-establishes eligibility with a fresh window.
        with self.settings(BROADCAST_MODERATION_REVALIDATION_DAYS=90):
            apply_moderation_decision(video, action="pass", actor=self.moderator)
        video.refresh_from_db()
        self.assertTrue(is_broadcast_eligible(video))
        self.assertGreater(video.moderation_expires_at, first_expiry)

    def test_pending_action_revokes_eligibility(self):
        video = _make_video(
            self.owner, moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=90),
        )
        apply_moderation_decision(video, action="pending", actor=self.moderator)
        video.refresh_from_db()
        self.assertFalse(is_broadcast_eligible(video))

    def test_block_action_deactivates_and_revokes_eligibility(self):
        video = _make_video(
            self.owner, moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=90),
        )
        apply_moderation_decision(video, action="block", actor=self.moderator)
        video.refresh_from_db()
        self.assertEqual(video.moderation_status, BroadcastVideo.ModerationStatus.BLOCKED)
        self.assertFalse(video.is_active)
        self.assertFalse(is_broadcast_eligible(video))

    def test_delete_action_deactivates_and_revokes_eligibility(self):
        video = _make_video(
            self.owner, moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=90),
        )
        apply_moderation_decision(video, action="delete", actor=self.moderator)
        video.refresh_from_db()
        self.assertEqual(video.moderation_status, BroadcastVideo.ModerationStatus.DELETED)
        self.assertFalse(video.is_active)
        self.assertFalse(is_broadcast_eligible(video))

    def test_unknown_action_raises(self):
        video = _make_video(self.owner)
        with self.assertRaises(ValueError):
            apply_moderation_decision(video, action="approve-forever", actor=self.moderator)


class FilterBroadcastEligibleQuerysetTests(TestCase):
    def setUp(self):
        self.owner = _make_user("0004")

    def test_queryset_only_returns_passed_unexpired_rows(self):
        eligible = _make_video(
            self.owner, title="eligible",
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=1),
        )
        _make_video(self.owner, title="pending")
        _make_video(self.owner, title="blocked", moderation_status=BroadcastVideo.ModerationStatus.BLOCKED)
        _make_video(
            self.owner, title="expired",
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() - timedelta(days=1),
        )
        ids = set(filter_broadcast_eligible(BroadcastVideo.objects.all()).values_list("id", flat=True))
        self.assertEqual(ids, {eligible.id})


class PublicEndpointEnforcementTests(TestCase):
    """End-to-end through the real views, not just the helper functions -
    proves the backend enforces this, not only a UI affordance."""

    def setUp(self):
        self.owner = _make_user("0005")
        self.viewer = _make_user("0006")

    def test_list_view_never_returns_a_non_passed_video(self):
        _make_video(self.owner, title="pending review")
        _make_video(self.owner, title="blocked", moderation_status=BroadcastVideo.ModerationStatus.BLOCKED)
        _make_video(
            self.owner, title="expired approval",
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() - timedelta(minutes=1),
        )
        client = APIClient()
        client.force_authenticate(user=self.viewer)
        resp = client.get("/api/v1/broadcasts/videos/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_list_view_returns_a_currently_passed_video(self):
        video = _make_video(
            self.owner, title="passed",
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=1),
        )
        client = APIClient()
        client.force_authenticate(user=self.viewer)
        resp = client.get("/api/v1/broadcasts/videos/")
        self.assertEqual(resp.status_code, 200)
        returned_ids = {row["id"] for row in resp.data}
        self.assertIn(str(video.id), returned_ids)

    def test_stream_view_404s_for_a_stranger_on_pending_review_video(self):
        video = _make_video(self.owner)  # PENDING_REVIEW by default
        client = APIClient()
        client.force_authenticate(user=self.viewer)
        resp = client.get(f"/api/v1/broadcasts/videos/{video.id}/stream/")
        self.assertEqual(resp.status_code, 404)

    def test_stream_view_404s_once_a_pass_expires(self):
        video = _make_video(
            self.owner,
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() - timedelta(seconds=1),
        )
        client = APIClient()
        client.force_authenticate(user=self.viewer)
        resp = client.get(f"/api/v1/broadcasts/videos/{video.id}/stream/")
        self.assertEqual(resp.status_code, 404)

    @override_settings(
        MEDIA_ROOT=tempfile.mkdtemp(),
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
    )
    def test_stream_view_owner_can_preview_their_own_pending_video(self):
        """A creator previewing their own not-yet-passed upload is not
        "public" distribution - the one deliberate exception to the gate.
        A real on-disk file is required here so a 200 unambiguously proves
        the moderation gate let the owner through, rather than both the
        gate and a missing file producing the same 404 either way."""
        from django.conf import settings as django_settings

        storage_path = f"broadcast_videos/{uuid.uuid4()}.mp4"
        full_path = os.path.join(django_settings.MEDIA_ROOT, storage_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as fh:
            fh.write(b"fake video bytes")

        video = _make_video(self.owner, storage_path=storage_path)  # PENDING_REVIEW by default

        client = APIClient()
        client.force_authenticate(user=self.owner)
        owner_resp = client.get(f"/api/v1/broadcasts/videos/{video.id}/stream/")
        self.assertEqual(owner_resp.status_code, 200)

        stranger = APIClient()
        stranger.force_authenticate(user=self.viewer)
        stranger_resp = stranger.get(f"/api/v1/broadcasts/videos/{video.id}/stream/")
        self.assertEqual(stranger_resp.status_code, 404)


class ValidateAssetReadyForPublishLiveCheckTests(TestCase):
    """Regression coverage for a real production bug: an admin passing a
    BroadcastVideo through the moderation website correctly flipped
    moderation_status to PASSED, but validate_asset_ready_for_publish (the
    gate BroadcastFeedEntryBroadcastView calls) only ever looked at a
    processing_status string frozen into the feed entry's JSON attachment
    payload at upload time - before any human review happened - so an
    already-passed video stayed permanently stuck reporting "still under
    review" to its own uploader trying to broadcast it."""

    def setUp(self):
        self.owner = _make_user("0007")

    def _attachment(self, video, **overrides):
        payload = {
            "id": str(video.id),
            "asset_type": "video",
            # The stale snapshot every real upload freezes in before any
            # human review - BLOCKED_STATUSES includes "pending_review".
            "processing_status": "pending_review",
        }
        payload.update(overrides)
        return payload

    def test_rejects_a_video_still_pending_review(self):
        from apps.broadcasts.media_pipeline import validate_asset_ready_for_publish
        from rest_framework.exceptions import ValidationError

        video = _make_video(self.owner)  # PENDING_REVIEW by default
        with self.assertRaises(ValidationError):
            validate_asset_ready_for_publish(self._attachment(video))

    def test_accepts_an_already_passed_video_despite_a_stale_pending_snapshot(self):
        from apps.broadcasts.media_pipeline import validate_asset_ready_for_publish

        video = _make_video(
            self.owner,
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=90),
        )
        # No exception raised is the assertion - the stale
        # processing_status="pending_review" snapshot must be ignored in
        # favor of the video's current, live moderation_status.
        validate_asset_ready_for_publish(self._attachment(video))

    def test_rejects_a_video_whose_pass_has_since_expired_despite_a_stale_ready_snapshot(self):
        from apps.broadcasts.media_pipeline import validate_asset_ready_for_publish
        from rest_framework.exceptions import ValidationError

        video = _make_video(
            self.owner,
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_expires_at=timezone.now() - timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            validate_asset_ready_for_publish(self._attachment(video, processing_status="ready"))

    def test_full_broadcast_flow_no_longer_reports_still_under_review_once_passed(self):
        """End-to-end through validate_feed_entry_ready_for_broadcast, the
        exact function BroadcastFeedEntryBroadcastView calls - proves the
        fix at the level the original bug report was actually filed at."""
        from apps.broadcasts.media_pipeline import validate_feed_entry_ready_for_broadcast

        video = _make_video(self.owner)
        entry = {"attachment": self._attachment(video)}
        with self.assertRaises(Exception):
            validate_feed_entry_ready_for_broadcast(entry)

        apply_moderation_decision(video, action="pass", actor=self.owner)
        video.refresh_from_db()
        # Still-stale entry dict (as a real one would be, unless the
        # composer happened to re-fetch it) - must succeed anyway now.
        validate_feed_entry_ready_for_broadcast(entry)

    def test_falls_back_to_snapshot_check_when_no_matching_broadcastvideo_exists(self):
        """Channel-content images/videos scanned via the separate,
        synchronous scan_channel_asset_payload_for_explicit_content path
        have no BroadcastVideo row and no follow-up human-review step -
        the snapshot-based check must still apply to those."""
        from apps.broadcasts.media_pipeline import validate_asset_ready_for_publish
        from rest_framework.exceptions import ValidationError

        payload = {"asset_type": "image", "processing_status": "pending_review"}
        with self.assertRaises(ValidationError):
            validate_asset_ready_for_publish(payload)

        payload_ready = {"asset_type": "image", "processing_status": "ready"}
        validate_asset_ready_for_publish(payload_ready)  # no exception


def _make_channel(owner):
    from apps.broadcasts.models import BroadcastChannel

    return BroadcastChannel.objects.create(
        owner_type=BroadcastChannel.OwnerType.USER, owner_id=owner.id, owner_user=owner,
        handle=f"moderation-gate-test-channel-{uuid.uuid4().hex[:8]}", display_name="Moderation Gate Test Channel",
    )


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(),
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class ChannelContentModerationDeleteTests(TestCase):
    """Regression coverage: the content-inspection website could only ever
    delete broadcast_video content - channel_content (the other real media
    type in the same moderation queue) had no gate wired up at all, and
    nothing anywhere purged a deleted piece of content's assets from
    storage. resolve_and_apply_moderation_decision is the same dispatcher
    admin_control's moderate endpoint calls."""

    def setUp(self):
        self.owner = _make_user("0008")
        self.channel = _make_channel(self.owner)
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type="video", title="A testimony video",
        )
        from django.conf import settings as django_settings

        self.storage_path = f"channel_content/{uuid.uuid4()}.mp4"
        full_path = os.path.join(django_settings.MEDIA_ROOT, self.storage_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as fh:
            fh.write(b"fake channel content video bytes")
        self.asset = ChannelContentAsset.objects.create(
            content=self.content, asset_type="video", storage_path=self.storage_path,
        )

    def test_channel_content_is_in_moderatable_target_types(self):
        from apps.broadcasts.moderation_gate import MODERATABLE_TARGET_TYPES

        self.assertIn("channel_content", MODERATABLE_TARGET_TYPES)

    def test_delete_soft_deletes_and_purges_storage(self):
        from django.core.files.storage import default_storage

        full_path_exists_before = default_storage.exists(self.storage_path)
        self.assertTrue(full_path_exists_before)

        result = resolve_and_apply_moderation_decision(
            "channel_content", str(self.content.id), action="delete", actor=self.owner,
        )
        self.assertTrue(result)

        self.content.refresh_from_db()
        self.assertTrue(self.content.is_deleted)
        self.assertEqual(self.content.status, ChannelContent.Status.ARCHIVED)
        self.assertEqual(self.content.visibility, ChannelContent.Visibility.PRIVATE)
        self.assertFalse(default_storage.exists(self.storage_path))

    def test_non_delete_action_is_a_no_op_and_returns_false(self):
        """ChannelContent has no pass/pending/block review lifecycle -
        only delete means anything for it."""
        result = resolve_and_apply_moderation_decision(
            "channel_content", str(self.content.id), action="pass", actor=self.owner,
        )
        self.assertFalse(result)
        self.content.refresh_from_db()
        self.assertFalse(self.content.is_deleted)

    def test_unknown_target_id_returns_false_without_raising(self):
        result = resolve_and_apply_moderation_decision(
            "channel_content", str(uuid.uuid4()), action="delete", actor=self.owner,
        )
        self.assertFalse(result)

    def test_self_service_delete_also_purges_storage(self):
        """ChannelContentDetailView.delete() - the user-facing endpoint,
        not the admin one - must purge storage the same way."""
        from django.core.files.storage import default_storage

        client = APIClient()
        client.force_authenticate(user=self.owner)
        resp = client.delete(f"/api/v1/broadcasts/channel-contents/{self.content.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(default_storage.exists(self.storage_path))
