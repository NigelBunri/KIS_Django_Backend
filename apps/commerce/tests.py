
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.billing.services import get_wallet_account
from .availability import DAY_KEYS, normalize_availability_payload
from .models import Shop, Product, ShopService, ServiceBooking, ServiceBookingEscrow


class CommerceSmokeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone='5550000000', username='seller', password='pass', country='NG')
        self.shop = Shop.objects.create(owner=self.user, name='My Shop', slug='my-shop')

    def test_create_product(self):
        p = Product.objects.create(shop=self.shop, sku='TEST-001', name='Test Product', price=1000)
        self.assertEqual(p.shop, self.shop)


class ServiceBookingAPITests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone='5551111111', username='owner', password='secret', country='NG')
        self.customer = User.objects.create_user(phone='5552222222', username='customer', password='secret', country='NG')
        self.shop = Shop.objects.create(owner=self.owner, name='Owner Shop', slug='owner-shop')
        self.service = ShopService.objects.create(
            shop=self.shop,
            name='Consultation',
            slug='consultation',
            price=Decimal('180.00'),
            deposit_percent=Decimal('50'),
            visibility='public',
            status='published',
        )
        wallet = get_wallet_account(self.customer)
        wallet.balance_cents = 50000
        wallet.save(update_fields=['balance_cents'])
        self.client.force_authenticate(user=self.customer)
        self.shared_slot = timezone.now() + timedelta(days=2, hours=1)

    def _create_booking_payload(self, *, scheduled_at=None):
        scheduled_at = scheduled_at or self.shared_slot
        return {
            'service_id': str(self.service.id),
            'scheduled_at': scheduled_at.isoformat(),
        }

    def _schedule_for_date(self, date, hour=10):
        naive = timezone.datetime.combine(date, timezone.datetime.min.time()) + timedelta(hours=hour)
        return timezone.make_aware(naive)

    def _apply_availability(self, payload):
        normalized = normalize_availability_payload(payload)
        self.service.availability = normalized
        self.service.save(update_fields=['availability'])

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
        self.assertEqual(customer_wallet.balance_cents, 50000 - booking.deposit_cents)
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
        self.assertEqual(customer_wallet.balance_cents, 50000 - booking.deposit_cents)
        self.assertEqual(owner_wallet.balance_cents, 0)
        escrow = booking.escrow
        self.assertEqual(escrow.amount_cents, booking.deposit_cents)
        self.assertEqual(escrow.status, ServiceBookingEscrow.STATUS_PENDING)

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
        second_wallet.balance_cents = 50000
        second_wallet.save(update_fields=['balance_cents'])
        self.client.force_authenticate(user=second_user)
        second = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        third_user = get_user_model().objects.create_user(phone='5554444444', username='third', password='secret', country='NG')
        third_wallet = get_wallet_account(third_user)
        third_wallet.balance_cents = 50000
        third_wallet.save(update_fields=['balance_cents'])
        self.client.force_authenticate(user=third_user)
        third = self.client.post('/api/v1/commerce/service-bookings/', self._create_booking_payload(), format='json')
        self.assertEqual(third.status_code, status.HTTP_409_CONFLICT)
