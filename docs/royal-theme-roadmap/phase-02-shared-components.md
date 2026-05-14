# Phase 02 - Shared Components And Primitives

## Goal

Make shared UI components express the royal palette consistently so downstream screens inherit the new look.

## Files To Inspect First

- `/Users/nigel/dev/KIS/src/constants/KISButton.tsx`
- `/Users/nigel/dev/KIS/src/constants/KISTextInput.tsx`
- `/Users/nigel/dev/KIS/src/constants/KISDateTimeInput.tsx`
- `/Users/nigel/dev/KIS/src/constants/TextCardComposer.tsx`
- `/Users/nigel/dev/KIS/src/components/common`
- `/Users/nigel/dev/KIS/src/components/verification`
- `/Users/nigel/dev/KIS/src/components/feeds/composer`

## Required Work

- Replace hard-coded orange with `palette.primary`, `palette.primaryStrong`, or named royal tokens.
- Use gold for primary button/action emphasis.
- Use purple for secondary or brand-depth elements.
- Inputs should have gold focus/active border and royal-purple helper states where appropriate.
- Chips, segmented controls, modals, and cards must use shared tokens rather than one-off gold/beige constants.

## Validation

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- `cd /Users/nigel/dev/KIS && npx eslint src/constants src/components/common src/components/verification src/components/feeds/composer --quiet`

## Best Prompt For Phase 03

```text
Please implement Phase 03 of the KIS Royal Gold + Purple theme roadmap without using git commands. Focus on app shell, auth, profile/dashboard, and messaging surfaces. Replace remaining orange or mismatched hard-coded colors in App.tsx, LoginScreen, RegisterScreen, ProfileScreen, profile styles/constants, MessagesScreen, ChatRoom modules, and message components with the royal theme tokens. Keep behavior unchanged, keep light theme readable, run focused typecheck/lint, document blockers, and update docs/royal-theme-roadmap/status.md and docs/BUILD_STATE.md.
```
