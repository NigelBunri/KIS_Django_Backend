# Feed Channels Embed Policy

Current launch posture: embeds are implemented behind `KIS_EMBEDS_ENABLED=False` and must stay disabled until QA, legal review, CSP review, and production monitoring are complete.

## Public Content

- Public, published channel content can be embedded only when the channel embed policy allows embeds.
- Optional `allowed_domains` and `blocked_domains` are enforced from `Origin` or `Referer` when available.
- Public embed responses expose only safe display fields: title, description, content type, channel public summary, thumbnail, public asset URL, dimensions, embed URL, and iframe HTML.
- Responses must not expose owner contact details, private metadata, storage paths, raw file paths, or raw signed token hashes.

## Private Or Unlisted Content

- Private, draft, unlisted, or policy-protected content requires a signed embed token.
- Embed token records store only token hashes.
- Tokens may be restricted to a domain and may expire.
- Raw tokens are returned only once during token creation.

## Operational Rules

- `KIS_EMBEDS_ENABLED` remains `False` by default.
- `KIS_EMBED_SIGNING_SECRET` must be a strong production secret before enabling any private/unlisted embeds.
- `KIS_PUBLIC_EMBED_BASE_URL` must point to the production public app/player host.
- Embed endpoints should be rate-limited before public launch.
- Embed impressions are recorded only when they can be safely tied to an existing legacy broadcast item and authenticated viewer.

## Phase 08 Remaining Work

- Add a web iframe player route outside the API host if the production frontend is web-capable.
- Add CSP/frame-ancestors policy after final allowed-domain decisions.
- Add provider-specific media playback signing once private media storage is fully connected.
