# Phase 08 - Embeds And Public Player

Purpose: allow approved KIS channel content to be embedded in external websites/apps.

## Files To Change

Backend:

- `apps/broadcasts/models.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/urls.py`
- `apps/broadcasts/tests.py`
- `.env.example`

Frontend/web:

- New React Native/web-compatible embed preview helper if applicable:
  - `/Users/nigel/dev/KIS/src/screens/broadcast/channels/embed/embedUtils.ts`
- New docs:
  - `docs/feed-channels-roadmap/embed-policy.md`

## Backend Models

Add:

```python
class ChannelEmbedPolicy(models.Model):
    channel = models.OneToOneField(BroadcastChannel, on_delete=models.CASCADE, related_name="embed_policy")
    allow_embeds = models.BooleanField(default=True)
    allowed_domains = models.JSONField(default=list, blank=True)
    blocked_domains = models.JSONField(default=list, blank=True)
    require_signed_token = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class ChannelContentEmbed(models.Model):
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="embeds")
    domain = models.CharField(max_length=255, blank=True, default="")
    token_hash = models.CharField(max_length=128, blank=True, default="")
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

## Public Embed Endpoints

Add:

- `GET /api/v1/broadcasts/embed/contents/<uuid:content_id>/`
- `GET /api/v1/broadcasts/embed/contents/<uuid:content_id>/oembed/`
- `POST /api/v1/broadcasts/channel-contents/<uuid:content_id>/embed-token/` owner/manager only

Response should include:

- title;
- channel public summary;
- thumbnail;
- content type;
- safe playback/display URL;
- dimensions;
- embed html snippet.

Do not expose private metadata, owner email/phone, storage paths, or raw signed tokens.

## Embed HTML

Use iframe snippet:

```html
<iframe
  src="https://<kis-host>/embed/content/<content-id>?token=<optional-token>"
  width="560"
  height="315"
  frameborder="0"
  allow="autoplay; encrypted-media; picture-in-picture"
  allowfullscreen>
</iframe>
```

## Security

- Validate `Origin` or `Referer` domain against allowlist where possible.
- For public content, allow unsigned embed if channel policy allows.
- For unlisted/private content, require signed short-lived token.
- Rate-limit embed metadata endpoints.
- Record `BroadcastEngagementEvent` with event type `embed_impression`.

## Env

Add:

```text
KIS_PUBLIC_EMBED_BASE_URL=https://app.example.com
KIS_EMBED_SIGNING_SECRET=replace-with-strong-secret
KIS_EMBEDS_ENABLED=False
```

## Validation

```bash
python3 manage.py makemigrations broadcasts
python3 manage.py check
python3 manage.py test apps.broadcasts.tests.ChannelEmbedTests --noinput
```

## ChatGPT Prompt

```text
Please implement Phase 08 of KIS Feed Channels without using git commands. Add safe public embed policy models, oEmbed/public embed endpoints, signed-token support for private/unlisted embeds, domain allowlist checks, env examples, and focused tests. Do not expose private metadata or storage paths. Keep embeds disabled by default in production flags until QA.
```

