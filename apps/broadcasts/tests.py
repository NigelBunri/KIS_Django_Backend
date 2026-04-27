import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from apps.accounts.models import Profile
from apps.broadcasts.models import (
    BroadcastFeedProfile,
    BroadcastItem,
    BroadcastMarketProfile,
    BroadcastSourceType,
    BroadcastVideo,
    EducationInstitution,
    EducationInstitutionBroadcast,
    EducationInstitutionEvent,
)
from apps.channels.models import Channel
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.communities.models import Community, CommunityMembership, CommunityRole
from apps.partners.models import Partner, PartnerJoinConfig, PartnerMembership, PartnerMembershipStatus
from rest_framework import status
from rest_framework.test import APITestCase


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
        self.assertEqual(profile.get('shops') or [], [{'id': 'shop_1', 'name': 'Shop One'}])

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
        self.assertEqual(first.data, {'shared': True, 'platform': 'app'})
        self.assertEqual(second.data, {'shared': True, 'platform': 'app'})

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
        self.client.force_authenticate(user=self.user)

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
        with override_settings(MEDIA_ROOT=self.temp_media_dir.name):
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

    @override_settings(
        API_BASE_URL='http://10.14.20.99:8000',
        SITE_URL='http://10.14.20.99:8000',
        ALLOWED_HOSTS=['testserver', '127.0.0.1'],
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
