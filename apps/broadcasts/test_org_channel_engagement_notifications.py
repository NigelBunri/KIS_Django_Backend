from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.broadcasts.models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelContent,
    ChannelContentComment,
    ChannelContentReaction,
    ChannelContentType,
    EducationInstitution,
)
from apps.broadcasts.services import resolve_channel_notification_recipients
from apps.commerce.models import Shop
from apps.health_ops.models import HealthInstitution
from apps.notifications.models import Notification
from apps.partners.models import Partner

User = get_user_model()


class OrgChannelEngagementNotificationTests(TestCase):
    """Regression coverage for a real bug: engagement notifications
    (comment/reaction) used to resolve their recipient from
    content.channel.owner_user_id directly, which is None for every
    non-USER owner_type (shop/health/education/partner). An org-owned
    channel's OWNER/MANAGER role holders - e.g. the creator, who
    BroadcastChannelCreateView always assigns an OWNER role to regardless
    of owner_type - silently received nothing.

    Covers all five BroadcastChannel.OwnerType values per the closure
    mandate: user, shop, health, education, partner.
    """

    def setUp(self):
        self.actor = User.objects.create_user(phone="5557710001", username="ocn_actor", password="secret", country="NG")
        self.individual_owner = User.objects.create_user(phone="5557710002", username="ocn_indiv", password="secret", country="NG")
        self.org_owner_role_user = User.objects.create_user(phone="5557710003", username="ocn_org_owner", password="secret", country="NG")
        self.org_manager_role_user = User.objects.create_user(phone="5557710004", username="ocn_org_mgr", password="secret", country="NG")
        self.org_editor_role_user = User.objects.create_user(phone="5557710005", username="ocn_org_editor", password="secret", country="NG")

        self.shop = Shop.objects.create(owner=self.org_owner_role_user, name="OCN Shop", slug="ocn-shop")
        self.health_institution = HealthInstitution.objects.create(owner=self.org_owner_role_user, name="OCN Clinic")
        self.education_institution = EducationInstitution.objects.create(owner=self.org_owner_role_user, name="OCN Academy")
        self.partner = Partner.objects.create(owner=self.org_owner_role_user, name="OCN Partner", slug="ocn-partner")

    def _make_content(self, channel):
        return ChannelContent.objects.create(
            channel=channel,
            content_type=ChannelContentType.TEXT,
            title="Engagement test content",
            text_plain="Body.",
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            created_by=channel.owner_user or self.org_owner_role_user,
        )

    # ---- resolve_channel_notification_recipients: direct unit coverage ----

    def test_user_owned_channel_resolves_to_owner_user_id(self):
        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.individual_owner.id,
            owner_user=self.individual_owner,
            handle="ocn-user-channel",
            display_name="Individual channel",
        )
        self.assertEqual(resolve_channel_notification_recipients(channel), [self.individual_owner.id])

    def _assert_org_channel_resolves_to_owner_and_manager_only(self, owner_type, owner_id, handle):
        channel = BroadcastChannel.objects.create(
            owner_type=owner_type, owner_id=owner_id, owner_user=None, handle=handle, display_name=handle,
        )
        BroadcastChannelRole.objects.create(channel=channel, user=self.org_owner_role_user, role=BroadcastChannelRole.Role.OWNER)
        BroadcastChannelRole.objects.create(channel=channel, user=self.org_manager_role_user, role=BroadcastChannelRole.Role.MANAGER)
        BroadcastChannelRole.objects.create(channel=channel, user=self.org_editor_role_user, role=BroadcastChannelRole.Role.EDITOR)

        recipients = set(resolve_channel_notification_recipients(channel))
        self.assertEqual(recipients, {self.org_owner_role_user.id, self.org_manager_role_user.id})
        self.assertNotIn(self.org_editor_role_user.id, recipients)
        return channel

    def test_shop_owned_channel_resolves_to_role_holders(self):
        self._assert_org_channel_resolves_to_owner_and_manager_only(BroadcastChannel.OwnerType.SHOP, self.shop.id, "ocn-shop-channel")

    def test_health_owned_channel_resolves_to_role_holders(self):
        self._assert_org_channel_resolves_to_owner_and_manager_only(
            BroadcastChannel.OwnerType.HEALTH, self.health_institution.id, "ocn-health-channel",
        )

    def test_education_owned_channel_resolves_to_role_holders(self):
        self._assert_org_channel_resolves_to_owner_and_manager_only(
            BroadcastChannel.OwnerType.EDUCATION, self.education_institution.id, "ocn-education-channel",
        )

    def test_partner_owned_channel_resolves_to_role_holders(self):
        self._assert_org_channel_resolves_to_owner_and_manager_only(
            BroadcastChannel.OwnerType.PARTNER, self.partner.id, "ocn-partner-channel",
        )

    def test_org_channel_with_no_role_holders_resolves_to_empty_list_not_an_error(self):
        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.SHOP, owner_id=self.shop.id, owner_user=None,
            handle="ocn-orphan-shop-channel", display_name="Orphan",
        )
        self.assertEqual(resolve_channel_notification_recipients(channel), [])

    # ---- end-to-end: comment/reaction signals actually notify the right people ----

    def test_comment_on_org_owned_channel_notifies_owner_and_manager_roles(self):
        channel = self._assert_org_channel_resolves_to_owner_and_manager_only(
            BroadcastChannel.OwnerType.SHOP, self.shop.id, "ocn-shop-comment-channel",
        )
        content = self._make_content(channel)

        ChannelContentComment.objects.create(content=content, user=self.actor, body="Nice post")

        notified_user_ids = set(
            Notification.objects.filter(type="channel.content.commented").values_list("user_id", flat=True)
        )
        self.assertIn(self.org_owner_role_user.id, notified_user_ids)
        self.assertIn(self.org_manager_role_user.id, notified_user_ids)
        self.assertNotIn(self.org_editor_role_user.id, notified_user_ids)
        self.assertNotIn(self.actor.id, notified_user_ids)  # never notify yourself

    def test_reaction_on_org_owned_channel_notifies_owner_and_manager_roles(self):
        channel = self._assert_org_channel_resolves_to_owner_and_manager_only(
            BroadcastChannel.OwnerType.HEALTH, self.health_institution.id, "ocn-health-reaction-channel",
        )
        content = self._make_content(channel)

        ChannelContentReaction.objects.create(content=content, user=self.actor, reaction="like")

        notified_user_ids = set(
            Notification.objects.filter(type="channel.content.reacted").values_list("user_id", flat=True)
        )
        self.assertIn(self.org_owner_role_user.id, notified_user_ids)
        self.assertIn(self.org_manager_role_user.id, notified_user_ids)
        self.assertNotIn(self.org_editor_role_user.id, notified_user_ids)

    def test_comment_on_user_owned_channel_still_notifies_the_individual_owner(self):
        """Regression guard: the fan-out change must not break the
        pre-existing, already-correct individual-owner path."""
        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.individual_owner.id,
            owner_user=self.individual_owner,
            handle="ocn-user-comment-channel",
            display_name="Individual channel",
        )
        content = self._make_content(channel)

        ChannelContentComment.objects.create(content=content, user=self.actor, body="Hello")

        notified_user_ids = set(
            Notification.objects.filter(type="channel.content.commented").values_list("user_id", flat=True)
        )
        self.assertEqual(notified_user_ids, {self.individual_owner.id})

    def test_org_channel_with_no_role_holders_does_not_error_on_comment(self):
        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.PARTNER, owner_id=self.partner.id, owner_user=None,
            handle="ocn-orphan-partner-channel", display_name="Orphan partner",
        )
        content = self._make_content(channel)
        # Must not raise, and must simply produce zero notifications.
        ChannelContentComment.objects.create(content=content, user=self.actor, body="Anyone there?")
        self.assertEqual(Notification.objects.filter(type="channel.content.commented").count(), 0)
