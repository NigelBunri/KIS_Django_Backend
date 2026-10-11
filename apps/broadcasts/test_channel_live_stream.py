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
  - ChannelLiveStreamArchiveView must require the shared webhook secret
    when one is configured (previously accepted unauthenticated calls
    despite its own comment claiming otherwise).
  - Chat message display_name must come from the account, not the
    request body, for authenticated posters.

Run:
  python3 manage.py test apps.broadcasts.test_channel_live_stream --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription
from apps.accounts.tiers import ensure_default_account_tiers

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelContent,
    ChannelLiveStream,
    ContentModerationStatus,
    LiveChatMessage,
)

User = get_user_model()


class LiveStreamTestBase(TestCase):
    def setUp(self):
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
