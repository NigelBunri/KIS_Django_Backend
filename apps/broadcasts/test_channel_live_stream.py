"""
ChannelLiveStream had zero dedicated test coverage despite being one of
the largest, most security-sensitive surfaces in this app (ingest URLs,
provider webhooks, a WHIP proxy that does a raw server-side HTTP fetch).
This covers the real end-to-end lifecycle (schedule -> go live -> viewers
can actually see/react to it -> end -> VOD auto-archive) plus the specific
gaps found and fixed during the P0.4 live-streaming closure pass:

  - Going live must not leave the stream's ChannelContent stuck behind
    the ChannelContent moderation gate (added separately in P0.2) -
    live content is auto-passed on first go-live, not pre-reviewed.
  - A moderator's BLOCKED decision on a live content row must survive a
    later Start/webhook-live call, not get silently re-passed.
  - Editing title/description on a live stream must not re-trigger the
    VOD re-review-on-edit reset (that would pull an in-progress live
    stream out of public view).
  - ChannelLiveStreamSerializer must never leak metadata["whip_url"] (an
    ingest credential) to a non-manager viewer.
  - ChannelLiveStreamWhipView must require edit permission (stream_id is
    a public, non-secret value - previously anyone could hijack ingest).
  - ChannelLiveStreamArchiveView and ChannelLiveStreamWebhookView's
    generic fallback must fail CLOSED (reject) when
    LIVE_STREAM_WEBHOOK_SECRET is unset, not skip verification entirely -
    an unset secret previously meant *no* signature check at all.
  - Chat message display_name must come from the account, not the
    request body, for authenticated posters.
  - Chat send/history must use stable "message"/"display_name"/
    "created_at" field names on the wire - a prior mobile/backend
    mismatch on these exact keys silently broke every live chat send.

Run:
  python3 manage.py test apps.broadcasts.test_channel_live_stream --keepdb -v 2
"""
import os
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription
from apps.accounts.tiers import ensure_default_account_tiers

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelChatBan,
    ChannelContent,
    ChannelLiveStream,
    ChannelModerationRecord,
    ContentModerationStatus,
    LiveChatMessage,
)
from apps.moderation.models import UserBlock

User = get_user_model()


class LiveStreamTestBase(TestCase):
    def setUp(self):
        # Django TestCase wraps the DB in a per-test transaction rollback,
        # but NOT the cache - a cache.add()-based dedup key (webhook replay
        # guard, view/viewer-ping dedup) set by one test method is still
        # present for the next one in the same process. Several tests reuse
        # the same literal provider_stream_id ("provider-stream-abc" etc.)
        # across methods, which otherwise makes a later test's "first"
        # webhook call get silently treated as a duplicate of an earlier
        # test's call to the same key, well within any short TTL - found the
        # hard way when adding ChannelLiveStreamWebhookView's dedup guard.
        cache.clear()
        self.owner = User.objects.create_user(phone="+2349120000001", password="pw123456", country="NG")
        self.viewer = User.objects.create_user(phone="+2349120000002", password="pw123456", country="NG")
        self.other = User.objects.create_user(phone="+2349120000003", password="pw123456", country="NG")
        ensure_default_account_tiers()
        partner_tier = AccountTier.objects.filter(name__iexact="Partner").first()
        assert partner_tier, "Partner tier must exist (seeded by ensure_default_account_tiers)"
        Subscription.objects.create(user=self.owner, tier=partner_tier, status="active")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle="live-channel",
            display_name="Live Channel",
            is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.client = APIClient()

    def _schedule_stream(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "Sunday Service"},
            format="json",
        )
        assert res.status_code == status.HTTP_201_CREATED, res.data
        return res.data


class LiveStreamSchedulingTests(LiveStreamTestBase):
    def test_scheduling_requires_live_streaming_feature(self):
        free_owner = User.objects.create_user(phone="+2349120000099", password="pw123456", country="NG")
        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=free_owner.id, owner_user=free_owner,
            handle="free-channel", display_name="Free Channel", is_public=True,
        )
        self.client.force_authenticate(free_owner)

        res = self.client.post(f"/api/v1/broadcasts/channels/{channel.id}/live-streams/", {"title": "x"}, format="json")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_scheduling_requires_title(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/", {}, format="json")

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scheduling_creates_scheduled_content_not_yet_public(self):
        data = self._schedule_stream()
        live = ChannelLiveStream.objects.get(id=data["id"])

        self.assertEqual(live.status, ChannelLiveStream.Status.SCHEDULED)
        self.assertIsNotNone(live.content_id)
        self.assertEqual(live.content.status, ChannelContent.Status.SCHEDULED)
        self.assertEqual(live.content.moderation_status, ContentModerationStatus.PENDING_REVIEW)

    def test_client_cannot_override_reserved_metadata_keys(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "x", "metadata": {"whip_url": "http://169.254.169.254/", "custom_tag": "ok"}},
            format="json",
        )

        live = ChannelLiveStream.objects.get(id=res.data["id"])
        self.assertNotEqual(live.metadata.get("whip_url"), "http://169.254.169.254/")
        self.assertEqual(live.metadata.get("custom_tag"), "ok")

    def test_non_manager_cannot_schedule_stream(self):
        self.client.force_authenticate(self.viewer)

        res = self.client.post(f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/", {"title": "x"}, format="json")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_schedule_with_wait_image_happy_path(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "x", "thumbnail_url": "https://cdn.example.com/wait.jpg", "thumbnail_media_type": "image"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["thumbnail_url"], "https://cdn.example.com/wait.jpg")
        self.assertEqual(res.data["thumbnail_media_type"], "image")
        live = ChannelLiveStream.objects.get(id=res.data["id"])
        # A static image is safe to also show as the generic content-card
        # thumbnail (feeds/discovery render that as a plain <Image>).
        self.assertEqual(live.content.thumbnail_url, "https://cdn.example.com/wait.jpg")

    def test_schedule_with_wait_video_happy_path(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "x", "thumbnail_url": "https://cdn.example.com/wait.mp4", "thumbnail_media_type": "video"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["thumbnail_media_type"], "video")
        live = ChannelLiveStream.objects.get(id=res.data["id"])
        # A video wait-asset must NOT leak into the generic content-card
        # thumbnail, which only ever renders a plain <Image> - that would be
        # a broken thumbnail in every feed/discovery surface showing this
        # scheduled stream's content card.
        self.assertEqual(live.content.thumbnail_url, "")

    def test_schedule_with_realistic_presigned_wait_image_url_succeeds(self):
        # CONFIRMED ROOT CAUSE of "Could not schedule stream" whenever a
        # wait-image/video was set: ChannelLiveStream.thumbnail_url (and
        # playback_url/replay_url) used Django's default URLField
        # max_length=200 and were written via a plain .create() call with
        # no full_clean(), so a real presigned S3 GET URL (X-Amz-Signature/
        # Credential/Date/Expires/SignedHeaders query string, 250-400+
        # chars) hit Postgres directly as "value too long for type
        # character varying(200)" - same bug class already fixed once for
        # BroadcastChannel.avatar_url/banner_url. A short fixed test URL
        # like the happy-path test above never exercises this at all.
        self.client.force_authenticate(self.owner)
        realistic_presigned_url = (
            "https://kis-media-bucket.s3.eu-west-2.amazonaws.com/"
            "broadcast_live_wait_media/2026/10/24/"
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890-wait-screen.jpg"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=AKIAEXAMPLE1234567890%2F20261024%2Feu-west-2%2Fs3%2Faws4_request"
            "&X-Amz-Date=20261024T140000Z"
            "&X-Amz-Expires=604800"
            "&X-Amz-SignedHeaders=host"
            "&X-Amz-Signature=" + "a" * 64
        )
        self.assertGreater(len(realistic_presigned_url), 200)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "x", "thumbnail_url": realistic_presigned_url, "thumbnail_media_type": "image"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["thumbnail_url"], realistic_presigned_url)

    def test_schedule_rejects_invalid_thumbnail_media_type(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "x", "thumbnail_url": "https://cdn.example.com/wait.jpg", "thumbnail_media_type": "gif"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_schedule_with_no_thumbnail_leaves_media_type_blank(self):
        data = self._schedule_stream()

        self.assertEqual(data["thumbnail_url"], "")
        self.assertEqual(data["thumbnail_media_type"], "")

    def test_schedule_ignores_media_type_when_no_thumbnail_url(self):
        """A type with no asset is meaningless - the mobile pre-live screen's
        fallback-to-default-loading-screen check keys off thumbnail_url being
        blank, so this must not leave a dangling 'video'/'image' with nothing
        to render."""
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "x", "thumbnail_media_type": "video"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["thumbnail_media_type"], "")


class LiveNowDiscoveryTests(LiveStreamTestBase):
    """LiveNowView (C1 of the YouTube-parity batch) - cross-channel 'Live
    Now' discovery. No existing endpoint aggregated live streams across
    channels before this; these guard the two things that would otherwise
    silently leak: a private channel's live stream must never surface
    here, and a merely-scheduled (not yet live) stream must not appear
    either, since this is a 'what's live right now' rail, not a schedule."""

    def _url(self):
        return "/api/v1/broadcasts/live-streams/live-now/"

    def _go_live(self, live_data):
        self.client.force_authenticate(self.owner)
        res = self.client.post(f"/api/v1/broadcasts/live-streams/{live_data['id']}/start/")
        assert res.status_code == status.HTTP_200_OK, res.data

    def test_live_public_stream_appears(self):
        live_data = self._schedule_stream()
        self._go_live(live_data)

        res = self.client.get(self._url())

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        ids = [row["id"] for row in res.data["results"]]
        self.assertIn(live_data["id"], ids)

    def test_merely_scheduled_stream_does_not_appear(self):
        self._schedule_stream()

        res = self.client.get(self._url())

        self.assertEqual(res.data["results"], [])

    def test_private_channel_live_stream_does_not_appear(self):
        self.channel.is_public = False
        self.channel.save(update_fields=["is_public"])
        live_data = self._schedule_stream()
        self._go_live(live_data)

        res = self.client.get(self._url())

        ids = [row["id"] for row in res.data["results"]]
        self.assertNotIn(live_data["id"], ids)

    def test_anonymous_can_view(self):
        live_data = self._schedule_stream()
        self._go_live(live_data)
        self.client.force_authenticate(user=None)

        res = self.client.get(self._url())

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        ids = [row["id"] for row in res.data["results"]]
        self.assertIn(live_data["id"], ids)

    def test_ordered_by_viewer_count_descending(self):
        low_data = self._schedule_stream()
        self._go_live(low_data)
        ChannelLiveStream.objects.filter(id=low_data["id"]).update(viewer_count=2)

        high_channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="live-channel-2", display_name="Live Channel 2", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=high_channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.client.force_authenticate(self.owner)
        high_res = self.client.post(f"/api/v1/broadcasts/channels/{high_channel.id}/live-streams/", {"title": "y"}, format="json")
        self._go_live(high_res.data)
        ChannelLiveStream.objects.filter(id=high_res.data["id"]).update(viewer_count=50)

        res = self.client.get(self._url())

        ids = [row["id"] for row in res.data["results"]]
        self.assertEqual(ids[0], high_res.data["id"])


class LiveStreamStartEndLifecycleTests(LiveStreamTestBase):
    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    def test_start_publishes_and_auto_passes_never_reviewed_content(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.live.refresh_from_db()
        self.assertEqual(self.live.status, ChannelLiveStream.Status.LIVE)
        self.assertIsNotNone(self.live.started_at)
        content = self.live.content
        content.refresh_from_db()
        self.assertEqual(content.status, ChannelContent.Status.PUBLISHED)
        self.assertEqual(content.visibility, ChannelContent.Visibility.PUBLIC)
        self.assertEqual(content.moderation_status, ContentModerationStatus.PASSED)
        self.assertIsNotNone(content.moderation_expires_at)

    def test_viewer_can_actually_see_live_content_after_start(self):
        """The real regression this closure pass fixed: wiring the P0.2
        moderation gate into every public choke point silently hid every
        live stream's content from anyone but the owner, because nothing
        ever set moderation_status to PASSED for live content."""
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")
        self.live.refresh_from_db()

        self.client.force_authenticate(self.viewer)
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.live.content_id}/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

    def test_already_blocked_content_is_not_silently_repassed_on_start(self):
        content = self.live.content
        content.moderation_status = ContentModerationStatus.BLOCKED
        content.save(update_fields=["moderation_status"])
        self.client.force_authenticate(self.owner)

        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")

        content.refresh_from_db()
        self.assertEqual(content.moderation_status, ContentModerationStatus.BLOCKED)

    def test_editing_title_while_live_does_not_reset_moderation(self):
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")
        content_id = self.live.content_id

        res = self.client.patch(f"/api/v1/broadcasts/channel-contents/{content_id}/", {"title": "New title"}, format="json")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        content = ChannelContent.objects.get(id=content_id)
        self.assertEqual(content.title, "New title")
        self.assertEqual(content.moderation_status, ContentModerationStatus.PASSED)

    def test_non_manager_cannot_start_or_end_stream(self):
        self.client.force_authenticate(self.viewer)

        start_res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")
        end_res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/end/")

        self.assertEqual(start_res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(end_res.status_code, status.HTTP_403_FORBIDDEN)

    def test_end_sets_ended_status_and_replay_url(self):
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")

        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/end/",
            {"replay_url": "https://cdn.example.com/replay.m3u8"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.live.refresh_from_db()
        self.assertEqual(self.live.status, ChannelLiveStream.Status.ENDED)
        self.assertIsNotNone(self.live.ended_at)
        self.assertEqual(self.live.replay_url, "https://cdn.example.com/replay.m3u8")

    def test_end_releases_provider_ingest_slot_for_real_providers(self):
        # kisvideo-live's ingest proxy has a small, fixed port pool (one
        # real-time x264 re-encode per port, sized to this box's CPU
        # budget - see that repo's docker-compose.prod.yml) and only ever
        # frees a port via DELETE /streams/{id}. Before this test's fix,
        # nothing called that on a normal "End stream" tap, so every past
        # broadcast permanently held its port and the very next "Go Live"
        # anyone attempted 503'd with "No ingest capacity available" even
        # though nothing was actually broadcasting - confirmed live
        # against production, not a hypothetical.
        self.live.provider = "kisvideo"
        self.live.provider_stream_id = "live/testkey123"
        self.live.save(update_fields=["provider", "provider_stream_id"])
        self.client.force_authenticate(self.owner)

        with patch("apps.broadcasts.live_stream_providers.get_live_stream_provider") as mock_get_provider:
            mock_lsp = mock_get_provider.return_value
            res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/end/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        mock_get_provider.assert_called_once_with("kisvideo")
        mock_lsp.delete_live_stream.assert_called_once_with("live/testkey123")

    def test_end_does_not_touch_provider_when_disabled(self):
        # The disabled/dev-payload provider has no real ingest slot to
        # release - calling delete_live_stream for it would be a no-op at
        # best, so the view must not even try.
        self.client.force_authenticate(self.owner)

        with patch("apps.broadcasts.live_stream_providers.get_live_stream_provider") as mock_get_provider:
            res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/end/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        mock_get_provider.assert_not_called()

    def test_end_auto_archives_vod_when_recording_url_present_exactly_once(self):
        self.live.metadata = {**self.live.metadata, "recording_url": "https://cdn.example.com/recording.mp4"}
        self.live.save(update_fields=["metadata"])
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")

        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/end/")
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/end/")  # idempotent re-end

        archived = ChannelContent.objects.filter(metadata__archived_from_stream=str(self.live.id))
        self.assertEqual(archived.count(), 1)
        self.assertEqual(archived.first().status, ChannelContent.Status.DRAFT)

    def test_scheduling_rejects_clearly_past_datetime(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "Oops", "scheduled_start_at": "2020-01-01T00:00:00Z"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class LiveStreamSubscriberNotificationTests(LiveStreamTestBase):
    """Tapping Start is not proof an encoder/camera actually connected - for
    a provider-backed stream, subscribers must only be told "it's live" once
    the provider's own webhook confirms real ingest, not merely because the
    creator opened the Studio session. Disabled/dev-payload streams (today's
    only working configuration in production - see live_stream_providers.py)
    keep the old immediate-notify behavior, since no webhook will ever
    arrive for them."""

    def setUp(self):
        super().setUp()
        from apps.notifications.models import Notification

        from .models import BroadcastChannelSubscription

        self.Notification = Notification
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])
        BroadcastChannelSubscription.objects.create(channel=self.channel, user=self.viewer)

    def _notified(self):
        return self.Notification.objects.filter(
            user_id=self.viewer.id, type="CHANNEL_LIVE_STARTED", target_id=str(self.live.id),
        )

    def test_disabled_provider_notifies_immediately_on_start(self):
        self.client.force_authenticate(self.owner)

        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")

        self.assertTrue(self._notified().exists())

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_provider_backed_stream_does_not_notify_until_webhook_confirms_live(self):
        self.live.provider = "kisvideo"
        self.live.provider_stream_id = "provider-stream-abc"
        self.live.save(update_fields=["provider", "provider_stream_id"])
        self.client.force_authenticate(self.owner)

        start_res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")
        self.assertEqual(start_res.status_code, status.HTTP_200_OK, start_res.data)
        self.assertFalse(self._notified().exists(), "tapping Start must not notify subscribers on its own")

        webhook_res = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
            {"provider_stream_id": "provider-stream-abc", "status": "live"}, format="json",
            HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(webhook_res.status_code, status.HTTP_200_OK, webhook_res.data)
        self.assertTrue(self._notified().exists(), "a confirmed-live webhook must notify subscribers")

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_repeated_live_webhooks_do_not_duplicate_notifications(self):
        self.live.provider = "kisvideo"
        self.live.provider_stream_id = "provider-stream-abc"
        self.live.save(update_fields=["provider", "provider_stream_id"])

        for _ in range(2):
            self.client.post(
                "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
                {"provider_stream_id": "provider-stream-abc", "status": "live"}, format="json",
                HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
            )

        self.assertEqual(self._notified().count(), 1)


class LiveStreamMetadataRedactionTests(LiveStreamTestBase):
    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])
        self.live.metadata = {**self.live.metadata, "whip_url": "https://ingest.example.com/whip/secret-token"}
        self.live.save(update_fields=["metadata"])

    def test_non_manager_viewer_does_not_see_whip_url(self):
        self.client.force_authenticate(self.viewer)

        res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotIn("whip_url", res.data["metadata"])

    def test_anonymous_viewer_does_not_see_whip_url(self):
        self.client.force_authenticate(user=None)  # _schedule_stream() in setUp left us authenticated as owner

        res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotIn("whip_url", res.data["metadata"])

    def test_manager_does_see_whip_url(self):
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/")

        self.assertEqual(res.data["metadata"].get("whip_url"), "https://ingest.example.com/whip/secret-token")


class LiveStreamWhipIngestAuthTests(LiveStreamTestBase):
    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    def test_anonymous_cannot_post_to_whip_endpoint(self):
        self.client.force_authenticate(user=None)  # _schedule_stream() in setUp left us authenticated as owner

        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/whip/",
            data=b"v=0", content_type="application/sdp",
        )

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_manager_cannot_post_to_whip_endpoint(self):
        self.client.force_authenticate(self.viewer)

        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/whip/",
            data=b"v=0", content_type="application/sdp",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_without_whip_url_gets_real_rtmp_fallback_not_nulls(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/whip/",
            data=b"v=0", content_type="application/sdp",
        )

        self.assertEqual(res.status_code, 501)
        self.assertTrue(res.data["ingest_url"])
        self.assertTrue(res.data["stream_key"])


class LiveStreamArchiveWebhookAuthTests(LiveStreamTestBase):
    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_archive_webhook_rejects_missing_secret(self):
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
            {"recording_url": "https://cdn.example.com/r.mp4"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_archive_webhook_accepts_correct_secret(self):
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
            {"recording_url": "https://cdn.example.com/r.mp4"}, format="json",
            HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="")
    def test_archive_webhook_fails_closed_when_no_secret_is_configured(self):
        """An unset LIVE_STREAM_WEBHOOK_SECRET must reject every call, not
        skip verification - `if configured_secret and supplied != ...`
        used to short-circuit to False whenever the secret was empty,
        silently accepting any unauthenticated POST. See
        _live_stream_shared_secret_valid in views.py."""
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
            {"recording_url": "https://cdn.example.com/r.mp4"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.live.refresh_from_db()
        self.assertEqual((self.live.metadata or {}).get("recording_url"), None)


class LiveStreamRecordingStatusTests(LiveStreamTestBase):
    """ChannelLiveStream.recording_status - the lifecycle field the real
    recording pipeline will drive once it exists (see
    ChannelLiveStreamArchiveView's own honesty-note docstring: nothing
    calls this webhook in production today, so only NOT_STARTED (default)
    and the READY/FAILED transitions this view makes are reachable right
    now)."""

    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    def test_defaults_to_not_started(self):
        self.assertEqual(self.live.recording_status, ChannelLiveStream.RecordingStatus.NOT_STARTED)
        self.assertEqual(self.live_data["recording_status"], ChannelLiveStream.RecordingStatus.NOT_STARTED)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_successful_archive_sets_ready(self):
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
            {"recording_url": "https://cdn.example.com/r.mp4", "duration_seconds": 1800},
            format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["recording_status"], ChannelLiveStream.RecordingStatus.READY)
        self.live.refresh_from_db()
        self.assertEqual(self.live.recording_status, ChannelLiveStream.RecordingStatus.READY)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_failed_status_does_not_create_a_replay_content_row(self):
        # The hard requirement this guards: never show a replay as ready
        # when no playable asset actually exists.
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
            {"status": "failed", "error": "transcode timed out"},
            format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["recording_status"], ChannelLiveStream.RecordingStatus.FAILED)
        self.live.refresh_from_db()
        self.assertEqual(self.live.recording_status, ChannelLiveStream.RecordingStatus.FAILED)
        self.assertEqual(self.live.metadata.get("recording_error"), "transcode timed out")
        self.assertFalse(
            ChannelContent.objects.filter(channel=self.channel, metadata__archived_from_stream=str(self.live.id)).exists()
        )

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_duplicate_success_callbacks_create_exactly_one_replay(self):
        # The realistic failure mode this guards against: a flaky webhook
        # delivery firing twice for the same completed recording, not a
        # malicious replay - see ChannelLiveStreamWebhookView's own
        # dedup-guard comment for the same reasoning applied there.
        payload = {"recording_url": "https://cdn.example.com/r.mp4", "duration_seconds": 1800}
        for _ in range(2):
            res = self.client.post(
                f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
                payload, format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
            )
            self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        self.assertEqual(
            ChannelContent.objects.filter(channel=self.channel, metadata__archived_from_stream=str(self.live.id)).count(),
            1,
        )

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_successful_archive_creates_a_playable_asset_not_just_a_status(self):
        # THE actual bug this whole lifecycle exists to prevent: a
        # recording_status of "ready" with no ChannelContentAsset behind
        # it is a replay that LOOKS done but has nothing for any existing
        # player surface (ChannelContentDetailPage's MediaStage reads
        # content.assets/content.first_asset - nothing else) to actually
        # play. This is the test that would have caught the original gap.
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
            {"recording_url": "https://cdn.example.com/r.mp4", "duration_seconds": 1800, "thumbnail_url": "https://cdn.example.com/t.jpg"},
            format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        content_id = res.data["content_id"]
        content = ChannelContent.objects.get(id=content_id)
        self.assertEqual(content.status, ChannelContent.Status.DRAFT)
        asset = content.assets.first()
        self.assertIsNotNone(asset, "archive must create a real ChannelContentAsset, not just a status flag")
        self.assertEqual(asset.asset_type, "video")
        self.assertEqual(asset.url, "https://cdn.example.com/r.mp4")
        self.assertEqual(asset.duration_seconds, 1800)
        self.assertEqual(asset.processing_status, "ready")

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_replay_inherits_original_broadcast_visibility(self):
        self.live.content.visibility = ChannelContent.Visibility.MEMBERS_ONLY
        self.live.content.save(update_fields=["visibility"])

        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
            {"recording_url": "https://cdn.example.com/r.mp4"},
            format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        content = ChannelContent.objects.get(id=res.data["content_id"])
        self.assertEqual(content.visibility, ChannelContent.Visibility.MEMBERS_ONLY)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_stale_failure_after_ready_does_not_regress_a_working_replay(self):
        # Realistic ordering risk: two independent webhook calls
        # (success, then a late/duplicate failure retry) can arrive out
        # of order. A working replay must never flip back to failed.
        self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
            {"recording_url": "https://cdn.example.com/r.mp4"},
            format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/archive/",
            {"status": "failed", "error": "stale retry"},
            format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["recording_status"], ChannelLiveStream.RecordingStatus.READY)
        self.live.refresh_from_db()
        self.assertEqual(self.live.recording_status, ChannelLiveStream.RecordingStatus.READY)


class LiveStreamRecordingUploadUrlTests(LiveStreamTestBase):
    """ChannelLiveStreamRecordingUploadUrlView - issues the presigned S3
    PUT kisvideo-live-api's app/recording.py uploads a finalized
    recording to."""

    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_rejects_missing_secret(self):
        res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/recording-upload-url/")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_issues_an_upload_url_scoped_under_the_correct_namespace(self):
        # The sandbox test settings run against local FileSystemStorage
        # (no real S3 - see config/settings/creator_sandbox.py), which has
        # no generate_presigned_put/url(key) of its own - mocking
        # default_storage here tests the VIEW's own logic (key
        # construction/scoping, response shape) in isolation from the
        # real S3MediaStorage implementation, which apps/media/tests.py
        # already covers separately.
        with patch("apps.broadcasts.views.default_storage") as mock_storage:
            mock_storage.generate_presigned_put.return_value = "https://s3.example.com/presigned-put"
            mock_storage.url.return_value = "https://s3.example.com/object-url"

            res = self.client.post(
                f"/api/v1/broadcasts/live-streams/{self.live.id}/recording-upload-url/",
                HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
            )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["upload_url"], "https://s3.example.com/presigned-put")
        self.assertEqual(res.data["object_url"], "https://s3.example.com/object-url")
        # Server-generated key, scoped to this exact channel+stream - never
        # something a caller could redirect to an arbitrary location.
        self.assertIn(f"broadcast_videos/live_recordings/{self.channel.id}/{self.live.id}/", res.data["object_key"])
        mock_storage.generate_presigned_put.assert_called_once()
        called_key = mock_storage.generate_presigned_put.call_args[0][0]
        self.assertIn(f"broadcast_videos/live_recordings/{self.channel.id}/{self.live.id}/", called_key)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_invalid_stream_id_returns_404(self):
        res = self.client.post(
            "/api/v1/broadcasts/live-streams/00000000-0000-0000-0000-000000000000/recording-upload-url/",
            HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class ChannelLiveStreamWebhookAuthTests(LiveStreamTestBase):
    """ChannelLiveStreamWebhookView's generic shared-secret fallback - the
    only check KisVideoLiveProvider's webhooks ever get, since that
    provider implements no verify_webhook_signature of its own (see
    live_stream_providers.py) and so always falls into this branch."""

    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="")
    def test_fails_closed_when_no_secret_is_configured(self):
        res = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
            {"provider_stream_id": "abc", "status": "live"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_rejects_missing_secret_when_one_is_configured(self):
        res = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
            {"provider_stream_id": "abc", "status": "live"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_accepts_correct_secret(self):
        res = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
            {"provider_stream_id": "abc", "status": "live"}, format="json",
            HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertNotEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class ChannelLiveStreamWebhookRecordingStatusTests(LiveStreamTestBase):
    """ChannelLiveStreamWebhookView's optional recording_status piggyback -
    kisvideo-live-api's hook_ready/hook_not_ready send this alongside the
    ordinary live/ended status (see that service's app/main.py)."""

    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])
        self.live.provider = "kisvideo"
        self.live.provider_stream_id = "rec-status-test-stream"
        self.live.save(update_fields=["provider", "provider_stream_id"])

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_live_event_can_set_recording_status_to_recording(self):
        res = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
            {"provider_stream_id": "rec-status-test-stream", "status": "live", "recording_status": "recording"},
            format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.live.refresh_from_db()
        self.assertEqual(self.live.recording_status, ChannelLiveStream.RecordingStatus.RECORDING)

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_cannot_regress_a_terminal_ready_status(self):
        self.live.recording_status = ChannelLiveStream.RecordingStatus.READY
        self.live.save(update_fields=["recording_status"])

        res = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
            {"provider_stream_id": "rec-status-test-stream", "status": "ended", "recording_status": "finalizing"},
            format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.live.refresh_from_db()
        self.assertEqual(
            self.live.recording_status, ChannelLiveStream.RecordingStatus.READY,
            "a terminal recording_status must never be regressed by a later live/ended status webhook",
        )

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_invalid_recording_status_value_is_ignored(self):
        res = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
            {"provider_stream_id": "rec-status-test-stream", "status": "live", "recording_status": "not_a_real_value"},
            format="json", HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.live.refresh_from_db()
        self.assertEqual(self.live.recording_status, ChannelLiveStream.RecordingStatus.NOT_STARTED)


class ChannelLiveStreamWebhookDedupTests(LiveStreamTestBase):
    """A duplicate webhook delivery for the same (provider, stream, status)
    within the dedup window must be absorbed, not reprocessed - guards
    against kisvideo-live's best-effort hook firing twice for one real
    publish/unpublish event."""

    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])
        self.live.provider = "kisvideo"
        self.live.provider_stream_id = "dedup-test-stream"
        self.live.save(update_fields=["provider", "provider_stream_id"])

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_duplicate_delivery_is_deduped(self):
        payload = {"provider_stream_id": "dedup-test-stream", "status": "live"}
        first = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/", payload, format="json",
            HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertNotIn("deduped", first.data)

        second = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/", payload, format="json",
            HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )
        self.assertEqual(second.status_code, 200, second.data)
        self.assertTrue(second.data.get("deduped"))

    @override_settings(LIVE_STREAM_WEBHOOK_SECRET="shh-its-a-secret")
    def test_different_status_for_same_stream_is_not_deduped(self):
        self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
            {"provider_stream_id": "dedup-test-stream", "status": "live"}, format="json",
            HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )
        ended = self.client.post(
            "/api/v1/broadcasts/live-streams/webhook/kisvideo/",
            {"provider_stream_id": "dedup-test-stream", "status": "ended"}, format="json",
            HTTP_X_LIVE_WEBHOOK_SECRET="shh-its-a-secret",
        )
        self.assertEqual(ended.status_code, 200, ended.data)
        self.assertNotIn("deduped", ended.data)
        self.live.refresh_from_db()
        self.assertEqual(self.live.status, ChannelLiveStream.Status.ENDED)


class ChannelLiveStreamStreamKeyRotateTests(LiveStreamTestBase):
    def _url(self, stream_id):
        return f"/api/v1/broadcasts/live-streams/{stream_id}/stream-key/rotate/"

    def test_requires_authentication(self):
        stream = self._schedule_stream()
        self.client.force_authenticate(user=None)
        res = self.client.post(self._url(stream["id"]))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_manager_cannot_rotate(self):
        stream = self._schedule_stream()
        self.client.force_authenticate(self.other)
        res = self.client.post(self._url(stream["id"]))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_rotation_changes_the_derived_key_in_disabled_mode(self):
        stream = self._schedule_stream()
        live = ChannelLiveStream.objects.get(id=stream["id"])
        old_provider_stream_id = live.provider_stream_id
        self.client.force_authenticate(self.owner)

        res = self.client.post(self._url(stream["id"]))

        self.assertEqual(res.status_code, 200, res.data)
        live.refresh_from_db()
        self.assertNotEqual(live.provider_stream_id, old_provider_stream_id)
        self.assertNotEqual(res.data["stream_key"], old_provider_stream_id)
        self.assertEqual(res.data["stream_key"], live.provider_stream_id)

    def test_cannot_rotate_while_live(self):
        stream = self._schedule_stream()
        live = ChannelLiveStream.objects.get(id=stream["id"])
        live.status = ChannelLiveStream.Status.LIVE
        live.save(update_fields=["status"])
        self.client.force_authenticate(self.owner)

        res = self.client.post(self._url(stream["id"]))

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_old_stream_key_hash_no_longer_matches_after_rotation(self):
        stream = self._schedule_stream()
        live = ChannelLiveStream.objects.get(id=stream["id"])
        old_hash = live.stream_key_hash
        self.client.force_authenticate(self.owner)

        self.client.post(self._url(stream["id"]))

        live.refresh_from_db()
        self.assertNotEqual(live.stream_key_hash, old_hash)


class LiveChatMessageIdentityTests(LiveStreamTestBase):
    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    def test_authenticated_poster_display_name_comes_from_account_not_body(self):
        self.client.force_authenticate(self.viewer)

        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/",
            {"message": "hello", "display_name": "Totally Not An Impersonator"},
            format="json",
        )

        self.assertEqual(res.status_code, 201, res.data)
        msg = LiveChatMessage.objects.get(id=res.data["id"])
        self.assertEqual(msg.user_id, self.viewer.id)
        self.assertNotEqual(msg.display_name, "Totally Not An Impersonator")

    def test_anonymous_poster_may_set_a_guest_display_name(self):
        self.client.force_authenticate(user=None)  # _schedule_stream() in setUp left us authenticated as owner

        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/",
            {"message": "hi", "display_name": "Guest123"},
            format="json",
        )

        self.assertEqual(res.status_code, 201, res.data)
        msg = LiveChatMessage.objects.get(id=res.data["id"])
        self.assertIsNone(msg.user_id)
        self.assertEqual(msg.display_name, "Guest123")

    def test_send_and_history_round_trip_the_same_field_names(self):
        """Regression test for a mobile/backend field-name mismatch: the
        app was POSTing {"text": ...} and reading back .text/.user_display
        from both the send response and the chat-history list, while this
        view only ever accepted/returned "message"/"display_name"/
        "created_at" - every real-world send silently 400'd, and any
        history that did load rendered blank text under "Viewer" for
        every message. This locks the actual wire contract so a future
        change to these key names fails a test instead of only failing
        silently on-device."""
        self.client.force_authenticate(self.viewer)

        send_res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/",
            {"message": "hello from the viewer"},
            format="json",
        )
        self.assertEqual(send_res.status_code, 201, send_res.data)
        for key in ("id", "display_name", "message", "created_at"):
            self.assertIn(key, send_res.data)
        self.assertEqual(send_res.data["message"], "hello from the viewer")

        history_res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/")
        self.assertEqual(history_res.status_code, 200, history_res.data)
        rows = history_res.data["results"]
        self.assertEqual(len(rows), 1)
        for key in ("id", "display_name", "avatar_url", "message", "created_at", "user_id"):
            self.assertIn(key, rows[0])
        self.assertEqual(rows[0]["message"], "hello from the viewer")


class LiveStreamEditCancelTests(LiveStreamTestBase):
    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    def _patch(self, data):
        self.client.force_authenticate(self.owner)
        return self.client.patch(f"/api/v1/broadcasts/live-streams/{self.live.id}/", data, format="json")

    def _cancel(self):
        return self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/cancel/")

    def test_owner_can_edit_title_and_schedule(self):
        new_time = (timezone.now() + timedelta(hours=2)).isoformat()
        res = self._patch({"title": "New Title", "scheduled_start_at": new_time})

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.live.refresh_from_db()
        self.assertEqual(self.live.title, "New Title")
        self.live.content.refresh_from_db()
        self.assertEqual(self.live.content.title, "New Title")

    def test_non_manager_cannot_edit(self):
        self.client.force_authenticate(self.other)

        res = self.client.patch(f"/api/v1/broadcasts/live-streams/{self.live.id}/", {"title": "Hijacked"}, format="json")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_edit_to_a_past_time(self):
        past = (timezone.now() - timedelta(hours=1)).isoformat()

        res = self._patch({"scheduled_start_at": past})

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_edit_title_to_blank(self):
        res = self._patch({"title": "   "})

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_edit_once_live(self):
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")

        res = self._patch({"title": "Too Late"})

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_cancel_scheduled_stream(self):
        self.client.force_authenticate(self.owner)

        res = self._cancel()

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.live.refresh_from_db()
        self.assertEqual(self.live.status, ChannelLiveStream.Status.CANCELLED)

    def test_non_manager_cannot_cancel(self):
        self.client.force_authenticate(self.other)

        res = self._cancel()

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_cancel_once_live(self):
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")

        res = self._cancel()

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancelled_stream_excluded_from_non_manager_list(self):
        self.client.force_authenticate(self.owner)
        self._cancel()

        self.client.force_authenticate(self.other)
        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        ids = [row["id"] for row in res.data["results"]]
        self.assertNotIn(str(self.live.id), ids)


class LiveStreamReminderTaskTests(LiveStreamTestBase):
    def test_sends_reminder_within_window_and_dedupes(self):
        from .tasks import send_live_stream_reminders_task

        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "Soon", "scheduled_start_at": (timezone.now() + timedelta(minutes=10)).isoformat()},
            format="json",
        )
        live = ChannelLiveStream.objects.get(id=res.data["id"])

        result = send_live_stream_reminders_task()
        self.assertEqual(result["reminded"], 1)
        live.refresh_from_db()
        self.assertTrue(live.metadata.get("reminder_sent"))

        # Second tick must not re-notify the same stream.
        result2 = send_live_stream_reminders_task()
        self.assertEqual(result2["reminded"], 0)

    def test_ignores_streams_outside_the_window(self):
        from .tasks import send_live_stream_reminders_task

        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "Later", "scheduled_start_at": (timezone.now() + timedelta(hours=3)).isoformat()},
            format="json",
        )

        result = send_live_stream_reminders_task()

        self.assertEqual(result["reminded"], 0)


class LiveStreamFailStaleTaskTests(LiveStreamTestBase):
    def _live_stream_with_provider(self, provider="kisvideo"):
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/live-streams/",
            {"title": "Stuck"},
            format="json",
        )
        live = ChannelLiveStream.objects.get(id=res.data["id"])
        live.provider = provider
        live.provider_stream_id = "provider-stream-stuck"
        live.save(update_fields=["provider", "provider_stream_id"])
        return live

    def test_fails_a_provider_backed_stream_with_no_confirmed_ingest_past_timeout(self):
        from .tasks import fail_stale_live_streams_task

        live = self._live_stream_with_provider()
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{live.id}/start/")
        live.refresh_from_db()
        live.started_at = timezone.now() - timedelta(minutes=20)
        live.save(update_fields=["started_at"])

        result = fail_stale_live_streams_task(timeout_minutes=10)

        self.assertEqual(result["failed"], 1)
        live.refresh_from_db()
        self.assertEqual(live.status, ChannelLiveStream.Status.FAILED)
        self.assertIsNotNone(live.ended_at)

    def test_does_not_fail_a_stream_whose_webhook_already_confirmed_live(self):
        from .tasks import fail_stale_live_streams_task

        live = self._live_stream_with_provider()
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{live.id}/start/")
        live.refresh_from_db()
        live.started_at = timezone.now() - timedelta(minutes=20)
        live.metadata = {**(live.metadata or {}), "subscribers_notified": True}
        live.save(update_fields=["started_at", "metadata"])

        result = fail_stale_live_streams_task(timeout_minutes=10)

        self.assertEqual(result["failed"], 0)
        live.refresh_from_db()
        self.assertEqual(live.status, ChannelLiveStream.Status.LIVE)

    def test_does_not_fail_a_disabled_provider_stream(self):
        from .tasks import fail_stale_live_streams_task

        live = self._live_stream_with_provider(provider="disabled")
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{live.id}/start/")
        live.refresh_from_db()
        live.started_at = timezone.now() - timedelta(minutes=20)
        live.save(update_fields=["started_at"])

        result = fail_stale_live_streams_task(timeout_minutes=10)

        self.assertEqual(result["failed"], 0)
        live.refresh_from_db()
        self.assertEqual(live.status, ChannelLiveStream.Status.LIVE)

    def test_does_not_fail_a_stream_still_within_the_timeout(self):
        from .tasks import fail_stale_live_streams_task

        live = self._live_stream_with_provider()
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{live.id}/start/")

        result = fail_stale_live_streams_task(timeout_minutes=10)

        self.assertEqual(result["failed"], 0)
        live.refresh_from_db()
        self.assertEqual(live.status, ChannelLiveStream.Status.LIVE)


class LiveChatRateLimitTests(LiveStreamTestBase):
    """ChannelLiveStreamChatHistoryView.post is the one real spam vector in
    live chat (a single shared, high-visibility room) - these pin that only
    POST goes through ScopedRateThrottle (not GET, which viewers may
    legitimately re-fetch far more often than a send limit should
    tolerate), rather than re-testing DRF's own throttle-rate math."""

    def test_post_is_throttled_when_the_scope_denies(self):
        stream = self._schedule_stream()
        self.client.force_authenticate(self.viewer)
        # allow_request=False alone leaves DRF's check_throttles() calling
        # throttle.wait(), which reads self.history - only ever populated by
        # the real allow_request() implementation we're bypassing here, so
        # wait() needs stubbing too or it AttributeErrors on a mock object.
        with patch("rest_framework.throttling.ScopedRateThrottle.allow_request", return_value=False), \
             patch("rest_framework.throttling.ScopedRateThrottle.wait", return_value=60):
            res = self.client.post(
                f"/api/v1/broadcasts/live-streams/{stream['id']}/chat/", {"message": "spam"}, format="json",
            )
        self.assertEqual(res.status_code, 429)

    def test_get_is_not_throttled_by_the_send_scope(self):
        stream = self._schedule_stream()
        self.client.force_authenticate(self.viewer)
        with patch("rest_framework.throttling.ScopedRateThrottle.allow_request", return_value=False):
            res = self.client.get(f"/api/v1/broadcasts/live-streams/{stream['id']}/chat/")
        self.assertEqual(res.status_code, 200)


class LiveChatHistoryVisibilityTests(LiveStreamTestBase):
    """ChannelLiveStreamChatHistoryView (GET/POST) is a REST path separate
    from channel.live.join's socket join - it was found to have NO
    visibility check at all (permission_classes=[AllowAny], no gate),
    letting anyone read or post into a private/members-only channel's live
    chat directly even after the socket-join path was fixed. These tests
    guard the fix: same "public, or a manager of a private channel" rule as
    ChannelLiveStreamDetailView.get, applied to both verbs."""

    def _url(self, stream_id):
        return f"/api/v1/broadcasts/live-streams/{stream_id}/chat/"

    def test_public_channel_anonymous_can_read_and_post(self):
        # self.channel (base setUp) is_public=True - anonymous guest chat on
        # public streams must keep working unchanged.
        stream = self._schedule_stream()
        self.client.force_authenticate(user=None)

        post_res = self.client.post(self._url(stream["id"]), {"message": "hi", "display_name": "Guest"}, format="json")
        self.assertEqual(post_res.status_code, 201, post_res.data)

        get_res = self.client.get(self._url(stream["id"]))
        self.assertEqual(get_res.status_code, 200, get_res.data)
        self.assertEqual(len(get_res.data["results"]), 1)

    def test_private_channel_non_manager_cannot_read(self):
        self.channel.is_public = False
        self.channel.save(update_fields=["is_public"])
        stream = self._schedule_stream()
        self.client.force_authenticate(self.other)

        res = self.client.get(self._url(stream["id"]))
        self.assertEqual(res.status_code, 404)

    def test_private_channel_non_manager_cannot_post(self):
        self.channel.is_public = False
        self.channel.save(update_fields=["is_public"])
        stream = self._schedule_stream()
        self.client.force_authenticate(self.other)

        res = self.client.post(self._url(stream["id"]), {"message": "sneaking in"}, format="json")
        self.assertEqual(res.status_code, 404)
        self.assertFalse(LiveChatMessage.objects.filter(stream_id=stream["id"]).exists())

    def test_private_channel_anonymous_cannot_read_or_post(self):
        self.channel.is_public = False
        self.channel.save(update_fields=["is_public"])
        stream = self._schedule_stream()
        self.client.force_authenticate(user=None)

        self.assertEqual(self.client.get(self._url(stream["id"])).status_code, 404)
        res = self.client.post(self._url(stream["id"]), {"message": "hi", "display_name": "Guest"}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_private_channel_owner_can_read_and_post(self):
        self.channel.is_public = False
        self.channel.save(update_fields=["is_public"])
        stream = self._schedule_stream()
        self.client.force_authenticate(self.owner)

        post_res = self.client.post(self._url(stream["id"]), {"message": "owner here"}, format="json")
        self.assertEqual(post_res.status_code, 201, post_res.data)
        get_res = self.client.get(self._url(stream["id"]))
        self.assertEqual(get_res.status_code, 200, get_res.data)
        self.assertEqual(len(get_res.data["results"]), 1)


class LiveChatModerationTests(LiveStreamTestBase):
    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    def _post_message(self, user, text="hello"):
        self.client.force_authenticate(user)
        res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/", {"message": text}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        return LiveChatMessage.objects.get(id=res.data["id"])

    def _delete_url(self, message_id):
        return f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/{message_id}/"

    def test_owner_can_delete_any_message(self):
        msg = self._post_message(self.viewer)
        self.client.force_authenticate(self.owner)

        res = self.client.delete(self._delete_url(msg.id))

        self.assertEqual(res.status_code, 200, res.data)
        msg.refresh_from_db()
        self.assertTrue(msg.is_deleted)
        self.assertTrue(ChannelModerationRecord.objects.filter(
            target_type=ChannelModerationRecord.TargetType.LIVE_CHAT_MESSAGE, target_id=msg.id,
        ).exists())

    def test_author_can_delete_own_message(self):
        msg = self._post_message(self.viewer)
        self.client.force_authenticate(self.viewer)

        res = self.client.delete(self._delete_url(msg.id))

        self.assertEqual(res.status_code, 200, res.data)

    def test_non_manager_non_author_cannot_delete(self):
        msg = self._post_message(self.viewer)
        self.client.force_authenticate(self.other)

        res = self.client.delete(self._delete_url(msg.id))

        self.assertEqual(res.status_code, 403)
        msg.refresh_from_db()
        self.assertFalse(msg.is_deleted)

    def test_deleted_message_excluded_from_history(self):
        msg = self._post_message(self.viewer)
        self.client.force_authenticate(self.owner)
        self.client.delete(self._delete_url(msg.id))

        res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/")

        self.assertEqual(len(res.data["results"]), 0)

    def test_report_message_creates_moderation_record_and_flag(self):
        from apps.moderation.models import Flag as ModerationFlag
        msg = self._post_message(self.viewer)
        self.client.force_authenticate(self.other)

        res = self.client.post(self._delete_url(msg.id), {"reason": "spam"}, format="json")

        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(ChannelModerationRecord.objects.filter(
            target_type=ChannelModerationRecord.TargetType.LIVE_CHAT_MESSAGE, target_id=msg.id, reporter=self.other,
        ).exists())
        self.assertTrue(ModerationFlag.objects.filter(target_id=msg.id, reporter_id=self.other.id).exists())


class ChannelChatBanTests(LiveStreamTestBase):
    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    def _bans_url(self):
        return f"/api/v1/broadcasts/channels/{self.channel.id}/chat-bans/"

    def test_owner_can_ban_user_from_chat(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(self._bans_url(), {"user_id": str(self.viewer.id)}, format="json")

        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(ChannelChatBan.objects.filter(channel=self.channel, user=self.viewer).exists())

    def test_non_manager_cannot_ban(self):
        self.client.force_authenticate(self.other)

        res = self.client.post(self._bans_url(), {"user_id": str(self.viewer.id)}, format="json")

        self.assertEqual(res.status_code, 403)

    def test_banned_user_cannot_post_in_chat(self):
        ChannelChatBan.objects.create(channel=self.channel, user=self.viewer, created_by=self.owner)
        self.client.force_authenticate(self.viewer)

        res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/", {"message": "let me in"}, format="json")

        self.assertEqual(res.status_code, 403)
        self.assertFalse(LiveChatMessage.objects.filter(stream=self.live, user=self.viewer).exists())

    def test_expired_ban_does_not_block_posting(self):
        ChannelChatBan.objects.create(
            channel=self.channel, user=self.viewer, created_by=self.owner,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        self.client.force_authenticate(self.viewer)

        res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/", {"message": "hi again"}, format="json")

        self.assertEqual(res.status_code, 201, res.data)

    def test_owner_can_unban(self):
        ChannelChatBan.objects.create(channel=self.channel, user=self.viewer, created_by=self.owner)
        self.client.force_authenticate(self.owner)

        res = self.client.delete(f"{self._bans_url()}?user_id={self.viewer.id}")

        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(ChannelChatBan.objects.filter(channel=self.channel, user=self.viewer).exists())


class LiveChatBlockingTests(LiveStreamTestBase):
    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])

    def test_viewer_does_not_see_messages_from_someone_they_blocked(self):
        self.client.force_authenticate(self.other)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/", {"message": "from other"}, format="json")
        UserBlock.objects.create(blocker=self.viewer, blocked=self.other)
        self.client.force_authenticate(self.viewer)

        res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/")

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(res.data["results"]), 0)

    def test_unrelated_viewer_still_sees_messages(self):
        self.client.force_authenticate(self.other)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/", {"message": "from other"}, format="json")
        self.client.force_authenticate(self.viewer)

        res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/")

        self.assertEqual(len(res.data["results"]), 1)

    def test_channel_owner_blocked_poster_cannot_post(self):
        UserBlock.objects.create(blocker=self.owner, blocked=self.viewer)
        self.client.force_authenticate(self.viewer)

        res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/", {"message": "sneaking in"}, format="json")

        self.assertEqual(res.status_code, 403)


class LiveStreamCanViewInternalEndpointTests(LiveStreamTestBase):
    """ChannelLiveStreamCanViewView backs Nest's channel.live.join socket
    handler (DjangoConversationClient.canViewLiveStream) the same way
    apps.statuses's can-view action backs status.join: without this check,
    a user excluded from a private channel could still join its live
    viewer-count/chat-broadcast socket room even though the REST endpoint
    404s them. These tests guard that this internal endpoint enforces the
    exact same visibility rule as ChannelLiveStreamDetailView.get."""

    def _url(self, stream_id):
        return f"/api/v1/broadcasts/live-streams/{stream_id}/can-view/"

    def test_requires_internal_auth(self):
        # Same authentication_classes=[]/permission_classes=[] config as
        # apps.statuses's can_view action - DRF has no authenticator to hang
        # a WWW-Authenticate challenge off of, so AuthenticationFailed here
        # surfaces as 403, not 401 (matching that sibling endpoint exactly).
        stream = self._schedule_stream()
        res = self.client.get(self._url(stream["id"]), {"user_id": str(self.viewer.id)})
        self.assertEqual(res.status_code, 403)

    def test_rejects_wrong_internal_token(self):
        stream = self._schedule_stream()
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(
                self._url(stream["id"]), {"user_id": str(self.viewer.id)}, HTTP_X_INTERNAL_AUTH="wrong-token",
            )
        self.assertEqual(res.status_code, 403)

    def test_public_channel_anyone_can_view(self):
        # self.channel (base setUp) is_public=True
        stream = self._schedule_stream()
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(
                self._url(stream["id"]), {"user_id": str(self.other.id)}, HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["can_view"])

    def test_private_channel_non_manager_cannot_view(self):
        self.channel.is_public = False
        self.channel.save(update_fields=["is_public"])
        stream = self._schedule_stream()
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(
                self._url(stream["id"]), {"user_id": str(self.other.id)}, HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data["can_view"])

    def test_private_channel_owner_can_view(self):
        self.channel.is_public = False
        self.channel.save(update_fields=["is_public"])
        stream = self._schedule_stream()
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(
                self._url(stream["id"]), {"user_id": str(self.owner.id)}, HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["can_view"])

    def test_unknown_stream_returns_404_not_leaking_existence(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(
                self._url("00000000-0000-0000-0000-000000000000"),
                {"user_id": str(self.viewer.id)},
                HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 404)

    def test_missing_user_id_returns_400(self):
        stream = self._schedule_stream()
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.get(self._url(stream["id"]), {}, HTTP_X_INTERNAL_AUTH="real-token")
        self.assertEqual(res.status_code, 400)


class LiveStreamAnalyticsTests(LiveStreamTestBase):
    """E1 (unified viewer-count/peak tracking across all three writers),
    E2 (honest view/watch-seconds counting), E3 (health reporting),
    E4 (playback-event reporting)."""

    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])
        self.client.force_authenticate(self.owner)
        res = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")
        assert res.status_code == status.HTTP_200_OK, res.data
        self.live.refresh_from_db()

    def test_viewer_ping_counts_a_view_once_per_distinct_viewer(self):
        self.client.force_authenticate(self.viewer)

        res1 = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/viewer-ping/")
        self.assertEqual(res1.status_code, 200, res1.data)
        res2 = self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/viewer-ping/")
        self.assertEqual(res2.status_code, 200, res2.data)

        self.live.refresh_from_db()
        # The second ping is the SAME viewer within the dedup window, not a
        # new view - total_view_count must not double-count it.
        self.assertEqual(self.live.total_view_count, 1)
        self.assertEqual(self.live.peak_viewer_count, 1)

    def test_viewer_ping_accumulates_watch_seconds_between_a_viewers_own_pings(self):
        self.client.force_authenticate(self.viewer)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/viewer-ping/")

        # Simulate ~10 real seconds having passed before the next ping by
        # backdating the cached last-seen timestamp, rather than sleeping.
        last_seen_key = f"live_viewer_last_seen:{self.live.id}:{self.viewer.id}"
        cache.set(last_seen_key, timezone.now().timestamp() - 10, timeout=120)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/viewer-ping/")

        self.live.refresh_from_db()
        self.assertGreaterEqual(self.live.total_watch_seconds, 9)

    def test_viewer_count_report_requires_internal_auth(self):
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/viewer-count-report/", {"count": 7}, format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_viewer_count_report_updates_current_and_peak(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.post(
                f"/api/v1/broadcasts/live-streams/{self.live.id}/viewer-count-report/",
                {"count": 7}, format="json", HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 200, res.data)

        self.live.refresh_from_db()
        self.assertEqual(self.live.viewer_count, 7)
        self.assertEqual(self.live.peak_viewer_count, 7)

    def test_viewer_count_report_rejects_negative_count(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.post(
                f"/api/v1/broadcasts/live-streams/{self.live.id}/viewer-count-report/",
                {"count": -1}, format="json", HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 400)

    def test_health_report_requires_edit_permission_and_records_sample(self):
        self.client.force_authenticate(self.viewer)
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/health/", {"status": "degraded"}, format="json",
        )
        self.assertEqual(res.status_code, 403)

        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/health/",
            {"status": "degraded", "detail": "bitrate drop"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)

        detail_res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/")
        self.assertEqual(detail_res.data["health_status"], "degraded")
        self.assertTrue(detail_res.data["health_updated_at"])

    def test_health_report_rejects_unknown_status(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/health/", {"status": "not_a_real_status"}, format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_playback_event_increments_counter_anonymously(self):
        self.client.force_authenticate(user=None)
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/playback-events/",
            {"event": "startup_failure", "reason": "manifest 404"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)

        self.live.refresh_from_db()
        self.assertEqual(self.live.playback_error_count, 1)

    def test_playback_event_rejects_unknown_event_type(self):
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/playback-events/", {"event": "bogus"}, format="json",
        )
        self.assertEqual(res.status_code, 400)


class ChannelAnalyticsLiveEngagementRollupTests(LiveStreamTestBase):
    """E5: live-stream chat/tip/peak-viewer engagement was never rolled into
    BroadcastChannelAnalyticsView's daily rollup - live_peak_viewers existed
    as a model field + serializer entry with nothing ever writing it, and
    chat/tip counts had no home at all. Live reactions (Batch 6) are
    deliberately excluded - they're ephemeral socket bursts with no
    persisted rows to count."""

    def setUp(self):
        super().setUp()
        self.live_data = self._schedule_stream()
        self.live = ChannelLiveStream.objects.get(id=self.live_data["id"])
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/start/")
        self.live.refresh_from_db()

    def test_rollup_includes_live_peak_viewers_and_chat_and_tip_counts(self):
        self.live.peak_viewer_count = 42
        self.live.save(update_fields=["peak_viewer_count"])
        LiveChatMessage.objects.create(stream=self.live, user=self.viewer, display_name="V", message="hi")
        LiveChatMessage.objects.create(stream=self.live, user=self.viewer, display_name="V", message="hi again")
        from .models import ChannelLiveStreamTip
        ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.viewer, amount_cents=500,
            status=ChannelLiveStreamTip.Status.COMPLETED,
        )
        ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.viewer, amount_cents=300,
            status=ChannelLiveStreamTip.Status.PENDING,
        )

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/analytics/")

        self.assertEqual(res.status_code, 200, res.data)
        rollup = res.data["rollups"][0]
        self.assertEqual(rollup["live_peak_viewers"], 42)
        self.assertEqual(rollup["metadata"]["live_chat_messages"], 2)
        self.assertEqual(rollup["metadata"]["live_tips_count"], 1)
        self.assertEqual(rollup["metadata"]["live_tips_amount_cents"], 500)

    def test_rollup_excludes_deleted_chat_and_non_completed_tips(self):
        LiveChatMessage.objects.create(stream=self.live, user=self.viewer, display_name="V", message="visible")
        LiveChatMessage.objects.create(stream=self.live, user=self.viewer, display_name="V", message="gone", is_deleted=True)
        from .models import ChannelLiveStreamTip
        ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.viewer, amount_cents=999,
            status=ChannelLiveStreamTip.Status.FAILED,
        )

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/analytics/")

        self.assertEqual(res.status_code, 200, res.data)
        rollup = res.data["rollups"][0]
        self.assertEqual(rollup["metadata"]["live_chat_messages"], 1)
        self.assertEqual(rollup["metadata"]["live_tips_count"], 0)
        self.assertEqual(rollup["metadata"]["live_tips_amount_cents"], 0)


class LiveStreamContentListNavigationTests(LiveStreamTestBase):
    """Regression coverage for the channel-content-list navigation bug:
    opening a live stream from the main Channels list worked (it already
    had the real ChannelLiveStream id), but opening the SAME stream from
    inside its own channel's content list showed it as upcoming forever,
    because the content list's response had no correct id for the
    frontend to navigate with at all - ChannelContent.id and
    ChannelLiveStream.id are different rows/ids, related only via
    ChannelLiveStream.content (a reverse OneToOneField, related_name
    "live_stream" on ChannelContent). See ChannelContentListSerializer's
    live_stream_id/live_stream_status fields."""

    def test_channel_content_list_exposes_the_real_live_stream_id(self):
        data = self._schedule_stream()
        live = ChannelLiveStream.objects.get(id=data["id"])
        # Scheduled content is owner/manager-only visible (not yet public -
        # see test_scheduling_creates_scheduled_content_not_yet_public), so
        # fetch as the owner, matching how a creator previewing their own
        # channel would see it.
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/contents/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        rows = res.data["results"]
        row = next((r for r in rows if r["id"] == str(live.content_id)), None)
        self.assertIsNotNone(row, "scheduled live-stream content row not found in content list")
        # The actual regression: content["id"] (ChannelContent.id) must
        # NOT be confused with the id a client needs to open the stream.
        self.assertNotEqual(row["id"], str(live.id))
        self.assertEqual(row["live_stream_id"], str(live.id))
        self.assertEqual(row["live_stream_status"], ChannelLiveStream.Status.SCHEDULED)
        self.assertEqual(row["content_type"], "live_stream")

    def test_live_stream_id_reflects_status_transitions_from_the_same_list(self):
        data = self._schedule_stream()
        live = ChannelLiveStream.objects.get(id=data["id"])
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/broadcasts/live-streams/{live.id}/start/")

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/contents/")

        row = next(r for r in res.data["results"] if r["id"] == str(live.content_id))
        self.assertEqual(row["live_stream_id"], str(live.id))
        self.assertEqual(row["live_stream_status"], ChannelLiveStream.Status.LIVE)

    def test_ordinary_content_has_no_live_stream_id(self):
        content = ChannelContent.objects.create(
            channel=self.channel,
            content_type="video",
            title="A normal video",
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            moderation_status=ContentModerationStatus.PASSED,
        )
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/contents/")

        row = next(r for r in res.data["results"] if r["id"] == str(content.id))
        self.assertEqual(row["live_stream_id"], "")
        self.assertEqual(row["live_stream_status"], "")
