# Monetization Legal Safety Runbook

Status: Phase 24 foundation.

This runbook protects KIS from treating promotional credits as money. It is not legal advice; counsel/product approval is still required before production launch.

## Non-Negotiable Policy

- KIS public currency is USD.
- Paid flows use direct payment providers, currently Flutterwave first.
- KIS promotional credits are gifts/rewards for approved platform activity.
- Promotional credits are not cash.
- Promotional credits are not transferable between users.
- Promotional credits are not withdrawable.
- Promotional credits are not exchange-rated against USD or any fiat currency.
- Historical wallet, ledger, billing, and receipt records remain readable for audit/history only.

## Forbidden Product Copy

Do not publish copy that says or implies:

- `KISC to USD`
- `KIS Coin exchange rate`
- `Buy KIS Coins`
- `Sell KIS Coins`
- `Withdraw credits`
- `Cash out wallet balance`
- `Convert credits to cash`
- `Transfer credits to another user`
- `Wallet deposit`
- `Wallet top-up`

Use this approved framing instead:

- `Pay in USD through Flutterwave or another approved payment provider.`
- `KIS promotional credits are gift/reward credits.`
- `Promotional credits may subsidize eligible platform benefits where approved.`
- `Promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.`

## Required Production Flags

These must remain disabled:

- `KIS_LEGACY_WALLET_DEPOSIT_ENABLED=False`
- `KIS_LEGACY_WALLET_TRANSFER_ENABLED=False`
- `KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED=False`
- `KIS_LEGACY_PROMO_CASH_BONUS_ENABLED=False`
- `KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED=False`
- `KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED=False`
- `KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED=False`

These should be configured only in staging/production with approved provider credentials:

- `KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED`
- `FLW_PUBLIC_KEY`
- `FLW_SECRET_KEY`
- `FLW_WEBHOOK_SECRET`
- `FLW_REDIRECT_URL`

Never print or paste secret values into logs, docs, tickets, or screenshots.

## Allowed Monetization Surfaces

- Account upgrades and subscriptions: USD/direct-provider first.
- Marketplace products and services: USD/direct-provider first.
- Education courses, enrollments, certificates, and services: USD/direct-provider first.
- Health appointments, sessions, and billing: USD/direct-provider first.
- Partner memberships, events, and services: USD/direct-provider first where implemented.
- Channel/creator monetization: USD/direct-provider first after creator compliance review.
- Ads/sponsorships: USD/direct-provider first, Christian-safety reviewed, no child-targeted ads.

## Launch Checks

Run these before release:

```bash
python3 manage.py verify_deployment_security --target-production
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
```

Confirm:

- `/api/v1/core/monetization/safety-summary/` reports no critical failures.
- `/api/v1/core/admin/security-launch-gate/` reports no critical failures for staff.
- Flutterwave staging callback evidence is attached to the release ticket.
- Copy scan has no forbidden money wording in public screens, serializers, templates, and translations.
- Historical wallet views are clearly read-only and do not offer top-up, transfer, withdrawal, or cash conversion.

## Incident Rollback

If unsafe monetization copy or behavior reaches production:

1. Disable payment link creation if needed with `KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED=False`.
2. Confirm all legacy wallet money flags remain `False`.
3. Remove unsafe public copy from app, API serializer, template, or translation.
4. Attach incident notes to the release ticket.
5. Ask counsel/product owner to approve the corrected copy before redeploy.
