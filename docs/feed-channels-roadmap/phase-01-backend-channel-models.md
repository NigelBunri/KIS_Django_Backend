# Phase 01 - Backend Channel Models

Purpose: add database structures for YouTube-style channels without breaking existing broadcast feeds.

## Files To Change

- `apps/broadcasts/models.py`
- `apps/broadcasts/admin.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/tests.py`
- New migration in `apps/broadcasts/migrations/`
- `docs/feed-channels-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Existing Anchors

- In `apps/broadcasts/models.py`, `BroadcastItem` starts near the top after `BroadcastSourceType`.
- `BroadcastFeedProfile` currently stores the old profile feed compatibility.
- `BroadcastReaction` and `BroadcastEngagementEvent` exist near the lower part of the file.

## Models To Add

Add after `BroadcastFeedProfile` or near other profile/channel models:

```python
class BroadcastChannel(models.Model):
    class OwnerType(models.TextChoices):
        USER = "user", "User"
        SHOP = "shop", "Shop"
        HEALTH = "health", "Health institution"
        EDUCATION = "education", "Education institution"
        PARTNER = "partner", "Partner organization"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_type = models.CharField(max_length=24, choices=OwnerType.choices, db_index=True)
    owner_id = models.UUIDField(db_index=True)
    owner_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_broadcast_channels")
    handle = models.SlugField(max_length=80, unique=True)
    display_name = models.CharField(max_length=140)
    description = models.TextField(blank=True, default="")
    avatar_url = models.URLField(blank=True, default="")
    banner_url = models.URLField(blank=True, default="")
    country = models.CharField(max_length=8, blank=True, default="")
    language = models.CharField(max_length=16, blank=True, default="")
    category = models.CharField(max_length=64, blank=True, default="")
    links = models.JSONField(default=list, blank=True)
    branding = models.JSONField(default=dict, blank=True)
    verification_badges = models.JSONField(default=list, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    is_public = models.BooleanField(default=True, db_index=True)
    is_verified = models.BooleanField(default=False, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    subscriber_count = models.PositiveIntegerField(default=0)
    content_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner_type", "owner_id"]),
            models.Index(fields=["handle"]),
            models.Index(fields=["is_public", "is_deleted"]),
        ]
```

Add:

```python
class BroadcastChannelRole(models.Model):
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="roles")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="broadcast_channel_roles")
    role = models.CharField(max_length=24, choices=[("owner","Owner"),("manager","Manager"),("editor","Editor"),("moderator","Moderator"),("analyst","Analyst")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("channel", "user", "role")]
```

Add:

```python
class BroadcastChannelSubscription(models.Model):
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="subscriptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="broadcast_channel_subscriptions")
    notifications = models.CharField(max_length=16, choices=[("none","None"),("personalized","Personalized"),("all","All")], default="personalized")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("channel", "user")]
```

Add:

```python
class BroadcastPlaylist(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="playlists")
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    visibility = models.CharField(max_length=16, choices=[("public","Public"),("unlisted","Unlisted"),("private","Private")], default="public")
    sort_order = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

## Required Serializers

In `apps/broadcasts/serializers.py`, add:

- `BroadcastChannelSummarySerializer`
- `BroadcastChannelDetailSerializer`
- `BroadcastChannelSubscriptionSerializer`
- `BroadcastPlaylistSerializer`

Keep fields public-safe. Do not expose owner private data. Include `is_subscribed` and `viewer_role` only from serializer context.

## Admin

In `apps/broadcasts/admin.py`, register:

- `BroadcastChannel`
- `BroadcastChannelRole`
- `BroadcastChannelSubscription`
- `BroadcastPlaylist`

Use list displays with `handle`, `display_name`, `owner_type`, `owner_id`, `is_public`, `is_verified`, `subscriber_count`, `created_at`.

## Tests

In `apps/broadcasts/tests.py`, add tests:

- channel handle uniqueness;
- subscription uniqueness;
- public serializer hides private owner details;
- staff/admin can inspect channel records.

## Validation

Run:

```bash
python3 manage.py makemigrations broadcasts
python3 manage.py check
python3 manage.py test apps.broadcasts.tests.BroadcastChannelModelTests --noinput
```

If DB tests block, record the command and blocker in `status.md`.

## ChatGPT Prompt For This Phase

```text
Please implement Phase 01 of KIS Feed Channels without using git commands. Add backward-compatible Django channel models, serializers, admin registration, migration, and focused tests. Do not remove existing BroadcastItem or JSON feed behavior. Use the exact model names and fields in this phase document unless the existing code requires a small compatibility adjustment. Update docs/feed-channels-roadmap/status.md and docs/BUILD_STATE.md with validation and blockers.
```

