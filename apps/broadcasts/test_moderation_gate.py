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

from apps.broadcasts.models import BroadcastVideo
from apps.broadcasts.moderation_gate import apply_moderation_decision, filter_broadcast_eligible, is_broadcast_eligible

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
