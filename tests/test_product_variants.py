from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.commerce.models import Product, ProductVariant, Shop
from apps.commerce.views import ProductViewSet


class ProductVariantSyncTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='variant-owner',
            email='variant@example.com',
            password='strong-pass-123',
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
        self.view = ProductViewSet()

    def _build_stub(self, payload):
        return SimpleNamespace(_variants_payload=payload, _variants_provided=True)

    def test_sync_creates_variants_from_payload(self):
        payload = [
            {
                'sku': 'VAR-001',
                'size': 'M',
                'color': 'Blue',
                'price': '14.50',
                'stock_qty': 5,
                'image_url': 'https://example.com/variant.png',
            },
        ]
        self.view._sync_product_variants(self.product, self._build_stub(payload))
        variants = list(ProductVariant.objects.filter(product=self.product))
        self.assertEqual(len(variants), 1)
        variant = variants[0]
        self.assertEqual(variant.sku, 'VAR-001')
        self.assertEqual(variant.size, 'M')
        self.assertEqual(variant.color, 'Blue')
        self.assertEqual(variant.price, Decimal('14.50'))
        self.assertEqual(variant.stock_qty, 5)
        self.assertEqual(variant.image_url, 'https://example.com/variant.png')

    def test_sync_updates_and_removes_variants(self):
        existing = ProductVariant.objects.create(
            product=self.product,
            sku='EXIST-001',
            price=Decimal('10.00'),
            stock_qty=2,
        )
        payload = [
            {
                'id': str(existing.id),
                'sku': 'EXIST-001',
                'size': 'L',
                'color': 'Red',
                'price': '12.00',
                'stock_qty': 3,
            },
            {
                'sku': 'NEW-002',
                'size': 'S',
                'color': 'Black',
                'price': '5.00',
                'stock_qty': 1,
            },
        ]
        self.view._sync_product_variants(self.product, self._build_stub(payload))
        variants = list(ProductVariant.objects.filter(product=self.product).order_by('sku'))
        self.assertEqual(len(variants), 2)
        updated = ProductVariant.objects.get(id=existing.id)
        self.assertEqual(updated.size, 'L')
        self.assertEqual(updated.color, 'Red')
        self.assertEqual(updated.price, Decimal('12.00'))
        # Now clearing the variants should delete them
        self.view._sync_product_variants(self.product, self._build_stub([]))
        self.assertFalse(ProductVariant.objects.filter(product=self.product).exists())
