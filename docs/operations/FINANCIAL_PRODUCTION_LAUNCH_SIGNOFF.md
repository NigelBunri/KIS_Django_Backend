# Financial Production Launch Sign-Off

Current Phase 8 status: **NO-GO for production financial launch** until the evidence and approval items below are complete.

This document is a production sign-off checklist for the KIS financial redesign. It is not legal advice. Counsel and product leadership must approve the final treatment of historical balances before public launch.

## Product Policy

- KIS Coins are promotional/gift/reward credits only.
- KIS Coins cannot be bought, sold, transferred peer-to-peer, withdrawn, redeemed for cash, converted to cash, or marketed as stored value.
- KIS Coins cannot be represented as an investment, currency, deposit, cash balance, exchange-rate product, or money substitute.
- New commerce, education, and health paid workflows must use USD with Flutterwave or another configured direct payment provider.
- Promotional credits may only subsidize eligible KIS account upgrades or narrowly approved platform fees.
- Historical KISC records may remain readable for receipts, audits, support, and migration compatibility, but public copy must describe them as historical promotional-credit records.

## Required Evidence Before Production

| Evidence | Required proof | Status |
| --- | --- | --- |
| Staging Flutterwave payment links | Marketplace order, service booking, education booking, and health billing each create a `DirectPaymentIntent` and valid `payment_url` | Blocked: staging credentials/provider dashboard evidence not attached |
| Signed callbacks | Successful, failed, cancelled, duplicate, unmatched, and invalid-signature callbacks validated in staging | Blocked: callback replay evidence not attached |
| React Native handoff | Real device or staging build opens checkout, returns to app, and refreshes status safely | Blocked: device QA evidence not attached |
| Provider dashboard | Flutterwave sandbox webhook URL, redirect URL, and sandbox mode evidence captured without secret exposure | Blocked: provider-console evidence not attached |
| Direct payment audits | `DirectPaymentAuditEvent` rows visible for each staging callback scenario | Blocked: staging audit evidence not attached |
| Legacy flags | Production/staging env confirms all legacy wallet/KISC buy, transfer, conversion, and checkout flags are disabled | Pending provider launch review |
| Legal/product approval | Written approval for historical KISC/wallet balance treatment | Blocked: counsel/product decision needed |

## Historical Balance Treatment Options

Counsel and product should choose one or more options before production launch:

1. **Freeze pending review**: keep historical balances read-only and unusable until legal/product approval is complete.
2. **Convert eligible grants to promotional credits**: convert clearly promotional/admin-granted balances into non-transferable credits usable only for eligible KIS account upgrades or approved platform fees.
3. **Refund eligible cash-funded balances**: if any balances came from user cash top-ups, review whether refunding through a compliant provider/manual process is required.
4. **Manual case review**: route disputed, mixed-source, suspicious, or high-value balances to an internal review queue before any treatment.
5. **Expire promotional grants**: expire only if the original terms and applicable law allow it, with notice where required.

No option should re-enable public KIS Coin purchase, transfer, withdrawal, or cash conversion.

## Production Environment Gate

Required production defaults:

```text
KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED=False until production go-live is approved
KIS_LEGACY_WALLET_DEPOSIT_ENABLED=False
KIS_LEGACY_WALLET_TRANSFER_ENABLED=False
KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED=False
KIS_LEGACY_WALLET_UPGRADE_ENABLED=False
KIS_LEGACY_PROMO_CASH_BONUS_ENABLED=False
KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED=False
KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED=False
KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED=False
```

`KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED=True` may be used only after staging evidence is complete and production approval is recorded.

## Rollback Checklist

If payment incidents occur:

1. Set `KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED=False`.
2. Keep all legacy wallet/KISC flags disabled.
3. Disable or pause the Flutterwave webhook in the provider dashboard if bad callback traffic continues.
4. Preserve `DirectPaymentIntent` and `DirectPaymentAuditEvent` rows for investigation.
5. Stop any worker/task that is repeatedly mutating paid state incorrectly.
6. Re-run `python3 manage.py direct_payment_staging_check --json` or the equivalent production-safe readiness check.
7. Compare provider dashboard transactions to internal `DirectPaymentIntent` rows before manually changing order/booking/session state.
8. Record the incident timeline, affected references, provider transaction ids, and final remediation in the release/incident ticket.

## Monitoring Checklist

Monitor and alert on:

- Direct payment `payment_url` generation failures.
- Flutterwave webhook 4xx/5xx rates.
- Invalid `verif-hash` callback attempts.
- Unmatched or missing `tx_ref` callbacks.
- Duplicate callback volume.
- Pending direct-payment intents older than the product-defined payment window.
- Failed target mutations after a successful provider callback.
- Commerce, education, or health records marked paid without a direct payment audit event.
- Mobile checkout open failures and post-return refresh failures.
- Any request attempting disabled legacy wallet deposit, transfer, conversion, or wallet checkout endpoints.

## Current Decision

Production financial launch is **NO-GO**.

The codebase now defaults away from KIS Coin money behavior, but production launch still requires:

- completed Phase 7 staging evidence;
- legal/product approval for historical balances;
- production secret-manager verification;
- provider dashboard proof;
- real-device React Native checkout/return QA.
