# Hosting your own KIS Bot Platform bot

You can run your bot's code **anywhere** — your own VM, a serverless function, a Raspberry Pi on your desk during development. KIS-managed hosting is never required, and there is no KIS-provided runtime your bot must use. All KIS needs from your bot is the ability to:

1. Call the KIS API over HTTPS (outbound).
2. Receive webhook deliveries over HTTPS (inbound) — if your bot reacts to events in real time rather than polling.

## Network requirements

- **Outbound HTTPS** to `https://api.kingdomimpactventures.org` — token requests, resource discovery, send-message, execute-action.
- **Inbound HTTPS, publicly reachable** — only if you configure a webhook. The URL you give KIS (`PUT /api/v1/bots/{bot_id}/webhook/`) must resolve to a real, public, non-private address — KIS validates this and will refuse to deliver to a loopback/private/link-local address (the same SSRF guard KIS uses for its own partner webhooks). A bot that only polls (e.g. periodically calling `GET /api/v1/bots/external/resources/` and deciding what to do) never needs an inbound listener at all, but then it can't react to `message.created`/`member.joined`/etc in real time — webhooks are the only real-time delivery mechanism.
- A valid TLS certificate on your webhook endpoint. KIS does not deliver to plain HTTP.

## Credential security

- Store `client_secret` the same way you'd store a database password: an environment variable or a secrets manager, never in source control, never in client-side code, never logged.
- The access token obtained from `/api/v1/bots/oauth/token/` is short-lived (default 1 hour) — don't persist it long-term; request a fresh one when it expires or when a call returns `401`.
- If you ever suspect your `client_secret` has leaked, rotate it immediately (`POST /api/v1/bots/{bot_id}/credential/`) — rotation invalidates the old secret instantly, it is not a grace-period swap.

## Webhook security

- **Always verify `X-KIS-Signature`** before processing a delivery's payload — see the worked examples in `docs/bot_platform_examples/`. An unverified webhook endpoint will process forged requests from anyone who finds your URL.
- **Reject stale timestamps.** `X-KIS-Timestamp` should be within a few minutes of your server's clock; anything older is either a replay or a client clock badly out of sync, and should be rejected either way.
- **Respond fast, process slow.** Acknowledge with `2xx` as soon as you've durably queued the event (write it to a database/queue), then do the actual work asynchronously. A webhook handler that does slow synchronous work before responding risks timing out and triggering an unnecessary retry.
- **De-duplicate on `event_id`.** KIS retries undelivered/failed webhooks with backoff; your endpoint must tolerate seeing the same `event_id` more than once without double-processing it.

## Recommended deployment practices

- Run your webhook receiver behind the same kind of reverse proxy/TLS termination you'd use for any production HTTPS service — KIS has no special requirements beyond "valid TLS, publicly reachable."
- Keep your bot's `client_secret` and any AI provider key (if you've opted into AI — see §11 of the API reference) in the same secrets-management system, with the same access controls, as any other production credential.
- If you run multiple instances of your bot for availability, make sure whichever instance receives a given webhook delivery can de-duplicate against a **shared** store (database, Redis, etc.) keyed on `event_id` — an in-memory-only dedupe set won't catch a retry that lands on a different instance.

## Logging and monitoring

- Log `event_id`, `event`, and your own processing outcome for every webhook delivery — this is what lets you reconcile against the delivery history visible to the bot owner in the Bot Platform dashboard (`GET /api/v1/bots/{bot_id}/webhook/deliveries/`) if something looks wrong.
- **Never log `client_secret`, access tokens, or AI provider API keys** — redact them from request/response logs explicitly if your HTTP client logs headers by default.
- Monitor your own `401`/`403`/`429` rate from the KIS API — a sudden spike usually means a revoked/expired credential or a scope your bot no longer has, not a KIS-side outage.
