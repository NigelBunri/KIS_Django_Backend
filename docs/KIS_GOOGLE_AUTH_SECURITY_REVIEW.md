# KIS Google Auth Migration — Security Review

Date: 2026-10-14

## 0. Post-ship finding: the redirect domain itself was broken (2026-10-14, same day)

During manual testing, a Google-link attempt completed on Google's side but the user was returned to a literal 404 instead of the app. Root cause: **`kis.app`, used for every KIS Auth mobile redirect URI (link/recovery/registration), is not owned by KIS** — its bare domain 302-redirects to a domain-parking marketplace listing, and it serves no `apple-app-site-association` file and nothing at any `/auth/*` path (confirmed directly via `curl`). This meant:
- Universal Links could never verify against it on any device, regardless of app configuration.
- `react-native-inappbrowser-reborn`'s `openAuth()` (`ASWebAuthenticationSession`/Custom Tabs) does **not** depend on Universal Link verification and should intercept the callback purely by URL-prefix match before any page loads — this is the primary, intended path and was working as designed.
- When that interception doesn't happen (native module unavailable in the test build, emulator, etc.), the code falls back to `Linking.openURL`, which depends entirely on Universal Links — and since those could never verify, the browser loaded the real, nonexistent page and 404'd.

**Fix**: moved KIS Auth's three redirect URIs to `go.kingdomimpactventures.org`, a subdomain KIS actually controls (same Cloudflare zone as `api.`/`chat.`). Added as a second associated domain/intent-filter on each platform, alongside the existing `kis.app` entries (not replacing — `kis.app` is still used for unrelated content-sharing deep links, e.g. broadcasts/channels/shorts, whose own ownership is a separate, unresolved issue out of scope here). `deepLinkRouter.ts` needed no changes — it matches on path only, never host.

Server-side (done directly against production, with the product owner's explicit authorization for each write):
- nginx now serves a real AASA file and a fallback page per auth purpose at `go.kingdomimpactventures.org`, each attempting an immediate `kis://` custom-scheme hand-off (no domain verification needed at all) with a manual link as backup.
- The existing shared `kingdomimpactventures.org` Let's Encrypt cert was expanded (via `certbot --expand`) to cover the new subdomain; verified no other domain on the cert broke.
- kis-auth's `kis-mobile` client row had the 3 new URIs **appended** to `allowed_redirect_uris` (old `kis.app` entries left in place, not removed, in case any already-shipped test build still references them).

**Known remaining gap**: `go.kingdomimpactventures.org/.well-known/assetlinks.json` was deployed with the correct Android package name (`com.kingdom.impact`) but a placeholder `sha256_cert_fingerprints` value — this needs the real value from Google Play Console → App integrity → App signing key certificate before Android App Links will actually verify. iOS is fully correct and complete (Team ID `K692889KRM`, bundle ID `com.kingdomimpactventures.kis`).

This finding does not change anything in §1–9 below — the trust model, assertion verification, and linking logic were never the problem; only the transport-layer redirect domain was.

## 1. Trust boundaries

```
[Google] --OIDC--> [kis-auth broker] --RS256 JWT, single-use, 60s TTL--> [Django]
                         ^                                                  |
                         | HMAC-signed exchange call                        | authoritative
                    [KIS mobile app]  <--- never talks to Google/kis-auth's |  account/session
                         |               identity endpoints directly ------>|  decisions
                    [backend/Nestjs] <--- HMAC-signed introspection only ---+
```

- **Google** is the identity provider. KIS never sees a Google access/refresh token; only kis-auth does, briefly, during the OAuth code exchange.
- **kis-auth** is the sole OAuth/OIDC client. It authenticates the user with Google, mints a short-lived (60s), single-use, RS256-signed JWT "identity assertion," and exposes a JWKS endpoint for independent verification. It never makes an authorization decision about a KIS account — only Django does that.
- **Django** redeems the assertion (signature, issuer, audience, expiry, single-use via the authorization-code consumption), then makes the sole authoritative decision about which KIS account is affected and what it's allowed to do. It issues/manages the KIS session (SimpleJWT) independently of the Google assertion's lifetime.
- **backend/Nestjs** (chat/calls/notifications) never talks to Google or kis-auth. It validates identity purely by introspecting Django's own session tokens over an HMAC-signed internal channel, so nothing about this migration changes its trust boundary.
- **KIS mobile app** never talks to Google directly and never performs its own OIDC verification. It launches kis-auth's `/authorize` endpoint in an in-app browser/universal link and only ever calls Django's `kis-auth/*` endpoints with the resulting single-use code.

## 2. Identity key: `provider_subject`, never email

Google's `sub` claim (surfaced to Django as `provider_subject`) is the only identity key used to link or recognize an account. Email is tracked only as a *property* of the identity (`provider_email`, `provider_email_verified`), never as a lookup/linking key. This means:
- A Google account changing its email address does not break or confuse an existing link — the `(provider, provider_subject)` pair is unchanged.
- Two Google accounts can never collide on email-based linking logic, because there is none; `auth_identity` enforces `UNIQUE(provider, provider_subject)` and `UNIQUE(kis_user_id)` at the database level in kis-auth.
- An unverified Google email can authenticate a *login/recovery* (proves control of the Google account) without that ever being treated as proof the associated email address is deliverable/owned — verification state is tracked separately (`provider_email_verified`).

## 3. Assertion lifecycle and replay prevention

| Property | Mechanism |
|---|---|
| Signature | RS256, verified against kis-auth's JWKS (key rotation supported via overlapping previous keys) |
| Issuer | `kisauth.kingdomimpactventures.org`, checked by Django's `PyJWKClient`-based verifier |
| Audience | `client_id` (`kis-django`), checked |
| Expiry | 60s default TTL on the assertion/authorization code |
| Single-use | Authorization codes and link tickets are consumed atomically from Redis (`SET NX` / atomic consume) — a second redemption attempt fails even within the TTL window |
| Purpose binding | Every assertion carries a `purpose` claim (`link` / `recovery` / `registration` / `enterprise_sso_registration`), re-checked server-side on every redemption — a `recovery`-purpose code cannot be replayed as a `link` |
| PKCE | S256 code challenge/verifier binds the authorization code to the browser session that started the flow |
| State/nonce | `state` revalidated at the OAuth callback; Google's `nonce` checked against the session nonce at ID-token verification |
| Redirect URI | Bound to the original `client_id`/`redirect_uri` at redemption, not just at issuance |
| Identity status | Re-checked as `active` at redemption time (not just issuance time) — a revoked identity can't be redeemed even with an otherwise-valid in-flight code |

## 4. Account-linking integrity

- **Account substitution**: `KisAuthLinkCompleteView` requires `verified.kis_user_id == request.user.id` — a link result minted for one session cannot be redeemed while authenticated as a different user, even with a technically-valid code (`apps/kis_auth_bridge/views.py`).
- **CSRF / session swap**: Linking is bound to a Django-minted, HMAC-signed, single-use `link_ticket` that encodes the authenticated session's user id *before* the browser round-trip to Google begins (`link_ticket.py`). An attacker who intercepts the OAuth callback cannot redirect the link onto their own account.
- **Race conditions**: Two concurrent registration attempts for the same Google identity cannot both succeed — `link_identity_server_to_server` enforces DB-level uniqueness on `(provider, provider_subject)`; a conflict rolls back the losing registration's account creation inside the same atomic transaction (`transaction.set_rollback(True)` on `ExchangeError`), so no orphaned or duplicate account is left behind.
- **Atomicity**: Account creation, Google identity linking, and device/token issuance happen inside one `transaction.atomic()` block in `KisAuthRegistrationCompleteView`. A failure at any step rolls back the whole operation — there is no state where an account exists without resolving the link attempt one way or the other.
- **Email-change safety**: `KisAuthLinkCompleteView` deliberately never writes to `user.email`/`user.email_verified` — linking an *existing* account's Google identity cannot silently change its contact email. The newly added `user.verification["google"]` block (see §5) is the only place a linked Google email is recorded, kept separate from the account's contact email. Covered by `test_link_does_not_touch_contact_email` in `apps/kis_auth_bridge/test_link_and_registration.py`.
- **Audit logging**: `AuditLog.log()` calls on every link/registration/recovery outcome record only `auth_identity_id` and the `provider_email_verified` boolean — never the JWT itself, the authorization code, or any Google token.

## 5. Email verification derivation — the fix made in this migration

**Finding**: Before this migration, Django's `UserSerializer` excluded only `password`/`is_superuser`/`user_permissions`/`groups`/`tier`/`status`/`is_active` from client writes — the generic `verification` JSONField (which now also carries Google-link state) was **not** read-only. Since `UserViewSet` is a full `ModelViewSet` with `IsSelfOrStaffForUserWrites` permitting self-PATCH, an authenticated user could have PATCHed their own `verification` field directly (e.g. `{"verification": {"google": {"linked": true}}}`), which — had Google-linked status lived there without this fix — would have let a client *claim* a Google link without ever redeeming a real identity assertion, directly violating the architecture's core invariant ("never grant privileges because the client claims linking succeeded").

**Fix**: `apps/accounts/serializers.py`'s `UserSerializer.Meta.read_only_fields` now includes `"verification"`. The only code paths that ever write to it are server-side, inside `apps/kis_auth_bridge/views.py`, after a KIS Auth JWT has already passed full signature/issuer/audience/expiry/purpose/single-use verification. `email_verified` itself was already correctly read-only and was already (pre-migration) only ever set from the verified `provider_email_verified` claim inside the atomic link/registration transaction — that part of the architecture required no change, only this adjacent closing of the `verification` JSONField write path.

Failed or cancelled Google auth never reaches the code path that sets either field — `email_verified`/`verification.google` are only touched inside the success branch of an atomically-committed transaction.

## 6. Recovery policy

- **Who can recover**: only accounts with a prior, Django-confirmed link between the KIS account and a Google `provider_subject` (established via the Link flow, or implicitly via Google-native registration). `KisAuthRecoveryCompleteView` redeems a `purpose=recovery` assertion and checks the redeemed `kis_user_id` maps to an existing, active account — it does not create a new account or link an arbitrary Google identity on the fly.
- **No account enumeration**: every failure branch (identity not found, identity not linked, exchange failure, wrong purpose) returns the same generic message and status code family. The mobile client (`ParentRecoveryScreen.tsx`) mirrors this — it shows uniform failure copy regardless of the real reason, including for the "no linked Google account" case, and only offers a support-contact path, never a hint about why recovery failed.
- **What recovery does NOT prove**: successfully authenticating with the linked Google account proves *control of that Google identity*. It is treated by Django exactly as equivalent in strength to the pre-existing `ParentRecoveryConfirmView` device-promotion flow it reuses (same device-trust/token-issuance code path) — it does not grant any capability beyond what that existing recovery mechanism already granted. No new, weaker recovery capability was introduced.
- **Rate limiting & auditing**: `KisAuthRegistrationCompleteView`/`KisAuthRecoveryCompleteView` share the existing `password_reset` throttle scope; every outcome is written to `AuditLog`.
- **Users with no linked Google identity**: `apps.accounts.ParentRecoveryInitView`/`ParentRecoveryConfirmView` (the pre-existing email-link device-recovery flow, distinct from the removed `apps/otp` OTP mechanism) remains the documented fallback — it was not removed, since spec §6 explicitly requires a safe alternative for unlinked legacy users rather than locking them out. This is **not** an OTP code; it is a signed, single-use recovery link skipping the OTP concept entirely, so removing OTP does not remove this path.

## 7. Linking policy decision (recorded per user instruction, 2026-10-14)

**Decision: Google linking is optional, strongly encouraged, never blocking.** New registrations are offered a skippable linking step immediately after account creation; existing accounts are offered the same flow from the profile screen. Skipping, failing, or being killed mid-flow always leaves the user with a working, logged-in, phone/password-authenticated account — never stuck on a modal with no way forward. This was an explicit product decision, not a default; the spec calls for this exact decision to be made and documented rather than silently defaulted.

**Trade-off accepted**: because linking is optional, a user who never links Google and needs password recovery has no self-serve Google-based path; they fall through to the documented `ParentRecoveryInitView`/`ParentRecoveryConfirmView` email-link fallback instead. This is the intended, documented behavior — not a gap.

## 8. Rollout-flag decision (recorded per user instruction, 2026-10-14)

**Decision: enable `KIS_AUTH_ENABLED`, `KIS_AUTH_REGISTRATION_ENABLED`, `KIS_AUTH_RECOVERY_ENABLED`, `KIS_AUTH_LINK_ENABLED` by default now**, since the product is moving to production and the explicit instruction was not to ship an incomplete/disabled feature. These flags were flipped in `config/settings/base.py` (still env-var overridable) and in `.env.example`/mobile `.env`/`.env.example`. They have **not** been flipped in any actual deployed environment (staging/production config) as part of this session — see the deployment runbook for the actual environment-by-environment rollout sequencing and the fact that this is a code-default change, not a deploy.

## 9. Residual risks / things intentionally not changed

1. **Device-revocation parity**: the OTP password-reset flow that was removed asserted "reset revokes every device." The Django implementation fork flagged this as worth independently re-verifying for the Google-recovery path (`KisAuthRecoveryCompleteView` reuses `ParentRecoveryConfirmView`'s device-promotion logic, which long predates this migration) — re-confirmed in this review: `ParentRecoveryConfirmView`'s transaction already revokes all other devices before issuing new tokens (same code path, unchanged), so parity holds. No action needed, documented here since the fork raised it as an open question.
2. **Website passwordless login** (`apps/accounts`'s `web_login` OTP purpose) is now gone, with no repo for the website in scope for this migration. The website falls back to phone+password login, which is unaffected. Flagged for whoever owns the website repo.
3. **kis-auth's own production secrets/config** were not touched or rotated by this session, per the instruction not to modify production authentication configuration without separate explicit authorization.
4. **No second Google implementation was introduced anywhere** — confirmed by audit (mobile uses the in-app-browser flow against kis-auth only; Django never talks to Google directly; kis-auth is the only OAuth client in the whole system).
