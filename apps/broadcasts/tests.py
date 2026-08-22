import json
import tempfile
import threading
import uuid
from io import StringIO
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from apps.accounts.models import AccountTier, Profile, Subscription
from apps.accounts.tiers import ensure_default_account_tiers
from apps.billing.direct_payments import reconcile_direct_payment_callback
from apps.billing.models import DirectPaymentIntent
from apps.media.models import MediaSafetyScan, MediaUploadIntent
from apps.broadcasts.models import (
    BroadcastChannel,
    BroadcastChannelRole,
    BroadcastChannelSubscription,
    BroadcastPlaylist,
    ChannelContent,
    ChannelContentAsset,
    ChannelContentEmbed,
    ChannelContentComment,
    ChannelAnalyticsDailyRollup,
    ChannelEmbedPolicy,
    ChannelContentReaction,
    ChannelContentSave,
    ChannelModerationRecord,
    ChannelContentType,
    BroadcastFeedProfile,
    BroadcastEngagementEvent,
    BroadcastItem,
    BroadcastPlaylist,
    BroadcastMarketProfile,
    BroadcastSourceType,
    BroadcastVideo,
    UserContentPlaylist,
    EducationCourseModuleItemType,
    EducationEnrollmentStatus,
    EducationInstitution,
    EducationInstitutionBooking,
    EducationInstitutionBroadcast,
    EducationInstitutionCourse,
    EducationInstitutionCourseModule,
    EducationInstitutionCourseModuleItem,
    EducationInstitutionClassSession,
    EducationInstitutionEnrollment,
    EducationInstitutionLesson,
    EducationInstitutionMaterial,
    EducationInstitutionMembership,
    EducationInstitutionMembershipPolicy,
    EducationInstitutionMembershipRole,
    EducationInstitutionMembershipStatus,
    EducationInstitutionPayoutAccountStatus,
    EducationInstitutionEvent,
    EducationCourseQuestion,
    EducationCourseReview,
)
from apps.broadcasts.serializers import BroadcastChannelDetailSerializer, BroadcastChannelSummarySerializer
from apps.verification.constants import VerificationBadgeCode, VerificationSubjectType
from apps.verification.models import VerificationBadge, VerificationCase
from apps.verification.services import (
    current_education_institution_verification_status,
    review_education_institution_case,
    start_education_institution_verification_case,
)
from apps.moderation.models import AuditLog as ModerationAuditLog, Flag as ModerationFlag
from apps.broadcasts.feed_entry_store import (
    append_feed_entry,
    delete_feed_entry,
    replace_feed_entry,
    resolve_feed_entry,
)
from apps.broadcasts.views import (
    _decode_feed_cursor,
    _encode_feed_cursor,
    _validate_feed_media_file,
    _validate_remote_feed_attachment,
)
from apps.channels.models import Channel
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.communities.models import Community, CommunityMembership, CommunityRole
from apps.partners.models import Partner, PartnerJoinConfig, PartnerMembership, PartnerMembershipStatus
from rest_framework import status
from rest_framework.test import APITestCase, APITransactionTestCase


class FeedEntryStoreTests(SimpleTestCase):
    def test_feed_entry_store_preserves_profile_shape_while_replacing_entry(self):
        profile = {
            'profile_name': 'Broadcast feed',
            'feeds': [
                {'id': 'one', 'title': 'One'},
                {'id': 'two', 'title': 'Two'},
            ],
        }

        next_profile, feeds, updated = replace_feed_entry(
            profile,
            'two',
            lambda entry: {**entry, 'title': 'Two updated'},
        )

        self.assertEqual(updated['title'], 'Two updated')
        self.assertEqual(next_profile['profile_name'], 'Broadcast feed')
        self.assertEqual([feed['id'] for feed in feeds], ['one', 'two'])
        self.assertEqual(profile['feeds'][1]['title'], 'Two')

    def test_feed_entry_store_append_resolve_delete_flow(self):
        profile = {'feeds': []}
        profile, feeds = append_feed_entry(profile, {'id': 'entry-1', 'title': 'Queued'})
        self.assertEqual(len(feeds), 1)
        resolved = resolve_feed_entry(profile, 'entry-1')
        self.assertEqual(resolved.entry['title'], 'Queued')

        profile, feeds, removed = delete_feed_entry(profile, 'entry-1')
        self.assertEqual(removed['id'], 'entry-1')
        self.assertEqual(feeds, [])
        self.assertEqual(profile['feeds'], [])


class FeedMediaValidationTests(SimpleTestCase):
    def test_local_feed_media_validation_rejects_unsupported_extension(self):
        upload = SimpleUploadedFile(
            'payload.exe',
            b'not-safe',
            content_type='application/x-msdownload',
        )

        with self.assertRaisesMessage(Exception, 'Unsupported'):
            _validate_feed_media_file(upload)

    def test_remote_short_video_validation_requires_duration_under_four_minutes(self):
        with self.assertRaisesMessage(Exception, 'Short video attachments must be under 4 minutes'):
            _validate_remote_feed_attachment(
                {
                    'url': 'https://cdn.example.com/clip.mp4',
                    'mimeType': 'video/mp4',
                    'kind': 'short_video',
                    'duration_seconds': 300,
                }
            )

    def test_remote_video_validation_normalizes_thumbnail_and_scan_status(self):
        attachment = _validate_remote_feed_attachment(
            {
                'url': 'https://cdn.example.com/clip.mp4',
                'mimeType': 'video/mp4',
                'thumbUrl': 'https://cdn.example.com/thumb.jpg',
                'duration_seconds': 42,
            }
        )

        self.assertEqual(attachment['media_type'], 'video')
        self.assertEqual(attachment['thumbnail_url'], 'https://cdn.example.com/thumb.jpg')
        self.assertEqual(attachment['thumbUrl'], 'https://cdn.example.com/thumb.jpg')
        self.assertEqual(attachment['duration_seconds'], 42)
        self.assertEqual(attachment['validation_status'], 'validated')
        self.assertEqual(attachment['scan_status'], 'not_configured')


class BroadcastFeedPaginationHelperTests(SimpleTestCase):
    def test_feed_cursor_round_trip_preserves_legacy_offset_compatibility(self):
        self.assertEqual(_encode_feed_cursor(20), 'o:20')
        self.assertEqual(_encode_feed_cursor(None), None)
        self.assertEqual(_decode_feed_cursor('o:20'), 20)
        self.assertEqual(_decode_feed_cursor('20'), 20)
        self.assertEqual(_decode_feed_cursor('-10'), 0)
        self.assertEqual(_decode_feed_cursor('not-a-cursor'), 0)


class BroadcastChannelModelTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone='5557000001',
            username='channel_owner',
            password='secret',
            country='NG',
        )
        self.viewer = User.objects.create_user(
            phone='5557000002',
            username='channel_viewer',
            password='secret',
            country='NG',
        )

    def _create_channel(self, **overrides):
        payload = {
            'owner_type': BroadcastChannel.OwnerType.USER,
            'owner_id': self.user.id,
            'owner_user': self.user,
            'handle': 'kis-channel',
            'display_name': 'KIS Channel',
            'description': 'Public creator home',
            'settings': {'private_email': 'owner@example.com', 'default_tab': 'home'},
        }
        payload.update(overrides)
        return BroadcastChannel.objects.create(**payload)

    def test_channel_handle_uniqueness_is_enforced_case_insensitively(self):
        self._create_channel(handle='kis-channel')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create_channel(handle='KIS-CHANNEL')

    def test_subscription_uniqueness_is_enforced(self):
        channel = self._create_channel()
        BroadcastChannelSubscription.objects.create(channel=channel, user=self.viewer)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BroadcastChannelSubscription.objects.create(channel=channel, user=self.viewer)

    def test_public_serializer_hides_private_owner_details(self):
        channel = self._create_channel()
        BroadcastPlaylist.objects.create(channel=channel, title='Launch playlist')
        BroadcastChannelSubscription.objects.create(channel=channel, user=self.viewer)

        summary = BroadcastChannelSummarySerializer(channel, context={'user': self.viewer}).data
        detail = BroadcastChannelDetailSerializer(channel, context={'user': self.viewer}).data

        self.assertNotIn('owner_id', summary)
        self.assertNotIn('owner_user', summary)
        self.assertNotIn('private_email', detail.get('settings') or {})
        self.assertTrue(summary['is_subscribed'])
        self.assertEqual(summary['viewer_role'], '')

    def test_staff_admin_can_inspect_channel_records(self):
        channel = self._create_channel()
        self.assertIn(BroadcastChannel, admin.site._registry)
        self.assertIn(BroadcastChannelSubscription, admin.site._registry)
        self.assertIn(BroadcastPlaylist, admin.site._registry)

        detail = BroadcastChannelDetailSerializer(channel, context={'user': self.user}).data
        self.assertNotIn('owner_id', detail)
        self.assertNotIn('owner_user', detail)


class ChannelContentCompatibilityTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone='5557100001',
            username='channel_content_owner',
            password='secret',
            country='NG',
        )
        self.client.force_authenticate(user=self.user)

    def _create_feed_entry(self, title='Launch Feed'):
        response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': title,
                'summary': 'A normalized content bridge test',
                'media_type': 'text',
                'text_plain': 'Bridge body',
                'text_doc': json.dumps({'blocks': [{'text': 'Bridge body'}]}),
            },
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response

    def test_creating_feed_entry_still_returns_old_feed_payload_without_content_row(self):
        response = self._create_feed_entry()
        feed = response.data.get('feed') or {}

        self.assertIn('feed', response.data)
        self.assertIn('feeds', response.data)
        self.assertEqual(feed.get('title'), 'Launch Feed')
        self.assertFalse(ChannelContent.objects.exists())

    def test_broadcasting_feed_entry_creates_channel_content_and_keeps_old_payload(self):
        create_response = self._create_feed_entry()
        entry_id = create_response.data['feed']['id']

        response = self.client.post(f'/api/v1/broadcasts/profiles/feeds/{entry_id}/broadcast/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        feed = response.data.get('feed') or {}
        self.assertEqual(feed.get('id'), entry_id)
        self.assertTrue(feed.get('channel_content_id'))
        content = ChannelContent.objects.get(legacy_feed_entry_id=entry_id)
        self.assertEqual(content.title, 'Launch Feed')
        self.assertEqual(content.status, ChannelContent.Status.PUBLISHED)
        self.assertEqual(content.created_by, self.user)
        self.assertEqual(str(content.id), feed.get('channel_content_id'))

    def test_editing_broadcast_feed_entry_updates_channel_content(self):
        create_response = self._create_feed_entry()
        entry_id = create_response.data['feed']['id']
        self.client.post(f'/api/v1/broadcasts/profiles/feeds/{entry_id}/broadcast/')

        response = self.client.patch(
            f'/api/v1/broadcasts/profiles/feeds/{entry_id}/',
            {
                'title': 'Updated Launch Feed',
                'summary': 'Updated summary',
                'media_type': 'text',
                'text_plain': 'Updated body',
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        content = ChannelContent.objects.get(legacy_feed_entry_id=entry_id)
        self.assertEqual(content.title, 'Updated Launch Feed')
        self.assertEqual(content.description, 'Updated summary')
        self.assertEqual(content.text_plain, 'Updated body')

    def test_delete_and_unbroadcast_archive_channel_content_without_hard_delete_row_removal(self):
        create_response = self._create_feed_entry()
        entry_id = create_response.data['feed']['id']
        self.client.post(f'/api/v1/broadcasts/profiles/feeds/{entry_id}/broadcast/')

        unbroadcast = self.client.delete(f'/api/v1/broadcasts/profiles/feeds/{entry_id}/unbroadcast/')
        self.assertEqual(unbroadcast.status_code, status.HTTP_200_OK, unbroadcast.data)
        content = ChannelContent.objects.get(legacy_feed_entry_id=entry_id)
        self.assertEqual(content.status, ChannelContent.Status.ARCHIVED)
        self.assertFalse(content.is_deleted)

        delete_response = self.client.delete(f'/api/v1/broadcasts/profiles/feeds/{entry_id}/')
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        content.refresh_from_db()
        self.assertEqual(content.status, ChannelContent.Status.ARCHIVED)
        self.assertTrue(content.is_deleted)


class ChannelBackfillTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone='5557150001',
            username='channel_backfill_owner',
            password='secret',
            country='NG',
        )
        self.entry_id = uuid.uuid4()
        self.feed_profile = BroadcastFeedProfile.objects.create(
            profile=self.user.profile,
            payload={
                'profile_name': 'Backfill Studio',
                'feeds': [
                    {
                        'id': str(self.entry_id),
                        'title': 'Backfill launch',
                        'summary': 'Legacy feed summary',
                        'media_type': 'image',
                        'text_plain': 'Legacy feed body',
                        'is_broadcast': True,
                        'attachments': [
                            {
                                'media_type': 'image',
                                'url': 'https://cdn.example.com/backfill.jpg',
                                'thumbnail_url': 'https://cdn.example.com/backfill-thumb.jpg',
                            }
                        ],
                    }
                ],
            },
        )
        self.broadcast_item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id=str(self.entry_id),
            broadcasted_by=self.user,
            metadata={'title': 'Backfill launch'},
        )

    def test_backfill_dry_run_creates_nothing(self):
        out = StringIO()
        call_command('backfill_broadcast_channels', '--dry-run', '--limit', '20', stdout=out)

        self.assertIn('mode=DRY-RUN', out.getvalue())
        self.assertFalse(BroadcastChannel.objects.exists())
        self.assertFalse(ChannelContent.objects.exists())

    def test_backfill_apply_creates_channel_content_assets_and_links_broadcast_item(self):
        out = StringIO()
        call_command('backfill_broadcast_channels', '--apply', '--limit', '20', stdout=out)

        self.assertIn('mode=APPLY', out.getvalue())
        channel = BroadcastChannel.objects.get(owner_user=self.user)
        content = ChannelContent.objects.get(channel=channel, legacy_feed_entry_id=self.entry_id)
        self.assertEqual(content.title, 'Backfill launch')
        self.assertEqual(content.status, ChannelContent.Status.PUBLISHED)
        self.assertEqual(content.assets.count(), 1)
        self.broadcast_item.refresh_from_db()
        self.assertEqual(self.broadcast_item.metadata.get('channel_content_id'), str(content.id))

    def test_backfill_apply_twice_is_idempotent_and_preserves_legacy_feed_api(self):
        call_command('backfill_broadcast_channels', '--apply', '--limit', '20', stdout=StringIO())
        call_command('backfill_broadcast_channels', '--apply', '--limit', '20', stdout=StringIO())

        channel = BroadcastChannel.objects.get(owner_user=self.user)
        self.assertEqual(ChannelContent.objects.filter(channel=channel, legacy_feed_entry_id=self.entry_id).count(), 1)
        self.assertEqual(channel.contents.first().assets.count(), 1)

        self.client.force_authenticate(user=self.user)
        legacy_response = self.client.get('/api/v1/broadcasts/profiles/feeds/')
        self.assertEqual(legacy_response.status_code, status.HTTP_200_OK, legacy_response.data)
        self.assertEqual(legacy_response.data['feeds'][0]['id'], str(self.entry_id))
        self.assertTrue(legacy_response.data['feeds'][0].get('channel_content_id'))

        normalized_response = self.client.get(f'/api/v1/broadcasts/channels/{channel.id}/contents/')
        self.assertEqual(normalized_response.status_code, status.HTTP_200_OK, normalized_response.data)
        self.assertEqual(len(normalized_response.data['results']), 1)


class BroadcastChannelApiTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5557200001',
            username='api_channel_owner',
            password='secret',
            country='NG',
        )
        self.viewer = User.objects.create_user(
            phone='5557200002',
            username='api_channel_viewer',
            password='secret',
            country='NG',
        )
        # Channel creation is tier-gated (Free tier's channels_create limit
        # is 0 — see apps/accounts/tier_presets.py). Give the viewer a
        # Business subscription (channels_create=5) so tests exercising
        # channel creation reflect a real paying user with enough headroom
        # to create more than one channel, rather than the untiered
        # default or a limit of exactly 1.
        ensure_default_account_tiers()
        business_tier = AccountTier.objects.filter(name__iexact="Business").first()
        if business_tier:
            Subscription.objects.create(user=self.viewer, tier=business_tier, status="active")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle='api-channel',
            display_name='API Channel',
            description='Public API channel',
            is_public=True,
        )
        BroadcastChannelRole.objects.create(
            channel=self.channel,
            user=self.owner,
            role=BroadcastChannelRole.Role.OWNER,
        )

    def test_anonymous_can_view_public_channel(self):
        response = self.client.get('/api/v1/broadcasts/channels/api-channel/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data.get('handle'), 'api-channel')
        self.assertNotIn('owner_id', response.data)

    def test_anonymous_cannot_view_private_channel(self):
        self.channel.is_public = False
        self.channel.save(update_fields=['is_public'])

        response = self.client.get(f'/api/v1/broadcasts/channels/{self.channel.id}/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(KIS_PUBLIC_WEB_ENABLED=True, KIS_PUBLIC_WEB_INDEXING_ENABLED=False, KIS_PUBLIC_WEB_BASE_URL="https://kis.example")
    def test_public_channel_landing_returns_safe_seo_and_share_metadata(self):
        ChannelContent.objects.create(
            channel=self.channel,
            content_type=ChannelContentType.VIDEO,
            title="Public testimony",
            description="Safe public testimony content.",
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            created_by=self.owner,
        )

        response = self.client.get("/api/v1/broadcasts/public/channels/api-channel/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        serialized = str(response.data).lower()
        self.assertEqual(response.data["type"], "channel")
        self.assertEqual(response.data["seo"]["robots"], "noindex,nofollow")
        self.assertEqual(response.data["share_card"]["url"], "https://kis.example/channels/api-channel")
        self.assertEqual(len(response.data["latest_contents"]), 1)
        self.assertNotIn("storage_path", serialized)
        self.assertNotIn("secret", serialized)

    @override_settings(KIS_PUBLIC_WEB_ENABLED=True, KIS_PUBLIC_WEB_INDEXING_ENABLED=False, KIS_PUBLIC_WEB_BASE_URL="https://kis.example")
    def test_public_channel_landing_sanitizes_private_profile_media_urls(self):
        self.channel.avatar_url = "https://cdn.example.com/private/raw/avatar.png"
        self.channel.banner_url = "https://cdn.example.com/private/raw/banner.png"
        self.channel.save(update_fields=["avatar_url", "banner_url", "updated_at"])

        response = self.client.get("/api/v1/broadcasts/public/channels/api-channel/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        rendered = json.dumps(response.data)
        self.assertEqual(response.data["avatar_url"], "")
        self.assertEqual(response.data["banner_url"], "")
        self.assertEqual(response.data["share_card"]["image"], "")
        self.assertNotIn("private/raw", rendered)

    @override_settings(KIS_PUBLIC_WEB_ENABLED=True, KIS_PUBLIC_WEB_BASE_URL="https://kis.example")
    def test_public_content_landing_hides_private_and_child_sensitive_content(self):
        public_content = ChannelContent.objects.create(
            channel=self.channel,
            content_type=ChannelContentType.TEXT,
            title="Public lesson",
            text_plain="A public lesson.",
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            created_by=self.owner,
        )
        private_content = ChannelContent.objects.create(
            channel=self.channel,
            content_type=ChannelContentType.TEXT,
            title="Private lesson",
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PRIVATE,
            published_at=timezone.now(),
            created_by=self.owner,
        )
        child_sensitive = ChannelContent.objects.create(
            channel=self.channel,
            content_type=ChannelContentType.TEXT,
            title="Child sensitive",
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            metadata={"child_sensitive": True},
            created_by=self.owner,
        )

        public_response = self.client.get(f"/api/v1/broadcasts/public/contents/{public_content.id}/")
        private_response = self.client.get(f"/api/v1/broadcasts/public/contents/{private_content.id}/")
        child_response = self.client.get(f"/api/v1/broadcasts/public/contents/{child_sensitive.id}/")

        self.assertEqual(public_response.status_code, status.HTTP_200_OK, public_response.data)
        self.assertEqual(public_response.data["seo"]["canonical_url"], f"https://kis.example/channels/api-channel/content/{public_content.id}")
        self.assertEqual(private_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(child_response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(KIS_PUBLIC_WEB_ENABLED=True, KIS_PUBLIC_WEB_BASE_URL="https://kis.example")
    def test_public_content_landing_sanitizes_private_asset_urls(self):
        content = ChannelContent.objects.create(
            channel=self.channel,
            content_type=ChannelContentType.VIDEO,
            title="Public video",
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            thumbnail_url="private/raw/thumb.jpg",
            created_by=self.owner,
        )
        ChannelContentAsset.objects.create(
            content=content,
            asset_type="video",
            url="private/raw/video.mp4",
            storage_path="private/raw/video.mp4",
            thumbnail_url="https://cdn.example.com/private/raw/thumb.jpg",
            mime_type="video/mp4",
            metadata={"private_email": "owner@example.com"},
        )

        response = self.client.get(f"/api/v1/broadcasts/public/contents/{content.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        rendered = json.dumps(response.data)
        self.assertEqual(response.data["thumbnail_url"], "")
        self.assertEqual(response.data["asset"]["url"], "")
        self.assertEqual(response.data["asset"]["thumbnail_url"], "")
        self.assertNotIn("storage_path", rendered)
        self.assertNotIn("private/raw", rendered)
        self.assertNotIn("owner@example.com", rendered)

    @override_settings(KIS_PUBLIC_WEB_ENABLED=True, KIS_PUBLIC_WEB_INDEXING_ENABLED=False)
    def test_public_robots_defaults_to_noindex_until_qa(self):
        response = self.client.get("/api/v1/broadcasts/public/robots.txt")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("Disallow: /", response.content.decode("utf-8"))

    def test_user_can_create_own_channel_and_duplicate_handle_fails(self):
        self.client.force_authenticate(user=self.viewer)
        first = self.client.post(
            '/api/v1/broadcasts/channels/',
            {'handle': 'viewer-channel', 'display_name': 'Viewer Channel'},
            format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertTrue(BroadcastChannel.objects.filter(handle='viewer-channel', owner_user=self.viewer).exists())
        created_id = first.data.get('id')
        self.assertTrue(
            BroadcastChannelRole.objects.filter(
                channel_id=created_id,
                user=self.viewer,
                role=BroadcastChannelRole.Role.OWNER,
            ).exists()
        )

        mine = self.client.get('/api/v1/broadcasts/channels/?mine=1')
        self.assertEqual(mine.status_code, status.HTTP_200_OK, mine.data)
        rows = mine.data.get('results', mine.data)
        self.assertTrue(any(str(row.get('id')) == str(created_id) for row in rows))

        second = self.client.post(
            '/api/v1/broadcasts/channels/',
            {'handle': 'VIEWER-channel', 'display_name': 'Duplicate'},
            format='json',
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_free_tier_user_cannot_create_broadcast_channel(self):
        # Free tier's channels_create limit is 0 (apps/accounts/tier_presets.py)
        # — this must be enforced server-side, not just hidden in the UI.
        free_user = get_user_model().objects.create_user(
            phone='5557200099', username='api_channel_free_user', password='secret', country='NG',
        )
        self.client.force_authenticate(user=free_user)
        response = self.client.post(
            '/api/v1/broadcasts/channels/',
            {'handle': 'free-tier-channel', 'display_name': 'Free Tier Channel'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.assertFalse(BroadcastChannel.objects.filter(handle='free-tier-channel').exists())

    def test_pro_tier_user_blocked_after_reaching_channel_limit(self):
        # Pro tier's channels_create limit is 1 (apps/accounts/tier_presets.py).
        ensure_default_account_tiers()
        pro_tier = AccountTier.objects.filter(name__iexact="Pro").first()
        pro_user = get_user_model().objects.create_user(
            phone='5557200098', username='api_channel_pro_user', password='secret', country='NG',
        )
        if pro_tier:
            Subscription.objects.create(user=pro_user, tier=pro_tier, status="active")
        self.client.force_authenticate(user=pro_user)

        first = self.client.post(
            '/api/v1/broadcasts/channels/',
            {'handle': 'pro-first-channel', 'display_name': 'First Channel'},
            format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)

        second = self.client.post(
            '/api/v1/broadcasts/channels/',
            {'handle': 'pro-second-channel', 'display_name': 'Second Channel'},
            format='json',
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST, second.data)
        self.assertFalse(BroadcastChannel.objects.filter(handle='pro-second-channel').exists())

    def test_non_manager_cannot_edit_channel_or_content(self):
        content = ChannelContent.objects.create(
            channel=self.channel,
            content_type='text',
            title='Draft',
            status=ChannelContent.Status.DRAFT,
            visibility=ChannelContent.Visibility.PRIVATE,
            created_by=self.owner,
        )
        self.client.force_authenticate(user=self.viewer)

        channel_response = self.client.patch(
            f'/api/v1/broadcasts/channels/{self.channel.id}/',
            {'display_name': 'Hijacked'},
            format='json',
        )
        content_response = self.client.patch(
            f'/api/v1/broadcasts/channel-contents/{content.id}/',
            {'title': 'Hijacked'},
            format='json',
        )

        self.assertEqual(channel_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(content_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_subscribe_unsubscribe_changes_subscriber_count_idempotently(self):
        self.client.force_authenticate(user=self.viewer)

        first = self.client.post(f'/api/v1/broadcasts/channels/{self.channel.id}/subscribe/', {}, format='json')
        second = self.client.post(f'/api/v1/broadcasts/channels/{self.channel.id}/subscribe/', {}, format='json')
        self.channel.refresh_from_db()
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual(self.channel.subscriber_count, 1)

        bell = self.client.patch(
            f'/api/v1/broadcasts/channels/{self.channel.id}/subscription/',
            {'notifications': 'all'},
            format='json',
        )
        self.assertEqual(bell.status_code, status.HTTP_200_OK, bell.data)
        self.assertEqual(bell.data.get('notifications'), 'all')

        delete = self.client.delete(f'/api/v1/broadcasts/channels/{self.channel.id}/subscribe/')
        repeat_delete = self.client.delete(f'/api/v1/broadcasts/channels/{self.channel.id}/subscribe/')
        self.channel.refresh_from_db()
        self.assertEqual(delete.status_code, status.HTTP_200_OK, delete.data)
        self.assertEqual(repeat_delete.status_code, status.HTTP_200_OK, repeat_delete.data)
        self.assertEqual(self.channel.subscriber_count, 0)

    def test_public_content_list_excludes_drafts(self):
        published = ChannelContent.objects.create(
            channel=self.channel,
            content_type='text',
            title='Published',
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            created_by=self.owner,
        )
        draft = ChannelContent.objects.create(
            channel=self.channel,
            content_type='text',
            title='Draft',
            status=ChannelContent.Status.DRAFT,
            visibility=ChannelContent.Visibility.PRIVATE,
            created_by=self.owner,
        )

        response = self.client.get(f'/api/v1/broadcasts/channels/{self.channel.id}/contents/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        ids = {row.get('id') for row in response.data.get('results') or []}
        self.assertIn(str(published.id), ids)
        self.assertNotIn(str(draft.id), ids)

    def test_owner_can_create_publish_asset_and_playlist(self):
        self.client.force_authenticate(user=self.owner)
        create = self.client.post(
            f'/api/v1/broadcasts/channels/{self.channel.id}/contents/',
            {'content_type': 'text', 'title': 'Studio Draft', 'text_plain': 'Body'},
            format='json',
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.data)
        content_id = create.data.get('id')

        asset = self.client.post(
            f'/api/v1/broadcasts/channel-contents/{content_id}/assets/',
            {'asset_type': 'image', 'url': 'https://example.com/image.jpg', 'caption': 'Cover'},
            format='json',
        )
        self.assertEqual(asset.status_code, status.HTTP_201_CREATED, asset.data)

        publish = self.client.post(f'/api/v1/broadcasts/channel-contents/{content_id}/publish/', {}, format='json')
        self.assertEqual(publish.status_code, status.HTTP_200_OK, publish.data)
        self.assertEqual(publish.data.get('status'), ChannelContent.Status.PUBLISHED)

        playlist = self.client.post(
            f'/api/v1/broadcasts/channels/{self.channel.id}/playlists/',
            {'title': 'Featured'},
            format='json',
        )
        self.assertEqual(playlist.status_code, status.HTTP_201_CREATED, playlist.data)
        self.assertTrue(BroadcastPlaylist.objects.filter(channel=self.channel, title='Featured').exists())

    def test_channel_content_rejects_review_held_attachments(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            f'/api/v1/broadcasts/channels/{self.channel.id}/contents/',
            {
                'content_type': 'image',
                'title': 'Unsafe pending upload',
                'attachments': [
                    {
                        'kind': 'image',
                        'url': '',
                        'scan_status': 'pending_review',
                        'requiresReview': True,
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ChannelContent.objects.filter(title='Unsafe pending upload').exists())

    def test_channel_content_publish_blocks_processing_asset(self):
        content = ChannelContent.objects.create(
            channel=self.channel,
            content_type=ChannelContentType.VIDEO,
            title='Processing video',
            status=ChannelContent.Status.DRAFT,
            visibility=ChannelContent.Visibility.PRIVATE,
            created_by=self.owner,
        )
        ChannelContentAsset.objects.create(
            content=content,
            asset_type='video',
            url='https://cdn.example.com/video.mp4',
            mime_type='video/mp4',
            processing_status='processing',
            metadata={'pipeline': {'processing_status': 'processing'}},
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(f'/api/v1/broadcasts/channel-contents/{content.id}/publish/', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        content.refresh_from_db()
        self.assertEqual(content.status, ChannelContent.Status.DRAFT)

    def test_legacy_feed_broadcast_blocks_review_held_attachment(self):
        self.client.force_authenticate(user=self.owner)
        create = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Review held legacy feed',
                'summary': 'Must not broadcast yet.',
                'media_type': 'image',
                'attachment_payloads': json.dumps([
                    {
                        'kind': 'image',
                        'url': 'https://cdn.example.com/image.jpg',
                        'mime_type': 'image/jpeg',
                        'scan_status': 'pending_review',
                        'requiresReview': True,
                    }
                ]),
            },
            format='multipart',
        )

        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST)

    def test_legacy_feed_create_with_channel_id_creates_channel_scoped_content(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Channel scoped legacy post',
                'summary': 'This should live under the selected channel.',
                'media_type': 'text',
                'channel_id': str(self.channel.id),
                'content_type': 'rich_text',
                'visibility': 'private',
                'text_plain': 'This should live under the selected channel.',
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        feed = response.data.get('feed') or {}
        self.assertEqual(feed.get('channel_id'), str(self.channel.id))
        self.assertTrue(feed.get('channel_content_id'))
        content = ChannelContent.objects.get(id=feed.get('channel_content_id'))
        self.assertEqual(content.channel_id, self.channel.id)
        self.assertEqual(content.content_type, ChannelContentType.RICH_TEXT)
        self.assertEqual(content.status, ChannelContent.Status.DRAFT)

    def test_owner_can_broadcast_and_unbroadcast_channel_idempotently(self):
        self.client.force_authenticate(user=self.owner)

        first = self.client.post(f'/api/v1/broadcasts/channels/{self.channel.id}/broadcast/', {}, format='json')
        second = self.client.post(f'/api/v1/broadcasts/channels/{self.channel.id}/broadcast/', {}, format='json')

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertTrue(first.data.get('is_broadcast'))
        self.assertEqual(
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.BROADCAST_CHANNEL,
                source_id=str(self.channel.id),
                is_deleted=False,
            ).count(),
            1,
        )

        feed = self.client.get('/api/v1/broadcasts/?source_type=broadcast_channel')
        self.assertEqual(feed.status_code, status.HTTP_200_OK, feed.data)
        self.assertTrue(any(row.get('source_type') == 'broadcast_channel' for row in feed.data.get('results') or []))

        delete = self.client.delete(f'/api/v1/broadcasts/channels/{self.channel.id}/broadcast/')
        repeat_delete = self.client.delete(f'/api/v1/broadcasts/channels/{self.channel.id}/broadcast/')
        self.assertEqual(delete.status_code, status.HTTP_200_OK, delete.data)
        self.assertEqual(repeat_delete.status_code, status.HTTP_200_OK, repeat_delete.data)
        self.assertFalse(delete.data.get('is_broadcast'))
        self.assertFalse(
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.BROADCAST_CHANNEL,
                source_id=str(self.channel.id),
                is_deleted=False,
            ).exists()
        )

    def test_owner_can_broadcast_and_unbroadcast_channel_content_idempotently(self):
        content = ChannelContent.objects.create(
            channel=self.channel,
            content_type='text',
            title='Promoted content',
            text_plain='Channel content body',
            status=ChannelContent.Status.DRAFT,
            visibility=ChannelContent.Visibility.PRIVATE,
            created_by=self.owner,
        )
        self.client.force_authenticate(user=self.owner)

        first = self.client.post(f'/api/v1/broadcasts/channel-contents/{content.id}/broadcast/', {}, format='json')
        second = self.client.post(f'/api/v1/broadcasts/channel-contents/{content.id}/broadcast/', {}, format='json')

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        content.refresh_from_db()
        self.assertEqual(content.status, ChannelContent.Status.PUBLISHED)
        self.assertEqual(content.visibility, ChannelContent.Visibility.PUBLIC)
        self.assertTrue(first.data.get('is_broadcast'))
        self.assertEqual(
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.CHANNEL_CONTENT,
                source_id=str(content.id),
                is_deleted=False,
            ).count(),
            1,
        )

        feed = self.client.get('/api/v1/broadcasts/?source_type=channel_content')
        self.assertEqual(feed.status_code, status.HTTP_200_OK, feed.data)
        self.assertTrue(any(row.get('channel_content_id') == str(content.id) for row in feed.data.get('results') or []))

        delete = self.client.delete(f'/api/v1/broadcasts/channel-contents/{content.id}/broadcast/')
        repeat_delete = self.client.delete(f'/api/v1/broadcasts/channel-contents/{content.id}/broadcast/')
        self.assertEqual(delete.status_code, status.HTTP_200_OK, delete.data)
        self.assertEqual(repeat_delete.status_code, status.HTTP_200_OK, repeat_delete.data)
        self.assertFalse(delete.data.get('is_broadcast'))
        self.assertFalse(
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.CHANNEL_CONTENT,
                source_id=str(content.id),
                is_deleted=False,
            ).exists()
        )

    def test_non_manager_cannot_broadcast_channel_or_content(self):
        content = ChannelContent.objects.create(
            channel=self.channel,
            content_type='text',
            title='Private',
            status=ChannelContent.Status.DRAFT,
            visibility=ChannelContent.Visibility.PRIVATE,
            created_by=self.owner,
        )
        self.client.force_authenticate(user=self.viewer)

        channel_response = self.client.post(f'/api/v1/broadcasts/channels/{self.channel.id}/broadcast/', {}, format='json')
        content_response = self.client.post(f'/api/v1/broadcasts/channel-contents/{content.id}/broadcast/', {}, format='json')

        self.assertEqual(channel_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(content_response.status_code, status.HTTP_403_FORBIDDEN)


@override_settings(
    KIS_EMBEDS_ENABLED=True,
    KIS_PUBLIC_EMBED_BASE_URL='https://kis.example.com',
    KIS_EMBED_SIGNING_SECRET='test-embed-secret',
)
class ChannelEmbedTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5557300001',
            username='embed_owner',
            password='secret',
            country='NG',
        )
        self.viewer = User.objects.create_user(
            phone='5557300002',
            username='embed_viewer',
            password='secret',
            country='NG',
        )
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle='embed-channel',
            display_name='Embed Channel',
            is_public=True,
        )
        BroadcastChannelRole.objects.create(
            channel=self.channel,
            user=self.owner,
            role=BroadcastChannelRole.Role.OWNER,
        )
        self.content = ChannelContent.objects.create(
            channel=self.channel,
            content_type='video',
            title='Public embed',
            description='Safe public description',
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            thumbnail_url='https://cdn.example.com/thumb.jpg',
            metadata={'private_email': 'owner@example.com'},
            created_by=self.owner,
        )
        ChannelContentAsset.objects.create(
            content=self.content,
            asset_type='video',
            url='https://cdn.example.com/video.mp4',
            storage_path='private/raw/video.mp4',
            thumbnail_url='https://cdn.example.com/thumb.jpg',
            mime_type='video/mp4',
            width=1280,
            height=720,
        )

    @override_settings(KIS_EMBEDS_ENABLED=False)
    def test_embeds_are_disabled_by_default_flag(self):
        response = self.client.get(f'/api/v1/broadcasts/embed/contents/{self.content.id}/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_embed_response_excludes_private_metadata_and_storage_path(self):
        response = self.client.get(
            f'/api/v1/broadcasts/embed/contents/{self.content.id}/',
            HTTP_ORIGIN='https://trusted.example.com',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data.get('title'), 'Public embed')
        self.assertNotIn('metadata', response.data)
        self.assertNotIn('storage_path', json.dumps(response.data))
        self.assertNotIn('private_email', json.dumps(response.data))
        self.assertIn('<iframe', response.data.get('embed_html') or '')

    def test_oembed_returns_public_iframe_payload(self):
        response = self.client.get(f'/api/v1/broadcasts/embed/contents/{self.content.id}/oembed/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data.get('version'), '1.0')
        self.assertEqual(response.data.get('provider_name'), 'KIS')
        self.assertIn(str(self.content.id), response.data.get('html') or '')

    def test_embed_payload_sanitizes_private_thumbnail_and_escapes_token_query(self):
        self.content.thumbnail_url = 'https://cdn.example.com/private/raw/thumb.jpg'
        self.content.save(update_fields=['thumbnail_url', 'updated_at'])
        self.content.assets.update(
            url='https://cdn.example.com/private/raw/video.mp4',
            thumbnail_url='https://cdn.example.com/private/raw/asset-thumb.jpg',
        )
        unsafe_token = 'bad"><script>alert(1)</script>'

        response = self.client.get(
            f'/api/v1/broadcasts/embed/contents/{self.content.id}/',
            {'token': unsafe_token},
            HTTP_ORIGIN='https://trusted.example.com',
        )
        oembed = self.client.get(
            f'/api/v1/broadcasts/embed/contents/{self.content.id}/oembed/',
            {'token': unsafe_token},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(oembed.status_code, status.HTTP_200_OK, oembed.data)
        rendered = json.dumps(response.data) + json.dumps(oembed.data)
        self.assertEqual(response.data.get('thumbnail_url'), '')
        self.assertEqual(oembed.data.get('thumbnail_url'), '')
        self.assertNotIn('private/raw', rendered)
        self.assertNotIn('<script', rendered.lower())
        self.assertIn('bad%22%3E%3Cscript%3Ealert%281%29%3C/script%3E', rendered)

    def test_blocked_domain_is_denied(self):
        ChannelEmbedPolicy.objects.create(channel=self.channel, blocked_domains=['blocked.example.com'])

        response = self.client.get(
            f'/api/v1/broadcasts/embed/contents/{self.content.id}/',
            HTTP_ORIGIN='https://blocked.example.com',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_private_content_requires_signed_token(self):
        self.content.visibility = ChannelContent.Visibility.PRIVATE
        self.content.status = ChannelContent.Status.DRAFT
        self.content.save(update_fields=['visibility', 'status', 'updated_at'])
        self.client.force_authenticate(user=self.owner)

        denied = self.client.get(f'/api/v1/broadcasts/embed/contents/{self.content.id}/')
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        token_response = self.client.post(
            f'/api/v1/broadcasts/channel-contents/{self.content.id}/embed-token/',
            {'domain': 'trusted.example.com'},
            format='json',
        )
        self.assertEqual(token_response.status_code, status.HTTP_201_CREATED, token_response.data)
        self.assertTrue(ChannelContentEmbed.objects.filter(content=self.content, domain='trusted.example.com').exists())

        token = token_response.data.get('token')
        allowed = self.client.get(
            f'/api/v1/broadcasts/embed/contents/{self.content.id}/?token={token}',
            HTTP_ORIGIN='https://trusted.example.com',
        )
        self.assertEqual(allowed.status_code, status.HTTP_200_OK, allowed.data)


class ChannelEngagementTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone='5557400001', username='engage_owner', password='secret', country='NG')
        self.viewer = User.objects.create_user(phone='5557400002', username='engage_viewer', password='secret', country='NG')
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle='engage-channel',
            display_name='Engage Channel',
            is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.content = ChannelContent.objects.create(
            channel=self.channel,
            content_type='video',
            title='Engage content',
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            created_by=self.owner,
        )

    def test_react_save_comment_share_and_view_update_counts(self):
        self.client.force_authenticate(user=self.viewer)

        react = self.client.post(f'/api/v1/broadcasts/channel-contents/{self.content.id}/react/', {'reaction': 'like'}, format='json')
        save = self.client.post(f'/api/v1/broadcasts/channel-contents/{self.content.id}/save/', {}, format='json')
        comment = self.client.post(f'/api/v1/broadcasts/channel-contents/{self.content.id}/comments/', {'body': 'Great upload'}, format='json')
        share = self.client.post(f'/api/v1/broadcasts/channel-contents/{self.content.id}/share/', {'completed': True}, format='json')
        view = self.client.post(f'/api/v1/broadcasts/channel-contents/{self.content.id}/view/', {'progress_seconds': 12}, format='json')

        self.assertEqual(react.status_code, status.HTTP_200_OK, react.data)
        self.assertEqual(save.status_code, status.HTTP_200_OK, save.data)
        self.assertEqual(comment.status_code, status.HTTP_201_CREATED, comment.data)
        self.assertEqual(share.status_code, status.HTTP_200_OK, share.data)
        self.assertEqual(view.status_code, status.HTTP_200_OK, view.data)
        self.assertTrue(ChannelContentReaction.objects.filter(content=self.content, user=self.viewer).exists())
        self.assertTrue(ChannelContentSave.objects.filter(content=self.content, user=self.viewer).exists())
        self.assertTrue(ChannelContentComment.objects.filter(content=self.content, user=self.viewer).exists())
        self.content.refresh_from_db()
        self.assertGreaterEqual(int(self.content.stats.get('views') or 0), 1)
        self.assertGreaterEqual(int(self.content.stats.get('shares') or 0), 1)

    def test_playlist_item_add_remove_requires_channel_manager(self):
        playlist = BroadcastPlaylist.objects.create(channel=self.channel, title='Featured')
        self.client.force_authenticate(user=self.owner)

        add = self.client.post(
            f'/api/v1/broadcasts/playlists/{playlist.id}/items/',
            {'content_id': str(self.content.id)},
            format='json',
        )
        self.assertEqual(add.status_code, status.HTTP_201_CREATED, add.data)
        self.assertEqual(playlist.items.count(), 1)

        remove = self.client.delete(f'/api/v1/broadcasts/playlists/{playlist.id}/items/{self.content.id}/')
        self.assertEqual(remove.status_code, status.HTTP_200_OK, remove.data)
        self.assertEqual(playlist.items.count(), 0)

    def test_channel_content_report_and_moderation_action_are_audited(self):
        self.client.force_authenticate(user=self.viewer)
        report = self.client.post(
            f'/api/v1/broadcasts/channel-contents/{self.content.id}/report/',
            {'reason': 'Unsafe content'},
            format='json',
        )
        self.assertEqual(report.status_code, status.HTTP_201_CREATED, report.data)
        record = ChannelModerationRecord.objects.get(id=report.data['id'])
        self.assertEqual(record.target_type, ChannelModerationRecord.TargetType.CONTENT)
        self.assertTrue(ModerationFlag.objects.filter(target_id=self.content.id).exists())

        self.client.force_authenticate(user=self.owner)
        action = self.client.post(
            f'/api/v1/broadcasts/channel-moderation/{record.id}/action/',
            {'action': 'hide', 'notes': 'Hidden for review'},
            format='json',
        )
        self.assertEqual(action.status_code, status.HTTP_200_OK, action.data)
        record.refresh_from_db()
        self.assertEqual(record.status, ChannelModerationRecord.Status.ACTIONED)
        self.content.refresh_from_db()
        self.assertEqual(self.content.visibility, ChannelContent.Visibility.PRIVATE)
        self.assertTrue(ModerationAuditLog.objects.filter(action='channel_moderation.hide', target_id=record.target_id).exists())

    def test_channel_analytics_endpoint_creates_rollup(self):
        self.client.force_authenticate(user=self.viewer)
        self.client.post(f'/api/v1/broadcasts/channel-contents/{self.content.id}/view/', {'progress_seconds': 23}, format='json')
        self.client.post(f'/api/v1/broadcasts/channel-contents/{self.content.id}/comments/', {'body': 'Useful'}, format='json')

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/broadcasts/channels/{self.channel.id}/analytics/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn('summary', response.data)
        self.assertGreaterEqual(response.data['summary']['views'], 1)
        self.assertTrue(ChannelAnalyticsDailyRollup.objects.filter(channel=self.channel, date=timezone.localdate()).exists())


class UserContentPlaylistApiTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone='5557600001',
            username='playlist_user',
            password='secret',
            country='NG',
        )
        self.client.force_authenticate(user=self.user)

    def test_user_playlist_list_and_create_do_not_crash(self):
        list_response = self.client.get('/api/v1/broadcasts/user-playlists/')
        self.assertEqual(list_response.status_code, status.HTTP_200_OK, list_response.data)
        self.assertEqual(list_response.data['results'], [])

        create_response = self.client.post(
            '/api/v1/broadcasts/user-playlists/',
            {'title': 'Watch later', 'visibility': 'private'},
            format='json',
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        self.assertEqual(create_response.data['title'], 'Watch later')
        self.assertTrue(
            UserContentPlaylist.objects.filter(
                user=self.user,
                title='Watch later',
            ).exists()
        )


@override_settings(
    KIS_EMBEDS_ENABLED=False,
    KIS_PUBLIC_WEB_INDEXING_ENABLED=False,
    KIS_PUBLIC_REFERRALS_ENABLED=False,
    LIVE_STREAM_PROVIDER='disabled',
    LIVE_STREAM_PROVIDER_SANDBOX_ENABLED=False,
    KIS_CHANNEL_MEDIA_LIVE_PROVIDER_CALLS_ENABLED=False,
)
class BroadcastChannelsLaunchProofCommandTests(SimpleTestCase):
    def test_verify_broadcast_channels_launch_command_passes_safe_defaults(self):
        output = StringIO()

        call_command('verify_broadcast_channels_launch', stdout=output)

        rendered = output.getvalue()
        self.assertIn('Broadcast/Channels launch guardrails ready: True', rendered)
        self.assertIn('PASS: broadcast_channel_urls_present', rendered)
        self.assertIn('PASS: channel_asset_public_serialization', rendered)
        self.assertIn('PASS: channel_media_safety_gate', rendered)
        self.assertNotIn('private/raw/video.mp4', rendered)


@override_settings(
    KIS_PUBLIC_WEB_ENABLED=True,
    KIS_PUBLIC_WEB_INDEXING_ENABLED=False,
    KIS_PUBLIC_REFERRALS_ENABLED=False,
    KIS_EMBEDS_ENABLED=False,
)
class PublicWebLaunchProofCommandTests(SimpleTestCase):
    def test_verify_public_web_launch_command_passes_safe_defaults(self):
        output = StringIO()

        call_command("verify_public_web_launch", stdout=output)

        rendered = output.getvalue()
        self.assertIn("Public web launch guardrails ready: True", rendered)
        self.assertIn("PASS: public_web_routes_present", rendered)
        self.assertIn("PASS: public_media_url_sanitizer", rendered)
        self.assertIn("PASS: public_asset_payload_redaction", rendered)
        self.assertNotIn("private/raw/video.mp4", rendered)


class BroadcastProfileManageTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone='5551012020',
            username='broadcast_profile_manage_user',
            password='secret',
            country='NG',
        )
        self.client.force_authenticate(user=self.user)

    def test_manage_market_profile_bootstraps_profile_when_missing(self):
        response = self.client.post(
            '/api/v1/broadcasts/profiles/manage/',
            {
                'profile_type': 'market_profile',
                'updates': {
                    'landing_page_builder': {
                        'hero': {'title': 'Market Profile'},
                        'sections': [],
                    },
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        profile = response.data.get('profile') or {}
        self.assertIsInstance(profile, dict)
        account_profile = Profile.objects.get(user=self.user)
        self.assertTrue(BroadcastMarketProfile.objects.filter(profile=account_profile).exists())

    def test_manage_market_profile_merges_attachments_without_dropping_existing_entries(self):
        first = self.client.post(
            '/api/v1/broadcasts/profiles/manage/',
            {
                'profile_type': 'market_profile',
                'updates': {
                    'attachments': [
                        {'url': 'https://example.com/one.jpg', 'name': 'one.jpg'},
                    ],
                    'shops': [{'id': 'shop_1', 'name': 'Shop One'}],
                },
            },
            format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)

        second = self.client.post(
            '/api/v1/broadcasts/profiles/manage/',
            {
                'profile_type': 'market_profile',
                'updates': {
                    'attachments': [
                        {'url': 'https://example.com/two.jpg', 'name': 'two.jpg'},
                    ],
                },
            },
            format='json',
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        profile = second.data.get('profile') or {}
        attachments = profile.get('attachments') or []
        self.assertEqual(len(attachments), 2)
        # _sanitize_shop (run on the first call, since 'shops' was in that
        # update) normalizes a raw shop entry into a full storefront record
        # (slug, tagline, category, metrics, ...) — this assertion only
        # cares that the shop set on the first call survives untouched
        # through a second update that doesn't mention shops at all.
        shops = profile.get('shops') or []
        self.assertEqual(len(shops), 1)
        self.assertEqual(shops[0].get('id'), 'shop_1')
        self.assertEqual(shops[0].get('name'), 'Shop One')

    def test_manage_education_profile_bootstraps_structure_and_appends_modules(self):
        response = self.client.post(
            '/api/v1/broadcasts/profiles/manage/',
            {
                'profile_type': 'education_profile',
                'updates': {
                    'modules': [
                        {
                            'title': 'Orientation',
                            'summary': 'Start here',
                            'resource_url': 'https://example.com/orientation.pdf',
                        }
                    ],
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        profile = response.data.get('profile') or {}
        self.assertIn('courses', profile)
        self.assertIn('modules', profile)
        self.assertEqual(len(profile.get('modules') or []), 1)
        self.assertEqual((profile.get('modules') or [])[0].get('title'), 'Orientation')

    def test_feed_entry_create_bootstraps_feed_profile_when_missing(self):
        response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'First broadcast feed post',
                'summary': 'Created without pre-building profile',
                'media_type': 'text',
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        profile = response.data.get('profile') or {}
        feed = response.data.get('feed') or {}
        self.assertEqual(profile.get('profile_name'), 'Broadcast feed')
        self.assertEqual(feed.get('title'), 'First broadcast feed post')
        self.assertTrue(feed.get('id'))

    def test_hide_broadcast_is_viewer_specific(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-1',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-1', 'title': 'Hello world'}},
        )

        response = self.client.post(f'/api/v1/broadcasts/{item.id}/hide/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.user.refresh_from_db()
        hidden_ids = self.user.preferences.get('hidden_broadcast_ids') or []
        self.assertIn(str(item.id), hidden_ids)

        feed_response = self.client.get('/api/v1/broadcasts/')
        self.assertEqual(feed_response.status_code, status.HTTP_200_OK, feed_response.data)
        results = feed_response.data.get('results') or []
        self.assertFalse(any(str(row.get('id')) == str(item.id) for row in results))

    def test_hide_broadcast_is_idempotent(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-hide-repeat',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-hide-repeat', 'title': 'Hide me twice'}},
        )

        first = self.client.post(f'/api/v1/broadcasts/{item.id}/hide/', {}, format='json')
        second = self.client.post(f'/api/v1/broadcasts/{item.id}/hide/', {}, format='json')

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.user.refresh_from_db()
        hidden_ids = self.user.preferences.get('hidden_broadcast_ids') or []
        self.assertEqual(hidden_ids.count(str(item.id)), 1)
        self.assertTrue(
            ModerationAuditLog.objects.filter(
                action='broadcast.hide',
                target_id=item.id,
            ).exists()
        )

    def test_report_broadcast_creates_admin_visible_flag_and_audit_log(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-report-1',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-report-1', 'title': 'Report me'}},
        )

        response = self.client.post(
            f'/api/v1/broadcasts/{item.id}/report/',
            {'reason': 'Unsafe content', 'category': 'safety'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data.get('reported'))
        flag_id = response.data.get('flag_id')
        self.assertTrue(flag_id)
        flag = ModerationFlag.objects.get(id=flag_id)
        self.assertEqual(flag.target_type, 'POST')
        self.assertEqual(str(flag.target_id), str(item.id))
        self.assertEqual(str(flag.reporter_id), str(self.user.id))
        self.assertEqual((flag.tags or {}).get('surface'), 'broadcast_feed')
        self.assertTrue(
            ModerationAuditLog.objects.filter(
                action='broadcast.report',
                target_id=item.id,
            ).exists()
        )

    def test_save_broadcast_marks_viewer_saved_and_can_unsave(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-save-1',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-save-1', 'title': 'Save me'}},
        )

        save_response = self.client.post(f'/api/v1/broadcasts/{item.id}/save/', {}, format='json')
        self.assertEqual(save_response.status_code, status.HTTP_200_OK, save_response.data)
        self.assertTrue(save_response.data.get('saved'))

        self.user.refresh_from_db()
        saved_ids = self.user.preferences.get('saved_broadcast_ids') or []
        self.assertIn(str(item.id), saved_ids)

        feed_response = self.client.get('/api/v1/broadcasts/')
        self.assertEqual(feed_response.status_code, status.HTTP_200_OK, feed_response.data)
        results = feed_response.data.get('results') or []
        matching = next((row for row in results if str(row.get('id')) == str(item.id)), None)
        self.assertIsNotNone(matching)
        self.assertTrue(matching.get('viewer_saved'))

        unsave_response = self.client.post(
            f'/api/v1/broadcasts/{item.id}/save/?action=unsave',
            {},
            format='json',
        )
        self.assertEqual(unsave_response.status_code, status.HTTP_200_OK, unsave_response.data)
        self.assertFalse(unsave_response.data.get('saved'))

        self.user.refresh_from_db()
        saved_ids = self.user.preferences.get('saved_broadcast_ids') or []
        self.assertNotIn(str(item.id), saved_ids)

    def test_save_broadcast_is_idempotent(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-save-repeat',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-save-repeat', 'title': 'Save me twice'}},
        )

        first = self.client.post(f'/api/v1/broadcasts/{item.id}/save/', {}, format='json')
        second = self.client.post(f'/api/v1/broadcasts/{item.id}/save/', {}, format='json')

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.user.refresh_from_db()
        saved_ids = self.user.preferences.get('saved_broadcast_ids') or []
        self.assertEqual(saved_ids.count(str(item.id)), 1)

    def test_react_endpoint_toggles_and_switches_emoji(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-react-1',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-react-1', 'title': 'React to me'}},
        )

        first = self.client.post(
            f'/api/v1/broadcasts/{item.id}/react/',
            {'emoji': '🔥'},
            format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertTrue(first.data.get('reacted'))
        self.assertEqual(first.data.get('emoji'), '🔥')
        self.assertEqual(first.data.get('count'), 1)

        second = self.client.post(
            f'/api/v1/broadcasts/{item.id}/react/',
            {'emoji': '🔥'},
            format='json',
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertFalse(second.data.get('reacted'))
        self.assertEqual(second.data.get('count'), 0)

        third = self.client.post(
            f'/api/v1/broadcasts/{item.id}/react/',
            {'emoji': '👏'},
            format='json',
        )
        self.assertEqual(third.status_code, status.HTTP_200_OK, third.data)
        self.assertTrue(third.data.get('reacted'))
        self.assertEqual(third.data.get('emoji'), '👏')
        self.assertEqual(third.data.get('count'), 1)

    def test_comment_room_creation_is_reused_and_membership_is_created(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-comments-1',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-comments-1', 'title': 'Comment on me'}},
        )

        first = self.client.post(f'/api/v1/broadcasts/{item.id}/comment-room/', {}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        first_conversation_id = first.data.get('conversation_id')
        self.assertTrue(first_conversation_id)

        second = self.client.post(f'/api/v1/broadcasts/{item.id}/comment-room/', {}, format='json')
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual(second.data.get('conversation_id'), first_conversation_id)

        item.refresh_from_db()
        self.assertEqual(str(item.comment_conversation_id), str(first_conversation_id))
        self.assertTrue(
          ConversationMember.objects.filter(conversation_id=first_conversation_id, user=self.user).exists()
        )

    def test_share_endpoint_is_repeatable_and_returns_stable_payload(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-share-1',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-share-1', 'title': 'Share me'}},
        )

        first = self.client.post(
            f'/api/v1/broadcasts/{item.id}/share/',
            {'platform': 'app'},
            format='json',
        )
        second = self.client.post(
            f'/api/v1/broadcasts/{item.id}/share/',
            {'platform': 'app'},
            format='json',
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertTrue(first.data.get('shared'))
        self.assertTrue(second.data.get('shared'))
        self.assertEqual(first.data.get('platform'), 'app')
        self.assertEqual(second.data.get('platform'), 'app')
        self.assertTrue(first.data.get('created'))
        self.assertFalse(second.data.get('created'))
        self.assertEqual(first.data.get('share_count'), 1)
        self.assertEqual(second.data.get('share_count'), 1)
        self.assertEqual(
            BroadcastEngagementEvent.objects.filter(
                broadcast_item=item,
                event_type='share',
            ).count(),
            1,
        )

    def test_view_endpoint_is_idempotent_within_window_and_counts_once(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-view-1',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-view-1', 'title': 'View me'}},
        )

        first = self.client.post(f'/api/v1/broadcasts/{item.id}/view/', {}, format='json')
        second = self.client.post(f'/api/v1/broadcasts/{item.id}/view/', {}, format='json')

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertTrue(first.data.get('created'))
        self.assertFalse(second.data.get('created'))
        self.assertEqual(first.data.get('view_count'), 1)
        self.assertEqual(second.data.get('view_count'), 1)

    def test_feed_list_exposes_engagement_counts_and_records_impression_once_per_window(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-counts-1',
            broadcasted_by=self.user,
            metadata={'entry': {'id': 'feed-counts-1', 'title': 'Count me'}},
        )
        BroadcastEngagementEvent.objects.create(
            broadcast_item=item,
            user=self.user,
            event_type='share',
            window_key='test-share',
            platform='app',
        )
        BroadcastEngagementEvent.objects.create(
            broadcast_item=item,
            user=self.user,
            event_type='view',
            window_key='test-view',
        )

        first = self.client.get('/api/v1/broadcasts/?code=broadcast_feed_entry')
        second = self.client.get('/api/v1/broadcasts/?code=broadcast_feed_entry')

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        matching = next((row for row in first.data.get('results') or [] if str(row.get('id')) == str(item.id)), None)
        self.assertIsNotNone(matching)
        self.assertEqual(matching.get('share_count'), 1)
        self.assertEqual(matching.get('view_count'), 1)
        self.assertGreaterEqual(matching.get('impression_count'), 1)
        self.assertEqual(matching.get('comment_count'), 0)
        self.assertEqual(
            BroadcastEngagementEvent.objects.filter(
                broadcast_item=item,
                event_type='impression',
            ).count(),
            1,
        )

    def test_patch_feed_entry_syncs_existing_broadcast_snapshot(self):
        profile_response = self.client.post(
            '/api/v1/broadcasts/profiles/create/',
            {
                'profile_type': 'broadcast_feed',
                'payload': {
                    'title': 'Feed profile',
                },
            },
            format='json',
        )
        self.assertEqual(profile_response.status_code, status.HTTP_200_OK, profile_response.data)

        create_response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Original title',
                'summary': 'Original summary',
                'media_type': 'text',
            },
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        entry = create_response.data.get('feed') or {}
        entry_id = entry.get('id')
        self.assertTrue(entry_id)

        broadcast_response = self.client.post(
            f'/api/v1/broadcasts/profiles/feeds/{entry_id}/broadcast/',
            {},
            format='json',
        )
        self.assertEqual(broadcast_response.status_code, status.HTTP_200_OK, broadcast_response.data)

        patch_response = self.client.patch(
            f'/api/v1/broadcasts/profiles/feeds/{entry_id}/',
            {
                'title': 'Updated title',
                'summary': 'Updated summary',
                'media_type': 'text',
            },
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK, patch_response.data)

        item = BroadcastItem.objects.get(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id=str(entry_id),
            broadcasted_by=self.user,
        )
        self.assertEqual(item.metadata.get('entry', {}).get('title'), 'Updated title')
        self.assertEqual(item.metadata.get('entry', {}).get('summary'), 'Updated summary')

    def test_broadcast_feed_entry_returns_broadcast_id_and_marks_live(self):
        create_response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Ready to broadcast',
                'summary': 'Broadcast lifecycle coverage',
                'media_type': 'text',
            },
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        entry_id = (create_response.data.get('feed') or {}).get('id')
        self.assertTrue(entry_id)

        broadcast_response = self.client.post(
            f'/api/v1/broadcasts/profiles/feeds/{entry_id}/broadcast/',
            {},
            format='json',
        )

        self.assertEqual(broadcast_response.status_code, status.HTTP_200_OK, broadcast_response.data)
        self.assertTrue(broadcast_response.data.get('broadcast_id'))
        feed = broadcast_response.data.get('feed') or {}
        self.assertTrue(feed.get('is_broadcast'))
        self.assertTrue(feed.get('broadcasted_at'))
        self.assertTrue(
            BroadcastItem.objects.filter(
                id=broadcast_response.data.get('broadcast_id'),
                source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
                source_id=str(entry_id),
                broadcasted_by=self.user,
                is_deleted=False,
            ).exists()
        )

    def test_feed_entry_create_preserves_advanced_composer_payload(self):
        rich_doc = {
            'type': 'doc',
            'content': [{'type': 'paragraph', 'text': 'Styled launch'}],
        }
        attachment_payload = {
            'url': 'https://cdn.example.com/video.mp4',
            'media_type': 'video',
            'mimeType': 'video/mp4',
            'kind': 'short_video',
        }
        response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Advanced composer',
                'summary': '',
                'media_type': 'short_video',
                'text': json.dumps(rich_doc),
                'text_plain': 'Styled launch',
                'text_preview': 'Styled launch',
                'link': 'https://example.com/launch',
                'poll': json.dumps({'question': 'Ready?', 'options': [{'id': 'yes', 'text': 'Yes'}]}),
                'event': json.dumps({'title': 'Launch live', 'startsAt': '2026-05-02T09:00:00Z'}),
                'composer_type': 'short_video',
                'attachment_payloads': json.dumps([attachment_payload]),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        feed = response.data.get('feed') or {}
        self.assertEqual(feed.get('text_plain'), 'Styled launch')
        self.assertEqual(feed.get('text_doc'), rich_doc)
        self.assertEqual(feed.get('text'), rich_doc)
        self.assertEqual(feed.get('link'), 'https://example.com/launch')
        self.assertEqual(feed.get('poll', {}).get('question'), 'Ready?')
        self.assertEqual(feed.get('event', {}).get('title'), 'Launch live')
        self.assertEqual(feed.get('composer_type'), 'short_video')
        self.assertEqual(feed.get('media_type'), 'video')
        self.assertEqual((feed.get('attachments') or [])[0].get('kind'), 'short_video')

    def test_feed_entry_rejects_unsupported_uploaded_media_type(self):
        upload = SimpleUploadedFile(
            'payload.exe',
            b'not-safe',
            content_type='application/x-msdownload',
        )

        response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Unsafe upload',
                'summary': 'Should fail',
                'media_type': 'file',
                'attachments': [upload],
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Unsupported', str(response.data))

    def test_feed_entry_rejects_unsupported_remote_attachment_payload(self):
        response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Unsafe remote',
                'summary': 'Should fail',
                'media_type': 'file',
                'attachment_payloads': json.dumps([
                    {
                        'url': 'https://cdn.example.com/payload.exe',
                        'mimeType': 'application/x-msdownload',
                        'size': 10,
                    }
                ]),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Unsupported', str(response.data))

    def test_unbroadcast_feed_entry_removes_live_item_without_deleting_queue_entry(self):
        create_response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Temporary broadcast',
                'summary': 'Will be removed from live feed',
                'media_type': 'text',
            },
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        entry_id = (create_response.data.get('feed') or {}).get('id')
        self.assertTrue(entry_id)

        broadcast_response = self.client.post(
            f'/api/v1/broadcasts/profiles/feeds/{entry_id}/broadcast/',
            {},
            format='json',
        )
        self.assertEqual(broadcast_response.status_code, status.HTTP_200_OK, broadcast_response.data)

        unbroadcast_response = self.client.delete(
            f'/api/v1/broadcasts/profiles/feeds/{entry_id}/unbroadcast/',
            {},
            format='json',
        )

        self.assertEqual(unbroadcast_response.status_code, status.HTTP_200_OK, unbroadcast_response.data)
        feed = unbroadcast_response.data.get('feed') or {}
        self.assertFalse(feed.get('is_broadcast'))
        self.assertIsNone(feed.get('broadcasted_at'))
        self.assertTrue(feed.get('unbroadcasted_at'))
        self.assertTrue(any(str(row.get('id')) == str(entry_id) for row in unbroadcast_response.data.get('feeds') or []))
        self.assertFalse(
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
                source_id=str(entry_id),
                broadcasted_by=self.user,
                is_deleted=False,
            ).exists()
        )
        self.assertTrue(
            ModerationAuditLog.objects.filter(
                action='broadcast.feed_entry.unbroadcast',
                target_id=entry_id,
            ).exists()
        )

    def test_broadcast_feed_list_deduplicates_primary_attachment(self):
        duplicate_attachment = {
            'url': 'http://10.14.20.99:8000/media/broadcast_videos/example.jpg',
            'path': 'broadcast_videos/example.jpg',
            'mime_type': 'image/jpeg',
            'media_type': 'image',
            'name': 'example.jpg',
            'size': 1,
        }
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-dedupe-1',
            broadcasted_by=self.user,
            broadcasted_at=timezone.now(),
            metadata={
                'profile_name': 'Broadcast feed',
                'entry': {
                    'id': 'feed-dedupe-1',
                    'title': 'Attachment dedupe',
                    'summary': 'Ensure primary attachment is not duplicated.',
                    'media_type': 'image',
                    'attachment': duplicate_attachment,
                    'attachments': [duplicate_attachment],
                },
            },
        )

        response = self.client.get('/api/v1/broadcasts/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        results = response.data.get('results') or []
        matched = next((row for row in results if str(row.get('id')) == str(item.id)), None)
        self.assertIsNotNone(matched)
        attachments = matched.get('attachments') or []
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get('path'), 'broadcast_videos/example.jpg')

    def test_delete_feed_attachment_syncs_existing_broadcast_snapshot(self):
        create_response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Attachment delete sync',
                'summary': 'Original summary',
                'media_type': 'image',
            },
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        entry_id = str((create_response.data.get('feed') or {}).get('id') or '')
        self.assertTrue(entry_id)

        account_profile = Profile.objects.get(user=self.user)
        feed_profile = BroadcastFeedProfile.objects.get(profile=account_profile)
        profile_payload = dict(feed_profile.payload or {})
        feeds = list(profile_payload.get('feeds') or [])
        self.assertTrue(feeds)
        feeds[0]['attachments'] = [
            {
                'url': 'http://10.14.20.99:8000/media/broadcast_videos/example-1.jpg',
                'path': 'broadcast_videos/example-1.jpg',
                'mime_type': 'image/jpeg',
                'media_type': 'image',
                'name': 'example-1.jpg',
                'size': 1,
            },
            {
                'url': 'http://10.14.20.99:8000/media/broadcast_videos/example-2.jpg',
                'path': 'broadcast_videos/example-2.jpg',
                'mime_type': 'image/jpeg',
                'media_type': 'image',
                'name': 'example-2.jpg',
                'size': 1,
            },
        ]
        feeds[0]['attachment'] = feeds[0]['attachments'][0]
        feed_profile.payload = {**profile_payload, 'feeds': feeds}
        feed_profile.save(update_fields=['payload'])

        broadcast_response = self.client.post(
            f'/api/v1/broadcasts/profiles/feeds/{entry_id}/broadcast/',
            {},
            format='json',
        )
        self.assertEqual(broadcast_response.status_code, status.HTTP_200_OK, broadcast_response.data)

        delete_response = self.client.delete(
            f'/api/v1/broadcasts/profiles/feeds/{entry_id}/attachments/?key=broadcast_videos/example-1.jpg'
        )
        self.assertEqual(delete_response.status_code, status.HTTP_200_OK, delete_response.data)

        item = BroadcastItem.objects.get(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id=entry_id,
            broadcasted_by=self.user,
        )
        attachments = (item.metadata.get('entry') or {}).get('attachments') or []
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get('path'), 'broadcast_videos/example-2.jpg')
        self.assertEqual(((item.metadata.get('entry') or {}).get('attachment') or {}).get('path'), 'broadcast_videos/example-2.jpg')

    def test_unsubscribe_partner_marks_membership_removed(self):
        conversation = Conversation.objects.create(
            type=ConversationType.CHANNEL,
            created_by=self.user,
            title='Partner updates',
        )
        partner = Partner.objects.create(
            name='KIS Partner',
            slug='kis-partner',
            owner=self.user,
            main_conversation=conversation,
        )
        PartnerJoinConfig.objects.create(partner=partner, allow_subscribe=True)
        PartnerMembership.objects.create(
            partner=partner,
            user=self.user,
            status=PartnerMembershipStatus.SUBSCRIBER,
            role='subscriber',
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=self.user,
            base_role=BaseConversationRole.READONLY,
        )

        response = self.client.post(
            '/api/v1/broadcasts/subscribe/?action=unsubscribe',
            {'target_type': 'partner', 'target_id': str(partner.id)},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        membership = PartnerMembership.objects.get(partner=partner, user=self.user)
        self.assertEqual(membership.status, PartnerMembershipStatus.REMOVED)
        conversation_member = ConversationMember.objects.get(conversation=conversation, user=self.user)
        self.assertIsNotNone(conversation_member.left_at)

    def test_unsubscribe_channel_marks_conversation_member_left(self):
        conversation = Conversation.objects.create(
            type=ConversationType.CHANNEL,
            created_by=self.user,
            title='Broadcast channel',
        )
        channel = Channel.objects.create(
            name='Broadcast channel',
            slug='broadcast-channel',
            owner=self.user,
            conversation=conversation,
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=self.user,
            base_role=BaseConversationRole.MEMBER,
        )

        response = self.client.post(
            '/api/v1/broadcasts/subscribe/?action=unsubscribe',
            {
                'target_type': 'channel',
                'target_id': str(channel.id),
                'conversation_id': str(conversation.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        conversation_member = ConversationMember.objects.get(conversation=conversation, user=self.user)
        self.assertIsNotNone(conversation_member.left_at)

    def test_unsubscribe_community_marks_membership_left(self):
        conversation = Conversation.objects.create(
            type=ConversationType.GROUP,
            created_by=self.user,
            title='Community lobby',
        )
        community = Community.objects.create(
            owner=self.user,
            name='Broadcast community',
            slug='broadcast-community',
            main_conversation=conversation,
        )
        CommunityMembership.objects.create(
            community=community,
            user=self.user,
            role=CommunityRole.MEMBER,
        )

        response = self.client.post(
            '/api/v1/broadcasts/subscribe/?action=unsubscribe',
            {'target_type': 'community', 'target_id': str(community.id)},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        membership = CommunityMembership.objects.get(community=community, user=self.user)
        self.assertIsNotNone(membership.left_at)

    def test_feed_list_supports_search_and_pagination(self):
        first = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-search-1',
            broadcasted_by=self.user,
            broadcasted_at=timezone.now(),
            metadata={'entry': {'id': 'feed-search-1', 'title': 'Alpha release notes', 'summary': 'Searchable entry'}},
        )
        second = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-search-2',
            broadcasted_by=self.user,
            broadcasted_at=timezone.now(),
            metadata={'entry': {'id': 'feed-search-2', 'title': 'Beta update', 'summary': 'Another entry'}},
        )
        third = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
            source_id='feed-search-3',
            broadcasted_by=self.user,
            broadcasted_at=timezone.now(),
            metadata={'entry': {'id': 'feed-search-3', 'title': 'Gamma patch', 'summary': 'Final entry'}},
        )

        search_response = self.client.get('/api/v1/broadcasts/?q=alpha')
        self.assertEqual(search_response.status_code, status.HTTP_200_OK, search_response.data)
        self.assertEqual(search_response.data.get('count'), 1)
        search_results = search_response.data.get('results') or []
        self.assertEqual(len(search_results), 1)
        self.assertEqual(str(search_results[0].get('id')), str(first.id))

        page_one = self.client.get('/api/v1/broadcasts/?limit=2&offset=0&code=broadcast_feed_entry')
        self.assertEqual(page_one.status_code, status.HTTP_200_OK, page_one.data)
        self.assertGreaterEqual(page_one.data.get('count') or 0, 3)
        self.assertEqual(len(page_one.data.get('results') or []), 2)
        self.assertTrue(page_one.data.get('next'))
        self.assertIsNone(page_one.data.get('previous'))

        page_two = self.client.get('/api/v1/broadcasts/?limit=2&offset=2&code=broadcast_feed_entry')
        self.assertEqual(page_two.status_code, status.HTTP_200_OK, page_two.data)
        self.assertGreaterEqual(page_two.data.get('count') or 0, 3)
        self.assertGreaterEqual(len(page_two.data.get('results') or []), 1)
        self.assertTrue(page_two.data.get('previous'))

        collected_ids = {
            str(row.get('id'))
            for row in (page_one.data.get('results') or []) + (page_two.data.get('results') or [])
        }
        self.assertTrue({str(first.id), str(second.id), str(third.id)}.issubset(collected_ids))


class EducationInstitutionFormNormalizationTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone="5553034040",
            username="education_form_user",
            password="secret",
            country="NG",
        )
        # EducationInstitutionListView.post correctly enforces a real,
        # intentional Business-Pro-and-up paywall via _resolve_profile_limit
        # (see apps.accounts.tier_presets: "education_profiles" is 0 on
        # lower tiers) — these tests predate that gate and need a
        # qualifying tier to exercise institution creation itself rather
        # than being blocked by the (correct) paywall.
        from apps.accounts.tiers import ensure_default_account_tiers

        ensure_default_account_tiers()
        self.user.tier = "Business Pro"
        self.user.save(update_fields=["tier"])
        self.client.force_authenticate(user=self.user)

    def test_direct_owner_without_membership_can_manage_education_institution(self):
        institution = EducationInstitution.objects.create(
            owner=self.user,
            name="Direct Owner Academy",
            institution_type="academy",
            membership_policy="application",
        )

        detail_response = self.client.get(f"/api/v1/broadcasts/education/institutions/{institution.id}/")
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK, detail_response.data)
        detail = detail_response.data["institution"]
        self.assertEqual(detail["owner_user_id"], str(self.user.id))
        self.assertEqual(detail["current_membership"]["role"], "owner")
        self.assertTrue(detail["can_manage"])

        course_response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution.id}/courses/",
            {"title": "Owner Launch Course"},
            format="json",
        )
        self.assertEqual(course_response.status_code, status.HTTP_201_CREATED, course_response.data)

    def test_manager_can_start_education_verification_with_safe_metadata(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Verification Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution_id = create_response.data["institution"]["id"]

        response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/verification/start/",
            {
                "provider": "sumsub",
                "evidence_metadata": {
                    "legal_registration": [{"private_media_id": "private-registration", "url": "https://example.com/reg.pdf"}],
                    "accreditation": [{"private_media_id": "private-accreditation", "expires_at": "2028-12-31"}],
                    "certificate_issuer_trust": [{"private_media_id": "private-issuer-proof"}],
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        case = VerificationCase.objects.get(id=response.data["case"]["id"])
        self.assertEqual(case.subject.subject_type, VerificationSubjectType.EDUCATION_INSTITUTION)
        self.assertEqual(case.evidence_metadata["legal_registration"][0]["private_media_id"], "private-registration")
        self.assertNotIn("url", case.evidence_metadata["legal_registration"][0])

    def test_education_verification_start_rejects_raw_document_payload(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Raw Payload Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution_id = create_response.data["institution"]["id"]

        response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/verification/start/",
            {"evidence_metadata": {"accreditation": [{"document_base64": "data:image/png;base64,abc123"}]}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_can_approve_education_case_and_issue_badges(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Accredited Academy"},
            format="json",
        )
        institution = EducationInstitution.objects.get(id=create_response.data["institution"]["id"])
        case = start_education_institution_verification_case(
            institution=institution,
            actor=self.user,
            evidence_metadata={"accreditation": [{"private_media_id": "private-accreditation"}]},
        )
        User = get_user_model()
        staff = User.objects.create_user(phone="5553034999", username="education_staff", password="secret", country="NG")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        self.client.force_authenticate(user=staff)

        response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution.id}/verification/cases/{case.id}/review/",
            {"action": "approve"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        codes = set(
            VerificationBadge.objects.filter(
                subject__subject_type=VerificationSubjectType.EDUCATION_INSTITUTION,
                subject__subject_id=institution.id,
            ).values_list("code", flat=True)
        )
        self.assertIn(VerificationBadgeCode.VERIFIED_EDUCATION_INSTITUTION, codes)
        self.assertIn(VerificationBadgeCode.ACCREDITED_EDUCATION, codes)
        self.assertTrue(current_education_institution_verification_status(institution)["verified"])

    def test_education_serializer_exposes_verification_summary(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Summary Academy"},
            format="json",
        )
        institution_id = create_response.data["institution"]["id"]
        institution = EducationInstitution.objects.get(id=institution_id)
        case = start_education_institution_verification_case(institution=institution, actor=self.user, evidence_metadata={})
        review_education_institution_case(case=case, actor=self.user, action="approve")

        response = self.client.get(f"/api/v1/broadcasts/education/institutions/{institution_id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data["institution"]["verification_summary"]["verified"])

    def test_institution_image_aliases_are_saved_and_returned(self):
        with override_settings(API_BASE_URL="http://10.112.162.99:8000", SITE_URL="http://10.112.162.99:8000"):
            response = self.client.post(
                "/api/v1/broadcasts/education/institutions/",
                {
                    "name": "Image Academy",
                    "description": "Institution with direct image aliases.",
                    "imageUrl": "http://10.112.162.99:8000/media/institutions/institution.jpg",
                    "logoUrl": "http://10.112.162.99:8000/media/institutions/logo.jpg",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        institution = response.data.get("institution") or {}
        self.assertEqual(institution.get("image_url"), "http://10.112.162.99:8000/media/institutions/institution.jpg")
        self.assertEqual(institution.get("logo_url"), "http://10.112.162.99:8000/media/institutions/logo.jpg")
        branding = institution.get("branding") or {}
        self.assertEqual(branding.get("image_url"), "http://10.112.162.99:8000/media/institutions/institution.jpg")
        self.assertEqual(branding.get("logo_url"), "http://10.112.162.99:8000/media/institutions/logo.jpg")
        stored = EducationInstitution.objects.get(id=institution["id"])
        self.assertEqual(stored.branding.get("image_url"), "/media/institutions/institution.jpg")
        self.assertEqual(stored.branding.get("logo_url"), "/media/institutions/logo.jpg")

    def test_education_broadcast_price_accepts_formatted_decimal(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Pricing Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution_id = create_response.data["institution"]["id"]

        with override_settings(API_BASE_URL="http://10.112.162.99:8000", SITE_URL="http://10.112.162.99:8000"):
            response = self.client.post(
                f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/",
                {
                    "broadcast_kind": "institution_notice",
                    "title": "Paid orientation",
                    "priceAmount": "KISC 1,200.50",
                    "coverImageUrl": "http://10.112.162.99:8000/media/education/orientation.jpg",
                    "booking_enabled": True,
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        broadcast = EducationInstitutionBroadcast.objects.get(id=response.data["broadcast"]["id"])
        self.assertEqual(str(broadcast.price_amount), "1200.50")
        self.assertEqual(broadcast.cover_image_url, "/media/education/orientation.jpg")
        self.assertEqual(
            response.data["broadcast"]["cover_image_url"],
            "http://10.112.162.99:8000/media/education/orientation.jpg",
        )

    @override_settings(FLW_WEBHOOK_SECRET="test-webhook-secret")
    def test_education_paid_booking_defaults_to_usd_provider_pending(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "USD Provider Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution_id = create_response.data["institution"]["id"]
        broadcast_response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/",
            {
                "broadcast_kind": "institution_notice",
                "title": "Paid USD orientation",
                "status": "published",
                "priceAmount": "25.00",
                "booking_enabled": True,
            },
            format="json",
        )
        self.assertEqual(broadcast_response.status_code, status.HTTP_201_CREATED, broadcast_response.data)
        broadcast_id = broadcast_response.data["broadcast"]["id"]

        booking_response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/{broadcast_id}/bookings/",
            {"seat_count": 1},
            format="json",
        )
        self.assertEqual(booking_response.status_code, status.HTTP_201_CREATED, booking_response.data)
        booking = booking_response.data["booking"]
        self.assertEqual(booking["amount_cents"], 2500)
        self.assertEqual(booking["currency"], "USD")
        self.assertTrue(booking["payment_required"])

        payment_response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/{broadcast_id}/bookings/{booking['id']}/pay/",
            {},
            format="json",
        )
        self.assertEqual(payment_response.status_code, status.HTTP_200_OK, payment_response.data)
        paid_booking = payment_response.data["booking"]
        self.assertEqual(paid_booking["status"], "payment_pending")
        self.assertEqual(paid_booking["currency"], "USD")
        self.assertEqual(paid_booking["payment_provider"], "flutterwave")
        self.assertEqual(paid_booking["payment_status"], "pending")
        self.assertIsNotNone(paid_booking["payment_intent_id"])
        self.assertIsNone(paid_booking["wallet_transaction_id"])

        intent = DirectPaymentIntent.objects.get(id=paid_booking["payment_intent_id"])
        self.assertEqual(intent.target_type, DirectPaymentIntent.TARGET_EDUCATION_BOOKING)
        self.assertEqual(intent.amount_cents, 2500)

        ok, result, _intent = reconcile_direct_payment_callback(
            payload={"data": {"tx_ref": intent.tx_ref, "status": "successful", "id": "flw-education-001"}},
            signature="test-webhook-secret",
        )
        self.assertTrue(ok)
        self.assertEqual(result, "paid")
        from apps.broadcasts.models import EducationInstitutionBooking, EducationBookingStatus

        refreshed = EducationInstitutionBooking.objects.get(id=booking["id"])
        self.assertEqual(str(refreshed.metadata.get("payment_status")), "paid")
        self.assertEqual(refreshed.status, EducationBookingStatus.CONFIRMED)
        self.assertEqual(refreshed.payment_method, "flutterwave")
        self.assertEqual(refreshed.currency, "USD")

    def test_education_wallet_checkout_is_disabled_by_default(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Wallet Block Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution_id = create_response.data["institution"]["id"]
        broadcast_response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/",
            {
                "broadcast_kind": "institution_notice",
                "title": "Paid blocked orientation",
                "status": "published",
                "priceAmount": "10.00",
                "booking_enabled": True,
            },
            format="json",
        )
        self.assertEqual(broadcast_response.status_code, status.HTTP_201_CREATED, broadcast_response.data)
        broadcast_id = broadcast_response.data["broadcast"]["id"]
        booking_response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/{broadcast_id}/bookings/",
            {},
            format="json",
        )
        self.assertEqual(booking_response.status_code, status.HTTP_201_CREATED, booking_response.data)

        payment_response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/{broadcast_id}/bookings/{booking_response.data['booking']['id']}/pay/",
            {"payment_method": "wallet"},
            format="json",
        )

        self.assertEqual(payment_response.status_code, status.HTTP_403_FORBIDDEN, payment_response.data)
        self.assertEqual(payment_response.data["code"], "legacy_education_wallet_checkout_disabled")

    def test_education_broadcast_price_accepts_nested_price_object(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Nested Pricing Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution_id = create_response.data["institution"]["id"]

        response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/",
            {
                "broadcast_kind": "institution_notice",
                "title": "Nested paid orientation",
                "price": {"amount": "KISC 1,200.50", "currency": "KISC"},
                "booking_enabled": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        broadcast = EducationInstitutionBroadcast.objects.get(id=response.data["broadcast"]["id"])
        self.assertEqual(str(broadcast.price_amount), "1200.50")

    def test_education_broadcast_free_price_does_not_fail_decimal_validation(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Free Pricing Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution_id = create_response.data["institution"]["id"]

        response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/",
            {
                "broadcast_kind": "institution_notice",
                "title": "Free orientation",
                "price": "free",
                "booking_enabled": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        broadcast = EducationInstitutionBroadcast.objects.get(id=response.data["broadcast"]["id"])
        self.assertIsNone(broadcast.price_amount)

    def test_education_broadcast_patch_ignores_javascript_object_price_string(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Patch Pricing Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution_id = create_response.data["institution"]["id"]
        broadcast_response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/",
            {
                "broadcast_kind": "institution_notice",
                "title": "Patch price event",
                "priceAmount": "25.00",
            },
            format="json",
        )
        self.assertEqual(broadcast_response.status_code, status.HTTP_201_CREATED, broadcast_response.data)
        broadcast_id = broadcast_response.data["broadcast"]["id"]

        response = self.client.patch(
            f"/api/v1/broadcasts/education/institutions/{institution_id}/broadcasts/{broadcast_id}/",
            {"price": "[object Object]"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        broadcast = EducationInstitutionBroadcast.objects.get(id=broadcast_id)
        self.assertIsNone(broadcast.price_amount)

    def test_event_broadcast_accepts_camel_case_event_payload(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Event Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution = EducationInstitution.objects.get(id=create_response.data["institution"]["id"])
        event = EducationInstitutionEvent.objects.create(
            institution=institution,
            title="Campus open day",
            summary="Meet the faculty.",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
            event_type="event",
            status="published",
        )

        response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution.id}/broadcasts/",
            {
                "broadcastKind": "event",
                "eventId": str(event.id),
                "title": "Campus open day broadcast",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        broadcast = EducationInstitutionBroadcast.objects.get(id=response.data["broadcast"]["id"])
        self.assertEqual(broadcast.broadcast_kind, "event")
        self.assertEqual(broadcast.event_id, event.id)

    def test_event_broadcast_infers_kind_from_nested_target(self):
        create_response = self.client.post(
            "/api/v1/broadcasts/education/institutions/",
            {"name": "Nested Event Academy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        institution = EducationInstitution.objects.get(id=create_response.data["institution"]["id"])
        event = EducationInstitutionEvent.objects.create(
            institution=institution,
            title="Nested open day",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
            event_type="event",
            status="published",
        )

        response = self.client.post(
            f"/api/v1/broadcasts/education/institutions/{institution.id}/broadcasts/",
            {
                "target": {"eventId": str(event.id)},
                "type": "online",
                "title": "Nested event broadcast",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        broadcast = EducationInstitutionBroadcast.objects.get(id=response.data["broadcast"]["id"])
        self.assertEqual(broadcast.broadcast_kind, "event")
        self.assertEqual(broadcast.event_id, event.id)


class BroadcastVideoContractTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            phone='5551013030',
            username='broadcast_video_user',
            password='secret',
            country='NG',
        )
        self.client.force_authenticate(user=self.user)
        self.temp_media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_media_dir.cleanup)

    @override_settings(MEDIA_ROOT=None)
    def test_feed_entry_create_shapes_video_attachment_with_stream_contract(self):
        with override_settings(MEDIA_ROOT=self.temp_media_dir.name):
            upload = SimpleUploadedFile('clip.mp4', b'fake-video-bytes', content_type='video/mp4')
            with patch('apps.broadcasts.views._probe_video_duration', return_value=12.4):
                response = self.client.post(
                    '/api/v1/broadcasts/profiles/feeds/',
                    {
                        'title': 'Video feed post',
                        'summary': 'Video summary',
                        'media_type': 'video',
                        'attachments': [upload],
                    },
                )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        feed = response.data.get('feed') or {}
        attachments = feed.get('attachments') or []
        self.assertEqual(len(attachments), 1)
        attachment = attachments[0]
        self.assertEqual(attachment.get('media_type'), 'video')
        self.assertEqual(attachment.get('mime_type'), 'video/mp4')
        self.assertTrue(attachment.get('url'))
        self.assertTrue(attachment.get('stream_url'))
        self.assertTrue(attachment.get('video_id'))
        self.assertIn('/api/v1/broadcasts/videos/', attachment.get('stream_url'))
        self.assertTrue(
            BroadcastVideo.objects.filter(id=attachment.get('video_id'), creator=self.user).exists()
        )

    @override_settings(MEDIA_ROOT=None)
    def test_feed_entry_create_uses_public_api_base_when_request_host_is_loopback(self):
        with override_settings(
            MEDIA_ROOT=self.temp_media_dir.name,
            API_BASE_URL='http://192.168.110.62:8000',
            SITE_URL='http://192.168.110.62:8000',
            # Explicit, not relying on ambient OBJECT_STORAGE_PROVIDER (a
            # local .env set to "s3" for manual dev testing switches
            # settings.STORAGES to S3MediaStorage) — build_media_url only
            # does the loopback/public-host URL rewrite this test checks
            # for under FileSystemStorage; a remote backend short-circuits
            # to default_storage.url() (a real, signed bucket URL) instead.
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            },
        ):
            upload = SimpleUploadedFile('clip.mp4', b'fake-video-bytes', content_type='video/mp4')
            with patch('apps.broadcasts.views._probe_video_duration', return_value=12.4):
                response = self.client.post(
                    '/api/v1/broadcasts/profiles/feeds/',
                    {
                        'title': 'Video feed post',
                        'summary': 'Video summary',
                        'media_type': 'video',
                        'attachments': [upload],
                    },
                    HTTP_HOST='10.14.20.99:8000',
                )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        attachment = ((response.data.get('feed') or {}).get('attachments') or [None])[0] or {}
        self.assertTrue(attachment.get('url', '').startswith('http://192.168.110.62:8000/media/'))
        self.assertTrue(
            attachment.get('stream_url', '').startswith('http://192.168.110.62:8000/api/v1/broadcasts/videos/')
        )
        video = BroadcastVideo.objects.get(id=attachment.get('video_id'))
        self.assertTrue(video.video_url.startswith('http://192.168.110.62:8000/media/'))

    @override_settings(MEDIA_ROOT=None)
    def test_video_stream_endpoint_supports_inline_and_range_requests(self):
        # This test exercises the local-disk fallback path specifically, so
        # force FileSystemStorage regardless of OBJECT_STORAGE_PROVIDER in
        # the environment (e.g. local dev with real S3 creds configured) —
        # otherwise default_storage would stay S3-backed and the raw file
        # this test drops straight into temp_media_dir would never be found.
        with override_settings(
            MEDIA_ROOT=self.temp_media_dir.name,
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            },
        ):
            rel_path = 'broadcast_videos/test-video.mp4'
            absolute_dir = f'{self.temp_media_dir.name}/broadcast_videos'
            absolute_path = f'{absolute_dir}/test-video.mp4'
            import os
            os.makedirs(absolute_dir, exist_ok=True)
            with open(absolute_path, 'wb') as handle:
                handle.write(b'0123456789abcdef')

            video = BroadcastVideo.objects.create(
                title='Streamable clip',
                description='',
                creator=self.user,
                video_url='http://testserver/media/broadcast_videos/test-video.mp4',
                thumbnail_url='',
                mime_type='video/mp4',
                storage_path=rel_path,
                type='video',
                duration_seconds=16,
            )

            full_response = self.client.get(f'/api/v1/broadcasts/videos/{video.id}/stream/')
            self.assertEqual(full_response.status_code, status.HTTP_200_OK)
            self.assertEqual(full_response['Accept-Ranges'], 'bytes')
            self.assertEqual(full_response['Content-Type'], 'video/mp4')
            self.assertIn('inline;', full_response['Content-Disposition'])
            self.assertTrue(full_response['X-Video-URL'].endswith('/media/broadcast_videos/test-video.mp4'))

            range_response = self.client.get(
                f'/api/v1/broadcasts/videos/{video.id}/stream/',
                HTTP_RANGE='bytes=0-3',
            )
            self.assertEqual(range_response.status_code, status.HTTP_206_PARTIAL_CONTENT)
            self.assertEqual(range_response['Accept-Ranges'], 'bytes')
            self.assertEqual(range_response['Content-Range'], 'bytes 0-3/16')
            self.assertEqual(range_response.content, b'0123')

    def test_video_stream_endpoint_redirects_to_remote_storage_url(self):
        # When default_storage is a remote backend (S3/Supabase), the stream
        # view must hand back a redirect to storage.url(...) instead of
        # trying (and failing) to read a local MEDIA_ROOT path — this is the
        # behavior that broke before: uploads went to S3 but playback always
        # read local disk regardless of OBJECT_STORAGE_PROVIDER.
        video = BroadcastVideo.objects.create(
            title='Remote clip',
            description='',
            creator=self.user,
            video_url='https://bucket.s3.amazonaws.com/broadcast_videos/remote.mp4',
            thumbnail_url='',
            mime_type='video/mp4',
            storage_path='broadcast_videos/remote.mp4',
            type='video',
            duration_seconds=16,
        )

        with patch('apps.broadcasts.views.default_storage') as mock_storage:
            mock_storage.exists.return_value = True
            mock_storage.url.return_value = 'https://bucket.s3.amazonaws.com/broadcast_videos/remote.mp4?sig=abc'
            # Empty tuple as the isinstance() classinfo makes the check
            # always False, standing in for "storage isn't FileSystemStorage".
            with patch('apps.broadcasts.views.FileSystemStorage', ()):
                response = self.client.get(f'/api/v1/broadcasts/videos/{video.id}/stream/')

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(response['Location'], 'https://bucket.s3.amazonaws.com/broadcast_videos/remote.mp4?sig=abc')
        mock_storage.exists.assert_called_once_with('broadcast_videos/remote.mp4')
        mock_storage.url.assert_called_once_with('broadcast_videos/remote.mp4')

    @override_settings(
        API_BASE_URL='http://10.14.20.99:8000',
        SITE_URL='http://10.14.20.99:8000',
        ALLOWED_HOSTS=['testserver', '127.0.0.1'],
        # See the comment in test_feed_entry_create_uses_public_api_base_when_request_host_is_loopback above.
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
    )
    def test_feed_entries_get_normalizes_stored_loopback_image_attachment_urls(self):
        response = self.client.post(
            '/api/v1/broadcasts/profiles/feeds/',
            {
                'title': 'Image feed post',
                'summary': 'Image summary',
                'media_type': 'image',
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        feed = response.data.get('feed') or {}
        entry_id = feed.get('id')

        account_profile = Profile.objects.get(user=self.user)
        feed_profile = BroadcastFeedProfile.objects.get(profile=account_profile)
        profile_payload = dict(feed_profile.payload or {})
        feeds = list(profile_payload.get('feeds') or [])
        self.assertTrue(feeds)
        feeds[0]['attachments'] = [
            {
                'url': 'http://127.0.0.1:8000/media/broadcast_videos/example.jpg',
                'path': 'broadcast_videos/example.jpg',
                'mime_type': 'image/jpeg',
                'media_type': 'image',
                'name': 'example.jpg',
                'size': 1,
            }
        ]
        feeds[0]['attachment'] = feeds[0]['attachments'][0]
        feed_profile.payload = {**profile_payload, 'feeds': feeds}
        feed_profile.save(update_fields=['payload'])

        get_response = self.client.get(
            f'/api/v1/broadcasts/profiles/feeds/{entry_id}/',
            HTTP_HOST='127.0.0.1:8000',
        )
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        normalized = (get_response.data.get('feed') or {}).get('attachment') or {}
        self.assertEqual(normalized.get('url'), 'http://10.14.20.99:8000/media/broadcast_videos/example.jpg')


class EducationCourseraCoreTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5558800001',
            username='education_owner',
            password='secret',
            country='NG',
        )
        self.learner = User.objects.create_user(
            phone='5558800002',
            username='education_learner',
            password='secret',
            country='NG',
        )
        self.institution = EducationInstitution.objects.create(
            owner=self.owner,
            name='Royal Academy',
            description='Structured learning for families.',
            payout_account_status=EducationInstitutionPayoutAccountStatus.ACTIVE,
            flutterwave_subaccount_id='RS_TEST_EDU',
        )
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution,
            title='Foundations of Service',
            summary='A practical course.',
            status='published',
            metadata={'certificate_enabled': True},
        )
        self.broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution,
            created_by=self.owner,
            broadcast_kind='course',
            course=self.course,
            title='Foundations of Service',
            summary='Learn with excellence.',
            description='A complete course detail.',
            booking_enabled=True,
            price_amount='15.00',
            price_currency='USD',
            status='published',
        )

    def test_verify_education_launch_command_passes_safe_local_defaults(self):
        output = StringIO()

        call_command('verify_education_launch', stdout=output)

        rendered = output.getvalue()
        self.assertIn('Education launch guardrails ready: True', rendered)
        self.assertIn('PASS: KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED - disabled', rendered)
        self.assertIn('PASS: KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER - flutterwave', rendered)
        self.assertIn('PASS: MEDIA_SAFETY_ENABLED', rendered)

    def test_discovery_exposes_coursera_style_trust_payment_and_safety_summaries(self):
        self.client.force_authenticate(self.learner)
        response = self.client.get('/api/v1/education/discovery/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        item = response.data['sections'][0]['items'][0]
        self.assertEqual(item['paymentSummary']['currency'], 'USD')
        self.assertTrue(item['paymentSummary']['providerRequired'])
        self.assertTrue(item['paymentSummary']['legacyWalletDisabled'])
        self.assertIn('reviewSummary', item)
        self.assertIn('trustSummary', item)
        self.assertEqual(item['safetySummary']['status'], 'allowed')

    def test_discovery_institution_id_filter_returns_only_that_institutions_courses(self):
        other_institution = EducationInstitution.objects.create(
            owner=self.owner,
            name='Other Academy',
            description='A different institution entirely.',
        )
        other_course = EducationInstitutionCourse.objects.create(
            institution=other_institution,
            title='Unrelated Course',
            status='published',
        )
        other_broadcast = EducationInstitutionBroadcast.objects.create(
            institution=other_institution,
            created_by=self.owner,
            broadcast_kind='course',
            course=other_course,
            title='Unrelated Course',
            summary='Should not show up when filtering by self.institution.',
            status='published',
        )

        self.client.force_authenticate(self.learner)
        response = self.client.get(
            '/api/v1/education/discovery/',
            {'institution_id': str(self.institution.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        all_ids = [
            item['id']
            for section in response.data['sections']
            for item in section['items']
        ]
        self.assertIn(str(self.broadcast.id), all_ids)
        self.assertNotIn(str(other_broadcast.id), all_ids)
        for section in response.data['sections']:
            for item in section['items']:
                self.assertEqual(item['partnerId'], str(self.institution.id))

    def test_enrolled_learner_can_create_review_and_question(self):
        EducationInstitutionEnrollment.objects.create(
            institution=self.institution,
            broadcast=self.broadcast,
            user=self.learner,
            course=self.course,
            status='enrolled',
        )
        self.client.force_authenticate(self.learner)

        review_response = self.client.post(
            f'/api/v1/education/contents/{self.broadcast.id}/reviews/',
            {'rating': 5, 'comment': 'Excellent learning path.'},
            format='json',
        )
        self.assertEqual(review_response.status_code, status.HTTP_201_CREATED, review_response.data)
        self.assertEqual(review_response.data['summary']['reviewCount'], 1)
        self.assertTrue(
            EducationCourseReview.objects.filter(broadcast=self.broadcast, user=self.learner).exists()
        )

        question_response = self.client.post(
            f'/api/v1/education/contents/{self.broadcast.id}/questions/',
            {'question': 'How do I prepare for the final certificate?'},
            format='json',
        )
        self.assertEqual(question_response.status_code, status.HTTP_201_CREATED, question_response.data)
        self.assertEqual(question_response.data['summary']['questionCount'], 1)
        self.assertTrue(
            EducationCourseQuestion.objects.filter(broadcast=self.broadcast, user=self.learner).exists()
        )

    def test_non_enrolled_learner_cannot_post_review(self):
        self.client.force_authenticate(self.learner)
        response = self.client.post(
            f'/api/v1/education/contents/{self.broadcast.id}/reviews/',
            {'rating': 5},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_paid_content_enrollment_uses_usd_direct_payment_without_wallet(self):
        EducationInstitutionMembership.objects.create(
            institution=self.institution,
            user=self.learner,
            role=EducationInstitutionMembershipRole.STUDENT,
            status=EducationInstitutionMembershipStatus.ACTIVE,
            decided_by=self.owner,
            decided_at=timezone.now(),
        )
        self.client.force_authenticate(self.learner)

        response = self.client.post(
            f'/api/v1/education/contents/{self.broadcast.id}/enroll/',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        booking = response.data['booking']
        self.assertEqual(booking['currency'], 'USD')
        self.assertEqual(booking['payment_provider'], 'flutterwave')
        self.assertEqual(booking['payment_status'], 'pending')
        self.assertTrue(booking['payment_required'])
        self.assertIsNone(booking['wallet_transaction_id'])
        self.assertIsNotNone(booking['payment_intent_id'])
        intent = DirectPaymentIntent.objects.get(id=booking['payment_intent_id'])
        self.assertEqual(intent.target_type, DirectPaymentIntent.TARGET_EDUCATION_BOOKING)
        self.assertEqual(booking['direct_payment_intent_id'], booking['payment_intent_id'])
        self.assertIn('payment_reference', booking)

    def _create_module_material_item(self):
        module = EducationInstitutionCourseModule.objects.create(
            institution=self.institution,
            course=self.course,
            title='Module One',
            status='published',
        )
        material = EducationInstitutionMaterial.objects.create(
            institution=self.institution,
            course=self.course,
            title='Secret Workbook',
            kind='document',
            resource_url='https://cdn.example.com/private/secret-workbook.pdf',
            status='published',
        )
        EducationInstitutionCourseModuleItem.objects.create(
            institution=self.institution,
            course=self.course,
            module=module,
            item_type=EducationCourseModuleItemType.MATERIAL,
            material=material,
        )
        return module, material

    @override_settings(FLW_WEBHOOK_SECRET="test-webhook-secret")
    def test_paid_course_hides_material_content_until_payment_confirmed(self):
        # Regression test: EducationContentEnrollmentView used to grant
        # ENROLLED (and therefore has_learning_access) the instant a
        # payment intent was created, before the buyer had actually paid -
        # see the paired backend fix that made this test pass. Module/item
        # *titles* must stay visible throughout (so a shopper can see what
        # the course covers) - only the actual content (resource_url etc.)
        # must stay hidden until the booking is confirmed.
        EducationInstitutionMembership.objects.create(
            institution=self.institution,
            user=self.learner,
            role=EducationInstitutionMembershipRole.STUDENT,
            status=EducationInstitutionMembershipStatus.ACTIVE,
            decided_by=self.owner,
            decided_at=timezone.now(),
        )
        module, material = self._create_module_material_item()
        self.client.force_authenticate(self.learner)

        enroll_response = self.client.post(
            f'/api/v1/education/contents/{self.broadcast.id}/enroll/',
            {},
            format='json',
        )
        self.assertEqual(enroll_response.status_code, status.HTTP_200_OK, enroll_response.data)
        self.assertEqual(enroll_response.data['enrollment']['status'], 'pending')

        enrollment = EducationInstitutionEnrollment.objects.get(
            broadcast=self.broadcast, user=self.learner,
        )
        self.assertEqual(enrollment.status, EducationEnrollmentStatus.PENDING)

        # The enrollment response itself must not leak the first item's
        # content even though it's not returned as part of "courseOutline".
        progress = enroll_response.data.get('progress') or {}
        current_item = progress.get('currentItem') or {}
        if current_item:
            self.assertEqual((current_item.get('content') or {}).get('resource_url'), '')

        detail_response = self.client.get(f'/api/v1/education/contents/{self.broadcast.id}/')
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK, detail_response.data)
        self.assertFalse(detail_response.data['content']['viewerState'].get('has_learning_access'))
        outline = detail_response.data['content']['courseOutline']
        module_titles = [row['title'] for row in outline]
        self.assertIn('Module One', module_titles)
        found_item = next(
            item
            for row in outline
            for item in row['items']
            if item['title'] == 'Secret Workbook'
        )
        self.assertEqual(found_item['content'].get('resource_url'), '')
        self.assertFalse(found_item['content'].get('is_downloadable'))

        item_action_response = self.client.post(
            f'/api/v1/education/contents/{self.broadcast.id}/items/{found_item["id"]}/action/',
            {'action': 'mark_attended'},
            format='json',
        )
        self.assertEqual(item_action_response.status_code, status.HTTP_404_NOT_FOUND)

        # Now simulate the payment webhook confirming the booking.
        from apps.broadcasts.models import EducationBookingStatus

        booking = EducationInstitutionBooking.objects.get(broadcast=self.broadcast, user=self.learner)
        intent = DirectPaymentIntent.objects.get(id=enroll_response.data['booking']['payment_intent_id'])
        ok, result, _intent = reconcile_direct_payment_callback(
            payload={'data': {'tx_ref': intent.tx_ref, 'status': 'successful', 'id': 'flw-test-ref'}},
            signature='test-webhook-secret',
        )
        self.assertTrue(ok, result)

        booking.refresh_from_db()
        self.assertEqual(booking.status, EducationBookingStatus.CONFIRMED)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, EducationEnrollmentStatus.ENROLLED)

        unlocked_response = self.client.get(f'/api/v1/education/contents/{self.broadcast.id}/')
        self.assertTrue(unlocked_response.data['content']['viewerState'].get('has_learning_access'))
        unlocked_material = next(
            item
            for row in unlocked_response.data['content']['courseOutline']
            for item in row['items']
            if item['title'] == 'Secret Workbook'
        )
        self.assertEqual(
            unlocked_material['content'].get('resource_url'),
            material.resource_url,
        )

    def test_education_material_rejects_local_file_paths_and_raw_storage_paths(self):
        self.client.force_authenticate(self.owner)
        url = f'/api/v1/broadcasts/education/institutions/{self.institution.id}/materials/'

        local_response = self.client.post(
            url,
            {'title': 'Local file', 'resource_url': 'file:///private/tmp/course.pdf'},
            format='json',
        )
        self.assertEqual(local_response.status_code, status.HTTP_400_BAD_REQUEST)

        storage_response = self.client.post(
            url,
            {'title': 'Raw path', 'resource_url': 'https://cdn.example.com/course.pdf', 'storage_path': 'private/raw/course.pdf'},
            format='json',
        )
        self.assertEqual(storage_response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("apps.media.storage_backends.S3MediaStorage._client")
    def test_education_material_preserves_safe_private_media_reference_without_exposing_storage_path(self, mock_client):
        # Phase 3 (direct-to-S3): resource_attachment now carries a
        # media_id resolved against a real, owned, confirmed
        # MediaUploadIntent — not a client-asserted url/scan_status, which
        # a forged request could previously set to anything (see git
        # history for the pre-Phase-3 version of this test).
        from apps.media.tests import _mock_s3_client

        client = _mock_s3_client()
        client.head_object.return_value = {"ContentLength": 1000, "ContentType": "application/pdf"}
        mock_client.return_value = client

        self.client.force_authenticate(self.owner)
        initiate = self.client.post(
            '/api/v1/broadcasts/education/uploads/initiate/',
            {
                'institutionId': str(self.institution.id),
                'context': 'education_material',
                'filename': 'workbook.pdf',
                'contentType': 'application/pdf',
                'sizeBytes': 1000,
            },
            format='json',
        )
        self.assertEqual(initiate.status_code, status.HTTP_201_CREATED, initiate.data)
        confirm = self.client.post(f"/api/v1/media/uploads/{initiate.data['uploadId']}/confirm/", {}, format='json')
        self.assertEqual(confirm.status_code, status.HTTP_200_OK, confirm.data)
        media_id = confirm.data['mediaId']

        url = f'/api/v1/broadcasts/education/institutions/{self.institution.id}/materials/'
        response = self.client.post(
            url,
            {
                'title': 'Workbook',
                'kind': 'document',
                'course_ids': [str(self.course.id)],
                'resource_attachment': {'media_id': media_id, 'name': 'workbook.pdf'},
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        material = response.data['material']
        self.assertEqual(material['private_media_ref'], media_id)
        self.assertEqual(material['storage_path'], '')
        self.assertTrue(material['safe_resource_url'])
        self.assertNotIn('https://media.example.com', material['safe_resource_url'])
        intent = MediaUploadIntent.objects.get(id=media_id)
        self.assertIsNotNone(intent.attached_at)


# ---------------------------------------------------------------------
# Phase 2 (Education system production-hardening project): concurrency
# safety for enrollment, proven with real threads + APITransactionTestCase
# (TestCase wraps everything in one rolled-back transaction, which would
# mask the exact race these tests exist to catch).
# ---------------------------------------------------------------------

class EducationEnrollmentConcurrencyTests(APITransactionTestCase):
    # Scoped to just the apps these tests touch. Without this,
    # TransactionTestCase's post-test flush TRUNCATEs every table in the
    # database in one statement and fails with "cannot truncate a table
    # referenced in a foreign key constraint" — apps.broadcasts.
    # education_models' tables (e.g. broadcasts_liveclassroom) still
    # physically exist in the database from a past migration but are no
    # longer imported by apps/broadcasts/models.py (Django's actual app
    # registry), so Django's flush doesn't know to include them in the
    # TRUNCATE list even though Postgres still enforces their FK to
    # accounts_user — a pre-existing inconsistency unrelated to this
    # phase's own changes, not something this test should have to resolve.
    available_apps = ['apps.accounts', 'apps.broadcasts']

    def _make_institution_and_broadcast(self, *, seat_limit=None, phone_prefix='55589'):
        User = get_user_model()
        owner = User.objects.create_user(
            phone=f'{phone_prefix}00001', username=f'edu_conc_owner_{phone_prefix}', password='secret', country='NG',
        )
        institution = EducationInstitution.objects.create(
            owner=owner,
            name='Concurrency Test Academy',
            membership_policy=EducationInstitutionMembershipPolicy.OPEN,
        )
        broadcast = EducationInstitutionBroadcast.objects.create(
            institution=institution,
            created_by=owner,
            broadcast_kind='course',
            title='Race Condition 101',
            summary='A free, unlimited-or-limited-seat broadcast for concurrency testing.',
            description='Detail text.',
            booking_enabled=False,
            status='published',
            seat_limit=seat_limit,
        )
        return owner, institution, broadcast

    def test_concurrent_enrollments_never_oversell_a_seat_limited_broadcast(self):
        User = get_user_model()
        _, _, broadcast = self._make_institution_and_broadcast(seat_limit=3, phone_prefix='55591')
        learners = [
            User.objects.create_user(
                phone=f'555910{i:03d}', username=f'edu_conc_learner_{i}', password='secret', country='NG',
            )
            for i in range(8)
        ]
        results = []
        lock = threading.Lock()

        def worker(user):
            from rest_framework.test import APIClient
            client = APIClient()
            client.force_authenticate(user)
            try:
                response = client.post(
                    f'/api/v1/education/contents/{broadcast.id}/enroll/', {}, format='json',
                )
                with lock:
                    results.append(response.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(learner,)) for learner in learners]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No thread should ever crash with an unhandled server error.
        self.assertTrue(all(code < 500 for code in results), results)
        self.assertEqual(len(results), 8)

        enrolled_count = EducationInstitutionEnrollment.objects.filter(
            broadcast=broadcast, status=EducationEnrollmentStatus.ENROLLED,
        ).count()
        waitlisted_count = EducationInstitutionEnrollment.objects.filter(
            broadcast=broadcast, status=EducationEnrollmentStatus.WAITLISTED,
        ).count()
        self.assertLessEqual(enrolled_count, 3, 'seat_limit=3 must never be oversold under concurrency')
        self.assertEqual(enrolled_count + waitlisted_count, 8, 'every learner must end up enrolled or waitlisted, never lost')

    def test_concurrent_duplicate_enrollment_attempts_from_the_same_user_never_crash(self):
        User = get_user_model()
        _, _, broadcast = self._make_institution_and_broadcast(seat_limit=None, phone_prefix='55592')
        learner = User.objects.create_user(
            phone='5559200002', username='edu_conc_dup_learner', password='secret', country='NG',
        )
        results = []
        lock = threading.Lock()

        def worker():
            from rest_framework.test import APIClient
            client = APIClient()
            client.force_authenticate(learner)
            try:
                response = client.post(
                    f'/api/v1/education/contents/{broadcast.id}/enroll/', {}, format='json',
                )
                with lock:
                    results.append(response.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(code == 200 for code in results), results)
        self.assertEqual(
            EducationInstitutionEnrollment.objects.filter(broadcast=broadcast, user=learner).count(), 1,
            'the unique_together=(broadcast,user) constraint must result in exactly one row, never an unhandled IntegrityError',
        )


# ---------------------------------------------------------------------
# Phase 2 (Education system production-hardening project): cross-user /
# cross-institution IDOR audit. Every test here authenticates as a user
# who owns Institution A, then attempts to read/write Institution B's
# resources by substituting IDs — proving server-side authorization holds
# regardless of what a client supplies, not just that the mobile UI hides
# the option.
# ---------------------------------------------------------------------

class EducationInstitutionIDORTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner_a = User.objects.create_user(
            phone='5559300001', username='edu_idor_owner_a', password='secret', country='NG',
        )
        self.owner_b = User.objects.create_user(
            phone='5559300002', username='edu_idor_owner_b', password='secret', country='NG',
        )
        self.institution_a = EducationInstitution.objects.create(owner=self.owner_a, name='Institution A')
        self.institution_b = EducationInstitution.objects.create(owner=self.owner_b, name='Institution B')
        self.material_a = EducationInstitutionMaterial.objects.create(
            institution=self.institution_a,
            title='Institution A private material',
            kind='document',
        )

    def test_owner_b_cannot_patch_institution_a_via_direct_id_substitution(self):
        self.client.force_authenticate(self.owner_b)
        response = self.client.patch(
            f'/api/v1/broadcasts/education/institutions/{self.institution_a.id}/',
            {'name': 'Hijacked by B'}, format='json',
        )
        self.assertIn(response.status_code, (403, 404))
        self.institution_a.refresh_from_db()
        self.assertEqual(self.institution_a.name, 'Institution A')

    def test_owner_b_cannot_delete_institution_a(self):
        self.client.force_authenticate(self.owner_b)
        response = self.client.delete(f'/api/v1/broadcasts/education/institutions/{self.institution_a.id}/')
        self.assertIn(response.status_code, (403, 404))
        self.assertTrue(EducationInstitution.objects.filter(id=self.institution_a.id).exists())

    def test_owner_b_cannot_read_institution_a_material_via_own_institution_url(self):
        """Institution B is legitimately B's own — the material_id belongs
        to A. The lookup must fail because the material doesn't belong to
        the institution named in the URL, not silently succeed."""
        self.client.force_authenticate(self.owner_b)
        response = self.client.get(
            f'/api/v1/broadcasts/education/institutions/{self.institution_b.id}/materials/{self.material_a.id}/',
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_b_cannot_read_institution_a_material_via_institution_a_url(self):
        """The outer boundary: B isn't a member of A at all, so B must be
        rejected before the material lookup even happens."""
        self.client.force_authenticate(self.owner_b)
        response = self.client.get(
            f'/api/v1/broadcasts/education/institutions/{self.institution_a.id}/materials/{self.material_a.id}/',
        )
        self.assertIn(response.status_code, (403, 404))

    def test_owner_b_cannot_delete_institution_a_material(self):
        self.client.force_authenticate(self.owner_b)
        response = self.client.delete(
            f'/api/v1/broadcasts/education/institutions/{self.institution_b.id}/materials/{self.material_a.id}/',
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(EducationInstitutionMaterial.objects.filter(id=self.material_a.id).exists())

    def test_anonymous_user_cannot_access_institution_admin_endpoints(self):
        response = self.client.get(f'/api/v1/broadcasts/education/institutions/{self.institution_a.id}/dashboard/')
        self.assertEqual(response.status_code, 401)


class EducationEnrollmentAndReviewIDORTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5559400001', username='edu_idor_owner', password='secret', country='NG',
        )
        self.learner_a = User.objects.create_user(
            phone='5559400002', username='edu_idor_learner_a', password='secret', country='NG',
        )
        self.learner_b = User.objects.create_user(
            phone='5559400003', username='edu_idor_learner_b', password='secret', country='NG',
        )
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='Shared Institution')
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution, title='Course', status='published',
        )
        self.broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution,
            created_by=self.owner,
            broadcast_kind='course',
            course=self.course,
            title='Course',
            summary='Summary',
            description='Description',
            status='published',
        )

    def test_a_cancelled_enrollment_cannot_post_a_review(self):
        """Re-verifies the existing enrollment-gated review rule
        (test_non_enrolled_learner_cannot_post_review already proves the
        never-enrolled case) against the adversarial case: an enrollment
        that existed and was cancelled must not still count as access."""
        EducationInstitutionEnrollment.objects.create(
            institution=self.institution,
            broadcast=self.broadcast,
            course=self.course,
            user=self.learner_a,
            status=EducationEnrollmentStatus.CANCELLED,
        )
        self.client.force_authenticate(self.learner_a)
        response = self.client.post(
            f'/api/v1/education/contents/{self.broadcast.id}/reviews/',
            {'rating': 5, 'comment': 'Should not be allowed.'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            EducationCourseReview.objects.filter(broadcast=self.broadcast, user=self.learner_a).exists()
        )

    def test_learner_b_cannot_mark_progress_on_a_content_item_without_being_enrolled(self):
        self.client.force_authenticate(self.learner_b)
        # A syntactically well-formed but arbitrary item_id — the request
        # must be rejected because learner_b has no ENROLLED/COMPLETED
        # enrollment at all, regardless of whether the item_id resolves.
        response = self.client.post(
            f'/api/v1/education/contents/{self.broadcast.id}/items/lesson-{uuid.uuid4()}/action/',
            {'action': 'mark_attended'},
            format='json',
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------
# Phase 3 (Education system production-hardening project): direct-to-S3
# upload authorization, cross-institution IDOR, and content-safety gating
# for the education_institution_logo / education_module_cover_image /
# education_material upload contexts (apps/broadcasts/education_media.py).
# ---------------------------------------------------------------------

INITIATE_URL = '/api/v1/broadcasts/education/uploads/initiate/'


def _media_confirm_url(upload_id):
    return f'/api/v1/media/uploads/{upload_id}/confirm/'


@patch("apps.media.storage_backends.S3MediaStorage._client")
class EducationUploadIntentTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5559500001', username='edu_upload_owner', password='secret', country='NG',
        )
        self.staff_member = User.objects.create_user(
            phone='5559500002', username='edu_upload_staff', password='secret', country='NG',
        )
        self.outsider = User.objects.create_user(
            phone='5559500003', username='edu_upload_outsider', password='secret', country='NG',
        )
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='Upload Academy')
        self.other_institution = EducationInstitution.objects.create(owner=self.outsider, name='Other Academy')
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution, title='Course', status='published',
        )
        self.other_course = EducationInstitutionCourse.objects.create(
            institution=self.other_institution, title='Other Course', status='published',
        )
        EducationInstitutionMembership.objects.create(
            institution=self.institution,
            user=self.staff_member,
            role=EducationInstitutionMembershipRole.MANAGER,
            status=EducationInstitutionMembershipStatus.ACTIVE,
        )

    def _initiate_and_confirm(self, mock_client, *, user, context, institution_id=None,
                               content_type='image/jpeg', size_bytes=1000, filename=None):
        from apps.media.tests import _mock_s3_client

        client = _mock_s3_client()
        client.head_object.return_value = {"ContentLength": size_bytes, "ContentType": content_type}
        mock_client.return_value = client
        self.client.force_authenticate(user)
        if filename is None:
            filename = 'upload.pdf' if content_type == 'application/pdf' else 'upload.jpg'
        body = {
            'context': context,
            'filename': filename,
            'contentType': content_type,
            'sizeBytes': size_bytes,
        }
        if institution_id is not None:
            body['institutionId'] = institution_id
        return self.client.post(INITIATE_URL, body, format='json')

    def test_institution_owner_can_initiate_for_material(self, mock_client):
        response = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_material',
            institution_id=str(self.institution.id), content_type='application/pdf',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIn('uploadId', response.data)

    def test_institution_manager_can_initiate(self, mock_client):
        response = self._initiate_and_confirm(
            mock_client, user=self.staff_member, context='education_module_cover_image',
            institution_id=str(self.institution.id),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_non_member_cannot_initiate_for_institution(self, mock_client):
        response = self._initiate_and_confirm(
            mock_client, user=self.outsider, context='education_material',
            institution_id=str(self.institution.id), content_type='application/pdf',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_logo_upload_allowed_without_institution_id_for_brand_new_institution(self, mock_client):
        response = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_institution_logo',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_cover_image_upload_requires_institution_id(self, mock_client):
        response = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_module_cover_image',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_material_upload_requires_institution_id(self, mock_client):
        response = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_material', content_type='application/pdf',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_oversized_material_upload_rejected(self, mock_client):
        # education_material's cap is 5 GiB (S3's own single-PUT ceiling —
        # see _education_material_max_bytes), raised from a previous 100
        # MiB that was silently rejecting anything but the shortest course
        # videos. 500 MiB, this test's old size, is now comfortably inside
        # that limit and no longer exercises this rejection path at all -
        # go just over the real current ceiling instead.
        response = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_material',
            institution_id=str(self.institution.id), content_type='application/pdf',
            size_bytes=6 * 1024 * 1024 * 1024,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unsupported_content_type_rejected(self, mock_client):
        response = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_module_cover_image',
            institution_id=str(self.institution.id), content_type='application/x-msdownload',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_media_confirmed_for_one_institution_cannot_attach_to_another(self, mock_client):
        initiate = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_material',
            institution_id=str(self.institution.id), content_type='application/pdf',
        )
        self.assertEqual(initiate.status_code, status.HTTP_201_CREATED, initiate.data)
        confirm = self.client.post(_media_confirm_url(initiate.data['uploadId']), {}, format='json')
        self.assertEqual(confirm.status_code, status.HTTP_200_OK, confirm.data)
        media_id = confirm.data['mediaId']

        # The owner also owns other_institution's... no — outsider owns it.
        # Make the owner a manager of other_institution to isolate the
        # "wrong institution" check from the "wrong user" check.
        EducationInstitutionMembership.objects.create(
            institution=self.other_institution,
            user=self.owner,
            role=EducationInstitutionMembershipRole.MANAGER,
            status=EducationInstitutionMembershipStatus.ACTIVE,
        )
        response = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.other_institution.id}/materials/',
            {'title': 'Cross-institution material', 'course_ids': [str(self.other_course.id)], 'resource_attachment': {'media_id': media_id}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_media_confirmed_by_one_user_cannot_be_used_by_another(self, mock_client):
        initiate = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_material',
            institution_id=str(self.institution.id), content_type='application/pdf',
        )
        confirm = self.client.post(_media_confirm_url(initiate.data['uploadId']), {}, format='json')
        media_id = confirm.data['mediaId']

        self.client.force_authenticate(self.staff_member)
        response = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/materials/',
            {'title': 'Stolen media', 'course_ids': [str(self.course.id)], 'resource_attachment': {'media_id': media_id}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_forged_attachment_without_media_id_is_rejected(self, mock_client):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/materials/',
            {
                'title': 'Forged',
                'resource_attachment': {
                    'url': 'https://evil.example.com/anything.pdf',
                    'quarantined': False,
                    'scan_status': 'allowed',
                },
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(MEDIA_SAFETY_ENABLED=True, MEDIA_EXPLICIT_SCAN_REQUIRED=True, MEDIA_SAFETY_PROVIDER='stub')
    def test_quarantined_material_upload_is_blanked_not_hard_rejected(self, mock_client):
        # Production defaults (stub provider, no live provider calls) mark
        # every upload pending_review/quarantine=True — the material must
        # still be CREATED (soft-quarantine, matching the pre-Phase-3
        # behavior of _education_material_media_payload), just with a blank
        # resource_url and a blocked_user_message, never a hard 400.
        initiate = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_material',
            institution_id=str(self.institution.id), content_type='application/pdf',
        )
        confirm = self.client.post(_media_confirm_url(initiate.data['uploadId']), {}, format='json')
        media_id = confirm.data['mediaId']

        response = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/materials/',
            {'title': 'Under review', 'course_ids': [str(self.course.id)], 'resource_attachment': {'media_id': media_id}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        material = response.data['material']
        self.assertEqual(material['safe_resource_url'], '')
        scan = MediaSafetyScan.objects.get(upload_id=media_id)
        self.assertEqual(scan.context, 'education')
        self.assertTrue(scan.quarantine)

    def test_confirmed_material_media_binds_and_cannot_be_reused(self, mock_client):
        initiate = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_material',
            institution_id=str(self.institution.id), content_type='application/pdf',
        )
        confirm = self.client.post(_media_confirm_url(initiate.data['uploadId']), {}, format='json')
        media_id = confirm.data['mediaId']

        first = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/materials/',
            {'title': 'First use', 'course_ids': [str(self.course.id)], 'resource_attachment': {'media_id': media_id}},
            format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        intent = MediaUploadIntent.objects.get(id=media_id)
        self.assertIsNotNone(intent.attached_at)

        second = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/materials/',
            {'title': 'Second use', 'course_ids': [str(self.course.id)], 'resource_attachment': {'media_id': media_id}},
            format='json',
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_institution_logo_resolves_media_id_and_ignores_smuggled_url_override(self, mock_client):
        initiate = self._initiate_and_confirm(
            mock_client, user=self.owner, context='education_institution_logo',
            institution_id=str(self.institution.id),
        )
        confirm = self.client.post(_media_confirm_url(initiate.data['uploadId']), {}, format='json')
        media_id = confirm.data['mediaId']

        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/',
            {
                'logo_attachment': {'media_id': media_id},
                'logo_url': 'https://evil.example.com/fake-logo.png',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        branding = response.data['institution']['branding']
        self.assertNotIn('evil.example.com', branding.get('logo_url', ''))
        intent = MediaUploadIntent.objects.get(id=media_id)
        self.assertIsNotNone(intent.attached_at)


# ---------------------------------------------------------------------
# Phase 4 (Education system production-hardening project): concurrency
# safety for booking creation (seat_limit overselling) and escrow release
# (double-payout), proven with real threads + APITransactionTestCase —
# same rationale as EducationEnrollmentConcurrencyTests (Phase 2): a
# rolled-back-transaction TestCase would mask the exact races these tests
# exist to catch.
# ---------------------------------------------------------------------

class EducationBookingConcurrencySafetyTests(APITransactionTestCase):
    # See EducationEnrollmentConcurrencyTests for why this is required.
    available_apps = ['apps.accounts', 'apps.broadcasts', 'apps.billing']

    def _make_institution_and_broadcast(self, *, seat_limit=None, booking_enabled=True,
                                         price_amount='10.00', phone_prefix='55594'):
        User = get_user_model()
        owner = User.objects.create_user(
            phone=f'{phone_prefix}00001', username=f'edu_booking_conc_owner_{phone_prefix}',
            password='secret', country='NG',
        )
        institution = EducationInstitution.objects.create(
            owner=owner,
            name='Booking Concurrency Academy',
            membership_policy=EducationInstitutionMembershipPolicy.OPEN,
        )
        broadcast = EducationInstitutionBroadcast.objects.create(
            institution=institution,
            created_by=owner,
            broadcast_kind='course',
            title='Booking Race Condition 101',
            summary='A booking-enabled broadcast for concurrency testing.',
            description='Detail text.',
            booking_enabled=booking_enabled,
            price_amount=price_amount,
            price_currency='USD',
            status='published',
            seat_limit=seat_limit,
        )
        return owner, institution, broadcast

    def test_concurrent_bookings_never_oversell_a_seat_limited_broadcast(self):
        User = get_user_model()
        _, _, broadcast = self._make_institution_and_broadcast(seat_limit=3, phone_prefix='55595')
        learners = [
            User.objects.create_user(
                phone=f'555950{i:03d}', username=f'edu_booking_conc_learner_{i}', password='secret', country='NG',
            )
            for i in range(8)
        ]
        results = []
        lock = threading.Lock()

        def worker(user):
            from rest_framework.test import APIClient
            client = APIClient()
            client.force_authenticate(user)
            try:
                response = client.post(
                    f'/api/v1/broadcasts/education/institutions/{broadcast.institution_id}/broadcasts/{broadcast.id}/bookings/',
                    {}, format='json',
                )
                with lock:
                    results.append(response.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(learner,)) for learner in learners]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(code < 500 for code in results), results)
        self.assertEqual(len(results), 8)

        from apps.broadcasts.models import EducationBookingStatus, EducationInstitutionBooking

        confirmed_count = EducationInstitutionBooking.objects.filter(
            broadcast=broadcast, status=EducationBookingStatus.CONFIRMED,
        ).count()
        pending_or_waitlisted_count = EducationInstitutionBooking.objects.filter(
            broadcast=broadcast, status__in=[EducationBookingStatus.PENDING, EducationBookingStatus.WAITLISTED],
        ).count()
        self.assertLessEqual(confirmed_count, 3, 'seat_limit=3 must never be oversold under concurrency')
        self.assertEqual(
            confirmed_count + pending_or_waitlisted_count, 8,
            'every learner must end up with a booking row (confirmed, pending, or waitlisted), never lost',
        )

    def test_concurrent_duplicate_booking_attempts_from_the_same_user_never_crash(self):
        User = get_user_model()
        _, _, broadcast = self._make_institution_and_broadcast(seat_limit=None, phone_prefix='55596')
        learner = User.objects.create_user(
            phone='5559600002', username='edu_booking_conc_dup_learner', password='secret', country='NG',
        )
        results = []
        lock = threading.Lock()

        def worker():
            from rest_framework.test import APIClient
            client = APIClient()
            client.force_authenticate(learner)
            try:
                response = client.post(
                    f'/api/v1/broadcasts/education/institutions/{broadcast.institution_id}/broadcasts/{broadcast.id}/bookings/',
                    {}, format='json',
                )
                with lock:
                    results.append(response.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(code in (200, 201) for code in results), results)

        from apps.broadcasts.models import EducationInstitutionBooking

        self.assertEqual(
            EducationInstitutionBooking.objects.filter(broadcast=broadcast, user=learner).count(), 1,
            'the unique_together=(broadcast,user) constraint must result in exactly one row, never an unhandled IntegrityError',
        )

    @override_settings(KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED=True)
    def test_concurrent_satisfaction_confirmations_never_double_release_escrowed_funds(self):
        from datetime import timedelta

        from apps.billing.models import WalletTransaction
        from apps.billing.services import get_wallet_account, lock_wallet_funds_for_booking
        from apps.broadcasts.models import EducationBookingStatus, EducationInstitutionBooking

        User = get_user_model()
        _, institution, broadcast = self._make_institution_and_broadcast(phone_prefix='55597')
        learner = User.objects.create_user(
            phone='5559700002', username='edu_booking_conc_payer', password='secret', country='NG',
        )
        EducationInstitutionMembership.objects.create(
            institution=institution,
            user=learner,
            role=EducationInstitutionMembershipRole.STUDENT,
            status=EducationInstitutionMembershipStatus.ACTIVE,
        )
        wallet = get_wallet_account(learner)
        wallet.balance_cents = 10_000
        wallet.save(update_fields=['balance_cents', 'updated_at'])
        _, wallet_tx = lock_wallet_funds_for_booking(
            user=learner, amount_cents=1_000, reference='test-education-booking-lock',
        )
        booking = EducationInstitutionBooking.objects.create(
            institution=institution,
            broadcast=broadcast,
            user=learner,
            status=EducationBookingStatus.AWAITING_SATISFACTION,
            amount_cents=1_000,
            currency='USD',
            wallet_transaction=wallet_tx,
            confirmed_at=timezone.now(),
            provider_completed_at=timezone.now(),
            satisfaction_deadline=timezone.now() + timedelta(days=3),
        )
        results = []
        lock = threading.Lock()

        def worker():
            from rest_framework.test import APIClient
            client = APIClient()
            client.force_authenticate(learner)
            try:
                response = client.post(
                    f'/api/v1/broadcasts/education/institutions/{institution.id}/bookings/{booking.id}/satisfy/',
                    {}, format='json',
                )
                with lock:
                    results.append(response.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one request wins the lock and completes the booking; the
        # rest correctly see it's no longer CONFIRMED/AWAITING_SATISFACTION
        # once serialized behind the winner and get a 400 — never a 500,
        # and never two winners double-releasing the escrow.
        self.assertTrue(all(code in (200, 400) for code in results), results)
        self.assertEqual(results.count(200), 1, results)
        booking.refresh_from_db()
        self.assertEqual(booking.status, EducationBookingStatus.COMPLETED)
        self.assertEqual(
            WalletTransaction.objects.filter(tx_ref=f'education-booking-payout-{booking.id}').count(), 1,
            'exactly one payout transaction must be created, never a double payout',
        )
        wallet.refresh_from_db()
        self.assertEqual(
            wallet.locked_cents, 0,
            'locked escrow funds must be released exactly once — a race here would over-release and corrupt the payer\'s wallet',
        )


# ---------------------------------------------------------------------
# Phase 5 (Education system production-hardening project): final
# integration-seam IDOR sweep. EducationInstitutionBookingActionView and
# EducationInstitutionBookingSatisfactionView gained a new select_for_update()
# fetch in Phase 4 (to close the escrow double-release race) but had no
# direct authorization test coverage of their own before this phase —
# every previous booking test exercised the happy path. These tests close
# that specific gap rather than re-proving what Phases 2-4 already covered.
# ---------------------------------------------------------------------

class EducationBookingActionIDORTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5559800001', username='edu_booking_idor_owner', password='secret', country='NG',
        )
        self.payer = User.objects.create_user(
            phone='5559800002', username='edu_booking_idor_payer', password='secret', country='NG',
        )
        self.stranger = User.objects.create_user(
            phone='5559800003', username='edu_booking_idor_stranger', password='secret', country='NG',
        )
        self.other_owner = User.objects.create_user(
            phone='5559800004', username='edu_booking_idor_other_owner', password='secret', country='NG',
        )
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='IDOR Booking Academy')
        self.other_institution = EducationInstitution.objects.create(owner=self.other_owner, name='Other Academy')
        self.broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution,
            created_by=self.owner,
            broadcast_kind='course',
            title='IDOR Booking Course',
            summary='Summary',
            description='Description',
            booking_enabled=True,
            price_amount='5.00',
            price_currency='USD',
            status='published',
        )
        EducationInstitutionMembership.objects.create(
            institution=self.institution,
            user=self.payer,
            role=EducationInstitutionMembershipRole.STUDENT,
            status=EducationInstitutionMembershipStatus.ACTIVE,
        )
        from apps.broadcasts.models import EducationBookingStatus, EducationInstitutionBooking

        self.booking = EducationInstitutionBooking.objects.create(
            institution=self.institution,
            broadcast=self.broadcast,
            user=self.payer,
            status=EducationBookingStatus.CONFIRMED,
            amount_cents=500,
            currency='USD',
            confirmed_at=timezone.now(),
        )

    def test_stranger_cannot_call_booking_action_view(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/bookings/{self.booking.id}/action/',
            {'action': 'confirm'},
            format='json',
        )
        self.assertIn(response.status_code, (403, 404))
        self.booking.refresh_from_db()
        from apps.broadcasts.models import EducationBookingStatus

        self.assertEqual(self.booking.status, EducationBookingStatus.CONFIRMED, 'a non-manager must never be able to mutate a booking')

    def test_payer_cannot_call_booking_action_view(self):
        # The action view is institution-management-only (complete/cancel/
        # confirm/etc as the provider) — the payer's own action is the
        # separate satisfaction endpoint, not this one.
        self.client.force_authenticate(self.payer)
        response = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/bookings/{self.booking.id}/action/',
            {'action': 'cancel'},
            format='json',
        )
        self.assertIn(response.status_code, (403, 404))

    def test_other_institution_manager_cannot_call_booking_action_view_via_id_substitution(self):
        self.client.force_authenticate(self.other_owner)
        response = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.other_institution.id}/bookings/{self.booking.id}/action/',
            {'action': 'confirm'},
            format='json',
        )
        self.assertEqual(response.status_code, 404, 'a booking scoped to institution A must not resolve under institution B')

    def test_stranger_cannot_confirm_satisfaction_for_someone_elses_booking(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/bookings/{self.booking.id}/satisfy/',
            {},
            format='json',
        )
        self.assertIn(response.status_code, (403, 404))
        self.booking.refresh_from_db()
        from apps.broadcasts.models import EducationBookingStatus

        self.assertEqual(self.booking.status, EducationBookingStatus.CONFIRMED)

    def test_institution_manager_cannot_confirm_satisfaction_on_the_payers_behalf(self):
        # Satisfaction confirmation is deliberately payer-only — an
        # institution owner marking their own booking "satisfied" would
        # defeat the whole point of the payer-controlled escrow release.
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f'/api/v1/broadcasts/education/institutions/{self.institution.id}/bookings/{self.booking.id}/satisfy/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 403)


class EducationInstitutionManagementDetailAccessControlTests(APITestCase):
    """Regression coverage for a real, confirmed production vulnerability:
    EducationInstitutionLessonDetailView.get (and 11 sibling
    Course/Program/CourseModule/CourseModuleItem/ClassSession/Material/
    Assessment/AssessmentQuestion/AssessmentOption detail views) had
    permission_classes = [IsAuthenticated] with NO staff-membership check
    on their GET methods, while their own PATCH/DELETE methods on the
    same class correctly required
    _require_manage_institution_membership. Any low-barrier member of
    the institution (a plain STUDENT-role membership, typically free to
    join under an 'application'/open policy — not staff, not requiring
    enrollment or payment for the specific paid course) could fetch the
    full, unsanitized lesson/course/material/assessment
    content (including EducationInstitutionLessonSerializer's raw
    `content` field, assessment question text, and — most severely —
    assessment option `is_correct` answer flags) for ANY institution's
    paid course, given only the institution_id/lesson_id UUIDs (which a
    non-enrolled user already legitimately has from the correctly-
    sanitized public course-preview endpoint). This is a completely
    separate, more severe endpoint than the student-facing
    /api/v1/education/contents/{id}/ flow, which does correctly redact
    content — this one returned it in full to anyone logged in."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5558800101', username='mgmt_access_owner', password='secret', country='NG',
        )
        self.stranger = User.objects.create_user(
            phone='5558800102', username='mgmt_access_stranger', password='secret', country='NG',
        )
        self.institution = EducationInstitution.objects.create(
            owner=self.owner, name='Guarded Academy', description='Paid content lives here.',
            payout_account_status=EducationInstitutionPayoutAccountStatus.ACTIVE,
            flutterwave_subaccount_id='RS_TEST_GUARD',
        )
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution, title='Secret Curriculum', status='published',
            price_amount='49.00', price_currency='USD',
        )
        self.lesson = EducationInstitutionLesson.objects.create(
            institution=self.institution, course=self.course, title='Confidential Lesson',
            content='This is the actual paid lesson body — should never leak.',
        )
        self.material = EducationInstitutionMaterial.objects.create(
            institution=self.institution, title='Answer Key PDF',
            resource_url='https://example.com/private/answer-key.pdf', is_downloadable=True,
        )
        # _get_education_institution_or_404 scopes lookup to
        # owner-or-any-membership, so the exploitable scenario isn't a
        # total stranger (who'd 404 before even reaching the missing
        # staff check) — it's any low-barrier STUDENT-role member (free
        # to join under an 'application'/open membership policy)
        # reaching a staff-only management endpoint with no staff role.
        EducationInstitutionMembership.objects.create(
            institution=self.institution, user=self.stranger,
            role=EducationInstitutionMembershipRole.STUDENT,
            status=EducationInstitutionMembershipStatus.ACTIVE,
        )

    def _lesson_detail_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/lessons/{self.lesson.id}/'

    def _course_detail_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/courses/{self.course.id}/'

    def _material_detail_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/materials/{self.material.id}/'

    def test_stranger_cannot_read_lesson_content(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.get(self._lesson_detail_url())
        self.assertEqual(response.status_code, 403, response.data)

    def test_stranger_cannot_read_course_detail(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.get(self._course_detail_url())
        self.assertEqual(response.status_code, 403, response.data)

    def test_stranger_cannot_read_material_detail(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.get(self._material_detail_url())
        self.assertEqual(response.status_code, 403, response.data)

    def test_owner_can_still_read_lesson_content(self):
        # The fix must not break legitimate staff access to their own content.
        self.client.force_authenticate(self.owner)
        response = self.client.get(self._lesson_detail_url())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['lesson']['content'], self.lesson.content)

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(self._lesson_detail_url())
        self.assertIn(response.status_code, (401, 403))


class EducationContentDetailProgressAccessSignalTests(APITestCase):
    """Regression coverage for the second half of the same reported bug:
    the student-facing /api/v1/education/contents/{id}/ booking-details
    screen was flashing the full paid-lesson reader UI open for a
    non-enrolled viewer before redirecting to checkout. The `content`
    outline itself was already correctly sanitized
    (resource_url/content blanked), but this endpoint unconditionally
    returned a populated `progress`/`insights`/`current_item` object
    regardless of access — the RN app read Boolean(progress) as an
    implicit "this viewer is enrolled" signal, unlocking the reader UI
    on that false signal alone. Every field on the response root that
    could be read as that signal must be null for a non-access viewer,
    and still populated for a genuinely enrolled one."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5558800201', username='progress_signal_owner', password='secret', country='NG',
        )
        self.learner = User.objects.create_user(
            phone='5558800202', username='progress_signal_learner', password='secret', country='NG',
        )
        self.institution = EducationInstitution.objects.create(
            owner=self.owner, name='Signal Academy', description='Paid content lives here.',
            payout_account_status=EducationInstitutionPayoutAccountStatus.ACTIVE,
            flutterwave_subaccount_id='RS_TEST_SIGNAL',
        )
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution, title='Paid Curriculum', status='published',
            price_amount='49.00', price_currency='USD',
        )
        self.broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution,
            created_by=self.owner,
            broadcast_kind='course',
            course=self.course,
            title='Paid Curriculum',
            summary='Should not unlock without payment.',
            booking_enabled=True,
            price_amount='49.00',
            price_currency='USD',
            status='published',
        )
        EducationInstitutionMembership.objects.create(
            institution=self.institution, user=self.learner,
            role=EducationInstitutionMembershipRole.STUDENT,
            status=EducationInstitutionMembershipStatus.ACTIVE,
        )

    def _content_detail_url(self):
        return f'/api/v1/education/contents/{self.broadcast.id}/'

    def test_non_enrolled_viewer_gets_no_progress_access_signal(self):
        self.client.force_authenticate(self.learner)
        response = self.client.get(self._content_detail_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data['progress'])
        self.assertIsNone(response.data['insights'])
        self.assertIsNone(response.data['current_item'])
        self.assertIsNone(response.data['current_module'])
        self.assertIsNone(response.data['next_item'])
        self.assertFalse(response.data['certificate']['ready'])
        self.assertEqual(response.data['certificate']['certificateId'], '')

    def test_enrolled_viewer_still_gets_real_progress(self):
        EducationInstitutionEnrollment.objects.create(
            institution=self.institution,
            broadcast=self.broadcast,
            course=self.course,
            user=self.learner,
            status='enrolled',
        )
        self.client.force_authenticate(self.learner)
        response = self.client.get(self._content_detail_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNotNone(response.data['progress'])
        self.assertIsNotNone(response.data['insights'])

    def test_pending_enroll_tap_does_not_return_progress_access_signal(self):
        self.client.force_authenticate(self.learner)
        response = self.client.post(f'{self._content_detail_url()}enroll/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data['progress'])


class EducationFreePreviewLessonTests(APITestCase):
    """Regression coverage for completing the course owner's "free preview
    lesson" feature (EducationInstitutionLesson.is_preview). The field and
    its plumbing (previewItemCount, detail_item.previewLesson) already
    existed, but _sanitize_learning_item_content_for_public_preview blanked
    every item's content unconditionally for a non-enrolled viewer,
    including ones explicitly marked as a free sample — the RN app's
    "Preview" button was consequently a dead stub that never showed real
    content. A lesson the owner marks is_preview=True must show its real
    body text to a shopper who has not paid; every other lesson must stay
    fully blanked exactly as before."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5558800301', username='preview_lesson_owner', password='secret', country='NG',
        )
        self.shopper = User.objects.create_user(
            phone='5558800302', username='preview_lesson_shopper', password='secret', country='NG',
        )
        self.institution = EducationInstitution.objects.create(
            owner=self.owner, name='Sampler Academy', description='Try before you buy.',
            payout_account_status=EducationInstitutionPayoutAccountStatus.ACTIVE,
            flutterwave_subaccount_id='RS_TEST_PREVIEW',
        )
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution, title='Sampler Curriculum', status='published',
            price_amount='49.00', price_currency='USD',
        )
        self.broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution,
            created_by=self.owner,
            broadcast_kind='course',
            course=self.course,
            title='Sampler Curriculum',
            summary='Free preview lesson should be visible.',
            booking_enabled=True,
            price_amount='49.00',
            price_currency='USD',
            status='published',
        )
        self.preview_lesson = EducationInstitutionLesson.objects.create(
            institution=self.institution, course=self.course, title='Free Sample Lesson',
            content='This is the real free-sample body text.',
            is_preview=True,
        )
        self.locked_lesson = EducationInstitutionLesson.objects.create(
            institution=self.institution, course=self.course, title='Paid-Only Lesson',
            content='This must stay hidden until purchase.',
            is_preview=False,
        )

    def _content_detail_url(self):
        return f'/api/v1/education/contents/{self.broadcast.id}/'

    def _outline_items(self, response):
        outline = response.data['content']['courseOutline']
        return [item for module in outline for item in module['items']]

    def test_non_enrolled_shopper_sees_real_content_for_preview_lesson_only(self):
        self.client.force_authenticate(self.shopper)
        response = self.client.get(self._content_detail_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        items = self._outline_items(response)
        preview_item = next(i for i in items if i['title'] == 'Free Sample Lesson')
        locked_item = next(i for i in items if i['title'] == 'Paid-Only Lesson')
        self.assertEqual(
            preview_item['content']['content'],
            'This is the real free-sample body text.',
        )
        self.assertEqual(locked_item['content']['content'], '')

    def test_anonymous_lookup_never_sees_locked_content_regardless_of_preview(self):
        # Sanity check the preview exemption didn't loosen anything else -
        # a non-preview lesson on the same course must still be blanked.
        self.client.force_authenticate(self.shopper)
        response = self.client.get(self._content_detail_url())
        items = self._outline_items(response)
        locked_item = next(i for i in items if i['title'] == 'Paid-Only Lesson')
        self.assertFalse(locked_item['content'].get('is_downloadable', False))

    def test_enrolled_learner_sees_both_lessons_in_full(self):
        EducationInstitutionEnrollment.objects.create(
            institution=self.institution,
            broadcast=self.broadcast,
            course=self.course,
            user=self.shopper,
            status='enrolled',
        )
        self.client.force_authenticate(self.shopper)
        response = self.client.get(self._content_detail_url())
        items = self._outline_items(response)
        locked_item = next(i for i in items if i['title'] == 'Paid-Only Lesson')
        self.assertEqual(
            locked_item['content']['content'],
            'This must stay hidden until purchase.',
        )
