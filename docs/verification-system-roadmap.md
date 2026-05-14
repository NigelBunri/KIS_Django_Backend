# KIS Verification System Roadmap

Last updated: 2026-05-06

## Purpose

Build one verification system for people and institutions, with visible badges across profile, broadcast, marketplace, health, education, partner, messaging, and admin surfaces.

This roadmap is intentionally phased. Phase 0 is analysis and architecture only. Later phases should implement in small, reversible changes without breaking current user, commerce, health, education, or partner flows.

## Phase 0 Status - Completed Analysis

### Current project fit

KIS already has several trust-related fields and flows:

- Users: `apps.accounts.models.User.verification` JSON field, `trust_score`, `email_verified`.
- Shops: `apps.commerce.models.Shop.is_verified`, `verification_status`, `trust_badges`, and `ShopVerificationRequest`.
- Partners: `Partner`, `PartnerOrganizationProfile`, and organization app metadata exist, but there is no unified verification status/badge model yet.
- Health institutions: `apps.health_ops.models.HealthInstitution`, `apps.broadcasts.models.BroadcastHealthInstitution`, and dashboard wrappers exist, but verification is not centralized.
- Education institutions: `apps.broadcasts.models.EducationInstitution` and `EducationProfile` exist, but verification is not centralized.
- UI surfaces already suitable for badge display include profile header, broadcast author cards/details, shop cards/dashboard, health institution cards/details, education institution/detail cards, partner header/discovery cards, chat headers, and admin panels.

### Recommended verification model

Use a centralized Django app, tentatively `apps.verification`, as the source of truth.

Core model shape:

- `VerificationSubject`: normalized subject reference for `user`, `shop`, `health_institution`, `education_institution`, and `partner`.
- `VerificationCase`: application/review lifecycle, provider references, status, risk score, reviewer notes, submitted evidence metadata.
- `VerificationCheck`: individual checks such as email, phone, ID document, liveness, business registration, address, license, domain, social handle, staff/owner authorization, AML/sanctions.
- `VerificationBadge`: badge code, label, level, issued/revoked timestamps, expiry, reason, and public display policy.
- `VerificationAuditEvent`: structured audit trail for status changes, provider callbacks, reviewer actions, badge issuance, badge revocation.

Do not store raw identity documents in these models. Store private media/file references and provider applicant/check ids only. Raw documents must remain private media with short-lived access and audit logging.

### Verification levels

Users:

- `basic`: email/phone/device trust complete.
- `identity_verified`: government ID/passport plus selfie/liveness passed.
- `professional_verified`: identity plus professional credential/license/manual review where relevant.
- Badge examples: `Verified user`, `ID verified`, `Verified professional`.

Shops:

- `merchant_basic`: owner identity + phone/email + shop image/profile completeness.
- `merchant_verified`: business registration or owner identity, address/phone match, policy acceptance.
- `trusted_merchant`: merchant verified plus successful order history, low dispute/fraud score, secure payout/payment status.
- Badge examples: `Verified shop`, `Trusted merchant`, `Secure pay`.

Health institutions:

- `health_basic`: owner/admin identity + institution profile completeness.
- `health_verified`: legal/business registration, address/phone/domain verification.
- `licensed_health`: license/accreditation/manual review, staff authorization, expiry tracking.
- Badge examples: `Verified health institution`, `Licensed provider`.

Education institutions:

- `education_basic`: owner/admin identity + institution profile completeness.
- `education_verified`: registration/domain/address verification.
- `accredited_education`: accreditation/certification/manual review, expiry tracking.
- Badge examples: `Verified education institution`, `Accredited`.

Partners/companies:

- `partner_basic`: owner identity + organization profile completeness.
- `partner_verified`: KYB registry/company document verification and representative authorization.
- `strategic_partner`: manual platform approval, active agreement, governance review.
- Badge examples: `Verified partner`, `Verified organization`, `Official partner`.

### Provider recommendation

Primary launch recommendation:

- Use Dojah for Nigeria/Africa-first user KYC, document verification, BVN/phone/account matching where legally appropriate, CAC/TIN/business verification, and Nigerian business address verification.
- Use Sumsub as the global fallback and higher-assurance KYB/KYC provider for passports, international company registry checks, UBO verification, AML screening, and multi-country expansion.

Optional later providers:

- Smile ID for Africa-focused identity verification where pricing, coverage, and UX outperform Dojah for a target country.
- Manual/admin review remains mandatory for health licenses, education accreditation, official partner status, disputes, and edge cases.

Provider basis:

- Dojah documents Nigeria-focused age/identity verification, global document verification, business verification, CAC/TIN/company data, and business address verification.
- Sumsub documents global KYC/KYB, business verification, UBO checks, company registry checks, AML screening, and verification links/WebSDK/API options.
- Smile ID positions itself as Africa-focused identity verification with face matching/document OCR/liveness.

### Entry points in the app

Use multiple contextual entry points, not one hidden settings screen:

- Profile page: `Get verified` card and badge status under the user identity header.
- Edit Profile / Account Settings: full user verification center with status, required actions, and history.
- Shop dashboard/editor: `Verify this shop` from shop setup, dashboard trust area, and before advanced selling/boosting.
- Health institution management/dashboard: verification prompt near institution profile completion and before publishing high-trust health services.
- Education institution/profile manager: verification prompt near institution profile setup and before issuing certificates/accreditation badges.
- Partner workspace/settings/governance: organization verification center with KYB, representative authorization, and official partner review.
- Admin/control area: review queue for manual cases, provider failures, badge issuance/revocation, and audit logs.

### Badge display rules

Badges must be generated from the centralized verification state, not manually duplicated per feature.

Display badge summaries in:

- User profile header and author previews.
- Broadcast feed cards/details and profile feed manager.
- Chat headers/member sheets for verified users and verified organizations.
- Shop cards/product/service detail pages.
- Health institution cards/detail/booking pages.
- Education institution/course/detail pages.
- Partner discovery cards/header/workspace.

Never show raw verification documents, provider ids, legal notes, or reviewer notes publicly.

### Environment keys

Phase 0 adds placeholders only. No real provider keys are rotated or added.

Expected backend env names:

- `VERIFICATION_PROVIDER_PRIMARY=dojah`
- `VERIFICATION_PROVIDER_FALLBACK=sumsub`
- `VERIFICATION_WEBHOOK_SECRET=...`
- `DOJAH_APP_ID=...`
- `DOJAH_SECRET_KEY=...`
- `DOJAH_BASE_URL=https://api.dojah.io`
- `SUMSUB_APP_TOKEN=...`
- `SUMSUB_SECRET_KEY=...`
- `SUMSUB_BASE_URL=https://api.sumsub.com`
- `SMILE_ID_PARTNER_ID=...`
- `SMILE_ID_API_KEY=...`
- `SMILE_ID_BASE_URL=...`

Secrets must be stored only in environment/provider secret managers. Docs and logs must show only redacted values.

## Implementation Phases

### Phase 1 - Canonical Backend Foundation - Completed 2026-05-03

Create `apps.verification` with normalized models, migrations, serializers, permissions, admin views, audit events, and status/badge constants. Add read-only badge summary helpers that can resolve badge state for users, shops, health institutions, education institutions, and partners without changing existing flows.

Delivered:

- Added `apps.verification` and registered it in Django settings.
- Added canonical models:
  - `VerificationSubject`
  - `VerificationCase`
  - `VerificationCheck`
  - `VerificationBadge`
  - `VerificationAuditEvent`
- Added constants for subject types, case statuses, check statuses, badge statuses, and public badge labels.
- Added DRF serializers and staff/read-only verification permissions.
- Added Django admin registrations and inline checks for review workflows.
- Added public badge summary helpers in `apps.verification.services`.
- Added env-backed provider settings in `config/settings/base.py`.
- Generated and applied local migration `apps/verification/migrations/0001_initial.py`.

Validation:

- `python3 -m py_compile apps/verification/models.py apps/verification/services.py apps/verification/serializers.py apps/verification/admin.py apps/verification/permissions.py config/settings/base.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py migrate verification --plan` showed only `verification.0001_initial`.
- `python3 manage.py migrate verification` applied successfully in local development.
- `python3 manage.py shell -c "from apps.verification.services import verification_summary; print(verification_summary('user', '00000000-0000-4000-8000-000000000000'))"` returned `{"verified": False, "badges": []}`.

Phase 1 did not add live provider calls, public APIs, or raw document storage.

### Phase 2 - User Verification Flow - Completed 2026-05-03

Add user verification request APIs, provider abstraction, Dojah/Sumsub adapter stubs, webhook endpoint, manual fallback review, badge issuance, and profile serializer badge summaries. Keep raw documents private and logs redacted.

Delivered:

- Added provider-neutral user verification endpoints under `/api/v1/verification/`:
  - `GET /api/v1/verification/user/status/`
  - `POST /api/v1/verification/user/start/`
  - `POST /api/v1/verification/user/cases/<case_id>/evidence/`
  - `POST /api/v1/verification/staff/user/cases/<case_id>/review/`
  - `POST /api/v1/verification/webhooks/<provider>/`
- Added Dojah and Sumsub adapter stubs that read env-backed configuration and return only safe configured/not-configured status. No live provider calls are made.
- Added webhook signature verification skeleton using `VERIFICATION_WEBHOOK_SECRET` and HMAC SHA-256. Missing or invalid signatures are rejected.
- Added raw-document/base64 rejection for evidence metadata. APIs accept private media references and metadata only.
- Added user case start, evidence submission, manual staff review, and badge issuance services.
- Staff approval issues public `verified_user` and `id_verified` badges for user verification cases.
- Connected public verification summaries to `UserSerializer`, `ProfileSerializer`, and detailed profile payloads.
- Added focused regression tests for empty status, case start, raw evidence rejection, staff approval badge issuance, and invalid webhook rejection.

Validation:

- `python3 -m py_compile apps/verification/providers.py apps/verification/services.py apps/verification/serializers.py apps/verification/views.py apps/verification/urls.py apps/verification/tests.py apps/accounts/serializers.py apps/accounts/views.py config/urls.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py shell -c "from django.urls import reverse; print(reverse('verification:user-status'))"` returned `/api/v1/verification/user/status/`.
- `python3 manage.py makemigrations verification --check --dry-run` returned no model changes.
- `python3 manage.py shell -c "from apps.accounts.serializers import UserSerializer, ProfileSerializer; print('verification_summary' in UserSerializer().fields); print('verification_summary' in ProfileSerializer().fields)"` returned `True` for both serializers.

Blocked / deferred:

- `python3 manage.py test apps.verification` stopped before tests because Django prompted to delete an existing SQLite test database.
- `python3 manage.py test apps.verification --noinput` was started but stayed stuck in local test database setup with no test output, so the run was stopped and recorded as blocked by local test DB setup.
- Live Dojah/Sumsub provider calls, provider sandbox testing, and real webhook event mapping are intentionally deferred.
- Raw documents are still expected to be uploaded through the private media path before evidence metadata is submitted.

### Phase 3 - Shop Verification Migration - Completed 2026-05-05

Connect existing `ShopVerificationRequest`, `Shop.is_verified`, `verification_status`, and `trust_badges` to the centralized verification system. Preserve current commerce APIs while making the centralized app the source of truth.

Delivered:

- Added shop subject helpers and current shop verification summaries in `apps.verification.services`.
- Added backward-compatible sync from each `ShopVerificationRequest` to a centralized `VerificationCase` with provider `commerce`.
- Kept legacy `ShopVerificationRequest` as the public commerce workflow while making centralized cases and badges available in parallel.
- Added safe evidence metadata sanitization for shop requests so centralized verification cases store private media references and document counts, not public URLs or raw document payloads.
- Wired shop verification sync into:
  - `ShopViewSet.request_verification`
  - `enqueue_shop_verification`
  - `ShopVerificationRequestViewSet.review`
- Manual approval now syncs legacy fields and centralized badges:
  - `Shop.is_verified=True`
  - `Shop.verification_status=VERIFIED`
  - legacy `trust_badges` includes `kyc` / `verified-shop`
  - centralized public badges include `verified_shop` and `trusted_merchant`
- Added public shop verification summaries to `ShopSerializer`.
- Added centralized case ID and verification summary fields to `ShopVerificationRequestSerializer`.
- Added focused regression tests for centralized case creation, URL-free evidence metadata, centralized badge issuance, legacy field syncing, public serializer summaries, and raw document rejection.

Validation:

- `python3 -m py_compile apps/verification/services.py apps/commerce/serializers.py apps/commerce/views.py apps/commerce/tasks.py apps/commerce/tests.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations verification commerce --check --dry-run` returned no model changes.
- `python3 manage.py shell -c "from apps.commerce.serializers import ShopSerializer, ShopVerificationRequestSerializer; print('verification_summary' in ShopSerializer().fields); print('verification_case_id' in ShopVerificationRequestSerializer().fields); print('verification_summary' in ShopVerificationRequestSerializer().fields)"` returned `True`, `True`, `True`.

Blocked / deferred:

- `python3 manage.py test apps.commerce.tests.ShopVerificationMigrationTests --noinput` started but stayed stuck during local test database setup after destroying the old SQLite test DB. The process was stopped and recorded as blocked by the same local test DB setup issue from Phase 2.
- Live KYB provider calls are still deferred.
- Existing legacy `ShopVerificationRequest.documents` remains backward-compatible for commerce, but centralized verification case metadata is sanitized and does not copy public URLs/raw document payloads.

### Phase 4 - Partner / Company KYB - Completed 2026-05-05

Add partner organization verification through KYB provider checks, representative identity verification, beneficial owner/authorization metadata, manual strategic partner approval, and partner badge display.

Delivered:

- Added partner subject helpers and read-only partner verification summaries in `apps.verification.services`.
- Added provider-neutral partner KYB case creation with metadata buckets for:
  - representative authorization
  - company registration
  - beneficial owners
  - tax/registry references
  - address references
- Added partner evidence metadata sanitization so centralized cases keep private media references and safe registry/ownership metadata only.
- Added partner manual review service with `approve`, `reject`, and `needs_more_info` actions.
- Staff approval can issue public partner badges:
  - `verified_partner`
  - `verified_organization`
  - `official_partner`
- Added partner verification endpoints without changing existing partner APIs:
  - `GET /api/v1/partners/<partner_id>/verification-status/`
  - `POST /api/v1/partners/<partner_id>/verification/start/`
  - `POST /api/v1/partners/<partner_id>/verification/cases/<case_id>/review/`
- Staff users can now access partner verification review targets through the partner queryset.
- Added public partner verification summaries to:
  - `PartnerListSerializer`
  - `PartnerDiscoverSerializer`
  - `PartnerDetailSerializer`
  - `PartnerOrganizationProfileSerializer`
- Added focused regression tests for partner KYB case creation, safe metadata, staff badge approval, serializer summaries, and raw document rejection.

Validation:

- `python3 -m py_compile apps/verification/services.py apps/verification/serializers.py apps/partners/serializers.py apps/partners/views.py apps/partners/tests.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations verification partners --check --dry-run` returned no model changes.
- `python3 manage.py shell -c "from apps.partners.serializers import PartnerListSerializer, PartnerDetailSerializer, PartnerDiscoverSerializer, PartnerOrganizationProfileSerializer; print('verification_summary' in PartnerListSerializer().fields); print('verification_summary' in PartnerDetailSerializer().fields); print('verification_summary' in PartnerDiscoverSerializer().fields); print('verification_summary' in PartnerOrganizationProfileSerializer().fields)"` returned `True`, `True`, `True`, `True`.
- URL reverse smoke check passed for partner verification status/start/review routes.

Blocked / deferred:

- Focused partner regression tests were started with `python3 manage.py test ... --noinput`, but local Django test database setup stuck after destroying the old SQLite test database. The run was stopped and recorded as blocked by the same local test DB issue from Phases 2 and 3.
- No live Dojah/Sumsub KYB provider calls are made yet.
- Provider webhook event mapping for partner KYB is still deferred.
- Dedicated admin review queue is still deferred to a later admin/revocation phase.

### Phase 5 - Health Institution Verification - Completed 2026-05-05

Add health institution verification with legal registration, address/domain/phone, medical license/accreditation evidence, expiry tracking, staff authorization, and manual reviewer workflow.

Delivered:

- Added health institution subject helpers and read-only verification summaries in `apps.verification.services`.
- Connected both health institution surfaces to centralized verification summaries:
  - `apps.health_ops.models.HealthInstitution`
  - `apps.broadcasts.models.BroadcastHealthInstitution`
- Added provider-neutral health verification case creation with evidence metadata buckets for:
  - legal registration
  - address
  - domain/phone
  - medical license
  - accreditation
  - staff authorization
  - expiry references
- Added evidence metadata sanitization so centralized health verification cases keep private media references and safe metadata only.
- Added staff/manual review service with `approve`, `reject`, and `needs_more_info`.
- Staff approval issues public health badges:
  - `verified_health_institution`
  - `licensed_provider`
- Added health-ops verification endpoints:
  - `GET /api/v1/health-ops/institutions/<institution_id>/verification-status/`
  - `POST /api/v1/health-ops/institutions/<institution_id>/verification/start/`
  - `POST /api/v1/health-ops/institutions/<institution_id>/verification/cases/<case_id>/review/`
- Added `verification_summary` to `HealthInstitutionSerializer`.
- Added public verification summaries to broadcast health institution payloads and health dashboard list/detail payloads.
- Added focused regression tests for health verification start, raw document rejection, staff approval badge issuance, serializer summaries, and broadcast health institution summaries.

Validation:

- `python3 -m py_compile apps/verification/services.py apps/verification/serializers.py apps/health_ops/serializers.py apps/health_ops/views.py apps/health_ops/urls.py apps/broadcasts/views.py apps/health_dashboard/views.py apps/health_ops/tests/test_verification.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations verification health_ops --check --dry-run` returned no model changes.
- `python3 manage.py shell -c "from apps.health_ops.serializers import HealthInstitutionSerializer; print('verification_summary' in HealthInstitutionSerializer().fields)"` returned `True`.
- URL reverse smoke check passed for health verification status/start/review routes.

Blocked / deferred:

- `python3 manage.py test apps.health_ops.tests.test_verification --noinput` started but stayed stuck during local Django test database setup after destroying the old SQLite test DB. The process was stopped and recorded as blocked by the same local test DB setup issue from earlier phases.
- No live Dojah/Sumsub health institution verification calls are made yet.
- Provider webhook event mapping for health institution cases is still deferred.
- Dedicated admin queue/revocation/expiry reminder workflows are still deferred.

### Phase 6 - Education Institution Verification - Completed 2026-05-05

Add education institution verification with legal registration, domain/address/phone, accreditation/certification evidence, expiry tracking, certificate issuer trust rules, and manual reviewer workflow.

Delivered:

- Added education institution subject helpers and read-only verification summaries in `apps.verification.services`.
- Connected `apps.broadcasts.models.EducationInstitution` to centralized verification summaries.
- Added provider-neutral education verification case creation with evidence metadata buckets for:
  - legal registration
  - domain/address/phone
  - accreditation
  - certification
  - certificate issuer trust
  - staff authorization
  - expiry references
- Added evidence metadata sanitization so centralized education cases keep private media references and safe metadata only.
- Added staff/manual review service with `approve`, `reject`, and `needs_more_info`.
- Staff approval issues public education badges:
  - `verified_education_institution`
  - `accredited_education`
- Added education institution verification endpoints:
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/verification-status/`
  - `POST /api/v1/broadcasts/education/institutions/<institution_id>/verification/start/`
  - `POST /api/v1/broadcasts/education/institutions/<institution_id>/verification/cases/<case_id>/review/`
- Added `verification_summary` to `EducationInstitutionSerializer`, covering list/detail/dashboard payloads that already use that serializer.
- Added focused regression tests for education verification start, raw document rejection, staff badge approval, and serializer summary behavior.

Validation:

- `python3 -m py_compile apps/verification/services.py apps/verification/serializers.py apps/broadcasts/serializers.py apps/broadcasts/views.py apps/broadcasts/urls.py apps/broadcasts/tests.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations verification broadcasts --check --dry-run` returned no model changes.
- `python3 manage.py shell -c "from apps.broadcasts.serializers import EducationInstitutionSerializer; print('verification_summary' in EducationInstitutionSerializer().fields)"` returned `True`.
- URL reverse smoke check passed for education institution verification status/start/review routes.

Blocked / deferred:

- Focused education regression tests were started with `python3 manage.py test ... --noinput`, but local Django test database setup stayed stuck after destroying the old SQLite test DB. The process was stopped and recorded as blocked by the same local test DB setup issue from earlier phases.
- No live Dojah/Sumsub education verification calls are made yet.
- Provider webhook event mapping for education institution cases is still deferred.
- Dedicated admin queue/revocation/expiry reminder workflows are still deferred.

### Phase 7 - Frontend Verification Center - Completed 2026-05-06

Build shared React Native verification components: badge renderer, status cards, verification center sheet, evidence metadata forms, provider handoff placeholder, review timeline, and contextual entry points across profile, shop, health, education, and partner screens.

Delivered:

- Added shared React Native verification service helpers in `/Users/nigel/dev/KIS/src/services/verificationService.ts`.
- Added shared UI components in `/Users/nigel/dev/KIS/src/components/verification/`:
  - `VerificationBadgeRow`
  - `VerificationStatusCard`
  - `VerificationCenterSheet`
  - `normalizeVerificationSummary`
- Added route helpers for:
  - user verification status/start
  - partner verification status/start
  - health institution verification status/start
  - education institution verification status/start
  - backward-compatible shop verification status/start
- Added public badge/status display to the profile hero and workspace launcher cards.
- Added a profile verification status card and sheet entry point.
- Added market/shop verification status and per-shop verification action.
- Added health institution verification entry point in the health management area.
- Added education institution verification badge/status entry point in the education workspace overview.
- Added partner workspace verification status card, badge row, and verification sheet entry point.
- Evidence submission is metadata-only and instructs users to provide private media references; no raw documents, base64 payloads, public document URLs, or provider secrets are exposed.
- Provider handoff remains placeholder-only; no live Dojah/Sumsub/Smile calls are made from the app.

Validation:

- `npm run typecheck -- --pretty false` passed in `/Users/nigel/dev/KIS`.
- `npx eslint . --quiet` passed in `/Users/nigel/dev/KIS`.
- `npm run ci:launch` passed in `/Users/nigel/dev/KIS`:
  - `npm audit --omit=dev --legacy-peer-deps` found 0 vulnerabilities.
  - `npm run typecheck:launch` passed.
  - `npm run lint:launch` passed.
- `python3 manage.py check` passed in the Django backend.

Blocked / deferred:

- No live identity/KYB provider handoff is enabled yet.
- The frontend uses private media reference text entry for evidence metadata; a later phase should connect the secure private media picker/upload UX directly.
- Shop verification still uses the backward-compatible commerce endpoint, so centralized shop verification remains synchronized through the backend compatibility layer.
- Admin review queue, badge revocation, expiry reminders, provider callback inspection, and abuse visibility remain Phase 8 work.

### Phase 8 - Admin Review, Abuse, and Revocation - Completed 2026-05-06

Add admin queues, provider callback inspection, badge issuance/revocation, expiry reminders, suspicious pattern alerts, audit export/read views, staff-only access checks, and operational visibility.

Delivered:

- Added staff-only verification review queue APIs:
  - `GET /api/v1/verification/staff/cases/`
  - `GET /api/v1/verification/staff/cases/<case_id>/`
  - `PATCH /api/v1/verification/staff/cases/<case_id>/`
- Added queue filters for status, subject type, provider, and search.
- Added staff case detail payloads with subject summaries, badges, public summaries, and safe evidence/provider payload shape summaries instead of raw evidence blobs.
- Added staff-only badge operations:
  - `POST /api/v1/verification/staff/badges/issue/`
  - `POST /api/v1/verification/staff/badges/<badge_id>/revoke/`
- Added centralized badge issue/revoke services with structured audit events.
- Added staff-only audit read endpoint:
  - `GET /api/v1/verification/staff/audit-events/`
- Added provider webhook/callback inspection endpoint:
  - `GET /api/v1/verification/staff/provider-callbacks/`
- Added suspicious verification signals endpoint:
  - `GET /api/v1/verification/staff/suspicious-signals/`
- Added expiry/reverification reminder endpoint:
  - `GET /api/v1/verification/staff/expiry-reminders/`
  - `POST /api/v1/verification/staff/expiry-reminders/`
- Added dry-run-safe overdue badge expiry action.
- Hardened Django admin list filters/date hierarchy for cases, badges, and audit events.
- Added focused regression tests for staff queue access, badge issue/revoke, audit/provider callback reads, suspicious signals, and expiry dry-run/expire behavior.

Validation:

- `python3 -m py_compile apps/verification/services.py apps/verification/serializers.py apps/verification/views.py apps/verification/urls.py apps/verification/admin.py apps/verification/tests.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations verification --check --dry-run` returned no model changes.
- Staff URL reverse smoke check passed for cases, case detail, badge issue, badge revoke, audit events, provider callbacks, suspicious signals, and expiry reminders.

Blocked / deferred:

- `python3 manage.py test apps.verification.tests.StaffVerificationOperationsTests --noinput` was started, but local Django test database setup stayed stuck after destroying the old SQLite test DB. The process was stopped and recorded as blocked by the same local test DB setup issue from prior phases.
- No live provider webhook mapping is enabled yet.
- No push/in-app notification dispatch is connected to expiry reminders yet; Phase 8 exposes reminder data and safe expiry marking only.
- No full frontend admin review console was added yet; the backend staff APIs are ready for that surface.
- Suspicious signals are intentionally conservative aggregate signals, not a full risk-scoring engine.

### Phase 9 - Launch QA and Provider Integration Hardening - Completed 2026-05-06

Run safe validation, provider sandbox readiness checks, webhook replay checks, private media checks, staff review QA, badge display QA, and production readiness documentation. Document provider coverage decisions and rollout rules.

Delivered:

- Added Smile ID as a provider adapter stub in the backend registry with no live calls.
- Added non-secret provider readiness command:
  - `python3 manage.py verification_provider_readiness`
- Added local webhook signature replay/check command:
  - `python3 manage.py verification_webhook_signature_check --payload ... --signature ...`
- Added provider sandbox runbook:
  - `docs/operations/VERIFICATION_PROVIDER_SANDBOX_RUNBOOK.md`
- Added verification launch QA checklist:
  - `docs/operations/VERIFICATION_LAUNCH_QA_CHECKLIST.md`
- Updated provider launch readiness checklist with verification-specific evidence gates.
- Updated React Native launch QA checklist with verification badge/sheet QA.
- Documented Dojah, Sumsub, and Smile ID sandbox requirements without storing secrets.
- Documented webhook replay/signature validation behavior.
- Documented private media proof requirements for verification evidence references.
- Documented staff review console QA, badge display QA across all subject types, expiry reminder notification planning, and production rollout/rollback rules.

Validation:

- `python3 -m py_compile apps/verification/providers.py apps/verification/management/commands/verification_provider_readiness.py apps/verification/management/commands/verification_webhook_signature_check.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations verification --check --dry-run` returned no model changes.
- `python3 manage.py verification_provider_readiness` passed and printed non-secret status for Dojah, Sumsub, and Smile ID.
- `npm run typecheck -- --pretty false` passed in `/Users/nigel/dev/KIS`.
- `npx eslint . --quiet` passed in `/Users/nigel/dev/KIS`.
- `npm run ci:launch` passed in `/Users/nigel/dev/KIS`:
  - `npm audit --omit=dev --legacy-peer-deps` found 0 vulnerabilities.
  - launch typecheck passed.
  - launch lint passed.

Blocked / deferred:

- Real provider sandbox calls were not made because live/provider network integration remains disabled unless explicitly configured.
- Webhook signature success replay requires a real staging `VERIFICATION_WEBHOOK_SECRET` and matching sandbox signature. The checker exists but no secret was printed or used in docs.
- Private media picker/upload integration remains planned, not implemented in this phase.
- Full frontend staff review console is still deferred.
- Expiry reminder notification dispatch remains planned, not implemented.

### Phase 10 - Final Implementation Bridge Before Live Provider Enablement - Completed 2026-05-06

Bridge the verification system into the final pre-live state: private evidence upload references, staff review UI, expiry reminder notification scheduling, and feature-flagged provider enablement controls.

Delivered:

- Added production-safe provider feature flags:
  - `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=false` by default.
  - `VERIFICATION_LIVE_PROVIDER_SUBJECTS=` for subject-scoped live-provider rollout.
  - `VERIFICATION_EXPIRY_REMINDER_DAYS=30,14,7,1` for renewal windows.
- Updated provider readiness output to show non-secret `live_calls_enabled` and keep `live_call_made=false`.
- Kept all Dojah, Sumsub, and Smile ID adapters no-live-call by default.
- Added `schedule_verification_expiry_reminders` management command:
  - dry-run by default;
  - `--send` creates in-app/push notification rows through the central notification service;
  - notification context excludes evidence metadata, raw documents, provider secrets, and reviewer notes.
- Added scheduler service coverage for expiring verification cases and badges with dedup keys per item/window.
- Fixed a notification service import needed by notification rule suppression.
- Added focused backend regression coverage for live-provider-disabled status and private-metadata-only expiry reminder dry-run.
- Connected the React Native verification center to the existing private upload endpoint:
  - selected files are uploaded with `visibility=private`;
  - the verification case receives only private media ids/reference metadata;
  - raw file contents, base64 payloads, public URLs, and secrets are not submitted to verification models.
- Added a React Native staff verification console component with:
  - staff review queue;
  - case status updates for `in_review`, `needs_more_info`, and `cancelled`;
  - evidence/provider payload summaries only;
  - expiry count visibility;
  - recent audit event visibility.
- Added a staff-gated profile entry point for the review console without changing normal user flows.

Validation:

- `python3 -m py_compile apps/verification/providers.py apps/verification/services.py apps/verification/tests.py apps/verification/management/commands/schedule_verification_expiry_reminders.py apps/notifications/services.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations --check --dry-run` returned no model changes.
- `python3 manage.py verification_provider_readiness` passed and printed non-secret status for Dojah, Sumsub, and Smile ID with live calls disabled.
- `python3 manage.py schedule_verification_expiry_reminders --days 30,14,7,1 --limit 50` passed in dry-run mode and created no notifications.
- `npm run typecheck -- --pretty false` passed in `/Users/nigel/dev/KIS`.
- Focused ESLint passed for verification components/service/profile files.
- `npm run ci:launch` passed in `/Users/nigel/dev/KIS`:
  - production npm audit found 0 vulnerabilities;
  - launch typecheck passed;
  - launch lint passed.

Blocked / deferred:

- Focused Django tests were added, but the local test runner is still blocked by test database setup. The first run prompted for deleting `test_db.sqlite3` and failed with EOF; the `--keepdb` retry stalled after selecting the existing test database, so it was stopped and recorded.
- The staff console can update case status but does not yet issue/revoke badges from the app; badge issue/revoke remains available through the backend staff APIs.
- Live provider calls remain disabled. Phase 11 should only enable sandbox provider calls behind flags in staging.
- Private upload currently uses the existing `/uploads/file` private attachment flow. Production still needs object-storage/private-media proof with signed-access QA.

### Phase 11 - Staging-Only Sandbox Provider Enablement - Completed 2026-05-06

Add the first safe provider sandbox bridge for user verification only, with production live calls disabled, redacted provider payloads, signed webhook mapping, staff badge actions, private media signed-access proof tooling, and end-to-end QA evidence.

Delivered:

- Added staging-only provider sandbox controls:
  - `VERIFICATION_PROVIDER_SANDBOX_ENABLED=true`
  - `VERIFICATION_PROVIDER_LIVE_ALLOWED_ENVS=staging`
  - live provider calls remain blocked when `DJANGO_ENV=production`.
- Added a production hard-fail in `config/settings/production.py` if `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED` is enabled.
- Added provider payload redaction utilities that mask tokens, secrets, document/image/base64 fields, identity numbers, phone/email values, and oversized strings before storage/audit.
- Added a user-only sandbox handoff path:
  - requires configured provider credentials;
  - requires live calls enabled;
  - requires allowed environment and subject type;
  - stores a redacted provider request/response summary;
  - marks the user case `provider_pending`;
  - does not make network calls.
- Added signed provider webhook mapping for user verification:
  - approved/passed/completed -> approves the user case and issues `verified_user` / `id_verified`;
  - rejected/failed/declined -> rejects the case;
  - needs-more-info/resubmit -> marks the case needs more info;
  - pending/review/processing -> keeps the case provider pending;
  - unmatched callbacks are accepted and audited without raw payload storage.
- Added webhook audit redaction so raw document/token fields are not stored in audit metadata.
- Added private media signed-access readiness command:
  - `python3 manage.py verification_private_media_access_check`
  - optional `--asset-id` validates a staging private `MediaAsset`, signed token generation, and TTL behavior without printing file contents.
- Added React Native staff console badge actions:
  - issue `verified_user`;
  - issue `id_verified`;
  - revoke active badges with reviewer notes.
- Added focused backend regression tests for:
  - staging sandbox handoff redaction and `provider_pending` behavior;
  - signed webhook approval mapping to public badges and redacted audit metadata.

Validation:

- `python3 -m py_compile apps/verification/providers.py apps/verification/services.py apps/verification/views.py apps/verification/tests.py apps/verification/management/commands/verification_provider_readiness.py apps/verification/management/commands/verification_private_media_access_check.py config/settings/base.py config/settings/production.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations --check --dry-run` returned no model changes.
- `python3 manage.py verification_provider_readiness` passed and printed non-secret configured/live/sandbox status.
- `python3 manage.py verification_private_media_access_check` passed in no-asset readiness mode.
- Focused Django tests passed with `--keepdb --noinput`:
  - `test_staging_sandbox_user_start_records_redacted_provider_handoff`
  - `test_signed_provider_webhook_maps_approved_user_case_to_badges`
- `npm run typecheck -- --pretty false` passed in `/Users/nigel/dev/KIS`.
- Focused React Native ESLint passed for the verification staff console and verification service.
- `npm run ci:launch` passed in `/Users/nigel/dev/KIS`; production audit found 0 vulnerabilities.

Blocked / deferred:

- The sandbox handoff intentionally does not make external network calls yet; it creates a redacted handoff record and waits for signed sandbox webhook replay.
- Full provider SDK/API request execution is still deferred until staging provider credentials, callback URLs, and provider-console evidence are ready.
- Private media command needs a real staging private `MediaAsset --asset-id` to complete file-specific signed-access proof.
- Staff console badge issue/revoke is connected for user cases; broader institution badge actions remain backend/API-ready but not yet optimized in the mobile console.
- Production live calls are still intentionally blocked.

### Phase 12 - Real Staging Sandbox Execution Readiness - Completed 2026-05-06

Prepare one-provider staging sandbox execution without enabling production calls. Provider adapters are now provider-specific, redacted, and gated; production remains hard-blocked.

Delivered:

- Added staging-only execution controls:
  - `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=false` by default.
  - `VERIFICATION_PROVIDER_TIMEOUT_SECONDS=10`.
  - `VERIFICATION_WEBHOOK_BASE_URL`.
- Added provider-specific sandbox request adapters:
  - Dojah: builds a sandbox KYC request with Dojah headers redacted before storage.
  - Sumsub: builds a sandbox applicant request with app token redacted before storage.
  - Smile ID: builds a sandbox ID verification request with partner/API credentials redacted before storage.
- Added optional sandbox network execution behind all gates:
  - `DJANGO_ENV=staging`;
  - `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=true`;
  - `VERIFICATION_PROVIDER_SANDBOX_ENABLED=true`;
  - `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=true`;
  - `VERIFICATION_LIVE_PROVIDER_SUBJECTS=user`.
- Added a production hard-fail if sandbox network calls are enabled in production.
- Provider payload persistence remains safe:
  - only redacted request summaries;
  - safe provider reference/status;
  - redacted bounded response metadata;
  - no raw files, no base64, no provider secrets, no document contents.
- Added webhook replay fixture command:
  - `python3 manage.py verification_webhook_replay_fixture --provider dojah --case-id <case-id> --status approved`
  - supports `approved`, `rejected`, `needs_more_info`, `provider_pending`, and `unmatched`.
- Added staging go/no-go checklist:
  - `docs/operations/VERIFICATION_STAGING_GO_NO_GO.md`
- Added focused backend tests for:
  - provider-specific redacted sandbox request builders for Dojah, Sumsub, and Smile ID;
  - signed replay fixtures for rejected, needs-more-info, provider-pending, and unmatched callbacks;
  - approved callback badge issuance retained from Phase 11.
- Staff console badge issue/revoke and audit inspection remain connected from Phase 11 and covered in the staging checklist.

Validation:

- `python3 -m py_compile apps/verification/providers.py apps/verification/services.py apps/verification/tests.py apps/verification/management/commands/verification_webhook_replay_fixture.py config/settings/base.py config/settings/production.py` passed.
- Focused Django tests passed with `--keepdb --noinput`:
  - `test_provider_specific_sandbox_requests_are_redacted`
  - `test_signed_provider_webhook_replay_status_fixtures`
  - `test_signed_provider_webhook_maps_approved_user_case_to_badges`
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations --check --dry-run` returned no model changes when run with local database access.
- `python3 manage.py verification_provider_readiness` passed and printed non-secret configured/live/sandbox-network status.
- `python3 manage.py verification_private_media_access_check` passed in no-asset readiness mode.
- `npm run typecheck -- --pretty false` passed in `/Users/nigel/dev/KIS`.
- Focused React Native ESLint passed for verification staff console and verification service.
- `npm run ci:launch` passed in `/Users/nigel/dev/KIS`; production audit found 0 vulnerabilities.

Blocked / deferred:

- No real external provider sandbox call was executed in this local environment because credentials/network are not configured here and `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED` defaults to false.
- Private media signed-access proof still needs a real staging private `MediaAsset --asset-id`.
- Real provider-console evidence and callback URLs must be captured in staging before any provider is treated as enabled.
- Institution-provider sandbox expansion remains Phase 13 work.

### Phase 13 - Institution Sandbox Expansion And Staging QA Hardening - Completed 2026-05-06

Extend the staging-only provider sandbox readiness model beyond users, without enabling production live provider calls.

Delivered:

- Generalized provider sandbox handoff from user-only to allowed subject types behind the existing staging-only gates:
  - `DJANGO_ENV=staging`;
  - `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=true`;
  - `VERIFICATION_PROVIDER_SANDBOX_ENABLED=true`;
  - optional `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=true`;
  - `VERIFICATION_LIVE_PROVIDER_SUBJECTS` includes the target subject type.
- Added shared redacted handoff behavior for partner, health institution, and education institution verification cases.
- Kept production live provider calls and production sandbox-network calls blocked by settings safeguards.
- Added subject-aware provider request shaping:
  - user cases remain document/identity style requests;
  - institution cases use business/KYB style provider request metadata where the adapter supports it.
- Added subject-specific webhook mapping for provider callbacks:
  - user approval issues `verified_user` and `id_verified`;
  - shop approval issues `verified_shop`;
  - partner approval issues `verified_partner`;
  - health institution approval issues `verified_health_institution`;
  - education institution approval issues `verified_education_institution`;
  - rejected, needs-more-info, and pending states update the matching case safely.
- Improved the React Native staff verification console:
  - subject filters for all, users, shops, partners, health, and education;
  - status filters for open, provider pending, submitted, needs info, approved, and rejected;
  - badge issue actions now adapt to the selected subject type;
  - revoke actions remain available for active badges.
- Added focused backend regression tests proving:
  - partner, health institution, and education institution sandbox handoff stores redacted `provider_pending` records;
  - provider webhook approval maps to the correct badge for shop, partner, health, and education subjects;
  - provider-specific sandbox request redaction still holds.

Validation:

- `python3 -m py_compile apps/verification/providers.py apps/verification/services.py apps/verification/tests.py` passed.
- Focused Django tests passed with `--keepdb --noinput`:
  - `test_institution_sandbox_handoff_is_redacted_and_provider_pending`
  - `test_provider_webhook_approval_maps_subject_specific_badges`
  - `test_provider_specific_sandbox_requests_are_redacted`
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations --check --dry-run` returned no model changes.
- `python3 manage.py verification_provider_readiness` passed and printed non-secret configured/live/sandbox-network status.
- `python3 manage.py verification_private_media_access_check` passed in no-asset readiness mode.
- Focused React Native ESLint passed for the verification staff console and verification service.
- `npm run ci:launch` passed in `/Users/nigel/dev/KIS`; production audit found 0 vulnerabilities.

Blocked / deferred:

- No real external provider sandbox HTTP call was executed locally because staging credentials/network are not configured and sandbox network execution defaults to false.
- Private media signed-access proof still needs a real staging private `MediaAsset --asset-id`.
- Real provider-console evidence, callback URL proof, and staff-console QA screenshots still need to be captured in staging.
- The legacy commerce shop verification submission flow remains commerce-driven for backward compatibility. Centralized provider webhook approval can issue shop badges, but full live provider handoff for legacy shop requests should be enabled only after staging evidence confirms it will not disrupt the commerce workflow.
- Production live calls remain intentionally disabled.

### Phase 14 - Production Sign-Off Readiness - Completed 2026-05-06

Finalize the verification production sign-off gate without enabling production live provider calls.

Delivered:

- Verified the local production settings safety posture:
  - production settings load when verification live provider calls and sandbox-network calls are disabled;
  - production settings fail closed if `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=true`;
  - production settings fail closed if `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=true`.
- Re-ran the full verification backend regression suite, including:
  - user verification start/status/evidence/review behavior;
  - provider webhook signature rejection and signed callback mapping;
  - subject-specific badge issuance for user, shop, partner, health institution, and education institution cases;
  - staff case access, badge issue/revoke, audit, provider-callback, suspicious-signal, and expiry-reminder endpoints.
- Re-ran provider readiness and private-media readiness commands without printing secrets or file contents.
- Re-ran React Native launch validation after staff-console filter/action changes.
- Expanded the staging go/no-go checklist into a production sign-off evidence matrix with explicit owners, proof requirements, monitoring requirements, rollback requirements, and approval rules.

Validation:

- `python3 manage.py test apps.verification --keepdb --noinput` passed with 17 tests.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations --check --dry-run` returned no model changes.
- `python3 manage.py verification_provider_readiness` passed with non-secret output.
- `python3 manage.py verification_private_media_access_check` passed in no-asset readiness mode.
- Production settings check with provider live/sandbox-network flags disabled passed using safe dummy env values.
- Production settings check with `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=true` failed closed with `ImproperlyConfigured`.
- Production settings check with `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=true` failed closed with `ImproperlyConfigured`.
- Focused React Native ESLint passed for the verification staff console and verification service.
- `npm run ci:launch` passed in `/Users/nigel/dev/KIS`; production audit found 0 vulnerabilities.

Explicit go/no-go status:

- **NO-GO for production live verification provider calls.**
- The code/config gate is in good shape, but production enablement still requires real staging evidence:
  - one provider end-to-end user case;
  - one provider end-to-end institution case;
  - real private-media signed-access proof with a staging asset id;
  - provider callback URL proof from the provider dashboard;
  - staff console QA evidence across planned launch subject types;
  - monitoring destination proof and rollback owner sign-off.

Blocked / deferred:

- Real external provider sandbox execution was not performed locally because staging credentials/network and provider-console access are not present here.
- Real private-media signed-access proof with `--asset-id` was not performed because no staging private asset id is available locally.
- Production live provider calls remain disabled and must not be enabled without explicit approval.

### Phase 15 - Staging Evidence Execution And Release-Ticket Capture - Completed 2026-05-06

Capture all Phase 15 evidence that can be proven from the current environment and create a clean release-ticket evidence log for the remaining staging-only proofs.

Delivered:

- Added `docs/operations/VERIFICATION_PHASE15_STAGING_EVIDENCE.md` as the release-ticket evidence log.
- Confirmed local provider readiness remains safe:
  - selected provider: Dojah;
  - Dojah/Sumsub/Smile ID are not configured in local env;
  - live provider calls are disabled;
  - sandbox network calls are disabled.
- Confirmed private-media proof tooling is ready but still needs a real staging private `MediaAsset --asset-id`.
- Generated local signed webhook replay fixtures with a non-production throwaway secret for:
  - approved;
  - rejected;
  - needs-more-info;
  - provider-pending;
  - unmatched.
- Re-ran the full backend verification regression suite.
- Re-ran React Native focused verification lint and full launch validation.
- Updated the staging go/no-go checklist with Phase 15 status and blockers.

Validation:

- `python3 manage.py verification_provider_readiness` passed and reported providers unconfigured with live calls disabled.
- `python3 manage.py verification_private_media_access_check` passed in no-asset readiness mode.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations --check --dry-run` returned no model changes.
- `python3 manage.py test apps.verification --keepdb --noinput` passed with 17 tests.
- `verification_webhook_replay_fixture` generated local signed fixtures for approved, rejected, needs-more-info, provider-pending, and unmatched statuses.
- Focused React Native ESLint passed for the verification staff console and verification service.
- `npm run ci:launch` passed in `/Users/nigel/dev/KIS`; production audit found 0 vulnerabilities.

Explicit go/no-go status:

- **NO-GO for production live verification provider calls.**
- Reason: real staging credentials, provider-console access, sandbox network configuration, a real staging private media asset id, monitoring evidence, and rollback owner sign-off were not available in this local environment.

Blocked / deferred:

- Real end-to-end user provider sandbox case.
- Real end-to-end institution provider sandbox case.
- Real private-media signed-access proof with staging `--asset-id`.
- Provider callback URL proof from provider dashboard.
- Staff console QA evidence from staging build/device.
- Monitoring and rollback evidence.

## Best Prompt For Phase 1

```text
Please proceed with Phase 1 of the KIS verification system without using git commands. Focus on the canonical Django backend foundation only. Create a new `apps.verification` app with backward-compatible models for verification subjects, cases, checks, badges, and audit events covering users, shops, health institutions, education institutions, and partners. Add serializers, admin registration, permissions, badge summary helpers, safe settings/env config, and migrations. Do not integrate live external providers yet and do not store raw documents in verification models. Preserve existing shop verification behavior. Run safe Django validation checks, record blockers, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 2.
```

## Best Prompt For Phase 2

```text
Please proceed with Phase 2 of the KIS verification system without using git commands. Focus on user verification flow only. Build provider-neutral user verification APIs on top of `apps.verification`: request/start case, submit evidence metadata, read current verification status, staff/manual review actions, webhook receiver skeleton with signature verification placeholder, and badge issuance for `verified_user` / `id_verified` without making live provider calls yet. Add Dojah and Sumsub adapter stubs that read env config but never log secrets. Connect public badge summaries to user/profile serializers where safe. Do not store raw documents in verification models; use private media references only. Preserve existing auth/profile behavior, add focused tests where safe, run Django validation, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 3.
```

## Best Prompt For Phase 3

```text
Please proceed with Phase 3 of the KIS verification system without using git commands. Focus on shop verification migration only. Connect existing `ShopVerificationRequest`, `Shop.is_verified`, `verification_status`, and `trust_badges` to the centralized `apps.verification` source of truth while preserving all current commerce shop verification endpoints and UI behavior. Add shop subject creation, backward-compatible syncing, public shop badge summaries, safe manual review mapping, and regression tests for existing shop verification behavior plus centralized badge issuance. Do not make live provider calls, do not store raw documents in verification models, and keep private media references only. Run safe Django validation, record blockers, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 4.
```

## Best Prompt For Phase 4

```text
Please proceed with Phase 4 of the KIS verification system without using git commands. Focus on partner/company KYB verification only. Connect partner organization profiles/accounts to the centralized `apps.verification` source of truth with provider-neutral business verification cases, representative authorization metadata, beneficial-owner/company registration evidence metadata, manual staff review actions, and public badges such as `verified_partner`, `verified_organization`, and `official_partner` where appropriate. Preserve existing partner APIs and UI behavior, do not make live provider calls, do not store raw documents in verification models, keep private media references only, add focused regression tests or record blockers, run safe Django validation, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 5.
```

## Best Prompt For Phase 5

```text
Please proceed with Phase 5 of the KIS verification system without using git commands. Focus on health institution verification only. Connect existing health institution models and health dashboard/public health institution serializers to the centralized `apps.verification` source of truth with provider-neutral legal registration, address/domain/phone, medical license/accreditation, expiry, and staff authorization evidence metadata. Add request/start status and staff/manual review paths where safe, issue public badges such as `verified_health_institution` and `licensed_provider`, preserve existing health APIs and UI behavior, do not make live provider calls, do not store raw documents in verification models, keep private media references only, add focused regression tests or record blockers, run safe Django validation, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 6.
```

## Best Prompt For Phase 6

```text
Please proceed with Phase 6 of the KIS verification system without using git commands. Focus on education institution verification only. Connect existing education institution models and public/dashboard education serializers to the centralized `apps.verification` source of truth with provider-neutral legal registration, domain/address/phone, accreditation/certification, expiry, certificate issuer trust, and staff authorization evidence metadata. Add request/start status and staff/manual review paths where safe, issue public badges such as `verified_education_institution` and `accredited_education`, preserve existing education APIs and UI behavior, do not make live provider calls, do not store raw documents in verification models, keep private media references only, add focused regression tests or record blockers, run safe Django validation, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 7.
```

## Best Prompt For Phase 7

```text
Please proceed with Phase 7 of the KIS verification system without using git commands. Focus on the frontend verification center and badge display only. Build or connect shared React Native verification UI components for badge rendering, status cards, verification center sheet, evidence metadata submission forms using private media references, provider handoff placeholders, review timeline/status history, and contextual entry points across user profile, shop dashboard, health institution management, education institution management, and partner workspace. Preserve existing screens and navigation behavior, do not make live provider calls, do not expose raw documents or secrets, keep local development working, add focused frontend validation/tests or record blockers, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 8.
```

## Best Prompt For Phase 8

```text
Please proceed with Phase 8 of the KIS verification system without using git commands. Focus on admin review, abuse visibility, badge revocation, expiry reminders, and audit operations. Add or connect staff-only review queues for user, shop, partner, health institution, and education institution verification cases; badge issue/revoke actions; provider callback inspection placeholders; suspicious pattern alerts; audit export/read views; expiry/reverification reminders; and focused backend/frontend regression tests where safe. Preserve existing APIs/UI, do not make live provider calls, do not expose secrets or raw documents, run safe validation, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 9.
```

## Best Prompt For Phase 9

```text
Please proceed with Phase 9 of the KIS verification system without using git commands. Focus on launch QA, provider integration hardening, and production readiness evidence. Add or document provider sandbox runbooks for Dojah, Sumsub, and Smile ID; webhook replay/signature validation checks; private media picker/upload integration planning; staff review console QA; badge display QA across profile/shop/partner/health/education surfaces; expiry reminder notification planning; and production rollout/rollback rules. Keep live provider calls disabled unless explicitly configured, do not expose secrets/raw documents, run safe backend/frontend validation, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 10.
```

## Best Prompt For Phase 10

```text
Please proceed with Phase 10 of the KIS verification system without using git commands. Focus on the final implementation bridge before live provider enablement: private media picker/upload integration for verification evidence, staff/admin review console UI, notification scheduling for verification expiry/reverification reminders, and feature-flagged provider enablement controls. Keep live provider calls disabled by default, do not expose secrets/raw documents, preserve existing flows, add focused backend/frontend tests or record blockers, run safe validation, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 11.
```

## Best Prompt For Phase 11

```text
Please proceed with Phase 11 of the KIS verification system without using git commands. Focus on staging-only live provider sandbox enablement behind feature flags. Wire the first safe provider sandbox path for user verification only, keep production live calls disabled, add provider request/response redaction, webhook event mapping for approved/rejected/needs-info states, sandbox callback replay tests, staff console badge issue/revoke actions, private media signed-access proof, and end-to-end QA evidence for user verification from upload to badge. Do not expose secrets/raw documents, do not enable live production calls, preserve existing flows, run safe backend/frontend validation, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 12.
```

## Best Prompt For Phase 12

```text
Please proceed with Phase 12 of the KIS verification system without using git commands. Focus on real staging sandbox execution readiness without enabling production live calls. Add provider-specific Dojah/Sumsub/Smile sandbox request adapters behind `DJANGO_ENV=staging` and `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=true`, use strict redacted logging, persist only safe provider references/results, complete private media signed-access proof with a real staging asset, add webhook replay fixtures for approved/rejected/needs-info/unmatched callbacks, expand staff console QA for badge issue/revoke and audit inspection, and document the exact go/no-go checklist for enabling one provider in staging. Do not expose secrets/raw documents, do not enable production calls, preserve existing flows, run safe backend/frontend validation, update docs/verification-system-roadmap.md and docs/BUILD_STATE.md, and give the best prompt for Phase 13.
```

## Best Prompt For Phase 13

```text
Please proceed with Phase 13 of the KIS verification system without using git commands. Focus on institution verification expansion and staging QA hardening without enabling production live calls. Extend the provider-sandbox readiness model from user verification to shops, partners, health institutions, and education institutions where safe; keep all provider calls behind staging-only flags; add subject-specific webhook mapping and badge issue/revoke behavior; improve the staff console filters/actions for all subject types; run private-media signed-access proof with a real staging asset if available; add focused backend/frontend regression tests or record blockers; update docs/verification-system-roadmap.md, docs/BUILD_STATE.md, and the staging go/no-go checklist with evidence needed before Phase 14 production sign-off.
```

## Best Prompt For Phase 14

```text
Please proceed with Phase 14 of the KIS verification system without using git commands. Focus on final production sign-off readiness without enabling production live provider calls until explicitly approved. Capture and verify staging evidence for one provider end-to-end across user and at least one institution subject, run private media signed-access proof with a real staging asset, complete provider callback URL proof, validate staff console review/badge/revoke/audit flows across user/shop/partner/health/education, finalize rollback/monitoring/alerting, verify production env flags keep provider calls disabled, run full backend/frontend validation, update docs/verification-system-roadmap.md, docs/BUILD_STATE.md, and docs/operations/VERIFICATION_STAGING_GO_NO_GO.md with final launch blockers and explicit go/no-go status.
```

## Best Prompt For Phase 15

```text
Please proceed with Phase 15 of the KIS verification system without using git commands. Focus only on staging evidence execution and release-ticket capture, not production enablement. Using one selected provider and approved staging credentials, run an end-to-end user verification sandbox case and one institution verification sandbox case, prove private media signed access with a real staging MediaAsset id, capture provider callback URL evidence, run signed webhook replay for approved/rejected/needs-info/pending/unmatched statuses, validate staff console review/badge/revoke/audit flows on a real device or staging build, attach monitoring/rollback evidence, keep production live provider calls disabled, update docs/verification-system-roadmap.md, docs/BUILD_STATE.md, and docs/operations/VERIFICATION_STAGING_GO_NO_GO.md with evidence links and the final Phase 15 go/no-go status.
```

## Best Prompt For Phase 16

```text
Please proceed with Phase 16 of the KIS verification system without using git commands. Focus on completing the blocked staging evidence from Phase 15, using approved staging-only credentials and a real staging private MediaAsset id. Execute one end-to-end user verification sandbox case and one institution verification sandbox case with the selected provider, capture provider-console callback URL proof, run `verification_private_media_access_check --asset-id <id>`, replay approved/rejected/needs-info/provider-pending/unmatched webhooks against staging, validate staff console review/badge/revoke/audit/provider-callback flows on a staging build, attach monitoring and rollback evidence, keep production live provider calls disabled, update docs/operations/VERIFICATION_PHASE15_STAGING_EVIDENCE.md, docs/operations/VERIFICATION_STAGING_GO_NO_GO.md, docs/verification-system-roadmap.md, and docs/BUILD_STATE.md with evidence links and a final go/no-go recommendation.
```
