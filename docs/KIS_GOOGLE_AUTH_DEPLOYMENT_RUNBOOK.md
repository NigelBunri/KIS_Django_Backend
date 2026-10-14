# KIS Google Auth Migration — Deployment Runbook

Date: 2026-10-14. Nothing in this migration has been deployed, migrated, or flag-flipped against any real staging/production environment. All changes described here exist only in each repo's local working tree on `main`, uncommitted, pending review.

## 1. What changed, per repo

| Repo | Code default change | DB migration | Requires env/secret changes to deploy |
|---|---|---|---|
| `backend/kis` | `KIS_AUTH_*` flags default `True`; `apps/otp` removed | Yes — `apps/otp/migrations/0005_delete_phoneotp.py` | Remove Infobip vars (no longer read); keep Resend vars |
| `backend/kis-auth` | None | None | None |
| `backend/Nestjs` | None | None | None |
| `KIS` (mobile) | `KIS_AUTH_LINK_ENABLED`/`KIS_AUTH_REGISTRATION_ENABLED` default `true` in app config; OTP screens removed | N/A | None new; takes effect on next app store release, not existing installs |

## 2. Deployment order

1. **kis-auth**: no change in this migration — confirm it's already running the version with PKCE/JWKS/link-ticket support (it already is, per audit). No redeploy strictly required, but safe to redeploy if other changes are bundled.
2. **backend/kis (Django)**:
   a. Apply migration `apps/otp/migrations/0005_delete_phoneotp.py` during a maintenance window or via standard rolling migration process — it only drops the now-empty `PhoneOTP` table, no data transformation needed (OTP codes are inherently short-lived/useless after expiry).
   b. Deploy the code that removes `apps/otp` views/urls/tasks and enables the `KIS_AUTH_*` flags. Because the flags are **code defaults** that still read from the environment, you can deploy the code first with an explicit env override (`KIS_AUTH_ENABLED=false` etc.) still in place if you want a separate, explicit flag-flip step — recommended for a staged rollout: deploy code with flags forced off via env, verify deployment health, then remove the env overrides (or set them `=true` explicitly) to actually enable KIS Auth flows.
   c. Remove Infobip env vars from the deployed environment once confirmed no other service reads them (this audit found no other caller, but re-grep the deployed environment's full codebase state before removing, since local audit can't see other deployed services).
3. **KIS mobile app**: ship the new build. Because this changes client-side navigation (registration → linking step) and removes screens, this needs an actual app store release — it cannot be hot-patched. Until users update, their app will still show the old OTP-based flows if Django's old OTP endpoints are gone, **existing un-updated clients calling removed OTP endpoints will get 404s** — see rollback/compatibility note below.
4. **backend/Nestjs**: no change, no redeploy required for this migration.

## 3. Compatibility window risk (read before step 2b)

Removing Django's `apps/otp` endpoints breaks any mobile client build that still calls them — i.e. every installed copy of the app older than the release from step 3. Because app store rollout is never instantaneous:

- **Recommended**: deploy the mobile app update (step 3) and wait for a majority of active sessions to upgrade (monitor via your existing client-version telemetry) *before* deploying the Django OTP removal (step 2b's url/view removal). The flag-enable part of step 2b (turning on `KIS_AUTH_*`) is safe to do immediately — it only adds new endpoints, it doesn't remove old ones.
- If business requirements force simultaneous rollout, accept that any user on an old app build who hits "forgot password" or hasn't yet updated will get a hard failure until they update — this is a product decision to make explicitly, not a silent side effect.

## 4. Health checks

- `backend/kis`: `python manage.py check` passed; confirm `/api/v1/kis-auth/link/initiate/`, `/link/complete/`, `/registration/complete/`, `/recovery/complete/` return `404` (not `500`) when their respective flags are off, and `200`/expected codes when on — this is already exercised by `apps/kis_auth_bridge`'s test suite.
- `backend/kis-auth`: existing `/health` endpoint (confirm via its own `PRODUCTION_CHECKLIST.md`) and JWKS endpoint reachability — Django's JWKS client caches but will fail closed if kis-auth is unreachable at redemption time.
- `backend/Nestjs`: no change; its existing Django-introspection health dependency is unaffected.

## 5. Identity-assertion contract agreement (confirm before enabling in a new environment)

All three services must agree on:
- `KISAUTH_BASE_URL` / JWKS URL (Django config) matching the actual kis-auth deployment.
- `CLIENT_ID = "kis-django"` (hardcoded in `apps/kis_auth_bridge/views.py`) matching a registered OAuth client in kis-auth's `ClientService`.
- `KISAUTH_INTERNAL_HMAC_SECRET` identical on both sides of the Django↔kis-auth exchange channel.
- Redirect URIs registered in kis-auth matching exactly what the mobile app and/or Django's own callback expect.

This was not re-verified against the actual staging/production kis-auth deployment in this session — do that as part of any real environment rollout, since it requires access to deployed secrets this session does not have.

## 6. Rollback procedure

**Code rollback (Django)**: revert the commit(s) removing `apps/otp` and flipping the flags. If migration `0005_delete_phoneotp.py` was already applied, run `python manage.py migrate otp 0004_alter_phoneotp_purpose` first — Django can regenerate the `CreateModel` operation for the reverted model code automatically since the migration is a plain `DeleteModel`. Do this only in a maintenance window; it recreates an empty `PhoneOTP` table, it does not restore any OTP codes that existed before deletion (they were always short-lived and not meant to survive this).

**Code rollback (mobile)**: ship a new app store release reverting to the previous build, or rely on staged rollout halting — there is no server-side kill switch for already-downloaded app binaries. This is why the compatibility-window guidance in §3 matters: a rollback here is slow (app review/propagation), unlike a Django env var flip.

**Flag rollback (fast path, no redeploy)**: setting `KIS_AUTH_ENABLED=false` (or any of the four flags) via environment variable immediately 404s the corresponding endpoints again, without needing a code revert — useful if a production issue is isolated to the KIS Auth bridge specifically rather than requiring a full OTP rollback.

**kis-auth / Nest**: no changes made, nothing to roll back.

## 7. Legacy account handling

- Existing accounts that never link Google continue to work exactly as before for login (phone/password, unaffected) and now use `ParentRecoveryInitView`/`ParentRecoveryConfirmView` (pre-existing, not OTP-based) as their only recovery path, since OTP-based recovery is gone.
- No account's `email_verified` or linkage state was reset or backfilled by migration `0005` — it only removes the OTP table. Historical `email_verified=True` values set by the old OTP flow are preserved as-is (not re-validated), consistent with "do not blindly reset all existing users' verification states."
- Phone numbers, credentials, sessions, and all business data are untouched by every change in this migration.

## 8. Required environment variable changes

### `backend/kis` `.env` / deployment config
- `KIS_AUTH_ENABLED=true`, `KIS_AUTH_REGISTRATION_ENABLED=true`, `KIS_AUTH_RECOVERY_ENABLED=true`, `KIS_AUTH_LINK_ENABLED=true` (new defaults; can still be overridden per-environment during staged rollout per §2b/§3).
- Remove: `KIS_PHONE_VERIFICATION_ENABLED`, `SMS_CHANNEL_ENABLED`, `WHATSAPP_CHANNEL_ENABLED`, Infobip credentials, `OTP_DEBUG_LOG_CODES`, `OTP_OVERRIDE_*`.
- Keep: all `KISAUTH_*` vars (base URL, JWKS URL, internal HMAC secret), Resend credentials (still used for non-auth transactional email).

### `KIS` mobile `.env` / `.env.example`
- `KIS_AUTH_LINK_ENABLED=true`, `KIS_AUTH_REGISTRATION_ENABLED=true` (recovery was already `true`).
- Remove: `KIS_PHONE_VERIFICATION_ENABLED`.

No real secret values are included in this document or in any `.env.example` change — only variable names and booleans.
