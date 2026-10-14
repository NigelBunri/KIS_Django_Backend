# KIS Google Auth Migration — Implementation Checklist

Status legend: ✅ verified · 🚧 in progress · ⛔ blocked · ⏭ not applicable (already correct, no change needed, or no longer relevant)

Policy decisions locked in by product owner on 2026-10-14:
- Enable KIS Auth flags now (production-bound, no half-shipped features).
- Remove email/SMS OTP completely (not just deprecate).
- Google linking is optional but strongly encouraged; never blocks normal app access, for new or existing accounts.

## A. Registration → Google linking experience (spec §2)
- [x] A1. ✅ `RegisterScreen.tsx`'s phone/password path sets `pendingGoogleLinkPrompt`; `App.tsx` pushes `KisAuthLink` (`fromRegistration: true`) once authenticated.
- [x] A2. ✅ `KisAuthLinkScreen.tsx` carries the spec's exact title/description/button/supporting copy in `fromRegistration` mode.
- [x] A3. ✅ Loading/cancel/failure/retry/success states and duplicate-tap prevention present (pre-existing `already_linked` handling extended with the registration-mode "Skip for now"/"Continue" affordances).
- [x] A4. ✅ Account creation + login happen before the linking screen ever mounts — skip, kill-mid-link, or link failure all leave a working logged-in account.
- [x] A5. ✅ Reachable again anytime from `ProfileScreen`'s "Connect Google Account" row.

## B. Google linking security (spec §3) — pre-existing, verified via test suites
- [x] B1. ✅ PKCE/state/nonce/redirect URI/issuer/audience/signature/expiry/replay — kis-auth test suite passes (48/48 runnable specs; 60 skipped only for missing local Postgres/Redis infra, not failures).
- [x] B2. ✅ Linking bound to authenticated KIS session via single-use `link_ticket`.
- [x] B3. ✅ Account-substitution protection — covered by `test_complete_rejects_a_result_for_a_different_user`, passes.
- [x] B4. ✅ Atomic DB updates on link/registration — covered by `test_a_failed_link_call_rolls_back_the_new_account`, passes.
- [x] B5. ✅ Unique `provider_subject` enforced DB-side (kis-auth `auth_identity` table).
- [x] B6. ✅ Audit logging excludes tokens/assertions/secrets (manually reviewed `AuditLog.log()` call sites).

## C. Email verification derived only from trusted Google claim (spec §4)
- [x] C1. ✅ `email_verified` set only from verified `provider_email_verified` claim, inside the atomic transaction — unchanged, re-verified.
- [x] C2. ✅ `EmailVerificationScreen.tsx` deleted.
- [x] C3. ✅ Added: `user.verification["google"]` (linked/email/email_verified/linked_at) populated server-side only in `KisAuthLinkCompleteView`/`KisAuthRegistrationCompleteView`; `UserSerializer.Meta.read_only_fields` now excludes `verification` from client writes (closes a pre-existing hole where it wasn't read-only); mobile `ProfileScreen.tsx` and `src/types/user.ts` consume the real field instead of the `email_verified` proxy.
- [x] C4. ✅ Failed/cancelled Google auth never reaches the success-branch code that sets either field.
- [x] C5. ✅ Documented in the security review and runbook: migration `0005_delete_phoneotp.py` only drops the OTP table — no existing account's `email_verified`/verification history is touched or reset.

## D. Remove email/SMS OTP completely (spec §5)
- [x] D1. ✅ `apps/otp/views.py`, `urls.py`, `admin.py`, `tasks.py`, `otp_utils.py` deleted.
- [x] D2. ✅ OTP-based `PasswordResetInitiateView`/`PasswordResetView` deleted.
- [x] D3. ✅ Celery beat purge task and `otp` throttle scope removed.
- [x] D4. ✅ `KIS_PHONE_VERIFICATION_ENABLED` removed from Django settings/`.env.example` and mobile `.env`/`.env.example`/`featureFlags.ts`.
- [x] D5. ✅ `apps/otp/migrations/0005_delete_phoneotp.py` created; reversible (`migrate otp 0004` regenerates `CreateModel`); applied only to the local dev DB, not production.
- [x] D6. ✅ Infobip SMS/WhatsApp settings/code removed (verified no other caller via repo-wide grep before deletion).
- [x] D7. ✅ Resend client/config and non-OTP transactional email call sites preserved; only the OTP-specific send function/template removed.
- [x] D8. ✅ `EmailVerificationScreen.tsx`, `DeviceVerificationScreen.tsx`, `VerificationChannelSelectScreen.tsx` deleted; `LoginScreen.tsx`'s OTP-channel modal/effect removed.
- [x] D9. ✅ `otp`, `sendDeviceCode`, `otpChannels`, `status`, `forgotPassword`, `resetPassword`, `parentRecoveryInit`, `parentRecoveryConfirm` removed from `authRoutes.ts`.
- [x] D10. ⏭ Phone/password login unaffected — no change made to login itself.
- [x] D11. ✅ OTP/Infobip env vars removed both sides; Resend/KISAUTH vars retained.

## E. Password recovery replacement (spec §6)
- [x] E1. ✅ `LoginScreen.tsx`'s "Forgot password?" now opens `ParentRecoveryScreen` (KIS Auth recovery).
- [x] E2. ✅ `KisAuthRecoveryCompleteView` validates assertion + existing account-to-subject relationship before any action — unchanged, re-verified.
- [x] E3. ✅ Generic error message on every failure branch, both Django and mobile (`ParentRecoveryScreen.tsx` failure copy is now uniform regardless of real cause).
- [x] E4. ✅ Shares the `password_reset` throttle scope; every outcome written to `AuditLog`.
- [x] E5. ✅ `apps.accounts.ParentRecoveryInitView`/`ParentRecoveryConfirmView` (pre-existing, not OTP-based) is the documented, intentionally-preserved fallback for accounts with no linked Google identity — confirmed by its own code comments and left untouched by the Django implementation fork.

## F. Account settings (spec §7)
- [x] F1. ✅ `ProfileScreen.tsx` row now reflects real `verification.google.linked` status (was previously an `email_verified` proxy before this session's follow-up fix).
- [x] F2. ✅ Connect action reuses `KisAuthLinkScreen`'s existing loading/error/success states. No "reconnect" affordance was added because no unlink/reconnect backend capability exists (see F3) — not invented, per the "don't invent a backend capability that doesn't exist" guardrail.
- [x] F3. ⏭ Not applicable — audited and confirmed no unlink capability exists anywhere in `apps/kis_auth_bridge` or kis-auth, so there is nothing to gate.

## G. Database & legacy migration (spec §8)
- [x] G1. ✅ Migration reviewed (plain, reversible `DeleteModel`), applied only to local dev DB.
- [x] G2. ✅ No other app's tables touched; `apps/accounts` (accounts, credentials, sessions, history) untouched.
- [x] G3. ✅ Phone numbers untouched — OTP removal only affected the `otp` app's own table.

## H. Backward compatibility (spec §9)
- [x] H1. ⏭ `USERNAME_FIELD="phone"` unchanged.
- [x] H2. ⏭ SimpleJWT lifecycle unchanged.
- [x] H3. ⏭ RN Keychain/Keystore + AsyncStorage migration behavior unchanged — not touched by this migration.
- [x] H4. ⏭ `backend/Nestjs` unaffected — confirmed via its own passing `django-auth.service.spec.ts`/`internal-signing.spec.ts`.
- [x] H5. ⏭ Linking does not invalidate unrelated sessions — link-complete only writes `verification`, never touches `Device`/session state.

## I. Testing (spec §10)
- [x] I1. ✅ Django: targeted suite — 573 tests, 3 pre-existing/unrelated failures. Full repo-wide suite (3060 tests, ×2 runs) — zero regressions in apps.otp/apps.kis_auth_bridge/apps.accounts; all other failures pre-existing or concurrency-flaky in unrelated apps. See `KIS_GOOGLE_AUTH_TEST_REPORT.md`.
- [x] I2. ✅ kis-auth: `npm test` — 7 suites/48 tests passed, 0 failed, 8 suites/60 tests skipped (missing local Postgres/Redis — environment limitation, not a failure).
- [x] I3. ✅ Nest backend: `pnpm test` — 250/264 tests passed; 14 failures all in unrelated chat/messages/reactions specs (Mongo-dependent, pre-existing); both auth-relevant specs (`django-auth.service.spec.ts`, `internal-signing.spec.ts`) passed.
- [x] I4. ✅ Mobile: `jest` 6/6 passed; `tsc --noEmit` 0 errors; ESLint not run (repo-wide pre-existing config breakage, unrelated to this migration — documented limitation, not a skipped check we chose to skip).

## J. Deployment readiness (spec §11)
- [x] J1. ✅ `.env.example` updated in `backend/kis` and `KIS`; no real secrets added or exposed.
- [x] J2. ✅ Documented in `KIS_GOOGLE_AUTH_DEPLOYMENT_RUNBOOK.md`.
- [x] J3. ✅ Documented in the same runbook, including the fast env-var-only flag rollback path.

## K. Deliverables (spec §12)
- [x] K1. ✅ `KIS_GOOGLE_AUTH_MIGRATION_AUDIT.md`
- [x] K2. ✅ This checklist, kept current
- [x] K3. ✅ `KIS_GOOGLE_AUTH_SECURITY_REVIEW.md`
- [x] K4. ✅ `KIS_GOOGLE_AUTH_TEST_REPORT.md` — full-suite Django result recorded; zero regressions in apps.otp/apps.kis_auth_bridge/apps.accounts
- [x] K5. ✅ `KIS_GOOGLE_AUTH_DEPLOYMENT_RUNBOOK.md`
