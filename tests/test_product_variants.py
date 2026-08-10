"""
Regression tests for ProductSerializer._sanitize_variants — the actual,
current mechanism for product variants.

This file previously tested ProductViewSet._sync_product_variants(), a
method that no longer exists anywhere in the codebase. Product variants
were refactored at some point from a separate ProductVariant model (still
present, still admin-registered, but no longer written to by any live code
path) to a normalized JSON blob stored directly on Product.variants via
ProductSerializer._sanitize_variants(). This file was never updated for
that refactor, so every test in it failed with AttributeError regardless
of the actual variant-handling code's correctness. Rewritten to test the
real, current implementation.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.commerce.models import Product, Shop
from apps.commerce.serializers import ProductSerializer


class ProductVariantSanitizationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            phone='+237699930002',
            email='variant@example.com',
            password='strong-pass-123',
            country='CM',
        )
        self.shop = Shop.objects.create(owner=self.user, name='Variant Shop', slug='variant-shop')
        self.product = Product.objects.create(
            shop=self.shop,
            sku='PRD-VARIANT-1',
            name='Variant Product',
            slug='variant-product',
            price=Decimal('9.00'),
            stock_qty=10,
        )
        self.serializer = ProductSerializer()

    def test_sanitizes_a_new_variant_payload(self):
        payload = [
            {
                'sku': 'VAR-001',
                'name': 'Medium Blue',
                'price': '14.50',
                'sale_price': '12.00',
                'stock_qty': 5,
                'options': {'size': 'M', 'color': 'Blue'},
            },
        ]
        sanitized = self.serializer._sanitize_variants(payload)

        self.assertEqual(len(sanitized), 1)
        variant = sanitized[0]
        self.assertEqual(variant['sku'], 'VAR-001')
        self.assertEqual(variant['name'], 'Medium Blue')
        self.assertEqual(variant['price'], '14.50')
        self.assertEqual(variant['sale_price'], '12.00')
        self.assertEqual(variant['stock_qty'], 5)
        self.assertEqual(variant['options'], {'size': 'M', 'color': 'Blue'})
        self.assertTrue(variant['is_active'])
        self.assertTrue(variant['id'])  # auto-generated when not supplied

    def test_missing_price_defaults_to_zero_rather_than_erroring(self):
        sanitized = self.serializer._sanitize_variants([{'sku': 'VAR-002'}])
        self.assertEqual(sanitized[0]['price'], '0.00')
        self.assertIsNone(sanitized[0]['sale_price'])

    def test_non_dict_entries_in_the_payload_are_dropped_not_erroring(self):
        sanitized = self.serializer._sanitize_variants(['not-a-dict', 42, {'sku': 'VAR-003'}])
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0]['sku'], 'VAR-003')

    def test_non_list_payload_sanitizes_to_an_empty_list(self):
        self.assertEqual(self.serializer._sanitize_variants({'not': 'a list'}), [])
        self.assertEqual(self.serializer._sanitize_variants(None), [])

    def test_saving_a_product_persists_sanitized_variants_and_a_later_save_replaces_them(self):
        # Mirrors the real create/update flow: PATCHing `variants` replaces
        # the whole list (it's a JSON blob, not synced rows), so "removing"
        # a variant means simply not including it in the next payload.
        self.product.variants = self.serializer._sanitize_variants([
            {'id': 'existing-1', 'sku': 'EXIST-001', 'name': 'Large Red', 'price': '12.00', 'stock_qty': 3},
            {'sku': 'NEW-002', 'name': 'Small Black', 'price': '5.00', 'stock_qty': 1},
        ])
        self.product.save(update_fields=['variants'])
        self.product.refresh_from_db()
        self.assertEqual(len(self.product.variants), 2)

        self.product.variants = self.serializer._sanitize_variants([
            {'id': 'existing-1', 'sku': 'EXIST-001', 'name': 'Large Red Updated', 'price': '13.00', 'stock_qty': 2},
        ])
        self.product.save(update_fields=['variants'])
        self.product.refresh_from_db()
        self.assertEqual(len(self.product.variants), 1)
        self.assertEqual(self.product.variants[0]['name'], 'Large Red Updated')
