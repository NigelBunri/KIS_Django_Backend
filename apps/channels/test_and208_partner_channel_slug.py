"""AND-208: "unable to create a partner channel" (mobile's
PartnerChannelsPanel.tsx "Create channel" button). Channel.slug has no
model-level blank=True or default, so ChannelCreateSerializer's
auto-generated slug field was required=True - but the mobile client's
createChannel() only ever sent name/channel_type/partner(/category), never
a slug. Every request from that screen 400'd on "slug", surfaced to the
user as a generic "Unable to create channel." alert. The existing test
suite never caught this because every test in apps/channels/tests.py
happens to pass slug explicitly. This replicates the exact payload shape
the mobile client actually sends.
"""
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.channels.models import Channel
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.partners.models import Partner, PartnerMembership, PartnerMembershipStatus


class PartnerChannelCreateWithoutSlugTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            phone="+237671000201", country="CM", password="pass1234",
            email="and208-owner@example.com", username="and208-owner",
        )
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="AND-208 Partner", created_by=self.owner,
        )
        ConversationMember.objects.create(
            conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER,
        )
        self.partner = Partner.objects.create(
            owner=self.owner, name="AND-208 Partner", slug="and-208-partner", main_conversation=conversation,
        )

    def test_create_channel_with_the_exact_mobile_payload_shape(self):
        """No "slug" key at all - exactly what PartnerChannelsPanel.createChannel() sends."""
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/partner-channels/channels/",
            {
                "name": "General Discussion",
                "channel_type": Channel.ChannelType.TEXT,
                "partner": str(self.partner.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        channel = Channel.objects.get(partner=self.partner, name="General Discussion")
        self.assertEqual(channel.slug, "general-discussion")

    def test_duplicate_names_in_the_same_partner_get_distinct_slugs(self):
        self.client.force_authenticate(self.owner)
        first = self.client.post(
            "/api/v1/partner-channels/channels/",
            {"name": "General", "channel_type": Channel.ChannelType.TEXT, "partner": str(self.partner.id)},
            format="json",
        )
        second = self.client.post(
            "/api/v1/partner-channels/channels/",
            {"name": "General", "channel_type": Channel.ChannelType.TEXT, "partner": str(self.partner.id)},
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.data)
        self.assertEqual(first.data["slug"], "general")
        self.assertEqual(second.data["slug"], "general-1")
