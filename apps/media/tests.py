from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from rest_framework.test import APITestCase
from urllib.parse import urlparse

from .models import MediaAsset, MediaSafetyScan, ProcessingJob


class MediaJobOwnershipTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="+237670003001", password="TestPass123!", country="CM")
        self.other = User.objects.create_user(phone="+237670003002", password="TestPass123!", country="CM")
        self.owner_asset = MediaAsset.objects.create(
            owner=self.owner,
            type="image",
            bucket_key="uploads/owner/image.jpg",
            status="ready",
            mime_type="image/jpeg",
        )
        self.other_asset = MediaAsset.objects.create(
            owner=self.other,
            type="image",
            bucket_key="uploads/other/image.jpg",
            status="ready",
            mime_type="image/jpeg",
        )
        self.owner_job = ProcessingJob.objects.create(asset=self.owner_asset, pipeline="analyze")
        ProcessingJob.objects.create(asset=self.other_asset, pipeline="analyze")
        self.client.force_authenticate(self.owner)

    def test_processing_jobs_are_limited_to_asset_owner(self):
        response = self.client.get("/api/v1/jobs/")

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["id"]), str(self.owner_job.id))


class PrivateMediaAccessTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="+237670003101", password="TestPass123!", country="CM")
        self.other = User.objects.create_user(phone="+237670003102", password="TestPass123!", country="CM")
        self.private_key = default_storage.save(
            "uploads/security-tests/private-owner.txt",
            ContentFile(b"private media"),
        )
        self.private_asset = MediaAsset.objects.create(
            owner=self.owner,
            type="document",
            bucket_key=self.private_key,
            status="ready",
            mime_type="text/plain",
            storage={"visibility": "private", "scan_status": "passed"},
        )
        self.public_asset = MediaAsset.objects.create(
            owner=self.owner,
            type="document",
            bucket_key="uploads/security-tests/public.txt",
            status="ready",
            mime_type="text/plain",
            storage={"visibility": "public"},
        )

    def tearDown(self):
        if default_storage.exists(self.private_key):
            default_storage.delete(self.private_key)

    def test_private_media_download_denies_anonymous_user(self):
        response = self.client.get(f"/api/v1/assets/{self.private_asset.id}/download/")

        self.assertEqual(response.status_code, 403)

    def test_private_media_download_denies_non_owner(self):
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/v1/assets/{self.private_asset.id}/download/")

        self.assertEqual(response.status_code, 403)

    def test_owner_can_create_signed_private_media_url_and_download(self):
        self.client.force_authenticate(self.owner)

        sign_response = self.client.post(f"/api/v1/assets/{self.private_asset.id}/sign/")

        self.assertEqual(sign_response.status_code, 200)
        signed_url = sign_response.data["signed_url"]
        parsed = urlparse(signed_url)

        self.client.force_authenticate(user=None)
        download_response = self.client.get(f"{parsed.path}?{parsed.query}")

        self.assertEqual(download_response.status_code, 200)
        body = b"".join(download_response.streaming_content)
        self.assertEqual(body, b"private media")

    def test_anonymous_asset_list_hides_explicit_private_media(self):
        response = self.client.get("/api/v1/assets/")

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        asset_ids = {str(row["id"]) for row in rows}
        self.assertNotIn(str(self.private_asset.id), asset_ids)
        self.assertIn(str(self.public_asset.id), asset_ids)


    def test_media_asset_serializer_hides_bucket_key_from_non_staff_owner(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/assets/{self.private_asset.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("bucket_key", response.data)
        self.assertTrue(response.data["private"])
        self.assertIn("/api/v1/assets/", response.data["display_url"])

    def test_staff_media_asset_serializer_can_inspect_bucket_key(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/assets/{self.private_asset.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["bucket_key"], self.private_key)

    def test_primary_media_asset_route_still_serves_download(self):
        self.client.force_authenticate(self.owner)

        sign_response = self.client.post(f"/api/v1/media/assets/{self.private_asset.id}/sign/")

        self.assertEqual(sign_response.status_code, 200)
        self.client.force_authenticate(user=None)
        parsed = urlparse(sign_response.data["signed_url"])
        response = self.client.get(f"{parsed.path}?{parsed.query}")

        self.assertEqual(response.status_code, 200)

    def test_legacy_media_asset_route_remains_available(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/assets/{self.private_asset.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.data["id"]), str(self.private_asset.id))


class MediaSafetyUploadTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670003201", password="TestPass123!", country="CM")
        self.client.force_authenticate(self.user)

    @override_settings(MEDIA_EXPLICIT_SCAN_REQUIRED=True, MEDIA_SAFETY_ENABLED=True)
    def test_upload_creates_quarantined_safety_scan_when_scan_required(self):
        upload = SimpleUploadedFile("family-photo.jpg", b"safe image bytes", content_type="image/jpeg")

        response = self.client.post(
            "/uploads/file",
            {"file": upload, "context": "chat", "visibility": "private"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        attachment = response.data["attachment"]
        self.assertEqual(attachment["scanStatus"], "pending_review")
        self.assertTrue(attachment["quarantined"])
        self.assertTrue(attachment["requiresReview"])
        self.assertEqual(attachment["url"], "")
        self.assertEqual(attachment["safety"]["policyVersion"], "kis-christian-safety-v1")
        scan = MediaSafetyScan.objects.get(upload_id=attachment["id"])
        self.assertEqual(scan.context, "chat")
        self.assertEqual(scan.status, "pending_review")
        self.assertTrue(scan.quarantine)
        self.assertEqual(scan.owner, self.user)

    @override_settings(MEDIA_EXPLICIT_SCAN_REQUIRED=True, MEDIA_SAFETY_ENABLED=True)
    def test_chat_upload_records_safe_audit_context_without_paths_or_secrets(self):
        upload = SimpleUploadedFile("family-photo.jpg", b"safe image bytes", content_type="image/jpeg")

        response = self.client.post(
            "/uploads/file?conversationId=conv-1&clientId=client-1&device_id=device-1",
            {"file": upload, "context": "dm", "visibility": "private"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        scan = MediaSafetyScan.objects.get(upload_id=response.data["attachment"]["id"])
        self.assertEqual(scan.context, "dm")
        self.assertEqual(scan.result["conversation_id"], "conv-1")
        self.assertEqual(scan.result["client_id"], "client-1")
        self.assertIs(scan.result["device_id_present"], True)
        self.assertNotIn("bucket_key", scan.result)
        self.assertNotIn("path", scan.result)

    @override_settings(MEDIA_EXPLICIT_SCAN_REQUIRED=False, MEDIA_SAFETY_ENABLED=True)
    def test_upload_remains_usable_in_local_style_configuration(self):
        upload = SimpleUploadedFile("family-photo.jpg", b"safe image bytes", content_type="image/jpeg")

        response = self.client.post(
            "/uploads/file",
            {"file": upload, "context": "profile", "visibility": "private"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        attachment = response.data["attachment"]
        self.assertEqual(attachment["scanStatus"], "not_configured")
        self.assertFalse(attachment["quarantined"])
        scan = MediaSafetyScan.objects.get(upload_id=attachment["id"])
        self.assertEqual(scan.context, "profile")
        self.assertEqual(scan.status, "not_configured")

        self.assertTrue(attachment["private"])
        self.assertEqual(attachment["url"], attachment["displayUrl"])
        self.assertEqual(attachment["url"], attachment["downloadUrl"])
        self.assertIn("/api/v1/assets/", attachment["url"])
        self.assertEqual(attachment["assetId"], attachment["mediaAssetId"])
        self.assertEqual(attachment["mediaAssetRef"], attachment["mediaAssetId"])
        self.assertNotIn("bucket_key", str(attachment))
        self.assertNotIn("uploads/", str(attachment["asset"]))

    def test_upload_blocks_dangerous_extension_before_storage(self):
        upload = SimpleUploadedFile("payload.sh", b"echo unsafe", content_type="text/plain")

        response = self.client.post(
            "/uploads/file",
            {"file": upload, "context": "chat", "visibility": "private"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(MediaSafetyScan.objects.count(), 0)

    def test_upload_blocks_generic_octet_stream_before_storage(self):
        upload = SimpleUploadedFile("photo.jpg", b"unsafe generic bytes", content_type="application/octet-stream")

        response = self.client.post(
            "/uploads/file",
            {"file": upload, "context": "chat", "visibility": "private"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("generic MIME type", str(response.data))
        self.assertEqual(MediaSafetyScan.objects.count(), 0)

    def test_upload_blocks_mismatched_extension_and_mime_before_storage(self):
        upload = SimpleUploadedFile("photo.pdf", b"fake image bytes", content_type="image/jpeg")

        response = self.client.post(
            "/uploads/file",
            {"file": upload, "context": "profile", "visibility": "private"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("extension does not match", str(response.data))
        self.assertEqual(MediaSafetyScan.objects.count(), 0)

    @override_settings(MEDIA_SAFETY_MAX_UPLOAD_BYTES=4)
    def test_upload_blocks_oversized_file_before_storage(self):
        upload = SimpleUploadedFile("photo.jpg", b"too-large", content_type="image/jpeg")

        response = self.client.post(
            "/uploads/file",
            {"file": upload, "context": "profile", "visibility": "private"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("File too large", str(response.data))
        self.assertEqual(MediaSafetyScan.objects.count(), 0)

    def test_safety_scan_list_is_limited_to_owner(self):
        other = get_user_model().objects.create_user(phone="+237670003202", password="TestPass123!", country="CM")
        own_scan = MediaSafetyScan.objects.create(owner=self.user, upload_id="own", context="chat", status="pending_review")
        MediaSafetyScan.objects.create(owner=other, upload_id="other", context="chat", status="pending_review")

        response = self.client.get("/api/v1/media-safety-scans/")

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["id"]), str(own_scan.id))
