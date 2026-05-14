# Phase 00 - Analysis And Plan

Status: Completed on 2026-05-13.

## Purpose

Create a dedicated roadmap for moving KIS from the current orange + purple brand into a luxury royal gold + deep purple design system without breaking the app or scattering one-off color edits.

## Findings

- The current brand foundation is orange-first:
  - `/Users/nigel/dev/KIS/src/theme/constants.ts` sets `KIS_COLORS.brand.primary`, `brand.orange`, and `gradientStart` to `#FF8A33`.
  - `/Users/nigel/dev/KIS/src/theme/navTheme.ts` uses `KIS_COLORS.brand.orange` as navigation primary.
  - `/Users/nigel/dev/KIS/src/theme/health/colors.ts` uses `#FF8A33` for health `primary` and `accentPrimary`.
- Purple already exists as a secondary brand color, but it needs to become deeper and more regal.
- Many screens consume `palette.primary`, `palette.primaryStrong`, `palette.primarySoft`, `palette.secondary`, and shared theme values. This makes a token-first migration the safest path.
- Recent Broadcast/Channels work includes local hard-coded gold and parchment values. These should be centralized into tokens instead of duplicated per screen.
- The backend mostly needs documentation/admin/generated UI alignment; the main visual system is in the React Native app.

## Target Direction

- Primary brand: royal gold.
- Secondary/depth brand: deep royal purple.
- Light surfaces: warm ivory/parchment balanced by purple structure.
- Dark surfaces: deep royal ink/plum with gold accents.
- No orange as a user-facing brand color after the migration.
- Preserve red/success/warning semantics so safety and payment states stay clear.

## Files Reviewed

- `/Users/nigel/dev/KIS/src/theme/constants.ts`
- `/Users/nigel/dev/KIS/src/theme/useTheme.ts`
- `/Users/nigel/dev/KIS/src/theme/navTheme.ts`
- `/Users/nigel/dev/KIS/src/theme/health/colors.ts`
- `/Users/nigel/dev/KIS/src/constants/KISButton.tsx`
- `/Users/nigel/dev/KIS/src/constants/KISTextInput.tsx`
- `/Users/nigel/dev/KIS/src/constants/TextCardComposer.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.styles.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/constants.ts`
- `/Users/nigel/dev/KIS/src/screens/market/market.styles.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelsDiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/FeedManagementModal.tsx`
- `admin_system/ROADMAP.md`
- `admin_system/IMPLEMENTATION_STATE.md`
- `apps/health_dashboard/`

## Validation

Documentation-only phase. No runtime validation required.

## Next Prompt

```text
Please implement Phase 01 of the KIS Royal Gold + Purple theme roadmap without using git commands. Focus on core theme tokens and navigation only. Replace the orange-first brand foundation with centralized royal gold + deep purple tokens in `/Users/nigel/dev/KIS/src/theme/constants.ts`, update navigation theme in `/Users/nigel/dev/KIS/src/theme/navTheme.ts`, update health theme colors in `/Users/nigel/dev/KIS/src/theme/health/colors.ts`, keep backward-compatible aliases so existing screens do not break, and do not redesign individual screens yet. Run `npm run typecheck -- --pretty false` and focused lint for `src/theme`/`src/constants`, record blockers, and update `docs/royal-theme-roadmap/status.md` and `docs/BUILD_STATE.md`.
```
