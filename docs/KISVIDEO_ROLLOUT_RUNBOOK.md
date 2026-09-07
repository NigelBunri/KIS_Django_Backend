# kisvideo VOD Integration — Rollout Runbook

Covers turning on `KIS_VIDEO_SERVICE_ENABLED` for real, once kisvideo is
deployed somewhere Django can reach. This does not change any code — it's
the checklist for the day someone flips the switch. Written before that
deployment exists, so it can be reviewed and argued with before it matters.

Code involved: `apps/broadcasts/kisvideo_provider.py`, `apps/broadcasts/tasks.py`
(`push_asset_to_kisvideo`), `apps/broadcasts/views.py`
(`ChannelContentAssetUploadView`), `apps/broadcasts/views_internal.py`
(`KisVideoJobCallbackView`). Live streaming (`live_stream_providers.py`,
Mux) is entirely unaffected by any of this, on or off.

---

## 1. Pre-flip checklist

Nothing below is optional — skipping any one of these turns "safe flag
flip" into "silent failure that looks like a hang."

- [ ] **kisvideo is deployed and health-checked.** `GET /health` on
      kisvideo returns `{"status": "ok", ...}` from wherever it's actually
      running — not just "the deploy succeeded."
- [ ] **A real Celery worker for the Django `broadcasts` queue is running
      and has been confirmed to process a task, not just be deployed.**
      `push_asset_to_kisvideo` is a `@shared_task`; it does nothing until a
      worker picks it up. `kis-celery-worker`/`kis-celery-beat` are
      reported running on the Lightsail host as of this writing, so this
      may no longer be the blocker an earlier infra audit found it to be —
      but "reported running" isn't the same as "confirmed processing this
      app's tasks," so don't skip the check on the strength of that alone.
      Confirm with a throwaway task (`from apps.broadcasts.tasks import
      purge_expired_broadcasts_task; purge_expired_broadcasts_task.delay()`
      from a shell, then check it actually ran) before trusting anything
      downstream of `.delay()`.
- [ ] **`KIS_VIDEO_SERVICE_BASE_URL` and `KIS_VIDEO_SERVICE_INTERNAL_TOKEN`
      are set on Django**, and the token matches what kisvideo's
      `INTERNAL_TOKEN` env var actually has (a mismatch fails as a 401 on
      every call, not a connection error — check Django logs for
      `kisvideo POST /uploads failed (401)` specifically, not just "it's
      not working").
- [ ] **Network path Django → kisvideo is actually reachable from where
      Django runs**, not just from your laptop. From the Django host (or a
      shell on it):
      ```
      curl -sS -o /dev/null -w '%{http_code}\n' "$KIS_VIDEO_SERVICE_BASE_URL/health"
      ```
      Expect `200`. If Django runs in a VPC/security-group-restricted
      environment (Django is on AWS Lightsail), this is the step most
      likely to be silently skipped and quietly break everything
      downstream.
- [ ] **`API_BASE_URL` on Django is set to a URL kisvideo can reach back
      to** — this is what `push_asset_to_kisvideo` builds the callback URL
      from. If Django's `API_BASE_URL` is a loopback/internal-only address
      that kisvideo can't route to, uploads will "succeed" (the tus push
      completes) but the job will finish and the webhook will silently
      fail to deliver — the asset stays at `processing_status="transcoding"`
      forever with no error anywhere, since kisvideo's webhook sender
      (`_send_webhook`) only logs a warning on delivery failure, it never
      surfaces it back to Django. Watch for this specifically in section 4.

---

## 2. First real test — minimal blast radius

Do **not** flip the flag globally as the first test. Two options, in order
of preference:

**Option A — per-request override, no flag flip at all.** If there's ever
a need to test the exact code path without touching the global flag, the
flag is read via `getattr(settings, "KIS_VIDEO_SERVICE_ENABLED", False)` —
there is no per-request override built in today. So in practice:

**Option B — flip the flag, but control blast radius by *what* you upload,
not by *who* can reach the code.** Flip `KIS_VIDEO_SERVICE_ENABLED=True` in
whatever environment you're testing in (ideally staging, not prod), then
upload exactly **one** real video, to a throwaway/test channel that
nothing else depends on. Every video/short_video asset with a
`storage_path` on that deploy will now route through kisvideo — the flag
has no per-channel or per-user granularity — so the "minimal blast radius"
comes from testing in an environment where that's acceptable, or from
picking a genuinely disposable channel in prod if staging isn't
representative enough.

What to actually watch, in order:

1. **Upload response** — `POST .../assets/` still returns `201` immediately
   (the kisvideo push happens in the background task, not in the request).
   Confirm the response body's `processing_status` is `"queued"`, not
   `"ready"` — if it's `"ready"`, the flag didn't take effect or the asset
   wasn't classified as video/short_video.
2. **`ChannelContentAsset.processing_status` transitions**, via Django
   admin/shell, polling the specific asset:
   `"queued"` → `"transcoding"` (set the moment `push_asset_to_kisvideo`
   successfully finishes the tus push) → `"ready"` (set by
   `KisVideoJobCallbackView` once the webhook lands) or `"failed"`.
3. **The webhook actually lands.** Watch Django's request logs for
   `POST /api/v1/broadcasts/internal/kisvideo-callback/` — the `token`
   query param is redacted in logs by the existing logging middleware, so
   you'll see `?asset_id=...&token=[REDACTED]`, which is expected, not a
   bug. A `200` with `{"status": "ok"}` is success; `404` means the
   `asset_id` didn't match (shouldn't happen — investigate immediately,
   don't assume it's transient); `400` means the token didn't verify
   (check that `KIS_VIDEO_SERVICE_INTERNAL_TOKEN` didn't change between
   when the job was created and when the callback arrived — it's part of
   the HMAC input, see `kisvideo_provider.sign_kisvideo_callback_token`).
4. **`ChannelContent.status` flips to `PUBLISHED`** (or back to whatever it
   was, if it wasn't `PROCESSING`) on the same request that resolves the
   asset.
5. **The video actually plays back in the app**, pointed at the new
   `ChannelContentAsset.url` (now kisvideo's `master_playlist_url`, an HLS
   `.m3u8`). This is the step that validates the *entire* chain end to
   end, not just Django's bookkeeping — `HlsVideo.tsx` on kistube-website
   was already verified to need zero code changes for this, but that was a
   static-code read, not a real playback test against a real kisvideo
   output. Do this with real ears/eyes on a real device, not just a 200
   response.

Only after one full success on step 5 does it make sense to test a second
video, then eventually a small cohort.

---

## 3. What "it broke" looks like, and the rollback

**Rolling back the flag itself is simple and safe going forward**: flip
`KIS_VIDEO_SERVICE_ENABLED` back to `False`. As of the flag re-check added
to `push_asset_to_kisvideo` (not present in the first version of this
integration — added specifically because this runbook asked "what
actually happens," and the honest answer was "nothing stops it," so a
guard was added and tested):

- Any asset **not yet enqueued** (i.e. before the upload request that
  creates it) will go through the old, always-existing path: created with
  `processing_status="ready"` immediately, no transcode step, exactly as
  every non-kisvideo asset behaves today.
- Any asset **already queued but not yet picked up by a worker** will be
  skipped by the task itself (`push_asset_to_kisvideo` now returns
  `{"status": "skipped_flag_disabled"}` without calling kisvideo) — but it
  is left at `processing_status="queued"` and **not automatically
  recovered**. This is the one case that needs manual attention (below).
- Any asset **already mid-flight inside `create_transcode_job`** (the
  worker had already started the tus push when you flipped the flag) will
  run to completion — the flag isn't re-checked mid-loop. It'll either
  finish normally (asset resolves via the webhook as usual) or fail
  normally (existing retry/failure handling applies). Flipping the flag
  does not, and cannot, abort in-flight HTTP calls.
- Any asset **already `"transcoding"` and waiting on kisvideo's webhook**
  is entirely unaffected by the Django-side flag — kisvideo doesn't know
  or care about it. It will resolve (or not) independent of Django's flag
  state.

**There is no "fall back to the old Mux flow" for video** — this needs to
be said plainly because it's easy to assume otherwise. Before this
integration, VOD assets were never routed through Mux or any transcode
provider at all (confirmed by grep — Mux only ever appears in
`live_stream_providers.py`, for live streaming). "Rollback" for VOD means
"go back to recording the client's raw uploaded file with no server-side
transcode step," not "go back to a different provider."

**Manual recovery for an asset stuck at `"queued"` after a rollback**
(the one case that needs a human), via Django shell:

```python
from apps.broadcasts.models import ChannelContentAsset, ChannelContent

asset = ChannelContentAsset.objects.get(id="<the stuck asset id>")

# Option 1: re-enable the flag and let it run — this is safe and
# idempotent, a fresh POST /uploads to kisvideo, nothing about the earlier
# skip left any state behind that would conflict with retrying.
from apps.broadcasts.tasks import push_asset_to_kisvideo
push_asset_to_kisvideo.delay(str(asset.id))

# Option 2: give up on kisvideo for this asset and revert it to the
# pre-integration behavior (serves whatever raw file/URL the client
# originally submitted — check asset.url and asset.storage_path are
# actually populated with something playable before doing this; the
# original upload request may not have included a direct playback URL
# if the client expected kisvideo to always supply one).
asset.processing_status = "ready"
asset.save(update_fields=["processing_status"])
if asset.content.status == ChannelContent.Status.PROCESSING:
    asset.content.status = ChannelContent.Status.PUBLISHED
    asset.content.save(update_fields=["status"])
```

Prefer Option 1 unless there's a reason kisvideo specifically needs to
stay off for that asset — it's the one that doesn't require guessing
whether the client already gave you a usable raw playback URL.

---

## 4. Monitoring during the first real test

Watch these three things side by side, not sequentially after the fact:

1. **Django logs** — grep for `kisvideo` (case-insensitive) across the
   request/error logs. Real failure modes and their signatures:
   - `kisvideo POST /uploads failed (401)` — token mismatch between Django
     and kisvideo.
   - `kisvideo POST /uploads failed (...)` / `kisvideo PATCH ... failed`
     with a 5xx — kisvideo-side error, check kisvideo's own logs next.
   - `kisvideo request failed: ...` (wraps a `requests.exceptions.*`) —
     network-level failure (timeout, connection reset, DNS) — this is the
     class of failure the retry logic (`max_retries=3`) exists for; check
     whether it eventually succeeded on retry or exhausted retries and
     marked the asset `"failed"`.
2. **kisvideo's own worker/API logs**, on whatever host it's deployed to —
   specifically whether `transcode_video` actually ran to completion for
   the test job's id, and whether its own webhook POST (`_send_webhook`)
   logged a delivery warning (that function only warns on failure, it
   never raises) — this is the single most likely silent-failure point
   per the `API_BASE_URL` reachability note in section 1.
3. **`KisVideoJobCallbackView`'s request logs on Django** — every callback
   attempt logs a normal request-log line
   (`POST /api/v1/broadcasts/internal/kisvideo-callback/...`) with its
   status code. A `200` here is the actual proof the loop closed; nothing
   before this point confirms end-to-end success, only that Django's side
   of the handoff worked.

If the test video never leaves `"transcoding"` and none of the three logs
above show anything — that's the `API_BASE_URL`-unreachable scenario from
section 1, not a hang. Check that first before assuming kisvideo itself is
broken.
