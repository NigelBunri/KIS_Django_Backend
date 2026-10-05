"""Tests for the shipping/fulfillment foundation (shipping_models.py,
shipping_services.py, shipping_views.py). See docs/commerce_shipping.md for
the architecture this exercises."""
from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APITestCase, APITransactionTestCase

from .models import Product, Shop, ShopPayoutAccountStatus
from .services import place_marketplace_order
from .signals import on_product_save
from .shipping_models import (
    CustomerAddress,
    Fulfillment,
    FulfillmentStatus,
    Shipment,
    ShipmentEvent,
    ShipmentStatus,
    ShippingMethod,
    ShippingMethodType,
    ShippingRate,
    ShippingRateType,
    ShippingZone,
)
from .shipping_services import (
    RateableItem,
    calculate_rate_cents,
    create_shipment,
    list_shipping_options,
    match_zone_for_address,
    record_shipment_event,
    transition_fulfillment,
    transition_shipment,
)


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


def _make_address(user, **overrides):
    defaults = dict(
        user=user, recipient_name="Test Recipient", recipient_phone="08012345678",
        country="NG", state="Lagos", city="Lagos", street_address="1 Test Street",
    )
    defaults.update(overrides)
    return CustomerAddress.objects.create(**defaults)


def _make_method_and_rate(shop, zone, *, rate_type=ShippingRateType.FLAT, base_cents=1000, **rate_overrides):
    method = ShippingMethod.objects.create(shop=shop, name="Standard", method_type=ShippingMethodType.STANDARD, min_days=2, max_days=5)
    rate = ShippingRate.objects.create(method=method, zone=zone, rate_type=rate_type, base_cents=base_cents, **rate_overrides)
    return method, rate


class ShippingZoneMatchingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557710001", username="zone_owner", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "zonetest")

    def test_most_specific_zone_wins(self):
        country_zone = ShippingZone.objects.create(shop=self.shop, name="Nigeria", country="NG")
        state_zone = ShippingZone.objects.create(shop=self.shop, name="Nigeria/Lagos", country="NG", state="Lagos")
        city_zone = ShippingZone.objects.create(shop=self.shop, name="Nigeria/Lagos/Lagos City", country="NG", state="Lagos", city="Lagos")

        address = _make_address(self.owner, country="NG", state="Lagos", city="Lagos")
        matched = match_zone_for_address(self.shop, address)
        self.assertEqual(matched.id, city_zone.id)

        address_no_city = _make_address(self.owner, country="NG", state="Lagos", city="Ikeja")
        matched2 = match_zone_for_address(self.shop, address_no_city)
        self.assertEqual(matched2.id, state_zone.id)

        address_other_state = _make_address(self.owner, country="NG", state="Abuja", city="Abuja")
        matched3 = match_zone_for_address(self.shop, address_other_state)
        self.assertEqual(matched3.id, country_zone.id)

    def test_postal_prefix_beats_city(self):
        city_zone = ShippingZone.objects.create(shop=self.shop, name="Lagos", country="NG", state="Lagos", city="Lagos")
        postal_zone = ShippingZone.objects.create(shop=self.shop, name="Lagos 101", country="NG", state="Lagos", city="Lagos", postal_code_prefix="101")
        address = _make_address(self.owner, country="NG", state="Lagos", city="Lagos", postal_code="101233")
        matched = match_zone_for_address(self.shop, address)
        self.assertEqual(matched.id, postal_zone.id)

    def test_no_match_returns_none(self):
        ShippingZone.objects.create(shop=self.shop, name="Nigeria only", country="NG")
        address = _make_address(self.owner, country="US", state="Texas", city="Austin")
        self.assertIsNone(match_zone_for_address(self.shop, address))

    def test_inactive_zone_is_not_matched(self):
        ShippingZone.objects.create(shop=self.shop, name="Inactive NG", country="NG", is_active=False)
        address = _make_address(self.owner, country="NG")
        self.assertIsNone(match_zone_for_address(self.shop, address))

    def test_priority_breaks_ties_at_equal_specificity(self):
        low = ShippingZone.objects.create(shop=self.shop, name="Low priority", country="NG", priority=0)
        high = ShippingZone.objects.create(shop=self.shop, name="High priority", country="NG", priority=10)
        address = _make_address(self.owner, country="NG")
        matched = match_zone_for_address(self.shop, address)
        self.assertEqual(matched.id, high.id)

    def test_other_shops_zones_are_never_matched(self):
        other_owner = get_user_model().objects.create_user(phone="5557710099", username="other_zone_owner", password="secret", country="NG")
        other_shop = _make_shop(other_owner, "otherzone")
        ShippingZone.objects.create(shop=other_shop, name="Other shop NG", country="NG")
        address = _make_address(self.owner, country="NG")
        self.assertIsNone(match_zone_for_address(self.shop, address))


class ShippingRateCalculationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557720001", username="rate_owner", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "ratetest")
        self.zone = ShippingZone.objects.create(shop=self.shop, name="NG", country="NG")
        self.method = ShippingMethod.objects.create(shop=self.shop, name="Standard")

    def _rate(self, **kwargs):
        return ShippingRate.objects.create(method=self.method, zone=self.zone, **kwargs)

    def test_flat_rate(self):
        rate = self._rate(rate_type=ShippingRateType.FLAT, base_cents=1500)
        cost = calculate_rate_cents(rate, subtotal_cents=99999, items=[RateableItem(quantity=3)])
        self.assertEqual(cost, 1500)

    def test_free_rate(self):
        rate = self._rate(rate_type=ShippingRateType.FREE, base_cents=1500)
        cost = calculate_rate_cents(rate, subtotal_cents=1, items=[RateableItem(quantity=1)])
        self.assertEqual(cost, 0)

    def test_threshold_rate_below_and_above(self):
        rate = self._rate(rate_type=ShippingRateType.THRESHOLD, base_cents=1000, free_above_cents=10000)
        below = calculate_rate_cents(rate, subtotal_cents=9999, items=[RateableItem(quantity=1)])
        above = calculate_rate_cents(rate, subtotal_cents=10000, items=[RateableItem(quantity=1)])
        self.assertEqual(below, 1000)
        self.assertEqual(above, 0)

    def test_quantity_rate_multiplies_by_total_items(self):
        rate = self._rate(rate_type=ShippingRateType.QUANTITY, base_cents=200)
        cost = calculate_rate_cents(rate, subtotal_cents=0, items=[RateableItem(quantity=3), RateableItem(quantity=2)])
        self.assertEqual(cost, 1000)

    def test_weight_rate_uses_product_weight(self):
        rate = self._rate(rate_type=ShippingRateType.WEIGHT, base_cents=500)
        cost = calculate_rate_cents(rate, subtotal_cents=0, items=[RateableItem(quantity=2, weight_kg=Decimal("1.500"))])
        # 2 * 1.5kg = 3.0kg -> 3 whole-kg units * 500
        self.assertEqual(cost, 1500)

    def test_weight_rate_with_missing_weight_is_unavailable(self):
        # Missing weight data must make a WEIGHT rate unavailable (None),
        # never silently price it as free shipping.
        rate = self._rate(rate_type=ShippingRateType.WEIGHT, base_cents=500)
        cost = calculate_rate_cents(rate, subtotal_cents=0, items=[RateableItem(quantity=5, weight_kg=None)])
        self.assertIsNone(cost)

    def test_weight_rate_partial_missing_weight_is_unavailable(self):
        rate = self._rate(rate_type=ShippingRateType.WEIGHT, base_cents=500)
        cost = calculate_rate_cents(rate, subtotal_cents=0, items=[
            RateableItem(quantity=1, weight_kg=Decimal("1.0")),
            RateableItem(quantity=1, weight_kg=None),
        ])
        self.assertIsNone(cost)

    def test_weight_rate_excluded_from_options_but_flat_rate_still_offered(self):
        # End-to-end through list_shipping_options: an unweighed basket must
        # not see a free WEIGHT-type option, but an unrelated FLAT-type
        # method in the same zone is unaffected.
        self._rate(rate_type=ShippingRateType.WEIGHT, base_cents=500)
        flat_method = ShippingMethod.objects.create(shop=self.shop, name="Flat")
        ShippingRate.objects.create(method=flat_method, zone=self.zone, rate_type=ShippingRateType.FLAT, base_cents=800)
        address = _make_address(self.owner, country="NG")
        options = list_shipping_options(
            shop=self.shop, address=address, subtotal_cents=5000,
            items=[RateableItem(quantity=1, weight_kg=None)],
        )
        method_ids = {o["shipping_method_id"] for o in options}
        self.assertNotIn(str(self.method.id), method_ids)
        self.assertIn(str(flat_method.id), method_ids)


class CheckoutShippingIntegrationTests(TestCase):
    """place_marketplace_order's shipping integration - server-authoritative
    cost, Fulfillment creation, and backward compatibility when no shipping
    is requested at all."""

    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557730001", username="checkout_ship_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5557740001", username="checkout_ship_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "checkoutship")
        self.product = Product.objects.create(
            shop=self.shop, sku="SHIP-001", name="Shippable Widget", slug="shippable-widget",
            price=Decimal("50.00"), stock_qty=20, currency="USD",
        )
        self.zone = ShippingZone.objects.create(shop=self.shop, name="NG", country="NG")
        self.method, self.rate = _make_method_and_rate(self.shop, self.zone, rate_type=ShippingRateType.FLAT, base_cents=800)
        self.address = _make_address(self.buyer, country="NG")

    def test_order_without_shipping_selection_is_unaffected(self):
        order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        self.assertEqual(order.total_amount, Decimal("50"))
        self.assertFalse(Fulfillment.objects.filter(order=order).exists())

    def test_order_with_shipping_adds_cost_and_creates_fulfillment(self):
        order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
            metadata={"address_id": str(self.address.id), "shipping_method_id": str(self.method.id)},
        )
        self.assertEqual(order.total_amount, Decimal("58"))  # 50 + 8 shipping
        self.assertEqual(order.metadata.get("shipping_amount_cents"), 800)
        fulfillment = Fulfillment.objects.get(order=order)
        self.assertEqual(fulfillment.shop_id, self.shop.id)
        self.assertEqual(fulfillment.status, FulfillmentStatus.PENDING)
        self.assertEqual(fulfillment.shipping_cost_cents, 800)
        self.assertEqual(fulfillment.delivery_address_snapshot["recipient_name"], "Test Recipient")

    def test_address_snapshot_is_frozen_against_later_edits(self):
        order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
            metadata={"address_id": str(self.address.id), "shipping_method_id": str(self.method.id)},
        )
        self.address.street_address = "A Completely Different Street"
        self.address.save()
        fulfillment = Fulfillment.objects.get(order=order)
        self.assertEqual(fulfillment.delivery_address_snapshot["street_address"], "1 Test Street")

    def test_cannot_use_another_users_address(self):
        other_buyer = get_user_model().objects.create_user(phone="5557740099", username="other_ship_buyer", password="secret", country="NG")
        other_address = _make_address(other_buyer)
        with self.assertRaises(ValidationError):
            place_marketplace_order(
                buyer=self.buyer, shop_id=self.shop.id,
                items=[{"product_id": str(self.product.id), "quantity": 1}],
                metadata={"address_id": str(other_address.id), "shipping_method_id": str(self.method.id)},
            )

    def test_shipping_method_not_available_for_address_is_rejected(self):
        far_address = _make_address(self.buyer, country="US", state="Texas", city="Austin")
        with self.assertRaises(ValidationError):
            place_marketplace_order(
                buyer=self.buyer, shop_id=self.shop.id,
                items=[{"product_id": str(self.product.id), "quantity": 1}],
                metadata={"address_id": str(far_address.id), "shipping_method_id": str(self.method.id)},
            )

    def test_client_supplied_shipping_cost_is_ignored(self):
        order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
            metadata={
                "address_id": str(self.address.id), "shipping_method_id": str(self.method.id),
                "shipping_amount_cents": 1,  # fabricated - must be ignored
            },
        )
        self.assertEqual(order.metadata["shipping_amount_cents"], 800)
        self.assertEqual(order.total_amount, Decimal("58"))


class FulfillmentTransitionTests(TestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557750001", username="transition_owner", password="secret", country="NG")
        self.outsider = User.objects.create_user(phone="5557750002", username="transition_outsider", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5557750003", username="transition_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "transition")
        self.product = Product.objects.create(
            shop=self.shop, sku="TRANS-001", name="Transition Widget", slug="transition-widget",
            price=Decimal("20.00"), stock_qty=10, currency="USD",
        )
        self.zone = ShippingZone.objects.create(shop=self.shop, name="NG", country="NG")
        self.method, _ = _make_method_and_rate(self.shop, self.zone)
        self.address = _make_address(self.buyer, country="NG")
        self.order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
            metadata={"address_id": str(self.address.id), "shipping_method_id": str(self.method.id)},
        )
        self.fulfillment = Fulfillment.objects.get(order=self.order)

    def test_valid_transition_sequence(self):
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.ACCEPTED, actor=self.owner)
        self.fulfillment.refresh_from_db()
        self.assertEqual(self.fulfillment.status, FulfillmentStatus.ACCEPTED)
        self.assertIsNotNone(self.fulfillment.accepted_at)

        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.PROCESSING, actor=self.owner)
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.READY_FOR_SHIPMENT, actor=self.owner)
        self.fulfillment.refresh_from_db()
        self.assertEqual(self.fulfillment.status, FulfillmentStatus.READY_FOR_SHIPMENT)
        self.assertIsNotNone(self.fulfillment.ready_at)

    def test_invalid_transition_is_rejected(self):
        with self.assertRaises(ValidationError):
            transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.SHIPPED, actor=self.owner)
        self.fulfillment.refresh_from_db()
        self.assertEqual(self.fulfillment.status, FulfillmentStatus.PENDING)

    def test_cancelled_is_terminal(self):
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.CANCELLED, actor=self.owner)
        with self.assertRaises(ValidationError):
            transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.ACCEPTED, actor=self.owner)

    def test_unrelated_user_cannot_transition(self):
        with self.assertRaises(PermissionDenied):
            transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.ACCEPTED, actor=self.outsider)

    def test_buyer_cannot_transition_their_own_order_fulfillment(self):
        # Fulfillment is the seller's responsibility - a buyer has no manage
        # rights over it even though they own the order.
        with self.assertRaises(PermissionDenied):
            transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.ACCEPTED, actor=self.buyer)

    def test_staff_can_transition_any_fulfillment(self):
        self.outsider.is_staff = True
        self.outsider.save(update_fields=["is_staff"])
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.ACCEPTED, actor=self.outsider)
        self.fulfillment.refresh_from_db()
        self.assertEqual(self.fulfillment.status, FulfillmentStatus.ACCEPTED)


class ShipmentCreationAndPartialShipmentTests(TestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557760001", username="shipment_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5557760002", username="shipment_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "shipmenttest")
        self.product = Product.objects.create(
            shop=self.shop, sku="SHIPTEST-001", name="Ten Pack Widget", slug="ten-pack-widget",
            price=Decimal("5.00"), stock_qty=50, currency="USD",
        )
        self.zone = ShippingZone.objects.create(shop=self.shop, name="NG", country="NG")
        self.method, _ = _make_method_and_rate(self.shop, self.zone)
        self.address = _make_address(self.buyer, country="NG")
        self.order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 10}],
            metadata={"address_id": str(self.address.id), "shipping_method_id": str(self.method.id)},
        )
        self.fulfillment = Fulfillment.objects.get(order=self.order)
        self.order_item = self.order.items.get()
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.ACCEPTED, actor=self.owner)
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.PROCESSING, actor=self.owner)
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.READY_FOR_SHIPMENT, actor=self.owner)

    def test_full_shipment_marks_fulfillment_shipped(self):
        shipment = create_shipment(
            fulfillment_id=self.fulfillment.id, actor=self.owner,
            items=[{"order_item_id": str(self.order_item.id), "quantity": 10}],
        )
        self.assertEqual(shipment.items.get().quantity, 10)
        self.fulfillment.refresh_from_db()
        self.assertEqual(self.fulfillment.status, FulfillmentStatus.SHIPPED)
        self.assertIsNotNone(self.fulfillment.shipped_at)
        self.assertTrue(ShipmentEvent.objects.filter(shipment=shipment, status=ShipmentStatus.PENDING).exists())

    def test_partial_shipment_sequence(self):
        create_shipment(
            fulfillment_id=self.fulfillment.id, actor=self.owner,
            items=[{"order_item_id": str(self.order_item.id), "quantity": 6}],
        )
        self.fulfillment.refresh_from_db()
        self.assertEqual(self.fulfillment.status, FulfillmentStatus.PARTIALLY_SHIPPED)

        create_shipment(
            fulfillment_id=self.fulfillment.id, actor=self.owner,
            items=[{"order_item_id": str(self.order_item.id), "quantity": 4}],
        )
        self.fulfillment.refresh_from_db()
        self.assertEqual(self.fulfillment.status, FulfillmentStatus.SHIPPED)
        self.assertEqual(Shipment.objects.filter(fulfillment=self.fulfillment).count(), 2)

    def test_cannot_ship_more_than_ordered(self):
        with self.assertRaises(ValidationError):
            create_shipment(
                fulfillment_id=self.fulfillment.id, actor=self.owner,
                items=[{"order_item_id": str(self.order_item.id), "quantity": 11}],
            )

    def test_cannot_ship_already_fully_shipped_item_again(self):
        create_shipment(
            fulfillment_id=self.fulfillment.id, actor=self.owner,
            items=[{"order_item_id": str(self.order_item.id), "quantity": 10}],
        )
        with self.assertRaises(ValidationError):
            create_shipment(
                fulfillment_id=self.fulfillment.id, actor=self.owner,
                items=[{"order_item_id": str(self.order_item.id), "quantity": 1}],
            )

    def test_unrelated_seller_cannot_create_shipment(self):
        other_owner = get_user_model().objects.create_user(phone="5557760099", username="other_shipment_owner", password="secret", country="NG")
        with self.assertRaises(PermissionDenied):
            create_shipment(
                fulfillment_id=self.fulfillment.id, actor=other_owner,
                items=[{"order_item_id": str(self.order_item.id), "quantity": 1}],
            )

    def test_idempotency_key_prevents_duplicate_shipment(self):
        first = create_shipment(
            fulfillment_id=self.fulfillment.id, actor=self.owner,
            items=[{"order_item_id": str(self.order_item.id), "quantity": 5}],
            idempotency_key="retry-key-1",
        )
        second = create_shipment(
            fulfillment_id=self.fulfillment.id, actor=self.owner,
            items=[{"order_item_id": str(self.order_item.id), "quantity": 5}],
            idempotency_key="retry-key-1",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(Shipment.objects.filter(fulfillment=self.fulfillment).count(), 1)


class ShipmentConcurrencyTests(TransactionTestCase):
    """Real concurrency test (spec §23): two threads race to ship the last
    remaining unit of the same order item. Uses TransactionTestCase, not
    TestCase, because the default TestCase wraps each test in one shared
    transaction - two threads would see the same uncommitted state and the
    race could never actually manifest."""

    def setUp(self):
        post_save.disconnect(on_product_save, sender=Product)
        self.addCleanup(post_save.connect, on_product_save, sender=Product)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557770001", username="race_ship_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5557770002", username="race_ship_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "raceship")
        self.product = Product.objects.create(
            shop=self.shop, sku="RACESHIP-001", name="Last Unit Widget", slug="raceship-last-unit-widget",
            price=Decimal("9.00"), stock_qty=5, currency="USD",
        )
        self.zone = ShippingZone.objects.create(shop=self.shop, name="NG", country="NG")
        self.method, _ = _make_method_and_rate(self.shop, self.zone)
        self.address = _make_address(self.buyer, country="NG")
        self.order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
            metadata={"address_id": str(self.address.id), "shipping_method_id": str(self.method.id)},
        )
        self.fulfillment = Fulfillment.objects.get(order=self.order)
        self.order_item = self.order.items.get()
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.ACCEPTED, actor=self.owner)
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.PROCESSING, actor=self.owner)
        transition_fulfillment(self.fulfillment.id, target_status=FulfillmentStatus.READY_FOR_SHIPMENT, actor=self.owner)

    def test_two_concurrent_shipment_creations_for_the_only_unit(self):
        from django.db import connections

        results = {}

        def attempt(key):
            try:
                create_shipment(
                    fulfillment_id=self.fulfillment.id, actor=self.owner,
                    items=[{"order_item_id": str(self.order_item.id), "quantity": 1}],
                )
                results[key] = "success"
            except ValidationError:
                results[key] = "rejected"
            finally:
                connections.close_all()

        t1 = threading.Thread(target=attempt, args=("a",))
        t2 = threading.Thread(target=attempt, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        outcomes = list(results.values())
        self.assertEqual(outcomes.count("success"), 1, f"expected exactly one winner, got {results}")
        self.assertEqual(outcomes.count("rejected"), 1, f"expected exactly one rejection, got {results}")
        self.assertEqual(Shipment.objects.filter(fulfillment=self.fulfillment).count(), 1)


class ShipmentTransitionAndEventTests(TestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557780001", username="shiptrans_owner", password="secret", country="NG")
        self.outsider = User.objects.create_user(phone="5557780002", username="shiptrans_outsider", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5557780003", username="shiptrans_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "shiptrans")
        self.product = Product.objects.create(
            shop=self.shop, sku="SHIPTRANS-001", name="Trackable Widget", slug="trackable-widget",
            price=Decimal("30.00"), stock_qty=10, currency="USD",
        )
        self.zone = ShippingZone.objects.create(shop=self.shop, name="NG", country="NG")
        self.method, _ = _make_method_and_rate(self.shop, self.zone)
        self.address = _make_address(self.buyer, country="NG")
        self.order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
            metadata={"address_id": str(self.address.id), "shipping_method_id": str(self.method.id)},
        )
        self.fulfillment = Fulfillment.objects.get(order=self.order)
        for target in (FulfillmentStatus.ACCEPTED, FulfillmentStatus.PROCESSING, FulfillmentStatus.READY_FOR_SHIPMENT):
            transition_fulfillment(self.fulfillment.id, target_status=target, actor=self.owner)
        self.shipment = create_shipment(
            fulfillment_id=self.fulfillment.id, actor=self.owner,
            items=[{"order_item_id": str(self.order.items.get().id), "quantity": 1}],
        )

    def test_full_valid_lifecycle_and_fulfillment_delivered_cascade(self):
        sequence = [
            ShipmentStatus.LABEL_CREATED, ShipmentStatus.READY_FOR_PICKUP, ShipmentStatus.PICKED_UP,
            ShipmentStatus.IN_TRANSIT, ShipmentStatus.OUT_FOR_DELIVERY, ShipmentStatus.DELIVERED,
        ]
        for target in sequence:
            transition_shipment(shipment_id=self.shipment.id, actor=self.owner, target_status=target)
        self.shipment.refresh_from_db()
        self.fulfillment.refresh_from_db()
        self.assertEqual(self.shipment.status, ShipmentStatus.DELIVERED)
        self.assertIsNotNone(self.shipment.delivered_at)
        self.assertEqual(self.fulfillment.status, FulfillmentStatus.DELIVERED)
        self.assertEqual(self.shipment.events.count(), len(sequence) + 1)  # +1 for the initial PENDING event

    def test_invalid_shipment_transition_rejected(self):
        with self.assertRaises(ValidationError):
            transition_shipment(shipment_id=self.shipment.id, actor=self.owner, target_status=ShipmentStatus.DELIVERED)

    def test_delivery_failed_then_retry_then_delivered(self):
        for target in (ShipmentStatus.LABEL_CREATED, ShipmentStatus.READY_FOR_PICKUP, ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT):
            transition_shipment(shipment_id=self.shipment.id, actor=self.owner, target_status=target)
        transition_shipment(shipment_id=self.shipment.id, actor=self.owner, target_status=ShipmentStatus.DELIVERY_FAILED, description="No one home")
        transition_shipment(shipment_id=self.shipment.id, actor=self.owner, target_status=ShipmentStatus.OUT_FOR_DELIVERY)
        transition_shipment(shipment_id=self.shipment.id, actor=self.owner, target_status=ShipmentStatus.DELIVERED)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, ShipmentStatus.DELIVERED)

    def test_unrelated_seller_cannot_transition_shipment(self):
        with self.assertRaises(PermissionDenied):
            transition_shipment(shipment_id=self.shipment.id, actor=self.outsider, target_status=ShipmentStatus.LABEL_CREATED)

    def test_duplicate_external_event_id_is_idempotent(self):
        first = record_shipment_event(self.shipment, status=self.shipment.status, description="carrier ping", external_event_id="carrier-evt-1")
        second = record_shipment_event(self.shipment, status=self.shipment.status, description="carrier ping (retry)", external_event_id="carrier-evt-1")
        self.assertEqual(first.id, second.id)
        self.assertEqual(ShipmentEvent.objects.filter(shipment=self.shipment, external_event_id="carrier-evt-1").count(), 1)


class MultiSellerFulfillmentIsolationAPITests(APITestCase):
    """KIS's checkout already creates one MarketplaceOrder per shop (the
    per-shop Cart design) - a buyer checking out items from two shops in
    one visit produces two independent orders, each with its own
    Fulfillment. This verifies that isolation holds end-to-end through the
    real API, not just at the service layer."""

    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner_a = User.objects.create_user(phone="5557790001", username="multiseller_owner_a", password="secret", country="NG")
        self.owner_b = User.objects.create_user(phone="5557790002", username="multiseller_owner_b", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5557790003", username="multiseller_buyer", password="secret", country="NG")
        self.shop_a = _make_shop(self.owner_a, "multisellera")
        self.shop_b = _make_shop(self.owner_b, "multisellerb")
        self.product_a = Product.objects.create(shop=self.shop_a, sku="MSA-001", name="Shop A Widget", slug="shop-a-widget", price=Decimal("10.00"), stock_qty=10, currency="USD")
        self.product_b = Product.objects.create(shop=self.shop_b, sku="MSB-001", name="Shop B Widget", slug="shop-b-widget", price=Decimal("15.00"), stock_qty=10, currency="USD")
        self.zone_a = ShippingZone.objects.create(shop=self.shop_a, name="NG", country="NG")
        self.zone_b = ShippingZone.objects.create(shop=self.shop_b, name="NG", country="NG")
        self.method_a, _ = _make_method_and_rate(self.shop_a, self.zone_a, base_cents=500)
        self.method_b, _ = _make_method_and_rate(self.shop_b, self.zone_b, base_cents=700)
        self.address = _make_address(self.buyer, country="NG")

        self.order_a = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop_a.id, items=[{"product_id": str(self.product_a.id), "quantity": 1}],
            metadata={"address_id": str(self.address.id), "shipping_method_id": str(self.method_a.id)},
        )
        self.order_b = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop_b.id, items=[{"product_id": str(self.product_b.id), "quantity": 1}],
            metadata={"address_id": str(self.address.id), "shipping_method_id": str(self.method_b.id)},
        )
        self.fulfillment_a = Fulfillment.objects.get(order=self.order_a)
        self.fulfillment_b = Fulfillment.objects.get(order=self.order_b)

    def test_each_order_has_its_own_fulfillment_with_correct_shipping_cost(self):
        self.assertEqual(self.fulfillment_a.shipping_cost_cents, 500)
        self.assertEqual(self.fulfillment_b.shipping_cost_cents, 700)
        self.assertNotEqual(self.fulfillment_a.id, self.fulfillment_b.id)

    def test_seller_a_cannot_list_or_view_seller_bs_fulfillment(self):
        self.client.force_authenticate(self.owner_a)
        list_response = self.client.get("/api/v1/commerce/fulfillments/")
        ids = {row["id"] for row in list_response.data.get("results", list_response.data)}
        self.assertNotIn(str(self.fulfillment_b.id), ids)
        self.assertIn(str(self.fulfillment_a.id), ids)

        detail_response = self.client.get(f"/api/v1/commerce/fulfillments/{self.fulfillment_b.id}/")
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_seller_a_cannot_accept_seller_bs_fulfillment(self):
        self.client.force_authenticate(self.owner_a)
        response = self.client.post(f"/api/v1/commerce/fulfillments/{self.fulfillment_b.id}/accept/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.fulfillment_b.refresh_from_db()
        self.assertEqual(self.fulfillment_b.status, FulfillmentStatus.PENDING)

    def test_buyer_can_view_both_fulfillments_via_order_detail(self):
        self.client.force_authenticate(self.buyer)
        resp_a = self.client.get(f"/api/v1/commerce/marketplace-orders/{self.order_a.id}/fulfillment/")
        resp_b = self.client.get(f"/api/v1/commerce/marketplace-orders/{self.order_b.id}/fulfillment/")
        self.assertEqual(resp_a.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_b.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_a.data["shop_name"], self.shop_a.name)
        self.assertEqual(resp_b.data["shop_name"], self.shop_b.name)

    def test_stranger_buyer_cannot_view_fulfillment_via_order_detail(self):
        stranger = get_user_model().objects.create_user(phone="5557790099", username="multiseller_stranger", password="secret", country="NG")
        self.client.force_authenticate(stranger)
        response = self.client.get(f"/api/v1/commerce/marketplace-orders/{self.order_a.id}/fulfillment/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ShippingOptionsAPITests(APITestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557800001", username="options_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5557800002", username="options_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "optionstest")
        self.product = Product.objects.create(
            shop=self.shop, sku="OPT-001", name="Option Widget", slug="option-widget",
            price=Decimal("40.00"), stock_qty=10, currency="USD",
        )
        self.zone = ShippingZone.objects.create(shop=self.shop, name="NG", country="NG")
        self.method, self.rate = _make_method_and_rate(self.shop, self.zone, base_cents=600)
        self.address = _make_address(self.buyer, country="NG")

    def test_options_reflect_real_catalog_price_not_client_input(self):
        self.client.force_authenticate(self.buyer)
        response = self.client.post("/api/v1/commerce/shipping/options/", {
            "shop_id": str(self.shop.id),
            "address_id": str(self.address.id),
            "items": [{"product_id": str(self.product.id), "quantity": 2, "unit_price_cents": 1}],  # fabricated price
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["subtotal_cents"], 8000)  # 2 * 4000, not 2 * 1
        self.assertEqual(len(response.data["options"]), 1)
        self.assertEqual(response.data["options"][0]["cost_cents"], 600)

    def test_options_empty_for_unsupported_address(self):
        self.client.force_authenticate(self.buyer)
        far_address = _make_address(self.buyer, country="FR", state="", city="Paris")
        response = self.client.post("/api/v1/commerce/shipping/options/", {
            "shop_id": str(self.shop.id),
            "address_id": str(far_address.id),
            "items": [{"product_id": str(self.product.id), "quantity": 1}],
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["options"], [])


class CustomerAddressAPITests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.buyer = User.objects.create_user(phone="5557810001", username="addr_buyer", password="secret", country="NG")
        self.other_buyer = User.objects.create_user(phone="5557810002", username="addr_other", password="secret", country="NG")

    def test_create_and_list_own_address(self):
        self.client.force_authenticate(self.buyer)
        response = self.client.post("/api/v1/commerce/addresses/", {
            "recipient_name": "Jane Doe", "recipient_phone": "08011112222",
            "country": "NG", "state": "Lagos", "city": "Lagos", "street_address": "12 Palm Ave",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        list_response = self.client.get("/api/v1/commerce/addresses/")
        results = list_response.data.get("results", list_response.data)
        self.assertEqual(len(results), 1)

    def test_setting_a_new_default_clears_the_old_one(self):
        self.client.force_authenticate(self.buyer)
        first = _make_address(self.buyer, is_default=True)
        second = self.client.post("/api/v1/commerce/addresses/", {
            "recipient_name": "Second", "recipient_phone": "08022223333",
            "country": "NG", "street_address": "2nd St", "is_default": True,
        }, format="json").data
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(CustomerAddress.objects.get(id=second["id"]).is_default)

    def test_cannot_see_or_modify_another_users_address(self):
        other_address = _make_address(self.other_buyer)
        self.client.force_authenticate(self.buyer)
        list_response = self.client.get("/api/v1/commerce/addresses/")
        ids = {row["id"] for row in list_response.data.get("results", list_response.data)}
        self.assertNotIn(str(other_address.id), ids)

        detail_response = self.client.get(f"/api/v1/commerce/addresses/{other_address.id}/")
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)

        patch_response = self.client.patch(f"/api/v1/commerce/addresses/{other_address.id}/", {"city": "Hijacked"}, format="json")
        self.assertEqual(patch_response.status_code, status.HTTP_404_NOT_FOUND)
        other_address.refresh_from_db()
        self.assertNotEqual(other_address.city, "Hijacked")

    def test_delete_is_a_soft_delete(self):
        self.client.force_authenticate(self.buyer)
        address = _make_address(self.buyer)
        response = self.client.delete(f"/api/v1/commerce/addresses/{address.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        address.refresh_from_db()
        self.assertTrue(address.is_deleted)


class ShippingConfigSellerIsolationAPITests(APITestCase):
    """A seller must never be able to configure or discover another shop's
    shipping zones/methods/rates - this is where a malicious seller could
    otherwise try to manipulate a competitor's shipping price (spec §22)."""

    def setUp(self):
        User = get_user_model()
        self.owner_a = User.objects.create_user(phone="5557820001", username="shipconfig_owner_a", password="secret", country="NG")
        self.owner_b = User.objects.create_user(phone="5557820002", username="shipconfig_owner_b", password="secret", country="NG")
        self.shop_a = _make_shop(self.owner_a, "shipconfiga")
        self.shop_b = _make_shop(self.owner_b, "shipconfigb")
        self.zone_b = ShippingZone.objects.create(shop=self.shop_b, name="B's zone", country="NG")
        self.method_b = ShippingMethod.objects.create(shop=self.shop_b, name="B's method")
        self.rate_b = ShippingRate.objects.create(method=self.method_b, zone=self.zone_b, rate_type=ShippingRateType.FLAT, base_cents=500)

    def test_seller_a_cannot_create_a_zone_for_shop_b(self):
        self.client.force_authenticate(self.owner_a)
        response = self.client.post("/api/v1/commerce/shipping-zones/", {
            "shop": str(self.shop_b.id), "name": "Hijacked zone", "country": "NG",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_seller_a_cannot_see_shop_bs_zones_methods_or_rates(self):
        self.client.force_authenticate(self.owner_a)
        for url in ("/api/v1/commerce/shipping-zones/", "/api/v1/commerce/shipping-methods/", "/api/v1/commerce/shipping-rates/"):
            response = self.client.get(url)
            results = response.data.get("results", response.data)
            ids = {row["id"] for row in results}
            self.assertNotIn(str(self.zone_b.id), ids)
            self.assertNotIn(str(self.method_b.id), ids)
            self.assertNotIn(str(self.rate_b.id), ids)

    def test_seller_a_cannot_change_shop_bs_rate_price(self):
        self.client.force_authenticate(self.owner_a)
        response = self.client.patch(f"/api/v1/commerce/shipping-rates/{self.rate_b.id}/", {"base_cents": 1}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.rate_b.refresh_from_db()
        self.assertEqual(self.rate_b.base_cents, 500)

    def test_staff_can_see_all_shops_shipping_config(self):
        self.owner_a.is_staff = True
        self.owner_a.save(update_fields=["is_staff"])
        self.client.force_authenticate(self.owner_a)
        response = self.client.get("/api/v1/commerce/shipping-zones/")
        ids = {row["id"] for row in response.data.get("results", response.data)}
        self.assertIn(str(self.zone_b.id), ids)


class ShippingOptionsNoImplicitTransactionAPITests(APITransactionTestCase):
    """Regression test: ShippingOptionsView.post() calls
    _normalize_marketplace_items, which uses select_for_update() -
    Postgres rejects that outside an open transaction. APITestCase (used by
    every other test in this file) wraps each test in its own transaction,
    which silently supplied one and would never have caught this - it took
    a real, non-test request against a live runserver to surface it. Only
    APITransactionTestCase (no implicit wrapping transaction) can actually
    exercise the same "no transaction already open" condition a real
    request hits. This was a genuine bug, fixed by wrapping the call in its
    own transaction.atomic() inside the view."""

    def setUp(self):
        post_save.disconnect(on_product_save, sender=Product)
        self.addCleanup(post_save.connect, on_product_save, sender=Product)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557830001", username="notrans_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5557830002", username="notrans_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "notrans")
        self.product = Product.objects.create(
            shop=self.shop, sku="NOTRANS-001", name="No Transaction Widget", slug="no-transaction-widget",
            price=Decimal("25.00"), stock_qty=5, currency="USD",
        )
        self.zone = ShippingZone.objects.create(shop=self.shop, name="NG", country="NG")
        self.method, _ = _make_method_and_rate(self.shop, self.zone, base_cents=400)
        self.address = _make_address(self.buyer, country="NG")

    def test_shipping_options_works_without_an_ambient_transaction(self):
        self.client.force_authenticate(self.buyer)
        response = self.client.post("/api/v1/commerce/shipping/options/", {
            "shop_id": str(self.shop.id),
            "address_id": str(self.address.id),
            "items": [{"product_id": str(self.product.id), "quantity": 1}],
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["options"][0]["cost_cents"], 400)


class ProductWeightValidationTests(TestCase):
    """Product.weight_kg is the real column shipping rate calculation reads
    (distinct from the cosmetic `weight` display attribute) - must reject
    negative/unreasonable values at the API boundary, not just in Python."""

    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5557840001", username="wkg_owner", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "weighttest")

    def _serializer_errors(self, weight_kg):
        from .serializers import ProductSerializer

        serializer = ProductSerializer(data={
            "shop": str(self.shop.id), "sku": "WKG-001", "name": "Weight Test Widget",
            "price": "10.00", "stock_qty": 5, "currency": "USD", "weight_kg": weight_kg,
        })
        serializer.is_valid()
        return serializer.errors

    def test_negative_weight_is_rejected(self):
        self.assertIn("weight_kg", self._serializer_errors("-1"))

    def test_unreasonably_large_weight_is_rejected(self):
        self.assertIn("weight_kg", self._serializer_errors("5000"))

    def test_valid_weight_is_accepted(self):
        self.assertNotIn("weight_kg", self._serializer_errors("12.500"))

    def test_null_weight_is_accepted(self):
        self.assertNotIn("weight_kg", self._serializer_errors(None))
