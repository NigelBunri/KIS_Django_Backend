# Phase 10 - Moderation, Analytics, Notifications

Purpose: add safety and creator visibility needed for a global-standard channel platform.

## Files To Change

Backend:

- `apps/broadcasts/models.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/admin.py`
- `apps/broadcasts/tests.py`
- `apps/notifications/*` if central notification app exists
- `apps/moderation/*` if existing moderation app is used

Frontend:

- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelAnalyticsPanel.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelModerationPanel.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelContentDetailPage.tsx`

## Backend Features

Moderation:

- report channel;
- report content;
- report comment;
- hide content for viewer;
- mute channel;
- block creator/user;
- staff moderation queue;
- channel owner comment moderation queue;
- audit log for publish/unpublish/delete/report/moderation actions.

Analytics:

- daily rollups for channel and content:
  - views, unique viewers, impressions, watch time, average duration, subscribers gained/lost, shares, saves, comments, embed impressions, live peak viewers.
- management command:
  - `python3 manage.py rollup_channel_analytics --date YYYY-MM-DD`

Notifications:

- subscriber notifications for published content;
- live stream starting soon;
- live stream started;
- creator notification for moderation/report status.

## Frontend

Analytics panel:

- compact dashboard cards;
- trend chart placeholders;
- top content table;
- audience sources including embed/external.

Moderation panel:

- reported content list;
- reported comments;
- action buttons: keep, hide, remove, restrict comments.

## Validation

```bash
python3 manage.py makemigrations broadcasts
python3 manage.py check
python3 manage.py test apps.broadcasts.tests.ChannelModerationAnalyticsTests --noinput
cd /Users/nigel/dev/KIS
npx eslint src/screens/broadcast/channels/studio --quiet
npm run typecheck
```

## ChatGPT Prompt

```text
Please implement Phase 10 of KIS Feed Channels without using git commands. Add channel/content/comment moderation, admin-visible audit records, channel analytics rollups, notification hooks for subscriptions/live events, and React Native Studio analytics/moderation panels. Preserve existing moderation and notification systems where present. Update status docs.
```

