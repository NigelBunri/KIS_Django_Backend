# KIS Channels Launch QA Checklist

Last updated: 2026-05-13

## Status

Current recommendation: **NO-GO for public production launch until real staging/device evidence is attached**.

The implementation is ready for staging QA. Local validation evidence was captured on 2026-05-13, but this is not a substitute for real staging migration/backfill and iOS/Android device evidence.

## 2026-05-13 Local Evidence Captured

- [x] Local migrations applied with `python3 manage.py migrate`.
- [x] `python3 manage.py check` passed.
- [x] `python3 manage.py makemigrations --check --dry-run` passed.
- [x] Focused backend Channels suite passed: 27 tests.
- [x] `python3 manage.py backfill_broadcast_channels --dry-run --limit 500` passed locally:
  - `mode=DRY-RUN profiles_seen=2 profiles_changed=2 entries_seen=3 entries_backfilled=3 channels_created=2 content_created=3 content_updated=0 broadcast_items_linked=0 skipped_invalid_entries=0 errors=0`
- [x] React Native `npm run typecheck -- --pretty false` passed after fixing the existing `EducationManagementModal.tsx` helper signature blocker.
- [x] Focused frontend lint passed:
  - `npx eslint src/screens/broadcast/channels src/screens/broadcast/feeds src/components/broadcast src/components/feeds src/screens/tabs/profile-screen/EducationManagementModal.tsx --quiet`
- [ ] Real staging migrations are not proven in this local workspace.
- [ ] Real staging `--apply` backfill was not run because dry-run counts require explicit acceptance on the target staging database.
- [ ] iOS/Android manual QA is not proven in this local workspace.
- [ ] Embed/live production flag evidence is not attached.

## Required Backend Evidence

- [x] Run and attach `python3 manage.py check`.
- [x] Run and attach `python3 manage.py makemigrations --check --dry-run`.
- [x] Run and attach focused backend tests:
  - `python3 manage.py test apps.broadcasts.tests.BroadcastChannelModelTests --noinput --keepdb`
  - `python3 manage.py test apps.broadcasts.tests.BroadcastChannelApiTests --noinput --keepdb`
  - `python3 manage.py test apps.broadcasts.tests.ChannelContentCompatibilityTests --noinput --keepdb`
  - `python3 manage.py test apps.broadcasts.tests.ChannelEmbedTests --noinput --keepdb`
  - `python3 manage.py test apps.broadcasts.tests.ChannelEngagementTests --noinput --keepdb`
  - `python3 manage.py test apps.broadcasts.tests.ChannelBackfillTests --noinput --keepdb`
- [ ] Apply all channel migrations in staging.
- [ ] Run `python3 manage.py backfill_broadcast_channels --dry-run --limit 500` in staging and attach reviewed counts.
- [ ] After approval, run `python3 manage.py backfill_broadcast_channels --apply --limit 500` in staging.
- [ ] Re-run the backfill command in staging to prove idempotency.
- [ ] Run `python3 manage.py rollup_channel_analytics --date YYYY-MM-DD` in staging on a known test date.

## Required Frontend Evidence

- [x] Run `npm run typecheck`.
- [x] Run `npx eslint src/screens/broadcast/channels src/screens/broadcast/feeds src/components/broadcast src/components/feeds --quiet`.
- [x] Verify no Channels-related type or lint failures remain.
- [ ] iOS device QA completed.
- [ ] Android device QA completed.
- [ ] Slow network QA completed.
- [ ] Offline/retry state QA completed.

## Manual Product QA

- [ ] Channel discovery loads, filters, and searches.
- [ ] Channel home opens from discovery.
- [ ] Channel banner, avatar, handle, badges, subscriber count, and content count render correctly.
- [ ] Subscribe and bell preference update without duplicate subscriptions.
- [ ] Content detail opens for:
  - [ ] video;
  - [ ] short video;
  - [ ] image;
  - [ ] gallery;
  - [ ] rich text;
  - [ ] audio;
  - [ ] document;
  - [ ] poll;
  - [ ] event;
  - [ ] live stream placeholder/replay.
- [ ] Comments load and post.
- [ ] Like/save/share/view counts update.
- [ ] Report content creates moderation queue entries.
- [ ] Channel Studio loads owned channels.
- [ ] Channel Studio content manager loads normalized and legacy content.
- [ ] Composer preserves old feed payloads while passing channel-ready fields.
- [ ] Publish, schedule, unpublish, archive/delete behavior is correct.
- [ ] Playlist rail displays public playlists.
- [ ] Live scheduling/start/end placeholder works with provider disabled.
- [ ] Embed API remains disabled unless `KIS_EMBEDS_ENABLED=True` is intentionally configured.
- [ ] Public embed response does not expose private storage paths or token hashes.

## Moderation And Safety QA

- [ ] Channel report creates `ChannelModerationRecord`.
- [ ] Content report creates `ChannelModerationRecord`.
- [ ] Comment report creates `ChannelModerationRecord`.
- [ ] Keep action dismisses the record.
- [ ] Hide action makes content private.
- [ ] Remove action archives/removes target content or comment.
- [ ] Restrict comments blocks non-manager comments.
- [ ] Admin can inspect channel moderation records.
- [ ] `apps.moderation.AuditLog` records moderation actions.

## Launch Configuration

- [ ] `KIS_EMBEDS_ENABLED=False` unless embed QA/legal approval is complete.
- [ ] `LIVE_STREAM_PROVIDER=disabled` unless provider sandbox QA is complete.
- [ ] Live provider credentials are absent from production unless explicitly approved.
- [ ] Public API host and mobile API URL point to the same staging/prod environment.
- [ ] Media URLs do not expose private storage paths.
- [ ] Notification worker/queue is running if content/live notifications are enabled.
- [ ] Analytics rollup command is scheduled, or explicitly documented as manual-only.

## Rollback

If production issues appear:

1. Disable/rollback the Channels tab or entry point through the frontend release/feature flag if available.
2. Keep legacy feed endpoints active.
3. Set `KIS_EMBEDS_ENABLED=False`.
4. Set `LIVE_STREAM_PROVIDER=disabled`.
5. Stop any scheduled backfill job.
6. Do not delete normalized channel rows; old JSON feed entries are still preserved.
7. Re-run old broadcast feed smoke tests.
8. Record the incident in `docs/BUILD_STATE.md` or the production incident log.

## Final Go/No-Go Rule

Go only when:

- backend checks and focused tests pass in staging;
- frontend typecheck and focused lint pass or blockers are accepted in writing;
- backfill dry-run and staging apply evidence are attached;
- manual QA is complete on iOS and Android;
- embed/live provider flags are intentionally configured;
- rollback owner and rollback command path are confirmed.
