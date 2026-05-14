# KIS Royal Theme Roadmap Status

Current status: Phase 01 completed. Phase 02 is next.

## Goal

Move KIS away from the current orange + purple identity into a luxury **royal gold + deep purple** design system. The target feel is premium, regal, calm, and polished: a place for kings, creators, institutions, partners, and high-trust commerce.

## Phase Status

- Phase 00: Analysis and roadmap. Completed.
- Phase 01: Core design tokens and navigation theme. Completed.
- Phase 02: Shared components and primitives. Next.
- Phase 03: App shell, auth, profile, dashboard, and messaging chrome.
- Phase 04: Broadcast, Channels, Feed Studio, and creator surfaces.
- Phase 05: Commerce, wallet, billing, education, health, and partner surfaces.
- Phase 06: Admin/backend generated UI and operational docs.
- Phase 07: Hard-coded color cleanup, visual QA, accessibility, and release evidence.

## Evidence From Current Code

Primary React Native theme files:

- `/Users/nigel/dev/KIS/src/theme/constants.ts`
- `/Users/nigel/dev/KIS/src/theme/useTheme.ts`
- `/Users/nigel/dev/KIS/src/theme/navTheme.ts`
- `/Users/nigel/dev/KIS/src/theme/health/colors.ts`
- `/Users/nigel/dev/KIS/src/constants/KISButton.tsx`
- `/Users/nigel/dev/KIS/src/constants/KISTextInput.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.styles.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/constants.ts`
- `/Users/nigel/dev/KIS/src/screens/market/market.styles.ts`

Current issue:

- Phase 01 replaced the core orange-first theme foundation with royal gold + purple tokens.
- `orange` remains only as a deprecated compatibility alias mapped to deep royal gold.
- Navigation primary now uses `KIS_COLORS.brand.primary`.
- Health theme primary accents now use royal gold and purple.
- Many screens correctly consume `palette.primary`, `palette.primaryStrong`, and `palette.primarySoft`, so a token-first migration will cover much of the app safely.
- Some files contain hard-coded one-off gold/beige colors added during recent UI work. These must be moved into shared royal tokens to avoid inconsistent luxury styling.

## Target Palette

Canonical brand direction:

- Royal Gold: `#C9A24A`
- Metallic Gold Highlight: `#FFF4B8`
- Metallic Gold Light: `#F4D77A`
- Metallic Gold Deep: `#9A6A14`
- Metallic Gold Shadow: `#5E3B0A`
- Deep Purple: `#4B1D78`
- Imperial Purple: `#6E35B7`
- Dark Royal Ink: `#17111F`
- Warm Ivory: `#FFFBF2`
- Parchment Surface: `#F8F1E3`
- Gold Border: `#E6D7B2`
- Muted Plum Text: `#6C6078`

Design rules:

- Gold is the primary action/accent color.
- Gold should appear as a metallic range, using highlight/midtone/deep/shadow values together for premium surfaces and gradients.
- Purple is the secondary brand and depth color.
- In light mode, navigation chrome should visibly use deep royal purple so the app does not become a white interface with small gold accents.
- Orange should not appear as a brand color in user-facing UI after the migration.
- Avoid beige-only pages. Ivory/parchment must be balanced with purple depth and gold accents.
- Cards stay 8px radius unless an existing component contract requires another value.
- Use `palette`/tokens first; hard-coded screen colors should be exceptions only for deliberate media overlays.

## Validation Log

2026-05-13 - Phase 00
- Files changed:
  - `docs/royal-theme-roadmap/phase-00-analysis-and-plan.md`
  - `docs/royal-theme-roadmap/status.md`
  - `docs/royal-theme-roadmap/royal-theme-roadmap.md`
  - `docs/royal-theme-roadmap/phase-01-core-theme-tokens.md`
  - `docs/royal-theme-roadmap/phase-02-shared-components.md`
  - `docs/royal-theme-roadmap/phase-03-app-shell-auth-profile-messaging.md`
  - `docs/royal-theme-roadmap/phase-04-broadcast-channels-feed-studio.md`
  - `docs/royal-theme-roadmap/phase-05-commerce-education-health-partners.md`
  - `docs/royal-theme-roadmap/phase-06-admin-backend-generated-ui.md`
  - `docs/royal-theme-roadmap/phase-07-visual-qa-release.md`
  - `docs/BUILD_STATE.md`
- Commands passed:
  - Documentation-only phase. No runtime validation required.
- Commands blocked:
  - None.
- Remaining risk:
  - This phase did not change runtime colors yet.
  - Real app-wide visual QA will be required after token migration because KIS has many hard-coded surface colors.
- Best next prompt:
  - Use `docs/royal-theme-roadmap/phase-01-core-theme-tokens.md`.

2026-05-13 - Phase 01
- Files changed:
  - `/Users/nigel/dev/KIS/src/theme/constants.ts`
  - `/Users/nigel/dev/KIS/src/theme/navTheme.ts`
  - `/Users/nigel/dev/KIS/src/theme/health/colors.ts`
  - `docs/royal-theme-roadmap/phase-01-core-theme-tokens.md`
  - `docs/royal-theme-roadmap/status.md`
  - `docs/BUILD_STATE.md`
- Commands passed:
  - `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
  - `cd /Users/nigel/dev/KIS && npx eslint src/theme src/constants --quiet`
  - `../env/bin/python manage.py check`
- Follow-up correction:
  - Expanded the flat gold token into a metallic gold family: highlight, light, midtone, rose-gold, deep, shadow, soft, muted, and gradient stops.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/theme src/constants --quiet`.
- Light-theme luxury correction:
  - Changed light background from plain white direction to warmer ivory/parchment.
  - Changed light `palette.bar` to deep royal purple for bottom navigation depth.
  - Changed React Navigation light `card` to deep royal purple with ivory text and gold notification.
  - Updated the bottom tab bar to use ivory/gold text contrast on the royal purple bar.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/theme src/constants src/navigation/AppNavigator.tsx --quiet`.
- Purple-first light-theme correction:
  - Moved the light app foundation toward pale royal purple instead of ivory-only.
  - Kept gold as the accent system for buttons, borders, active states, and premium highlights.
  - Kept the bottom main tab bar white from the later user correction.
  - Removed the dark-gold Messages page app-bar experiment and returned it to the purple chrome system.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/theme src/constants src/navigation/AppNavigator.tsx src/screens/tabs/MessagesScreen.tsx src/Module/ChatRoom/messagesUtils.ts --quiet`.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
- Deep-purple light-theme correction:
  - Changed light `bg`, `chrome`, and `bar` to deep royal purple `#2A0F45`.
  - Kept cards and inputs warm/light for readability.
  - Kept gold as the accent/border/active-state system.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/theme src/navigation/AppNavigator.tsx --quiet`.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
- Page-background correction:
  - Changed global light `bg` back to a very light royal purple so Bible, Broadcast, Profile, and general chat rooms are not deep purple.
  - Kept Messages main page top controls on a controlled royal-purple panel.
  - Rounded the Messages app bar top and bottom corners.
  - Made Messages top-tab text ivory/gold so it remains visible on purple.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/theme src/navigation/AppNavigator.tsx src/screens/tabs/MessagesScreen.tsx src/Module/ChatRoom/messagesUtils.ts --quiet`.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
- Gold-border correction:
  - Converted core border tokens away from purple into deep/soft gold values.
  - Removed Broadcast main header/tab/filter section container borders.
  - Kept Broadcast item/button/card borders gold-based.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/theme src/screens/tabs/BroadcastScreen.tsx src/components/broadcast/BroadcastMainTabs.tsx src/screens/broadcast/feeds/components/SectionHeader.tsx --quiet`.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
- Reference-image light-theme correction:
  - Remapped light theme to warm cream pages, coffee-brown primary controls/text accents, and tan-gold borders.
  - Kept dark theme untouched.
  - Kept bottom main tab bar white.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/theme src/navigation/AppNavigator.tsx --quiet`.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
- Metallic button/selection correction:
  - Added shiny metallic-gold gradients to shared `KISButton` primary buttons.
  - Added metallic-gold selected state to the bottom main tab indicator.
  - Added metallic-gold active state to shared messaging filter chips.
  - Shifted shared secondary button borders/text to gold instead of purple.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/constants/KISButton.tsx src/theme/foundations/buttons.ts src/components/messaging/Filters.tsx src/navigation/AppNavigator.tsx --quiet`.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
- Custom-button metallic correction:
  - Added shiny metallic-gold selected chips and floating filter button to the Bible main page.
  - Added metallic-gold treatment to the partner settings button.
  - Added metallic-gold active state to partner side account selectors, standalone channel selectors, group selectors, and community group selectors.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/screens/tabs/BibleScreen.tsx src/components/partners/center/PartnerHeaderSection.tsx src/components/partners/center/PartnerGroupsSection.tsx src/components/partners/center/PartnerChannelsSection.tsx src/components/partners/center/PartnerCommunitiesSection.tsx src/components/partners/PartnersLeftRail.tsx --quiet`.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
- Profile orange-border correction:
  - Replaced hard-coded orange Marketplace and Appointments dashboard borders with theme gold.
  - Replaced the remaining static profile management orange border with a gold value.
  - Replaced nearby partner feed and education filter orange border paths with gold token usage.
  - Verified no remaining hard-coded orange `borderColor` references under `src`.
  - Re-ran `cd /Users/nigel/dev/KIS && npx eslint src/screens/tabs/profile/components/dashboard/ProfileDashboardBlocks.tsx src/screens/tabs/profile/profile.styles.ts src/components/partners/PartnersCenterPane.tsx src/screens/broadcast/education/components/EducationFilterSheet.tsx --quiet`.
  - Re-ran `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`.
- Commands blocked:
  - None.
- Remaining risk:
  - Many individual screens still have local hard-coded colors and orange naming. Phase 02 starts shared primitives; later phases handle screens.
- Best next prompt:
  - Use `docs/royal-theme-roadmap/phase-02-shared-components.md`.
