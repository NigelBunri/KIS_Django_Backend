from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User, UserContact
from apps.moderation.models import AuditLog, Flag, UserBlock
from apps.statuses.models import (
    StatusAudienceTarget,
    StatusItem,
    StatusMute,
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
        res = self.client.get(reverse("status-list"))

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

        res = self.client.get(reverse("status-list"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        author_entries = [
            entry for entry in res.json()["results"] if entry["user"]["id"] == str(self.author.id)
        ]
        self.assertEqual(author_entries, [])
        mark_view = self.client.post(reverse("status-view", kwargs={"pk": status_item.id}))
        self.assertEqual(mark_view.status_code, status.HTTP_403_FORBIDDEN)

    def test_only_share_with_requires_target_membership(self):
        self._create_status(
            author=self.author,
            visibility=StatusVisibility.ONLY_SHARE_WITH,
            targets=[self.viewer],
        )
        self.client.force_authenticate(self.viewer)
        allowed = self.client.get(reverse("status-list"))
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(len(allowed.json()["results"]), 1)

        self.client.force_authenticate(self.excluded)
        denied = self.client.get(reverse("status-list"))
        author_entries = [
            entry for entry in denied.json()["results"] if entry["user"]["id"] == str(self.author.id)
        ]
        self.assertEqual(author_entries, [])

    def test_mute_and_block_remove_author_from_status_feed(self):
        self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        mute_res = self.client.post(reverse("status-mute"), {"user_id": str(self.author.id)}, format="json")
        self.assertEqual(mute_res.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StatusMute.objects.filter(user=self.viewer, muted_user=self.author).exists()
        )
        muted_list = self.client.get(reverse("status-list"))
        author_entries = [
            entry for entry in muted_list.json()["results"] if entry["user"]["id"] == str(self.author.id)
        ]
        self.assertEqual(author_entries, [])

        self.client.post(reverse("status-unmute"), {"user_id": str(self.author.id)}, format="json")
        UserBlock.objects.create(blocker=self.viewer, blocked=self.author, reason="status_block")
        blocked_list = self.client.get(reverse("status-list"))
        author_entries = [
            entry for entry in blocked_list.json()["results"] if entry["user"]["id"] == str(self.author.id)
        ]
        self.assertEqual(author_entries, [])

    def test_report_creates_status_flag(self):
        status_item = self._create_status(author=self.author)
        self.client.force_authenticate(self.viewer)

        res = self.client.post(
            reverse("status-report", kwargs={"pk": status_item.id}),
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

        res = self.client.get(reverse("status-search"), {"q": "Launch checklist"})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        rows = res.json()["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(matching.id))
        self.assertEqual(rows[0]["view_count"], 1)

    def test_viewers_endpoint_is_owner_only(self):
        status_item = self._create_status(author=self.author)
        StatusItemView.objects.create(status=status_item, user=self.viewer)

        self.client.force_authenticate(self.author)
        owner_res = self.client.get(reverse("status-viewers", kwargs={"pk": status_item.id}))
        self.assertEqual(owner_res.status_code, status.HTTP_200_OK)
        self.assertEqual(owner_res.json()["view_count"], 1)
        self.assertEqual(owner_res.json()["results"][0]["id"], str(self.viewer.id))

        self.client.force_authenticate(self.viewer)
        viewer_res = self.client.get(reverse("status-viewers", kwargs={"pk": status_item.id}))
        self.assertEqual(viewer_res.status_code, status.HTTP_404_NOT_FOUND)
