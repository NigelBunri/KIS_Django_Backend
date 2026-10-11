"""
Closes the asymmetry flagged in the broadcast/channels production-closure
audit: ChannelContent (the platform's real, modern content model) had no
equivalent to BroadcastVideo's fail-closed, human-review-required
moderation gate - see apps.broadcasts.moderation_gate's module docstring.
These tests cover the gate itself (apps.broadcasts.moderation_gate) and
its wiring into the real public-facing surface (apps.broadcasts.views).
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.broadcasts.models import (
    BroadcastChannel,
    ChannelContent,
    ChannelContentAsset,
    ChannelContentType,
    ContentModerationStatus,
)
from apps.broadcasts.moderation_gate import (
    apply_channel_content_moderation_decision,
    filter_channel_content_broadcast_eligible,
    is_channel_content_broadcast_eligible,
    resolve_and_apply_moderation_decision,
)

User = get_user_model()


def _make_channel(owner):
    return BroadcastChannel.objects.create(
        owner_type=BroadcastChannel.OwnerType.USER,
        owner_id=owner.id,
        owner_user=owner,
        handle=f"ccml-channel-{owner.id}",
        display_name="Moderation lifecycle channel",
    )


def _make_content(channel, owner, **overrides):
    defaults = dict(
        channel=channel,
        content_type=ChannelContentType.TEXT,
        title="Test content",
        text_plain="Body.",
        status=ChannelContent.Status.PUBLISHED,
        visibility=ChannelContent.Visibility.PUBLIC,
        published_at=timezone.now(),
        created_by=owner,
    )
    defaults.update(overrides)
    return ChannelContent.objects.create(**defaults)


class IsChannelContentBroadcastEligibleTests(TestCase):
    """Direct unit coverage of the gate function itself - the exact same
    shape of test suite BroadcastVideo's is_broadcast_eligible would get,
    extended to every state ChannelContent can actually be in."""

    def setUp(self):
        self.owner = User.objects.create_user(phone="5558820001", username="ccml_owner", password="secret", country="NG")
        self.channel = _make_channel(self.owner)

    def test_new_content_defaults_to_pending_review_and_is_not_eligible(self):
        """Covers: missing asset (plain text/poll/link content has none at
        all) and the 'safe scan alone is not sufficient' requirement -
        there is no scan verdict here whatsoever, just a brand new row,
        and it must still default closed."""
        content = _make_content(self.channel, self.owner)
        self.assertEqual(content.moderation_status, ContentModerationStatus.PENDING_REVIEW)
        self.assertFalse(is_channel_content_broadcast_eligible(content))

    def test_passed_with_future_expiry_is_eligible(self):
        """Covers: safe content, once an actual human pass has been
        recorded."""
        content = _make_content(
            self.channel, self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + timedelta(days=90),
        )
        self.assertTrue(is_channel_content_broadcast_eligible(content))

    def test_passed_with_no_expiry_set_is_not_eligible(self):
        """Mirrors is_broadcast_eligible's own defensive case: a PASSED
        row that somehow has no expiry (should never happen via
        apply_channel_content_moderation_decision, but must not be
        trusted if it does) is treated as ineligible, not eligible."""
        content = _make_content(self.channel, self.owner, moderation_status=ContentModerationStatus.PASSED, moderation_expires_at=None)
        self.assertFalse(is_channel_content_broadcast_eligible(content))

    def test_expired_moderation_is_not_eligible(self):
        """Covers: expired moderation - a pass that has lapsed must stop
        being eligible without anyone having to take a new action."""
        content = _make_content(
            self.channel, self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now() - timedelta(days=200),
            moderation_expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertFalse(is_channel_content_broadcast_eligible(content))

    def test_blocked_is_not_eligible(self):
        """Covers: prohibited content."""
        content = _make_content(self.channel, self.owner, moderation_status=ContentModerationStatus.BLOCKED)
        self.assertFalse(is_channel_content_broadcast_eligible(content))

    def test_ambiguous_pending_review_is_not_eligible(self):
        """Covers: ambiguous content / scanner failure / storage failure -
        all three route to PENDING_REVIEW (see
        test_channel_asset_explicit_content_scan.py for the asset-scan
        side of this), and PENDING_REVIEW must never be treated as
        eligible, regardless of why the row ended up there."""
        content = _make_content(self.channel, self.owner, moderation_status=ContentModerationStatus.PENDING_REVIEW)
        self.assertFalse(is_channel_content_broadcast_eligible(content))

    def test_filter_queryset_matches_single_item_check_exactly(self):
        eligible = _make_content(
            self.channel, self.owner, title="Eligible",
            moderation_status=ContentModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=1),
        )
        ineligible_pending = _make_content(self.channel, self.owner, title="Pending")
        ineligible_expired = _make_content(
            self.channel, self.owner, title="Expired",
            moderation_status=ContentModerationStatus.PASSED,
            moderation_expires_at=timezone.now() - timedelta(days=1),
        )
        ineligible_blocked = _make_content(self.channel, self.owner, title="Blocked", moderation_status=ContentModerationStatus.BLOCKED)

        result_ids = set(filter_channel_content_broadcast_eligible(ChannelContent.objects.all()).values_list("id", flat=True))

        self.assertIn(eligible.id, result_ids)
        self.assertNotIn(ineligible_pending.id, result_ids)
        self.assertNotIn(ineligible_expired.id, result_ids)
        self.assertNotIn(ineligible_blocked.id, result_ids)


class ApplyChannelContentModerationDecisionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="5558820002", username="ccml_actor", password="secret", country="NG")
        self.moderator = User.objects.create_user(phone="5558820003", username="ccml_mod", password="secret", country="NG")
        self.channel = _make_channel(self.owner)
        self.content = _make_content(self.channel, self.owner)

    @override_settings(BROADCAST_MODERATION_REVALIDATION_DAYS=45)
    def test_pass_sets_status_timestamps_and_reviewer_and_respects_revalidation_setting(self):
        apply_channel_content_moderation_decision(self.content, action="pass", actor=self.moderator)
        self.content.refresh_from_db()
        self.assertEqual(self.content.moderation_status, ContentModerationStatus.PASSED)
        self.assertIsNotNone(self.content.moderation_passed_at)
        self.assertEqual(self.content.moderation_reviewed_by_id, self.moderator.id)
        expected_expiry = self.content.moderation_passed_at + timedelta(days=45)
        self.assertAlmostEqual(self.content.moderation_expires_at, expected_expiry, delta=timedelta(seconds=5))
        self.assertTrue(is_channel_content_broadcast_eligible(self.content))

    def test_pending_resets_to_pending_review(self):
        apply_channel_content_moderation_decision(self.content, action="pass", actor=self.moderator)
        apply_channel_content_moderation_decision(self.content, action="pending", actor=self.moderator)
        self.content.refresh_from_db()
        self.assertEqual(self.content.moderation_status, ContentModerationStatus.PENDING_REVIEW)
        self.assertFalse(is_channel_content_broadcast_eligible(self.content))

    def test_block_sets_blocked_and_is_not_reversed_by_mere_time_passing(self):
        apply_channel_content_moderation_decision(self.content, action="block", actor=self.moderator)
        self.content.refresh_from_db()
        self.assertEqual(self.content.moderation_status, ContentModerationStatus.BLOCKED)
        self.assertFalse(is_channel_content_broadcast_eligible(self.content))

    def test_unknown_action_raises(self):
        with self.assertRaises(ValueError):
            apply_channel_content_moderation_decision(self.content, action="nonsense", actor=self.moderator)

    def test_resolve_and_apply_dispatches_pass_pending_block_for_channel_content(self):
        """Regression guard: this dispatcher used to hard-reject every
        action except "delete" for channel_content (the exact asymmetry
        this whole change closes)."""
        for action in ("pass", "pending", "block"):
            handled = resolve_and_apply_moderation_decision("channel_content", self.content.id, action=action, actor=self.moderator)
            self.assertTrue(handled, f"action={action!r} should be handled for channel_content")

    def test_resolve_and_apply_delete_still_routes_to_delete_channel_content(self):
        handled = resolve_and_apply_moderation_decision("channel_content", self.content.id, action="delete", actor=self.moderator)
        self.assertTrue(handled)
        self.content.refresh_from_db()
        self.assertTrue(self.content.is_deleted)

    def test_resolve_and_apply_unknown_target_id_returns_false_not_an_error(self):
        handled = resolve_and_apply_moderation_decision("channel_content", "00000000-0000-0000-0000-000000000000", action="pass", actor=self.moderator)
        self.assertFalse(handled)


class ChannelContentDetailViewModerationGatingTests(APITestCase):
    """End-to-end: an ordinary viewer must not be able to see content that
    hasn't passed moderation, regardless of status=PUBLISHED/
    visibility=PUBLIC - and a channel manager previewing their own
    content is unaffected, same creator-preview exception already
    established for age-restriction."""

    def setUp(self):
        self.owner = User.objects.create_user(phone="5558820004", username="ccml_view_owner", password="secret", country="NG")
        self.viewer = User.objects.create_user(phone="5558820005", username="ccml_viewer", password="secret", country="NG")
        self.channel = _make_channel(self.owner)

    def test_pending_review_content_is_404_for_ordinary_viewer(self):
        content = _make_content(self.channel, self.owner)
        self.client.force_authenticate(self.viewer)
        response = self.client.get(f"/api/v1/broadcasts/channel-contents/{content.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_passed_content_is_visible_to_ordinary_viewer(self):
        content = _make_content(
            self.channel, self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=1),
        )
        self.client.force_authenticate(self.viewer)
        response = self.client.get(f"/api/v1/broadcasts/channel-contents/{content.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_owner_can_preview_their_own_pending_review_content(self):
        content = _make_content(self.channel, self.owner)
        self.client.force_authenticate(self.owner)
        response = self.client.get(f"/api/v1/broadcasts/channel-contents/{content.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_draft_content_is_404_for_viewer_regardless_of_moderation_status(self):
        """Covers: unpublished/draft content - moderation PASSED does not
        override the separate, pre-existing publish-status gate."""
        content = _make_content(
            self.channel, self.owner,
            status=ChannelContent.Status.DRAFT,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=1),
        )
        self.client.force_authenticate(self.viewer)
        response = self.client.get(f"/api/v1/broadcasts/channel-contents/{content.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_scheduled_content_is_404_for_viewer_regardless_of_moderation_status(self):
        """Covers: scheduled content."""
        content = _make_content(
            self.channel, self.owner,
            status=ChannelContent.Status.SCHEDULED,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=1),
        )
        self.client.force_authenticate(self.viewer)
        response = self.client.get(f"/api/v1/broadcasts/channel-contents/{content.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ChannelContentListViewModerationGatingTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="5558820006", username="ccml_list_owner", password="secret", country="NG")
        self.viewer = User.objects.create_user(phone="5558820007", username="ccml_list_viewer", password="secret", country="NG")
        self.channel = _make_channel(self.owner)

    def test_public_channel_content_list_excludes_unapproved_content(self):
        passed = _make_content(
            self.channel, self.owner, title="Passed",
            moderation_status=ContentModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=1),
        )
        pending = _make_content(self.channel, self.owner, title="Pending")

        self.client.force_authenticate(self.viewer)
        response = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/contents/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item["id"] for item in response.data["results"]}
        self.assertIn(str(passed.id), returned_ids)
        self.assertNotIn(str(pending.id), returned_ids)

    def test_manager_sees_their_own_unapproved_content_in_the_list(self):
        pending = _make_content(self.channel, self.owner, title="Pending")
        self.client.force_authenticate(self.owner)
        response = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/contents/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item["id"] for item in response.data["results"]}
        self.assertIn(str(pending.id), returned_ids)


class ReReviewOnEditTests(APITestCase):
    """Covers: edited content requiring re-review."""

    def setUp(self):
        self.owner = User.objects.create_user(phone="5558820008", username="ccml_edit_owner", password="secret", country="NG")
        self.channel = _make_channel(self.owner)
        self.content = _make_content(
            self.channel, self.owner, title="Original title",
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + timedelta(days=1),
        )
        self.client.force_authenticate(self.owner)

    def test_editing_title_resets_passed_content_to_pending_review(self):
        response = self.client.patch(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/", {"title": "A materially different title"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.content.refresh_from_db()
        self.assertEqual(self.content.moderation_status, ContentModerationStatus.PENDING_REVIEW)
        self.assertIsNone(self.content.moderation_expires_at)
        self.assertFalse(is_channel_content_broadcast_eligible(self.content))

    def test_editing_unrelated_field_does_not_reset_moderation(self):
        """Only substantive content fields trigger re-review - toggling
        comments_disabled, for example, isn't a content change."""
        response = self.client.patch(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/", {"comments_disabled": True}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.content.refresh_from_db()
        self.assertEqual(self.content.moderation_status, ContentModerationStatus.PASSED)
        self.assertTrue(is_channel_content_broadcast_eligible(self.content))

    def test_patch_with_same_title_value_does_not_reset_moderation(self):
        response = self.client.patch(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/", {"title": "Original title"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.content.refresh_from_db()
        self.assertEqual(self.content.moderation_status, ContentModerationStatus.PASSED)

    def test_adding_a_new_asset_resets_passed_content_to_pending_review(self):
        response = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/assets/",
            {"asset_type": "document", "url": "https://cdn.example.com/f.pdf", "storage_path": "private/x/f.pdf"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.content.refresh_from_db()
        self.assertEqual(self.content.moderation_status, ContentModerationStatus.PENDING_REVIEW)
        self.assertIsNone(self.content.moderation_expires_at)

    def test_adding_asset_to_already_pending_content_is_a_no_op_for_moderation_fields(self):
        """The reset logic should only fire off of PASSED - it must not,
        for example, clear a BLOCKED verdict back to PENDING_REVIEW just
        because someone attached a new file."""
        self.content.moderation_status = ContentModerationStatus.BLOCKED
        self.content.moderation_expires_at = None
        self.content.save(update_fields=["moderation_status", "moderation_expires_at"])

        response = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/assets/",
            {"asset_type": "document", "url": "https://cdn.example.com/g.pdf", "storage_path": "private/x/g.pdf"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.content.refresh_from_db()
        self.assertEqual(self.content.moderation_status, ContentModerationStatus.BLOCKED)
