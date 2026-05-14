# Phase 12 - QA And Launch Runbook

Purpose: finish launch confidence for KIS Channels.

## Files To Change

- New: `docs/operations/KIS_CHANNELS_LAUNCH_QA_CHECKLIST.md`
- `docs/feed-channels-roadmap/status.md`
- `docs/BUILD_STATE.md`
- Add or update focused tests in backend/frontend where gaps are found.

## Backend QA Matrix

Run or document blockers:

```bash
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
python3 manage.py test apps.broadcasts.tests.BroadcastChannelModelTests --noinput
python3 manage.py test apps.broadcasts.tests.BroadcastChannelApiTests --noinput
python3 manage.py test apps.broadcasts.tests.ChannelContentCompatibilityTests --noinput
python3 manage.py test apps.broadcasts.tests.ChannelEmbedTests --noinput
python3 manage.py test apps.broadcasts.tests.ChannelModerationAnalyticsTests --noinput
python3 manage.py test apps.broadcasts.tests.ChannelBackfillTests --noinput
```

## Frontend QA Matrix

```bash
cd /Users/nigel/dev/KIS
npm run typecheck
npx eslint src/screens/broadcast/channels src/screens/broadcast/feeds src/components/broadcast src/components/feeds --quiet
```

Manual QA:

- channel discovery search/filter;
- channel home banner/avatar/subscribe;
- content detail for video, short video, image, rich text, audio, document, poll, event;
- creator studio create/edit/publish/schedule/unpublish/delete;
- live schedule/start/end placeholder or provider sandbox;
- playlist create/reorder;
- embed copy and external iframe preview;
- report/hide/mute;
- notification bell;
- offline/slow network states;
- iOS and Android safe-area checks.

## Production Launch Checklist

- DB migrations applied in staging.
- Backfill dry-run reviewed.
- Backfill applied in staging and idempotency confirmed.
- Public channel URLs/handles confirmed.
- Embed domains and policy approved.
- Live provider disabled or staging-only until provider QA.
- Private media remains private.
- Moderation admin queue tested.
- Rollback documented:
  - disable channels tab from frontend feature flag if available;
  - disable embeds with `KIS_EMBEDS_ENABLED=False`;
  - disable live provider with `LIVE_STREAM_PROVIDER=disabled`;
  - keep old feed endpoints active.

## ChatGPT Prompt

```text
Please implement Phase 12 of KIS Feed Channels without using git commands. Create the final launch QA checklist, run lightweight backend/frontend validation where possible, record blockers exactly, add only low-risk tests/docs needed for launch confidence, and update docs/feed-channels-roadmap/status.md and docs/BUILD_STATE.md with final go/no-go status.
```

