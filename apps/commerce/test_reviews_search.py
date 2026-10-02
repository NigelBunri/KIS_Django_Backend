"""Tests for the verified-purchase/seller-response/report-abuse review
system and the trigram-based typo-tolerant product search, both added as
part of closing remaining gaps from the mega-shop capability audit."""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Product, ProductReview, Shop, ShopPayoutAccountStatus
from .services import _is_verified_purchase, place_marketplace_order
from .signals import on_product_save


def _disable_recommendation_signal(test_case):
    post_save.disconnect(on_product_save, sender=Product)
    test_case.addCleanup(post_save.connect, on_product_save, sender=Product)


def _make_shop(owner, key, **overrides):
    defaults = dict(
        owner=owner, name=f"{key} Shop", slug=f"{key}-shop",
        payout_account_status=ShopPayoutAccountStatus.ACTIVE,
        flutterwave_subaccount_id=f"RS_TEST_{key.upper()}",
    )
    defaults.update(overrides)
    return Shop.objects.create(**defaults)


class VerifiedPurchaseTests(TestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5558010001", username="vp_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5558010002", username="vp_buyer", password="secret", country="NG")
        self.non_buyer = User.objects.create_user(phone="5558010003", username="vp_nonbuyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "vptest")
        self.product = Product.objects.create(
            shop=self.shop, sku="VP-001", name="Verified Widget", slug="verified-widget",
            price=Decimal("10.00"), stock_qty=10, currency="USD",
        )

    def test_buyer_with_no_order_is_not_verified(self):
        self.assertFalse(_is_verified_purchase(self.non_buyer, self.product))

    def test_buyer_with_pending_payment_order_is_not_yet_verified(self):
        # TEMPORAL = payment not completed yet - shouldn't count.
        place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        self.assertFalse(_is_verified_purchase(self.buyer, self.product))

    def test_buyer_with_satisfied_order_is_verified(self):
        order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        order.status = "satisfied"
        order.save(update_fields=["status"])
        self.assertTrue(_is_verified_purchase(self.buyer, self.product))


class ReviewAPITests(APITestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5558020001", username="rev_owner", password="secret", country="NG")
        self.other_owner = User.objects.create_user(phone="5558020002", username="rev_other_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5558020003", username="rev_buyer", password="secret", country="NG")
        self.reporter_a = User.objects.create_user(phone="5558020004", username="rev_reporter_a", password="secret", country="NG")
        self.reporter_b = User.objects.create_user(phone="5558020005", username="rev_reporter_b", password="secret", country="NG")
        self.reporter_c = User.objects.create_user(phone="5558020006", username="rev_reporter_c", password="secret", country="NG")
        self.reporter_d = User.objects.create_user(phone="5558020007", username="rev_reporter_d", password="secret", country="NG")
        self.reporter_e = User.objects.create_user(phone="5558020008", username="rev_reporter_e", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "reviewapi")
        self.other_shop = _make_shop(self.other_owner, "reviewapiother")
        self.product = Product.objects.create(
            shop=self.shop, sku="REVAPI-001", name="Review API Widget", slug="review-api-widget",
            price=Decimal("15.00"), stock_qty=10, currency="USD",
        )

    def _create_review(self, user):
        return ProductReview.objects.create(product=self.product, user=user, rating=4, title="Decent", body="It's fine.")

    def test_review_is_verified_when_order_completed(self):
        order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        order.status = "completed"
        order.save(update_fields=["status"])

        self.client.force_authenticate(self.buyer)
        response = self.client.post("/api/v1/commerce/product-reviews/", {
            "product": str(self.product.id), "rating": 5, "title": "Great", "body": "Loved it.",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["is_verified_purchase"])

    def test_client_cannot_fake_verified_purchase(self):
        self.client.force_authenticate(self.buyer)
        response = self.client.post("/api/v1/commerce/product-reviews/", {
            "product": str(self.product.id), "rating": 5, "title": "Great", "body": "Never bought it",
            "is_verified_purchase": True,  # fabricated - must be ignored
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertFalse(response.data["is_verified_purchase"])

    def test_seller_can_respond_to_review(self):
        review = self._create_review(self.buyer)
        self.client.force_authenticate(self.owner)
        response = self.client.post(f"/api/v1/commerce/product-reviews/{review.id}/respond/", {
            "response": "Thanks for the feedback!",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["seller_response"], "Thanks for the feedback!")
        self.assertIsNotNone(response.data["seller_response_at"])

    def test_unrelated_seller_cannot_respond(self):
        review = self._create_review(self.buyer)
        self.client.force_authenticate(self.other_owner)
        response = self.client.post(f"/api/v1/commerce/product-reviews/{review.id}/respond/", {
            "response": "Hijacked response",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        review.refresh_from_db()
        self.assertEqual(review.seller_response, "")

    def test_buyer_cannot_respond_as_seller(self):
        review = self._create_review(self.buyer)
        self.client.force_authenticate(self.buyer)
        response = self.client.post(f"/api/v1/commerce/product-reviews/{review.id}/respond/", {
            "response": "Self-response attempt",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_report_is_idempotent_per_user(self):
        review = self._create_review(self.buyer)
        self.client.force_authenticate(self.reporter_a)
        first = self.client.post(f"/api/v1/commerce/product-reviews/{review.id}/report/", {"reason": "spam"}, format="json")
        second = self.client.post(f"/api/v1/commerce/product-reviews/{review.id}/report/", {"reason": "spam"}, format="json")
        self.assertEqual(first.data["report_count"], 1)
        self.assertEqual(second.data["report_count"], 1)
        self.assertTrue(first.data["created"])
        self.assertFalse(second.data["created"])

    def test_five_reports_auto_hides_review_for_moderation(self):
        review = self._create_review(self.buyer)
        for reporter in [self.reporter_a, self.reporter_b, self.reporter_c, self.reporter_d, self.reporter_e]:
            self.client.force_authenticate(reporter)
            self.client.post(f"/api/v1/commerce/product-reviews/{review.id}/report/", {"reason": "fake"}, format="json")
        review.refresh_from_db()
        self.assertEqual(review.report_count, 5)
        self.assertEqual(review.status, ProductReview.STATUS_PENDING)

    def test_hidden_review_not_visible_to_other_buyers(self):
        review = self._create_review(self.buyer)
        for reporter in [self.reporter_a, self.reporter_b, self.reporter_c, self.reporter_d, self.reporter_e]:
            self.client.force_authenticate(reporter)
            self.client.post(f"/api/v1/commerce/product-reviews/{review.id}/report/", {"reason": "fake"}, format="json")

        other_buyer = get_user_model().objects.create_user(phone="5558020099", username="rev_other_buyer", password="secret", country="NG")
        self.client.force_authenticate(other_buyer)
        list_response = self.client.get(f"/api/v1/commerce/product-reviews/?product={self.product.id}")
        results = list_response.data.get("results", list_response.data)
        ids = {row["id"] for row in results}
        self.assertNotIn(str(review.id), ids)

    def test_image_urls_capped_at_five(self):
        self.client.force_authenticate(self.buyer)
        urls = [f"https://images.kis.test/review-{i}.jpg" for i in range(6)]
        response = self.client.post("/api/v1/commerce/product-reviews/", {
            "product": str(self.product.id), "rating": 5, "title": "Too many pics", "body": "x",
            "image_urls": urls,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TrigramSearchTests(TestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5558030001", username="trgm_owner", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "trgmtest")
        self.widget = Product.objects.create(
            shop=self.shop, sku="TRGM-001", name="Wireless Headphones", slug="wireless-headphones",
            price=Decimal("50.00"), stock_qty=10, currency="USD",
        )
        self.unrelated = Product.objects.create(
            shop=self.shop, sku="TRGM-002", name="Garden Hose", slug="garden-hose",
            price=Decimal("20.00"), stock_qty=10, currency="USD",
        )

    def test_exact_substring_still_matches(self):
        from .views import ProductViewSet
        view = ProductViewSet()
        view.request = type("R", (), {"query_params": {"q": "Headphones"}, "user": self.owner})()
        qs = view.get_queryset()
        self.assertIn(self.widget, list(qs))
        self.assertNotIn(self.unrelated, list(qs))

    def test_typo_tolerant_match_via_trigram(self):
        from .views import ProductViewSet
        view = ProductViewSet()
        # "Headphnes" - missing an 'o' - should still find the real product
        # via trigram similarity even though it's not a substring match.
        view.request = type("R", (), {"query_params": {"q": "Headphnes"}, "user": self.owner})()
        qs = view.get_queryset()
        self.assertIn(self.widget, list(qs))
        self.assertNotIn(self.unrelated, list(qs))

    def test_unrelated_query_matches_nothing(self):
        from .views import ProductViewSet
        view = ProductViewSet()
        view.request = type("R", (), {"query_params": {"q": "xyznonexistent"}, "user": self.owner})()
        qs = view.get_queryset()
        self.assertEqual(list(qs), [])
