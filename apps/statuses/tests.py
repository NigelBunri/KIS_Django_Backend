from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User, UserContact
from apps.media.models import MediaSafetyScan, MediaUploadIntent
from apps.media.tests import _mock_s3_client
from apps.moderation.models import AuditLog, Flag, UserBlock
from apps.statuses.models import (
    StatusAudienceTarget,
    StatusComment,
    StatusHide,
    StatusItem,
    StatusItemView,
    StatusModerationStatus,
    StatusMute,
    StatusReaction,
    StatusReplyPermission,
    StatusType,
    StatusVisibility,
)


class StatusPrivacyContractTests(APITestCase):
    def setUp(self):
        self.author = User.objects.create_user(
            phone="+2348000000101",
            password="password123",
            country="NG",
            display_name="Author",
        )
        self.viewer = User.objects.create_user(
            phone="+2348000000102",
            password="password123",
            country="NG",
            display_name="Viewer",
        )
        self.excluded = User.objects.create_user(
            phone="+2348000000103",
            password="password123",
            country="NG",
            display_name="Excluded",
        )
        self.stranger = User.objects.create_user(
            phone="+2348000000104",
            password="password123",
            country="NG",
            display_name="Stranger",
        )

        self._link_contacts(self.author, self.viewer)
        self._link_contacts(self.author, self.excluded)

    def _link_contacts(self, left: User, right: User) -> None:
        UserContact.objects.create(
            user=left,
            contact_user=right,
            contact_phone=right.phone,
            contact_phone_number=right.phone,
            contact_display_name=right.display_name or "",
        )
        UserContact.objects.create(
            user=right,
            contact_user=left,
            contact_phone=left.phone,
            contact_phone_number=left.phone,
            contact_display_name=left.display_name or "",
        )

    def _create_status(
        self,
        *,
        author: User,
        visibility: str = StatusVisibility.CONTACTS,
        reply_permission: str = StatusReplyPermission.CONTACTS,
        targets: list[User] | None = None,
    ) -> StatusItem:
        status_item = StatusItem.objects.create(
            user=author,
            type=StatusType.TEXT,
            text="Hello status",
            visibility=visibility,
            reply_permission=reply_permission,
        )
        for target in targets or []:
            StatusAudienceTarget.objects.create(status=status_item, target_user=target)
        return status_item

    def test_list_only_returns_server_visible_statuses(self):
        visible_status = self._create_status(author=self.author)
        self._create_status(author=self.stranger)

        self.client.force_authenticate(self.viewer)
        res = self.client.get(reverse("statuses:status-list"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.json()["results"]
        user_ids = [entry["user"]["id"] for entry in results]
        self.assertIn(str(self.author.id), user_ids)
        self.assertNotIn(str(self.stranger.id), user_ids)
        first_author = next(entry for entry in results if entry["user"]["id"] == str(self.author.id))
        self.assertEqual(first_author["items"][0]["id"], str(visible_status.id))
        self.assertTrue(first_author["items"][0]["reply_allowed"])

    def test_contacts_except_hides_excluded_viewer(self):
        status_item = self._create_status(
            author=self.author,
            visibility=StatusVisibility.CONTACTS_EXCEPT,
            targets=[self.excluded],
        )
        self.client.force_authenticate(self.excluded)

        res = self.client.get(reverse("statuses:status-list"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        author_entries = [
            entry for entry in res.json()["results"] if entry["user"]["id"] == str(self.author.id)
        ]
        self.assertEqual(author_entries, [])
        mark_view = self.client.post(f"/api/v1/statuses/{status_item.id}/view/")
        self.assertEqual(mark_view.status_code, status.HTTP_403_FORBIDDEN)

    def test_only_share_with_requires_selected_target(self):
        self._create_status(
            author=self.author,
            visibility=StatusVisibility.ONLY_SHARE_WITH,
            targets=[self.viewer],
        )
        self.client.force_authenticate(self.viewer)
        allowed = self.client.get(reverse("statuses:status-list"))
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(len(allowed.json()["results"]), 1)

        self.client.force_authenticate(self.excluded)
        denied = self.client.get(reverse("statuses:status-list"))
        author_entries = [
            entry for entry in denied.json()["results"] if entry["user"]["id"] == str(self.author.id)
        ]
        self.assertEqual(author_entries, [])

    def test_author_contacts_can_view_without_saving_author_back(self):
        author_only_contact = User.objects.create_user(
            phone="+2348000000105",
            password="password123",
            country="NG",
            display_name="Author Contact",
        )
        UserContact.objects.create(
            user=self.author,
            contact_user=author_only_contact,
            contact_phone=author_only_contact.phone,
            contact_phone_number=author_only_contact.phone,
            contact_display_name=author_only_contact.display_name or "",
        )
        self._create_status(author=self.author)

        self.client.force_authenticate(author_only_contact)
        res = self.client.get(reverse("statuses:status-list"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        user_ids = [entry["user"]["id"] for entry in res.json()["results"]]
        self.assertIn(str(self.author.id), user_ids)

    def test_only_share_with_selected_contact_can_view_without_reverse_contact(self):
        author_only_contact = User.objects.create_user(
            phone="+2348000000106",
            password="password123",
            country="NG",
            display_name="Selected Contact",
        )
        UserContact.objects.create(
            user=self.author,
            contact_user=author_only_contact,
            contact_phone=author_only_contact.phone,
            contact_phone_number=author_only_contact.phone,
            contact_display_name=author_only_contact.display_name or "",
        )
        self._create_status(
            author=self.author,
            visibility=StatusVisibility.ONLY_SHARE_WITH,
            targets=[author_only_contact],
        )

        self.client.force_authenticate(author_only_contact)
        res = self.client.get(reverse("statuses:status-list"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        user_ids = [entry["user"]["id"] for entry in res.json()["results"]]
        self.assertIn(str(self.author.id), user_ids)

    def test_create_accepts_explicit_audience_aliases(self):
        self.client.force_authenticate(self.author)
        res = self.client.post(
            "/api/v1/statuses/",
            {
                "type": StatusType.TEXT,
                "text": "Alias audience",
                "visibility": StatusVisibility.ONLY_SHARE_WITH,
                "allowed_user_ids": [str(self.viewer.id)],
            },
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        created = StatusItem.objects.get(text="Alias audience")
        self.assertTrue(
            StatusAudienceTarget.objects.filter(
                status=created,
                target_user=self.viewer,
            ).exists()
        )

    def test_mute_and_block_remove_author_from_status_feed(self):
        self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        mute_res = self.client.post(reverse("statuses:status-mute"), {"user_id": str(self.author.id)}, format="json")
        self.assertEqual(mute_res.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StatusMute.objects.filter(user=self.viewer, muted_user=self.author).exists()
        )
        muted_list = self.client.get(reverse("statuses:status-list"))
        author_entries = [
            entry for entry in muted_list.json()["results"] if entry["user"]["id"] == str(self.author.id)
        ]
        self.assertEqual(author_entries, [])

        self.client.post(reverse("statuses:status-unmute"), {"user_id": str(self.author.id)}, format="json")
        UserBlock.objects.create(blocker=self.viewer, blocked=self.author, reason="status_block")
        blocked_list = self.client.get(reverse("statuses:status-list"))
        author_entries = [
            entry for entry in blocked_list.json()["results"] if entry["user"]["id"] == str(self.author.id)
        ]
        self.assertEqual(author_entries, [])

    def test_report_creates_status_flag(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        res = self.client.post(
            reverse("statuses:status-report", kwargs={"pk": status_item.id}),
            {"reason": "spam"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        flag = Flag.objects.get(target_id=status_item.id)
        self.assertEqual(flag.target_type, "STATUS")
        self.assertEqual(flag.reason, "spam")
        self.assertTrue(
            AuditLog.objects.filter(action="status.report", target_id=status_item.id).exists()
        )

    def test_search_returns_visible_statuses_and_viewer_metrics(self):
        matching = self._create_status(author=self.author)
        matching.text = "Launch checklist update"
        matching.save(update_fields=["text"])
        hidden = self._create_status(author=self.stranger)
        hidden.text = "Launch checklist update"
        hidden.save(update_fields=["text"])
        self.client.force_authenticate(self.viewer)
        StatusItemView.objects.create(status=matching, user=self.viewer)

        res = self.client.get(reverse("statuses:status-search"), {"q": "Launch checklist"})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        rows = res.json()["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(matching.id))
        self.assertEqual(rows[0]["view_count"], 1)

    def test_viewers_endpoint_is_owner_only(self):
        status_item = self._create_status(author=self.author)
        StatusItemView.objects.create(status=status_item, user=self.viewer)

        self.client.force_authenticate(self.author)
        owner_res = self.client.get(reverse("statuses:status-viewers", kwargs={"pk": status_item.id}))
        self.assertEqual(owner_res.status_code, status.HTTP_200_OK)
        self.assertEqual(owner_res.json()["view_count"], 1)
        self.assertEqual(owner_res.json()["results"][0]["id"], str(self.viewer.id))

        self.client.force_authenticate(self.viewer)
        viewer_res = self.client.get(reverse("statuses:status-viewers", kwargs={"pk": status_item.id}))
        self.assertEqual(viewer_res.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(MEDIA_EXPLICIT_SCAN_REQUIRED=True, MEDIA_SAFETY_ENABLED=True)
    def test_media_status_is_held_for_family_safety_review(self):
        self.client.force_authenticate(self.author)
        upload = SimpleUploadedFile("family.jpg", b"safe image bytes", content_type="image/jpeg")

        res = self.client.post(
            "/api/v1/statuses/",
            {"type": StatusType.IMAGE, "file": upload, "visibility": StatusVisibility.CONTACTS},
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("file", res.json())
        scan = MediaSafetyScan.objects.get(context="status")
        self.assertEqual(scan.owner, self.author)
        self.assertEqual(scan.status, "pending_review")
        self.assertTrue(scan.quarantine)


class StatusReplyTests(StatusPrivacyContractTests):
    """apps/statuses/views.py::reply() - now a thin back-compat alias that
    creates a top-level StatusComment (no parent) directly, with no call
    to Nest/chat at all - see StatusCommentTests for the full comment/
    reply-threading contract and apps/statuses/services.py's
    deliver_status_reply_message docstring for why the old chat-delivery
    path (this class used to mock and assert against) was removed: it
    polluted the viewer's and author's ordinary chat history with every
    status reply, and depended on a synchronous Django->Nest HTTP call
    that had never actually worked in production (wrong internal token
    name, wrong call signature - see git history)."""

    def _reply_url(self, status_item) -> str:
        return f"/api/v1/statuses/{status_item.id}/reply/"

    def test_reply_succeeds_for_visible_status_and_creates_a_comment(self):
        status_item = self._create_status(author=self.author)

        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._reply_url(status_item), {"text": "Nice status!"})

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.json()["text"], "Nice status!")
        self.assertIsNone(res.json()["parent_id"])
        comment = StatusComment.objects.get(id=res.json()["id"])
        self.assertEqual(comment.status_id, status_item.id)
        self.assertEqual(comment.author_id, self.viewer.id)
        self.assertTrue(
            AuditLog.objects.filter(action="status.comment", target_id=status_item.id).exists()
        )

    def test_reply_never_creates_a_chat_conversation_or_message(self):
        # The old implementation called get_or_create_direct_conversation
        # and Nest's send-as-user endpoint - neither apps.chat.models nor
        # any HTTP client should be touched by this path anymore.
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        with patch("apps.statuses.services.deliver_status_reply_message") as mock_deliver:
            res = self.client.post(self._reply_url(status_item), {"text": "Hi"})

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        mock_deliver.assert_not_called()
        self.assertNotIn("conversation_id", res.json())
        self.assertNotIn("message_id", res.json())

    def test_reply_returns_404_when_viewer_is_excluded_from_audience(self):
        status_item = self._create_status(
            author=self.author,
            visibility=StatusVisibility.CONTACTS_EXCEPT,
            targets=[self.excluded],
        )

        self.client.force_authenticate(self.excluded)
        res = self.client.post(self._reply_url(status_item), {"text": "Hi"})

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(StatusComment.objects.count(), 0)

    def test_reply_returns_404_for_stranger_outside_contacts(self):
        status_item = self._create_status(author=self.author)

        self.client.force_authenticate(self.stranger)
        res = self.client.post(self._reply_url(status_item), {"text": "Hi"})

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(StatusComment.objects.count(), 0)

    def test_reply_returns_404_when_viewer_is_blocked(self):
        status_item = self._create_status(author=self.author)
        UserBlock.objects.create(blocker=self.author, blocked=self.viewer)

        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._reply_url(status_item), {"text": "Hi"})

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(StatusComment.objects.count(), 0)

    def test_reply_returns_403_when_author_disabled_replies(self):
        status_item = self._create_status(
            author=self.author, reply_permission=StatusReplyPermission.NOBODY,
        )

        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._reply_url(status_item), {"text": "Hi"})

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(StatusComment.objects.count(), 0)

    def test_reply_rejects_empty_text(self):
        status_item = self._create_status(author=self.author)

        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._reply_url(status_item), {"text": "   "})

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StatusComment.objects.count(), 0)

    def test_reply_returns_404_for_missing_status(self):
        import uuid

        self.client.force_authenticate(self.viewer)
        res = self.client.post(f"/api/v1/statuses/{uuid.uuid4()}/reply/", {"text": "Hi"})

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class StatusCommentTests(StatusPrivacyContractTests):
    """The full comments/replies contract: threaded replies, pagination,
    idempotency, and that a reply can never be attached to a different
    status by manipulating parent_id."""

    def _comments_url(self, status_item) -> str:
        return f"/api/v1/statuses/{status_item.id}/comments/"

    def test_reply_to_a_comment_is_threaded_under_it(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)
        top_level = self.client.post(self._comments_url(status_item), {"text": "First!"}).json()

        self.client.force_authenticate(self.author)
        res = self.client.post(
            self._comments_url(status_item),
            {"text": "Thanks!", "parent_id": top_level["id"]},
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.json()["parent_id"], top_level["id"])

        replies = self.client.get(self._comments_url(status_item), {"parent_id": top_level["id"]})
        self.assertEqual(replies.status_code, status.HTTP_200_OK)
        self.assertEqual(len(replies.json()["results"]), 1)
        self.assertEqual(replies.json()["results"][0]["text"], "Thanks!")

        top_level_list = self.client.get(self._comments_url(status_item))
        self.assertEqual(top_level_list.json()["results"][0]["reply_count"], 1)

    def test_cannot_reply_to_a_reply(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)
        top_level = self.client.post(self._comments_url(status_item), {"text": "First!"}).json()
        reply = self.client.post(
            self._comments_url(status_item), {"text": "A reply", "parent_id": top_level["id"]},
        ).json()

        res = self.client.post(
            self._comments_url(status_item), {"text": "Reply to a reply", "parent_id": reply["id"]},
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_parent_id_from_a_different_status_is_rejected(self):
        status_a = self._create_status(author=self.author)
        status_b = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)
        comment_on_a = self.client.post(self._comments_url(status_a), {"text": "On A"}).json()

        # Attempting to attach a reply to status B, using a parent_id that
        # is a real comment id but belongs to status A - must be rejected,
        # not silently reparented onto B.
        res = self.client.post(
            self._comments_url(status_b), {"text": "Sneaky", "parent_id": comment_on_a["id"]},
        )

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(StatusComment.objects.filter(status=status_b).exists())

    def test_duplicate_submission_with_same_client_id_does_not_create_a_second_comment(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        first = self.client.post(
            self._comments_url(status_item), {"text": "Hi", "client_id": "retry-key-1"},
        )
        second = self.client.post(
            self._comments_url(status_item), {"text": "Hi", "client_id": "retry-key-1"},
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(StatusComment.objects.filter(status=status_item).count(), 1)

    def test_comments_endpoint_returns_404_for_viewer_outside_audience(self):
        status_item = self._create_status(
            author=self.author,
            visibility=StatusVisibility.CONTACTS_EXCEPT,
            targets=[self.excluded],
        )

        self.client.force_authenticate(self.excluded)
        res = self.client.get(self._comments_url(status_item))

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_comments_rejects_text_over_2000_characters(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        res = self.client.post(self._comments_url(status_item), {"text": "x" * 2001})

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_comments_pagination_returns_has_more(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)
        for i in range(5):
            self.client.post(self._comments_url(status_item), {"text": f"Comment {i}"})

        res = self.client.get(self._comments_url(status_item), {"limit": 3})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.json()["results"]), 3)
        self.assertTrue(res.json()["has_more"])


class StatusReactionTests(StatusPrivacyContractTests):
    """apps/statuses/views.py::react()/unreact()/reactions() - heart/emoji
    quick-reactions, persisted directly (no chat delivery - see
    StatusReplyTests' docstring for why), with reactions() now readable by
    any authorized viewer (previously owner-only) and grouped by emoji."""

    def _react_url(self, status_item) -> str:
        return f"/api/v1/statuses/{status_item.id}/react/"

    def _unreact_url(self, status_item) -> str:
        return f"/api/v1/statuses/{status_item.id}/unreact/"

    def _reactions_url(self, status_item) -> str:
        return f"/api/v1/statuses/{status_item.id}/reactions/"

    def test_react_records_reaction_with_no_chat_delivery(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        with patch("apps.statuses.services.deliver_status_reply_message") as mock_deliver:
            res = self.client.post(self._react_url(status_item), {"emoji": "❤️"})

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        mock_deliver.assert_not_called()
        self.assertTrue(
            StatusReaction.objects.filter(status=status_item, user=self.viewer, emoji="❤️").exists()
        )

    def test_second_reaction_from_same_user_replaces_the_first(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        self.client.post(self._react_url(status_item), {"emoji": "❤️"})
        self.client.post(self._react_url(status_item), {"emoji": "😂"})

        self.assertEqual(StatusReaction.objects.filter(status=status_item, user=self.viewer).count(), 1)
        self.assertEqual(
            StatusReaction.objects.get(status=status_item, user=self.viewer).emoji, "😂",
        )

    def test_unreact_removes_the_reaction(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)
        self.client.post(self._react_url(status_item), {"emoji": "❤️"})

        res = self.client.post(self._unreact_url(status_item))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.json()["removed"])
        self.assertFalse(StatusReaction.objects.filter(status=status_item, user=self.viewer).exists())

    def test_unreact_when_no_reaction_exists_is_a_harmless_no_op(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        res = self.client.post(self._unreact_url(status_item))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.json()["removed"])

    def test_react_returns_404_for_stranger_outside_contacts(self):
        status_item = self._create_status(author=self.author)

        self.client.force_authenticate(self.stranger)
        res = self.client.post(self._react_url(status_item), {"emoji": "❤️"})

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(StatusReaction.objects.filter(status=status_item).exists())

    def test_reactions_endpoint_is_readable_by_any_authorized_viewer_grouped_by_emoji(self):
        status_item = self._create_status(author=self.author)
        StatusReaction.objects.create(status=status_item, user=self.viewer, emoji="❤️")
        StatusReaction.objects.create(status=status_item, user=self.excluded, emoji="❤️")

        self.client.force_authenticate(self.author)
        owner_res = self.client.get(self._reactions_url(status_item))
        self.assertEqual(owner_res.status_code, status.HTTP_200_OK)
        self.assertEqual(owner_res.json()["reaction_count"], 2)
        self.assertEqual(owner_res.json()["groups"][0]["emoji"], "❤️")
        self.assertEqual(owner_res.json()["groups"][0]["count"], 2)
        self.assertEqual(len(owner_res.json()["groups"][0]["users"]), 2)

        # A regular authorized viewer (not the owner) can read the exact
        # same grouped data now - this used to 404 for anyone but the author.
        self.client.force_authenticate(self.viewer)
        viewer_res = self.client.get(self._reactions_url(status_item))
        self.assertEqual(viewer_res.status_code, status.HTTP_200_OK)
        self.assertEqual(viewer_res.json()["reaction_count"], 2)
        self.assertEqual(viewer_res.json()["my_reaction"], "❤️")

    def test_reactions_endpoint_still_404s_for_a_viewer_outside_the_audience(self):
        status_item = self._create_status(
            author=self.author,
            visibility=StatusVisibility.CONTACTS_EXCEPT,
            targets=[self.excluded],
        )

        self.client.force_authenticate(self.excluded)
        res = self.client.get(self._reactions_url(status_item))

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class StatusHideTests(StatusPrivacyContractTests):
    """Per-viewer "hide this one status" - apps/statuses/views.py's
    hide_status()/unhide_status(), distinct from StatusMute (hides every
    status from a given author)."""

    def test_hiding_a_status_removes_it_from_only_my_list(self):
        status_item = self._create_status(author=self.author)

        self.client.force_authenticate(self.viewer)
        hide_res = self.client.post(f"/api/v1/statuses/{status_item.id}/hide/")
        self.assertEqual(hide_res.status_code, status.HTTP_200_OK)

        my_list = self.client.get(reverse("statuses:status-list")).json()["results"]
        self.assertEqual([e for e in my_list if e["user"]["id"] == str(self.author.id)], [])

        # The excluded user (also a contact of author, not hiding) still sees it.
        self.client.force_authenticate(self.excluded)
        other_list = self.client.get(reverse("statuses:status-list")).json()["results"]
        self.assertTrue(any(e["user"]["id"] == str(self.author.id) for e in other_list))

    def test_unhiding_restores_visibility(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)
        self.client.post(f"/api/v1/statuses/{status_item.id}/hide/")

        unhide_res = self.client.post(f"/api/v1/statuses/{status_item.id}/unhide/")
        self.assertEqual(unhide_res.status_code, status.HTTP_200_OK)
        self.assertTrue(unhide_res.json()["removed"])

        my_list = self.client.get(reverse("statuses:status-list")).json()["results"]
        self.assertTrue(any(e["user"]["id"] == str(self.author.id) for e in my_list))

    def test_hiding_does_not_delete_the_status_for_its_owner(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)
        self.client.post(f"/api/v1/statuses/{status_item.id}/hide/")

        self.client.force_authenticate(self.author)
        owner_list = self.client.get(reverse("statuses:status-list")).json()["results"]
        self.assertTrue(any(e["user"]["id"] == str(self.author.id) for e in owner_list))


class StatusExpiryTests(StatusPrivacyContractTests):
    """72-hour maximum lifetime: server-computed from creation time, not
    client-controllable, and enforced on every read path."""

    def test_new_status_expires_at_is_72_hours_from_creation(self):
        before = timezone.now()
        status_item = self._create_status(author=self.author)
        # _create_status uses StatusItem.objects.create() directly (not the
        # create serializer/perform_create path exercised by the real POST
        # endpoint below) - save()'s own fallback is what's under test here.
        expected = before + timedelta(hours=StatusItem.MAX_LIFETIME_HOURS)
        self.assertGreaterEqual(status_item.expires_at, expected - timedelta(seconds=5))
        self.assertLessEqual(status_item.expires_at, expected + timedelta(seconds=5))

    def test_create_endpoint_clamps_expiry_to_72_hours_even_if_client_sends_its_own(self):
        self.client.force_authenticate(self.author)
        before = timezone.now()

        res = self.client.post(
            "/api/v1/statuses/",
            {
                "type": "text",
                "text": "Hello",
                # A client-supplied expires_at must be ignored entirely -
                # StatusCreateSerializer declares it read_only.
                "expires_at": (before + timedelta(days=30)).isoformat(),
            },
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        created = StatusItem.objects.get(id=res.json()["id"])
        max_allowed = before + timedelta(hours=StatusItem.MAX_LIFETIME_HOURS)
        self.assertLessEqual(created.expires_at, max_allowed + timedelta(seconds=5))

    def test_expired_status_is_excluded_from_list_and_all_discussion_endpoints(self):
        status_item = self._create_status(author=self.author)
        StatusItem.objects.filter(id=status_item.id).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        self.client.force_authenticate(self.viewer)
        listing = self.client.get(reverse("statuses:status-list")).json()["results"]
        self.assertEqual([e for e in listing if e["user"]["id"] == str(self.author.id)], [])

        react_res = self.client.post(f"/api/v1/statuses/{status_item.id}/react/", {"emoji": "❤️"})
        self.assertEqual(react_res.status_code, status.HTTP_404_NOT_FOUND)

        comment_res = self.client.post(
            f"/api/v1/statuses/{status_item.id}/comments/", {"text": "too late"},
        )
        self.assertEqual(comment_res.status_code, status.HTTP_404_NOT_FOUND)

        reactions_res = self.client.get(f"/api/v1/statuses/{status_item.id}/reactions/")
        self.assertEqual(reactions_res.status_code, status.HTTP_404_NOT_FOUND)

    def test_changing_the_device_clock_cannot_extend_a_status_past_72_hours(self):
        # expires_at is computed from timezone.now() on the server at
        # create time and never recomputed from anything client-supplied on
        # any later request - there's no endpoint that re-derives it from a
        # client timestamp at all, so there's nothing for a spoofed device
        # clock to influence. This test documents that invariant: reading a
        # status back (e.g. after a reaction) must never move expires_at.
        status_item = self._create_status(author=self.author)
        original_expiry = status_item.expires_at

        self.client.force_authenticate(self.viewer)
        self.client.post(f"/api/v1/statuses/{status_item.id}/react/", {"emoji": "❤️"})

        status_item.refresh_from_db()
        self.assertEqual(status_item.expires_at, original_expiry)


class StatusDeletionAndMutationTests(StatusPrivacyContractTests):
    """DELETE (real soft-delete + file cleanup) and PUT/PATCH (should be
    entirely disabled) on /api/v1/statuses/{id}/ - Phase 4 hardening."""

    def _delete_url(self, status_item) -> str:
        return f"/api/v1/statuses/{status_item.id}/"

    def test_owner_can_delete_own_status(self):
        status_item = self._create_status(author=self.author)

        self.client.force_authenticate(self.author)
        res = self.client.delete(self._delete_url(status_item))

        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        status_item.refresh_from_db()
        self.assertTrue(status_item.is_deleted)

    def test_deleted_status_disappears_from_viewer_list(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.author)
        self.client.delete(self._delete_url(status_item))

        self.client.force_authenticate(self.viewer)
        res = self.client.get(reverse("statuses:status-list"))
        author_entries = [
            entry for entry in res.json()["results"] if entry["user"]["id"] == str(self.author.id)
        ]
        self.assertEqual(author_entries, [])

    def test_deleting_status_removes_the_underlying_file(self):
        status_item = self._create_status(author=self.author)
        status_item.type = StatusType.IMAGE
        status_item.file.save("test.jpg", SimpleUploadedFile("test.jpg", b"fake-bytes"), save=True)
        storage = status_item.file.storage
        stored_name = status_item.file.name
        self.assertTrue(storage.exists(stored_name))

        self.client.force_authenticate(self.author)
        res = self.client.delete(self._delete_url(status_item))

        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(storage.exists(stored_name))

    def test_stranger_cannot_delete_someone_elses_status(self):
        status_item = self._create_status(author=self.author)

        self.client.force_authenticate(self.stranger)
        res = self.client.delete(self._delete_url(status_item))

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        status_item.refresh_from_db()
        self.assertFalse(status_item.is_deleted)

    def test_put_and_patch_are_disabled(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.author)

        patch_res = self.client.patch(self._delete_url(status_item), {"expires_at": "2099-01-01T00:00:00Z"})
        put_res = self.client.put(self._delete_url(status_item), {"text": "rewritten"})

        self.assertEqual(patch_res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(put_res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        status_item.refresh_from_db()
        self.assertEqual(status_item.text, "Hello status")


class StatusPurgeCommandTests(StatusPrivacyContractTests):
    """apps.statuses.services.purge_expired_statuses / the
    purge_expired_statuses management command - Phase 4 hardening for the
    previously-nonexistent expiry cleanup (expires_at/is_deleted were
    read-only filters everywhere, nothing ever actually purged a row)."""

    def test_purges_soft_deleted_and_long_expired_statuses(self):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone

        from apps.statuses.services import purge_expired_statuses

        soft_deleted = self._create_status(author=self.author)
        soft_deleted.is_deleted = True
        soft_deleted.save(update_fields=["is_deleted"])

        long_expired = self._create_status(author=self.author)
        StatusItem.objects.filter(id=long_expired.id).update(
            expires_at=timezone.now() - timedelta(days=30)
        )

        recently_expired = self._create_status(author=self.author)
        StatusItem.objects.filter(id=recently_expired.id).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

        still_active = self._create_status(author=self.author)

        result = purge_expired_statuses(grace_days=7)

        self.assertEqual(result["purged_count"], 2)
        remaining_ids = set(StatusItem.objects.values_list("id", flat=True))
        self.assertNotIn(soft_deleted.id, remaining_ids)
        self.assertNotIn(long_expired.id, remaining_ids)
        self.assertIn(recently_expired.id, remaining_ids)
        self.assertIn(still_active.id, remaining_ids)

        # The management command is a thin wrapper - confirm it actually
        # invokes the same function rather than a second implementation.
        call_command("purge_expired_statuses", "--grace-days", "0")
        self.assertFalse(StatusItem.objects.filter(id=recently_expired.id).exists())
        self.assertTrue(StatusItem.objects.filter(id=still_active.id).exists())

    def test_purge_removes_the_underlying_file(self):
        status_item = self._create_status(author=self.author)
        status_item.type = StatusType.IMAGE
        status_item.file.save("test.jpg", SimpleUploadedFile("test.jpg", b"fake-bytes"), save=True)
        storage = status_item.file.storage
        stored_name = status_item.file.name
        status_item.is_deleted = True
        status_item.save(update_fields=["is_deleted"])

        from apps.statuses.services import purge_expired_statuses

        purge_expired_statuses()

        self.assertFalse(storage.exists(stored_name))
        self.assertFalse(StatusItem.objects.filter(id=status_item.id).exists())


INITIATE_URL = "/api/v1/media/uploads/initiate/"


def _confirm_url(upload_id):
    return f"/api/v1/media/uploads/{upload_id}/confirm/"


@patch("apps.media.storage_backends.S3MediaStorage._client")
class StatusMediaUploadTests(APITestCase):
    """Covers the direct-to-S3 presigned-upload path for status media
    (apps/statuses/status_media.py + StatusCreateSerializer's `media_id`
    field) — the generic initiate/confirm views already have their own
    coverage in apps/media/tests.py; these tests focus on the status-specific
    attach step: context matching, one-time-use, moderation gating, and the
    authorized media-url retrieval endpoint."""

    def setUp(self):
        self.author = User.objects.create_user(
            phone="+2348000000201", password="password123", country="NG", display_name="Author",
        )
        self.contact = User.objects.create_user(
            phone="+2348000000202", password="password123", country="NG", display_name="Contact",
        )
        self.stranger = User.objects.create_user(
            phone="+2348000000203", password="password123", country="NG", display_name="Stranger",
        )
        UserContact.objects.create(
            user=self.author, contact_user=self.contact,
            contact_phone=self.contact.phone, contact_phone_number=self.contact.phone,
            contact_display_name=self.contact.display_name,
        )
        UserContact.objects.create(
            user=self.contact, contact_user=self.author,
            contact_phone=self.author.phone, contact_phone_number=self.author.phone,
            contact_display_name=self.author.display_name,
        )

    def _initiate_and_confirm(self, mock_client, *, context="status_image", content_type="image/jpeg", **overrides):
        client = _mock_s3_client()
        client.head_object.return_value = {"ContentLength": 1_000_000, "ContentType": content_type}
        mock_client.return_value = client
        self.client.force_authenticate(self.author)
        body = {
            "context": context,
            "filename": "status.jpg",
            "content_type": content_type,
            "size_bytes": 1_000_000,
        }
        body.update(overrides)
        initiate = self.client.post(INITIATE_URL, body, format="json")
        assert initiate.status_code == 201, initiate.data
        upload_id = initiate.data["uploadId"]
        confirm = self.client.post(_confirm_url(upload_id), {}, format="json")
        assert confirm.status_code == 200, confirm.data
        return confirm.data["mediaId"]

    def test_status_created_from_confirmed_media_binds_object_key(self, mock_client):
        media_id = self._initiate_and_confirm(mock_client)

        res = self.client.post(
            "/api/v1/statuses/",
            {"type": StatusType.IMAGE, "media_id": media_id, "visibility": StatusVisibility.CONTACTS},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        created = StatusItem.objects.get(id=res.data["id"])
        intent = MediaUploadIntent.objects.get(id=media_id)
        self.assertEqual(created.file.name, intent.object_key)
        self.assertIsNotNone(intent.attached_at)

    def test_document_status_binds_object_key_and_preserves_original_filename(self, mock_client):
        media_id = self._initiate_and_confirm(
            mock_client,
            context="status_document",
            content_type="application/pdf",
            filename="Sermon Notes.pdf",
        )

        res = self.client.post(
            "/api/v1/statuses/",
            {"type": StatusType.DOCUMENT, "media_id": media_id, "visibility": StatusVisibility.CONTACTS},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        created = StatusItem.objects.get(id=res.data["id"])
        intent = MediaUploadIntent.objects.get(id=media_id)
        self.assertEqual(created.file.name, intent.object_key)
        self.assertEqual(created.original_filename, "Sermon Notes.pdf")
        # Not visually scannable (see SCANNABLE_STATUS_TYPES) - passes
        # immediately like audio/text, never queued for review.
        self.assertEqual(created.moderation_status, StatusModerationStatus.PASSED)
        # original_filename round-trips through the read serializer (list/
        # search), not the create response - StatusCreateSerializer only
        # returns write-relevant fields.
        list_res = self.client.get("/api/v1/statuses/")
        mine = next(entry for entry in list_res.data["results"] if entry["user"]["id"] == str(self.author.id))
        self.assertEqual(mine["items"][0]["original_filename"], "Sermon Notes.pdf")

    def test_status_media_context_must_match_declared_type(self, mock_client):
        media_id = self._initiate_and_confirm(
            mock_client, context="status_video", content_type="video/mp4", filename="status.mp4",
        )

        res = self.client.post(
            "/api/v1/statuses/",
            {"type": StatusType.IMAGE, "media_id": media_id, "visibility": StatusVisibility.CONTACTS},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(StatusItem.objects.filter(user=self.author).exists())

    def test_confirmed_media_cannot_be_attached_twice(self, mock_client):
        media_id = self._initiate_and_confirm(mock_client)
        first = self.client.post(
            "/api/v1/statuses/",
            {"type": StatusType.IMAGE, "media_id": media_id, "visibility": StatusVisibility.CONTACTS},
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self.client.post(
            "/api/v1/statuses/",
            {"type": StatusType.IMAGE, "media_id": media_id, "visibility": StatusVisibility.CONTACTS},
            format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(MEDIA_EXPLICIT_SCAN_REQUIRED=True, MEDIA_SAFETY_ENABLED=True)
    def test_moderation_block_prevents_status_creation_and_leaves_media_unattached(self, mock_client):
        media_id = self._initiate_and_confirm(mock_client)

        res = self.client.post(
            "/api/v1/statuses/",
            {"type": StatusType.IMAGE, "media_id": media_id, "visibility": StatusVisibility.CONTACTS},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(StatusItem.objects.filter(user=self.author).exists())
        intent = MediaUploadIntent.objects.get(id=media_id)
        self.assertIsNone(intent.attached_at)
        scan = MediaSafetyScan.objects.get(context="status", upload_id=str(media_id))
        self.assertTrue(scan.quarantine)

    def test_media_url_requires_view_authorization(self, mock_client):
        media_id = self._initiate_and_confirm(mock_client)
        create = self.client.post(
            "/api/v1/statuses/",
            {"type": StatusType.IMAGE, "media_id": media_id, "visibility": StatusVisibility.CONTACTS},
            format="json",
        )
        status_id = create.data["id"]

        self.client.force_authenticate(self.contact)
        allowed = self.client.get(f"/api/v1/statuses/{status_id}/media-url/")
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertIn("mediaUrl", allowed.data)

        self.client.force_authenticate(self.stranger)
        denied = self.client.get(f"/api/v1/statuses/{status_id}/media-url/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    def test_media_url_404_for_unknown_status(self, mock_client):
        self.client.force_authenticate(self.author)
        res = self.client.get("/api/v1/statuses/00000000-0000-0000-0000-000000000000/media-url/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class StatusContentSafetyScanTests(APITestCase):
    """P0 fix: both status upload paths called scan_upload_for_explicit_
    content() with no file_path/storage_path at all, which — per that
    function's own routing (apps/media/safety.py) — can never produce a
    real detection; with live provider calls enabled it would instead
    reject every single media status outright. Fixed by moving the scan
    to after the file exists at a real storage location and routing
    through scan_saved_upload_for_explicit_content() (the same shared
    entry point the "4-of-5-sites" pass already uses elsewhere).

    Mocks apps.statuses.status_media.scan_saved_upload_for_explicit_content
    directly — that's the actual call this fix wires up — rather than the
    real NudeNet model or content-safety service, since exercising real
    inference isn't available in this environment (no GPU/model weights)
    and isn't the point of this test: the point is that a real storage_path
    is now passed in, and that the resulting decision correctly gates
    StatusItem creation/visibility."""

    def setUp(self):
        self.author = User.objects.create_user(
            phone="+2348000000201", password="password123", country="NG", display_name="Scanned Author",
        )
        self.contact = User.objects.create_user(
            phone="+2348000000202", password="password123", country="NG", display_name="Contact",
        )
        UserContact.objects.create(
            user=self.author, contact_user=self.contact,
            contact_phone=self.contact.phone, contact_phone_number=self.contact.phone,
            contact_display_name=self.contact.display_name,
        )
        UserContact.objects.create(
            user=self.contact, contact_user=self.author,
            contact_phone=self.author.phone, contact_phone_number=self.author.phone,
            contact_display_name=self.author.display_name,
        )
        # APITestCase already provides self.client as a real APIClient —
        # no need to construct one separately.

    def _post_image(self):
        upload = SimpleUploadedFile("photo.jpg", b"fake-jpeg-bytes", content_type="image/jpeg")
        self.client.force_authenticate(self.author)
        return self.client.post(
            "/api/v1/statuses/", {"type": "image", "file": upload, "visibility": "contacts"}, format="multipart",
        )

    def _post_video(self):
        upload = SimpleUploadedFile("clip.mp4", b"fake-mp4-bytes", content_type="video/mp4")
        self.client.force_authenticate(self.author)
        return self.client.post(
            "/api/v1/statuses/", {"type": "video", "file": upload, "visibility": "contacts"}, format="multipart",
        )

    def _post_audio(self):
        upload = SimpleUploadedFile("clip.m4a", b"fake-m4a-bytes", content_type="audio/m4a")
        self.client.force_authenticate(self.author)
        return self.client.post(
            "/api/v1/statuses/", {"type": "audio", "file": upload, "visibility": "contacts"}, format="multipart",
        )

    def _decision(self, *, status_, quarantine, reason, requires_review=False):
        from apps.media.safety import MediaSafetyDecision

        return MediaSafetyDecision(
            status=status_, quarantine=quarantine, provider="nudenet", reason=reason,
            user_message="test message", requires_review=requires_review,
        )

    @patch("apps.statuses.status_media.scan_saved_upload_for_explicit_content")
    def test_blocked_image_is_rejected_and_never_created(self, mock_scan):
        mock_scan.return_value = self._decision(status_="blocked", quarantine=True, reason="nudenet_explicit:TEST")
        response = self._post_image()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StatusItem.objects.count(), 0)
        # The scan really was called with a real, on-storage path — not
        # skipped, not called with file_path=None like the pre-fix code.
        self.assertTrue(mock_scan.called)
        self.assertTrue(mock_scan.call_args.kwargs.get("storage_path"))

    @patch("apps.statuses.status_media.scan_saved_upload_for_explicit_content")
    def test_passed_image_is_created_and_visible(self, mock_scan):
        mock_scan.return_value = self._decision(status_="passed", quarantine=False, reason="nudenet_clean")
        response = self._post_image()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        item = StatusItem.objects.get(id=response.data["id"])
        self.assertEqual(item.moderation_status, "passed")

        self.client.force_authenticate(self.contact)
        listing = self.client.get("/api/v1/statuses/")
        author_entry = next((e for e in listing.data["results"] if e["user"]["id"] == str(self.author.id)), None)
        self.assertIsNotNone(author_entry, "passed status should be visible to a contact")

    @patch("apps.statuses.status_media.scan_saved_upload_for_explicit_content")
    def test_queued_video_is_pending_and_hidden_from_others_but_visible_to_author(self, mock_scan):
        from apps.statuses.status_media import NUDENET_SCAN_QUEUED_REASON

        mock_scan.return_value = self._decision(
            status_="pending_review", quarantine=True, reason=NUDENET_SCAN_QUEUED_REASON, requires_review=True,
        )
        with patch("apps.media.tasks.scan_video_and_resolve_task.delay") as mock_delay:
            response = self._post_video()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        item = StatusItem.objects.get(id=response.data["id"])
        self.assertEqual(item.moderation_status, "pending_review")
        # The real point of this fix: quarantined/pending content must not
        # silently become publicly visible — a genuinely async decision
        # enqueues the same resolver task the other 3 video call sites use.
        mock_delay.assert_called_once()
        scan = MediaSafetyScan.objects.filter(result__resolution_id=str(item.id)).first()
        self.assertIsNotNone(scan)
        self.assertEqual(scan.result.get("resolution_target"), "status_item")

        self.client.force_authenticate(self.contact)
        listing = self.client.get("/api/v1/statuses/")
        author_entry = next((e for e in listing.data["results"] if e["user"]["id"] == str(self.author.id)), None)
        self.assertIsNone(author_entry, "pending_review video must not be visible to a contact yet")

        self.client.force_authenticate(self.author)
        own_listing = self.client.get("/api/v1/statuses/")
        own_entry = next(e for e in own_listing.data["results"] if e["user"]["id"] == str(self.author.id))
        self.assertEqual(len(own_entry["items"]), 1, "author must still see their own pending status")

    @patch("apps.statuses.status_media.scan_saved_upload_for_explicit_content")
    def test_async_resolution_flips_pending_video_to_passed_and_visible(self, mock_scan):
        from apps.statuses.status_media import NUDENET_SCAN_QUEUED_REASON

        mock_scan.return_value = self._decision(
            status_="pending_review", quarantine=True, reason=NUDENET_SCAN_QUEUED_REASON, requires_review=True,
        )
        with patch("apps.media.tasks.scan_video_and_resolve_task.delay"):
            response = self._post_video()
        item_id = response.data["id"]
        scan = MediaSafetyScan.objects.get(result__resolution_id=item_id)

        # ContentSafetyProvider is imported locally inside
        # scan_video_and_resolve_task (not at apps.media.tasks module
        # level), so it must be patched at its defining module instead —
        # the function-local `from .content_safety_provider import
        # ContentSafetyProvider` still resolves to this patched object
        # since the patch is active before the task runs.
        with patch("apps.media.content_safety_provider.ContentSafetyProvider") as mock_provider_cls:
            mock_provider_cls.return_value.scan.return_value = (None, 0.0)  # clean
            from apps.media.tasks import scan_video_and_resolve_task

            scan_video_and_resolve_task.run(scan_id=str(scan.id))

        item = StatusItem.objects.get(id=item_id)
        self.assertEqual(item.moderation_status, "passed")

        self.client.force_authenticate(self.contact)
        listing = self.client.get("/api/v1/statuses/")
        author_entry = next((e for e in listing.data["results"] if e["user"]["id"] == str(self.author.id)), None)
        self.assertIsNotNone(author_entry, "resolved-clean video should now be visible to a contact")

    @patch("apps.statuses.status_media.scan_saved_upload_for_explicit_content")
    def test_audio_status_never_invokes_visual_scan(self, mock_scan):
        response = self._post_audio()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        item = StatusItem.objects.get(id=response.data["id"])
        self.assertEqual(item.moderation_status, "passed")
        mock_scan.assert_not_called()

    @patch("apps.statuses.status_media.scan_saved_upload_for_explicit_content")
    def test_text_status_never_invokes_visual_scan(self, mock_scan):
        self.client.force_authenticate(self.author)
        response = self.client.post(
            "/api/v1/statuses/", {"type": "text", "text": "hello world", "visibility": "contacts"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        mock_scan.assert_not_called()
