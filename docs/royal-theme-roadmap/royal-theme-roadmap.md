# KIS Royal Gold + Purple Theme Roadmap

Status: Phase 00 completed 2026-05-13. Phase 01 is next.

## Product Direction

KIS should feel like a premium royal platform: gold for prestige and primary actions, deep purple for power and depth, warm ivory for light surfaces, and rich ink colors for authority. The app should feel luxurious without becoming noisy, dark, or hard to read.

This roadmap changes the full system in phases so each model/session can continue without confusion.

## Phase 00 - Analysis And Plan

Status: completed.

Findings:

- The app is currently orange + purple at the token level.
- The biggest leverage point is `/Users/nigel/dev/KIS/src/theme/constants.ts`.
- The navigation theme and health theme duplicate orange and must be updated with the core theme.
- Many screens already use semantic palette keys, so a token migration should update large parts of the app safely.
- Recent channel/feed UI contains hard-coded gold-like values. These should become named tokens in Phase 01/02.

## Phase 01 - Core Theme Tokens And Navigation

Purpose: replace the orange brand foundation with royal gold + purple without changing screen layouts.

Files:

- `/Users/nigel/dev/KIS/src/theme/constants.ts`
- `/Users/nigel/dev/KIS/src/theme/navTheme.ts`
- `/Users/nigel/dev/KIS/src/theme/health/colors.ts`
- `/Users/nigel/dev/KIS/src/theme/useTheme.ts`
- `/Users/nigel/dev/KIS/src/theme/foundations/icons.ts`
- `/Users/nigel/dev/KIS/src/theme/foundations/buttons.ts`

Required work:

- Introduce canonical royal tokens:
  - `gold`;
  - `goldDeep`;
  - `goldSoft`;
  - `purple`;
  - `purpleDeep`;
  - `purpleSoft`;
  - `ivory`;
  - `parchment`;
  - `royalInk`.
- Keep compatibility aliases:
  - `orange` may remain only as deprecated alias pointing to gold for old imports.
  - `primary` should become gold.
  - `secondary` should remain purple.
  - `gradientStart` should become gold.
  - `gradientEnd` should become purple.
- Update light and dark palettes:
  - light `bg`: warm ivory, not stark white;
  - light `card`: refined warm surface;
  - dark `bg`: deep royal ink/plum;
  - dark `card`: deep purple-black.
- Update chat bubble colors to gold-tinted outgoing and purple-tinted incoming while preserving readability.
- Update nav theme primary/notification colors.
- Update health theme `accentPrimary`/`primary` from orange to gold.

Validation:

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- `cd /Users/nigel/dev/KIS && npx eslint src/theme src/constants --quiet`

## Phase 02 - Shared Components And Primitives

Purpose: ensure common UI components express the royal theme everywhere.

Files:

- `/Users/nigel/dev/KIS/src/constants/KISButton.tsx`
- `/Users/nigel/dev/KIS/src/constants/KISTextInput.tsx`
- `/Users/nigel/dev/KIS/src/constants/KISDateTimeInput.tsx`
- `/Users/nigel/dev/KIS/src/constants/TextCardComposer.tsx`
- `/Users/nigel/dev/KIS/src/components/common/*`
- `/Users/nigel/dev/KIS/src/components/verification/*`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/*`

Required work:

- Buttons:
  - primary = gold fill with deep ink text or white only when contrast requires;
  - secondary = purple outline/fill;
  - destructive remains red;
  - disabled stays neutral.
- Inputs:
  - ivory card background;
  - gold focus border;
  - purple helper/error accent where appropriate.
- Chips/pills:
  - gold selected state;
  - purple secondary state.
- Composer:
  - no orange hard-coded values;
  - use royal token aliases.

Validation:

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- `cd /Users/nigel/dev/KIS && npx eslint src/constants src/components/common src/components/verification src/components/feeds/composer --quiet`

## Phase 03 - App Shell, Auth, Profile, Dashboard, Messaging

Purpose: migrate high-traffic surfaces first.

Files:

- `/Users/nigel/dev/KIS/App.tsx`
- `/Users/nigel/dev/KIS/src/screens/LoginScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/RegisterScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.styles.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.constants.ts`
- `/Users/nigel/dev/KIS/src/screens/MessagesScreen.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/**/*`
- `/Users/nigel/dev/KIS/src/components/messages/**/*`

Required work:

- Replace orange hard-coded accents with royal tokens.
- Ensure light theme left panels and headers are not dark/unclear.
- Messaging:
  - outgoing bubble gold-tinted;
  - incoming bubble purple-tinted;
  - status/read indicators use purple/gold hierarchy.
- Profile/dashboard:
  - premium cards and stats should use gold borders and purple depth.

Validation:

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- Focused lint for touched files.
- Manual QA: login/register, profile, messages list, chat room.

## Phase 04 - Broadcast, Channels, Feed Studio

Purpose: align the Feed Channels work with the royal brand.

Files:

- `/Users/nigel/dev/KIS/src/screens/tabs/BroadcastScreen.tsx`
- `/Users/nigel/dev/KIS/src/components/broadcast/BroadcastMainTabs.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelsDiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelHomePage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelContentDetailPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/*`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/FeedManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/feeds/*`

Required work:

- Replace hard-coded local gold values with centralized tokens from Phase 01.
- Make channel discovery, channel home, detail view, and Studio all feel like one royal system.
- Keep the new Channel Studio behavior:
  - create channel;
  - create in channel;
  - broadcast/unbroadcast channel/content.
- Ensure broadcast workspace feels luxurious, not beige-only.

Validation:

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- `cd /Users/nigel/dev/KIS && npx eslint src/screens/broadcast src/components/broadcast src/screens/tabs/profile-screen/FeedManagementModal.tsx --quiet`
- Manual QA: Broadcast tabs, Channels list, Channel Studio, feed detail.

## Phase 05 - Commerce, Education, Health, Partners

Purpose: move business/institution surfaces into the royal system.

Files:

- `/Users/nigel/dev/KIS/src/screens/market/**/*`
- `/Users/nigel/dev/KIS/src/components/market/**/*`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/**/*`
- `/Users/nigel/dev/KIS/src/screens/health/**/*`
- `/Users/nigel/dev/KIS/src/theme/health/colors.ts`
- `/Users/nigel/dev/KIS/src/components/partners/**/*`
- `/Users/nigel/dev/KIS/src/screens/partners/**/*`

Required work:

- Commerce: USD/payment flows must use gold for primary purchase/checkout emphasis and purple for trust/secondary actions.
- Education: learning cards should use royal academic styling.
- Health: keep healthcare clarity while replacing orange with gold.
- Partners: company workspace should feel executive and premium.

Validation:

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- Focused lint for touched module directories.
- Manual QA: market checkout, education management, health management, partner workspace.

## Phase 06 - Admin Backend Generated UI And Docs

Purpose: ensure admin/generated surfaces and docs do not contradict the brand.

Files:

- `admin_system/ROADMAP.md`
- `admin_system/IMPLEMENTATION_STATE.md`
- `admin_system/*`
- Backend templates/static files if present.
- Docs screenshots/checklists if they mention orange branding.

Required work:

- Replace orange branding references in admin UI plans.
- If any backend-served HTML/static admin UI exists, update its CSS tokens.
- Keep Django admin default if no custom admin theme is active.

Validation:

- `../env/bin/python manage.py check`
- Static/template scan for old orange values.

## Phase 07 - Hard-Coded Color Cleanup, Accessibility, QA

Purpose: finish the migration with confidence.

Required work:

- Search for old orange values:
  - `#FF8A33`
  - `255,138,51`
  - `orange`
  - old orange gradient usage.
- Replace with semantic tokens or documented exceptions.
- Search for isolated hard-coded gold/beige values and centralize them.
- Confirm contrast:
  - primary buttons;
  - text on gold;
  - dark mode cards;
  - chat bubbles;
  - disabled states.
- Run mobile visual QA across major screens.

Validation:

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- `cd /Users/nigel/dev/KIS && npx eslint . --quiet`
- `../env/bin/python manage.py check`
- Manual QA checklist attached to `docs/royal-theme-roadmap/status.md`.

## Immediate Best Next Prompt

```text
Please implement Phase 01 of the KIS Royal Gold + Purple theme roadmap without using git commands. Focus on core theme tokens and navigation only. Replace the orange-first brand foundation with centralized royal gold + deep purple tokens in `/Users/nigel/dev/KIS/src/theme/constants.ts`, update navigation theme in `/Users/nigel/dev/KIS/src/theme/navTheme.ts`, update health theme colors in `/Users/nigel/dev/KIS/src/theme/health/colors.ts`, keep backward-compatible aliases so existing screens do not break, and do not redesign individual screens yet. Run `npm run typecheck -- --pretty false` and focused lint for `src/theme`/`src/constants`, record blockers, and update `docs/royal-theme-roadmap/status.md` and `docs/BUILD_STATE.md`.
```
