# Phase 07 - Broadcast/Channels, Feeds, And Public Content Launch Proof

Date: 2026-05-17

## Scope

This phase verifies the launch-safe Broadcast/Channels foundation without changing normal user-facing behavior. It focuses on channel creation, channel-scoped content creation, legacy feed compatibility, subscriptions/bell state, playlists, comments, saves, watch history, broadcast/unbroadcast, public/private/unlisted visibility, embed/oEmbed safety, trust badge display, media safety gating, and report/moderation hooks.

## Implementation Completed

- Added a read-only, non-secret launch verifier:
  - `python3 manage.py verify_broadcast_channels_launch`
  - `python3 manage.py verify_broadcast_channels_launch --strict`
- Verified required Broadcast/Channels URL contracts resolve for:
  - legacy broadcast feed list;
  - channel list/create/detail;
  - subscription/bell state;
  - channel/content broadcast and unbroadcast;
  - channel contents and assets;
  - playlists and playlist items;
  - content reactions, saves, shares, views, comments, reports;
  - moderation queues/actions;
  - live-stream placeholders;
  - public landing pages;
  - embed and oEmbed endpoints.
- Added safe-default verifier coverage proving:
  - embeds are disabled by default;
  - public indexing is disabled by default;
  - referrals are disabled by default;
  - live stream provider calls are disabled by default;
  - channel media provider calls are disabled by default;
  - asset serializers do not expose raw `storage_path`;
  - quarantined/unsafe media is blocked before publish/broadcast.
- Added focused test coverage for the verifier.
- Confirmed existing focused channel tests cover:
  - visible channel creation and channel handle uniqueness;
  - channel-scoped legacy feed creation;
  - channel and content broadcast/unbroadcast idempotency;
  - owner/manager permission checks;
  - public/private/unlisted safety for landing pages and embeds;
  - signed private/unlisted embed tokens;
  - engagement counts for reactions, saves, comments, shares, and views;
  - playlist add/remove;
  - report and moderation action audit behavior;
  - analytics rollup creation.

## Safety Decisions

- Live streaming remains launch-gated.
- Public indexing remains disabled until privacy/SEO/abuse evidence is attached.
- Public referrals remain disabled until abuse-safe growth evidence is attached.
- Embeds remain disabled by default; signed/private/unlisted embed behavior is implemented and tested, but production enablement needs QA evidence.
- The verifier prints only flag states, counts, and route/proof status. It does not print secrets, private media paths, raw storage paths, private embed tokens, or provider payloads.

## Validation

Passed:

- `python3 -m py_compile apps/broadcasts/management/commands/verify_broadcast_channels_launch.py apps/broadcasts/tests.py apps/broadcasts/media_pipeline.py apps/broadcasts/serializers.py apps/broadcasts/views.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py verify_broadcast_channels_launch --include-counts`
  - 11 pass / 0 fail / 1 warning.
- `python3 manage.py test apps.broadcasts.tests.BroadcastChannelsLaunchProofCommandTests apps.broadcasts.tests.BroadcastChannelApiTests apps.broadcasts.tests.ChannelEmbedTests apps.broadcasts.tests.ChannelEngagementTests --noinput --keepdb`
  - PostgreSQL-backed: 27 tests passed.
- React Native `npm run typecheck -- --pretty false`
- React Native `npx eslint src/screens/broadcast/channels src/components/broadcast src/network/routes/broadcastRoutes.ts src/types/broadcast.ts --quiet`
- Nest `pnpm tsc --noEmit --pretty false --incremental false`

Blocked / warnings:

- `verify_broadcast_channels_launch --include-counts` could not read live channel/feed counts in this local command context due `OperationalError`.
- The first focused React Native lint command used a stale path, `src/services/channelContentApi.ts`, which does not exist. The corrected focused lint command passed.
- Realtime notification emission during tests logged local connection refused messages for the local realtime bridge; the channel API tests still passed. Staging must prove Django-to-Nest realtime badge delivery.

## Remaining Launch Evidence Needed

| Priority | Evidence gap |
|---|---|
| P0 | Staging `python3 manage.py verify_broadcast_channels_launch --strict --include-counts` with database access. |
| P0 | Real-device proof for create channel, select channel, create content in selected channel, publish, broadcast, unbroadcast, delete/archive, and legacy feed compatibility. |
| P0 | Real-device proof that channel subscriptions/bell state update badges and notification preferences correctly. |
| P0 | Staging proof that quarantined/pending-review channel assets cannot publish, broadcast, or render in embeds. |
| P0 | Embed/oEmbed staging proof with allowed domain, blocked domain, public content, private/unlisted signed token, and no raw storage path exposure. |
| P0 | Public indexing remains off in production until SEO/privacy/abuse review approves it. |
| P1 | Channel trust badge policy: decide whether launch uses inherited user/partner/institution trust only or dedicated channel/creator verification subject types. |
| P1 | Live streaming provider/player/moderation evidence is still post-launch unless separately approved. |

## Phase 08 Prompt

```text
Please implement Phase 08 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on Bible, Spiritual Growth, And KCAN Vision Launch Proof. Use Phase 00-07 evidence to verify Bible reader UX, plans, streaks/reminders, highlights, notes, comments, daily meditations, offline/low-bandwidth scripture access, KCAN/partner ministry publishing, Our Vision page behavior, child/family-safe spiritual content controls, notification badge read-state, and moderation/media safety for devotional content. Prefer PostgreSQL-backed Django tests; if Postgres, Redis, or environment setup blocks validation, record the exact blocker and move on. Preserve existing UI behavior, do not expose private data or secrets, keep unproven content publishing/public indexing flagged unless evidence exists, run safe Django/Nest/React Native validation, update docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 09.
```
