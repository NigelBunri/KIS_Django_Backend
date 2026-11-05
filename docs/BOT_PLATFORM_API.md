# KIS Bot Platform — Developer API Reference

This document describes the **actual implemented** external API for the KIS Bot Platform (`apps/bots`). Every endpoint, field, and status code below is taken directly from the live route definitions and serializers in this repository — nothing here is aspirational.

The Bot Platform lets you host a bot **anywhere you like** — your own server, a serverless function, a laptop during development — and connect it to KIS resources your KIS account already has access to (Groups, Channels, Communities, Partners, Direct Conversations, My Feed). KIS never requires you to host your bot on KIS infrastructure, and AI is entirely optional: a bot can be purely rule-based and never touch an AI provider at all.

Base URL: `https://api.kingdomimpactventures.org`

---

## 1. Authentication

The external bot API uses the standard **OAuth2 client-credentials** grant. You never authenticate as a KIS user — you authenticate as *your bot*, using a `client_id`/`client_secret` pair issued from the Bot Platform dashboard (`PUT /api/v1/bots/{bot_id}/credential/`, which returns the secret **exactly once** — it is never retrievable again after that response).

### Obtaining an access token

```
POST /api/v1/bots/oauth/token/
Content-Type: application/json

{
  "grant_type": "client_credentials",
  "client_id": "bot_c1a2...",
  "client_secret": "sk_bot_..."
}
```

**Response `200`:**
```json
{
  "access_token": "<signed token>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "send_messages read_members"
}
```

`expires_in` is seconds (default 3600 / 1 hour). There is no refresh token — when the token expires, request a new one the same way. Repeated failed attempts with a wrong secret are rate-limited and will eventually lock out further attempts for a cooldown window; a lockout clears automatically on the next successful authentication.

Every subsequent request carries this token as:
```
Authorization: Bearer <access_token>
```

---

## 2. Bot identity and resource discovery

### Who am I?
```
GET /api/v1/bots/external/me/
Authorization: Bearer <token>
```
```json
{
  "id": "…", "name": "…", "handle": "…", "status": "active",
  "scopes": ["send_messages", "read_members"]
}
```

### What resources can I act on?
```
GET /api/v1/bots/external/resources/
Authorization: Bearer <token>
```
```json
[
  {
    "grant_id": "…",
    "resource_type": "group",
    "resource_id": "…",
    "scopes": ["send_messages", "read_members"]
  }
]
```

`resource_type` is one of: `direct_conversation`, `group`, `channel`, `community`, `partner`, `feed_profile`.

**Important:** being a member of a Community or Partner does **not** by itself grant your bot access to every Group/Channel underneath it. Each resource your bot can touch has its own explicit grant, created by the bot owner through the Bot Platform dashboard — `GET /api/v1/bots/external/resources/` is the authoritative list of exactly what your bot can see and do, nothing more.

### Permission scopes
| Scope | Meaning |
|---|---|
| `read_messages` | Receive message-related events |
| `send_messages` | Send a message into a connected conversation-backed resource |
| `read_members` | Receive membership events, read member info |
| `moderate` | Ban/remove/mute members (sensitive — see §6) |
| `publish_content` | Publish feed/channel content |
| `manage_resource` | Manage resource-level settings |

A grant's scopes are a **ceiling**, not a promise — every call is re-checked against the grant's *current* state at request time, not just at grant-creation time. If the bot owner revokes a scope, or the owner's own account loses access to the underlying resource, the very next call fails immediately — nothing is cached past the moment of the request.

---

## 3. Sending a message

```
POST /api/v1/bots/external/resources/{grant_id}/send-message/
Authorization: Bearer <token>
Content-Type: application/json

{ "text": "Hello from my bot!" }
```

Requires the `send_messages` scope on that grant, and the underlying resource must actually have a backing conversation — **Channels and My Feed grants do not**, since they're broadcast/content resources, not chat resources. Calling this against a Channel or Feed grant returns `400` with `{"resource": "This resource has no linked conversation to send into."}` — this is expected and permanent for those two resource types, not a transient error.

**Response `201`:**
```json
{ "ok": true, "...": "provider-specific delivery metadata" }
```

---

## 4. Executing an action

```
POST /api/v1/bots/external/resources/{grant_id}/execute-action/
Authorization: Bearer <token>
Content-Type: application/json

{ "action": "ban_member", "details": { "user_id": "…", "reason": "spam" } }
```

### Supported actions
| Action | Supported resource types | Sensitive (requires owner approval)? |
|---|---|---|
| `ban_member` | Group | Yes |
| `unban_member` | Group, Community | No (corrective) |
| `remove_member` | Group, Community | Yes |
| `mute_member` | Partner only | Yes |
| `unmute_member` | Partner only | No (corrective) |

Calling an action against an unsupported resource type returns a clear `400` error naming exactly which resource types it does support — nothing here is silently ignored.

### Two possible responses

**Immediate execution (`200`):**
```json
{ "status": "executed", "result": { "user_id": "…" } }
```

**Queued for owner approval (`202`):**
```json
{ "status": "pending_approval", "approval_id": "…" }
```
Sensitive actions never execute immediately, regardless of your bot's scopes — the bot owner (or, for a resource grant where the owner pre-approved this exact action type, automatically) must approve it first. Poll is not required; there is currently no webhook event for "approval decided" — if you need to know the outcome, the bot owner can see it in the dashboard, or you can have the owner grant `auto_approved_actions` for that specific action up front.

Every call here — regardless of outcome — is re-validated against the grant's live state, the bot's daily rate limit (tier-dependent), and the action's required scope, in that order, before anything executes.

---

## 5. Event subscriptions and webhooks

You do not "subscribe" to individual events one at a time — you configure **one webhook endpoint** per bot, and declare which event names you want delivered to it.

### Configuring your webhook
```
PUT /api/v1/bots/{bot_id}/webhook/
Authorization: Bearer <KIS user session token - this call is made by the BOT OWNER, not the bot itself>
Content-Type: application/json

{ "url": "https://your-server.example.com/kis-webhook", "events": ["message.created", "member.joined"] }
```
The response includes `secret` **only the first time** the subscription is created (or right after `POST /api/v1/bots/{bot_id}/webhook/rotate-secret/`) — store it immediately; it is never shown again.

### Event names
`message.created`, `message.edited`, `member.joined`, `member.left`, `post.created`, `comment.created`, `schedule` (internal — scheduled workflows only, never delivered as a webhook).

### Receiving and verifying a delivery

Every delivery is a `POST` to your configured URL with headers:
```
X-KIS-Bot-Event: message.created
X-KIS-Bot-Id: <your bot's id>
X-KIS-Timestamp: <unix seconds>
X-KIS-Signature: <hex HMAC-SHA256>
```
and a JSON body:
```json
{ "event": "message.created", "event_id": "...", "bot_id": "...", "payload": { ... }, "delivered_at": "..." }
```

**Verify the signature** by recomputing HMAC-SHA256 over the string `"{X-KIS-Timestamp}." + <raw request body bytes>` using your webhook secret, and comparing it (constant-time) to `X-KIS-Signature`. **Reject the request if `X-KIS-Timestamp` is more than a few minutes old or in the future** — this is what makes a captured request unreplayable after the window closes; signing the timestamp into the payload is the entire point, don't skip this check. See the worked examples in `docs/bot_platform_examples/` for exact code in three languages.

### Idempotency and retries

`event_id` is your idempotency key. KIS itself de-duplicates on `(subscription, event_id)` — the same occurrence is never created as two delivery rows — but KIS **will retry a delivery your endpoint didn't acknowledge** (exponential backoff, up to the subscription's `retry_limit`, default 5, after which it becomes `dead_letter` and stops auto-retrying). This means **your endpoint may receive the same `event_id` more than once** across retries. Always:
1. Check whether you've already processed this exact `event_id` before doing anything with side effects.
2. Return `200` (any `2xx`) as soon as you've durably recorded the event — do the slow work asynchronously afterward, not before responding. KIS treats anything outside `200`–`299` as a failure and will retry.

---

## 6. Approval behaviour

See §4. The short version: KIS enforces a hard boundary between "your bot asked to do something" and "that thing actually happened." A sensitive action is never executed by an API call alone — it is only ever executed once a human (the bot owner) has approved it, or the owner pre-authorized that exact action type in advance on that specific grant. There is no way to bypass this from the external API; it is enforced entirely server-side regardless of what your request contains.

---

## 7. Rate limits

- **Token endpoint**: throttled per client to prevent credential brute-forcing; repeated failures lock out further attempts for a cooldown window.
- **Actions/events**: each bot has a daily action/event limit determined by its owner's KIS account tier (Business tier and above only — this is where bot ownership eligibility starts). Exceeding it returns `429` and, if abuse-like behavior continues, can auto-suspend the bot (see §9).

---

## 8. Error responses

Standard DRF error shapes:
- `400` — validation error, body shaped `{"field_name": "message"}` or `{"detail": "message"}`.
- `401` — missing/invalid/expired access token.
- `403` — the token is valid but the requested action isn't permitted (e.g. missing scope).
- `404` — the grant/bot doesn't exist or doesn't belong to this credential.
- `429` — rate limited.

---

## 9. Credential rotation and revocation

- **Rotate**: `POST /api/v1/bots/{bot_id}/credential/` while an active credential already exists issues a new secret and immediately invalidates the old one — not a grace period. Mirrors the webhook `rotate-secret` behavior.
- **Revoke**: `DELETE /api/v1/bots/{bot_id}/credential/` immediately deactivates it. Every subsequent request with the old credential fails `401` from that point forward — revocation is checked at authentication time, not cached.
- A KIS superadmin can also revoke your credential platform-wide if abuse is detected — same immediate effect.

---

## 10. Security requirements for your own bot

See `docs/BOT_PLATFORM_EXTERNAL_HOSTING.md` for the full checklist (network, logging, deployment practices). The two non-negotiables:
1. **Never log your `client_secret` or access tokens.** Treat them exactly like a database password.
2. **Always verify the webhook signature** before trusting a delivery's contents — anyone who knows your webhook URL can `POST` to it; the signature is what proves it actually came from KIS.

---

## 11. Optional AI — bring your own key

The Bot Platform's AI features (owner-configured per bot, never required) let your bot's workflow ask an AI model to choose among the **workflow's own pre-approved actions** — the AI can never invent a new action or bypass anything described above. You connect your own Anthropic/OpenAI/Gemini/compatible-custom API key through the Bot Platform dashboard (`PUT /api/v1/bots/{bot_id}/ai-connection/`); KIS never sees it in plaintext after that call, never logs it, and never pays for your usage — billing is between you and your chosen provider. See the AI example in `docs/bot_platform_examples/` for how this looks from the owner-configuration side (there is nothing for an *external bot* to do here — AI decisions happen inside KIS's own workflow engine, not in your externally-hosted code).
