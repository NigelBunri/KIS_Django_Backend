"""
P1.4 discovery/search pass surfaced a systemic gap: a whole family of
"player feature" GET endpoints (subtitles, end-screen, cards, clips,
audio tracks, geo-restriction, premiere, product tags, related-content,
and the embed/oEmbed public views) were AllowAny "so anonymous viewers
can see this too" - a deliberate, correct intent - but never actually
checked whether the underlying content was visible to that viewer at
all. Any of these would happily return subtitle text, product links,
end-screen CTAs, etc. for a PRIVATE, DRAFT, or BLOCKED piece of content
to anyone who knew/guessed its UUID, not just the "real" public ones the
comments on each class claimed to be serving. Fixed by adding the same
_user_can_view_content() check ChannelContentDetailView.get already
uses - still AllowAny, so a real public+approved item works for
anonymous viewers exactly as before (this file also re-confirms that),
but a private/unapproved one now correctly 404s for everyone but a
channel manager.

Run:
  python3 manage.py test apps.broadcasts.test_player_feature_visibility --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelContent,
    ChannelContentCard,
    ChannelContentSubtitle,
    ChannelContentType,
    ContentModerationStatus,
)

User = get_user_model()


class PlayerFeatureVisibilityTestBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349190000001", password="pw123456", country="NG")
        self.outsider = User.objects.create_user(phone="+2349190000002", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="player-feature-channel", display_name="Player Feature Channel", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.public_content = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Public video",
            status=ChannelContent.Status.PUBLISHED, visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(), created_by=self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + datetime.timedelta(days=90),
        )
        self.private_content = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Private video",
            status=ChannelContent.Status.DRAFT, visibility=ChannelContent.Visibility.PRIVATE,
            created_by=self.owner,
        )
        self.client = APIClient()


class SubtitleCardVisibilityTests(PlayerFeatureVisibilityTestBase):
    def test_anonymous_can_read_subtitles_on_public_content(self):
        ChannelContentSubtitle.objects.create(content=self.public_content, language="en", vtt_url="https://cdn.example.com/en.vtt")

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.public_content.id}/subtitles/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["results"]), 1)

    def test_anonymous_cannot_read_subtitles_on_private_content(self):
        ChannelContentSubtitle.objects.create(content=self.private_content, language="en", vtt_url="https://cdn.example.com/en.vtt")

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/subtitles/")

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_preview_can_still_read_subtitles_on_their_own_private_content(self):
        ChannelContentSubtitle.objects.create(content=self.private_content, language="en", vtt_url="https://cdn.example.com/en.vtt")
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/subtitles/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_anonymous_cannot_read_cards_on_private_content(self):
        ChannelContentCard.objects.create(content=self.private_content, card_type=ChannelContentCard.CardType.LINK)

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/cards/")

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_can_read_cards_on_public_content(self):
        ChannelContentCard.objects.create(content=self.public_content, card_type=ChannelContentCard.CardType.LINK)

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.public_content.id}/cards/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["results"]), 1)


class OtherPlayerFeatureVisibilityTests(PlayerFeatureVisibilityTestBase):
    def test_end_screen_404s_on_private_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/end-screen/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_audio_tracks_404s_on_private_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/audio-tracks/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_audio_tracks_still_ok_on_public_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.public_content.id}/audio-tracks/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_geo_restriction_404s_on_private_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/geo-restriction/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_geo_restriction_still_ok_on_public_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.public_content.id}/geo-restriction/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_premiere_404s_on_private_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/premiere/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_products_404s_on_private_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/products/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_clips_404s_on_private_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/clips/public/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_related_404s_on_private_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.private_content.id}/related/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_related_still_ok_on_public_content_for_anonymous(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.public_content.id}/related/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)


class EmbedModerationGateTests(PlayerFeatureVisibilityTestBase):
    def test_embed_404s_for_unapproved_content_even_though_public_and_published(self):
        """The embed gate's own `is_public` check (visibility+status only)
        would have let this through before the moderation-gate fix - this
        content is public+published but never actually PASSED review."""
        content = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Unreviewed",
            status=ChannelContent.Status.PUBLISHED, visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(), created_by=self.owner,
        )
        with self.settings(KIS_EMBEDS_ENABLED="true"):
            res = self.client.get(f"/api/v1/broadcasts/embed/contents/{content.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_embed_works_for_approved_public_content(self):
        with self.settings(KIS_EMBEDS_ENABLED="true"):
            res = self.client.get(f"/api/v1/broadcasts/embed/contents/{self.public_content.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
