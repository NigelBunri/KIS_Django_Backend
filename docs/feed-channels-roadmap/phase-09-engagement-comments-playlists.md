# Phase 09 - Engagement, Comments, Playlists

Purpose: make channels feel complete: comments, playlists, saves, reactions, shares, watch history, and subscriptions.

## Files To Change

Backend:

- `apps/broadcasts/models.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/urls.py`
- `apps/broadcasts/tests.py`
- possibly Nest chat/comment bridge files if comments remain in Nest.

Frontend:

- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelHomePage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelContentDetailPage.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/components/ChannelCommentsPanel.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/components/PlaylistRail.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/components/SubscribeBellButton.tsx`

## Backend Models

Add if not already covered:

- `ChannelContentComment` or bridge to existing conversation/comment system.
- `BroadcastPlaylistItem`
- `ChannelContentSave`
- `ChannelWatchHistory`

Minimum playlist item:

```python
class BroadcastPlaylistItem(models.Model):
    playlist = models.ForeignKey(BroadcastPlaylist, on_delete=models.CASCADE, related_name="items")
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="playlist_items")
    sort_order = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("playlist", "content")]
```

## API Endpoints

- `POST /channel-contents/<id>/react/`
- `POST /channel-contents/<id>/save/`
- `DELETE /channel-contents/<id>/save/`
- `POST /channel-contents/<id>/share/`
- `POST /channel-contents/<id>/view/`
- `GET /channel-contents/<id>/comments/`
- `POST /channel-contents/<id>/comments/`
- `POST /playlists/<id>/items/`
- `DELETE /playlists/<id>/items/<content_id>/`

## Frontend Requirements

- Subscribe button shows subscribed state and notification bell menu.
- Comments panel supports loading, posting, retry, empty state.
- Playlist rail supports horizontal cards and vertical see-all.
- Detail viewer should update counts optimistically but reconcile with backend.
- Share should only count after native share result is actually completed where possible.

## Tests

Backend:

- idempotent view/share events;
- playlist item add/remove;
- saved content idempotency;
- comments require auth for writing;
- public can read comments if content comments are enabled.

Frontend:

- subscribe button state;
- playlist rail renders;
- detail action counts update.

## Validation

```bash
python3 manage.py check
cd /Users/nigel/dev/KIS
npx eslint src/screens/broadcast/channels --quiet
npm run typecheck
```

## ChatGPT Prompt

```text
Please implement Phase 09 of KIS Feed Channels without using git commands. Add durable channel content engagement, comments, playlists, saves, watch history, and subscription bell behavior. Keep existing broadcast engagement endpoints working. Add frontend comments panel, playlist rail, and subscribe bell UI. Run safe validation and update status docs.
```

