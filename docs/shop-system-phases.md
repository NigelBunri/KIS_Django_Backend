# Shop System Phases

Last updated: 2026-04-15

## Phase 0 — Establish continuity and baseline
- Goal: create stable continuation artifacts and record the current truth.
- Scope:
  - create progress docs
  - capture baseline subsystem maturity
  - record current risks and continuation rules
- Expected files/modules:
  - `docs/shop-system-progress.md`
  - `docs/shop-system-phases.md`
  - `docs/shop-system-open-issues.md`
  - `docs/shop-system-verification.md`
- Completion criteria:
  - progress files exist
  - baseline truth is documented clearly
- Status: done

## Phase 1 — Full shop-system audit refresh and gap map
- Goal: produce a precise implementation map of what is complete, partial, broken, split, or still unverified.
- Scope:
  - shop CRUD and management
  - product flows
  - service flows
  - booking flows
  - cart/order/shop grouping flows
  - landing-page flows
  - dashboard and storefront/public presentation flows
- Expected files/modules:
  - `apps/commerce/models.py`
  - `apps/commerce/serializers.py`
  - `apps/commerce/views.py`
  - `src/screens/market/*`
  - `src/screens/broadcast/*`
  - `src/screens/profile/ProfileLandingEditorScreen.tsx`
  - docs files in `docs/`
- Completion criteria:
  - structured subsystem gap map recorded
  - next coding phases can proceed without guesswork
- Status: done

## Phase 2 — Product system completion and verification
- Goal: harden the strongest subsystem and make its remaining gaps explicit.
- Scope:
  - product create/edit/delete
  - categories
  - cards/details/gallery
  - cart behavior by shop
  - totals/hydration/reload behavior
  - product broadcast integration
  - product order display paths
- Expected files/modules:
  - `apps/commerce/models.py`
  - `apps/commerce/serializers.py`
  - `apps/commerce/views.py`
  - `apps/commerce/tests.py`
  - frontend product/cart/order/broadcast screens
- Completion criteria:
  - product subsystem can be honestly called complete or near-complete
  - known remaining gaps, if any, are documented
- Status: done

## Phase 3 — Service system completion
- Goal: align service CRUD, media, categories, display, pricing, and edit rehydration.
- Scope:
  - service create/edit/delete
  - service categories
  - service cards/details/media
  - availability and pricing modes
  - advanced service fields
  - packages/add-ons/requirements/policies
  - edit-state rehydration
- Expected files/modules:
  - `apps/commerce/models.py`
  - `apps/commerce/serializers.py`
  - `apps/commerce/views.py`
  - `apps/commerce/tests.py`
  - `src/screens/market/ServiceEditorDrawer.tsx`
  - `src/screens/market/ShopDashboardScreen.tsx`
  - service display screens
- Completion criteria:
  - service CRUD and display are coherently wired
  - persisted service data rehydrates correctly
- Status: done

## Phase 4 — Booking system completion
- Goal: complete the service-booking product surface, not just the basic create call.
- Scope:
  - booking create/details/cancel/reschedule/completion/satisfaction
  - complaints and receipts
  - participant/location/coverage/terms/requirements flows
  - package/add-on integration
  - quote-required and negotiable pricing paths
  - policy-driven frontend behavior
- Expected files/modules:
  - `apps/commerce/models.py`
  - `apps/commerce/serializers.py`
  - `apps/commerce/views.py`
  - `apps/commerce/tests.py`
  - `src/screens/market/ServiceBookingScreen.tsx`
  - `src/screens/market/ServiceBookingDetailsPage.tsx`
- Completion criteria:
  - booking UX reflects true backend capability
  - policy display and enforcement are aligned
- Status: done

## Phase 5 — Landing page system unification
- Goal: make market shop landing pages one coherent system.
- Scope:
  - dashboard editor entry
  - persistence model
  - public preview/storefront rendering
  - hero/content sections
  - visibility and SEO fields
  - removal of split source-of-truth behavior
- Expected files/modules:
  - `apps/commerce/models.py`
  - `apps/commerce/serializers.py`
  - `apps/commerce/views.py`
  - `src/screens/market/ShopDashboardScreen.tsx`
  - `src/screens/profile/ProfileLandingEditorScreen.tsx`
  - landing preview/storefront screens
- Completion criteria:
  - editor, storage, and preview read/write the same source of truth
- Status: done

## Phase 6 — End-to-end system verification and cleanup
- Goal: stabilize and verify the entire shop system after subsystem completion.
- Scope:
  - backend checks and targeted tests
  - frontend verification passes
  - cross-surface consistency
  - debug residue cleanup
  - final honest completeness assessment
- Expected files/modules:
  - commerce backend
  - market/broadcast/profile frontend
  - docs verification ledger
- Completion criteria:
  - final verification summary exists
  - remaining issues are explicitly documented
- Status: done
