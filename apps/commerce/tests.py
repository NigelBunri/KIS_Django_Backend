
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
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
        self.service.coverage = ['Lagos']
        self.service.travel_radius_km = Decimal('25.00')
        self.service.save(update_fields=['coverage', 'travel_radius_km'])
        payload = self._create_booking_payload(scheduled_at=self.shared_slot + timedelta(days=1))
        payload.update({
            'location': {'city': 'Abuja', 'state': 'FCT', 'country': 'NG'},
            'distance_km': '40',
        })
        response = self.client.post('/api/v1/commerce/service-bookings/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        location_message = self._extract_field_message(response.data, 'location')
        distance_message = self._extract_field_message(response.data, 'distance_km')
        self.assertTrue('coverage' in location_message.lower() or 'destination' in location_message.lower())
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
        payload = self._create_booking_payload(scheduled_at=self.shared_slot + timedelta(days=1))
        payload.update({'participant_count': 3, 'staff_on_site': 1})
        response = self.client.post('/api/v1/commerce/service-bookings/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        participants_message = self._extract_field_message(response.data, 'participant_count')
        staff_message = self._extract_field_message(response.data, 'staff_on_site')
        self.assertIn('maximum', participants_message.lower())
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
        self.assertEqual(booking.price_cents, 5550)
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
        self.assertEqual(booking.price_cents, 20000)

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
        self.assertEqual(booking.price_cents, 18500)

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
        self.assertEqual(booking.price_cents, 19800)
