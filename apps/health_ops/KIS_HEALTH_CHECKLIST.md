# KIS Health — Production Readiness Checklist

Master checklist for completing KIS Health against the full platform vision
(practitioners, institutions, verification, consultations, health records,
labs, pharmacy, billing, partner/community integration, observability,
security, UX, accessibility). Started 2026-10-03.

**Status legend:** NOT_STARTED · IN_PROGRESS · BLOCKED · COMPLETE · VERIFIED
(VERIFIED = implemented AND tested AND the test run was actually observed
passing in this session, not just "written".)

**Critical context established on day 1**: this is NOT a greenfield build.
A mature KIS Health implementation already exists — `apps/health_ops/`
(~13k lines, 20 migrations, 76+ tests passing at session start) and
`apps/health_dashboard/` (~4.7k lines, a separate public-profile/CMS
layer), plus a RN frontend with 40+ screens under `src/screens/health/`.
The work here is gap-closing and hardening an existing system, not
designing one from scratch. Three parallel "health institution"
representations exist (health_ops.HealthInstitution, health_dashboard's
HealthDashboardInstitution, broadcasts.BroadcastHealthInstitution) — **this
was resolved in a later pass, see Section 12 below**: they are legitimately
different concepts (client-editable JSON profile / SQL projection+CMS layer
/ clinically-authoritative record) now linked by a real FK rather than a
bare settings-key string.

---

## 1. Discovery (DONE)

| # | Item | Status |
|---|---|---|
| 1.1 | Inventory existing health_ops models/views/services/tests | VERIFIED — 1162-line models.py, 7681-line views.py, full workflow-engine architecture read and understood |
| 1.2 | Inventory existing health_dashboard | COMPLETE (surface-level — models.py not yet read line-by-line) |
| 1.3 | Confirm existing generic verification system reuse | VERIFIED — `apps/verification/` already handles HEALTH_INSTITUTION subject type, badges `VERIFIED_HEALTH_INSTITUTION`/`LICENSED_PROVIDER`, full case/review workflow, test_verification.py passing |
| 1.4 | Confirm existing Partner/Community/Channel/Group reuse pattern | VERIFIED — `apps/broadcasts/education_communication_sync.py` is the exact template; Health had an explicitly-documented gap (`partner_sync.py` docstring) |
| 1.5 | Baseline existing test suite | VERIFIED — 76/76 tests green in health_ops + health_dashboard + verification before any changes |
| 1.6 | RN frontend discovery | COMPLETE — 40+ screens, own theme tokens, verification badge component, chat/call stack confirmed separate from health_ops's own VideoConsultationSession/SecureMessagingSession |
| 1.7 | Deep line-by-line audit of views.py (7681 lines) for IDOR/stub/security issues | **NOT_STARTED** — two dispatched audit agents failed on an account-wide session rate limit; not yet re-attempted or done manually |
| 1.8 | Deep audit of the 40+ RN health screens for wiring/completion state | **NOT_STARTED** — same rate-limit failure; not yet re-attempted |

## 2. Partner → Community/Channel Auto-Provisioning (Section 14-15 of spec)

| # | Item | Status |
|---|---|---|
| 2.1 | Extract shared join/leave/admin-grant helpers to `apps/partners/communication_sync_helpers.py` (dedup with Education's copy) | VERIFIED — education's 67 dedicated tests pass after refactor |
| 2.2 | Add `HealthInstitution.community` + `HealthService.channel` FKs | VERIFIED — migration `0020_healthinstitution_community_healthservice_channel` applied to dev DB |
| 2.3 | Build `apps/health_ops/communication_sync.py` (ensure_institution_community, ensure_service_channel, sync_membership_communication, sync_patient_service_communication, on_institution_partner_connected/disconnected) | COMPLETE, test run in progress |
| 2.4 | Wire into institution creation, workflow-session start, partner connect/disconnect views | COMPLETE |
| 2.5 | Tests: idempotency, role→admin mapping, late-partner-connect backfill (the exact bug class Education's own acceptance test found), disconnect-never-deletes | VERIFIED — 12/12 tests passing |
| 2.6 | Full health_ops + broadcasts regression run after this change | VERIFIED — 60/60 health_ops passing at the time, 67/67 broadcasts education tests passing |

## 3. Practitioner Verification (Section 2 of spec)

| # | Item | Status |
|---|---|---|
| 3.1 | Confirm no `HealthPractitioner`/credential model exists today | VERIFIED gap — `TelemedicineConsult.doctor`/`MentalHealthSession.therapist` are plain User FKs with zero license/specialty/qualification fields; `EMedication.prescribed_by` is a **free-text CharField**, not even an FK |
| 3.2 | Design `HealthPractitioner` model (license number, specialty, qualifications, registration authority, jurisdiction, license status/expiry, linked User + optional HealthInstitution) | VERIFIED — `apps/health_ops/extended_models.py`, migration `0021_healthpractitioner` applied |
| 3.3 | Wire into existing `apps.verification` system (new subject type `HEALTH_PRACTITIONER` + reuse existing `LICENSED_PROVIDER` badge, scoped so practitioner cases can never self-issue `VERIFIED_HEALTH_INSTITUTION`) | VERIFIED — `apps/verification/services.py` (`practitioner_subject_for`, `current_practitioner_verification_status`, `start_practitioner_verification_case`, `review_practitioner_case`, `issue_practitioner_badges`), migration `verification/0002` |
| 3.4 | Migration + serializers + views + tests | VERIFIED — `HealthPractitionerSerializer`/`PractitionerDirectoryEntrySerializer` (narrow, never exposes license_number/registration_authority/evidence to patients)/`StartPractitionerVerificationSerializer`/`ReviewPractitionerCaseSerializer` in `extended_serializers.py`; `MyPractitionerProfileView` (self-service create/update, institution-claim blocked unless a real `HealthInstitutionMembership` exists), `PractitionerDirectoryView` (replaces the old is_staff-scaffold `DoctorDirectoryView`), `PractitionerVerificationStatusView`/`StartView`/`ReviewView` (admin-only) in `extended_views.py`; routes in `extended_urls.py`; 11 new tests in `tests/test_practitioner_verification.py`, all passing; full `apps.health_ops` suite re-run green at 71/71 |
| 3.5 | RN UI: practitioner verification submission form + badge display reusing `VerificationBadgeRow`/`BadgeTone` | NOT_STARTED — backend is ready to wire into |

## 4. Patient Health Record Gaps (Section 7 of spec)

| # | Item | Status |
|---|---|---|
| 4.1 | Confirm what exists: `EMedication`, `HealthGoal`, `HealthVitalReading`, `HealthCarePlan`, `PregnancyTracker`, `BabyMilestone`, `BloodTypeRegistry`, `MoodEntry` all exist | VERIFIED |
| 4.2 | Confirmed MISSING: structured `Condition`, `Allergy`, `Immunization` models | VERIFIED gap |
| 4.3 | Confirmed MISSING: `Referral` model (Section 13) — no referring/receiving practitioner-institution linkage exists anywhere | VERIFIED gap |
| 4.4 | Confirmed MISSING: structured `LabResult`/`LabOrder` model — lab ordering exists only as a generic `ClinicalEngineCode.LAB_ORDER` workflow with a JSON payload, not a queryable clinical record | VERIFIED gap |
| 4.5 | Design + implement Condition/Allergy/Immunization models | VERIFIED (self-service scope only — see 4.5a) — `apps/health_ops/extended_models.py` (`Condition`, `Allergy`, `Immunization`, `ClinicalRecordSource`), migration `0022_immunization_condition_allergy` applied; serializers + `ConditionViewSet`/`AllergyViewSet`/`ImmunizationViewSet` in extended_serializers/extended_views.py; routes `conditions/`, `allergies/`, `immunizations/` under `/api/v1/health/extended/`; 7 tests in `tests/test_patient_clinical_record.py`, all passing; full health_ops suite re-run green at 84/84 |
| 4.5a | **RESOLVED** — the Encounter concept now exists (see Section 4a below) and clinician-write is wired up: `EncounterConditionListCreateView`/`EncounterAllergyListCreateView`/`EncounterImmunizationListCreateView` in `clinical_views.py`, each forcing `source=clinician_recorded` and `recorded_by`/`administered_by=encounter.practitioner` server-side, gated on the requesting user being that exact encounter's own practitioner AND the encounter being `in_progress`/`completed` (never `scheduled` or `closed`). The patient's own self-service path (4.5) is untouched and remains fully separate — a patient can never produce a `clinician_recorded` entry through either path. 9 new tests in `tests/test_clinical_domain.py::ClinicianAuthoredRecordTests`, all passing. |
| 4.6 | Design + implement `ClinicalReferral` model (referring/receiving practitioner+institution) | VERIFIED — see Section 4a below |
| 4.7 | Design + implement structured LabOrder/LabResult linked to patient + encounter | VERIFIED — see Section 4a below |

## 4a. Clinical Encounter + Referral + Laboratory (built this pass — the Encounter dependency that unblocked 4.5a/4.6/4.7)

| # | Item | Status |
|---|---|---|
| 4a.1 | `Encounter` model (patient, practitioner, institution, optional workflow_session backlink, type, status lifecycle, reason/notes/assessment/treatment_plan, timestamps) | VERIFIED — `apps/health_ops/extended_models.py`, migration `0023_encounter_laborder_specimen_labtestcatalogitem_and_more` applied. Deliberately distinct from the pre-existing generic `ClinicalEngineSession` (a booking-workflow step tracker with no clinical semantics of its own) — Encounter is the real clinical-relationship record those write paths now anchor to. |
| 4a.2 | Encounter creation authorization (`apps/health_ops/clinical_authorization.py`) | VERIFIED — requires (a) the acting user to hold a currently-active `LICENSED_PROVIDER` badge via `apps.verification` (not merely a `HealthPractitioner` row), (b) institution membership, (c) the named patient to have a real, data-backed prior relationship with that institution (`ServiceWorkflowSession` exists) — a practitioner cannot fabricate an encounter with a patient who has never engaged that institution. |
| 4a.3 | Encounter status lifecycle + transitions | VERIFIED — `scheduled → checked_in/in_progress → completed/cancelled/no_show → closed`, enforced via an explicit transition table in `EncounterTransitionView`; only the encounter's own practitioner may transition it; a `closed` encounter is immutable (clinical-content PATCH rejected). |
| 4a.4 | `ClinicalReferral` model + lifecycle (pending/accepted/declined/completed/cancelled, priority, referring/receiving practitioner+institution) | VERIFIED — `ClinicalReferralListCreateView`/`DetailView`/`ActionView` in `clinical_views.py`; create requires an open encounter owned by the acting practitioner; accept/decline/complete restricted to the receiving side; cancel restricted to the referring practitioner; a practitioner can never accept their own outgoing referral. |
| 4a.5 | Structured Laboratory domain (`LabTestCatalogItem`, `LabOrder`, `Specimen`, `LabResult`) replacing the generic JSON `ClinicalEngineCode.LAB_ORDER` step | VERIFIED — full lifecycle `ordered → specimen_collected → processing → result_pending_verification → resulted`; order creation requires the encounter's own practitioner; specimen collection and result entry require institution membership + an active `LICENSED_PROVIDER` badge; **four-eyes enforced** — the practitioner who entered a result can never also verify it; a patient can never see a result before it is lab-verified (`result` field is nulled out of their response until `status=resulted`), and `delivered_at` is stamped the first time the patient actually views a verified result. |
| 4a.6 | Tests | VERIFIED — `tests/test_clinical_domain.py`, 24 tests covering Encounter/Referral/Lab lifecycles and every authorization edge case (unverified practitioner, non-member, no prior patient relationship, stranger practitioner, wrong-status writes, four-eyes violation, patient-forged writes). All passing; full `apps.health_ops` suite re-run green at 108/108. |

## 4b. Prescription / Pharmacy Workflow Integrity (Section 8 of spec)

| # | Item | Status |
|---|---|---|
| 4b.1 | Real prescriber relationship for `EMedication` | VERIFIED — added `prescribing_practitioner` (FK to `HealthPractitioner`) and `encounter` (FK to `Encounter`) fields, migration `0024_emedication_encounter_emedication_is_revoked_and_more` applied. The pre-existing `prescribed_by` free-text field is kept only for a patient's own legacy self-tracked "medications I'm taking" list and is explicitly documented as never proof of a real prescription; `is_verified_prescription` is a model property that is only ever true when `prescribing_practitioner` is set and the entry is not revoked. |
| 4b.2 | Clinician prescribing endpoint | VERIFIED — `EncounterPrescriptionListCreateView` in `clinical_views.py`, same Encounter-anchored authorization pattern as 4.5a (Condition/Allergy/Immunization): only the encounter's own practitioner, only while the encounter is open, `prescribing_practitioner`/`encounter` always server-set. A patient cannot write through this path at all. |
| 4b.3 | Prescription revocation | VERIFIED — `PrescriptionRevokeView`, restricted to the original prescribing practitioner or someone who can manage the institution the encounter belonged to; a revoked prescription immediately reports `is_verified_prescription=False`. |
| 4b.4 | **FIXED — real vulnerability found**: pharmacy workflow's `verify_prescription` step trusted an arbitrary client-supplied payload with zero server-side validation. Any authenticated patient calling `PharmacyFulfillmentSessionStepUpdateView.patch` directly (they already have legitimate access to this endpoint as the workflow session's owner) could mark their own `verify_prescription` step `is_completed=True`, flipping the pharmacy session to `VERIFYING` status with no real prescription behind it at all — a textbook "fake success" / client-controlled-state defect of exactly the kind Section 18/28 of the spec calls out. | FIXED — `apps/health_ops/views.py`, `PharmacyFulfillmentSessionStepUpdateView.patch`: completing `verify_prescription` now requires (a) the acting user to be institution staff, never the patient themselves, and (b) `payload.prescription_id` to reference a real `EMedication` row for that exact patient where `is_verified_prescription` is true (real prescriber, not revoked). 10 regression tests in `tests/test_prescription_integrity.py`, all passing, including cross-patient and revoked-prescription rejection cases. Full `apps.health_ops` suite re-run green at 118/118. |
| 4b.5 | Dispensing/inventory steps (`validate_inventory`, `confirm_delivery`, `fulfillment_tracking`) | Reviewed — these only move session status based on the pharmacy-side workflow's own progress and institution-member actions (not client-asserted completion of a *clinical* fact); no equivalent fake-completeness risk found. Full line-by-line audit of inventory/delivery business logic itself (stock levels, delivery-partner integration) is out of scope for this pass — flagged, not fabricated as reviewed. |

## 12. HealthInstitution Architecture Resolution (Section 5 of the final-completion prompt)

| # | Item | Status |
|---|---|---|
| 12.1 | Investigate the three representations | VERIFIED — read all three model definitions end-to-end. They are **legitimately different concepts, not accidental duplication**: `apps.broadcasts.BroadcastHealthProfile.payload` is the original, fully client-editable JSON blob (the mobile app's own free-form "manage my health listing" data — no schema, no validation); `apps.broadcasts.BroadcastHealthInstitution` is a real SQL table that is *rebuilt from that JSON on every profile save* (a projection, not an independent source of truth — see `apps/broadcasts/views.py`'s institution-sync logic); `apps.health_dashboard.HealthDashboardInstitution` is a presentation/CMS layer with landing-page styling, card copy, etc., and already had a real `OneToOneField` straight onto `BroadcastHealthInstitution`. `apps.health_ops.HealthInstitution` — the model this entire session's clinical work (Encounter, verification, billing, communities) anchors to — is the fourth and clinically-authoritative one. Collapsing them into one model would destroy real distinctions (JSON free-form self-reporting vs. CMS presentation vs. clinical record with real FKs/constraints); the correct fix is explicit, constrained linkage, not unification. |
| 12.2 | Replace the string-only cross-reference with a real relationship | VERIFIED — added `HealthInstitution.legacy_broadcast_institution` (`OneToOneField` to `BroadcastHealthInstitution`, `SET_NULL`), migration `0025_healthinstitution_legacy_broadcast_institution`. The pre-existing `settings["legacy_institution_id"]` JSON key — a bare string with no FK constraint, no uniqueness enforcement, no join-ability — is kept only as a fallback for rows that predate this field; `_find_legacy_health_institution()` in `views.py` always tries the real FK first. Both bootstrap functions (`_bootstrap_health_ops_institution_from_broadcast`, `_bootstrap_health_ops_context_from_broadcast`) now resolve and persist the FK at the moment a `HealthInstitution` is created or re-matched from legacy data — this is the only place the relationship is ever created, so it can never drift. |
| 12.3 | Backfill existing rows | VERIFIED — migration `0026_backfill_legacy_broadcast_institution`, a conservative `RunPython` data migration: only sets the FK when `institution_uid` resolves to exactly one `BroadcastHealthInstitution` row globally; an ambiguous match is left `NULL` rather than guessed at (a missed backfill degrades to the pre-existing fallback lookup; an incorrect guess would silently misattribute institution identity — asymmetric risk, so the conservative choice is correct). |
| 12.4 | Prove the relationship holds under drift | VERIFIED — `tests/test_legacy_institution_identity.py`, 5 tests: bootstrap creates the FK correctly; repeated bootstrap calls are idempotent (no duplicate `HealthInstitution` rows); when the legacy settings-key string is deliberately corrupted/stale, the real FK is still used and resolves correctly (proving the FK, not the string, is the actual source of truth); the full chain `HealthInstitution` ↔ `BroadcastHealthInstitution` ↔ `HealthDashboardInstitution` is walkable and consistent; the migration's own backfill-resolution logic is proven equivalent to the live bootstrap path. All passing; full `apps.health_ops` + `apps.health_dashboard` suite re-run green at 128/128. |

## 5. Security Audit of health_ops Views (Section 18 of spec)

| # | Item | Status |
|---|---|---|
| 5.1 | IDOR audit across all ~90 view classes in views.py (7681+ lines) | VERIFIED — systematic pass: enumerated every `APIView`/`ModelViewSet` class, confirmed each either (a) is explicitly `AllowAny` public-discovery data, (b) is `IsAdminUser`-gated platform admin, (c) scopes queries to `request.user` directly (no client-supplied owner id), or (d) calls the centralized `_is_institution_member`/`_can_manage_institution`/`_can_access_workflow_session` helpers before touching data. `_can_access_workflow_session` alone has 61 call sites across the ten domain-engine Start/Detail/StepUpdate/Payload/End view families (Video, SecureMessaging, Clinical, AdmissionBed, EmergencyDispatch, PharmacyFulfillment, PaymentBilling, HomeLogistics, WellnessProgram, NotificationReminder) — confirmed via direct read that sensitive data (e.g. video join tokens) is never serialized before that check runs. Two real gaps found and fixed (see below); everything else checked out. |
| 5.1a | **FIXED**: `EngineContentBlockListCreateView.post` and `EngineContentBlockDetailView` (patch/delete) allowed *any* authenticated user — including a plain patient — to create/edit/delete `EngineContentBlock` rows, which are global platform content attached to an `EngineRegistry` entry and shown to every patient of every institution using that engine (not institution-scoped). Fixed by requiring `IsAdminUser` for all three write paths; reads remain open to any authenticated user. 4 regression tests added (`tests/test_engine_content_block_permissions.py`), all passing. |
| 5.1b | **FIXED** (privacy-minimization, not a true IDOR): `HealthInstitutionSerializer` exposed `payout_account_name`/`payout_bank_last4` to *any* active institution member (STAFF/MEMBER role), not just those who can manage billing. Added `to_representation` masking so only viewers where `can_manage` is true see those two fields; `payout_account_status` stays visible to all members (useful, low-sensitivity). 2 regression tests added (`tests/test_payout_field_privacy.py`), all passing. |
| 5.2 | Verification-bypass audit (any path that sets verified status outside apps.verification's case workflow) | VERIFIED — grepped all `current_status`/badge-issuing writes in health_ops; the only paths that flip verification state are `apps.verification.services.review_health_institution_case`/`review_practitioner_case`, both reached only via the `IsAdminUser`-gated review views. No view in health_ops writes `VerificationSubject.current_status` or creates a `VerificationBadge` directly. |
| 5.3 | Payment/billing view audit (confirm real Stripe/Flutterwave calls, no fake-success paths) | VERIFIED — see Section 13 below. Two real "fake success" vulnerabilities found and fixed. |
| 5.4 | Emergency/critical-path audit (EmergencyDispatchSession, EmergencyAlert) | VERIFIED — see Section 14 below. A serious safety-relevant vulnerability found and fixed. |

## 13. Payment/Billing Security Audit (Section 18 of the final-completion prompt)

| # | Item | Status |
|---|---|---|
| 13.1 | Full line-by-line audit of `PaymentBillingSessionStartView`/`DetailView`/`StepUpdateView`/`PayloadView`/`EndView` (~600 lines) | VERIFIED. `StartView`'s quoting (total/insurance/payable amounts) was already correctly server-authoritative — only `_is_institution_member` callers can set a quote; a patient's own client-supplied amount is silently ignored in favor of the server-derived `ServiceEngineMap.cost_micro`. `EndView` is safe as a direct consequence of the two fixes below (it only completes when `billing_session.paid_at` is already set, and `paid_at` can now only be set through a real wallet debit or a webhook-confirmed payload). |
| 13.2 | **FIXED — real vulnerability (fake payment success)**: `PaymentBillingSessionStepUpdateView`'s `authorize_payment` step accepted a bare client-supplied `payload.payment_status == "paid"` as sufficient proof of payment, OR'd directly alongside the real checks. Any patient — who already has legitimate access to this endpoint as the workflow's own owner — could PATCH `{"step_key": "authorize_payment", "payload": {"payment_status": "paid"}}` and flip their own bill straight to `PAID` with zero real money moving. This is precisely the "fake success" class the spec's Section 18/28 names explicitly. | FIXED — the explicit client-trust OR-clause was removed entirely. `PAID` can now only be reached via (a) a real, synchronous KIS-wallet debit performed server-side in the same request (validated against actual wallet balance), or (b) `_health_provider_payment_confirmed()`, which reads only the stored `billing_session.payload`/`metadata`/`paid_at` — fields that are, after the companion fix in 13.3, only ever written by the real payment-provider webhook (`apps.billing.direct_payments.reconcile_direct_payment_callback`), never by this request's own body. |
| 13.3 | **FIXED — companion vulnerability (payload poisoning + amount forgery)**: `PaymentBillingSessionPayloadView.patch` let *any* caller with workflow access — including the patient — merge an arbitrary `payment_status`/`paymentStatus` directly into the stored payload (which `_health_provider_payment_confirmed` reads back as ground truth on a later call), and separately let them set `amount_paid_micro`/`amount_paid_kisc`/`invoice_number` directly, with no validation at all. | FIXED — `payment_status`/`paymentStatus` are now stripped from both `payload` and `metadata` unless the caller is institution billing staff (`_is_institution_member`); explicit `amount_paid_micro`/`amount_paid_kisc`/`invoice_number` overrides are likewise restricted to billing staff (to record a legitimate out-of-band/cash payment) or an explicit, bounded server-side wallet debit — never a bare patient claim. The step-update view's own optimistic default (provisionally setting `amount_paid_micro` to the full payable amount while `PAYMENT_PENDING`, used to gate the "amount paid" display before real confirmation) was preserved since on its own it was never what gated the `PAID` status — only an *explicit* client override of that figure for a non-wallet, non-staff caller is now blocked. |
| 13.4 | Regression tests | VERIFIED — `tests/test_billing_payment_integrity.py`, 5 tests: patient cannot self-certify payment via the step-update endpoint; patient cannot poison the stored payload via the payload endpoint and then ride that into a `PAID` flip; patient cannot set `amount_paid_micro` via the payload endpoint; institution staff legitimately can (out-of-band payment recording); and the real webhook-confirmed path still works end-to-end. All passing alongside the two pre-existing tests that exercise the legitimate wallet-debit and Flutterwave-webhook flows (`test_authorize_payment_debits_kis_wallet_once`, `test_health_billing_defaults_to_usd_provider_pending_without_wallet_debit` in `test_workflow_runtime.py`) — full `apps.health_ops` suite re-run green at 128/128. |

## 14. Emergency/Critical-Path Security Audit (Section 19 of the final-completion prompt)

| # | Item | Status |
|---|---|---|
| 14.1 | `EmergencyAlertViewSet` (patient's own SOS/notify-contacts log) | VERIFIED — correctly scoped: `get_queryset` filters strictly to `request.user`; this model has no institution/dispatch dependency (it's a personal log of "I notified my own emergency contacts"), so a patient marking their own alert resolved is legitimate self-reporting, not a trust boundary. No fix needed. |
| 14.2 | `SOSCreateView` "nearest hospitals" response | Reviewed — honestly self-labeled as scaffolded in both code comments and the API response itself (`"note": "Hospital proximity data is scaffolded..."`), not presented as working. Building a real geospatial/Places-API integration requires an actual vendor choice (Google Places, Mapbox, etc.) and credentials — a genuine external dependency, not an engineering gap to fake around. See Section 10 (External Dependencies) in the final report. |
| 14.3 | **FIXED — real, safety-relevant vulnerability**: a patient could self-certify real-world emergency-response facts — "ambulance dispatched", "paramedics arrived", "emergency resolved" — with zero institution confirmation, across **three separate endpoints**: `EmergencyDispatchSessionStepUpdateView`'s `dispatch_ambulance`/`track_response` steps, `EmergencyDispatchTrackingView`'s direct client-supplied `status` field (a `ChoiceField`-validated but institution-unrestricted enum write), and `EmergencyDispatchSessionEndView`'s resolve action. Any of the three could flip a live emergency session straight to `DISPATCHED`/`ARRIVED`/`RESOLVED` by the patient's own claim alone — by panic, UI mistake, or malice — which could suppress a genuinely-needed institution-side dispatch or escalation. This is the same "fake success" defect class as the billing vulnerability (Section 13), but in a domain where the stakes are physical safety, not money. | FIXED — all three endpoints now require `_is_institution_member` (real emergency-response staff) for every status-advancing transition. The one exception deliberately preserved: `CANCELLED` remains patient-accessible on the tracking and end endpoints — a patient calling off their own false alarm / no-longer-needed request is a legitimate, safe, patient-originated action, unlike claiming real-world response events occurred. Self-reported information (`capture_location`, `triage_form` — the patient's own location and symptoms) remains patient-writable, since those are facts only the patient can supply, not confirmations of institution action. |
| 14.4 | Regression tests | VERIFIED — `tests/test_emergency_dispatch_integrity.py`, 11 tests: patient can still report own location/triage; patient blocked from self-certifying dispatch/arrival via step-update; institution staff can legitimately confirm both; patient blocked from self-setting status via the tracking endpoint but can still cancel; institution staff can set any status via tracking; patient blocked from self-resolving via the end endpoint but can still cancel; institution staff can resolve; a stranger with no relationship to the session is rejected outright. All passing; full `apps.health_ops` suite re-run green at 139/139. |

## 6. Observability (Section 19 of spec — see separate KIS Health Infrastructure Audit artifact for the cross-repo version)

| # | Item | Status |
|---|---|---|
| 6.1 | health_ops-specific: Sentry/logging coverage for the workflow-engine task paths | VERIFIED — confirmed the project's existing `sentry_sdk.init(...)` (config/settings/production.py) already captures every unhandled exception project-wide via `DjangoIntegration`/`CeleryIntegration` with no additional per-app wiring needed, AND (since default integrations are additive, not replaced) its default `LoggingIntegration` automatically turns any `logger.error(...)` call into a real Sentry event — this is what the new stuck-session sweeps below rely on rather than inventing a parallel alerting mechanism. |
| 6.2 | Stuck-session detection for long-running EngineSession/workflow rows | VERIFIED — see Section 15 below. New `apps/health_ops/tasks.py` with two Celery beat sweeps, following the project's own established recurring-sweep convention (`apps/broadcasts/tasks.py`'s `sweep_stuck_education_bookings`, `apps/billing/tasks.py`'s expiry sweeps) rather than designing a new pattern. |

## 15. Workflow-Engine Observability / Stuck-Session Sweeps (built this pass, closes 6.1/6.2)

| # | Item | Status |
|---|---|---|
| 15.1 | `sweep_stuck_emergency_dispatch_sessions` | VERIFIED — flags any live (`WAITING`/`TRIAGING`/`DISPATCHED`/`IN_TRANSIT`) `EmergencyDispatchSession` whose last real activity (`last_tracking_at`, falling back to `created_at` via `Coalesce` for a session abandoned before its first tracking update ever landed — see 15.2) is older than 30 minutes, logging at `ERROR` level (auto-captured by Sentry per 6.1) with session id/institution/patient/stuck-duration. Registered in `CELERY_BEAT_SCHEDULE` every 10 minutes — deliberately tight relative to the 30-minute threshold, since this is the one health_ops domain where "stuck" plausibly means a patient is still waiting for help that never came. |
| 15.2 | NULL-`last_tracking_at` edge case | VERIFIED gap-then-fix within the same pass: a session created and then immediately abandoned (e.g. a dropped connection right after `/start`, before any tracking ping) would never match a plain `last_tracking_at__lte=cutoff` filter at all, since SQL NULL comparisons never match — exactly the "silently stuck forever" failure this sweep exists to prevent. Fixed by `Coalesce("last_tracking_at", "created_at")` before filtering. Covered by its own dedicated regression test. |
| 15.3 | `sweep_stuck_billing_sessions` | VERIFIED — flags any `PaymentBillingSession` stuck `PAYMENT_PENDING` for 60+ minutes (a likely lost/delayed payment-provider webhook — not a security issue per the Section 13 audit, since the session can never self-certify `PAID`, but a real operational gap: a patient who may have already paid externally stuck unable to receive care). Logged at `WARNING` (operational, not life-safety). Registered hourly, matching the project's standard non-critical sweep cadence. |
| 15.4 | Tests | VERIFIED — `tests/test_stuck_session_sweeps.py`, 7 tests: stale-tracking detection, the NULL-fallback edge case, recently-updated sessions correctly NOT flagged, terminal states (`RESOLVED`/`CANCELLED`) never flagged regardless of age, stuck billing detection, recently-updated billing not flagged. All passing; full `apps.health_ops` suite re-run green at 146/146. |

## 7. RN Frontend Completion Audit (Sections 17, 35 of spec)

| # | Item | Status |
|---|---|---|
| 7.1 | Deep per-screen audit (wired vs. mock data, loading/error/empty states, verification display correctness, navigation dead-ends) | PARTIAL — see Section 16 below. Full line-by-line manual review of all 40+ screens was not performed (genuinely infeasible in remaining session scope); instead did (a) a targeted deep-read of every screen in the direct chain this session's backend work touches (practitioner directory/booking/verification), and (b) a repo-wide grep sweep across all 40 screens for TODO/mock/placeholder/scaffold markers, triaging every match. This is an honest partial coverage, not a claim of exhaustive review. |
| 7.2 | Fix findings from 7.1 | VERIFIED for everything found — see Section 16. |

## 16. RN Practitioner Verification UI + Frontend Audit Findings (Section 16/17 of the final-completion prompt)

| # | Item | Status |
|---|---|---|
| 16.1 | **FIXED — real bug, confirmed crash**: `DoctorDirectoryScreen.tsx`'s `Doctor` type and rendering expected `name`/`rating`/`review_count` fields that the real backend (`PractitionerDirectoryEntrySerializer`) has never sent — not even the old is_staff-scaffold predecessor sent a rating. `doc.rating.toFixed(1)` would throw on the very first real API response (`rating` is `undefined`). Rewrote the screen to match the actual response shape (`legal_name`/`profession_type`/`specialty`/`institution_name`/`verification_status`), replacing the fake star-rating UI with the real verification badge — directly satisfying this section's own requirement to surface practitioner verification status to patients. | FIXED |
| 16.2 | **FIXED — confirmed dead-end navigation**: the directory's "Book" button navigated to `ConsultDetailScreen` with `{doctorId, doctorName}`, but that screen only ever reads `route.params.consultId` — it would fetch `/consults/undefined/` and never recover. No consult was ever created. Fixed by having "Book" actually `POST` a new `TelemedicineConsult` (using the practitioner's `user` id — added to the directory serializer for exactly this purpose, reusing the existing `TelemedicineConsult.doctor` User FK rather than inventing a parallel booking model) and navigating with the real created consult's id. | FIXED |
| 16.3 | **Found, not fixed — flagged honestly, out of scope for this pass**: `DoctorDirectoryScreen` and `ConsultDetailScreen` are declared in `RootStackParamList` but are **never registered in `AppNavigator.tsx`** and are not reachable through `BroadcastHealthcarePage.tsx` (the screen that actually hosts `SoloPractitionerDashboard`) either — they are orphaned, unreachable screens today regardless of the data-contract fix above. Deliberately not rewired in this pass: this user has prior, explicit, painful experience with RN navigation/overlay changes breaking the whole app (see the `feedback_rn_modal_stacking` memory), and choosing *how* these screens should become reachable (stack push vs. the embedded-panel convention this app actually uses elsewhere) is a product/navigation-architecture decision, not a confirmable bug fix — it is called out here rather than silently left as an undocumented gap. |
| 16.4 | Practitioner verification RN UI (backend was verified, UI was not) | VERIFIED — added `'health_practitioner'` as a first-class `VerificationSubjectType` in `verificationService.ts` with real route wiring (`practitionerMe`, `practitionerVerificationStatus`, `practitionerVerificationStart` in `healthExtendedRoutes.ts`), then built `PractitionerVerificationCard` inside `SoloPractitionerDashboard.tsx`'s Profile tab — the one already-reachable practitioner-facing screen. Deliberately reuses the existing `VerificationBadgeRow`/`VerificationCenterSheet` components rather than duplicating verification UI (explicit instruction in this section), and deliberately does NOT touch this screen's own pre-existing, separately-flagged legacy profile system (`ROUTES.healthcare.profile`) — a real, pre-existing architectural split a prior session already documented as unresolved, not something to silently merge here. Handles both states: no practitioner profile yet (create form: legal name + profession type) and an existing profile (badge + "Manage verification" sheet). |
| 16.5 | Repo-wide grep sweep for TODO/mock/placeholder/scaffold across all 40 screens | VERIFIED — 24 of 40 files matched at least one keyword; triaged every match. All but one were benign (`placeholder=` TextInput hint props) or honestly-labeled genuine gaps already self-documented as "coming soon" in `NotificationReminderManager.tsx`/`PaymentBillingManager.tsx` (not faked, not hidden). `SOSCreateView`'s scaffolded "nearest hospitals" response (backend) is likewise honestly labeled and requires a real geospatial/Places-API vendor choice — a genuine external dependency, not an engineering gap (see final report's External Dependencies section). No other hidden mock-data or fake-completeness patterns found in the frontend within this pass's scope. |
| 16.6 | Build verification | VERIFIED — `node_modules/.bin/tsc --noEmit` across the entire RN project: **zero TypeScript errors**. `eslint` on every changed file: zero new errors (5 pre-existing `@typescript-eslint/no-unused-vars` errors at other, untouched lines in `SoloPractitionerDashboard.tsx`, confirmed pre-existing via `git diff --stat` showing this change as 161 pure insertions with zero deletions/modifications); only new findings are `react-native/no-inline-styles` warnings consistent with this file's own pre-existing style throughout. No RN simulator/device was available in this environment to click through the live UI — this is disclosed explicitly rather than claimed as done; typecheck + lint + direct code reading are the verification actually performed. |

---

## FINAL COMPLETION PASS — Honest Summary

This checklist spans two passes. The first pass established the real
architecture (a mature existing system, not a blank slate) and shipped four
vertical slices (partner/community auto-provisioning, practitioner
verification backend, the first IDOR audit, self-service patient records).
The second — this final completion pass — closed every gap that pass left
open, per the explicit "do not stop, build the dependency" instruction:

5. **Clinical Encounter foundation (Section 4a)** — the blocking dependency
   the first pass correctly identified and declined to fake around. A real
   `Encounter` model with a full status lifecycle, authorization anchored
   to a verified `LICENSED_PROVIDER` badge + institution membership + a
   data-backed prior patient relationship (never a bare claim).
6. **Clinician-authored records (Section 4.5a)** — `Condition`/`Allergy`/
   `Immunization` writes anchored to an open Encounter, strictly separated
   from the patient's own self-service path; a patient can never produce a
   `clinician_recorded` entry through either path.
7. **Referral lifecycle (Section 4a)** — pending/accepted/declined/
   completed/cancelled, authorization split correctly between referring
   and receiving sides.
8. **Structured Laboratory domain (Section 4a)** — replaced the generic
   JSON lab-order step with real catalog/order/specimen/result models,
   four-eyes-enforced (the result-enterer can never also verify), patient
   never sees a result before lab verification.
9. **Prescription/pharmacy integrity (Section 4b)** — real prescriber
   relationship added to `EMedication`; **found and fixed a real fake-
   payment-style vulnerability**: the pharmacy workflow's `verify_prescription`
   step trusted a bare client claim with zero server-side check.
10. **HealthInstitution architecture resolved (Section 12)** — the three
    representations are legitimately different concepts (confirmed by
    reading all three end-to-end, not assumed), now linked by a real FK
    instead of a bare settings-key string, backfilled for existing rows.
11. **Payment/billing security audit (Section 13)** — **two real "fake
    success" vulnerabilities found and fixed**: `authorize_payment` trusted
    a bare client `payment_status: "paid"` claim to flip a bill to PAID
    with zero real money moving; the generic payload endpoint let any
    patient poison the same stored field and forge `amount_paid_micro`
    directly. Both closed; the legitimate wallet-debit and
    Flutterwave-webhook paths re-verified still work.
12. **Emergency/critical-path security audit (Section 14)** — **a real,
    safety-relevant vulnerability found and fixed**: a patient could
    self-certify "ambulance dispatched"/"arrived"/"resolved" across three
    separate endpoints with zero institution confirmation — the same bug
    class as the billing one, but here the stakes are physical safety, not
    money. Fixed across all three; patient self-cancellation deliberately
    preserved as the one legitimate patient-originated transition.
13. **Workflow-engine observability (Section 15)** — confirmed the
    project's existing Sentry/logging infrastructure already covers
    health_ops with zero extra wiring, then built two new Celery beat
    sweeps (stuck emergency sessions, stuck pending payments) following the
    project's own established sweep convention, including a NULL-fallback
    edge case the first version of the sweep would have missed entirely.
14. **RN frontend — practitioner verification UI + confirmed bug fixes
    (Section 16)** — found and fixed a confirmed crash (`DoctorDirectoryScreen`
    rendering fields the real backend has never sent) and a confirmed
    dead-end (the "Book" button navigated nowhere real); built the
    practitioner-facing verification UI the backend was ready for but the
    frontend never had, reusing the existing verification components
    rather than duplicating them. Also surfaced, honestly and without
    attempting to silently fix given this app's documented navigation
    fragility, that `DoctorDirectoryScreen`/`ConsultDetailScreen` are
    orphaned — declared in the route types but never mounted in the
    navigator or any embedded-panel host.

### Test results (exact numbers)

- Backend, full combined regression (`apps.health_ops` + `apps.verification`
  + `apps.health_dashboard` + `apps.broadcasts` education suite), final run
  this pass: **241 / 241 passing, 0 failed, 0 skipped.**
- `apps.health_ops` alone: **146 / 146 passing** (started this pass at 84).
- `python manage.py makemigrations --check --dry-run`: **no changes
  detected** — every model change has a committed migration.
- Frontend: `tsc --noEmit` across the entire RN project: **0 errors.**
  `eslint` on every changed file: **0 new errors** (5 pre-existing,
  unrelated errors confirmed via `git diff --stat` showing this session's
  change as pure insertions). No RN simulator/device was available in this
  environment — this is disclosed explicitly, not glossed over; typecheck +
  lint + direct code reading are what was actually performed, not a claim
  of interactive UI testing.
- `git status`, both repos: clean, every changed/new file accounted for and
  intentional, nothing stray or suspicious.

### Security findings this pass, all fixed and regression-tested

1. Pharmacy `verify_prescription` step — bare client claim accepted as
   proof of payment-equivalent clinical fact. **Fixed.**
2. Billing `authorize_payment` step — bare client `payment_status: "paid"`
   accepted as proof of real payment. **Fixed.**
3. Billing generic payload endpoint — patient could poison stored payment
   facts / forge `amount_paid_micro` directly. **Fixed.**
4. Emergency dispatch — patient could self-certify dispatch/arrival/
   resolution across three endpoints with no institution confirmation.
   **Fixed** (the one safety-relevant finding of this entire pass).

### What remains — genuinely, not as a hiding place

- ~~RN: `DoctorDirectoryScreen`/`ConsultDetailScreen` reachability~~ —
  **corrected and resolved in the Closure Pass (Section 17).** The original
  claim above that these screens were unmounted was itself wrong: they were
  already registered in `App.tsx`'s real `RootStack` (229 screens) the whole
  time — `AppNavigator.tsx` is a decoy file that only holds the 5-tab bottom
  bar, not the real navigator. The actual gap was narrower: nothing in the
  live UI ever navigated to `TelemedicineHub`, the screen that leads to
  them. Fixed with one entry card, no navigator restructuring involved.
- **Full manual line-by-line audit of all 40+ RN Health screens.**
  Genuinely infeasible to complete exhaustively in this pass. What was
  actually done: a deep read of every screen in the direct chain this
  session's backend work touches, plus a repo-wide grep sweep across all 40
  for TODO/mock/placeholder markers with every match triaged. This is
  disclosed as partial coverage, not claimed as exhaustive.
- **Payment/billing business logic beyond the security audit** (e.g. full
  line-by-line review of dispensing/inventory stock-level logic) — flagged
  as out of this pass's scope in Section 4b.5, not fabricated as reviewed.
- Sections 1.2 (health_dashboard surface-level-only inventory from the
  first pass) was not re-deepened in this pass.

## External Dependencies (genuinely cannot be completed from this repo/environment)

- **`SOSCreateView`'s "nearest hospitals" lookup** — **resolved as far as
  engineering goes in the Closure Pass (see Section 17)**: a pluggable
  `HospitalProximityProvider` interface now exists
  (`apps/health_ops/hospital_proximity.py`), defaulting to a
  `NullHospitalProximityProvider` that honestly reports zero results and
  `hospital_lookup_available: False` rather than fabricating a hospital
  entry. What remains is a genuine external dependency: selecting and
  credentialing a real geospatial/Places vendor (Google Places, Mapbox, HERE,
  or similar) and pointing `HEALTH_HOSPITAL_PROXIMITY_PROVIDER` at an
  implementation of that interface.
- **Live RN simulator/device testing.** No iOS/Android simulator or
  physical device was available in this environment. Static verification
  (TypeScript compile, lint, direct code/contract reading) was performed to
  the fullest extent possible and is clearly distinguished above from
  interactive UI testing, which was not performed.
- **App-store-style approval processes, physical-location inspection for
  institution verification, regulatory/licensing-board integration** —
  none of these were in scope for this pass's actual findings, but are
  named here per the standing instruction to distinguish genuine external
  blockers from unfinished engineering, should they arise in a future pass.

**Environment note for future sessions**: this repo's `kis_test` Postgres
database serves double duty as both the real "default" dev DB (for
`manage.py migrate`/`shell`) and Django's test-runner scratch DB — the two
uses are mutually exclusive against the same DB name. Drop it before
`manage.py test`, recreate + `migrate` it before any other `manage.py`
command. See the `kis-test-db-dual-purpose` memory for the full explanation.
**Update from the Closure Pass**: a concurrent sibling session on this same
machine can be mid-`migrate`/`test` against `kis_test` at the same time —
check `ps aux | grep manage.py` and `psql -c "SELECT * FROM pg_stat_activity
WHERE datname='kis_test'"` before any drop/recreate, and prefer
`manage.py test --keepdb` when the schema is already current, since a race
on an unconditional drop corrupts the DB (`duplicate key value violates
unique constraint "pg_type_typname_nsp_index"` was observed and
self-resolved once the other session's `migrate` finished).

---

## 17. Closure Pass (2026-10-03) — complete

Scope: finish every closeable engineering gap, verify properly, leave only
genuine human/external dependencies. This section is being updated live as
the pass proceeds — see task list for current status.

### Found and fixed

1. **9 orphaned Health screens had zero live entry point.**
   `EmergencyHub` (the SOS screen), `MentalHealthHub` (and transitively
   `MoodJournal`/`CrisisResources`), `AddictionRecovery`, `SobrietyTracker`,
   `PregnancyTrackerScreen`, `BabyMilestones`, `Medications`, `HealthGoals`,
   `SymptomChecker` were all correctly registered in `App.tsx`'s
   `RootStack` and individually functional, but nothing in the live app ever
   called `navigation.navigate()` on any of them — confirmed via exhaustive
   grep for incoming calls to each screen name. A patient had no way to
   reach the emergency SOS screen at all. Fixed with a "Quick Tools" entry
   grid added to `BroadcastHealthcarePage.tsx` (the real live Health tab
   surface), using the same plain `navigation.navigate()` pattern already
   used elsewhere in that file. `tsc --noEmit` and `eslint`: 0 errors.
2. **Real crash: AI Symptom Checker field-name mismatch.** Backend
   (`AISymptomsCheckerView` / `_triage_symptoms` in `extended_views.py`)
   returned `level`/`recommendation`, while the RN screen
   (`SymptomCheckerScreen.tsx`) reads `triage_level`/`recommendations`. Every
   successful check crashed with `Cannot read properties of undefined
   (reading 'toUpperCase')`. Rewrote the backend response to match the
   frontend's existing (better-designed) contract, mapped the old "low"/
   "unknown" triage levels onto the frontend's allowed set
   (emergency/urgent/moderate/mild), added an honest "this is rule-based
   keyword matching, not a real AI model" disclaimer that the frontend now
   renders. Regression tests: `test_symptom_checker_contract.py` (6 tests).
3. **Dishonest SOS confirmation/success copy.** `EmergencyScreen.tsx` told
   the user pressing SOS would "alert your emergency contacts and nearby
   responders" and that afterward "Help is on the way." Neither claim was
   true: there is no `EmergencyContact` model anywhere in the codebase
   (backend or frontend — confirmed by grep), `EmergencyAlert.notified_contacts`
   is never populated by any code path, and `EmergencyAlertViewSet` scopes
   alerts to the creating user only — nobody else, human or automated, is
   notified. Rewrote the copy to be honest: the alert is saved to the
   user's account and nearest-hospital info is shown when a provider is
   configured, but the user is explicitly told to call local emergency
   services directly for real dispatch. Also fixed a pre-existing unused
   `navigation` prop lint error in the same file (unrelated to this change,
   cleaned up while already in the file).
4. **Nearest-hospitals production boundary (Task from Section 1(c)/4
   of the Closure Pass prompt).** Found already done by a concurrent sibling
   session on this machine (`apps/health_ops/hospital_proximity.py`): a
   clean `HospitalProximityProvider` ABC, a `NullHospitalProximityProvider`
   default that honestly returns zero results instead of fabricating a
   hospital entry, and `SOSCreateView` wired to use it. Reviewed it — sound,
   matches exactly what this pass would have built. Added the missing
   `HEALTH_HOSPITAL_PROXIMITY_PROVIDER` setting to `config/settings/base.py`
   (previously only an undocumented `getattr` default) so ops has a
   documented integration point. Regression tests already existed
   (`test_hospital_proximity_boundary.py`, 4 tests, verified still passing).
5. **Patient/alert isolation regression coverage added.** New
   `test_emergency_sos_honesty.py` covers SOS-alert persistence and
   per-user list isolation (`EmergencyAlertViewSet` correctly scopes to
   `request.user`, confirmed with a two-user test), which the existing
   hospital-proximity test file didn't exercise.

### Verified clean (no action needed)

- `MedicationsScreen.tsx`, `HealthGoalsScreen.tsx`: real API calls, correct
  loading/empty states, all `KISIcon` names valid against the `ion` map in
  `kisIcons.tsx`.
- `apps/core/views.py` permission classes (`CanManageRolesPermission` etc.)
  — the word "Placeholder" in one docstring is a stale naming leftover, the
  actual enforcement (`is_staff`/`is_superuser` check) is real.
- The billing `amount_paid_micro` provisional-default comment in
  `views.py` (~line 5522) documents already-fixed logic from the prior
  pass, not a new gap.

### Cross-session finding

This machine is running at least one other concurrent Claude Code session
(`dev-b5`) in this exact same working directory (not an isolated worktree),
confirmed via `ListAgents`. It independently built the
`hospital_proximity.py` fix at the same time this pass was about to. Sent a
coordination message via `SendMessage` after the DB collision above to
avoid further duplicate work and flag the collision pattern. Future
sessions: check `ListAgents` and `ps aux | grep manage.py` before heavy
DB-touching work in this repo.

### Additional findings (second sweep of the 9 previously-orphaned screens)

6. **Crisis hotline country-code mismatch.** `CrisisResourcesScreen.tsx`'s
   country picker sends ISO-3166 alpha-2 codes, including `GB` for the
   United Kingdom. `CRISIS_HOTLINES` in `extended_views.py` only had a `UK`
   key, so a UK user got the generic international fallback entry instead
   of the real Samaritans/PAPYRUS numbers already present in the dict —
   silently serving the wrong (less specific) hotlines in a safety-critical
   feature. Fixed by adding a `GB` key with the same data. Deliberately did
   **not** add numbers for the four other listed-but-uncovered countries
   (KE, GH, CA, IN) — crisis hotline phone numbers are exactly the kind of
   safety-critical fact that must come from a verified source, not model
   recall; this is called out below as something needing real content
   verification, not invented.
7. **Pre-existing lint errors in `SoloPractitionerDashboard.tsx`** (not
   introduced this pass, found while re-linting a file this pass already
   touches): one completely dead `activeConsult`/`setActiveConsult` state
   pair (declared, never read or set anywhere) and three `catch (_) {}`
   blocks tripping `no-unused-vars`. Removed the dead state; changed the
   catch blocks to the parameterless `catch {}` form (identical behavior,
   valid since ES2019). 0 errors now.
8. Manually read all 9 previously-orphaned screens end-to-end (not just the
   two above): `MedicationsScreen`, `HealthGoalsScreen`, `MentalHealthScreen`
   + `MoodJournalScreen` + `CrisisResourcesScreen`, `PregnancyTrackerScreen`,
   `BabyMilestonesScreen`, `AddictionRecoveryScreen`, `SobrietyTrackerScreen`.
   All use real API calls, correct loading/empty states, and valid `KISIcon`
   names (cross-checked against both the `ion` map and the component's
   special-cased names like `crown`). One minor, non-crashing completeness
   gap noted: `PregnancyTrackerScreen` has no UI to *create* a pregnancy
   profile (the backend `PregnancyTrackerViewSet` supports POST, the screen
   only GETs/PATCHes) — flagged, not built, given time/scope.

### Remaining at time of this checklist update

- Remaining ~30 of 40+ Health screens beyond the 9 orphaned ones not yet
  individually audited for API-contract/crash issues (same disclosed
  partial-coverage boundary as the prior pass).
- Crisis hotline coverage for KE/GH/CA/IN — needs real, verified phone
  numbers from the project owner or a vetted source, not invented.
- Full security re-verification pass — **done this pass.** Directly
  re-read (not just re-ran) the authorization code for: `_is_institution_member`
  /`_can_manage_institution` (institution membership/role checks),
  `_can_view_encounter`/`can_write_clinical_content`/`_encounter_clinical_write_guard`
  (Encounter + clinician-authored record IDOR), `_is_receiving_side`
  (referral accept/decline/complete), `_can_view_lab_order` + the lab
  four-eyes check in `LabResultVerifyView` (entrant cannot also verify),
  `PrescriptionRevokeView`'s authorization docstring/check, the billing
  `authorize_payment`/payload-endpoint fixes from the prior pass (confirmed
  still intact, not reverted by concurrent edits), the emergency-dispatch
  institution-membership gates, `PractitionerVerificationStartView`'s
  self-or-institution-manager check, and `apps.core`'s
  `_accessible_patient_ids`/`_can_access_patient_record` PHI-scoping (spot
  check of the explicitly-out-of-full-audit-scope domain — found solid:
  superuser-only unrestricted access, correct three-path union for everyone
  else, used consistently in `get_queryset()` so default DRF retrieve/
  update/destroy are protected, not just custom actions). No new
  authorization gap found; all prior fixes confirmed intact.
- **Final test suite run — done.** `python manage.py test apps.health_ops
  apps.verification apps.health_dashboard --keepdb -v 1`: **190/190
  passing, 0 failed.** (187 pre-existing + 3 new from
  `test_crisis_hotlines_country_codes.py`; the other new files this pass —
  `test_symptom_checker_contract.py` 6 tests, `test_emergency_sos_honesty.py`
  3 tests — were also verified independently passing earlier in the same
  pass and are included in this 190.)
- Frontend: `tsc --noEmit` across the entire RN project: **0 errors.**
  `eslint` on every file touched this pass: **0 errors** (pre-existing
  `react-native/no-inline-styles` warnings only, same pattern as the prior
  pass). Found and fixed pre-existing (not introduced this pass) lint
  errors in `SoloPractitionerDashboard.tsx` while already in the file.
- Final A–F closure report compiled for the user this pass (see below).

### Scope-boundary finding not previously documented: `apps.core`

While tracing the RN frontend's `src/network/routes/healthRoutes.ts` for the
40+-screen audit, found that 7 of those screens (including
`ClinicalCommandCenterScreen.tsx`, `EHRManager.tsx`,
`VideoConsultationManager.tsx`) are powered not by `health_ops` at all but by
a separate, mature Django app, **`apps/core`** (8577 lines, wired into
`config/urls.py` via `path("api/v1/", include("apps.core.urls"))`,
131 of its own tests — not run as part of this pass's 190, see below). It
implements a parallel hospital/clinical-operations domain:
`PatientMasterRecord`, `StaffProfile`, `Ward`, `ClinicalTask`,
`EmergencyEscalation`, `TriageRecord`, `ReferralRoute`, `ClinicalEventLog`,
`ComplianceAuditLog`, `TelemedicineSession`. It has its own IDOR-prevention
pattern (`PatientScopedQuerySetMixin`, `_accessible_patient_ids`/
`_can_access_patient_record`, backed by a `HealthDataAccessGrant` model) that
is NOT the same mechanism as `health_ops`'s institution-membership checks.

This was never audited by any pass of this KIS Health effort before the spot
check noted earlier in this section (which found it solid: superuser-only
unrestricted access, correct three-path union otherwise, enforced in
`get_queryset()` so it isn't just the custom actions that are protected).
That spot check is proportionate due diligence, **not a substitute for a
real audit** — `apps.core` is comparable in size to all of the Encounter/
Referral/Lab/clinical-authorization work built across this entire KIS Health
effort, and deserves its own pass with its own dedicated time, the same way
`health_ops` got. Disclosed here plainly rather than either silently
expanding scope to cover it or omitting that it exists.

### Safety check: `apps.broadcasts` unaffected by the communication_sync refactor

`apps/broadcasts/education_communication_sync.py` was refactored (by this
pass's work in Section 2) to delegate to the new shared
`apps/partners/communication_sync_helpers.py` instead of duplicating that
logic. Ran the full `apps.broadcasts` suite (403 tests) to confirm this
didn't regress Education: **5 pre-existing failures found, all in
`EducationInstitutionFormNormalizationTests`** (price-string normalization
and payment-eligibility-gating tests) — confirmed via `git log` that the
files involved (`apps/billing/eligibility.py`, `apps/broadcasts/tests.py`)
were last committed 2026-08-23, over a month before this session, and have
zero uncommitted changes from this session or the concurrent peer session.
None of the 5 failures reference community/channel/sync in any way. This is
a genuine, pre-existing, out-of-scope issue in Education's payment flow —
named here for honesty, not fixed, since it predates and is unrelated to
this Health effort.
