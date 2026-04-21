# Shop System Verification

Last updated: 2026-04-15

## Verification ledger

### Baseline checks completed
- `python3 manage.py check`
  - Result: passed
  - Notes: backend structural check is clean at the current baseline.

### Broad commerce backend suite
- Check:
  - `../env/bin/python manage.py test apps.commerce.tests`
- Result: passed
- Notes:
  - full commerce backend test module is now green
  - this is the strongest backend verification point reached for the shop system so far

## Recently verified targeted fixes

### Service broadcast endpoint
- Check:
  - `../env/bin/python manage.py test apps.commerce.tests.ServiceBookingAPITests.test_service_can_be_broadcast_by_owner apps.commerce.tests.ServiceBookingAPITests.test_service_broadcast_can_be_removed`
- Result: passed
- Notes:
  - also required fixing duplicate test slugs in `ServiceBookingAPITests`.

### Service complaint submission without client-supplied escrow
- Check:
  - `../env/bin/python manage.py test apps.commerce.tests.ServiceBookingAPITests.test_payer_can_submit_complaint_without_manual_escrow_field`
- Result: passed
- Notes:
  - confirms complaint create returns `201` when frontend submits only booking + user-entered complaint fields.

## Audit-based verification notes

### Product subsystem
- Verified by code inspection:
  - product CRUD endpoints exist
  - product dashboard editor wiring exists
  - product broadcast route exists
  - product details/cart/order surfaces exist
- Verified by targeted tests:
  - product serializer persists catalog categories, variants, and gallery images
  - product serializer exposes featured image + gallery image URLs
  - product broadcast create/remove endpoints work
- Verified by frontend code alignment:
  - market product types now include the backend fields used by cards/details/dashboard surfaces
  - product cart subscriptions in product/broadcast/cart pages now return proper cleanup functions
  - product details variant/quantity UI no longer relies on missing style keys or loose string inference
- Not yet fully verified:
  - full manual runtime sweep across every product surface in-app

### Product targeted tests
- Check:
  - `../env/bin/python manage.py test apps.commerce.tests.ProductSystemTests apps.commerce.tests.ProductBroadcastAPITests`
- Result: passed
- Notes:
  - validated fixes for attribute-backed product serializer fields and list-based `variants` payload parsing
  - latest rerun passed after the frontend product-alignment fixes, confirming backend product behavior stayed stable

### Service subsystem
- Verified by code inspection:
  - service CRUD endpoints exist
  - service broadcast route exists
  - service media/category support exists
  - service booking/detail/complaint/receipt surfaces exist
- Verified by targeted fixes:
  - service broadcast route
  - complaint submission contract
  - booking-state refresh event
  - provider completion button visibility
  - service featured/gallery removal contract during edit
- Verified by frontend code alignment:
  - service editor now sends explicit featured-image and persisted-gallery removal intent
  - shop services page reads service summary/compare-price/category fields from the current backend payload
  - shared dashboard service cards now render service-oriented summary/category/price/duration information
- Not yet fully verified:
  - full manual runtime sweep across every service management surface

### Service targeted tests
- Check:
  - `../env/bin/python manage.py test apps.commerce.tests.ServiceCategorySystemTests`
- Result: passed
- Notes:
  - covers service category persistence
  - covers multipart gallery upload handling
  - now also covers featured-image clearing and targeted gallery-image removal during edit

### Booking subsystem
- Verified by code inspection:
  - create/cancel/reschedule/mark-completed/satisfy/receipt/complaint flows exist
  - frontend details and booking screens are wired
- Verified by targeted tests:
  - manager/admin can view and mark a booking completed
  - quote/no-payment bookings can later create a payment via `pay-remaining` and be satisfied successfully
  - advanced booking metadata now persists location, distance, remote region, participant count, and staff-on-site values
  - quote-required, negotiation, package pricing, add-on pricing, requirements, terms acceptance, refund policy, and reschedule policy paths all passed under a single targeted test sweep
- Verified by frontend code alignment:
  - booking review now estimates KISC totals/deposit/balance using selected package/add-ons/requested price instead of always showing the base service price only
  - booking success messaging no longer falsely claims wallet charge on quote/negotiation flows with zero deposit
  - booking detail page now converts backend-normalized booking amounts back to user-facing KISC
  - booking detail page now renders persisted booking metadata for package/add-ons/requested price/location/participants/staff/requirements/terms
  - provider completion visibility now aligns better with manager/admin access
  - booking detail page now provides a dedicated slot-picker reschedule flow that uses the real backend reschedule endpoint
- Not yet fully verified:
  - full manual verification of policy-driven UI behavior across the live app

### Landing-page subsystem
- Verified by code inspection:
  - commerce backend has `ShopLandingPage`
  - dashboard has visibility toggles and editor entry point
  - public preview/storefront path exists
- Verified by targeted implementation/tests:
  - market landing pages now persist richer builder data through commerce `shop.landing_page`
  - market landing editor now loads/saves through the commerce shop endpoint instead of generic broadcast profile storage
  - public preview/storefront keeps reading the same `shop.landing_page` payload

### Landing-page targeted tests
- Check:
  - `../env/bin/python manage.py test apps.commerce.tests.ShopLandingPageSystemTests`
- Result: passed
- Notes:
  - confirms shop PATCH can persist rich landing builder payload through `landing_page`
  - confirms GET shop detail returns the same landing sections/hero/gallery/background/logo/testimonials shape back to the frontend

### Landing-page regression sweep
- Check:
  - `../env/bin/python manage.py test apps.commerce.tests.ProductSystemTests apps.commerce.tests.ServiceCategorySystemTests apps.commerce.tests.ShopLandingPageSystemTests`
- Result: passed
- Notes:
  - used to confirm the richer landing serializer changes did not regress adjacent product/service serializer behavior

## Known unreliable or incomplete verification areas
- Repo-wide frontend type verification is not currently recorded as clean for the whole shop surface.
- `pnpm tsc --noEmit` still fails with many unrelated frontend errors plus some shop-adjacent errors, so frontend compile cannot yet be used as a clean shop-system verification gate.
- Manual end-to-end verification across:
  - dashboard
  - product management
  - service management
  - booking lifecycle
  - landing-page editor/preview/public display
  is still pending.

## Debug residue observed during audit
- Backend:
  - Phase 6 removed the main commerce/payment debug prints on the shop path.
- Frontend:
  - Phase 6 removed the main market/cart/order/shop editor logs on the shop path.
  - additional non-shop logs still exist elsewhere in the app.

## What still needs verification after the completed phase plan
- broader manual runtime verification of the updated market landing editor/preview flow
- manual app-level verification across dashboard/product/service/booking/cart/order/public storefront flows
- frontend TypeScript cleanup until `pnpm tsc --noEmit` becomes a meaningful verification gate

## Latest booking verification sweep
- Check:
  - `../env/bin/python manage.py test apps.commerce.tests.ServiceBookingAPITests.test_manager_can_view_and_mark_completed apps.commerce.tests.ServiceBookingAPITests.test_pay_remaining_creates_payment_for_quote_booking apps.commerce.tests.ServiceBookingAPITests.test_booking_metadata_persists_location_and_capacity_fields apps.commerce.tests.ServiceBookingAPITests.test_quote_required_flow_skips_payment apps.commerce.tests.ServiceBookingAPITests.test_negotiation_requests_record_requested_price apps.commerce.tests.ServiceBookingAPITests.test_package_pricing_increases_price apps.commerce.tests.ServiceBookingAPITests.test_addon_pricing_increases_price apps.commerce.tests.ServiceBookingAPITests.test_requirements_acknowledgement_is_required apps.commerce.tests.ServiceBookingAPITests.test_service_terms_acceptance_is_enforced apps.commerce.tests.ServiceBookingAPITests.test_refund_policy_window_blocks_close_cancellations apps.commerce.tests.ServiceBookingAPITests.test_reschedule_updates_schedule_and_metadata`
- Result: passed
- Notes:
  - this sweep also confirmed the move from frozen import-time booking feature flags to dynamic settings-based evaluation in `apps/commerce/views.py`

## Latest frontend compile status
- Check:
  - `pnpm tsc --noEmit`
- Result: failed
- Notes:
  - many failures remain outside the shop system
  - some failures still touch shop-adjacent files such as `BroadcastMarketPage.tsx`, `ShopDashboardScreen.tsx`, cart pages, and marketplace order detail
  - because of that, the whole shop system cannot yet be described as fully cleanly verified end to end
