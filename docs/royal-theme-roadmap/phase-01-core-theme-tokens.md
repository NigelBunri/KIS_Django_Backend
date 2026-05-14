# Phase 01 - Core Theme Tokens And Navigation

Status: Completed on 2026-05-13.

## Goal

Replace the orange-first foundation with royal gold + deep purple while preserving app behavior.

## Files To Change

- `/Users/nigel/dev/KIS/src/theme/constants.ts`
- `/Users/nigel/dev/KIS/src/theme/navTheme.ts`
- `/Users/nigel/dev/KIS/src/theme/health/colors.ts`
- `/Users/nigel/dev/KIS/src/theme/useTheme.ts` only if type exports need adjustment.
- `/Users/nigel/dev/KIS/src/theme/foundations/icons.ts` only if icon tones hard-code orange.
- `/Users/nigel/dev/KIS/src/theme/foundations/buttons.ts` only if button tones hard-code orange.

## Required Implementation

- Add royal metallic-gold tokens to `KIS_COLORS.brand`:
  - `goldHighlight: '#FFF4B8'`
  - `goldLight: '#F4D77A'`
  - `gold: '#C9A24A'`
  - `goldRose: '#D6B15E'`
  - `goldDeep: '#9A6A14'`
  - `goldShadow: '#5E3B0A'`
  - `goldSoft: '#FFF2C7'`
  - `goldMuted: '#E6D7B2'`
  - `goldGradientStart: '#FFF4B8'`
  - `goldGradientMid: '#C9A24A'`
  - `goldGradientEnd: '#8A5A12'`
  - `purple: '#4B1D78'`
  - `purpleDeep: '#2A0F45'`
  - `purpleSoft: '#EEE4FA'`
  - `imperialPurple: '#6E35B7'`
  - `royalInk: '#17111F'`
  - `ivory: '#FFFBF2'`
  - `parchment: '#F8F1E3'`
- Make `primary` equal a contrast-safe deep royal gold.
- Make `secondary` equal deep purple.
- Keep `orange` as a deprecated alias to `gold` for compatibility.
- Change `gradientStart` to gold and `gradientEnd` to purple.
- Update light surfaces to warm ivory/parchment.
- Update dark surfaces to royal ink/deep purple.
- Update `primarySoft`, `primaryStrong`, `chatBg`, `outgoingBubble`, `incomingBubble`, `readStatus`.
- Update `navTheme.ts`:
  - primary = gold;
  - notification = purple.
- Update `health/colors.ts`:
  - `accentPrimary` and `primary` = gold;
  - `accentSecondary` = purple.

## Do Not Do Yet

- Do not redesign individual screens.
- Do not remove compatibility aliases.
- Do not change business logic.

## Validation

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false` passed.
- `cd /Users/nigel/dev/KIS && npx eslint src/theme src/constants --quiet` passed.
- `../env/bin/python manage.py check` passed.

## Files Changed

- `/Users/nigel/dev/KIS/src/theme/constants.ts`
- `/Users/nigel/dev/KIS/src/theme/navTheme.ts`
- `/Users/nigel/dev/KIS/src/theme/health/colors.ts`
- `docs/royal-theme-roadmap/phase-01-core-theme-tokens.md`
- `docs/royal-theme-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Notes

- `orange` remains as a deprecated compatibility alias mapped to deep royal gold, so legacy screens do not break while later phases remove orange naming.
- This phase intentionally did not redesign individual screens.
- Gold is now a metallic range for later UI phases, not one flat brand color.
- Light mode was refined so the app does not feel like plain white with gold placed on top:
  - page background is warm ivory;
  - card background is soft ivory;
  - navigation chrome/bar uses deep royal purple;
  - bottom-tab active icon uses metallic gold with royal-ink icon contrast.

## Best Prompt For Phase 02

```text
Please implement Phase 02 of the KIS Royal Gold + Purple theme roadmap without using git commands. Focus on shared React Native components and primitives only. Update KISButton, KISTextInput, KISDateTimeInput, TextCardComposer, common cards/chips/modals, verification components, and feed composer primitives to consume the new royal gold + purple palette instead of orange or hard-coded local colors. Keep behavior unchanged, preserve accessibility contrast, run `npm run typecheck -- --pretty false` and focused lint for touched shared component folders, record blockers, and update `docs/royal-theme-roadmap/status.md` and `docs/BUILD_STATE.md`.
```
