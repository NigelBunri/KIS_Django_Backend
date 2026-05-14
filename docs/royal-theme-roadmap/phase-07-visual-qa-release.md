# Phase 07 - Visual QA Release

## Goal

Finish the royal theme migration with evidence.

## Required Work

- Scan for:
  - `#FF8A33`
  - `255,138,51`
  - `orange`
  - isolated gold/beige values that should be tokens.
- Replace or document exceptions.
- Confirm accessibility contrast:
  - primary buttons;
  - text on gold;
  - dark mode surfaces;
  - chat bubbles;
  - disabled states.
- Run manual QA across core app paths.

## Validation

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- `cd /Users/nigel/dev/KIS && npx eslint . --quiet`
- `../env/bin/python manage.py check`

## Final Output

- Updated `docs/royal-theme-roadmap/status.md`.
- Updated `docs/BUILD_STATE.md`.
- Final remaining hard-coded color exception list.
- Final go/no-go recommendation for release.
