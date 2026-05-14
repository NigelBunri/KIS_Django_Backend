# Phase 14 - Channel And Content Broadcast

Purpose: support both broadcasting individual feed/channel content and broadcasting/promoting a whole channel.

## Required Behavior

- Individual content can be broadcast and unbroadcast.
- A whole channel can be broadcast/promoted as a discovery unit.
- Broadcast state is visible in Channel Studio.
- Legacy feed broadcast/unbroadcast still works.
- Broadcast feed list can include channel promotions without breaking old clients.
- Broadcasting is idempotent.

## Files To Change

- `apps/broadcasts/models.py` if a new source type is needed.
- `apps/broadcasts/views.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/urls.py`
- `apps/broadcasts/tests.py`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/hooks/useChannelsData.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelContentManager.tsx`

## Validation

```bash
python3 manage.py check
python3 manage.py test apps.broadcasts.tests.BroadcastChannelApiTests apps.broadcasts.tests.ChannelContentCompatibilityTests --noinput --keepdb
cd /Users/nigel/dev/KIS
npm run typecheck -- --pretty false
npx eslint src/screens/broadcast/channels src/network/routes/broadcastRoutes.ts --quiet
```

## ChatGPT Prompt

```text
Please implement Phase 14 of the KIS Feed Channels 200% YouTube roadmap without using git commands. Focus on channel and content broadcast semantics. Add safe backend APIs to broadcast/unbroadcast a whole channel and normalized channel content while preserving legacy feed broadcast behavior. Show broadcast status and actions in Channel Studio and content manager. Ensure idempotency, compatibility with old feed list responses, focused tests, validation, and docs updates.
```
