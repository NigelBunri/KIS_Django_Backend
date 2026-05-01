from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework.test import APITestCase
from urllib.parse import urlparse

from .models import MediaAsset, ProcessingJob


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
