# Phase 03 - Backend Channel APIs

Purpose: expose YouTube-style channel APIs without breaking existing broadcast endpoints.

## Files To Change

- `apps/broadcasts/views.py`
- `apps/broadcasts/urls.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/tests.py`
- `docs/feed-channels-roadmap/status.md`
- `docs/BUILD_STATE.md`

## API Endpoints To Add

Add under `/api/v1/broadcasts/channels/`:

- `GET /channels/` public channel discovery
- `POST /channels/` create channel for authenticated user
- `GET /channels/<handle_or_id>/` channel detail
- `PATCH /channels/<id>/` update channel, owner/manager only
- `POST /channels/<id>/subscribe/`
- `DELETE /channels/<id>/subscribe/`
- `PATCH /channels/<id>/subscription/` set notification bell
- `GET /channels/<id>/contents/`
- `POST /channels/<id>/contents/`
- `GET /channels/<id>/playlists/`
- `POST /channels/<id>/playlists/`

Add content endpoints:

- `GET /channel-contents/<id>/`
- `PATCH /channel-contents/<id>/`
- `DELETE /channel-contents/<id>/`
- `POST /channel-contents/<id>/publish/`
- `POST /channel-contents/<id>/unpublish/`
- `POST /channel-contents/<id>/schedule/`
- `POST /channel-contents/<id>/assets/`

## View Classes

In `apps/broadcasts/views.py`, add classes near other broadcast profile/feed API classes:

- `BroadcastChannelListCreateView`
- `BroadcastChannelDetailView`
- `BroadcastChannelSubscribeView`
- `BroadcastChannelSubscriptionView`
- `BroadcastChannelContentListCreateView`
- `ChannelContentDetailView`
- `ChannelContentPublishView`
- `ChannelContentAssetUploadView`
- `BroadcastPlaylistListCreateView`

## Permissions

Create helper functions in `views.py`:

- `_user_can_manage_channel(user, channel)`
- `_user_can_edit_content(user, content)`
- `_resolve_channel_owner(user, owner_type, owner_id)`

Rules:

- public can read public channels and public published content;
- authenticated user can create one personal channel by default;
- organization channels require matching owner/admin role;
- managers/editors can create/edit content;
- moderators can moderate comments but cannot edit content unless also editor;
- private/unlisted content is not in public discovery.

## URL Changes

In `apps/broadcasts/urls.py`, add paths before generic dynamic routes:

```python
path("broadcasts/channels/", BroadcastChannelListCreateView.as_view(), name="broadcast-channel-list"),
path("broadcasts/channels/<str:handle_or_id>/", BroadcastChannelDetailView.as_view(), name="broadcast-channel-detail"),
path("broadcasts/channels/<uuid:channel_id>/subscribe/", BroadcastChannelSubscribeView.as_view(), name="broadcast-channel-subscribe"),
path("broadcasts/channels/<uuid:channel_id>/subscription/", BroadcastChannelSubscriptionView.as_view(), name="broadcast-channel-subscription"),
path("broadcasts/channels/<uuid:channel_id>/contents/", BroadcastChannelContentListCreateView.as_view(), name="broadcast-channel-contents"),
path("broadcasts/channels/<uuid:channel_id>/playlists/", BroadcastPlaylistListCreateView.as_view(), name="broadcast-channel-playlists"),
path("broadcasts/channel-contents/<uuid:content_id>/", ChannelContentDetailView.as_view(), name="broadcast-channel-content-detail"),
path("broadcasts/channel-contents/<uuid:content_id>/publish/", ChannelContentPublishView.as_view(), name="broadcast-channel-content-publish"),
path("broadcasts/channel-contents/<uuid:content_id>/assets/", ChannelContentAssetUploadView.as_view(), name="broadcast-channel-content-assets"),
```

## Query Behavior

For `GET /channels/<id>/contents/` support:

- `type=video|short_video|post|audio|document|live_stream`
- `status=published|scheduled|draft` only for channel managers
- `q=`
- `limit=`
- `cursor=` but keep offset compatibility if using existing pagination helper.

## Tests

Add tests:

- anonymous can view public channel;
- anonymous cannot view private channel;
- user can create own channel;
- duplicate handle fails;
- non-manager cannot edit channel/content;
- subscribe/unsubscribe changes subscriber_count idempotently;
- list contents excludes drafts for public users.

## Validation

```bash
python3 manage.py check
python3 manage.py test apps.broadcasts.tests.BroadcastChannelApiTests --noinput
```

## ChatGPT Prompt

```text
Please implement Phase 03 of KIS Feed Channels without using git commands. Add public and creator-facing Django APIs for BroadcastChannel, subscriptions, channel contents, assets, and playlists. Preserve all existing broadcast feed endpoints and response shapes. Add ownership/role checks and focused API tests. Update docs/feed-channels-roadmap/status.md and docs/BUILD_STATE.md.
```

