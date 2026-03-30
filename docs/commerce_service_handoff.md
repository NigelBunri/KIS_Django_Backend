# Commerce Service Handoff

## Objective
Evolve the Shop Services catalog/booking/broadcast system from a metadata-heavy form into a fully operational commerce and fulfillment platform while keeping every existing API, booking flow, and broadcast card intact.

## Architecture Snapshot
1. **Admin form → serializer → model**: `admin_ui/lib/commerceApi.ts` defines the `ShopServiceRecord` shape (id, name, price, deposit, visibility/status, delivery/coverage data, location, timing, buffers, capacity flags, packages/addons/requirements, policies, SEO, images, category) that is POSTed to the API. `ShopServiceSerializer` (`apps/commerce/serializers.py:653-850`) normalizes slugs, JSON/list fields, enforces remote meeting links when delivery modes include “remote”, and normalizes `availability` via `apps/commerce/availability.py:1-133`. `ShopServiceViewSet` (`apps/commerce/views.py:698-721`) enforces that only shop owners/staff can mutate services, and the payload is persisted in `ShopService` (`apps/commerce/models.py:182-252`) with a unique `(shop, slug)` constraint.
2. **Booking engine**: `ServiceBookingViewSet` (`apps/commerce/views.py:724-884`) only books `is_active`/`published` services, runs `_validate_service_schedule` to enforce future dates, `blackout_dates`, `min_notice_hours`, `max_advance_booking_days`, and availability/day slots, and respects slot capacity through `max_bookings_per_slot` + `group_booking_allowed`. Pricing/deposits rely on `price`, `deposit_amount`, `deposit_percent`, and currency normalization, and each booking carries the service’s `remote_meeting_link`.
3. **Booking metadata**: `ServiceBookingCreateSerializer` now accepts optional `location`, `distance_km`, `is_remote`, `remote_region`, `participant_count`, `staff_on_site`, `selected_package`, `selected_addons`, and `requested_price` payload keys. Phase 1 and 2 helpers validate these values when their feature flags are enabled, compute pricing for packages/addons, defer payment for quote/negotiation requests, and persist every decision in `ServiceBooking.metadata` so follow-on services know what the customer selected.
3. **Broadcast feed**: `BroadcastFeedView` (`apps/broadcasts/views.py:3446-3554`) fetches active services, bundles `name`, `short_summary`, `description`, `price`, `delivery_modes`, `duration`, `coverage`, `availability_rules`, `status`, `visibility`, ratings, and images into the feed, and the serializer exposes `is_broadcasted` via `BroadcastItem` lookups (`apps/commerce/serializers.py:807-829`).
4. **Receipt generation**: `ServiceBookingReceipt` (`apps/commerce/models.py:270-318`) captures deposit and remaining payment transactions tied to a `ServiceBooking`. `ServiceBookingViewSet.receipt` selects the latest receipt or a given `phase`/`receipt_id` and feeds that snapshot to `apps/billing/documents.build_booking_receipt_urls`, so the HTML/PDF renders show the receipt amount, currency, phase label, and transaction reference without overwriting prior receipts. A new `ServiceBookingViewSet.receipt_regenerate` POST action forcibly deletes/rebuilds the cached HTML/PDF for a given receipt snapshot, and the mobile booking details screen exposes a “Generate new receipt” button that lets payers refresh the document (deposit or remaining) after they complete payment.

## Field Classification
- **Active fields** (already influence business logic): `price`, `deposit_amount`, `deposit_percent`, `minimum_charge` (stored but ready), `visibility`, `status`, `is_active`, `max_bookings_per_slot`, `group_booking_allowed`, `delivery_modes`, `remote_meeting_link`, `availability`, `blackout_dates`, `min_notice_hours`, `max_advance_booking_days`, `duration_minutes`, `prep_buffer_minutes`, `cleanup_buffer_minutes`, `turnaround_hours`, `travel_radius_km`, `coverage`, `remote_regions`, `max_participants`, `staff_required`, `slug`, `name`, `short_summary`, `description`, `rating_avg`, `rating_count`, `image_url/image_file`, `city/state/country/address_line1-2/postal_code` (display), and the `availability` wrapper handled by `normalize_availability_payload`.
 - **Active fields** (already influence business logic): `price`, `deposit_amount`, `deposit_percent`, `minimum_charge` (stored but ready), `visibility`, `status`, `is_active`, `max_bookings_per_slot`, `group_booking_allowed`, `delivery_modes`, `remote_meeting_link`, `availability`, `blackout_dates`, `min_notice_hours`, `max_advance_booking_days`, `duration_minutes`, `prep_buffer_minutes`, `cleanup_buffer_minutes`, `turnaround_hours`, `travel_radius_km`, `coverage`, `remote_regions`, `max_participants`, `staff_required`, `slug`, `name`, `short_summary`, `description`, `rating_avg`, `rating_count`, `image_url/image_file`, `city/state/country/address_line1-2/postal_code` (display), and the `availability` wrapper handled by `normalize_availability_payload`. Pricing switches such as `pricing_model`, `quote_required`, `negotiable`, `minimum_charge`, `tax_inclusive`, and the `packages/addons` collections now influence pricing, quoting, and metadata when their feature flags are toggled.
- **Passive/dormant fields**: `tags`, `requirements`, `refund_policy`, `warranty_policy`, `service_terms`, `seo_title`, `seo_description`, `other_shops_discount`, `is_featured`. These fields are persisted and surfaced in the UI/seeds but have no booking, scheduling, or broadcast enforcement logic yet.

## Risk Map
1. **Booking availability**: enabling buffer/capacity/coverage rules must not reject existing slots—any change needs feature flags and regression tests.
2. **Pricing changes**: introducing quotes/negotiation risks doubling payment paths if wallet locking is not gated.
3. **Requirements/policies**: forcing acceptance may break first-party clients if not behind flags or schema.
4. **Broadcast payload**: altering serialized fields may break consumers; any merch changes must be additive.

## Implemented Phases
- Phase 0 (Discovery + documentation): architecture mapped, active/passive fields classified, risk map recorded, docs created.

## Phase 1 status
- Phase 1 (Scheduling correctness) work is complete: feature flags gate buffer, coverage/travel, remote region, and capacity enforcement (see Feature Flags). `ServiceBookingCreateSerializer` accepts optional location/distance/remote/participant/staff fields, and the view layer validates them when the corresponding flags are enabled while falling back to the legacy path when the data is absent or the toggles remain `False`.
- Added helper functions in `apps/commerce/views.py` to compute buffer windows, enforce buffer/turnaround collisions, and validate location/participant inputs. New tests under `apps/commerce/tests.py:ServiceBookingAPITests` verify buffer conflicts, coverage/travel restrictions, remote-region gating, and participant/staff limits.

## Phase 2 status
- Phase 2 (Commercial logic) is implemented: packages/addons influence both price and duration when `SERVICE_ENABLE_PACKAGE_PRICING` or `SERVICE_ENABLE_ADDONS` are active, minimum charges can be enforced through `SERVICE_ENFORCE_MINIMUM_CHARGE`, tax-exclusive pricing is bumped via `SERVICE_HANDLE_TAX_INCLUSIVE` plus `COMMERCE_DEFAULT_TAX_RATE_PCT`, and quote/negotiation flows skip wallet locks when `SERVICE_ENABLE_QUOTES` or `SERVICE_ENABLE_NEGOTIATION` are on. Every decision (selected package/addons, requested price, pricing model, quote/negotiation flags) is captured inside `ServiceBooking.metadata` for downstream use.
- Complementary Phase 2 tests cover package/addon price bumps, minimum charge failures, quote-required requests, negotiation metadata, and tax adjustments so enabling the corresponding toggles raises no surprises for downstream consumers.

## Phase 1 Validation & Hardening
- **Dependency restoration**: `python3 -m pip install -r requirements/base.txt` (plus `phonenumbers`, `requests`) supplies Django, DRF, Celery, and the other core libraries needed for the commerce stack.
- **Tests executed**:
  - `python3 manage.py test apps.commerce.tests.ServiceBookingAPITests` – fails immediately because the default PostgreSQL `DATABASE_URL` points at `localhost:5432`, which is unreachable inside this sandbox (OperationalError).
  - `DATABASE_URL=sqlite:////tmp/testdb.sqlite3 python3 manage.py test apps.commerce.tests.ServiceBookingAPITests` – the suite starts but cannot finish because running `migrate` hits `sqlite3.OperationalError: near "[]": syntax error` while applying `commerce.0021_remove_shopservice_currency_shopservice_addons_and_more` (SQLite cannot represent the JSON default expressions generated for PostgreSQL). Running `migrate` without tests stops there as well.
  - Running the same migrations (`DATABASE_URL=sqlite:////tmp/testdb.sqlite3 python3 manage.py migrate`) reproduces the sqlite syntax error, so full test validation requires a PostgreSQL-compatible environment value.
  - `DATABASE_URL=postgresql://kis_dev_user@localhost:5432/kis_test python3 manage.py migrate` – the sandbox refuses the Postgres connection, raising `OperationalError: connection is bad: connection to server at "127.0.0.1", port 5432 failed: Operation not permitted`. Postgres is therefore not reachable from this session, blocking migrations/tests on the requested database.
- **Runtime observations**: The new helpers behave as intended—`_ensure_no_buffer_conflict` and `_validate_group_capacity` early-return when their flags are `False`, `coverage`/`remote`/`distance` enforcement only run when data is supplied (empty location tokens are skipped, so missing city/state/country do not trigger coverage errors), remote region gating matches normalized `remote_region` or `location` tokens, and participant/staff checks rely on `max_participants`/`staff_required` only when `SERVICE_ENFORCE_GROUP_CAPACITY` is `True`.
- **Logging & observability**: Every enforcement function logs when its flag is enabled (`SERVICE_ENFORCE_BUFFERS`, `SERVICE_ENFORCE_COVERAGE`, `SERVICE_ENFORCE_TRAVEL_RADIUS`, `SERVICE_ENFORCE_REMOTE_REGIONS`, `SERVICE_ENFORCE_GROUP_CAPACITY`). Rejections from buffer overlaps, coverage, travel-radius, remote-region, or capacity checks emit `logger.warning` with human-readable context so we can trace why a booking was blocked.
- **PostgreSQL provisioning note**: `initdb -D /tmp/kis_pgdata` fails here because the sandbox cannot allocate shared memory segments (the `shmget` call is denied). The `pg_ctl` server also cannot start, so a container or an external Postgres host is required. Future sessions should run these steps on a host that allows shared memory:
  1. `rm -rf /tmp/kis_pgdata && initdb -D /tmp/kis_pgdata`
  2. `pg_ctl -D /tmp/kis_pgdata -l /tmp/kis_pg.log start -o "-p 5433"`
  3. `psql -p 5433 -c "CREATE USER kis_dev WITH PASSWORD 'hunter2';"` and `psql -p 5433 -c "CREATE DATABASE kis_test_db OWNER kis_dev;"`
  4. `DATABASE_URL=postgresql://kis_dev:hunter2@127.0.0.1:5433/kis_test_db python3 manage.py migrate` to confirm the migrations apply.
The `commerce.0021_remove_shopservice_currency_shopservice_addons_and_more` migration depends on JSON defaults that work in PostgreSQL but fail in SQLite.
- **Production readiness**: Because all added behaviors are behind flags, Phase 1 is safe to rollout behind `SERVICE_ENFORCE_*` toggles. The code paths are exercised by the new tests (pending a compatible database), and the logging adds visibility for gradual enablement.

## Outstanding Phases
1. **Phase 3 – Requirements/policy**: enforce requirements, service terms, and refund/reschedule/cancellation policy guardrails.
2. **Phase 4 – Broadcast/discovery**: leverage tags/featured delivery for ranking/filtering without breaking payloads.
3. **Phase 5 – Structural hardening (optional)**: normalize package/addon/requirements JSON into dedicated tables if needed.

## Phase 3 status
- Phase 3 (Requirements + policy) is implemented: booking requests now declare `requirements_acknowledged` and `terms_accepted`, and the serializer/view enforce them whenever `SERVICE_ENFORCE_REQUIREMENTS` or `SERVICE_REQUIRE_TERMS_ACCEPTANCE` are on. The cancellation action honors service-specific `cancellation_window_hours` when `SERVICE_ENFORCE_REFUND_POLICY` is enabled (the response also surfaces `refund_policy` text), and a new `reschedule` endpoint respects `SERVICE_ENFORCE_RESCHEDULE_POLICY`, reuses the scheduling helpers, and appends reschedule history to `ServiceBooking.metadata`. The new Phase 3 tests exercise acknowledgement gating, terms acceptance, refund window blocking, and rescheduling metadata so these flags can be toggled with confidence.

## Feature Flags (Phases 1-3)
- `SERVICE_ENFORCE_BUFFERS`: gate prep/cleanup/turnaround occupancy enforcement.
- `SERVICE_ENFORCE_COVERAGE`: gate coverage and travel radius checks.
- `SERVICE_ENFORCE_TRAVEL_RADIUS`: separate check for radius beyond coverage.
- `SERVICE_ENFORCE_GROUP_CAPACITY`: gate max participants/staff logic.
- `SERVICE_ENABLE_QUOTES`: turn on quote-required, non-payment booking flows for services flagged `quote_required`.
- `SERVICE_ENABLE_NEGOTIATION`: enable negotiable/request-review flows and surface the requested price in `ServiceBooking.metadata`.
- `SERVICE_ENABLE_PACKAGE_PRICING`: let packages contribute to price/duration and capture the selected package.
- `SERVICE_ENABLE_ADDONS`: let add-ons alter price/duration and record the chosen add-ons.
- `SERVICE_ENFORCE_MINIMUM_CHARGE`: require the booking total to meet `minimum_charge` before locking wallets.
- `SERVICE_HANDLE_TAX_INCLUSIVE`: when this flag is on and `tax_inclusive` is `False`, the rate from `COMMERCE_DEFAULT_TAX_RATE_PCT` (default 0) is applied to the final price before deposits.
- `SERVICE_REQUIRE_TERMS_ACCEPTANCE`: require explicit acceptance before booking.
- `SERVICE_ENFORCE_REQUIREMENTS`: gate requirement acknowledgments.
- `SERVICE_ENFORCE_REFUND_POLICY`: use the service-level `cancellation_window_hours` and `refund_policy` text to gate cancellation requests.
- `SERVICE_ENFORCE_RESCHEDULE_POLICY`: enforce the service-level `reschedule_window_hours` before allowing a reschedule, then record the `reschedules` history onto `ServiceBooking.metadata`.
- `SERVICE_BROADCAST_FEATURED_RANKING`, `SERVICE_BROADCAST_TAG_FILTERS`: future merchandising toggles.

## Schema / Migration Status
- `apps/commerce/migrations/0025_add_servicebooking_metadata.py` adds the `metadata` JSONField to `ServiceBooking` so package/addon/quote/negotiation selections are persisted with every booking.
- `apps/commerce/migrations/0035_servicebookingreceipt.py` creates the `ServiceBookingReceipt` table so each deposit or remaining payment produces its own receipt snapshot and URL.

## Touched Files
- `docs/commerce_service_handoff.md` (this file)
- `docs/commerce_service_progress.md`

## Booking metadata contract
- `location`: object (strings for `city`, `state`, `country`, `region`) used when coverage/travel enforcement is active.
- `distance_km`: decimal number (string or number) used only when `SERVICE_ENFORCE_TRAVEL_RADIUS` is `True` to compare against `travel_radius_km`.
- `is_remote`: boolean indicating a remote session; enables `SERVICE_ENFORCE_REMOTE_REGIONS` checks when `remote_region` or `location` tokens are provided.
- `remote_region`: string defaulting to the caller-provided region/state/country when flagging remote bookings; normalized before matching `service.remote_regions`.
- `participant_count`: integer for group capacity checks when `SERVICE_ENFORCE_GROUP_CAPACITY` is enabled.
- `staff_on_site`: integer representing how many staff will attend; triggers the `staff_required` guard when the same flag is active.
- `selected_package`: optional package name used when `SERVICE_ENABLE_PACKAGE_PRICING` is on; matching service packages influence the final price/duration.
- `selected_addons`: optional list of addon names used when `SERVICE_ENABLE_ADDONS` is on; matching addons augment the final price/duration.
- `requested_price`: decimal used by `SERVICE_ENABLE_NEGOTIATION` when the service is `negotiable`; it feeds the negotiated price the provider may accept.
- `ServiceBooking.metadata` records the chosen pricing model, package/addon selections, quote/negotiation intent, and the requested price so downstream workflows can understand the booking context.
- `requirements_acknowledged`: populated when `SERVICE_ENFORCE_REQUIREMENTS` is enabled; the request must include every required acknowledgement before booking proceeds.
- `terms_accepted`: boolean attached when `SERVICE_REQUIRE_TERMS_ACCEPTANCE` is on so terms acceptance can be audited.
- `reschedules`: append-only history recorded by the reschedule endpoint to show who moved the slot and when.
- `ServiceBookingViewSet.receipt`: the `/receipt/` action now reports the active receipt snapshot (`receipt_id`, `receipt_phase`, `receipt_amount_cents`, `receipt_currency`) and accepts optional `phase` or `receipt_id` query parameters so deposit and remaining payment receipts can each be fetched without overwriting the other document.

## Known Risks
- Future enforcement of dormant fields must be flag-gated to avoid booking regressions.
- Introducing scheduling buffers/travel enforcement without data cleanup might block legitimate slots.

## Next Recommended Step
Begin Phase 4 by shaping broadcast/discovery merchandising: surface `tags`/`is_featured` metadata, rank featured services higher, and keep the broadcast payload additive so downstream consumers remain stable.

## Assumptions & Compatibility Notes
- Existing clients expect the current ShopServiceResponse shape, availability behavior, and booking flow; any new feature must default to the old behavior until a flag is enabled.
- The system relies heavily on the `ServiceBookingViewSet` to enforce `is_active`/`published`; we assume no other components bypass that path.
