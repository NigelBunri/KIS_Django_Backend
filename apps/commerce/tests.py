
from datetime import timedelta
from decimal import Decimal
import json
import uuid

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.http import QueryDict
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from unittest.mock import patch
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.billing.direct_payments import reconcile_direct_payment_callback
from apps.billing.models import DirectPaymentAuditEvent, DirectPaymentIntent
from apps.billing.services import get_wallet_account
from .category_catalog import ensure_catalog_categories
from .services import mark_provider_completed, place_marketplace_order, satisfy_marketplace_order
from .availability import DAY_KEYS, normalize_availability_payload
from .models import (
    CatalogCategory,
    Cart,
    CartItem,
    MarketplaceOrderStatus,
    Product,
    ProductImage,
    ProductQuestion,
    ProductReview,
    ServiceBooking,
    ServiceBookingComplaint,
    ServiceBookingEscrow,
    ServiceBookingPayment,
    Shop,
    ShopLandingPage,
    ShopService,
    ShopServiceImage,
    ShopTeamMember,
    ShopRole,
    ShopVerificationRequest,
)
from .serializers import (
    MarketplaceOrderSerializer,
    ProductSerializer,
    ShopSerializer,
    ShopServiceSerializer,
    ShopVerificationRequestSerializer,
)
from apps.broadcasts.models import BroadcastItem, BroadcastSourceType
from apps.verification.constants import VerificationBadgeCode, VerificationSubjectType
from apps.verification.models import VerificationBadge
from apps.verification.services import current_shop_verification_status, sync_shop_verification_request
from .signals import on_product_save


def disable_product_recommendation_signal(test_case):
    post_save.disconnect(on_product_save, sender=Product)
    test_case.addCleanup(post_save.connect, on_product_save, sender=Product)


class CommerceSmokeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone='5550000000', username='seller', password='pass', country='NG')
        self.shop = Shop.objects.create(owner=self.user, name='My Shop', slug='my-shop')

    def test_create_product(self):
        p = Product.objects.create(shop=self.shop, sku='TEST-001', name='Test Product', price=1000)
        self.assertEqual(p.shop, self.shop)


class ShopVerificationMigrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone='5550000100', username='shop_verify_owner', password='pass', country='NG')
        self.staff = User.objects.create_user(phone='5550000101', username='shop_verify_staff', password='pass', country='NG')
        self.staff.is_staff = True
        self.staff.save(update_fields=['is_staff'])
        self.shop = Shop.objects.create(owner=self.owner, name='Verify Shop', slug='verify-shop')

    def test_shop_request_sync_creates_central_case_without_public_document_url(self):
        request = ShopVerificationRequest.objects.create(
            shop=self.shop,
            requested_by=self.owner,
            documents=[
                {
                    'type': 'BUSINESS_REG',
                    'url': 'https://example.com/public-document.pdf',
                    'private_media_id': 'private-doc-001',
                }
            ],
        )

        case = sync_shop_verification_request(verification_request=request, actor=self.owner)

        self.assertIsNotNone(case)
        self.assertEqual(case.subject.subject_type, VerificationSubjectType.SHOP)
        self.assertEqual(case.provider, 'commerce')
        self.assertEqual(case.evidence_metadata['documents'][0]['private_media_id'], 'private-doc-001')
        self.assertNotIn('url', case.evidence_metadata['documents'][0])

    def test_approved_shop_request_issues_central_badges_and_syncs_legacy_fields(self):
        request = ShopVerificationRequest.objects.create(shop=self.shop, requested_by=self.owner, documents=[])
        request.status = 'APPROVED'
        request.processed_at = timezone.now()
        request.save(update_fields=['status', 'processed_at'])

        sync_shop_verification_request(verification_request=request, actor=self.staff)
        self.shop.refresh_from_db()

        self.assertTrue(self.shop.is_verified)
        self.assertEqual(self.shop.verification_status, 'VERIFIED')
        self.assertIn('verified-shop', self.shop.trust_badges)
        codes = set(
            VerificationBadge.objects.filter(
                subject__subject_type=VerificationSubjectType.SHOP,
                subject__subject_id=self.shop.id,
            ).values_list('code', flat=True)
        )
        self.assertIn(VerificationBadgeCode.VERIFIED_SHOP, codes)
        self.assertIn(VerificationBadgeCode.TRUSTED_MERCHANT, codes)
        self.assertTrue(current_shop_verification_status(self.shop)['verified'])

    def test_shop_serializer_exposes_public_verification_summary(self):
        request = ShopVerificationRequest.objects.create(shop=self.shop, requested_by=self.owner, status='APPROVED')
        sync_shop_verification_request(verification_request=request, actor=self.staff)

        data = ShopSerializer(self.shop).data

        self.assertIn('verification_summary', data)
        self.assertTrue(data['verification_summary']['verified'])

    def test_shop_verification_serializer_rejects_raw_document_payload(self):
        serializer = ShopVerificationRequestSerializer(
            data={
                'shop': str(self.shop.id),
                'requested_by': str(self.owner.id),
                'documents': [{'type': 'BUSINESS_REG', 'document_base64': 'data:image/png;base64,abc123'}],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('documents', serializer.errors)


class MarketplaceUsdCheckoutTests(TestCase):
    def setUp(self):
        disable_product_recommendation_signal(self)
        User = get_user_model()
        self.provider = User.objects.create_user(phone='5555551100', username='provider_usd', password='secret', country='NG')
        self.buyer = User.objects.create_user(phone='5555552200', username='buyer_usd', password='secret', country='NG')
        self.shop = Shop.objects.create(owner=self.provider, name='USD Provider Shop', slug='usd-provider-shop')
        self.product = Product.objects.create(
            shop=self.shop,
            sku='MP-USD-001',
            name='USD Marketplace Product',
            price=Decimal('10.00'),
            sale_price=Decimal('10.00'),
            stock_qty=10,
            currency='USD',
        )
        buyer_wallet = get_wallet_account(self.buyer)
        buyer_wallet.balance_cents = 50_000
        buyer_wallet.save(update_fields=['balance_cents'])

    def test_default_marketplace_order_is_usd_provider_pending_without_wallet_lock(self):
        order = place_marketplace_order(
            buyer=self.buyer,
            shop_id=self.shop.id,
            items=[{'product_id': str(self.product.id), 'quantity': 1, 'unit_price_cents': 1_000}],
        )

        buyer_wallet = get_wallet_account(self.buyer)
        buyer_wallet.refresh_from_db()

        self.assertEqual(order.currency, 'USD')
        self.assertEqual(order.total_amount, Decimal('10'))
        self.assertIsNone(order.buyer_debit_transaction_id)
        self.assertEqual(order.metadata['payment_status'], 'pending')
        self.assertEqual(order.metadata['payment_provider'], 'flutterwave')
        self.assertTrue(order.metadata['payment_required'])
        self.assertEqual(buyer_wallet.balance_cents, 50_000)
        self.assertEqual(buyer_wallet.locked_cents, 0)

        data = MarketplaceOrderSerializer(order).data
        self.assertEqual(data['total_usd_label'], '$10.00')
        self.assertEqual(data['currency_label'], 'USD')
        self.assertEqual(data['payment_status'], 'pending')
        self.assertEqual(data['payment_provider'], 'flutterwave')
        self.assertIsNotNone(data['payment_intent_id'])

        intent = DirectPaymentIntent.objects.get(id=order.metadata['direct_payment_intent_id'])
        self.assertEqual(intent.target_type, DirectPaymentIntent.TARGET_MARKETPLACE_ORDER)
        self.assertEqual(intent.target_id, order.id)
        self.assertEqual(intent.amount_cents, 1000)
        self.assertEqual(intent.status, DirectPaymentIntent.STATUS_PENDING)
        self.assertEqual(intent.payment_url, '')

    def test_wallet_marketplace_checkout_is_disabled_by_default(self):
        with self.assertRaises(ValidationError) as ctx:
            place_marketplace_order(
                buyer=self.buyer,
                shop_id=self.shop.id,
                items=[{'product_id': str(self.product.id), 'quantity': 1, 'unit_price_cents': 1_000}],
                metadata={'payment_method': 'wallet'},
            )

        self.assertIn('Commerce wallet/KIS Coin checkout is disabled', str(ctx.exception.detail))

    def test_provider_pending_marketplace_order_cannot_be_completed_or_satisfied(self):
        order = place_marketplace_order(
            buyer=self.buyer,
            shop_id=self.shop.id,
            items=[{'product_id': str(self.product.id), 'quantity': 1, 'unit_price_cents': 1_000}],
        )

        with self.assertRaises(ValidationError):
            mark_provider_completed(order)
        with self.assertRaises(ValidationError):
            satisfy_marketplace_order(order)

    @patch('apps.commerce.services._schedule_marketplace_order_auto_satisfaction')
    def test_flutterwave_callback_marks_marketplace_order_paid_idempotently(self, schedule_mock):
        order = place_marketplace_order(
            buyer=self.buyer,
            shop_id=self.shop.id,
            items=[{'product_id': str(self.product.id), 'quantity': 1, 'unit_price_cents': 1_000}],
        )
        intent = DirectPaymentIntent.objects.get(id=order.metadata['direct_payment_intent_id'])
        payload = {'data': {'tx_ref': intent.tx_ref, 'status': 'successful', 'id': 'flw-test-001'}}

        ok, result, paid_intent = reconcile_direct_payment_callback(payload=payload, signature='')
        self.assertTrue(ok)
        self.assertEqual(result, 'paid')
        self.assertEqual(paid_intent.status, DirectPaymentIntent.STATUS_PAID)
        order.refresh_from_db()
        self.assertEqual(order.metadata['payment_status'], 'paid')
        self.assertEqual(order.metadata['provider_transaction_id'], 'flw-test-001')

        mark_provider_completed(order)
        order.refresh_from_db()
        self.assertEqual(order.status, MarketplaceOrderStatus.AWAITING_SATISFACTION)
        schedule_mock.assert_called_once()

        ok, result, _paid_intent = reconcile_direct_payment_callback(payload=payload, signature='')
        self.assertTrue(ok)
        self.assertEqual(result, 'paid')
        self.assertEqual(DirectPaymentAuditEvent.objects.filter(intent=intent, event='callback.paid').count(), 1)

    def test_historical_kisc_marketplace_order_uses_safe_compatibility_label(self):
        order = place_marketplace_order(
            buyer=self.buyer,
            shop_id=self.shop.id,
            items=[{'product_id': str(self.product.id), 'quantity': 1, 'unit_price_cents': 1_000}],
        )
        order.currency = 'KISC'
        order.metadata = {'payment_status': 'paid', 'legacy_wallet_checkout': True}
        order.save(update_fields=['currency', 'metadata'])

        data = MarketplaceOrderSerializer(order).data

        self.assertEqual(data['currency_label'], 'Historical promotional-credit order')

    def test_marketplace_order_serializer_exposes_fulfillment_and_trust_guidance(self):
        self.shop.is_verified = True
        self.shop.trust_badges = ['trusted-merchant']
        self.shop.save(update_fields=['is_verified', 'trust_badges'])
        order = place_marketplace_order(
            buyer=self.buyer,
            shop_id=self.shop.id,
            items=[{'product_id': str(self.product.id), 'quantity': 1, 'unit_price_cents': 1_000}],
        )

        data = MarketplaceOrderSerializer(order).data

        self.assertEqual(data['seller_trust']['label'], 'Verified seller')
        self.assertEqual(data['fulfillment_summary']['delivery_status'], 'pending')
        self.assertEqual(data['next_action']['code'], 'open_checkout')
        self.assertEqual(data['currency_label'], 'USD')


class CommerceAmazonCoreApiTests(APITestCase):
    def setUp(self):
        disable_product_recommendation_signal(self)
        User = get_user_model()
        self.seller = User.objects.create_user(phone='5557771100', username='seller_120', password='secret', country='NG')
        self.buyer = User.objects.create_user(phone='5557772200', username='buyer_120', password='secret', country='NG')
        self.shop = Shop.objects.create(
            owner=self.seller,
            name='Trusted Royal Shop',
            slug='trusted-royal-shop',
            is_verified=True,
            trust_badges=['verified-shop'],
        )
        self.product = Product.objects.create(
            shop=self.shop,
            sku='ROYAL-120-001',
            name='Royal Lamp',
            price=Decimal('25.00'),
            sale_price=Decimal('20.00'),
            stock_qty=3,
            currency='USD',
        )
        self.client.force_authenticate(user=self.buyer)

    def test_product_detail_exposes_trust_reviews_questions_and_fulfillment(self):
        ProductReview.objects.create(product=self.product, user=self.buyer, rating=5, title='Excellent')
        ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            question='Does this ship this week?',
            answer='Yes.',
            answered_by=self.seller,
            answered_at=timezone.now(),
            status=ProductQuestion.STATUS_ANSWERED,
        )

        response = self.client.get(f'/api/v1/commerce/products/{self.product.id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['currency'], 'USD')
        self.assertTrue(response.data['seller_trust']['verified'])
        self.assertEqual(response.data['review_summary']['count'], 1)
        self.assertEqual(response.data['question_summary']['answered_count'], 1)
        self.assertEqual(response.data['fulfillment_summary']['stock_status'], 'in_stock')

    def test_cart_item_mutations_keep_cart_subtotal_in_sync(self):
        cart = Cart.objects.create(user=self.buyer, shop=self.shop)
        payload = {
            'cart': str(cart.id),
            'product': str(self.product.id),
            'quantity': 2,
            'price_snapshot': '20.00',
            'stock_snapshot': 3,
        }
        create_response = self.client.post('/api/v1/commerce/cart-items/', payload, format='json')
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        cart.refresh_from_db()
        self.assertEqual(cart.subtotal, Decimal('40.00'))

        update_response = self.client.patch(
            f"/api/v1/commerce/cart-items/{create_response.data['id']}/",
            {'quantity': 1},
            format='json',
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK, update_response.data)
        cart.refresh_from_db()
        self.assertEqual(cart.subtotal, Decimal('20.00'))

    def test_reviews_questions_and_discovery_endpoints_are_available(self):
        review_response = self.client.post(
            '/api/v1/commerce/product-reviews/',
            {'product': str(self.product.id), 'rating': 5, 'title': 'Loved it'},
            format='json',
        )
        self.assertEqual(review_response.status_code, status.HTTP_201_CREATED, review_response.data)
        question_response = self.client.post(
            '/api/v1/commerce/product-questions/',
            {'product': str(self.product.id), 'question': 'Can I pick this up?'},
            format='json',
        )
        self.assertEqual(question_response.status_code, status.HTTP_201_CREATED, question_response.data)

        discovery_response = self.client.get('/api/v1/commerce/discovery/?q=Royal')

        self.assertEqual(discovery_response.status_code, status.HTTP_200_OK, discovery_response.data)
        self.assertEqual(discovery_response.data['currency'], 'USD')
        self.assertIn('featured_products', discovery_response.data['sections'])


@override_settings(KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED=True)
class MarketplaceOrderSettlementTests(TestCase):
    def setUp(self):
        disable_product_recommendation_signal(self)
        User = get_user_model()
        self.provider = User.objects.create_user(phone='5555551000', username='provider', password='secret', country='NG')
        self.buyer = User.objects.create_user(phone='5555552000', username='buyer', password='secret', country='NG')
        self.shop = Shop.objects.create(owner=self.provider, name='Provider Shop', slug='provider-shop')
        self.product = Product.objects.create(
            shop=self.shop,
            sku='MP-001',
            name='Marketplace Product',
            price=Decimal('10.00'),
            sale_price=Decimal('10.00'),
            stock_qty=10,
        )
        buyer_wallet = get_wallet_account(self.buyer)
        buyer_wallet.balance_cents = 50_000
        buyer_wallet.save(update_fields=['balance_cents'])

    def test_marketplace_satisfaction_credits_provider_exact_cents(self):
        order = place_marketplace_order(
            buyer=self.buyer,
            shop_id=self.shop.id,
            items=[
                {
                    'product_id': str(self.product.id),
                    'quantity': 1,
                    'unit_price_cents': 1_000,
                }
            ],
            metadata={'payment_method': 'wallet'},
        )

        buyer_wallet = get_wallet_account(self.buyer)
        provider_wallet = get_wallet_account(self.provider)
        buyer_wallet.refresh_from_db()
        provider_wallet.refresh_from_db()

        self.assertEqual(order.total_amount, Decimal('10'))
        self.assertEqual(buyer_wallet.balance_cents, 49_000)
        self.assertEqual(buyer_wallet.locked_cents, 1_000)
        self.assertEqual(provider_wallet.balance_cents, 0)

        satisfy_marketplace_order(order)

        order.refresh_from_db()
        buyer_wallet.refresh_from_db()
        provider_wallet.refresh_from_db()

        self.assertEqual(order.status, MarketplaceOrderStatus.SATISFIED)
        self.assertIsNotNone(order.provider_credit_transaction)
        self.assertEqual(order.provider_credit_transaction.amount_cents, 1_000)
        self.assertEqual(buyer_wallet.locked_cents, 0)
        self.assertEqual(provider_wallet.balance_cents, 1_000)

    def test_marketplace_satisfaction_uses_locked_buyer_transaction_amount(self):
        buyer_wallet = get_wallet_account(self.buyer)
        buyer_wallet.balance_cents = 2_000_000
        buyer_wallet.save(update_fields=['balance_cents'])

        order = place_marketplace_order(
            buyer=self.buyer,
            shop_id=self.shop.id,
            items=[
                {
                    'product_id': str(self.product.id),
                    'quantity': 1,
                    'unit_price_cents': 1_000_000,
                }
            ],
            metadata={'payment_method': 'wallet'},
        )

        buyer_wallet = get_wallet_account(self.buyer)
        provider_wallet = get_wallet_account(self.provider)
        buyer_wallet.refresh_from_db()
        provider_wallet.refresh_from_db()

        self.assertEqual(buyer_wallet.locked_cents, 1_000_000)

        satisfy_marketplace_order(order)

        order.refresh_from_db()
        buyer_wallet.refresh_from_db()
        provider_wallet.refresh_from_db()

        self.assertEqual(order.status, MarketplaceOrderStatus.SATISFIED)
        self.assertIsNotNone(order.provider_credit_transaction)
        self.assertEqual(order.provider_credit_transaction.amount_cents, 1_000_000)
        self.assertEqual(buyer_wallet.locked_cents, 0)
        self.assertEqual(provider_wallet.balance_cents, 1_000_000)


class ServiceBookingMoneyNormalizationTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone='5556661111', username='owner_money_norm', password='secret', country='NG')
        self.customer = User.objects.create_user(phone='5556662222', username='customer_money_norm', password='secret', country='NG')
        self.shop = Shop.objects.create(owner=self.owner, name='Money Norm Shop', slug='money-norm-shop')
        self.service = ShopService.objects.create(
            shop=self.shop,
            name='Deep Consult',
            slug='deep-consult',
            price=Decimal('100.00'),
            visibility='public',
            status='published',
        )
        wallet = get_wallet_account(self.customer)
        wallet.balance_cents = 2_000_000
        wallet.save(update_fields=['balance_cents'])
        self.client.force_authenticate(user=self.customer)

    def test_usd_booking_creates_pending_provider_payment_without_wallet_lock(self):
        scheduled_at = timezone.now() + timedelta(days=2)
        response = self.client.post(
            '/api/v1/commerce/service-bookings/',
            {
                'service_id': str(self.service.id),
                'scheduled_at': scheduled_at.isoformat(),
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        booking = ServiceBooking.objects.get(id=response.data['id'])
        wallet = get_wallet_account(self.customer)
        wallet.refresh_from_db()

        self.assertEqual(booking.price_cents, 10_000)
        self.assertEqual(booking.deposit_cents, 10_000)
        self.assertEqual(wallet.balance_cents, 2_000_000)
        self.assertEqual(wallet.locked_cents, 0)
        payment = ServiceBookingPayment.objects.get(booking=booking)
        self.assertEqual(payment.currency, 'USD')
        self.assertEqual(payment.payment_method, 'flutterwave')
        self.assertEqual(payment.payment_status, ServiceBookingPayment.STATUS_PENDING)
        intent = DirectPaymentIntent.objects.get(id=booking.metadata['direct_payment_intent_id'])
        self.assertEqual(intent.target_type, DirectPaymentIntent.TARGET_SERVICE_BOOKING_PAYMENT)
        self.assertEqual(intent.target_id, payment.id)
        self.assertEqual(payment.transaction_reference, intent.tx_ref)

    def test_service_booking_wallet_checkout_is_disabled_by_default(self):
        scheduled_at = timezone.now() + timedelta(days=2)
        response = self.client.post(
            '/api/v1/commerce/service-bookings/',
            {
                'service_id': str(self.service.id),
                'scheduled_at': scheduled_at.isoformat(),
                'payment_method': 'wallet',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.assertEqual(response.data['code'], 'legacy_commerce_wallet_checkout_disabled')


class ShopLandingPageSystemTests(APITestCase):
    @staticmethod
    def _tiny_gif(name: str):
        return SimpleUploadedFile(
            name,
            (
                b'GIF89a\x01\x00\x01\x00\x80\x00\x00'
                b'\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,'
                b'\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
            ),
            content_type='image/gif',
        )

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            phone='5551112222',
            username='shop_landing_owner',
            password='secret',
            country='NG',
        )
        self.shop = Shop.objects.create(
            owner=self.owner,
            name='Landing Shop',
            slug='landing-shop',
            description='A shop with a custom landing page.',
            image_file=self._tiny_gif('shop.gif'),
        )
        self.client.force_authenticate(user=self.owner)

    def test_shop_landing_page_builder_round_trips_through_shop_endpoint(self):
        payload = {
            'landing_page_is_public': True,
            'landing_page_is_published': True,
            'landing_page': {
                'headline': 'Launch your next order with confidence.',
                'subheadline': 'Shop smarter with a curated landing flow.',
                'hero_image_url': 'https://example.com/hero.jpg',
                'hero_cta_text': 'Shop now',
                'hero_cta_url': 'https://example.com/shop',
                'landingBackgroundImageUrl': 'https://example.com/background.jpg',
                'landingBackgroundColorKey': 'ocean_mist',
                'landingLogoUrl': 'https://example.com/logo.png',
                'hero': {
                    'title': 'Launch your next order with confidence.',
                    'slogan': 'Shop smarter with a curated landing flow.',
                    'imageUrl': 'https://example.com/hero.jpg',
                    'ctaLabel': 'Shop now',
                    'ctaUrl': 'https://example.com/shop',
                },
                'about': 'We curate products and services for your home and team.',
                'gallery': [
                    'https://example.com/gallery-1.jpg',
                    'https://example.com/gallery-2.jpg',
                ],
                'contact': {
                    'phone': '+2348000000000',
                    'email': 'hello@example.com',
                    'address': '12 Broad Street',
                },
                'faqs': [
                    {'question': 'Do you deliver nationwide?', 'answer': 'Yes.'},
                ],
                'seo': {
                    'title': 'Landing Shop',
                    'description': 'Marketplace landing page',
                    'keywords': ['shop', 'marketplace'],
                },
                'sections': [
                    {
                        'id': 'hero_1',
                        'name': 'Hero Banner',
                        'type': 'hero_banner',
                        'data': {
                            'backgroundImageUrl': 'https://example.com/hero.jpg',
                            'title': 'Launch your next order with confidence.',
                            'subtitle': 'Shop smarter with a curated landing flow.',
                            'ctaText': 'Shop now',
                            'ctaLink': 'https://example.com/shop',
                        },
                    },
                    {
                        'id': 'testimonials_1',
                        'name': 'Testimonials',
                        'type': 'testimonials',
                        'data': {
                            'items': [
                                {
                                    'id': 'quote_1',
                                    'quote': 'This shop always delivers.',
                                    'author': 'Ada',
                                    'role': 'Buyer',
                                }
                            ],
                        },
                    },
                    {
                        'id': 'gallery_1',
                        'name': 'Gallery',
                        'type': 'image_gallery_grid',
                        'data': {
                            'images': [
                                'https://example.com/gallery-1.jpg',
                                'https://example.com/gallery-2.jpg',
                            ],
                        },
                    },
                ],
            },
        }

        response = self.client.patch(
            f'/api/v1/commerce/shops/{self.shop.id}/',
            payload,
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        landing_page = ShopLandingPage.objects.get(shop=self.shop)
        self.assertEqual(landing_page.headline, 'Launch your next order with confidence.')
        self.assertEqual(landing_page.hero_cta_text, 'Shop now')
        self.assertEqual(landing_page.builder_data.get('landingBackgroundColorKey'), 'ocean_mist')
        self.assertEqual(len(landing_page.builder_data.get('sections', [])), 3)

        detail_response = self.client.get(f'/api/v1/commerce/shops/{self.shop.id}/')
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK, detail_response.data)
        data = detail_response.data
        landing = data['landing_page']

        self.assertTrue(data['landing_page_is_public'])
        self.assertTrue(data['landing_page_is_published'])
        self.assertEqual(landing['hero']['title'], 'Launch your next order with confidence.')
        self.assertEqual(landing['hero']['ctaLabel'], 'Shop now')
        self.assertEqual(landing['landingBackgroundImageUrl'], 'https://example.com/background.jpg')
        self.assertEqual(landing['landingLogoUrl'], 'https://example.com/logo.png')
        self.assertEqual(len(landing['gallery']), 2)
        self.assertEqual(len(landing['sections']), 3)
        self.assertEqual(landing['testimonials'][0]['quote'], 'This shop always delivers.')


class ServiceCategorySystemTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone='5559991111', username='service_owner', password='secret', country='NG')
        self.shop = Shop.objects.create(owner=self.owner, name='Service Category Shop', slug='service-category-shop')
        ensure_catalog_categories()

    @staticmethod
    def _tiny_gif(name: str):
        return SimpleUploadedFile(
            name,
            (
                b'GIF89a\x01\x00\x01\x00\x80\x00\x00'
                b'\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,'
                b'\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
            ),
            content_type='image/gif',
        )

    def test_service_catalog_categories_include_parent_hierarchy(self):
        parent = CatalogCategory.objects.get(slug='tech-digital-services')
        child = CatalogCategory.objects.get(slug='custom-software-development')

        self.assertEqual(parent.category_type, 'service')
        self.assertIsNone(parent.parent_id)
        self.assertEqual(child.category_type, 'service')
        self.assertEqual(child.parent_id, parent.id)

    def test_service_serializer_saves_and_updates_catalog_category_ids(self):
        create_serializer = ShopServiceSerializer(data={
            'shop': self.shop.id,
            'name': 'Managed Support',
            'price': '250',
            'duration_minutes': 60,
            'delivery_modes': ['remote'],
            'remote_meeting_link': 'https://example.com/meet/support',
            'category_ids': [str(CatalogCategory.objects.get(slug='tech-digital-services').id)],
            'catalog_category_ids': [str(CatalogCategory.objects.get(slug='custom-software-development').id)],
        })
        self.assertTrue(create_serializer.is_valid(), create_serializer.errors)
        service = create_serializer.save()

        saved_slugs = list(service.catalog_categories.order_by('sort_order', 'name').values_list('slug', flat=True))
        self.assertEqual(saved_slugs, ['tech-digital-services', 'custom-software-development'])

        update_serializer = ShopServiceSerializer(
            instance=service,
            data={
                'name': 'Managed Support Plus',
                'category_ids': [str(CatalogCategory.objects.get(slug='professional-services').id)],
                'catalog_category_ids': [str(CatalogCategory.objects.get(slug='virtual-administrative-support').id)],
            },
            partial=True,
        )
        self.assertTrue(update_serializer.is_valid(), update_serializer.errors)
        updated_service = update_serializer.save()
        updated_slugs = list(updated_service.catalog_categories.order_by('sort_order', 'name').values_list('slug', flat=True))
        self.assertEqual(updated_slugs, ['professional-services', 'virtual-administrative-support'])

    def test_service_serializer_accepts_querydict_category_lists_on_partial_update(self):
        parent = CatalogCategory.objects.get(slug='home-services')
        first_child = CatalogCategory.objects.get(slug='landscape-architecture-design')
        second_child = CatalogCategory.objects.get(slug='subscription-box-curation')
        service = ShopService.objects.create(
            shop=self.shop,
            name='VIP Session',
            slug='vip-session',
            price=Decimal('199.99'),
        )
        service.catalog_categories.set([parent, first_child])

        query_data = QueryDict('', mutable=True)
        query_data.update({
            'name': 'Echo Davis VIP Session',
            'description': 'Reserved slot with a trusted specialist.',
            'price': '199.99',
            'service_type': 'appointment',
            'availability': json.dumps({
                'timezone': 'UTC',
                'slot_duration_minutes': 60,
                'date_range': None,
                'days': {
                    'monday': {'enabled': True, 'all_day': True, 'times': []},
                    'tuesday': {'enabled': True, 'all_day': True, 'times': []},
                    'wednesday': {'enabled': True, 'all_day': True, 'times': []},
                    'thursday': {'enabled': True, 'all_day': True, 'times': []},
                    'friday': {'enabled': True, 'all_day': True, 'times': []},
                    'saturday': {'enabled': True, 'all_day': True, 'times': []},
                    'sunday': {'enabled': True, 'all_day': True, 'times': []},
                },
                'specific_dates': {},
            }),
            'availability_rules': '[]',
            'category_id': str(parent.id),
        })
        query_data.setlist('category_ids', [str(parent.id), str(second_child.id)])
        query_data.setlist('catalog_category_ids', [str(parent.id), str(second_child.id)])

        serializer = ShopServiceSerializer(instance=service, data=query_data, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated_service = serializer.save()

        updated_slugs = list(updated_service.catalog_categories.order_by('sort_order', 'name').values_list('slug', flat=True))
        self.assertEqual(updated_slugs, ['home-services', 'subscription-box-curation'])

    def test_service_serializer_accepts_querydict_with_uploaded_files(self):
        parent = CatalogCategory.objects.get(slug='home-services')
        service = ShopService.objects.create(
            shop=self.shop,
            name='Photo Session',
            slug='photo-session',
            price=Decimal('99.99'),
        )
        service.catalog_categories.set([parent])

        query_data = QueryDict('', mutable=True)
        query_data.update({
            'name': 'Photo Session Updated',
            'price': '120.00',
            'category_id': str(parent.id),
        })
        query_data.setlist('category_ids', [str(parent.id)])
        query_data.setlist('catalog_category_ids', [str(parent.id)])
        query_data.setlist('images', [
            self._tiny_gif('gallery-1.gif'),
            self._tiny_gif('gallery-2.gif'),
        ])

        serializer = ShopServiceSerializer(instance=service, data=query_data, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(len(serializer.initial_data.getlist('images')), 2)
        updated_service = serializer.save()
        self.assertEqual(updated_service.images.count(), 2)

    def test_service_serializer_exposes_featured_image_url_and_gallery_images(self):
        parent = CatalogCategory.objects.get(slug='home-services')
        service = ShopService.objects.create(
            shop=self.shop,
            name='Design Session',
            slug='design-session',
            price=Decimal('150.00'),
            image_file=self._tiny_gif('featured.gif'),
        )
        service.catalog_categories.set([parent])
        ShopServiceImage.objects.create(
            service=service,
            image_file=self._tiny_gif('gallery.gif'),
            order=1,
        )

        payload = ShopServiceSerializer(instance=service).data

        self.assertIn('featured', str(payload.get('image_url') or ''))
        self.assertEqual(len(payload.get('images') or []), 1)

    def test_service_serializer_can_remove_featured_and_selected_gallery_images(self):
        parent = CatalogCategory.objects.get(slug='home-services')
        service = ShopService.objects.create(
            shop=self.shop,
            name='Removal Session',
            slug='removal-session',
            price=Decimal('150.00'),
            image_file=self._tiny_gif('featured-remove.gif'),
        )
        service.catalog_categories.set([parent])
        first_image = ShopServiceImage.objects.create(
            service=service,
            image_file=self._tiny_gif('gallery-remove-1.gif'),
            order=1,
        )
        second_image = ShopServiceImage.objects.create(
            service=service,
            image_file=self._tiny_gif('gallery-remove-2.gif'),
            order=2,
        )

        query_data = QueryDict('', mutable=True)
        query_data.update({
            'remove_featured_image': 'true',
        })
        query_data.setlist('remove_image_ids', [str(first_image.id)])

        serializer = ShopServiceSerializer(instance=service, data=query_data, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated_service = serializer.save()
        updated_service.refresh_from_db()

        self.assertFalse(bool(updated_service.image_file))
        self.assertEqual(updated_service.image_url, '')
        self.assertEqual(updated_service.images.count(), 1)
        self.assertEqual(str(updated_service.images.first().id), str(second_image.id))


class ProductSystemTests(TestCase):
    def setUp(self):
        User = get_user_model()
        token = uuid.uuid4().hex[:8]
        self.owner = User.objects.create_user(
            phone=f'555888{token[:4]}',
            username=f'product_owner_{token}',
            password='secret',
            country='NG',
        )
        self.shop = Shop.objects.create(
            owner=self.owner,
            name='Product Category Shop',
            slug=f'product-category-shop-{token}',
        )
        ensure_catalog_categories()

    @staticmethod
    def _tiny_gif(name: str):
        return SimpleUploadedFile(
            name,
            (
                b'GIF89a\x01\x00\x01\x00\x80\x00\x00'
                b'\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,'
                b'\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
            ),
            content_type='image/gif',
        )

    def test_product_serializer_saves_catalog_categories_variants_and_gallery_images(self):
        parent = CatalogCategory.objects.get(slug='sustainable-apparel')
        child = CatalogCategory.objects.get(slug='luxury-leather-goods')
        serializer = ProductSerializer(data={
            'shop': self.shop.id,
            'sku': f'PROD-{uuid.uuid4().hex[:6]}',
            'name': 'Layered Jacket',
            'slug': f'layered-jacket-{uuid.uuid4().hex[:6]}',
            'description': 'Warm jacket with variants.',
            'price': '180.00',
            'sale_price': '150.00',
            'inventory_type': 'PHYSICAL',
            'stock_qty': 9,
            'image_file': self._tiny_gif('main.gif'),
            'images': [self._tiny_gif('gallery-1.gif'), self._tiny_gif('gallery-2.gif')],
            'category_ids': [str(parent.id)],
            'catalog_category_ids': [str(parent.id), str(child.id)],
            'available_sizes': ['M', 'L'],
            'available_colors': ['Black', 'Olive'],
            'variants': [
                {
                    'id': 'variant-1',
                    'name': 'Black / M',
                    'sku': 'JACKET-BLK-M',
                    'price': '180.00',
                    'sale_price': '150.00',
                    'stock_qty': 4,
                    'is_active': True,
                    'options': {'size': 'M', 'color': 'Black'},
                }
            ],
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        product = serializer.save()

        saved_slugs = list(product.catalog_categories.order_by('sort_order', 'name').values_list('slug', flat=True))
        self.assertEqual(saved_slugs, ['sustainable-apparel', 'luxury-leather-goods'])
        self.assertEqual(product.gallery_images.count(), 2)
        self.assertEqual(product.variants[0]['sku'], 'JACKET-BLK-M')
        self.assertEqual(product.variants[0]['options'], {'size': 'M', 'color': 'Black'})

    def test_product_serializer_exposes_featured_image_url_and_gallery_images(self):
        parent = CatalogCategory.objects.get(slug='sustainable-apparel')
        product = Product.objects.create(
            shop=self.shop,
            sku=f'LOOK-{uuid.uuid4().hex[:6]}',
            name='Tailored Suit',
            slug=f'tailored-suit-{uuid.uuid4().hex[:6]}',
            price=Decimal('220.00'),
            main_image=self._tiny_gif('featured.gif'),
        )
        product.catalog_categories.set([parent])
        ProductImage.objects.create(
            product=product,
            image_file=self._tiny_gif('gallery.gif'),
            sort_order=1,
        )

        payload = ProductSerializer(instance=product).data

        self.assertIn('featured', str(payload.get('image_url') or ''))
        self.assertEqual(len(payload.get('gallery_images') or []), 1)


class ProductBroadcastAPITests(APITestCase):
    def setUp(self):
        User = get_user_model()
        token = uuid.uuid4().hex[:8]
        self.owner = User.objects.create_user(
            phone=f'555777{token[:4]}',
            username=f'product_broadcast_owner_{token}',
            password='secret',
            country='NG',
        )
        self.shop = Shop.objects.create(
            owner=self.owner,
            name='Broadcast Product Shop',
            slug=f'broadcast-product-shop-{token}',
        )
        self.product = Product.objects.create(
            shop=self.shop,
            sku=f'BROADCAST-{token}',
            name='Broadcast Sneakers',
            slug=f'broadcast-sneakers-{token}',
            price=Decimal('75.00'),
            stock_qty=3,
        )
        self.client.force_authenticate(user=self.owner)

    def test_product_can_be_broadcast_by_owner(self):
        response = self.client.post(f'/api/v1/commerce/products/{self.product.id}/broadcast/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.MARKET_PRODUCT,
                source_id=str(self.product.id),
                is_deleted=False,
            ).exists()
        )

    def test_product_broadcast_can_be_removed(self):
        self.client.post(f'/api/v1/commerce/products/{self.product.id}/broadcast/', {}, format='json')
        response = self.client.delete(f'/api/v1/commerce/products/{self.product.id}/broadcast/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.MARKET_PRODUCT,
                source_id=str(self.product.id),
                is_deleted=False,
            ).exists()
        )


class ServiceBookingAPITests(APITestCase):
    def setUp(self):
        User = get_user_model()
        token = uuid.uuid4().hex[:8]
        self.owner = User.objects.create_user(phone=f'555111{token[:4]}', username=f'owner_{token}', password='secret', country='NG')
        self.customer = User.objects.create_user(phone=f'555222{token[4:]}', username=f'customer_{token}', password='secret', country='NG')
        self.manager = User.objects.create_user(phone=f'555333{token[:4]}', username=f'manager_{token}', password='secret', country='NG')
        self.shop = Shop.objects.create(owner=self.owner, name='Owner Shop', slug=f'owner-shop-{token}')
        ShopTeamMember.objects.create(shop=self.shop, user=self.manager, role=ShopRole.MANAGER, is_active=True)
        self.service = ShopService.objects.create(
            shop=self.shop,
            name='Consultation',
            slug=f'consultation-{token}',
            price=Decimal('180.00'),
            deposit_percent=Decimal('50'),
            visibility='public',
            status='published',
        )
        wallet = get_wallet_account(self.customer)
        wallet.balance_cents = 5_000_000
        wallet.save(update_fields=['balance_cents'])
        self.client.force_authenticate(user=self.customer)
        self.shared_slot = timezone.now() + timedelta(days=2, hours=1)

    def _create_booking_payload(self, *, scheduled_at=None, **extra):
        scheduled_at = scheduled_at or self.shared_slot
        payload = {
            'service_id': str(self.service.id),
            'scheduled_at': scheduled_at.isoformat(),
        }
        payload.update(extra)
        return payload

    def _schedule_for_date(self, date, hour=10):
        naive = timezone.datetime.combine(date, timezone.datetime.min.time()) + timedelta(hours=hour)
        return timezone.make_aware(naive)

    def _apply_availability(self, payload):
        normalized = normalize_availability_payload(payload)
        self.service.availability = normalized
        self.service.save(update_fields=['availability'])

    def test_service_can_be_broadcast_by_owner(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(f'/api/v1/commerce/shop-services/{self.service.id}/broadcast/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.MARKET_SERVICE,
                source_id=str(self.service.id),
                is_deleted=False,
            ).exists()
        )

    def test_service_broadcast_can_be_removed(self):
        self.client.force_authenticate(user=self.owner)
        self.client.post(f'/api/v1/commerce/shop-services/{self.service.id}/broadcast/', {}, format='json')
        response = self.client.delete(f'/api/v1/commerce/shop-services/{self.service.id}/broadcast/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.MARKET_SERVICE,
                source_id=str(self.service.id),
                is_deleted=False,
            ).exists()
        )

    def _extract_field_message(self, response_data, field):
        value = response_data.get(field)
        if isinstance(value, (list, tuple)):
            return ' '.join(str(item) for item in value)
        if isinstance(value, dict):
            return ' '.join(str(item) for item in value.values())
        return str(value or '')

    def test_create_booking_charges_wallet_and_owner(self):
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=response.data['id'])
        self.assertEqual(booking.user, self.customer)
        owner_wallet = get_wallet_account(self.owner)
        customer_wallet = get_wallet_account(self.customer)
        customer_wallet.refresh_from_db()
        owner_wallet.refresh_from_db()
        self.assertEqual(response.data['deposit_cents'], booking.deposit_cents)
        self.assertEqual(customer_wallet.balance_cents, 5_000_000 - booking.deposit_cents)
        self.assertEqual(owner_wallet.balance_cents, 0)
        escrow = booking.escrow
        self.assertEqual(escrow.amount_cents, booking.deposit_cents)
        self.assertEqual(escrow.status, ServiceBookingEscrow.STATUS_PENDING)

    def test_duplicate_slot_returns_conflict(self):
        payload = self._create_booking_payload()
        first = self.client.post('/api/v1/commerce/service-bookings/', payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self.client.post('/api/v1/commerce/service-bookings/', payload, format='json')
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        owner_wallet = get_wallet_account(self.owner)
        customer_wallet = get_wallet_account(self.customer)
        owner_wallet.refresh_from_db()
        customer_wallet.refresh_from_db()
        booking = ServiceBooking.objects.get(id=first.data['id'])
        self.assertEqual(customer_wallet.balance_cents, 5_000_000 - booking.deposit_cents)
        self.assertEqual(owner_wallet.balance_cents, 0)
        escrow = booking.escrow
        self.assertEqual(escrow.amount_cents, booking.deposit_cents)
        self.assertEqual(escrow.status, ServiceBookingEscrow.STATUS_PENDING)

    def test_payer_can_submit_complaint_without_manual_escrow_field(self):
        wallet = get_wallet_account(self.customer)
        wallet.balance_cents = 2_000_000
        wallet.save(update_fields=['balance_cents'])
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=response.data['id'])
        booking.mark_provider_completed()
        booking.save(update_fields=['status', 'provider_completed_at', 'satisfaction_deadline'])

        complaint_response = self.client.post(
            '/api/v1/commerce/service-booking-complaints/',
            {
                'booking': str(booking.id),
                'personal_statement': 'The service was not delivered as agreed.',
                'reason': 'Provider marked complete before finishing.',
                'receipt_url': '',
            },
            format='json',
        )
        self.assertEqual(complaint_response.status_code, status.HTTP_201_CREATED)
        booking.refresh_from_db()
        complaint = ServiceBookingComplaint.objects.get(id=complaint_response.data['id'])
        self.assertEqual(complaint.booking_id, booking.id)
        self.assertEqual(complaint.escrow_id, booking.escrow.id)
        self.assertEqual(complaint.submitted_by_id, self.customer.id)
        self.assertEqual(booking.status, ServiceBooking.STATUS_DISPUTE)

    def test_manager_can_view_and_mark_completed(self):
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking_id = response.data['id']

        self.client.force_authenticate(user=self.manager)
        detail = self.client.get(f'/api/v1/commerce/service-bookings/{booking_id}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

        complete = self.client.post(f'/api/v1/commerce/service-bookings/{booking_id}/mark-completed/', format='json')
        self.assertEqual(complete.status_code, status.HTTP_200_OK)

        booking = ServiceBooking.objects.get(id=booking_id)
        self.assertEqual(booking.status, ServiceBooking.STATUS_AWAITING_SATISFACTION)
        self.assertIsNotNone(booking.provider_completed_at)

    @override_settings(SERVICE_ENABLE_QUOTES=True)
    def test_pay_remaining_creates_payment_for_quote_booking(self):
        wallet = get_wallet_account(self.customer)
        wallet.balance_cents = 2_000_000
        wallet.save(update_fields=['balance_cents'])
        booking = ServiceBooking.objects.create(
            service=self.service,
            shop=self.shop,
            user=self.customer,
            scheduled_at=self.shared_slot,
            price_cents=1_250_000,
            deposit_cents=0,
            balance_cents=1_250_000,
            instructions='Quote approved, pending full payment.',
            payment_tx_ref='quote-booking-test',
            metadata={'quote_required': True},
        )

        pay_remaining = self.client.post(f'/api/v1/commerce/service-bookings/{booking.id}/pay-remaining/', format='json')
        self.assertEqual(pay_remaining.status_code, status.HTTP_200_OK)

        booking.refresh_from_db()
        payment = ServiceBookingPayment.objects.get(booking=booking)
        self.assertEqual(payment.amount_cents, booking.price_cents)
        self.assertEqual(payment.payment_status, ServiceBookingPayment.STATUS_PAID)
        self.assertEqual(booking.balance_cents, 0)
        self.assertEqual(booking.deposit_cents, booking.price_cents)

        satisfy = self.client.patch(f'/api/v1/commerce/payments/{payment.id}/satisfy/', format='json')
        self.assertEqual(satisfy.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, ServiceBooking.STATUS_COMPLETED)
        self.assertEqual(payment.payment_status, ServiceBookingPayment.STATUS_SATISFIED)

    def test_insufficient_balance_returns_bad_request(self):
        customer_wallet = get_wallet_account(self.customer)
        customer_wallet.balance_cents = 0
        customer_wallet.save(update_fields=['balance_cents'])
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Insufficient wallet balance.', response.data.get('detail', ''))

    def test_blackout_date_blocked(self):
        blackout_date = (timezone.now() + timedelta(days=3)).date()
        self.service.blackout_dates = [blackout_date]
        self.service.save(update_fields=['blackout_dates'])
        scheduled_at = timezone.make_aware(timezone.datetime.combine(blackout_date, timezone.datetime.min.time())) + timedelta(hours=10)
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=scheduled_at), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        scheduled_message = self._extract_field_message(response.data, 'scheduled_at')
        self.assertIn('unavailable', scheduled_message)

    def test_min_notice_hours_enforced(self):
        self.service.min_notice_hours = 48
        self.service.save(update_fields=['min_notice_hours'])
        scheduled_at = timezone.now() + timedelta(hours=12)
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=scheduled_at), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        scheduled_message = self._extract_field_message(response.data, 'scheduled_at')
        self.assertIn('hours ahead', scheduled_message)

    def test_max_advance_booking_days_enforced(self):
        self.service.max_advance_booking_days = 5
        self.service.save(update_fields=['max_advance_booking_days'])
        scheduled_at = timezone.now() + timedelta(days=10)
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=scheduled_at), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        scheduled_message = self._extract_field_message(response.data, 'scheduled_at')
        self.assertIn('days ahead', scheduled_message)

    def test_booking_within_date_range_is_allowed(self):
        start_date = (timezone.now() + timedelta(days=4)).date()
        end_date = start_date + timedelta(days=2)
        self._apply_availability({
            'timezone': 'UTC',
            'slot_duration_minutes': 60,
            'date_range': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            },
        })
        scheduled_at = self._schedule_for_date(start_date, hour=10)
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=scheduled_at), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_booking_outside_date_range_blocked(self):
        start_date = (timezone.now() + timedelta(days=5)).date()
        end_date = start_date + timedelta(days=2)
        self._apply_availability({
            'timezone': 'UTC',
            'slot_duration_minutes': 60,
            'date_range': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            },
        })
        scheduled_at = self._schedule_for_date(end_date + timedelta(days=1), hour=10)
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=scheduled_at), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        scheduled_message = self._extract_field_message(response.data, 'scheduled_at')
        self.assertIn('unavailable', scheduled_message.lower())

    def test_booking_blocked_when_day_disabled(self):
        target_date = (timezone.now() + timedelta(days=3)).date()
        scheduled_at = self._schedule_for_date(target_date, hour=11)
        day_key = DAY_KEYS[target_date.weekday()]
        self._apply_availability({
            'timezone': 'UTC',
            'slot_duration_minutes': 60,
            'days': {
                day_key: {'enabled': False, 'all_day': True, 'times': []},
            },
        })
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=scheduled_at), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        scheduled_message = self._extract_field_message(response.data, 'scheduled_at')
        self.assertIn('unavailable', scheduled_message)

    def test_booking_rejects_time_not_in_schedule(self):
        target_date = (timezone.now() + timedelta(days=4)).date()
        scheduled_at = self._schedule_for_date(target_date, hour=9)
        day_key = DAY_KEYS[target_date.weekday()]
        self._apply_availability({
            'timezone': 'UTC',
            'slot_duration_minutes': 60,
            'days': {
                day_key: {'enabled': True, 'all_day': False, 'times': ['12:00']},
            },
        })
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=scheduled_at), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        scheduled_message = self._extract_field_message(response.data, 'scheduled_at')
        self.assertIn('time is unavailable', scheduled_message.lower())

    def test_remote_service_booking_includes_meeting_link(self):
        self.service.delivery_modes = ['remote']
        self.service.remote_meeting_link = 'https://meet.example.com/session'
        self.service.save(update_fields=['delivery_modes', 'remote_meeting_link'])
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data.get('remote_meeting_link'), 'https://meet.example.com/session')

    def test_group_booking_allows_multiple_users_when_enabled(self):
        self.service.group_booking_allowed = True
        self.service.max_bookings_per_slot = 2
        self.service.save(update_fields=['group_booking_allowed', 'max_bookings_per_slot'])
        first = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second_user = get_user_model().objects.create_user(phone='5553333333', username='second', password='secret', country='NG')
        second_wallet = get_wallet_account(second_user)
        second_wallet.balance_cents = 5_000_000
        second_wallet.save(update_fields=['balance_cents'])
        self.client.force_authenticate(user=second_user)
        second = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        third_user = get_user_model().objects.create_user(phone='5554444444', username='third', password='secret', country='NG')
        third_wallet = get_wallet_account(third_user)
        third_wallet.balance_cents = 5_000_000
        third_wallet.save(update_fields=['balance_cents'])
        self.client.force_authenticate(user=third_user)
        third = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(third.status_code, status.HTTP_409_CONFLICT)

    @override_settings(SERVICE_ENFORCE_BUFFERS=True)
    def test_buffer_conflict_blocks_close_slots_when_enabled(self):
        self.service.prep_buffer_minutes = 30
        self.service.cleanup_buffer_minutes = 15
        self.service.turnaround_hours = 1
        self.service.save(update_fields=['prep_buffer_minutes', 'cleanup_buffer_minutes', 'turnaround_hours'])
        first = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        later_slot = self.shared_slot + timedelta(hours=1)
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=later_slot), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        scheduled_message = self._extract_field_message(response.data, 'scheduled_at')
        self.assertIn('buffers', scheduled_message.lower())

    @override_settings(SERVICE_ENFORCE_COVERAGE=True, SERVICE_ENFORCE_TRAVEL_RADIUS=True)
    def test_request_outside_coverage_or_radius_is_blocked(self):
        self.service.coverage = ['Abuja']
        self.service.travel_radius_km = Decimal('25.00')
        self.service.save(update_fields=['coverage', 'travel_radius_km'])
        payload = self._create_booking_payload(scheduled_at=self.shared_slot + timedelta(days=1))
        payload.update({
            'location': {'city': 'Abuja', 'state': 'FCT', 'country': 'NG'},
            'distance_km': '40',
        })
        response = self.client.post('/api/v1/commerce/service-bookings/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        distance_message = self._extract_field_message(response.data, 'distance_km')
        self.assertIn('travel distance', distance_message.lower())

    @override_settings(SERVICE_ENFORCE_REMOTE_REGIONS=True)
    def test_remote_booking_requires_supported_region(self):
        self.service.delivery_modes = ['remote']
        self.service.remote_regions = ['North America']
        self.service.save(update_fields=['delivery_modes', 'remote_regions'])
        payload = self._create_booking_payload(scheduled_at=self.shared_slot)
        payload.update({'is_remote': True, 'remote_region': 'Europe'})
        response = self.client.post('/api/v1/commerce/service-bookings/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        remote_message = self._extract_field_message(response.data, 'remote_region')
        self.assertIn('remote region', remote_message.lower())

    @override_settings(SERVICE_ENFORCE_GROUP_CAPACITY=True)
    def test_participant_and_staff_limits_are_enforced(self):
        self.service.max_participants = 2
        self.service.staff_required = 2
        self.service.save(update_fields=['max_participants', 'staff_required'])
        participant_payload = self._create_booking_payload(scheduled_at=self.shared_slot + timedelta(days=1))
        participant_payload.update({'participant_count': 3, 'staff_on_site': 2})
        participant_response = self.client.post('/api/v1/commerce/service-bookings/', participant_payload, format='json')
        self.assertEqual(participant_response.status_code, status.HTTP_400_BAD_REQUEST)
        participants_message = self._extract_field_message(participant_response.data, 'participant_count')
        self.assertIn('maximum', participants_message.lower())

        staff_payload = self._create_booking_payload(scheduled_at=self.shared_slot + timedelta(days=2))
        staff_payload.update({'participant_count': 2, 'staff_on_site': 1})
        staff_response = self.client.post('/api/v1/commerce/service-bookings/', staff_payload, format='json')
        self.assertEqual(staff_response.status_code, status.HTTP_400_BAD_REQUEST)
        staff_message = self._extract_field_message(staff_response.data, 'staff_on_site')
        self.assertIn('at least', staff_message.lower())

    @override_settings(SERVICE_ENFORCE_REQUIREMENTS=True)
    def test_requirements_acknowledgement_is_required(self):
        self.service.requirements = ['Signed intake form', 'Materials submitted']
        self.service.save(update_fields=['requirements'])
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('requirements_acknowledged', response.data)
        payload = self._create_booking_payload(requirements_acknowledged=self.service.requirements)
        payload['scheduled_at'] = (timezone.now() + timedelta(days=2)).isoformat()
        success = self.client.post('/api/v1/commerce/service-bookings/', payload, format='json')
        self.assertEqual(success.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=success.data['id'])
        self.assertEqual(booking.metadata.get('requirements_acknowledged'), self.service.requirements)

    @override_settings(SERVICE_REQUIRE_TERMS_ACCEPTANCE=True)
    def test_service_terms_acceptance_is_enforced(self):
        self.service.service_terms = "Client agrees to provide workspace access."
        self.service.save(update_fields=['service_terms'])
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        terms_payload = self._create_booking_payload(terms_accepted=True)
        success = self.client.post('/api/v1/commerce/service-bookings/', terms_payload, format='json')
        self.assertEqual(success.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=success.data['id'])
        self.assertTrue(booking.metadata.get('terms_accepted'))

    def test_booking_metadata_persists_location_and_capacity_fields(self):
        self.service.delivery_modes = ['onsite', 'remote']
        self.service.max_participants = 4
        self.service.staff_required = 2
        self.service.save(update_fields=['delivery_modes', 'max_participants', 'staff_required'])
        payload = self._create_booking_payload(
            is_remote=False,
            participant_count=3,
            staff_on_site=2,
            location={'address_line1': '12 Marina', 'city': 'Lagos', 'country': 'NG'},
            distance_km='10',
            remote_region='West Africa',
        )
        response = self.client.post('/api/v1/commerce/service-bookings/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=response.data['id'])
        self.assertEqual(booking.metadata.get('participant_count'), 3)
        self.assertEqual(booking.metadata.get('staff_on_site'), 2)
        self.assertEqual(booking.metadata.get('remote_region'), 'West Africa')
        self.assertEqual(booking.metadata.get('distance_km'), '10.00')
        self.assertEqual(booking.metadata.get('location', {}).get('city'), 'Lagos')

    @override_settings(SERVICE_ENFORCE_REFUND_POLICY=True)
    def test_refund_policy_window_blocks_close_cancellations(self):
        self.service.cancellation_window_hours = 48
        self.service.refund_policy = "Full refund when canceled at least 48 hours before the scheduled slot."
        self.service.save(update_fields=['cancellation_window_hours', 'refund_policy'])
        scheduled_at = timezone.now() + timedelta(hours=30)
        payload = self._create_booking_payload(scheduled_at=scheduled_at)
        booking_resp = self.client.post('/api/v1/commerce/service-bookings/', payload, format='json')
        self.assertEqual(booking_resp.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=booking_resp.data['id'])
        cancel = self.client.post(f'/api/v1/commerce/service-bookings/{booking.id}/cancel/', format='json')
        self.assertEqual(cancel.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('48 hours', cancel.data.get('message', ''))

    @override_settings(SERVICE_ENFORCE_RESCHEDULE_POLICY=True)
    def test_reschedule_within_window_is_blocked(self):
        self.service.reschedule_window_hours = 72
        self.service.save(update_fields=['reschedule_window_hours'])
        scheduled_at = timezone.now() + timedelta(hours=48)
        booking_resp = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=scheduled_at), format='json')
        self.assertEqual(booking_resp.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=booking_resp.data['id'])
        target = timezone.now() + timedelta(days=3)
        reschedule = self.client.post(
            f'/api/v1/commerce/service-bookings/{booking.id}/reschedule/',
            {'scheduled_at': target.isoformat()},
            format='json',
        )
        self.assertEqual(reschedule.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Reschedule requests must be made', reschedule.data.get('message', ''))

    @override_settings(SERVICE_ENFORCE_RESCHEDULE_POLICY=True)
    def test_reschedule_updates_schedule_and_metadata(self):
        self.service.reschedule_window_hours = 24
        self.service.save(update_fields=['reschedule_window_hours'])
        scheduled_at = timezone.now() + timedelta(days=5)
        booking_resp = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(scheduled_at=scheduled_at), format='json')
        self.assertEqual(booking_resp.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=booking_resp.data['id'])
        new_slot = timezone.now() + timedelta(days=10)
        reschedule = self.client.post(
            f'/api/v1/commerce/service-bookings/{booking.id}/reschedule/',
            {'scheduled_at': new_slot.isoformat()},
            format='json',
        )
        self.assertEqual(reschedule.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.scheduled_at, new_slot)
        history = booking.metadata.get('reschedules') or []
        self.assertTrue(history)
        self.assertEqual(history[-1]['to'], new_slot.isoformat())
    @override_settings(SERVICE_ENABLE_QUOTES=True)
    def test_quote_required_flow_skips_payment(self):
        self.service.quote_required = True
        self.service.save(update_fields=['quote_required'])
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=response.data['id'])
        self.assertEqual(booking.deposit_cents, 0)
        self.assertEqual(booking.balance_cents, booking.price_cents)
        self.assertFalse(ServiceBookingEscrow.objects.filter(booking=booking).exists())
        self.assertTrue(booking.metadata.get('quote_required', False))

    @override_settings(SERVICE_ENABLE_NEGOTIATION=True)
    def test_negotiation_requests_record_requested_price(self):
        self.service.negotiable = True
        self.service.save(update_fields=['negotiable'])
        response = self.client.post(
            '/api/v1/commerce/service-bookings/',
            self._create_booking_payload(requested_price='55.50'),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=response.data['id'])
        self.assertEqual(booking.price_cents, 555000)
        self.assertEqual(booking.deposit_cents, 0)
        self.assertTrue(booking.metadata.get('negotiation_requested'))
        self.assertEqual(booking.metadata.get('requested_price'), '55.50')

    @override_settings(SERVICE_ENABLE_PACKAGE_PRICING=True)
    def test_package_pricing_increases_price(self):
        self.service.packages = [
            {'name': 'Premium', 'price': '20.00', 'duration_minutes': 30},
        ]
        self.service.save(update_fields=['packages'])
        response = self.client.post(
            '/api/v1/commerce/service-bookings/',
            self._create_booking_payload(selected_package='Premium'),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=response.data['id'])
        self.assertEqual(booking.price_cents, 2000000)

    @override_settings(SERVICE_ENABLE_ADDONS=True)
    def test_addon_pricing_increases_price(self):
        self.service.addons = [
            {'name': 'Recording', 'price': '5.00', 'duration_minutes': 15},
        ]
        self.service.save(update_fields=['addons'])
        response = self.client.post(
            '/api/v1/commerce/service-bookings/',
            self._create_booking_payload(selected_addons=['Recording']),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=response.data['id'])
        self.assertEqual(booking.price_cents, 1850000)

    @override_settings(SERVICE_ENFORCE_MINIMUM_CHARGE=True)
    def test_minimum_charge_is_enforced(self):
        self.service.price = Decimal('20.00')
        self.service.minimum_charge = Decimal('75.00')
        self.service.save(update_fields=['price', 'minimum_charge'])
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('minimum charge', response.data.get('detail', '').lower())

    @override_settings(SERVICE_HANDLE_TAX_INCLUSIVE=True, COMMERCE_DEFAULT_TAX_RATE_PCT='10')
    def test_tax_handling_applies_extra_amount_when_exclusive(self):
        self.service.tax_inclusive = False
        self.service.save(update_fields=['tax_inclusive'])
        response = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = ServiceBooking.objects.get(id=response.data['id'])
        self.assertEqual(booking.price_cents, 1_980_000)
