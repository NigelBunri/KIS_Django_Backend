# Shop System Open Issues

Last updated: 2026-04-15

## Open issues

### 1. Market landing page uses split source of truth
- Subsystem: landing pages
- Severity: high
- Root cause:
  - commerce shop landing data is persisted through `ShopSerializer` / `ShopLandingPage`
  - market landing editing is routed through the generic profile-builder flow
  - preview/storefront behavior depends on shop-side landing fields
- Files involved:
  - [apps/commerce/models.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/models.py)
  - [apps/commerce/serializers.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/serializers.py)
  - [src/screens/market/ShopDashboardScreen.tsx](/Users/nigel/dev/KIS/src/screens/market/ShopDashboardScreen.tsx)
  - [src/screens/profile/ProfileLandingEditorScreen.tsx](/Users/nigel/dev/KIS/src/screens/profile/ProfileLandingEditorScreen.tsx)
- Status: fixed
- State: Phase 5 completed; market landing editing now loads/saves through commerce `shop.landing_page` instead of the separate generic profile-builder store

### 2. Landing-page UI promises more than commerce persistence stores
- Subsystem: landing pages
- Severity: high
- Root cause:
  - dashboard/editor advertises hero, products, services, testimonials, contact, FAQs and richer dynamic sections
  - commerce landing persistence currently stores only a narrow hero/visibility subset
- Files involved:
  - [apps/commerce/serializers.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/serializers.py)
  - [apps/commerce/models.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/models.py)
  - [src/screens/market/ShopDashboardScreen.tsx](/Users/nigel/dev/KIS/src/screens/market/ShopDashboardScreen.tsx)
  - [src/screens/profile/ProfileLandingEditorScreen.tsx](/Users/nigel/dev/KIS/src/screens/profile/ProfileLandingEditorScreen.tsx)
- Status: fixed
- State: Phase 5 completed; commerce `ShopLandingPage.builder_data` now stores the richer landing-builder payload while serializer keeps legacy hero/visibility fields aligned

### 3. Product subsystem needs completion-grade verification, not assumption
- Subsystem: products
- Severity: medium
- Root cause:
  - products appear closest to complete, but no full completion verification ledger exists yet
  - cart/order/broadcast/card/detail flows have had multiple bug fixes and still need broad confirmation
- Files involved:
  - product/cart/order/broadcast frontend screens
  - `apps/commerce/views.py`
  - `apps/commerce/serializers.py`
  - `apps/commerce/tests.py`
- Status: fixed
- State: Phase 2 completed; targeted backend verification passed and no remaining product-specific blocker is currently known

### 4. Service subsystem is feature-rich but still in stabilization
- Subsystem: services
- Severity: medium
- Root cause:
  - service CRUD, media, categories, booking integration, broadcasting, and complaints exist
  - repeated recent fixes indicate real misalignment risk across create/edit/display flows
- Files involved:
  - `apps/commerce/models.py`
  - `apps/commerce/serializers.py`
  - `apps/commerce/views.py`
  - [src/screens/market/ServiceEditorDrawer.tsx](/Users/nigel/dev/KIS/src/screens/market/ServiceEditorDrawer.tsx)
  - [src/screens/market/ShopDashboardScreen.tsx](/Users/nigel/dev/KIS/src/screens/market/ShopDashboardScreen.tsx)
  - service display screens
- Status: fixed
- State: Phase 3 completed for service CRUD/media/display alignment; remaining lifecycle work belongs to booking verification in Phase 4

### 12. Service media removal semantics were previously local-only
- Subsystem: services / media
- Severity: medium
- Root cause:
  - service editor could remove featured/gallery images from local state
  - backend had no explicit API contract to clear the featured image or delete selected persisted gallery images during edit
- Files involved:
  - [apps/commerce/serializers.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/serializers.py)
  - [apps/commerce/tests.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/tests.py)
  - [ServiceEditorDrawer.tsx](/Users/nigel/dev/KIS/src/screens/market/ServiceEditorDrawer.tsx)
  - [ShopDashboardScreen.tsx](/Users/nigel/dev/KIS/src/screens/market/ShopDashboardScreen.tsx)
- Status: fixed
- State: verified by targeted serializer tests

### 5. Booking subsystem needs broader end-to-end verification
- Subsystem: bookings
- Severity: medium
- Root cause:
  - rich booking contract now exists, but policy, receipts, complaints, completion, reschedule, and display flows span many screens/endpoints
  - targeted bugs were fixed, but full regression coverage is still not established
- Files involved:
  - `apps/commerce/views.py`
  - `apps/commerce/serializers.py`
  - `apps/commerce/tests.py`
  - [src/screens/market/ServiceBookingScreen.tsx](/Users/nigel/dev/KIS/src/screens/market/ServiceBookingScreen.tsx)
  - [src/screens/market/ServiceBookingDetailsPage.tsx](/Users/nigel/dev/KIS/src/screens/market/ServiceBookingDetailsPage.tsx)
- Status: fixed
- State:
  - Phase 4 has fixed major backend lifecycle gaps:
    - manager/admin booking access
    - pay-remaining creating a real payment for later satisfaction
    - dynamic policy flags
    - advanced booking metadata persistence
    - complaint queryset visibility
  - Phase 4 has also fixed major frontend lifecycle gaps:
    - booking review amount estimation
    - booking detail amount conversion back to KISC
    - richer metadata display on booking details
    - more accurate policy/completion messaging
    - dedicated frontend reschedule slot-picker interaction
  - Broader runtime/manual verification still pending, but that is now a Phase 6 stabilization concern rather than a Phase 4 contract gap

### 6. Debug residue remains in production paths
- Subsystem: stabilization / cleanup
- Severity: low
- Root cause:
  - `print(...)` and `console.log(...)` remain in commerce backend and market/broadcast frontend surfaces
- Files involved:
  - [apps/commerce/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/views.py)
  - [apps/commerce/serializers.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/serializers.py)
  - [src/screens/broadcast/pages/BroadcastMarketPage.tsx](/Users/nigel/dev/KIS/src/screens/broadcast/pages/BroadcastMarketPage.tsx)
  - [src/screens/market/ServiceBookingDetailsPage.tsx](/Users/nigel/dev/KIS/src/screens/market/ServiceBookingDetailsPage.tsx)
  - other frontend files noted in verification
- Status: open
- State: partially fixed in Phase 6; commerce/market critical paths were cleaned, but unrelated frontend logs still remain elsewhere

### 16. Repo-wide frontend TypeScript is still not clean, including some shop surfaces
- Subsystem: frontend verification / shop-adjacent UI
- Severity: medium
- Root cause:
  - the repo still has broad TypeScript debt across multiple modules
  - some remaining errors are still in shop-adjacent files such as `BroadcastMarketPage.tsx`, `ShopDashboardScreen.tsx`, cart pages, and order detail pages
- Files involved:
  - [src/screens/broadcast/pages/BroadcastMarketPage.tsx](/Users/nigel/dev/KIS/src/screens/broadcast/pages/BroadcastMarketPage.tsx)
  - [src/screens/market/ShopDashboardScreen.tsx](/Users/nigel/dev/KIS/src/screens/market/ShopDashboardScreen.tsx)
  - [src/screens/market/cart/CartDetailPage.tsx](/Users/nigel/dev/KIS/src/screens/market/cart/CartDetailPage.tsx)
  - [src/screens/market/cart/CartsListPage.tsx](/Users/nigel/dev/KIS/src/screens/market/cart/CartsListPage.tsx)
  - [src/screens/market/orders/MarketplaceOrderDetailPage.tsx](/Users/nigel/dev/KIS/src/screens/market/orders/MarketplaceOrderDetailPage.tsx)
  - plus non-shop files listed in `docs/shop-system-verification.md`
- Status: open
- State: pending frontend cleanup; this is the main remaining blocker to calling the whole shop system fully cleanly verified end to end

### 15. Shop PATCH updates were blocked by a wrong image field reference
- Subsystem: shops / backend update flow
- Severity: high
- Root cause:
  - `ShopViewSet.perform_update()` checked `serializer.instance.main_image`
  - `Shop` uses `image_file`, so any PATCH without a new upload could crash or reject incorrectly
- Files involved:
  - [apps/commerce/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/views.py)
- Status: fixed
- State: fixed during Phase 5 while verifying landing-page saves through the shop endpoint

## Recently fixed issues relevant to this project

### 7. Product serializer could drop or mis-handle frontend product payload fields
- Subsystem: products
- Severity: high
- Root cause:
  - attribute-backed frontend product fields like `available_sizes`, `available_colors`, and `compare_at_price` could leak into model create/update kwargs
  - list-valued `variants` payloads were being dropped because `_parse_json_field(...)` did not pass lists through
- Files involved:
  - [apps/commerce/serializers.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/serializers.py)
  - [apps/commerce/tests.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/tests.py)
- Status: fixed
- State: verified by targeted tests

### 8. Service broadcast endpoint was missing
- Subsystem: services / broadcast
- Severity: high
- Root cause:
  - frontend route existed, backend `ShopServiceViewSet` lacked `/broadcast/`
- Files involved:
  - [apps/commerce/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/views.py)
  - [apps/commerce/tests.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/tests.py)
- Status: fixed
- State: verified by targeted test

### 9. Service complaint creation rejected frontend payload shape
- Subsystem: bookings / complaints
- Severity: high
- Root cause:
  - serializer exposed backend-owned fields like `escrow` at the API boundary
- Files involved:
  - [apps/commerce/serializers.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/serializers.py)
  - [apps/commerce/tests.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/tests.py)
- Status: fixed
- State: verified by targeted test

### 10. Broadcast service card could crash due missing navigation variable
- Subsystem: services / broadcast frontend
- Severity: medium
- Root cause:
  - card component referenced `navigation` without creating it
- Files involved:
  - [src/screens/broadcast/pages/BroadcastMarketPage.tsx](/Users/nigel/dev/KIS/src/screens/broadcast/pages/BroadcastMarketPage.tsx)
- Status: fixed
- State: pending broader UI verification

### 11. Provider completion button stayed visible after completion
- Subsystem: bookings / frontend details page
- Severity: medium
- Root cause:
  - button visibility relied only on payment status and ignored booking completion state
- Files involved:
  - [src/screens/market/ServiceBookingDetailsPage.tsx](/Users/nigel/dev/KIS/src/screens/market/ServiceBookingDetailsPage.tsx)
- Status: fixed
- State: pending broader UI verification

### 14. Booking feature flags were previously frozen at module import time
- Subsystem: bookings / backend configuration
- Severity: high
- Root cause:
  - booking feature flags like quotes, negotiation, package pricing, addons, refund policy, reschedule policy, requirements, and terms acceptance were read once at module import
  - runtime settings changes and override-based tests did not reliably affect booking behavior
- Files involved:
  - [apps/commerce/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/views.py)
  - [apps/commerce/tests.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/commerce/tests.py)
- Status: fixed
- State: verified by targeted booking tests
