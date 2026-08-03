from unittest.mock import patch

from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User, UserContact
from apps.media.models import MediaSafetyScan, MediaUploadIntent
from apps.media.tests import _mock_s3_client
from apps.moderation.models import AuditLog, Flag, UserBlock
from apps.statuses.models import (
    StatusAudienceTarget,
    StatusItem,
    StatusItemView,
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
