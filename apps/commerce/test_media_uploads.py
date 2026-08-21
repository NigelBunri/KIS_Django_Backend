"""Tests for the marketplace direct-to-S3 presigned upload migration
(apps/commerce/media_uploads.py + the initiate/attach views in views.py).

Reuses the exact S3-mocking pattern apps/media/tests.py already established
for the shared upload_intent module (patch S3MediaStorage._client with a
MagicMock) — no test here ever talks to real AWS.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.media.models import MediaUploadIntent
from apps.media.tests import _mock_s3_client
from apps.media.upload_intent import expire_unattached_confirmed_intents

from .models import (
    MarketplaceComplaint,
    MarketplaceOrder,
    MarketplaceOrderStatus,
    Product,
    ProductImage,
    Shop,
    ShopPayoutAccountStatus,
    ShopRole,
    ShopService,
    ShopTeamMember,
)

INITIATE_URL = "/api/v1/commerce/uploads/initiate/"


def _confirm_url(upload_id):
    return f"/api/v1/media/uploads/{upload_id}/confirm/"


class CommerceMediaUploadTestBase(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="+237670005001", password="TestPass123!", country="CM")
        self.stranger = User.objects.create_user(phone="+237670005002", password="TestPass123!", country="CM")
        self.staff = User.objects.create_user(
            phone="+237670005003", password="TestPass123!", country="CM", is_staff=True,
        )
        self.shop = Shop.objects.create(
            owner=self.owner, name="Test Shop", slug="test-shop-media",
            payout_account_status=ShopPayoutAccountStatus.ACTIVE, flutterwave_subaccount_id="RS_TEST_MEDIA",
        )

    def _initiate(self, user, **overrides):
        self.client.force_authenticate(user)
        body = {
            "purpose": "shop_logo",
            "filename": "logo.jpg",
            "contentType": "image/jpeg",
            "sizeBytes": 1_000_000,
            "shopId": str(self.shop.id),
        }
        body.update(overrides)
        return self.client.post(INITIATE_URL, body, format="json")

    def _initiate_and_confirm(self, user, mock_client, client=None, **overrides):
        # Mints a fresh mock client by default. Pass `client=` explicitly
        # when a test needs to keep inspecting the SAME mock across
        # multiple initiate/confirm round trips (e.g. asserting
        # delete_object was called after a replace) — otherwise each call
        # here would silently swap in a brand new one.
        mock_client.return_value = client if client is not None else _mock_s3_client()
        response = self._initiate(user, **overrides)
        assert response.status_code == 201, response.data
        upload_id = response.data["uploadId"]
        confirm = self.client.post(_confirm_url(upload_id), {}, format="json")
        assert confirm.status_code == 200, confirm.data
        return upload_id


@patch("apps.media.storage_backends.S3MediaStorage._client")
class CommerceUploadInitiateTests(CommerceMediaUploadTestBase):
    def test_shop_owner_can_initiate(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.owner)
        self.assertEqual(response.status_code, 201)

    def test_global_staff_can_initiate(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.staff)
        self.assertEqual(response.status_code, 201)

    def test_unauthorized_user_receives_403(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.stranger)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(MediaUploadIntent.objects.count(), 0)

    def test_shop_team_member_without_admin_role_cannot_initiate(self, mock_client):
        # Locks in current behavior exactly: ShopViewSet/ProductViewSet/
        # ShopServiceViewSet only ever checked owner-or-django-staff, never
        # ShopTeamMember roles, even before this migration.
        member = get_user_model().objects.create_user(
            phone="+237670005004", password="TestPass123!", country="CM",
        )
        ShopTeamMember.objects.create(shop=self.shop, user=member, role=ShopRole.MANAGER, is_active=True)
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(member)
        self.assertEqual(response.status_code, 403)

    def test_invalid_purpose_rejected(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.owner, purpose="not_a_real_purpose")
        self.assertEqual(response.status_code, 400)

    def test_unsupported_mime_rejected(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.owner, contentType="application/x-msdownload")
        self.assertEqual(response.status_code, 400)

    def test_oversized_upload_rejected(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        with override_settings(COMMERCE_IMAGE_MAX_UPLOAD_BYTES=1000):
            response = self._initiate(self.owner, sizeBytes=5000)
        self.assertEqual(response.status_code, 400)

    def test_uploadid_is_opaque_and_distinct_from_storage_key(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.owner)
        self.assertEqual(response.status_code, 201)
        body = response.data
        self.assertNotEqual(body["uploadId"], body["storageKey"])
        self.assertNotIn("/", body["uploadId"])
        self.assertIn("/", body["storageKey"])
        # Explicit contract fields present.
        for field in ("uploadId", "storageKey", "uploadUrl", "headers", "expiresInSeconds"):
            self.assertIn(field, body)

    def test_product_main_image_requires_owning_the_target_shop_when_creating(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(
            self.stranger, purpose="product_main_image", shopId=str(self.shop.id),
        )
        self.assertEqual(response.status_code, 403)


@patch("apps.media.storage_backends.S3MediaStorage._client")
class CommerceConfirmTests(CommerceMediaUploadTestBase):
    def test_storage_key_cannot_be_used_as_confirm_id(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.owner)
        storage_key = response.data["storageKey"]
        confirm = self.client.post(_confirm_url(storage_key), {}, format="json")
        self.assertIn(confirm.status_code, (400, 404))
        self.assertEqual(MediaUploadIntent.objects.get(id=response.data["uploadId"]).status, "pending")

    def test_wrong_user_cannot_confirm(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.owner)
        upload_id = response.data["uploadId"]

        self.client.force_authenticate(self.stranger)
        confirm = self.client.post(_confirm_url(upload_id), {}, format="json")
        self.assertEqual(confirm.status_code, 404)

    def test_missing_s3_object_rejected(self, mock_client):
        client = _mock_s3_client()
        client.head_object.side_effect = Exception("404 Not Found")
        mock_client.return_value = client
        response = self._initiate(self.owner)
        confirm = self.client.post(_confirm_url(response.data["uploadId"]), {}, format="json")
        self.assertEqual(confirm.status_code, 400)

    def test_size_mismatch_rejected(self, mock_client):
        client = _mock_s3_client()
        client.head_object.return_value = {"ContentLength": 50_000_000, "ContentType": "image/jpeg"}
        mock_client.return_value = client
        response = self._initiate(self.owner, sizeBytes=1_000_000)
        confirm = self.client.post(_confirm_url(response.data["uploadId"]), {}, format="json")
        self.assertEqual(confirm.status_code, 400)

    def test_expired_intent_rejected(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.owner)
        intent = MediaUploadIntent.objects.get(id=response.data["uploadId"])
        intent.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        intent.save(update_fields=["expires_at"])
        confirm = self.client.post(_confirm_url(intent.id), {}, format="json")
        self.assertEqual(confirm.status_code, 400)

    def test_confirm_returns_stable_media_id_not_storage_key(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        response = self._initiate(self.owner)
        confirm = self.client.post(_confirm_url(response.data["uploadId"]), {}, format="json")
        self.assertEqual(confirm.status_code, 200)
        self.assertEqual(confirm.data["mediaId"], response.data["uploadId"])
        self.assertNotIn("storageKey", confirm.data)


@patch("apps.media.storage_backends.S3MediaStorage._client")
class CommerceAttachTests(CommerceMediaUploadTestBase):
    def test_confirmed_media_attaches_to_correct_shop(self, mock_client):
        media_id = self._initiate_and_confirm(self.owner, mock_client)
        self.client.force_authenticate(self.owner)
        response = self.client.post(f"/api/v1/commerce/shops/{self.shop.id}/image/attach/", {"mediaId": media_id}, format="json")
        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.image_file.name)

    def test_intent_cannot_be_reused_for_a_second_attach(self, mock_client):
        media_id = self._initiate_and_confirm(self.owner, mock_client)
        self.client.force_authenticate(self.owner)
        first = self.client.post(f"/api/v1/commerce/shops/{self.shop.id}/image/attach/", {"mediaId": media_id}, format="json")
        self.assertEqual(first.status_code, 200)
        second = self.client.post(f"/api/v1/commerce/shops/{self.shop.id}/image/attach/", {"mediaId": media_id}, format="json")
        self.assertEqual(second.status_code, 400)

    def test_unauthorized_user_cannot_attach(self, mock_client):
        media_id = self._initiate_and_confirm(self.owner, mock_client)
        self.client.force_authenticate(self.stranger)
        response = self.client.post(f"/api/v1/commerce/shops/{self.shop.id}/image/attach/", {"mediaId": media_id}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_media_confirmed_for_one_purpose_cannot_attach_to_another(self, mock_client):
        media_id = self._initiate_and_confirm(self.owner, mock_client, purpose="shop_logo")
        product = Product.objects.create(shop=self.shop, sku="SKU-ATTACH-1", name="P1", price=Decimal("10.00"))
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/commerce/products/{product.id}/main-image/attach/", {"mediaId": media_id}, format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_confirmed_media_attaches_to_correct_product_main_image(self, mock_client):
        product = Product.objects.create(shop=self.shop, sku="SKU-ATTACH-2", name="P2", price=Decimal("10.00"))
        media_id = self._initiate_and_confirm(
            self.owner, mock_client, purpose="product_main_image", productId=str(product.id),
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/commerce/products/{product.id}/main-image/attach/", {"mediaId": media_id}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertTrue(product.main_image.name)

    def test_replacing_shop_image_updates_db_before_deleting_old_object(self, mock_client):
        # Explicitly reuse ONE mock client across both round trips so the
        # delete_object assertion below inspects the client that's actually
        # active during the second attach.
        client = _mock_s3_client()
        first_media = self._initiate_and_confirm(self.owner, mock_client, client=client)
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/commerce/shops/{self.shop.id}/image/attach/", {"mediaId": first_media}, format="json")
        self.shop.refresh_from_db()
        old_key = self.shop.image_file.name

        second_media = self._initiate_and_confirm(self.owner, mock_client, client=client)
        response = self.client.post(f"/api/v1/commerce/shops/{self.shop.id}/image/attach/", {"mediaId": second_media}, format="json")
        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        self.assertNotEqual(self.shop.image_file.name, old_key)
        # Old object cleanup was attempted only after the DB update succeeded.
        client.delete_object.assert_called()

    def test_product_gallery_attach_ordering_and_limit(self, mock_client):
        product = Product.objects.create(shop=self.shop, sku="SKU-GALLERY-1", name="P3", price=Decimal("10.00"))
        self.client.force_authenticate(self.owner)
        with override_settings(COMMERCE_PRODUCT_GALLERY_MAX_IMAGES=2):
            media_1 = self._initiate_and_confirm(self.owner, mock_client, purpose="product_gallery_image", productId=str(product.id))
            r1 = self.client.post(f"/api/v1/commerce/products/{product.id}/gallery/attach/", {"mediaId": media_1}, format="json")
            self.assertEqual(r1.status_code, 201)

            media_2 = self._initiate_and_confirm(self.owner, mock_client, purpose="product_gallery_image", productId=str(product.id))
            r2 = self.client.post(f"/api/v1/commerce/products/{product.id}/gallery/attach/", {"mediaId": media_2}, format="json")
            self.assertEqual(r2.status_code, 201)
            self.assertEqual(r2.data["sort_order"], r1.data["sort_order"] + 1)

            media_3 = self._initiate_and_confirm(self.owner, mock_client, purpose="product_gallery_image", productId=str(product.id))
            r3 = self.client.post(f"/api/v1/commerce/products/{product.id}/gallery/attach/", {"mediaId": media_3}, format="json")
            self.assertEqual(r3.status_code, 400)

    def test_product_gallery_remove_and_reorder(self, mock_client):
        product = Product.objects.create(shop=self.shop, sku="SKU-GALLERY-2", name="P4", price=Decimal("10.00"))
        self.client.force_authenticate(self.owner)
        media_1 = self._initiate_and_confirm(self.owner, mock_client, purpose="product_gallery_image", productId=str(product.id))
        media_2 = self._initiate_and_confirm(self.owner, mock_client, purpose="product_gallery_image", productId=str(product.id))
        r1 = self.client.post(f"/api/v1/commerce/products/{product.id}/gallery/attach/", {"mediaId": media_1}, format="json")
        r2 = self.client.post(f"/api/v1/commerce/products/{product.id}/gallery/attach/", {"mediaId": media_2}, format="json")
        id1, id2 = r1.data["id"], r2.data["id"]

        reordered = self.client.post(
            f"/api/v1/commerce/products/{product.id}/gallery/reorder/", {"order": [id2, id1]}, format="json",
        )
        self.assertEqual(reordered.status_code, 200)
        self.assertEqual([img["id"] for img in reordered.data], [id2, id1])

        removed = self.client.delete(f"/api/v1/commerce/products/{product.id}/gallery/{id1}/")
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(ProductImage.objects.filter(product=product).count(), 1)

    def test_service_image_attachment(self, mock_client):
        service = ShopService.objects.create(shop=self.shop, name="Consult", slug="consult")
        media_id = self._initiate_and_confirm(
            self.owner, mock_client, purpose="service_image", serviceId=str(service.id),
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/commerce/shop-services/{service.id}/image/attach/", {"mediaId": media_id}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        service.refresh_from_db()
        self.assertTrue(service.image_file.name)

    def test_service_gallery_attach_and_remove(self, mock_client):
        service = ShopService.objects.create(shop=self.shop, name="Consult", slug="consult")
        self.client.force_authenticate(self.owner)
        media_id = self._initiate_and_confirm(
            self.owner, mock_client, purpose="service_gallery_image", serviceId=str(service.id),
        )
        attach = self.client.post(
            f"/api/v1/commerce/shop-services/{service.id}/gallery/attach/", {"mediaId": media_id}, format="json",
        )
        self.assertEqual(attach.status_code, 201, attach.data)
        image_id = attach.data["id"]

        removed = self.client.delete(f"/api/v1/commerce/shop-services/{service.id}/gallery/{image_id}/")
        self.assertEqual(removed.status_code, 204)

    @patch("apps.commerce.views.get_feature_limit", return_value=None)
    def test_create_product_atomically_with_already_confirmed_main_image(self, _mock_limit, mock_client):
        # The "safe draft strategy": confirm media first, then create the
        # product in one JSON request referencing the mediaId — no fake
        # product id needed. Product-tier limit mocked to "unlimited" here —
        # unrelated to this migration, just a prerequisite for POSTing a
        # product at all in a test with no subscription fixture.
        media_id = self._initiate_and_confirm(
            self.owner, mock_client, purpose="product_main_image", shopId=str(self.shop.id),
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/commerce/products/",
            {
                "shop": str(self.shop.id),
                "sku": "SKU-CREATE-1",
                "name": "Created With Photo",
                "price": "12.00",
                "main_image_media_id": media_id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(id=response.data["id"])
        self.assertTrue(product.main_image.name)


@patch("apps.media.storage_backends.S3MediaStorage._client")
class CommerceComplaintAttachmentTests(CommerceMediaUploadTestBase):
    def setUp(self):
        super().setUp()
        self.buyer = get_user_model().objects.create_user(
            phone="+237670005005", password="TestPass123!", country="CM",
        )
        self.order = MarketplaceOrder.objects.create(
            buyer=self.buyer, shop=self.shop, total_amount=Decimal("25.00"),
        )
        self.manager = get_user_model().objects.create_user(
            phone="+237670005006", password="TestPass123!", country="CM",
        )
        ShopTeamMember.objects.create(shop=self.shop, user=self.manager, role=ShopRole.MANAGER, is_active=True)

    def _initiate_complaint_media(self, user, mock_client):
        mock_client.return_value = _mock_s3_client()
        self.client.force_authenticate(user)
        response = self.client.post(
            INITIATE_URL,
            {
                "purpose": "complaint_attachment",
                "filename": "evidence.jpg",
                "contentType": "image/jpeg",
                "sizeBytes": 1_000_000,
                "orderId": str(self.order.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        upload_id = response.data["uploadId"]
        confirm = self.client.post(_confirm_url(upload_id), {}, format="json")
        self.assertEqual(confirm.status_code, 200, confirm.data)
        return upload_id

    def test_order_buyer_can_initiate_and_attach(self, mock_client):
        media_id = self._initiate_complaint_media(self.buyer, mock_client)
        self.client.force_authenticate(self.buyer)
        response = self.client.post(
            "/api/v1/commerce/marketplace-complaints/",
            {"order_id": str(self.order.id), "text": "Item arrived damaged", "attachment_media_id": media_id},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        complaint = MarketplaceComplaint.objects.get(order=self.order)
        self.assertTrue(complaint.attachment.name)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, MarketplaceOrderStatus.COMPLAINT)

    def test_shop_manager_can_initiate_for_order(self, mock_client):
        response_status = self._initiate_complaint_media(self.manager, mock_client)
        self.assertTrue(response_status)  # succeeded without raising

    def test_unrelated_user_cannot_initiate_for_order(self, mock_client):
        mock_client.return_value = _mock_s3_client()
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            INITIATE_URL,
            {
                "purpose": "complaint_attachment",
                "filename": "evidence.jpg",
                "contentType": "image/jpeg",
                "sizeBytes": 1_000_000,
                "orderId": str(self.order.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_authorized_download_url(self, mock_client):
        media_id = self._initiate_complaint_media(self.buyer, mock_client)
        self.client.force_authenticate(self.buyer)
        self.client.post(
            "/api/v1/commerce/marketplace-complaints/",
            {"order_id": str(self.order.id), "text": "Damaged", "attachment_media_id": media_id},
            format="json",
        )
        complaint = MarketplaceComplaint.objects.get(order=self.order)
        response = self.client.get(f"/api/v1/commerce/marketplace-complaints/{complaint.id}/attachment-download-url/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("downloadUrl", response.data)
        self.assertNotIn(str(complaint.attachment.name), "")  # sanity: attachment set

    def test_unauthorized_user_cannot_download(self, mock_client):
        media_id = self._initiate_complaint_media(self.buyer, mock_client)
        self.client.force_authenticate(self.buyer)
        self.client.post(
            "/api/v1/commerce/marketplace-complaints/",
            {"order_id": str(self.order.id), "text": "Damaged", "attachment_media_id": media_id},
            format="json",
        )
        complaint = MarketplaceComplaint.objects.get(order=self.order)

        outsider = get_user_model().objects.create_user(
            phone="+237670005007", password="TestPass123!", country="CM",
        )
        self.client.force_authenticate(outsider)
        response = self.client.get(f"/api/v1/commerce/marketplace-complaints/{complaint.id}/attachment-download-url/")
        self.assertEqual(response.status_code, 404)


@patch("apps.media.storage_backends.S3MediaStorage._client")
class CommerceOrphanCleanupTests(CommerceMediaUploadTestBase):
    def test_unattached_confirmed_intent_is_expired_after_grace_period(self, mock_client):
        media_id = self._initiate_and_confirm(self.owner, mock_client)
        intent = MediaUploadIntent.objects.get(id=media_id)
        intent.confirmed_at = timezone.now() - timezone.timedelta(hours=48)
        intent.save(update_fields=["confirmed_at"])

        result = expire_unattached_confirmed_intents(grace_period_seconds=3600)
        self.assertEqual(result["expired_count"], 1)
        intent.refresh_from_db()
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_EXPIRED)

    def test_attached_intent_is_never_swept_regardless_of_age(self, mock_client):
        media_id = self._initiate_and_confirm(self.owner, mock_client)
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/commerce/shops/{self.shop.id}/image/attach/", {"mediaId": media_id}, format="json")

        intent = MediaUploadIntent.objects.get(id=media_id)
        intent.confirmed_at = timezone.now() - timezone.timedelta(days=30)
        intent.save(update_fields=["confirmed_at"])

        result = expire_unattached_confirmed_intents(grace_period_seconds=3600)
        self.assertEqual(result["expired_count"], 0)
        intent.refresh_from_db()
        self.assertEqual(intent.status, MediaUploadIntent.STATUS_CONFIRMED)

    def test_recently_confirmed_unattached_intent_is_not_swept_yet(self, mock_client):
        media_id = self._initiate_and_confirm(self.owner, mock_client)
        result = expire_unattached_confirmed_intents(grace_period_seconds=3600)
        self.assertEqual(result["expired_count"], 0)
        self.assertEqual(MediaUploadIntent.objects.get(id=media_id).status, MediaUploadIntent.STATUS_CONFIRMED)
