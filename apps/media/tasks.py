# media/tasks.py
from celery import shared_task
from .models import ProcessingJob, MediaAsset
from .upload_intent import expire_abandoned_upload_intents, expire_unattached_confirmed_intents


@shared_task
def expire_abandoned_media_uploads():
    """Periodic sweep for presigned uploads that were never confirmed (S3
    PUT never happened, or happened but the client never called confirm).
    Schedule via Celery Beat; apps/media/management/commands/
    expire_media_uploads.py wraps the same function for manual/cron use
    where Beat isn't configured."""
    return expire_abandoned_upload_intents()


@shared_task
def expire_unattached_media_uploads():
    """Periodic sweep for CONFIRMED uploads that were never attached to a
    real resource (e.g. a marketplace flow where the client confirmed a
    product photo but never finished creating the product). Never touches
    an attached intent. Schedule via Celery Beat alongside
    expire_abandoned_media_uploads."""
    return expire_unattached_confirmed_intents()

@shared_task(bind=True)
def process_job_worker(self, job_id):
    """
    Worker stub: pick a ProcessingJob, perform pipeline, write results.
    Replace with integration to FFMPEG, image pipelines, ML models, etc.
    """
    job = ProcessingJob.objects.get(id=job_id)
    job.mark_running(worker_meta={"worker": "local-stub"})
    # Fake processing depending on pipeline
    if job.pipeline == "phash":
        # compute a faux perceptual hash
        result_meta = {"phash": "0000abcd1234", "derived_variant": None}
    elif job.pipeline == "analyze":
        result_meta = {"labels": {"nsfw": 0.01}, "derived_variant": None}
    else:
        result_meta = {"notes": "processed by stub"}

    job.mark_done(result_meta=result_meta)
    return {"job": str(job_id), "status": "done"}

@shared_task
def schedule_asset_processing(asset_id):
    asset = MediaAsset.objects.get(id=asset_id)
    # Create jobs for common pipelines
    ProcessingJob.objects.create(asset=asset, pipeline="phash", priority=40)
    ProcessingJob.objects.create(asset=asset, pipeline="analyze", priority=50)
    ProcessingJob.objects.create(asset=asset, pipeline="transcode", priority=60)
    return {"asset": str(asset_id)}


# ---------------------------------------------------------------------------
# Explicit-content screening for uploads that never pass through Django —
# everything now routed direct-to-S3 via Nest (chat images/videos/docs,
# voice notes, stickers, avatars, broadcast video). Django-routed uploads
# already get scanned synchronously in the request itself (see
# apps/broadcasts/views.py's _record_upload_safety); the task below is what
# closes the gap for the direct-to-S3 path, triggered by Nest calling
# media/internal/scan-upload/ right after every confirmed upload (see
# UploadIntentService.confirm() on the Nest side). Separate from the
# ProcessingJob "analyze" pipeline stub above (that one is a placeholder,
# has no caller wired to it, and returns a hardcoded fake nsfw score) — this
# is the real, model-backed path, and it writes to MediaSafetyScan/Flag
# (apps.moderation) instead of ProcessingJob.
# ---------------------------------------------------------------------------

import os
import tempfile

from django.core.files.storage import default_storage

from .safety import normalize_upload_context, scan_upload_for_explicit_content


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def scan_uploaded_object_task(
    self,
    *,
    object_key: str,
    mime_type: str,
    original_filename: str,
    size_bytes: int,
    context: str,
    owner_id: str | None,
) -> str:
    from apps.moderation.services import create_media_safety_alert_for_scan

    from .models import MediaSafetyScan

    normalized_context = normalize_upload_context(context)

    if not default_storage.exists(object_key):
        # Nothing to scan — object may have been cleaned up already, or the
        # confirm-then-notify call raced a since-reverted upload. Not an
        # error worth retrying.
        return "object_missing"

    suffix = os.path.splitext(object_key)[1] or ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            with default_storage.open(object_key, "rb") as remote_file:
                tmp.write(remote_file.read())
            tmp.flush()
            decision = scan_upload_for_explicit_content(
                filename=original_filename,
                mime_type=mime_type,
                context=normalized_context,
                file_path=tmp.name,
            )
    except Exception as exc:
        # Downloading/scanning failed for a reason unrelated to the content
        # itself (network blip, transient S3 error) — retry a couple of
        # times before giving up and leaving it for manual review.
        raise self.retry(exc=exc)

    scan = MediaSafetyScan.objects.create(
        owner_id=owner_id,
        upload_id=object_key,
        context=normalized_context,
        original_name=original_filename,
        mime_type=mime_type,
        bytes=size_bytes,
        provider=decision.provider,
        status=decision.status,
        quarantine=decision.quarantine,
        requires_review=decision.requires_review,
        policy_version=decision.policy_version,
        reason=decision.reason,
        result=decision.as_metadata(),
    )

    if decision.quarantine:
        create_media_safety_alert_for_scan(scan)
        _notify_nest_to_quarantine(object_key)

    return decision.status


def _notify_nest_to_quarantine(object_key: str) -> None:
    """Fire-and-forget callback to Nest so a flagged chat attachment is
    taken down immediately, not just in Django's own (chat-blind) records —
    Nest owns the Message documents, Django has no other way to reach them.
    General/broadcast content quarantines directly in Django's own tables
    instead (see MediaAsset.status updates elsewhere) and doesn't need this.
    Mirrors apps/chat/tasks.py's _post_to_nest exactly (same NEST_INTERNAL_URL/
    NEST_INTERNAL_TOKEN settings, same sign_internal_request helper)."""
    import json
    import urllib.request

    from django.conf import settings

    from apps.chat.internal_signing import sign_internal_request

    base = str(getattr(settings, "NEST_INTERNAL_URL", "")).strip().rstrip("/")
    token = str(getattr(settings, "NEST_INTERNAL_TOKEN", "")).strip()
    if not base or not token:
        return
    url = f"{base}/attachments/quarantine"
    body = {"objectKey": object_key}
    try:
        headers = {
            "Content-Type": "application/json",
            **sign_internal_request("POST", url, body, secret=token),
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
    except Exception:
        # Best-effort — the MediaSafetyScan/Flag rows above are already the
        # source of truth for GO's review queue even if this call fails.
        pass
