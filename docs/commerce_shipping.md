# Commerce shipping & fulfillment

Status: foundation implemented and live-tested. Manual/internal provider only — no
external carrier integration yet.

## Architecture

```
MarketplaceOrder (what was purchased, pre-existing, per-shop)
  └── Fulfillment (1:1 — how this shop is preparing/delivering it)
        └── Shipment (0..N — a physical delivery movement; several for partial shipment)
              ├── ShipmentItem (which order items/quantities are in this shipment)
              └── ShipmentEvent (append-only tracking history)
```

**Why Fulfillment is 1:1 with MarketplaceOrder, not 1:N.** KIS's checkout already
creates one `MarketplaceOrder` per shop — `Cart.shop` is a required FK, and
`place_marketplace_order` never spans shops. There is therefore already exactly one
seller responsible per order; the generic "one order, many seller fulfillments"
model some marketplaces need does not apply here. A buyer checking out items from
three shops in one visit produces three `MarketplaceOrder`s, each with its own
`Fulfillment` — multi-seller is handled at the order layer, which already existed,
not invented here. If KIS ever moves to a true single-order/multi-shop cart, this
is the one assumption that would need revisiting; everything below is otherwise
unaffected (a `Fulfillment` already supports 0..N `Shipment`s with no order-level
exclusivity encoded anywhere).

**Why no `FulfillmentItem`.** The generic sketch for this kind of system usually
adds a `FulfillmentItem` layer between `Fulfillment` and `MarketplaceOrderItem`.
Because `Fulfillment` is already 1:1 with the order, "this fulfillment's items" is
simply "all of the order's items" — an extra join table would carry zero
information `MarketplaceOrderItem` doesn't already have. Partial shipment is
tracked one level down instead, directly on `ShipmentItem` against
`MarketplaceOrderItem`.

## Shipping zone matching

Zones are **shop-scoped** (each seller defines their own delivery regions) and
matched **deterministically by specificity**, most specific wins:

```
postal_code_prefix > city > state > country-only ("ships anywhere in this country")
```

Ties at equal specificity break on the zone's `priority` field (higher wins), then
on `created_at` (earlier wins). Nothing hard-codes any country's administrative
divisions — a zone is just four optional string fields plus a required country.

Worked example: a shop defines zone **"Nigeria"** (country=NG only) and zone
**"Nigeria · Lagos"** (country=NG, state=Lagos). A Lagos address matches both;
"Nigeria · Lagos" wins because `state` is set and `country-only` isn't. An Abuja
address only matches "Nigeria". A US address matches neither — `list_shipping_options`
returns an empty list, and checkout proceeds without shipping for that shop (see
"Backward compatibility" below).

See `shipping_services.match_zone_for_address`.

## Rate calculation

A `ShippingRate` is the price of one `ShippingMethod` in one `ShippingZone` — at
most one active rate per (method, zone) pair, so pricing is always a single
unambiguous lookup, never a rule-priority engine. Five rate types:

| Type | Calculation |
|---|---|
| `flat` | `base_cents` |
| `free` | `0` |
| `threshold` | `0` if order subtotal ≥ `free_above_cents`, else `base_cents` |
| `quantity` | `base_cents × total item quantity` |
| `weight` | `base_cents × ceil(total weight_kg)`; **unavailable** (not `0`) if any item in the basket has no `weight_kg` set |

A `weight` rate is deliberately never offered — not priced at `$0` — when
product weight data is missing, so a seller who forgets to set
`Product.weight_kg` never accidentally gives away free shipping. If a `weight`
rate is the only rate configured for a method/zone and the basket is
unweighed, that method simply doesn't appear in the options list; other rate
types in the same zone (e.g. a `flat` fallback) are unaffected. See
`shipping_services.calculate_rate_cents` (returns `None` to signal this) and
`list_shipping_options` (filters `None` results out).

See `shipping_services.calculate_rate_cents`. Adding distance-based, dimensional,
carrier-API, or promotional rates later means adding a new `rate_type` branch here
— checkout and the options endpoint never change.

**The server is always the one computing this.** The client selects a
`shipping_method_id`; `resolve_shipping_selection` (called from both the
`/shipping/options/` preview endpoint and `place_marketplace_order` itself)
re-derives the cost, zone, and estimate from the shop's own `ShippingRate` rows
every time. No shipping price, delivery estimate, or zone is ever accepted from
the client.

## Checkout integration

`place_marketplace_order(buyer, shop_id, items, metadata)` optionally accepts
`metadata.address_id` and `metadata.shipping_method_id`:

- **Both omitted** — behaves exactly as before this feature existed. No
  `Fulfillment` is created, no shipping cost is added. This is what makes the
  feature backward-compatible with every pre-existing order-creation test and
  every shop that hasn't configured shipping yet.
- **Both present** — the address is resolved and ownership-checked (must belong
  to the buyer), the shipping method/zone/cost are re-derived server-side, the
  cost is added to the order total, and a `Fulfillment` is created in the same
  database transaction as the order and its items. If the method isn't actually
  available for that address (no matching zone, or no active rate), the whole
  order creation fails with a clear `ValidationError` — nothing is partially
  created.
- **Only one present** — rejected with a `ValidationError` (an address without a
  method, or vice versa, is treated as a client error, not silently ignored).

The address is **snapshotted** onto the `Fulfillment` (`delivery_address_snapshot`)
at order-creation time. A buyer editing or deleting their saved address later never
changes a placed order's delivery details — every fulfillment/shipment read goes
through the snapshot, never a live FK dereference into `CustomerAddress`.

## Fulfillment lifecycle

```
PENDING ─────────► ACCEPTED ─────────► PROCESSING ─────────► READY_FOR_SHIPMENT
   │                   │                    │                        │
   ▼                   ▼                    ▼                   ┌────┴────┐
CANCELLED          CANCELLED            CANCELLED                ▼         ▼
                                                          PARTIALLY_SHIPPED  SHIPPED
                                                                  │           │
                                                                  ▼           ▼
                                                               SHIPPED    DELIVERED
                                                                  │           │
                                                                  ▼           ▼
                                                              DELIVERED   RETURNED
```

`PARTIALLY_SHIPPED`/`SHIPPED` are set automatically by
`shipping_services._recompute_fulfillment_shipping_status` whenever a shipment is
created, by comparing shipped quantity against ordered quantity per line item —
sellers never set these two directly. `DELIVERED` is set automatically when every
active shipment under the fulfillment reaches `DELIVERED`
(`_maybe_mark_fulfillment_delivered`). Every other transition is explicit, through
`shipping_services.transition_fulfillment`, which is the **only** place
`Fulfillment.status` may be assigned — views never set it directly — and which
checks the transition against `FULFILLMENT_TRANSITIONS` before allowing it.

## Shipment lifecycle

```
PENDING → LABEL_CREATED → READY_FOR_PICKUP → PICKED_UP → IN_TRANSIT
                                                              │
                                              ┌───────────────┼───────────────┐
                                              ▼               ▼               ▼
                                      OUT_FOR_DELIVERY   DELIVERY_FAILED   CANCELLED
                                              │               │
                                              ▼               ▼
                                          DELIVERED    OUT_FOR_DELIVERY (retry)
                                              │               │
                                              ▼               ▼
                                          RETURNED        RETURNED / CANCELLED
```

Enforced the same way, through `shipping_services.transition_shipment` against
`SHIPMENT_TRANSITIONS`. Every transition writes a `ShipmentEvent` — status is never
silently overwritten without a corresponding history row.

## Partial shipment

A `Fulfillment` can have multiple `Shipment`s. `shipping_services.create_shipment`
validates, per order item, that `already_shipped + requested ≤ ordered` — using the
sum of `ShipmentItem.quantity` across every non-cancelled shipment for that order
item — and rejects the request otherwise. Example: an order for 10 units can ship
6 now and 4 later as two separate shipments; a third attempt to ship any more of
that line item is rejected regardless of how it's split.

## Concurrency

`create_shipment` and `transition_fulfillment`/`transition_shipment` all run inside
`transaction.atomic()` with `select_for_update()` on the `Fulfillment` (and, for
shipment creation, on the referenced `MarketplaceOrderItem` rows too) — the same
locking pattern `services.py` already uses for `Product` stock. Two concurrent
"create shipment" calls for the same fulfillment (two seller devices, or a retried
request) serialize on that lock: the second sees the first's already-shipped
quantity before deciding whether anything remains, rather than both reading stale
state and both succeeding. Verified with a real multi-threaded
`TransactionTestCase` test (`ShipmentConcurrencyTests`), not merely two sequential
requests.

## Idempotency

- **Shipment creation**: `create_shipment` accepts an optional `idempotency_key`;
  a second call with the same key against the same fulfillment returns the
  existing shipment rather than creating a duplicate.
- **Tracking events**: `record_shipment_event` accepts an optional
  `external_event_id` (for a future carrier webhook); a second delivery of the
  same event id for the same shipment returns the existing row. Carrier webhooks
  are exactly the at-least-once-delivery case this guards against.

## Provider abstraction

`shipping_services.ShippingProvider` is the interface every provider implements:
`create_shipment`, `cancel_shipment`, `get_tracking`. `ManualShippingProvider` is
the only concrete implementation today — KIS has no logistics API yet, so shipment
creation/cancellation/tracking are driven entirely by seller/admin action through
the normal API, using the exact same `Shipment`/`ShipmentEvent` models a future
real integration will write to. `ShippingMethod.provider_code` selects the
provider (`"manual"` today); adding a real carrier means adding a class to the
registry in `get_shipping_provider` and setting that code on the relevant
methods — no change to checkout, fulfillment, or the state machines.

## Security

- **Customer addresses** are strictly self-scoped — no staff override exists,
  since there's no legitimate reason for anyone but the owner to read another
  person's address book.
- **Fulfillment/Shipment access** is scoped to shops the requesting user owns or
  manages (`_provider_can_manage_shop`), or to staff. An unrelated seller's
  fulfillment/shipment id **404s**, not 403 — the same "don't even confirm it
  exists" behavior as `MarketplaceOrderViewSet` — enforced by routing every
  detail action through DRF's `get_object()` (which applies the scoped
  `get_queryset()`) before calling into the service layer, never passing a raw
  URL `pk` straight to a service function.
- **Shipping cost/zone/method** are always re-derived server-side; see "Rate
  calculation" above.
- **Buyers** can read their own order's fulfillment (`GET
  /marketplace-orders/<id>/fulfillment/`, scoped through the same
  `get_object()` the rest of `MarketplaceOrderViewSet` uses) but cannot act on
  it — fulfillment is the seller's responsibility, not the buyer's, even though
  the buyer owns the order.

## Known limitations

- Manual/internal provider only; no real carrier integration.
- No shipping-label generation or printing.
- No returns/RMA workflow yet — `RETURNED` is a terminal status on both
  `Fulfillment` and `Shipment` with no modeled return-shipment or refund
  linkage. Refund initiation on a return is an integration point for future
  returns work, not built here.
- `ShippingOptionsView` and checkout both compute options/cost synchronously
  within the request — fine at current scale; would need caching if zone/rate
  lookups become a hot path under load (not measured here — no load testing was
  performed as part of this task).
- Weight-based rates require `Product.weight_kg` to be set per product
  (seller-editable on the `ProductEditorDrawer` weight field for `PHYSICAL`
  inventory_type products). Missing weight makes the rate unavailable rather
  than under-charging — see the rate-type table above.

## Local-environment note (not a code issue)

This repo's `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` both default to
`redis://{DEV_SERVER_HOST}:...`, where `DEV_SERVER_HOST` defaults to a LAN IP
meant for the developer's own machine. In a sandboxed/cloud agent environment
without access to that LAN, **both** env vars must be overridden to a reachable
Redis (e.g. `redis://localhost:6379/0` and `.../1`) before running
`manage.py runserver` or any management command that creates a notification —
`apps.notifications.tasks.process_notification_delivery.delay(...)` blocks
indefinitely otherwise (confirmed: it is the result backend specifically, not
just the broker, that must be reachable). This is an environment-reachability
fact, not a bug in this codebase — on a real local dev machine, `DEV_SERVER_HOST`
is already a reachable address and this never surfaces.
