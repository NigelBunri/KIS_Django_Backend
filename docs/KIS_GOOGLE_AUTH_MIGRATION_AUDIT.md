# KIS Google Auth Migration — Baseline Audit

Date: 2026-10-14
Scope: `backend/kis` (Django, source of truth), `backend/kis-auth` (NestJS Google/OIDC broker), `backend/Nestjs` (chat/calls/notifications consumer), `KIS` (React Native app).

## 0. Executive summary

This is **not** a greenfield migration. A production-grade KIS Auth Google-linking system already exists across all three backend-adjacent repos, built in a prior phase (mobile code comments reference "Phase 2 §7/§8"). It implements almost exactly the trust model this migration requires:

- Google/OIDC handled solely by `backend/kis-auth` (authorization-code + PKCE, state/nonce, RS256-signed single-use JWT assertions, JWKS).
- Django (`apps/kis_auth_bridge`) redeems assertions via JWKS, links by `provider_subject` (never by email string), sets `email_verified` only from the verified `provider_email_verified` claim, atomically creates/links accounts, has CSRF/session-swap protection on linking, and audits without secrets.
- `backend/Nestjs` already delegates 100% of identity/authorization decisions to Django introspection; it has zero OTP coupling and needs no change.

**What was actually missing / wrong, and has now been addressed by this migration:**
1. The whole system was gated behind Django flags defaulting to `False` (`KIS_AUTH_ENABLED`, `KIS_AUTH_REGISTRATION_ENABLED`, `KIS_AUTH_RECOVERY_ENABLED`, `KIS_AUTH_LINK_ENABLED`) — i.e. built but never turned on.
2. Email OTP and SMS OTP (`apps/otp`) were still the live, fully-wired password-reset and login-fallback mechanisms, not actually removed (only `KIS_PHONE_VERIFICATION_ENABLED` defaulted off for registration).
3. Mobile `EmailVerificationScreen` ran a live OTP flow for pre-recovery email verification — directly contradicting "verification must derive only from a trusted Google assertion."
4. `LoginScreen` still routed into an OTP-channel picker; `DeviceVerificationScreen` / `VerificationChannelSelectScreen` were still reachable from navigation.
5. No account-settings surface showed Google connection/verification status.
6. The phone/password registration path did not consistently route into the Google-linking screen afterward.

Per explicit user decision (2026-10-14): **(a)** enable the KIS Auth flags now — the product is moving to production and no feature should ship half-on; **(b)** remove email/SMS OTP completely, not just deprecate; **(c)** Google linking is **optional but strongly encouraged** — it must never lock a user out of normal app access, now or for existing accounts.

## 1. Repository inventory

| Repo | Path | Role | Git state at audit time |
|---|---|---|---|
| KIS Django Backend | `backend/kis` | Source of truth for accounts, authorization, verification state | `main`, clean, in sync with `origin/main` |
| KIS Auth | `backend/kis-auth` | NestJS/Fastify Google/OIDC identity broker | `main`, clean, in sync with `origin/main` |
| KIS Nest Backend | `backend/Nestjs` | Chat/calls/notifications consumer of Django identity | `main`, clean, in sync with `origin/main` |
| KIS React Native app | `KIS` | Mobile client | `main`, clean, in sync with `origin/main` |

No uncommitted or unpushed work existed in any repo prior to this migration's changes. `KIS/.worktrees/` contains 4 unrelated feature worktrees (broadcast-channels-closure, calls-ringing-fix, education-program-class, university-acceptance-test) — not touched.

## 2. `backend/kis-auth` — identity broker (verified correct, no code changes required)

- OAuth/OIDC: authorization-code + PKCE (S256), `state`, `nonce`, redirect URI validated against registered client (`oauth.controller.ts`).
- Assertion: RS256 JWT, claims `purpose`, `auth_identity_id`, `provider_subject`, `provider_email`, `provider_email_verified`, `provider`, `iss=kisauth.kingdomimpactventures.org`, `aud=client_id`, `exp` (60s default), `jti`. JWKS served with rotation overlap (`authorization-jwt.service.ts`, `jwks.controller.ts`).
- Django→kis-auth exchange call itself is authenticated via HMAC (`internal-signing.ts`, shared secret `KISAUTH_INTERNAL_HMAC_SECRET`), distinct from the RS256 assertion — both mechanisms named in the spec are real, scoped to different hops.
- Single-use enforcement: Redis atomic consume on authorization codes and link tickets; codes bound to original `client_id`/`redirect_uri`; identity `status === 'active'` re-checked at redemption time, not just issuance time.
- DB uniqueness: `UNIQUE(provider, provider_subject)` and `UNIQUE(kis_user_id)` on `auth_identity` (`src/db/migrations/001_init.sql`).
- `purpose=link` requires a Django-minted, HMAC-signed, single-use `link_ticket` binding the already-authenticated KIS session to the subsequent Google round trip — prevents unauthenticated account-substitution.
- Zero OTP/SMS/email-OTP code present (confirmed by repo-wide grep).

No changes made to this repo. It already fully satisfies spec sections 3 and parts of 6.

## 3. `backend/kis` — Django (changes made here; see checklist)

- `apps/accounts`: `User.email_verified` (BooleanField), `phone_is_placeholder` (enterprise SSO JIT accounts without a phone), `USERNAME_FIELD = "phone"` — **unchanged, per spec section 9**.
- `apps/kis_auth_bridge` (stateless, no models/migrations of its own — Google identity storage lives in kis-auth's DB, reached server-to-server):
  - `exchange_client.py`: RS256 verification via `PyJWKClient` against kis-auth's JWKS endpoint, issuer + audience checked.
  - `views.py`: `KisAuthLinkInitiateView`/`KisAuthLinkCompleteView` (require caller's authenticated id to match the redeemed `kis_user_id` — explicit account-substitution defense), `KisAuthRegistrationCompleteView` (atomic account + identity-link + device-issuance transaction, rolls back on link failure), `KisAuthRecoveryCompleteView` (no-auth endpoint, proof is the single-use recovery authorization code; reuses the existing `ParentRecoveryConfirmView` device-promotion/token-issuance logic rather than reimplementing it; generic error message on every failure branch — no account enumeration), `KisAuthSecurityEventView`/`KisAuthSsoConfigView` (separate HMAC secret, fail closed if unconfigured).
  - `email_verified` is set only inside the atomic registration/link transaction, only when the verified JWT claim `provider_email_verified` is true — never from a client-supplied flag.
  - Audit logging (`AuditLog.log(...)`) records identity id and verification boolean only, never tokens/assertions/secrets.
- `apps/otp` (727-line `views.py`): `ChannelsView`, `OtpInitiateView`, `OtpVerifyView`, `PasswordResetInitiateView`, `PasswordResetView`. Providers: Infobip (SMS/WhatsApp), Resend (email). This was the live password-reset and login-fallback path — **removed in this migration**, see checklist and security review for the replacement recovery policy for users without a linked Google identity.
- Rollout flags (`config/settings/base.py`): `KIS_AUTH_ENABLED`, `KIS_AUTH_REGISTRATION_ENABLED`, `KIS_AUTH_RECOVERY_ENABLED`, `KIS_AUTH_LINK_ENABLED`, `KIS_PHONE_VERIFICATION_ENABLED` — **flipped to `True`/removed per user decision**, see checklist.
- Migrations: `apps/accounts` latest `0047_user_search_trigram_indexes.py`, `apps/otp` latest `0004_alter_phoneotp_purpose.py` before this migration added an OTP-removal migration.

## 4. `backend/Nestjs` — consumer (no changes required)

All identity/authorization flows through `DjangoAuthService.introspect()`, which calls Django's `/auth/introspect/` over an HMAC-signed internal channel and caches briefly for revocation propagation. No independent OTP/email/phone-verification logic exists here. If Django's introspection payload ever gains new fields, no change is needed here unless a consumer needs to branch on them.

## 5. `KIS` — React Native app (changes made here; see checklist)

Pre-existing KIS Auth integration (not built in this session): `KisAuthLinkScreen.tsx` (sole entry point for linking, CSRF-style state + single-use `link_ticket`, explicit "linking never happens implicitly" invariant), `ParentRecoveryScreen.tsx` (account recovery via KIS Auth, deep-link + state verified), `KisAuthRegisterPhoneScreen.tsx` (cold-start fallback for the Google sign-up universal-link return), `src/security/kisAuthBrowser.ts` (in-app-browser OAuth launcher; pending-state deliberately kept in memory, not AsyncStorage), `src/network/routes/authRoutes.ts` (`kisAuthRecoveryComplete`, `kisAuthLinkInitiate`, `kisAuthLinkComplete`, `kisAuthRegistrationComplete` — all hit Django, never kis-auth directly from the client).

Pre-existing OTP surfaces — **removed in this migration**: `EmailVerificationScreen.tsx` (live email-OTP for pre-recovery verification), `DeviceVerificationScreen.tsx`, `VerificationChannelSelectScreen.tsx`, `LoginScreen.tsx`'s `ROUTES.auth.otpChannels` routing, OTP-related routes in `authRoutes.ts`.

Google Sign-In is browser/redirect-based (in-app browser + universal link) via kis-auth's `/authorize` endpoint, not a native Google Sign-In SDK — consistent with "Django/kis-auth are the sole broker," no second independent Google implementation exists on the client.

## 6. Classification of auth-adjacent functionality (spec section 5 requirement)

| Item | Classification | Disposition |
|---|---|---|
| `apps/otp` SMS/email OTP generation+verification | Authentication code | Removed |
| `apps/otp` password reset (OTP-based) | Authentication code | Removed; replaced by KIS Auth recovery + documented support path for unlinked legacy users |
| Phone/password login (`apps/accounts`) | Authentication code, independent of OTP | Retained unchanged, per spec section 9 |
| `USERNAME_FIELD="phone"` | Account identity model | Retained unchanged |
| Resend (email provider) | Used for OTP **and** other transactional email | OTP usage removed; non-auth transactional email usage retained |
| Infobip (SMS/WhatsApp provider) | Used only for OTP in this codebase | Provider integration removed along with OTP; no other caller found |
| `apps/kis_auth_bridge` | Authentication/linking code | Retained, flags enabled |
| Chat/calls/notifications (`backend/Nestjs`) | Unrelated business logic, depends on identity only via introspection | Untouched |
| Coins/payments/content/messages data | Unrelated business data | Untouched |

See `KIS_GOOGLE_AUTH_IMPLEMENTATION_CHECKLIST.md` for the itemized, verified checklist and `KIS_GOOGLE_AUTH_SECURITY_REVIEW.md` for the recovery policy for users without a linked Google identity.
