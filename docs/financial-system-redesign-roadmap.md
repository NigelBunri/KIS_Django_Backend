# KIS Financial System Redesign Roadmap

Last updated: 2026-05-07

## Phase 0 Status - Completed Trace And Redesign Plan

This roadmap exists because the current financial system can create legal and compliance risk if KIS Coins continue to behave like money, stored value, transferable value, or a substitute for USD. This is not legal advice. Before launch, counsel should review the final design in every launch country.

## Target Financial Model

The safer target model is:

- USD is the real-money unit for prices, checkout, subscriptions, invoices, receipts, refunds, disputes, and provider settlement.
- Flutterwave is the primary direct-payment rail for USD payments where supported.
- KIS Coins become non-cash promotional credits/gift credits only.
- KIS Coins are earned from platform-defined actions, campaigns, goodwill grants, or admin promotional adjustments.
- KIS Coins cannot be bought directly with cash.
- KIS Coins cannot be withdrawn, redeemed for cash, sold, transferred peer-to-peer, or represented as an investment.
- KIS Coins can only subsidize KIS account upgrades or selected platform fees.
- KIS Coins should be described as promotional credits, gift credits, rewards, or discount credits, not as currency, wallet money, stored cash, or an investment.

## Regulatory Design Rationale

The safest product direction is to avoid making KIS Coins look like convertible virtual currency, open-loop stored value, or investment-like assets.

Relevant public guidance:

- FinCEN guidance distinguishes virtual currency from real currency and describes convertible virtual currency as having equivalent real-currency value or acting as a substitute for real currency. It also notes that selling units of convertible virtual currency for real currency can create money-transmission risk.
- FinCEN prepaid-access guidance treats closed-loop prepaid access differently when it is limited to goods/services at defined merchants or locations and subject to limits.
- CFPB prepaid materials distinguish open-loop prepaid cards from closed-loop cards and explain that prepaid-account rules include consumer-protection requirements.
- SEC guidance applies the Howey investment-contract analysis to crypto asset transactions when money is invested with an expectation of profit from others' efforts.

Product implication: KIS Coins should not be marketed as money, not have a USD exchange promise, not be bought/sold, not be transferable, and not be redeemable outside narrow platform subsidy use.

Sources:

- FinCEN virtual currency guidance: https://www.fincen.gov/resources/statutes-regulations/guidance/application-fincens-regulations-persons-administering
- FinCEN prepaid access FAQ: https://www.fincen.gov/resources/statutes-regulations/guidance/frequently-asked-questions-regarding-prepaid-access
- CFPB prepaid card overview: https://www.consumerfinance.gov/consumer-tools/prepaid-cards/choose-the-right-card/
- SEC crypto asset transactions guidance: https://www.sec.gov/resources-small-businesses/capital-raising-building-blocks/transactions-involving-crypto-assets

## Current Backend Trace

### Django Billing

Files:

- `apps/billing/models.py`
- `apps/billing/services.py`
- `apps/billing/views.py`
- `apps/billing/serializers.py`
- `apps/billing/urls.py`
- `apps/billing/documents.py`
- `apps/billing/admin.py`
- `apps/billing/tests.py`
- `apps/billing/signals.py`
- `apps/billing/migrations/*`

Current behavior:

- `WalletAccount` stores `balance_cents`, `locked_cents`, `currency=USD`, and metadata.
- `CreditAccount` stores `credits` and `locked_credits`.
- `WalletLedgerEntry` records deposits, conversions, transfers, tier upgrades, promos, purchases, payouts, refunds, and admin adjustments.
- `WalletTransaction` stores Flutterwave/internal transactions, payment URLs, provider references, raw payloads, and status.
- `PromoCode` can grant cash bonus cents and credit bonuses.
- Wallet endpoints expose:
  - wallet status;
  - ledger;
  - transactions;
  - subscription details;
  - billing history;
  - subscription cancel/resume/downgrade;
  - transaction retry;
  - deposit/top-up;
  - transfer;
  - upgrade;
  - promo redemption.
- Flutterwave webhook credits deposits or upgrades after successful provider callback.
- Finance admin endpoints manage reconciliation, insurance claims, payment disputes, pricing insights, and admin wallet adjustment.

High-risk areas:

- Cash wallet top-up / deposit creates stored user value.
- Cash-to-credit and credit-to-cash conversion are high risk.
- Peer-to-peer wallet and credit transfer are high risk.
- KIS Coin labels imply a currency-like product.
- Admin cash adjustments and promo cash bonuses can create stored value.
- Wallet balance can pay account upgrades directly.
- Raw Flutterwave payload storage should remain redacted/reviewed before production.

### Django Money Conversion

Files:

- `apps/core/money.py`

Current behavior:

- Defines `KISC_TO_USD_RATE = 100`.
- Converts frontend KISC major units to USD cents.
- Parses money strings containing KISC, USD, and currency symbols.

High-risk areas:

- Hard-coded KISC-to-USD exchange rate.
- Naming and conversion design make KIS Coins look convertible into real value.

### Django Commerce And Marketplace

Files:

- `apps/commerce/models.py`
- `apps/commerce/services.py`
- `apps/commerce/views.py`
- `apps/commerce/serializers.py`
- `apps/commerce/documents.py`
- `apps/commerce/tasks.py`
- `apps/commerce/constants.py`
- `apps/commerce/tests.py`
- `apps/commerce/migrations/*`

Current behavior:

- Products, services, orders, service bookings, booking payments, escrow, complaints, marketplace orders, and receipts use KISC or wallet-backed pricing in multiple paths.
- Service booking deposits lock wallet funds.
- Booking completion releases locked funds to providers.
- Booking cancellation/refund returns locked funds.
- Marketplace orders lock wallet funds, then release/refund based on order lifecycle.
- Documents and receipts still use KISC wording in marketplace and booking flows.

High-risk areas:

- Marketplace goods/services paid with KISC wallet balance.
- KISC used as checkout/settlement currency.
- Escrow and provider payout behavior can look like money transmission if wallet value is user-funded or transferable.
- Receipts can describe KISC payments as financial settlement.

### Django Broadcast / Education / Health Surfaces

Files:

- `apps/broadcasts/models.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/tasks.py`
- `apps/broadcasts/tests.py`
- `apps/health_ops/models.py`
- `apps/health_ops/views.py`
- `apps/health_ops/serializers.py`
- `apps/health_ops/services.py`
- `apps/health_ops/tests/*`

Current behavior:

- Education broadcasts and bookings use `price_amount`, `price_currency`, wallet transactions, and KISC wallet payment language.
- Education booking payment requires wallet/KISC methods.
- Broadcast market sections expose wallet balance and KISC/price metadata.
- Health payment billing sessions currently accept only `kis_wallet`.
- Health billing converts KISC/micro amounts and debits wallet balance.

High-risk areas:

- Education and health paid services should move to USD + Flutterwave direct payment.
- Health billing should not rely on KIS wallet as payment provider.
- Education pricing should be USD-first, with KIS Coins only as subsidy toward eligible platform fees if approved.

### Django Accounts / Tiers / Subscriptions

Files:

- `apps/accounts/models.py`
- `apps/accounts/serializers.py`
- `apps/accounts/views.py`
- `apps/accounts/tier_presets.py`
- `apps/accounts/tiers.py`
- `apps/tiers/models.py`
- `apps/tiers/views.py`
- `apps/tiers/serializers.py`
- `apps/tiers/tasks.py`
- `apps/tiers/tests.py`

Current behavior:

- `AccountTier.price_cents` defines account upgrade prices.
- `Subscription` tracks active tiers, cancellation, downgrade, pending tier, and billing metadata.
- `apps.billing` currently performs account upgrades using credits, wallet balance, mock, or Flutterwave.
- `apps.tiers` has a separate billing-plan/subscription/invoice model family and should be reconciled with `apps.accounts`/`apps.billing` before launch.

Target behavior:

- Account tiers remain priced in USD.
- Flutterwave handles paid upgrade checkout.
- Promotional KIS Coins can subsidize an account upgrade but cannot be bought, withdrawn, transferred, or converted.
- Subscription lifecycle should be one canonical implementation, not split between `apps.accounts` and `apps.tiers` without clear ownership.

## Current Nest Trace

Path:

- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src`

Current finding:

- No active financial subsystem was found in Nest.
- Financial terms found there are non-financial, mostly message ordering/sequence wording.
- Current financial redesign work should not need Nest changes unless future realtime payment notifications are added.

## Current React Native Trace

### Wallet / Upgrade / Billing UI

Files:

- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/WalletModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.constants.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/useProfileController.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/ProfileSheets.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile/sheets/UpgradeSheet.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/components/AccountCreditsCard.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`
- `/Users/nigel/dev/KIS/src/network/routes/healthRoutes.ts`
- `/Users/nigel/dev/KIS/src/network/routes/billingRoutes.ts`
- `/Users/nigel/dev/KIS/src/services/financialService.ts`
- `/Users/nigel/dev/KIS/src/utils/currency.ts`
- `/Users/nigel/dev/KIS/__tests__/phase5.wallet-modal.test.tsx`
- `/Users/nigel/dev/KIS/__tests__/phase5.profile-controller.test.tsx`

Current behavior:

- Wallet modal says `Manage your KIS Coin wallet. 1 KISC = $100 USD`.
- Wallet modes are `Add KIS Coins` and `Send KIS Coins`.
- Upgrade UI says upgrades apply after KIS Coin confirmation.
- Profile controller upgrades with `payment_method: 'kisc'`.
- Profile controller deposits wallet value and opens Flutterwave payment URL.
- Profile controller transfers wallet value after recipient verification.
- Account credits card says wallet value is used for upgrades, transfers, and billing.
- Currency utility defines `KISC` and KISC-to-backend-cent conversions.

High-risk areas:

- User can buy/top-up KIS Coins.
- User can send KIS Coins.
- KIS Coins have an explicit USD exchange representation.
- Upgrade flow is wallet/KISC-first instead of Flutterwave/USD-first with promotional discount support.

### Market / Commerce UI

Files:

- `/Users/nigel/dev/KIS/src/screens/market/*`
- `/Users/nigel/dev/KIS/src/screens/market/orders/*`
- `/Users/nigel/dev/KIS/src/screens/market/cart/*`
- `/Users/nigel/dev/KIS/src/screens/broadcast/market/*`
- `/Users/nigel/dev/KIS/src/screens/broadcast/pages/BroadcastMarketPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/market/api/*`
- `/Users/nigel/dev/KIS/src/components/broadcast/MarketStudioSection.tsx`

Current behavior:

- Product/service prices are shown with KISC-to-USD estimates in places.
- Cart/order/booking flows display KISC payment/receipt language.
- Marketplace checkout is tied to wallet balance in backend.

Target behavior:

- Market pricing and checkout should be USD-first.
- Flutterwave direct checkout should be used for real-money purchases.
- KIS Coins should not buy marketplace goods or services unless counsel approves a narrow discount-only use.

### Education UI

Files:

- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/*`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/components/EducationCreatorConsole.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/components/EducationProfileDashboard.tsx`

Current behavior:

- Education has course/module/material/booking pricing fields.
- UI text says paid education actions use KISC/credits in multiple places.

Target behavior:

- Education pricing should be USD-first.
- Provider settlement should be Flutterwave/direct payment or a compliant provider flow, not wallet-stored value.
- KIS Coins should only subsidize eligible platform upgrade fees unless legal review approves broader reward use.

### Health UI

Files:

- `/Users/nigel/dev/KIS/src/services/healthOpsPhase5Service.ts`
- `/Users/nigel/dev/KIS/src/services/healthOpsEngineManagerService.ts`
- `/Users/nigel/dev/KIS/src/screens/health/HealthServiceSessionScreen.tsx`
- `/Users/nigel/dev/KIS/src/features/health-dashboard/*`
- `/Users/nigel/dev/KIS/src/services/healthDashboardService.ts`

Current behavior:

- Health billing session UI and services support payment/billing workflow.
- Health engine manager converts KISC-like micro amounts.
- Backend health billing requires `kis_wallet`.

Target behavior:

- Health fees should be USD-first.
- Health payment sessions should use Flutterwave direct payment or a compliant payment provider path.
- Wallet/KISC debit should be removed from patient/provider settlement.

### Translation / Copy Surface

Files:

- `/Users/nigel/dev/KIS/src/languages/*.json`

Current behavior:

- Many translated strings mention KISC, KIS wallet, credits checkout, KIS Coin, KISC amount, and KISC-to-USD value.

Target behavior:

- Copy must consistently use USD for money and promotional credits/gift credits for KIS Coins.

## Redesign Phases

### Phase 1 - Kill The Highest-Risk Behaviors

Goal: stop KIS Coins from acting like money.

Status: completed locally on 2026-05-06.

Primary changes:

- Disable or gate wallet top-up/deposit.
- Disable peer-to-peer wallet and credit transfers.
- Disable cash-to-credit and credit-to-cash conversion.
- Remove public KISC-to-USD exchange copy.
- Keep read-only historical wallet/ledger views available.
- Keep Flutterwave USD upgrade path available.
- Add feature flags for legacy wallet paths so local development remains recoverable.

Implementation completed:

- Added explicit legacy financial feature flags with production-safe defaults:
  - `KIS_LEGACY_WALLET_DEPOSIT_ENABLED=False`
  - `KIS_LEGACY_WALLET_TRANSFER_ENABLED=False`
  - `KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED=False`
  - `KIS_LEGACY_WALLET_UPGRADE_ENABLED=False`
  - `KIS_LEGACY_PROMO_CASH_BONUS_ENABLED=False`
- Blocked wallet top-up/deposit by default.
- Blocked peer-to-peer wallet and promotional-credit transfers by default.
- Blocked cash-to-credit and credit-to-cash conversion by default.
- Blocked wallet/KISC upgrade payments by default.
- Kept promotional-credit tier upgrade support through `payment_method=credits`.
- Kept USD Flutterwave tier upgrade checkout available and made it the frontend default for paid upgrades.
- Kept historical wallet, ledger, transaction, billing, receipt, and invoice views readable.
- Prevented cash-value promo code bonuses from creating wallet value unless the explicit legacy promo-cash flag is enabled.
- Updated profile wallet/upgrade UI copy away from buy/send/exchange language toward read-only promotional-credit wording.
- Updated focused backend/frontend tests for the new default-disabled behavior.

Likely backend files:

- `apps/billing/services.py`
- `apps/billing/views.py`
- `apps/billing/serializers.py`
- `apps/billing/models.py`
- `apps/core/money.py`
- `.env.example`

Likely frontend files:

- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/WalletModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.constants.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/useProfileController.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/components/AccountCreditsCard.tsx`
- `/Users/nigel/dev/KIS/src/utils/currency.ts`
- `/Users/nigel/dev/KIS/__tests__/phase5.wallet-modal.test.tsx`
- `/Users/nigel/dev/KIS/__tests__/phase5.profile-controller.test.tsx`

Files changed in Phase 1:

- `config/settings/base.py`
- `.env.example`
- `apps/billing/services.py`
- `apps/billing/views.py`
- `apps/billing/tests.py`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/WalletModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.constants.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/useProfileController.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile/sheets/UpgradeSheet.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/components/AccountCreditsCard.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/components/dashboard/ProfileDashboardBlocks.tsx`
- `/Users/nigel/dev/KIS/__tests__/phase5.wallet-modal.test.tsx`
- `/Users/nigel/dev/KIS/__tests__/phase5.profile-controller.test.tsx`

### Phase 2 - Rename And Reframe KIS Coins As Promotional Credits

Goal: change the data contract and UI language away from coin/currency/stored value.

Status: completed locally on 2026-05-06.

Primary changes:

- Introduce canonical terminology: promotional credit, gift credit, reward credit, subsidy credit.
- Add backend constants/helpers for promotional-credit display.
- Keep database fields backward compatible in this phase.
- Change serializers to expose promotional-credit labels instead of KISC labels.
- Update translation strings and app copy.
- Add tests that public APIs no longer claim a KISC-to-USD exchange rate.

Implementation completed:

- Added canonical billing promotional-credit helpers in `apps/billing/promotional_credits.py`.
- Updated `WalletAccountSerializer` to expose:
  - `promotional_credit_label`;
  - `promotional_credit_policy`;
  - `can_buy_promotional_credits=false`;
  - `can_transfer_promotional_credits=false`;
  - `can_convert_promotional_credits_to_cash=false`.
- Reframed the backward-compatible `balance_kisc_label` field so the value now says promotional credits.
- Retained the backward-compatible `balance_usd_label` field but returned `null` so it no longer implies an exchange rate.
- Updated `CreditAccountSerializer` with promotional-credit label/policy fields.
- Updated `WalletLedgerEntrySerializer` with promotional-credit amount labels and credit-delta labels.
- Added backend regression tests proving wallet and ledger serializers do not emit KISC/KIS Coin wording in the Phase 2 public wallet surfaces.
- Added a copy-scan regression test for selected backend and React Native wallet/profile/upgrade surfaces to prevent reintroducing unsafe exchange/buy/send phrases.
- Updated React Native profile wallet loading to prefer `promotional_credit_label`.
- Removed public USD wallet exchange labels from profile wallet fallback state and dashboard calls.
- Updated focused React Native profile/dashboard/wallet utility language and selected language entries for the old profile wallet/upgrade strings.

Likely backend files:

- `apps/billing/serializers.py`
- `apps/billing/services.py`
- `apps/core/money.py`
- `apps/accounts/serializers.py`
- `docs/*`

Files changed in Phase 2:

- `apps/billing/promotional_credits.py`
- `apps/billing/serializers.py`
- `apps/billing/tests.py`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/useProfileController.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/components/dashboard/ProfileDashboardBlocks.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/components/AccountCreditsCard.tsx`
- `/Users/nigel/dev/KIS/src/utils/currency.ts`
- `/Users/nigel/dev/KIS/src/languages/en.json`
- `/Users/nigel/dev/KIS/src/languages/es.json`

Likely frontend files:

- `/Users/nigel/dev/KIS/src/utils/currency.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/WalletModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.constants.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/components/AccountCreditsCard.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile/sheets/UpgradeSheet.tsx`
- `/Users/nigel/dev/KIS/src/languages/*.json`

### Phase 3 - USD + Flutterwave Direct Upgrade Flow

Goal: account upgrades are USD-priced and paid directly through Flutterwave, with optional promotional-credit subsidy.

Primary changes:

- Add/standardize upgrade quote endpoint:
  - tier USD price;
  - available promotional credits;
  - max subsidy;
  - final payable USD cents;
  - Flutterwave payment URL when payable amount remains.
- Make `payment_method=flutterwave` the default for paid upgrades.
- Allow promotional credits only as a discount/subsidy, not a payment instrument.
- Ensure webhook applies tier only after Flutterwave success, or immediately if 100 percent promotional subsidy is allowed.

Likely backend files:

- `apps/billing/views.py`
- `apps/billing/services.py`
- `apps/accounts/models.py`
- `apps/accounts/serializers.py`
- `apps/accounts/tier_presets.py`
- `apps/billing/tests.py`

Likely frontend files:

- `/Users/nigel/dev/KIS/src/screens/tabs/profile/useProfileController.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile/sheets/UpgradeSheet.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/ProfileSheets.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`

### Phase 4 - Commerce / Marketplace USD Checkout

Goal: products, services, carts, orders, receipts, and provider settlement use USD + direct payment.

Primary changes:

- Make product/service currencies USD-first.
- Stop marketplace/service booking from requiring wallet/KISC balance.
- Add Flutterwave checkout/session for service booking deposits and marketplace orders.
- Replace wallet lock/release with payment intent + settlement status.
- Keep historical KISC orders readable.
- Update receipts to display USD and provider payment references.

Likely backend files:

- `apps/commerce/models.py`
- `apps/commerce/services.py`
- `apps/commerce/views.py`
- `apps/commerce/serializers.py`
- `apps/commerce/documents.py`
- `apps/commerce/tasks.py`
- `apps/billing/services.py`
- `apps/billing/documents.py`

Likely frontend files:

- `/Users/nigel/dev/KIS/src/screens/market/*`
- `/Users/nigel/dev/KIS/src/screens/market/cart/*`
- `/Users/nigel/dev/KIS/src/screens/market/orders/*`
- `/Users/nigel/dev/KIS/src/screens/broadcast/market/*`
- `/Users/nigel/dev/KIS/src/screens/broadcast/pages/BroadcastMarketPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/market/api/*`

### Phase 5 - Education And Health Payment Migration

Goal: education and health paid workflows stop using KIS wallet/KISC settlement.

Primary changes:

- Make education pricing USD-first.
- Replace education wallet booking payment with Flutterwave payment intent/session.
- Make health billing sessions use USD and a direct provider payment path.
- Preserve existing workflow/session UX but change payment provider semantics.
- Update health/education receipts and admin dashboards.

Likely backend files:

- `apps/broadcasts/models.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/tasks.py`
- `apps/health_ops/models.py`
- `apps/health_ops/views.py`
- `apps/health_ops/serializers.py`
- `apps/health_ops/services.py`
- `apps/billing/services.py`

Likely frontend files:

- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/*`
- `/Users/nigel/dev/KIS/src/screens/health/HealthServiceSessionScreen.tsx`
- `/Users/nigel/dev/KIS/src/services/healthOpsPhase5Service.ts`
- `/Users/nigel/dev/KIS/src/services/healthOpsEngineManagerService.ts`
- `/Users/nigel/dev/KIS/src/features/health-dashboard/*`

### Phase 6 - Data Migration, Backward Compatibility, And Audit

Goal: safely preserve old balances/records without continuing risky behavior.

Primary changes:

- Decide how existing wallet balances become promotional credits, refunded balances, or manually reviewed balances.
- Add migration scripts/commands for legacy wallet state.
- Add admin audit views for legacy balances and conversion decisions.
- Add clear user-facing history labels.
- Keep historical receipts immutable but mark legacy payment method.

Likely backend files:

- `apps/billing/models.py`
- `apps/billing/services.py`
- `apps/billing/views.py`
- `apps/billing/admin.py`
- `apps/billing/tests.py`
- new `apps/billing/management/commands/*`

Likely frontend files:

- wallet/profile billing history surfaces.

### Phase 7 - Compliance QA And Launch Evidence

Goal: prove the new financial system does not expose coin-as-money behavior.

Primary changes:

- Add backend tests for disabled top-up/transfer/conversion.
- Add tests for USD-only checkout and upgrade payment.
- Add frontend tests for changed wallet/upgrade copy.
- Run docs/copy scan for banned phrases:
  - `1 KISC =`
  - `Send KIS Coins`
  - `Add KIS Coins`
  - `cash to credits`
  - `credits to cash`
  - `KISC checkout`
- Prepare counsel review packet and launch checklist.

Likely files:

- `apps/billing/tests.py`
- `apps/commerce/tests.py`
- `apps/broadcasts/tests.py`
- `apps/health_ops/tests/*`
- `/Users/nigel/dev/KIS/__tests__/*`
- docs under `docs/operations/`

## Phase 0 Validation

Commands run:

- `rg` trace over Django financial keywords.
- `rg` trace over Nest financial keywords.
- `rg` trace over React Native financial keywords.
- Manual inspection of billing, money conversion, commerce, health, education/broadcast, tier/subscription, and React Native wallet/upgrade files.

No code behavior was changed in Phase 0.

## Phase 1 Validation

Commands run:

- `python3 -m py_compile config/settings/base.py apps/billing/services.py apps/billing/views.py apps/billing/tests.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py test apps.billing.tests.BillingWalletFlowTests apps.billing.tests.WalletTransferPayloadValidationTests apps.billing.tests.WalletUpgradeApiTests apps.billing.tests.WalletHistoryManagementApiTests --keepdb --noinput`
- `npx eslint src/screens/tabs/profile-screen/WalletModal.tsx src/screens/tabs/profile/profile.constants.ts src/screens/tabs/profile/useProfileController.ts src/screens/tabs/profile/profile/sheets/UpgradeSheet.tsx src/screens/tabs/profile/components/AccountCreditsCard.tsx src/screens/tabs/profile/components/dashboard/ProfileDashboardBlocks.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx --quiet`
- `npm run typecheck`
- `npm run test:phase5 -- __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx`
- `npm run ci:launch`

Validation result:

- Django system check passed.
- Django migration dry-run reported no model changes.
- Focused billing tests passed: 22 tests.
- Focused React Native lint passed.
- React Native typecheck passed.
- Focused wallet/profile Jest tests passed: 5 tests.
- React Native launch CI command passed, including production audit with zero vulnerabilities.

Phase 1 remaining risks:

- This is still not legal advice. Counsel should review the final financial model before production.
- Legacy database fields and some non-profile domains still contain KISC terminology for backward compatibility.
- Marketplace, education, and health checkout paths still need Phase 3+ migration away from wallet/KISC settlement.
- Existing historical balances need a product/legal migration decision: promotional-credit conversion, refund, manual review, or freeze.
- Backend serializers still expose some KISC-style labels for compatibility until Phase 2.

## Phase 2 Validation

Commands run:

- `python3 -m py_compile apps/billing/promotional_credits.py apps/billing/serializers.py apps/billing/tests.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py test apps.billing.tests.BillingWalletFlowTests --keepdb --noinput`
- `python3 manage.py test apps.billing.tests.BillingWalletFlowTests apps.billing.tests.WalletTransferPayloadValidationTests apps.billing.tests.WalletUpgradeApiTests apps.billing.tests.WalletHistoryManagementApiTests --keepdb --noinput`
- `rg -n "1 KISC|Add KIS Coins|Send KIS Coins|Manage your KIS Coin wallet|KIS Coin confirmation|Not enough KIS Coins|Verify the recipient first before sending KIS Coins" ...selected Phase 2 surfaces`
- `npx eslint src/screens/tabs/profile-screen/WalletModal.tsx src/screens/tabs/profile/profile.constants.ts src/screens/tabs/profile/useProfileController.ts src/screens/tabs/ProfileScreen.tsx src/screens/tabs/profile/profile/sheets/UpgradeSheet.tsx src/screens/tabs/profile/components/AccountCreditsCard.tsx src/screens/tabs/profile/components/dashboard/ProfileDashboardBlocks.tsx src/utils/currency.ts __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx --quiet`
- `npm run typecheck`
- `npm run test:phase5 -- __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx`
- `npm run ci:launch`

Validation result:

- Django system check passed.
- Django migration dry-run reported no model changes.
- Focused billing tests passed: 25 tests.
- Focused React Native lint passed.
- React Native typecheck passed.
- Focused wallet/profile Jest tests passed: 5 tests.
- React Native launch CI command passed, including production audit with zero vulnerabilities.
- Phase 2 selected-surface copy scan found no unsafe profile wallet/upgrade exchange/buy/send phrases.

Phase 2 remaining risks:

- This phase intentionally preserved backward-compatible field names such as `balance_kisc_label`; the emitted value is safe, but the field name should be deprecated in a later API version.
- Marketplace, education, and health payment flows still contain KISC/wallet settlement behavior and copy. Those are Phase 3+ migration targets and were not behaviorally changed in Phase 2.
- Some translation and source strings still contain KISC for health/market/education paths that are not part of this phase.
- `apps/core/money.py` still contains compatibility conversion helpers for existing payloads; Phase 3+ should remove public reliance on those helpers where checkout moves to USD.

## Best Prompt For Phase 3

```text
Please proceed with Phase 3 of the KIS financial system redesign without using git commands. Focus on marketplace and commerce checkout migration away from wallet/KISC settlement while preserving historical order/receipt readability. Make new marketplace product, cart, order, service booking, and shop-service payment paths USD-first with Flutterwave/direct provider payment where safe. Disable new wallet/KISC checkout for commerce by default behind explicit legacy flags. Keep existing historical KISC orders readable, avoid destructive migrations, add compatibility serializers that show USD/payment-provider status plus safe historical labels, update React Native market and broadcast-market UI copy away from KISC checkout language, add focused backend/frontend regression tests or record blockers, run safe validation, update docs/financial-system-redesign-roadmap.md and docs/BUILD_STATE.md with progress, risks, validation, and the best prompt for Phase 4.
```

## Phase 3 Validation

Scope completed:

- Added commerce launch flags:
  - `KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED=False`
  - `KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER=flutterwave`
- Changed new marketplace order creation to default to USD provider-pending checkout instead of wallet/KISC escrow.
- Blocked new marketplace wallet/KISC checkout unless the explicit legacy commerce flag is enabled.
- Preserved historical marketplace wallet escrow behavior behind the legacy flag for migration/local recovery.
- Prevented provider-pending marketplace orders from being marked complete or satisfied before payment is confirmed.
- Changed service booking deposit and remaining payment paths to create USD provider-pending `ServiceBookingPayment` records by default.
- Blocked service booking wallet/KISC payment requests by default with a clear `legacy_commerce_wallet_checkout_disabled` response code.
- Kept historical booking payments, marketplace orders, receipts, ledger rows, and wallet transactions readable.
- Added compatibility serializer fields for USD labels, payment provider, payment status, and safe historical promotional-credit labels.
- Updated React Native market/cart/order/service booking/broadcast-market copy away from KISC checkout and fixed exchange-rate wording.

Main files changed:

- `config/settings/base.py`
- `.env.example`
- `apps/commerce/services.py`
- `apps/commerce/views.py`
- `apps/commerce/serializers.py`
- `apps/commerce/tests.py`
- `docs/financial-system-redesign-roadmap.md`
- `docs/BUILD_STATE.md`
- `/Users/nigel/dev/KIS/src/utils/currency.ts`
- `/Users/nigel/dev/KIS/src/screens/market/market.constants.ts`
- `/Users/nigel/dev/KIS/src/screens/market/cart/CartDetailPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/market/ServiceBookingScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/market/ServiceBookingDetailsPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/market/orders/MyOrdersPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/market/orders/ProviderOrdersPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/market/orders/MarketplaceOrderDetailPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/market/ServiceEditorDrawer.tsx`
- `/Users/nigel/dev/KIS/src/screens/market/ProductEditorDrawer.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/pages/BroadcastMarketPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/market/pages/MarketProductsPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/market/pages/ShopProductsPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/market/pages/ShopServicesPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/market/ProductDetailsPage.tsx`

Commands run:

- `python3 -m py_compile apps/commerce/services.py apps/commerce/views.py apps/commerce/serializers.py apps/commerce/tests.py config/settings/base.py`
- `python3 manage.py test apps.commerce.tests.MarketplaceUsdCheckoutTests apps.commerce.tests.MarketplaceOrderSettlementTests apps.commerce.tests.ServiceBookingMoneyNormalizationTests --noinput`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `rg -n "KISC|KIS Coin|1 .* = .*USD|wallet was charged|wallet checkout|wallet balance" /Users/nigel/dev/KIS/src/screens/market /Users/nigel/dev/KIS/src/screens/broadcast/market /Users/nigel/dev/KIS/src/screens/broadcast/pages/BroadcastMarketPage.tsx -S`
- `npx eslint src/utils/currency.ts src/screens/market/market.constants.ts src/screens/market/cart/CartDetailPage.tsx src/screens/market/ServiceBookingScreen.tsx src/screens/market/ServiceBookingDetailsPage.tsx src/screens/market/orders/MyOrdersPage.tsx src/screens/market/orders/ProviderOrdersPage.tsx src/screens/market/orders/MarketplaceOrderDetailPage.tsx src/screens/market/ServiceEditorDrawer.tsx src/screens/market/ProductEditorDrawer.tsx src/screens/broadcast/pages/BroadcastMarketPage.tsx src/screens/broadcast/market/pages/MarketProductsPage.tsx src/screens/broadcast/market/pages/ShopProductsPage.tsx src/screens/broadcast/market/pages/ShopServicesPage.tsx src/screens/broadcast/market/ProductDetailsPage.tsx --quiet`
- `npm run typecheck`

Validation result:

- Python compile passed.
- Django focused commerce tests passed: 8 tests.
- Django system check passed.
- Django migration dry-run reported no model changes.
- Focused React Native ESLint passed.
- React Native typecheck passed.
- Targeted market/broadcast-market copy scan found no unsafe KISC/exchange/wallet-charge wording except `KISContact`, which is a contact type name and not financial copy.

Phase 3 remaining risks:

- This phase does not yet create real Flutterwave payment intents or consume provider callbacks for marketplace/service-booking payments. It creates provider-pending records and blocks completion until payment is confirmed.
- A staff/admin or provider-callback path still needs to mark provider payments paid in a controlled way before satisfaction/completion.
- Historical database fields and migrations still contain KISC defaults for backward compatibility.
- Product/service editor forms are now USD-labeled, but deeper marketplace payment UX should be revisited after provider payment intents exist.
- Education and health checkout flows remain Phase 4 work and still contain wallet/KISC settlement language.

## Best Prompt For Phase 4

```text
Please proceed with Phase 4 of the KIS financial system redesign without using git commands. Focus on education and health paid-workflow migration away from wallet/KISC settlement while preserving historical records. Make new education enrollment/booking/payment and health billing/session/payment paths USD-first with Flutterwave/direct provider payment where safe. Disable new wallet/KISC checkout for education and health by default behind explicit legacy flags. Keep historical KISC education/health records readable, avoid destructive migrations, add compatibility serializers that show USD/payment-provider status plus safe historical labels, update React Native education and health UI copy away from KISC/wallet checkout language, add focused backend/frontend regression tests or record blockers, run safe validation, update docs/financial-system-redesign-roadmap.md and docs/BUILD_STATE.md with progress, risks, validation, and the best prompt for Phase 5.
```

## Phase 4 Validation

Scope completed:

- Added education and health launch flags:
  - `KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED=False`
  - `KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER=flutterwave`
  - `KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED=False`
  - `KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER=flutterwave`
- Changed new paid education broadcast bookings to record USD/provider-pending payment metadata by default.
- Blocked new education wallet/KISC payment requests unless the explicit legacy education flag is enabled.
- Preserved historical education wallet transaction behavior behind the legacy flag for migration/local recovery.
- Prevented paid education bookings from being marked completed before wallet escrow release or direct provider payment confirmation.
- Changed health billing sessions to default to USD/provider checkout instead of `kis_wallet`.
- Blocked new health wallet/KISC billing sessions and payment-method selections unless the explicit legacy health flag is enabled.
- Kept the existing health engine workflow structure intact while moving new paid sessions into provider-pending semantics.
- Added compatibility serializer fields for education and health USD labels, payment provider, payment status, payment-required state, and safe historical promotional-credit labels.
- Updated React Native education and health UI copy away from KISC/wallet checkout, wallet debit, and KISC escrow wording.

Main files changed:

- `config/settings/base.py`
- `.env.example`
- `apps/broadcasts/views.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/tasks.py`
- `apps/broadcasts/tests.py`
- `apps/health_ops/views.py`
- `apps/health_ops/serializers.py`
- `apps/health_ops/tests/test_workflow_runtime.py`
- `docs/financial-system-redesign-roadmap.md`
- `docs/BUILD_STATE.md`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/EducationV2DiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationContentCard.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationEnrollmentSheet.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationDetailSheet.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthServiceSessionScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthInstitutionCardsScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/InstitutionServicesCatalogScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthEnginesDashboads/VideoConsultationManager.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthEnginesDashboads/LabOrderManager.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthEnginesDashboads/AppointmentManager.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthEnginesDashboads/EmergencyDispatchManager.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthEnginesDashboads/AdmissionBedManager.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthEnginesDashboads/EPrescriptionManager.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthEnginesDashboads/PharmacyManager.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthEnginesDashboads/ImagingOrderManager.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthEnginesDashboads/HomeLogisticsManager.tsx`

Commands run:

- `python3 -m py_compile config/settings/base.py apps/broadcasts/views.py apps/broadcasts/serializers.py apps/broadcasts/tasks.py apps/broadcasts/tests.py apps/health_ops/views.py apps/health_ops/serializers.py apps/health_ops/tests/test_workflow_runtime.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py test apps.broadcasts.tests.EducationInstitutionFormNormalizationTests apps.health_ops.tests.test_workflow_runtime.HealthOpsWorkflowRuntimeTests --noinput`
- `npm run typecheck`
- `npx eslint src/screens/broadcast/education/EducationV2DiscoverPage.tsx src/screens/broadcast/education/components/EducationContentCard.tsx src/screens/broadcast/education/components/EducationEnrollmentSheet.tsx src/screens/broadcast/education/components/EducationDetailSheet.tsx src/screens/health/HealthServiceSessionScreen.tsx src/screens/health/HealthInstitutionCardsScreen.tsx src/screens/health/InstitutionServicesCatalogScreen.tsx src/screens/health/HealthEnginesDashboads/VideoConsultationManager.tsx src/screens/health/HealthEnginesDashboads/LabOrderManager.tsx src/screens/health/HealthEnginesDashboads/AppointmentManager.tsx src/screens/health/HealthEnginesDashboads/EmergencyDispatchManager.tsx src/screens/health/HealthEnginesDashboads/AdmissionBedManager.tsx src/screens/health/HealthEnginesDashboads/EPrescriptionManager.tsx src/screens/health/HealthEnginesDashboads/PharmacyManager.tsx src/screens/health/HealthEnginesDashboads/ImagingOrderManager.tsx src/screens/health/HealthEnginesDashboads/HomeLogisticsManager.tsx --quiet`
- `rg -n "KISC|KIS Coin|kis_wallet|wallet_balance|KIS wallet|wallet debit|KISC escrow|wallet checkout" /Users/nigel/dev/KIS/src/screens/broadcast/education /Users/nigel/dev/KIS/src/screens/health -S`

Validation result:

- Python compile passed.
- Django focused education/health tests passed: 20 tests.
- Django system check passed.
- Django migration dry-run reported no changes.
- React Native typecheck passed.
- Focused React Native ESLint passed.
- Targeted education/health copy scan found no unsafe public KISC/wallet checkout wording. The remaining hits were `KISContact` contact type names and a compatibility `wallet_balance` snapshot field name, not public payment copy.

Phase 4 remaining risks:

- This phase still does not create real Flutterwave payment intents or consume provider callbacks for education/health payments. It records provider-pending state and blocks completion until payment is confirmed.
- A provider callback or staff-controlled reconciliation path still needs to move provider-pending education and health payments to paid in a controlled, idempotent way.
- Historical education and health wallet/KISC records remain readable and old database fields remain for backward compatibility.
- Some internal compatibility payload names still contain wallet/KISC terms so older records and clients do not break; public UI copy was moved away from those terms for the touched surfaces.
- This is still not legal advice. Counsel should review the final financial model and historical-balance treatment before production.

## Best Prompt For Phase 5

```text
Please proceed with Phase 5 of the KIS financial system redesign without using git commands. Focus on direct provider payment-intent and callback completion for USD workflows. Add provider-neutral payment intent/session creation for commerce, education, and health payments with Flutterwave as the first adapter, signed callback/webhook verification, payment status reconciliation, idempotency, admin-visible payment audit logs, and safe paid-state transitions from provider-pending to paid without wallet/KISC settlement. Keep legacy wallet flows disabled by default behind explicit flags, preserve historical records, avoid destructive migrations, add focused backend/frontend regression tests or record blockers, run safe validation, update docs/financial-system-redesign-roadmap.md and docs/BUILD_STATE.md with progress, risks, validation, and the best prompt for Phase 6.
```

## Phase 5 Validation

Scope completed:

- Added provider-neutral direct payment intent records for USD checkout:
  - marketplace orders;
  - service booking payments;
  - education bookings;
  - health billing sessions.
- Added structured direct payment audit events for intent creation, updates, provider callback outcomes, duplicate callbacks, unmatched callbacks, and invalid signatures.
- Added a Flutterwave-first adapter path with payment-link creation behind `KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED=False` by default.
- Added signed Flutterwave callback reconciliation for direct-payment intents without wallet/KISC settlement.
- Kept the existing wallet Flutterwave webhook path backward compatible while routing direct-payment `tx_ref` values through the new direct-payment reconciler.
- Added admin-visible read-only direct payment audit endpoint.
- Wired provider-pending records to store:
  - `direct_payment_intent_id`;
  - `payment_reference`;
  - `payment_url` when provider links are explicitly enabled;
  - redacted provider/callback metadata.
- Added safe paid-state transitions:
  - marketplace orders become provider-paid and can move to awaiting satisfaction;
  - service booking payments become paid;
  - education bookings become confirmed;
  - health billing sessions become paid.
- Added focused regression test coverage for intent creation and callback reconciliation where safe.

Main files changed:

- `config/settings/base.py`
- `.env.example`
- `apps/billing/models.py`
- `apps/billing/direct_payments.py`
- `apps/billing/serializers.py`
- `apps/billing/views.py`
- `apps/billing/urls.py`
- `apps/billing/migrations/0007_directpaymentintent_directpaymentauditevent_and_more.py`
- `apps/commerce/services.py`
- `apps/commerce/views.py`
- `apps/commerce/serializers.py`
- `apps/commerce/tests.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/tests.py`
- `apps/health_ops/views.py`
- `apps/health_ops/serializers.py`
- `apps/health_ops/tests/test_workflow_runtime.py`
- `docs/financial-system-redesign-roadmap.md`
- `docs/BUILD_STATE.md`

Commands run:

- `python3 manage.py makemigrations billing`
- `python3 -m py_compile config/settings/base.py apps/billing/models.py apps/billing/direct_payments.py apps/billing/serializers.py apps/billing/views.py apps/billing/urls.py apps/commerce/services.py apps/commerce/views.py apps/commerce/serializers.py apps/commerce/tests.py apps/broadcasts/views.py apps/broadcasts/serializers.py apps/broadcasts/tests.py apps/health_ops/views.py apps/health_ops/serializers.py apps/health_ops/tests/test_workflow_runtime.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- Attempted: `python3 manage.py test apps.commerce.tests.MarketplaceUsdCheckoutTests apps.commerce.tests.ServiceBookingMoneyNormalizationTests apps.broadcasts.tests.EducationInstitutionFormNormalizationTests apps.health_ops.tests.test_workflow_runtime.HealthOpsWorkflowRuntimeTests --noinput`

Validation result:

- Python compile passed.
- Django system check passed.
- Django migration dry-run passed with no changes detected after creating the new billing migration.
- Focused regression test run was blocked by environment/runtime cost. The first run reached tests but hit a Redis/Celery retry while marketplace auto-satisfaction scheduling was invoked; that path was patched to mock the scheduler in the new callback test. The rerun was stopped after long test database setup per the instruction to skip blocked checks and move on.

Phase 5 remaining risks:

- Real Flutterwave payment-link creation remains disabled by default. Enable only in staging/production after setting `KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED=True`, `FLW_SECRET_KEY`, `FLW_WEBHOOK_SECRET`, and `FLW_REDIRECT_URL`.
- Callback verification currently uses Flutterwave `verif-hash` matching `FLW_WEBHOOK_SECRET`; production should confirm the exact Flutterwave dashboard secret and callback URL are configured.
- The new migration creates direct payment intent/audit tables; it is additive and non-destructive but still needs to be applied in staging before launch testing.
- Focused payment regression tests need a clean test environment without Redis/Celery scheduling delays, or Celery eager settings, before being treated as launch evidence.
- Frontend payment handoff still needs to use the new `payment_url` / `direct_payment_intent_id` fields consistently across commerce, education, and health.

## Best Prompt For Phase 6

```text
Please proceed with Phase 6 of the KIS financial system redesign without using git commands. Focus on frontend payment handoff and production QA for the new direct USD payment intents. Connect React Native commerce, education, and health payment screens to the new `direct_payment_intent_id`, `payment_reference`, and `payment_url` fields; open Flutterwave checkout only when a provider URL exists; add polling/status refresh after payment return; keep wallet/KISC legacy flows disabled by default; add user-safe error states for pending/failed/cancelled payments; run lightweight backend/frontend validation and record blockers instead of waiting on long environment setup; update docs/financial-system-redesign-roadmap.md and docs/BUILD_STATE.md with progress, risks, validation, and the best prompt for Phase 7.
```

## Phase 6 Validation

Scope completed:

- Added a shared React Native direct payment handoff helper:
  - normalizes `direct_payment_intent_id`, `payment_intent_id`, `payment_reference`, `payment_url`, provider, and payment status from mixed backend payloads;
  - opens secure provider checkout only when a `payment_url` exists;
  - avoids treating generic object `status` as payment status when explicit payment fields exist.
- Connected marketplace order detail to direct payment state:
  - shows payment status, reference, and intent id;
  - opens Flutterwave/provider checkout only when `payment_url` exists;
  - adds refresh payment status action;
  - prevents buyer satisfaction/provider completion actions until provider payment is paid.
- Connected service booking detail to direct payment state:
  - shows payment reference and intent id;
  - offers secure checkout for pending/failed provider payments only when a provider URL exists;
  - keeps remaining-payment requests in provider-pending mode and asks the user to refresh/open checkout.
- Connected education booking/enrollment flow to direct payment state:
  - reads payment URL/reference from returned booking payloads;
  - opens provider checkout when available;
  - shows a clear pending message when checkout URL has not been created yet.
- Connected health billing session UI to direct payment state:
  - shows direct payment intent and reference;
  - adds secure checkout action when provider URL exists;
  - blocks local `authorize_payment` completion when no provider checkout URL exists, avoiding fake paid state.

Main files changed:

- `/Users/nigel/dev/KIS/src/utils/directPaymentHandoff.ts`
- `/Users/nigel/dev/KIS/src/screens/market/orders/MarketplaceOrderDetailPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/market/ServiceBookingDetailsPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/EducationV2DiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthServiceSessionScreen.tsx`
- `docs/financial-system-redesign-roadmap.md`
- `docs/BUILD_STATE.md`

Commands run:

- `npx eslint src/utils/directPaymentHandoff.ts src/screens/market/orders/MarketplaceOrderDetailPage.tsx src/screens/market/ServiceBookingDetailsPage.tsx src/screens/broadcast/education/EducationV2DiscoverPage.tsx src/screens/broadcast/education/components/EducationEnrollmentSheet.tsx src/screens/health/HealthServiceSessionScreen.tsx --quiet`
- `npm run typecheck`
- `python3 manage.py check`

Validation result:

- Focused React Native ESLint passed.
- React Native typecheck passed.
- Django system check passed.
- Long backend/frontend runtime tests were not run in this phase per instruction to skip blocked or high-cost checks and record them.

Phase 6 remaining risks:

- Real Flutterwave checkout cannot be fully verified until staging has `KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED=True`, valid Flutterwave keys, a callback URL, and a redirect URL.
- Current mobile behavior depends on backend returning `payment_url`; when provider link generation is disabled, the UI safely shows pending/reference states instead of opening checkout.
- Payment return handling is refresh/poll oriented; a deeper app-link redirect handler can be added after staging provider URLs are verified.
- Full device QA is still required for iOS/Android external checkout handoff and return behavior.

## Best Prompt For Phase 7

```text
Please proceed with Phase 7 of the KIS financial system redesign without using git commands. Focus on staging Flutterwave QA and launch evidence for direct USD payments. Enable payment-link generation only in staging with approved Flutterwave sandbox credentials; verify marketplace order, service booking, education booking, and health billing payment links; validate signed webhook callbacks for successful, failed, cancelled, duplicate, and unmatched payments; confirm React Native checkout handoff, return refresh, and pending/failed UI on a real device or staging build; keep wallet/KISC legacy flows disabled by default; record provider dashboard callback URL evidence, audit-log evidence, rollback steps, blockers, and validation in docs/financial-system-redesign-roadmap.md and docs/BUILD_STATE.md; then give the best prompt for Phase 8.
```

## Phase 7 Validation

Scope completed:

- Added staging direct-payment go/no-go checklist:
  - `docs/operations/FINANCIAL_DIRECT_PAYMENT_STAGING_GO_NO_GO.md`
- Added a non-secret Django readiness command:
  - `python3 manage.py direct_payment_staging_check --json`
- The readiness command checks:
  - staging environment gate;
  - direct provider payment-link gate;
  - Flutterwave secret presence without printing values;
  - callback/redirect URL readiness;
  - legacy wallet/KISC flags remain disabled;
  - optional direct-payment intent counts with `--include-counts`.
- Documented provider dashboard evidence requirements:
  - Flutterwave sandbox mode;
  - staging webhook URL;
  - staging redirect URL;
  - release-ticket screenshots/links with no secret exposure.
- Documented staging evidence matrix for:
  - marketplace order;
  - service booking;
  - education booking;
  - health billing;
  - successful/failed/cancelled/duplicate/unmatched callbacks;
  - invalid signature rejection;
  - React Native checkout handoff and return refresh.
- Documented rollback steps that keep wallet/KISC legacy flows disabled.

Main files changed:

- `apps/billing/management/__init__.py`
- `apps/billing/management/commands/__init__.py`
- `apps/billing/management/commands/direct_payment_staging_check.py`
- `docs/operations/FINANCIAL_DIRECT_PAYMENT_STAGING_GO_NO_GO.md`
- `docs/financial-system-redesign-roadmap.md`
- `docs/BUILD_STATE.md`

Commands run:

- `python3 -m py_compile apps/billing/management/commands/direct_payment_staging_check.py`
- `python3 manage.py direct_payment_staging_check --json`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`

Validation result:

- Python compile passed.
- Django system check passed.
- Django migration dry-run reported no changes.
- Local readiness command ran successfully and printed no secret values.
- Local readiness status was expectedly not ready for staging provider-link QA because this local environment is not `DJANGO_ENV=staging`, provider links are disabled, and Flutterwave secrets are not configured.

Phase 7 remaining risks:

- No real Flutterwave sandbox payment link was created locally because approved staging credentials and provider-console access are not present.
- No real provider dashboard callback URL screenshot/evidence was captured locally.
- No real React Native device checkout handoff or return-refresh evidence was captured locally.
- Production remains **NO-GO** until the staging evidence matrix in `docs/operations/FINANCIAL_DIRECT_PAYMENT_STAGING_GO_NO_GO.md` is completed.

## Best Prompt For Phase 8

```text
Please proceed with Phase 8 of the KIS financial system redesign without using git commands. Focus on final launch compliance cleanup and production sign-off for the financial redesign. Review all public backend serializers, React Native screens, translations, receipt/document templates, docs, and env examples for unsafe KISC/wallet-as-money wording; confirm KIS Coins are only promotional/gift/reward credits and cannot be bought, transferred, withdrawn, or converted; verify direct USD payment launch evidence from Phase 7 staging is attached or record exact blockers; finalize historical wallet/KISC balance treatment options for counsel/product approval; add a production rollback and monitoring checklist for payment incidents; keep legacy wallet/KISC checkout flags disabled by default; run lightweight validation and copy scans, record blockers instead of waiting on long tests, update docs/financial-system-redesign-roadmap.md and docs/BUILD_STATE.md, and give the final launch-readiness summary.
```

## Phase 8 Final Compliance Cleanup And Sign-Off

Scope completed:

- Added the final production sign-off checklist:
  - `docs/operations/FINANCIAL_PRODUCTION_LAUNCH_SIGNOFF.md`
- Reconfirmed the canonical policy:
  - KIS Coins are promotional/gift/reward credits only;
  - KIS Coins cannot be bought, sold, transferred peer-to-peer, withdrawn, redeemed for cash, converted to cash, or marketed as stored value;
  - new paid workflows are USD-first through Flutterwave/direct provider payment.
- Cleaned remaining public backend wording:
  - marketplace PDF receipt title no longer says `KISC Marketplace Receipt`;
  - marketplace receipts now label historical KISC records as historical promotional credits;
  - service booking receipt generation no longer defaults missing currency to `KISC`;
  - education payment FAQ now describes secure USD checkout and historical promotional-credit compatibility.
- Cleaned React Native public copy in translation files for old KISC escrow/wallet wording and changed exposed education dashboard finance labels away from KISC money language.
- Added `.env.example` comments clarifying that remaining health KISC micro-unit knobs are legacy compatibility settings only, not a public exchange-rate product.
- Captured historical balance treatment options for counsel/product approval.
- Added production rollback and monitoring checklist for direct-payment incidents.

Main files changed:

- `apps/billing/documents.py`
- `apps/commerce/documents.py`
- `apps/broadcasts/views.py`
- `.env.example`
- `/Users/nigel/dev/KIS/src/languages/en.json`
- `/Users/nigel/dev/KIS/src/languages/es.json`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`
- `docs/operations/FINANCIAL_PRODUCTION_LAUNCH_SIGNOFF.md`
- `docs/financial-system-redesign-roadmap.md`
- `docs/BUILD_STATE.md`

Lightweight copy scans run:

- Backend/docs/env scan for `KISC`, `KIS Coin`, wallet-as-money, exchange-rate, top-up, withdrawal, conversion, and transfer wording.
- React Native scan for the same public wording.

Copy scan result:

- Remaining backend hits are mostly:
  - migrations and database compatibility fields;
  - internal money conversion helpers retained for old records;
  - disabled endpoint error messages;
  - tests proving unsafe public copy is blocked;
  - roadmap/build-state historical analysis.
- Remaining React Native hits are mostly:
  - `KISContact`, which is not financial copy;
  - internal compatibility constants/helpers;
  - safe promotional-credit copy;
  - education metadata fields still carrying `price_currency: 'KISC'` for backward compatibility.
- Older non-launch docs such as shop/education progress notes still mention historical KISC behavior. They were not rewritten wholesale because they are progress/history documents, but production-facing launch docs now carry the correct policy.

Validation commands run:

- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py direct_payment_staging_check --json`
- `python3 -m py_compile apps/billing/documents.py apps/commerce/documents.py apps/broadcasts/views.py`
- `npx eslint src/screens/tabs/profile-screen/EducationManagementModal.tsx --quiet`
- `node -e "JSON.parse(require('fs').readFileSync('src/languages/en.json','utf8')); JSON.parse(require('fs').readFileSync('src/languages/es.json','utf8')); console.log('translation json ok')"`
- focused `rg` copy scans listed above.

Validation result:

- Django system check passed.
- Django migration dry-run reported no model changes.
- Direct payment staging check ran without printing secret values.
- Python compile passed for changed backend document/view files.
- Focused React Native ESLint passed for the changed education management screen.
- Translation JSON parse check passed for English and Spanish.
- Local readiness remains false as expected because this is not `DJANGO_ENV=staging`, direct provider links are disabled, and Flutterwave secret/webhook secret values are not configured locally.
- Focused React Native unsafe-phrase scan found no remaining live matches for the old KISC escrow/wallet phrases cleaned in this phase.
- Backend focused unsafe-phrase scan found only historical roadmap notes and tests that intentionally assert unsafe copy is absent.

Phase 8 final launch-readiness decision:

- **Production financial launch is NO-GO today.**
- Code and public copy are substantially safer, and legacy wallet/KISC behavior remains disabled by default.
- Production launch is still blocked by missing Phase 7 staging evidence:
  - no attached Flutterwave sandbox payment-link evidence;
  - no signed callback replay evidence for success/failure/cancel/duplicate/unmatched/invalid-signature scenarios;
  - no real-device React Native checkout handoff/return-refresh evidence;
  - no provider dashboard callback URL proof;
  - no counsel/product approval for historical wallet/KISC balance treatment.

Recommended next action:

- Do not start another code-hardening phase yet.
- Execute the staging evidence checklist and legal/product sign-off in `docs/operations/FINANCIAL_PRODUCTION_LAUNCH_SIGNOFF.md` and `docs/operations/FINANCIAL_DIRECT_PAYMENT_STAGING_GO_NO_GO.md`.
