# Phase 06 - Admin Backend Generated UI

## Goal

Ensure non-mobile surfaces and documentation do not contradict the new brand.

## Files To Inspect First

- `admin_system/ROADMAP.md`
- `admin_system/IMPLEMENTATION_STATE.md`
- `admin_system`
- Backend templates/static files if present.
- Docs that mention orange branding.

## Required Work

- Update custom admin/generated UI plans and styles from orange to royal gold + purple.
- Do not change Django admin behavior unless a custom admin theme exists.
- Keep operational docs accurate.

## Validation

- `../env/bin/python manage.py check`
- Scan for old orange values and record remaining exceptions.

## Best Prompt For Phase 07

```text
Please implement Phase 07 of the KIS Royal Gold + Purple theme roadmap without using git commands. Focus on hard-coded color cleanup, accessibility, and release QA. Scan the full React Native app and backend docs/templates for old orange values and inconsistent local gold/beige values, centralize or replace them with royal theme tokens, verify contrast for primary buttons, dark mode, cards, chat bubbles, disabled states, and key flows, run full typecheck/lint where safe plus manage.py check, record blockers, and update docs/royal-theme-roadmap/status.md and docs/BUILD_STATE.md with final go/no-go evidence.
```
