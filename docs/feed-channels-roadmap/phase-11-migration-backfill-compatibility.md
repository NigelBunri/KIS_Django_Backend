# Phase 11 - Migration, Backfill, Compatibility

Purpose: move old broadcast feed entries into channels/content without breaking old clients.

## Files To Change

- `apps/broadcasts/management/commands/backfill_broadcast_channels.py`
- `apps/broadcasts/feed_entry_store.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/tests.py`
- `docs/feed-channels-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Backfill Command

Create management command:

```bash
python3 manage.py backfill_broadcast_channels --dry-run
python3 manage.py backfill_broadcast_channels --apply --limit 500
```

Command should:

1. Find users with `BroadcastFeedProfile`.
2. Create a default personal `BroadcastChannel` for each user if missing.
3. Convert each JSON feed entry into `ChannelContent`.
4. Link matching `BroadcastItem` rows by source type/id.
5. Create `ChannelContentAsset` rows from attachments.
6. Store `legacy_feed_entry_id`.
7. Not delete old JSON entries.
8. Be idempotent.

## Compatibility Requirements

- Existing `/broadcasts/profiles/feeds/` endpoints continue to work.
- Existing `/broadcasts/` feed list continues to include legacy and channel content.
- New channel APIs should prefer normalized `ChannelContent`.
- Feed detail should resolve both old `BroadcastItem` and new `ChannelContent`.

## Safety

- Default to `--dry-run`.
- Print counts only, not raw user private info.
- Batch by limit.
- Use transactions per profile/channel, not one giant transaction.
- Record errors and continue.

## Tests

- backfill dry-run creates nothing;
- apply creates channel and content;
- apply twice does not duplicate;
- legacy feed API still returns entries;
- normalized channel content endpoint sees migrated content.

## Validation

```bash
python3 manage.py check
python3 manage.py backfill_broadcast_channels --dry-run
python3 manage.py test apps.broadcasts.tests.ChannelBackfillTests --noinput
```

## ChatGPT Prompt

```text
Please implement Phase 11 of KIS Feed Channels without using git commands. Add an idempotent dry-run-first management command to backfill existing BroadcastFeedProfile JSON feed entries into BroadcastChannel/ChannelContent/ChannelContentAsset while preserving all old APIs. Add focused compatibility tests and update status docs.
```

