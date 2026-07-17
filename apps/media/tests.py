from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from urllib.parse import urlparse

from .models import MediaAsset, MediaSafetyScan, MediaUploadIntent, ProcessingJob
from .upload_intent import expire_abandoned_upload_intents


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


def _mock_s3_client(**overrides):
    """A MagicMock standing in for S3MediaStorage._client()'s boto3 client.
    All tests in MediaUploadIntentTests patch S3MediaStorage._client with
    this — no test in this class ever talks to real AWS."""
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://mock-bucket.s3.mock-region.amazonaws.com/mock-signed?X-Amz-Signature=redacted"
    # Matches _initiate()'s default size_bytes=1_000_000 so tests that don't
    # care about size stay within the confirm flow's declared/actual size
    # tolerance instead of accidentally tripping size_mismatch.
    client.head_object.return_value = {"ContentLength": 1_000_000, "ContentType": "image/jpeg"}
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


@patch("apps.media.storage_backends.S3MediaStorage._client")
class MediaUploadIntentTests(APITestCase):
    """Covers apps/media/upload_intent.py end to end. Every S3 interaction
    goes through the mocked boto3 client injected by the class-level patch
    (S3MediaStorage._client) — real AWS is never called."""

    INITIATE_URL = "/api/v1/media/uploads/profile-image/initiate/"

    def _confirm_url(self, upload_id):
        return f"/api/v1/media/uploads/{upload_id}/confirm/"

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670004001", password="TestPass123!", country="CM")
        self.other = User.objects.create_user(phone="+237670004002", password="TestPass123!", country="CM")

    def _initiate(self, *, size_bytes=1_000_000, content_type="image/jpeg", filename="profile.jpg", kind="avatar"):
        self.client.force_authenticate(self.user)
        return self.client.post(
            self.INITIATE_URL,
            {"filename": filename, "content_type": content_type, "size_bytes": size_bytes, "kind": kind},
            format="json",
        )

    # ---- initiate ----

    def test_initiate_requires_authentication(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        self.client.force_authenticate(None)
        response = self.client.post(
            self.INITIATE_URL,
            {"filename": "a.jpg", "content_type": "image/jpeg", "size_bytes": 1000},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_valid_initiate_returns_presigned_put_without_credentials(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate()

        self.assertEqual(response.status_code, 201)
        body = response.data
        self.assertIn("upload_id", body)
        self.assertEqual(body["upload_url"], "https://mock-bucket.s3.mock-region.amazonaws.com/mock-signed?X-Amz-Signature=redacted")
        self.assertEqual(body["required_headers"], {"Content-Type": "image/jpeg"})
        self.assertEqual(body["expires_in"], 600)
        # No AWS key/secret anywhere in the response.
        serialized = str(body)
        self.assertNotIn("AKIA", serialized)
        self.assertNotIn("aws_secret", serialized.lower())

    def test_initiate_rejects_unsupported_mime_type(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(content_type="application/x-msdownload")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MediaUploadIntent.objects.count(), 0)

    def test_initiate_rejects_oversized_file(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        with override_settings(PROFILE_IMAGE_MAX_UPLOAD_BYTES=1000):
            response = self._initiate(size_bytes=5000)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MediaUploadIntent.objects.count(), 0)

    def test_initiate_rejects_non_positive_size(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(size_bytes=0)
        self.assertEqual(response.status_code, 400)

    def test_initiate_generates_server_side_key_never_trusting_client_filename(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(filename="../../etc/passwd.jpg")

        self.assertEqual(response.status_code, 201)
        object_key = response.data["object_key"]
        # Server-chosen prefix/shape, own user id, uuid-based filename — the
        # client's filename never appears in the key.
        self.assertTrue(object_key.startswith(f"private/profile-images/{self.user.id}/"))
        self.assertNotIn("etc/passwd", object_key)
        self.assertNotIn("..", object_key)
        intent = MediaUploadIntent.objects.get(id=response.data["upload_id"])
        self.assertEqual(intent.object_key, object_key)
        self.assertEqual(intent.owner_id, self.user.id)

    def test_presign_expiry_setting_is_respected(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        with override_settings(PROFILE_IMAGE_PRESIGN_EXPIRY_SECONDS=120):
            response = self._initiate()
        self.assertEqual(response.data["expires_in"], 120)

    # ---- confirm ----

    def test_confirm_requires_authentication(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate()
        upload_id = response.data["upload_id"]

        self.client.force_authenticate(None)
        response = self.client.post(self._confirm_url(upload_id), {}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_confirm_rejects_missing_s3_object(self, mock_client):
        client = _mock_s3_client()
        client.head_object.side_effect = Exception("404 Not Found")
        mock_client.return_value = client
        response = self._initiate()
        upload_id = response.data["upload_id"]

        confirm_response = self.client.post(self._confirm_url(upload_id), {}, format="json")

        self.assertEqual(confirm_response.status_code, 400)
        intent = MediaUploadIntent.objects.get(id=upload_id)
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_FAILED)
        self.assertEqual(intent.error_code, "object_missing")

    def test_confirm_rejects_oversized_actual_object(self, mock_client):
        client = _mock_s3_client()
        client.head_object.return_value = {"ContentLength": 50 * 1024 * 1024, "ContentType": "image/jpeg"}
        mock_client.return_value = client
        response = self._initiate(size_bytes=1_000_000)
        upload_id = response.data["upload_id"]

        confirm_response = self.client.post(self._confirm_url(upload_id), {}, format="json")

        self.assertEqual(confirm_response.status_code, 400)
        intent = MediaUploadIntent.objects.get(id=upload_id)
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_FAILED)

    def test_confirm_rejects_invalid_stored_mime_type(self, mock_client):
        client = _mock_s3_client()
        # Isolate the content-type check: keep ContentLength within the
        # declared-size tolerance so this doesn't also trip size_mismatch.
        client.head_object.return_value = {"ContentLength": 1_000_000, "ContentType": "application/octet-stream"}
        mock_client.return_value = client
        response = self._initiate()
        upload_id = response.data["upload_id"]

        confirm_response = self.client.post(self._confirm_url(upload_id), {}, format="json")

        self.assertEqual(confirm_response.status_code, 400)
        intent = MediaUploadIntent.objects.get(id=upload_id)
        self.assertEqual(intent.error_code, "content_type_mismatch")

    def test_confirm_rejects_expired_upload(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate()
        upload_id = response.data["upload_id"]
        MediaUploadIntent.objects.filter(id=upload_id).update(expires_at=timezone.now() - timedelta(seconds=1))

        confirm_response = self.client.post(self._confirm_url(upload_id), {}, format="json")

        self.assertEqual(confirm_response.status_code, 400)
        intent = MediaUploadIntent.objects.get(id=upload_id)
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_EXPIRED)

    def test_another_user_cannot_confirm_the_upload(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate()
        upload_id = response.data["upload_id"]

        self.client.force_authenticate(self.other)
        confirm_response = self.client.post(self._confirm_url(upload_id), {}, format="json")

        self.assertEqual(confirm_response.status_code, 404)
        intent = MediaUploadIntent.objects.get(id=upload_id)
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_PENDING)

    def test_successful_confirm_updates_profile_avatar_and_deletes_old_image_after_success(self, mock_client):
        from apps.accounts.models import Profile

        profile, _ = Profile.objects.get_or_create(user=self.user)
        profile.avatar_file.name = "profiles/old/avatar/old.jpg"
        profile.save(update_fields=["avatar_file"])

        client = _mock_s3_client()
        mock_client.return_value = client
        response = self._initiate()
        upload_id = response.data["upload_id"]
        new_key = response.data["object_key"]

        confirm_response = self.client.post(self._confirm_url(upload_id), {}, format="json")

        self.assertEqual(confirm_response.status_code, 200)
        self.assertEqual(confirm_response.data["status"], "confirmed")
        profile.refresh_from_db()
        self.assertEqual(profile.avatar_file.name, new_key)
        # Old object deleted only after the new one was attached successfully.
        client.delete_object.assert_called_once()
        _, delete_kwargs = client.delete_object.call_args
        self.assertEqual(delete_kwargs["Key"], "profiles/old/avatar/old.jpg")
        intent = MediaUploadIntent.objects.get(id=upload_id)
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_CONFIRMED)
        self.assertIsNotNone(intent.confirmed_at)

    def test_repeated_confirm_is_idempotent(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate()
        upload_id = response.data["upload_id"]

        first = self.client.post(self._confirm_url(upload_id), {}, format="json")
        second = self.client.post(self._confirm_url(upload_id), {}, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["status"], "confirmed")

    def test_cover_kind_updates_cover_file(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(kind="cover")
        upload_id = response.data["upload_id"]
        new_key = response.data["object_key"]
        self.assertIn("profile-images", new_key)

        confirm_response = self.client.post(self._confirm_url(upload_id), {}, format="json")

        self.assertEqual(confirm_response.status_code, 200)
        from apps.accounts.models import Profile

        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.cover_file.name, new_key)

    # ---- cleanup ----

    def test_expire_abandoned_upload_intents_ignores_confirmed_uploads(self, mock_client):
        client = _mock_s3_client()
        mock_client.return_value = client
        response = self._initiate()
        upload_id = response.data["upload_id"]
        self.client.post(self._confirm_url(upload_id), {}, format="json")
        # Force the (now-confirmed) intent's expires_at into the past — it
        # must still be left alone by the sweep.
        MediaUploadIntent.objects.filter(id=upload_id).update(expires_at=timezone.now() - timedelta(days=1))

        result = expire_abandoned_upload_intents()

        self.assertEqual(result["expired_count"], 0)
        intent = MediaUploadIntent.objects.get(id=upload_id)
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_CONFIRMED)

    def test_expire_abandoned_upload_intents_expires_pending_past_ttl(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate()
        upload_id = response.data["upload_id"]
        MediaUploadIntent.objects.filter(id=upload_id).update(expires_at=timezone.now() - timedelta(seconds=1))

        result = expire_abandoned_upload_intents()

        self.assertEqual(result["expired_count"], 1)
        intent = MediaUploadIntent.objects.get(id=upload_id)
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_EXPIRED)
