# Public Web, Embeds, SEO, And Growth Runbook

Status: Phase 26 foundation.

This runbook defines the safe path for making KIS channels and content discoverable on the public web without exposing private, unlisted, child-sensitive, or raw storage data.

## Default State

- Public metadata endpoints may be enabled.
- Search indexing is disabled by default until QA approves launch evidence.
- Embeds are disabled by default until embed QA and abuse controls are approved.
- Referral/growth loops are disabled by default until anti-spam review is complete.

Required defaults:

- `KIS_PUBLIC_WEB_ENABLED=True`
- `KIS_PUBLIC_WEB_INDEXING_ENABLED=False`
- `KIS_PUBLIC_REFERRALS_ENABLED=False`
- `KIS_EMBEDS_ENABLED=False`

## Public Eligibility

Public web endpoints may expose only content that is:

- attached to a public channel;
- published;
- `visibility=public`;
- not deleted;
- not marked `child_sensitive`;
- not marked `private_context`;
- not marked `contains_private_data`.

Do not expose:

- private or unlisted content;
- direct storage paths;
- raw verification documents;
- private health/payment data;
- child-sensitive content;
- provider secrets or raw provider payloads.

## Public Endpoints

- `/api/v1/broadcasts/public/channels/<handle>/`
- `/api/v1/broadcasts/public/contents/<content_id>/`
- `/api/v1/broadcasts/public/robots.txt`
- `/api/v1/broadcasts/public/sitemap-plan/`
- Existing oEmbed endpoint remains behind embed policy:
  - `/api/v1/broadcasts/embed/contents/<content_id>/oembed/`

## QA Before Indexing

Before setting `KIS_PUBLIC_WEB_INDEXING_ENABLED=True`:

1. Verify public channel landing metadata has canonical URL, title, description, share image, report URL, and trust badges.
2. Verify public content metadata has canonical URL, title, description, thumbnail, oEmbed pointer, report URL, and safe channel summary.
3. Verify private, unlisted, deleted, and child-sensitive content returns 404.
4. Verify no response includes `storage_path`, secrets, raw documents, or private health/payment fields.
5. Verify public reporting creates moderation records.
6. Verify embeds remain disabled unless `KIS_EMBEDS_ENABLED=True` and channel policy permits the domain.
7. Verify robots/sitemap outputs match launch decision.

## Growth Loop Rules

- Referral links must identify campaigns without exposing private user relationships.
- Public share cards must use monetization-safe copy.
- Public pages must show verification/trust badges only from public badge summaries.
- Child/youth content must never be used for growth targeting.
- Abuse reporting must be available from every public channel/content page.

## Validation Commands

```bash
python3 manage.py test apps.broadcasts.tests.BroadcastChannelApiTests --noinput --keepdb
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
```

React Native:

```bash
npx eslint src/network/routes/broadcastRoutes.ts src/services/publicGrowthService.ts src/components/dashboard/PublicGrowthReadinessCard.tsx src/screens/tabs/ProfileScreen.tsx --quiet
npm run typecheck -- --pretty false
```

## Rollback

If unsafe public exposure is found:

1. Set `KIS_PUBLIC_WEB_ENABLED=False`.
2. Set `KIS_PUBLIC_WEB_INDEXING_ENABLED=False`.
3. Set `KIS_EMBEDS_ENABLED=False`.
4. Remove affected public route from navigation/web shell.
5. Preserve moderation and audit evidence.
6. Re-run private/unlisted/child-sensitive 404 tests before re-enabling.
