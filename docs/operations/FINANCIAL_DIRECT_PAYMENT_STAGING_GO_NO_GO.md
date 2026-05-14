# Financial Direct Payment Staging Go/No-Go

Current Phase 7 status: **NO-GO for production direct payment launch**.

The code path is ready for staging evidence, but this local environment does not have approved Flutterwave sandbox credentials, provider-console access, a public staging callback URL, or real device checkout evidence.

## Required Staging Environment

- `DJANGO_ENV=staging`
- `DEBUG=False`
- `API_BASE_URL=https://<staging-api-host>`
- `SITE_URL=https://<staging-api-host>`
- `FLW_PUBLIC_KEY` is configured in the staging secret manager.
- `FLW_SECRET_KEY` is configured in the staging secret manager.
- `FLW_WEBHOOK_SECRET` matches the Flutterwave dashboard webhook secret.
- `FLW_REDIRECT_URL` points to the staging mobile/web payment completion route.
- `KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED=True` in staging only.
- `KIS_LEGACY_WALLET_DEPOSIT_ENABLED=False`
- `KIS_LEGACY_WALLET_TRANSFER_ENABLED=False`
- `KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED=False`
- `KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED=False`
- `KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED=False`

## Provider Dashboard Evidence

Flutterwave sandbox dashboard must show:

- Webhook URL: `https://<staging-api-host>/api/v1/direct-payments/webhook/flutterwave/`
- Redirect URL: same value as staging `FLW_REDIRECT_URL`.
- Webhook secret configured and stored only in the staging secret manager.
- Sandbox mode enabled.
- Screenshots or release-ticket links attached without exposing keys or full secret values.

## Readiness Command

Run this in staging after applying migrations:

```bash
python3 manage.py migrate
python3 manage.py direct_payment_staging_check --json
```

The command prints booleans only. It must not print secret values.

Go/no-go rule:

- `ready_for_staging_provider_link_qa` must be `true`.
- All legacy wallet/KISC flags must be disabled.
- Direct provider links must be enabled only in staging for this QA pass.

## Staging Payment Evidence Matrix

| Flow | Required evidence | Status | Owner |
| --- | --- | --- | --- |
| Marketplace order | Creates `DirectPaymentIntent`, returns `payment_url`, opens Flutterwave checkout, successful callback marks metadata `payment_status=paid` | Evidence needed in staging | TODO_QA_OWNER |
| Service booking deposit/remaining payment | Creates `DirectPaymentIntent`, returns `payment_url`, successful callback marks `ServiceBookingPayment.payment_status=paid` | Evidence needed in staging | TODO_QA_OWNER |
| Education booking | Creates `DirectPaymentIntent`, opens checkout, successful callback changes booking from `payment_pending` to `confirmed` | Evidence needed in staging | TODO_QA_OWNER |
| Health billing | Creates `DirectPaymentIntent`, opens checkout, successful callback changes billing session to `paid` without local fake completion | Evidence needed in staging | TODO_QA_OWNER |
| Failed callback | Provider failed callback updates intent/audit and leaves the app in a safe failed/pending state | Evidence needed in staging | TODO_QA_OWNER |
| Cancelled callback | Provider cancelled callback updates intent/audit and does not mark target paid | Evidence needed in staging | TODO_QA_OWNER |
| Duplicate callback | Replaying successful callback is idempotent and creates duplicate audit evidence without double settlement | Evidence needed in staging | TODO_QA_OWNER |
| Unmatched callback | Unknown `tx_ref` is rejected and audit event is visible | Evidence needed in staging | TODO_QA_OWNER |
| Invalid signature | Bad `verif-hash` is rejected with no target mutation | Evidence needed in staging | TODO_SECURITY_OWNER |
| React Native handoff | Real device opens provider checkout from marketplace, service booking, education, and health screens | Evidence needed in staging | TODO_MOBILE_QA_OWNER |
| React Native return/refresh | After returning to KIS, refresh/status polling shows paid/failed/cancelled correctly | Evidence needed in staging | TODO_MOBILE_QA_OWNER |

## Manual Callback Replay Notes

Use Flutterwave sandbox webhooks when possible. If manual replay is required, send only staging test references and use the staging webhook secret.

Expected endpoint:

```text
POST https://<staging-api-host>/api/v1/direct-payments/webhook/flutterwave/
Header: verif-hash: <staging-webhook-secret>
Body: {"data":{"tx_ref":"<staging-direct-payment-tx-ref>","status":"successful","id":"<provider-transaction-id>"}}
```

Do not paste real secrets into docs, screenshots, tickets, or chat.

## Rollback

If staging payment links or callbacks behave incorrectly:

1. Set `KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED=False`.
2. Keep all legacy wallet/KISC checkout flags disabled.
3. Leave existing `DirectPaymentIntent` and `DirectPaymentAuditEvent` rows intact for investigation.
4. Remove or disable the Flutterwave dashboard webhook URL if repeated bad callbacks occur.
5. Re-run `python3 manage.py direct_payment_staging_check --json`.
6. Attach audit rows and non-secret provider evidence to the release ticket.

## Phase 7 Local Evidence

- Local code supports staging readiness checks.
- Local docs now define required staging evidence.
- No real Flutterwave sandbox payment was executed locally.
- No real React Native device checkout evidence was captured locally.
- Production launch remains blocked until the staging evidence matrix is complete.
