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


# ---------------------------------------------------------------------------
# Async video content-safety scan + resolution.
#
# Real video-content NudeNet scans are always async (see the module-level
# docstring on scan_uploaded_object_task above for the sync-eligible cases —
# this is the counterpart for the 4 call sites that used to skip real
# scanning entirely for lack of a file_path: apps/media/views.py::
# UploadFileView, apps/broadcasts/views.py's BroadcastVideoUploadView and
# _build_feed_attachment, apps/broadcasts/views_internal.py::
# ProcessBroadcastVideoUploadView, and apps/broadcasts/education_media.py's
# material uploads). Each of those call sites creates a MediaSafetyScan row
# with safety.queued_for_async_scan_decision() and enqueues
# scan_video_and_resolve_task, storing where to apply the result via a
# FIXED, explicit enum + one hardcoded resolver function per value — no
# reflective/dynamic dispatch (e.g. resolving a model by an arbitrary
# app_label.ModelName string) for something that mutates safety-relevant
# state on a retryable/redeliverable Celery task.
# ---------------------------------------------------------------------------

import enum
import logging

logger = logging.getLogger("apps.media.content_safety")


class ContentSafetyResolutionTarget(str, enum.Enum):
    BROADCAST_VIDEO = "broadcast_video"
    MEDIA_ASSET = "media_asset"
    EDUCATION_MATERIAL = "education_material"
    STATUS_ITEM = "status_item"


def _resolve_broadcast_video(target_id: str, decision, storage_path: str) -> None:
    """Deliberately does NOT touch video_url or moderation_status. An AI
    verdict, clean or not, only ever feeds the moderation queue (via the
    quarantine->Flag alert above in the caller) — it is never sufficient on
    its own to make a video public. See apps.broadcasts.moderation_gate:
    only an explicit, unexpired human PASS does that. This function exists
    (rather than being removed) so the resolver-dispatch table's shape
    stays uniform across target types, and as the anchor point for any
    future purely-informational side effect that should still run when a
    video's AI scan resolves (e.g. updating a "why is this still pending"
    admin hint) without ever re-introducing an AI-driven visibility path."""
    from apps.broadcasts.models import BroadcastVideo

    if not BroadcastVideo.objects.filter(id=target_id).exists():
        logger.warning("content_safety.resolve.missing_target", extra={"target": "broadcast_video", "target_id": target_id})


def _resolve_media_asset(target_id: str, decision, storage_path: str) -> None:
    from apps.broadcasts.media_utils import build_media_url
    from .models import MediaAsset

    try:
        asset = MediaAsset.objects.get(id=target_id)
    except MediaAsset.DoesNotExist:
        logger.warning("content_safety.resolve.missing_target", extra={"target": "media_asset", "target_id": target_id})
        return
    asset.status = "pending" if (decision.quarantine or decision.requires_review) else "ready"
    storage = dict(asset.storage) if isinstance(asset.storage, dict) else {}
    storage["scan_status"] = decision.status
    asset.storage = storage
    update_fields = ["status", "storage"]
    is_public = storage.get("visibility") == "public"
    if is_public and not decision.quarantine and asset.bucket_key:
        asset.canonical_url = build_media_url(None, asset.bucket_key)
        update_fields.append("canonical_url")
    asset.save(update_fields=update_fields)


def _resolve_education_material(target_id: str, decision, storage_path: str) -> None:
    from apps.broadcasts.models import EducationInstitutionMaterial

    try:
        material = EducationInstitutionMaterial.objects.get(id=target_id)
    except EducationInstitutionMaterial.DoesNotExist:
        logger.warning("content_safety.resolve.missing_target", extra={"target": "education_material", "target_id": target_id})
        return
    metadata = dict(material.metadata) if isinstance(material.metadata, dict) else {}
    media_safety = dict(metadata.get("media_safety") if isinstance(metadata.get("media_safety"), dict) else {})
    media_safety.update({
        "status": decision.status,
        "quarantined": decision.quarantine,
        "requires_review": decision.requires_review,
    })
    metadata["media_safety"] = media_safety
    material.metadata = metadata
    update_fields = ["metadata"]
    if decision.quarantine:
        metadata["blocked_user_message"] = "This learning material is under safety review."
    elif not material.resource_url and storage_path:
        # EducationInstitutionMaterial.storage_path is never actually
        # populated by the material-creation path (the private object
        # reference lives in resource_url instead, set directly from
        # intent.object_key - see _education_material_media_payload) - use
        # the storage_path this task itself scanned from (stored on the
        # scan row at enqueue time), not the model's own storage_path field.
        material.resource_url = storage_path
        update_fields.append("resource_url")
    material.save(update_fields=update_fields)


def _resolve_status_item(target_id: str, decision, storage_path: str) -> None:
    from apps.statuses.models import StatusItem, StatusModerationStatus

    try:
        item = StatusItem.objects.get(id=target_id)
    except StatusItem.DoesNotExist:
        logger.warning("content_safety.resolve.missing_target", extra={"target": "status_item", "target_id": target_id})
        return
    item.moderation_status = (
        StatusModerationStatus.BLOCKED
        if decision.status == "blocked"
        else StatusModerationStatus.PENDING_REVIEW
        if decision.quarantine or decision.requires_review
        else StatusModerationStatus.PASSED
    )
    item.save(update_fields=["moderation_status"])
    # No visibility side-effect beyond the field itself - can_view_status()
    # (apps/statuses/services.py) reads moderation_status directly on every
    # read path, so flipping it here is the entire "make visible" /
    # "keep hidden" decision. Unlike MediaAsset/BroadcastVideo there's no
    # separate canonical_url/video_url to populate - StatusItem.file was
    # already pointed at the real object at create() time (see
    # StatusCreateSerializer.create), it was only ever the *visibility*
    # that was withheld pending this resolution, not the file reference.


_RESOLVERS = {
    ContentSafetyResolutionTarget.BROADCAST_VIDEO: _resolve_broadcast_video,
    ContentSafetyResolutionTarget.MEDIA_ASSET: _resolve_media_asset,
    ContentSafetyResolutionTarget.EDUCATION_MATERIAL: _resolve_education_material,
    ContentSafetyResolutionTarget.STATUS_ITEM: _resolve_status_item,
}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def scan_video_and_resolve_task(self, *, scan_id: str):
    """Runs the real (service-backed) NudeNet video scan for a
    MediaSafetyScan row created with safety.queued_for_async_scan_decision(),
    then applies the resolved decision to whatever row created it (via the
    resolution_target/resolution_id stored on the scan's own `result` JSON
    at enqueue time).

    Idempotent by construction: only proceeds if the scan's `reason` is
    still NUDENET_SCAN_QUEUED_REASON — a redelivered task (Celery
    at-least-once delivery), a duplicate .delay() call, or a task that
    already ran to completion once, all see a reason that's no longer the
    queued marker and return immediately without reprocessing or
    overwriting an already-finalized decision. Mirrors the terminal-state
    guard on kisvideo's KisVideoJobCallbackView - same problem, same fix.

    Failure is never silent: a content-safety-service call failure retries
    (matching push_asset_to_kisvideo's pattern), and if retries are
    exhausted the scan is marked "failed" with the real error logged - the
    downstream row is left exactly as its safe pending/quarantined default,
    never guessed at, and "failed" is a distinct, queryable state from
    "still queued", so a stuck item is findable rather than silently stuck
    forever with no signal.
    """
    from apps.moderation.services import create_media_safety_alert_for_scan

    from .content_safety_provider import ContentSafetyProvider, ContentSafetyProviderError
    from .models import MediaSafetyScan
    from .safety import NUDENET_SCAN_QUEUED_REASON, build_nudenet_decision

    try:
        scan = MediaSafetyScan.objects.get(id=scan_id)
    except MediaSafetyScan.DoesNotExist:
        logger.error("content_safety.scan.missing", extra={"scan_id": scan_id})
        return {"status": "missing"}

    if scan.reason != NUDENET_SCAN_QUEUED_REASON:
        # Already resolved (or resolved-to-failed) by a previous run of this
        # exact task - idempotency guard, see docstring above.
        return {"status": "already_resolved", "current_reason": scan.reason}

    result = scan.result if isinstance(scan.result, dict) else {}
    target_type_raw = str(result.get("resolution_target") or "")
    target_id = str(result.get("resolution_id") or "")
    storage_path = str(result.get("storage_path") or "")
    mime_type = str(result.get("mime_type") or scan.mime_type or "")

    try:
        target_type = ContentSafetyResolutionTarget(target_type_raw)
    except ValueError:
        logger.error(
            "content_safety.scan.unknown_target",
            extra={"scan_id": scan_id, "target_type": target_type_raw},
        )
        scan.status = "failed"
        scan.reason = "content_safety_unknown_resolution_target"
        scan.save(update_fields=["status", "reason"])
        return {"status": "failed", "error": "unknown_resolution_target"}

    if not default_storage.exists(storage_path):
        logger.error("content_safety.scan.storage_missing", extra={"scan_id": scan_id, "storage_path": storage_path})
        scan.status = "failed"
        scan.reason = "content_safety_storage_missing"
        scan.save(update_fields=["status", "reason"])
        return {"status": "failed", "error": "storage_missing"}

    try:
        with default_storage.open(storage_path, "rb") as fh:
            filename = os.path.basename(storage_path)
            label, score = ContentSafetyProvider().scan(fh, filename=filename, content_type=mime_type)
    except ContentSafetyProviderError as exc:
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error(
                "content_safety.scan.failed_after_retries",
                extra={"scan_id": scan_id, "error": str(exc)},
            )
            scan.status = "failed"
            scan.reason = f"content_safety_scan_error:{type(exc).__name__}"
            scan.save(update_fields=["status", "reason"])
            return {"status": "failed", "error": str(exc)}

    decision = build_nudenet_decision(label, score)
    scan.status = decision.status
    scan.quarantine = decision.quarantine
    scan.requires_review = decision.requires_review
    scan.reason = decision.reason
    scan.result = {**result, **decision.as_metadata()}
    scan.save(update_fields=["status", "quarantine", "requires_review", "reason", "result"])

    resolver = _RESOLVERS[target_type]
    resolver(target_id, decision, storage_path)

    if decision.quarantine:
        # This was the actual gap: every OTHER content-safety path (the
        # synchronous image path in scan_uploaded_object_task above) already
        # raises a moderation Flag on quarantine, but this async video-
        # resolve path never did - a blocked/pending_review video verdict
        # updated MediaSafetyScan and the resolver target, then went
        # nowhere near GO's moderation queue. Confirmed empirically: zero
        # Flag rows existed for any video scan before this fix.
        create_media_safety_alert_for_scan(scan)

    return {"status": "resolved", "decision_status": decision.status}


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
    # Nest's InternalAuthGuard checks against its own DJANGO_INTERNAL_TOKEN
    # env var, not NEST_INTERNAL_TOKEN (which Nest never reads) - see
    # apps/chat/tasks.py's _post_to_nest for the full explanation.
    token = str(getattr(settings, "DJANGO_INTERNAL_TOKEN", "")).strip()
    if not base or not token:
        return
    # RealtimeInternalController is @Controller('internal') on the Nest
    # side - missing this prefix 404s (confirmed via a real production
    # test during this session's closure verification).
    url = f"{base}/internal/attachments/quarantine"
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


# ---------------------------------------------------------------------------
# Blocked-content permanent deletion — an AI (or staff) block takes content
# offline immediately on its own (apps.broadcasts.moderation_gate.
# apply_ai_block_takedown / apps.moderation.services._takedown_blocked_content),
# but per explicit product policy AI never schedules or performs the actual
# file/record deletion. Only a human admin's explicit "Delete" action
# (admin_control.views.media_safety.AdminMediaSafetyModerateView) sets
# MediaSafetyScan.scheduled_deletion_at; this sweep just processes whatever
# a human has actually scheduled, promptly (every 15 min), so illicit
# material a human confirmed for deletion doesn't linger in storage.
# ---------------------------------------------------------------------------

from django.utils import timezone

_DELETE_TARGET_RESOLVERS = {}


def _hard_delete_broadcast_video(target_id: str) -> None:
    from apps.broadcasts.models import BroadcastVideo

    BroadcastVideo.objects.filter(id=target_id).delete()


_DELETE_TARGET_RESOLVERS["broadcast_video"] = _hard_delete_broadcast_video


def delete_blocked_media() -> dict:
    """Real logic behind delete_blocked_media_task - kept independently
    testable/callable without Celery, matching the house pattern
    (apps.accounts.tasks.purge_accounts_past_grace_period)."""
    from .models import MediaSafetyScan

    now = timezone.now()
    due = MediaSafetyScan.objects.filter(
        status="blocked", scheduled_deletion_at__lte=now, deleted_at__isnull=True,
    )

    deleted = 0
    errors = 0
    for scan in due:
        try:
            # Marked BEFORE the actual deletion - same tradeoff as the
            # account-purge sweep: prefer under-deleting on a mid-task
            # crash over any risk of double-processing. This is what makes
            # a redelivered/duplicate task run for the same scan a no-op.
            scan.deleted_at = now
            scan.save(update_fields=["deleted_at", "updated_at"])

            result = scan.result if isinstance(scan.result, dict) else {}
            storage_path = str(result.get("storage_path") or scan.upload_id or "").strip()
            if storage_path and default_storage.exists(storage_path):
                default_storage.delete(storage_path)

            target_type = str(result.get("resolution_target") or "")
            target_id = str(result.get("resolution_id") or "")
            resolver = _DELETE_TARGET_RESOLVERS.get(target_type)
            if resolver and target_id:
                resolver(target_id)

            deleted += 1
        except Exception:
            errors += 1
            logger.exception("Failed to permanently delete blocked media (scan_id=%s)", scan.id)

    return {"deleted": deleted, "errors": errors}


@shared_task
def delete_blocked_media_task():
    return delete_blocked_media()


# ---------------------------------------------------------------------------
# Orphaned-record housekeeping - distinct from delete_blocked_media above,
# which performs a HUMAN's explicit delete decision. This function makes no
# moderation judgment of its own: it only ever reacts to storage state that
# is already true (the underlying S3 object is gone, for whatever reason -
# a prior partial failure, a manual/manual-console intervention, a legacy
# path that predates this system), so the database never keeps a dangling
# reference to a file that no longer exists. It never removes a file that
# is still actually stored.
#
# Scoped to every non-deleted MediaSafetyScan, not just "blocked" ones -
# any scan whose file is gone is stale data regardless of its moderation
# status. At current volume (low hundreds of rows) one existence check per
# row per run is cheap; this does not batch S3 HEAD calls and would need
# revisiting (e.g. S3 inventory/event-driven sync instead of polling) if
# this table grows into the tens of thousands.
# ---------------------------------------------------------------------------

def sync_orphaned_media_records() -> dict:
    from .models import MediaSafetyScan

    now = timezone.now()
    candidates = MediaSafetyScan.objects.filter(deleted_at__isnull=True).exclude(upload_id="")

    synced = 0
    errors = 0
    for scan in candidates.iterator():
        try:
            result = scan.result if isinstance(scan.result, dict) else {}
            storage_path = str(result.get("storage_path") or scan.upload_id or "").strip()
            if not storage_path or default_storage.exists(storage_path):
                continue  # no path to check, or the file is still genuinely there

            scan.deleted_at = now
            scan.save(update_fields=["deleted_at", "updated_at"])

            target_type = str(result.get("resolution_target") or "")
            target_id = str(result.get("resolution_id") or "")
            resolver = _DELETE_TARGET_RESOLVERS.get(target_type)
            if resolver and target_id:
                resolver(target_id)

            synced += 1
        except Exception:
            errors += 1
            logger.exception("Failed to sync orphaned media record (scan_id=%s)", scan.id)

    return {"synced": synced, "errors": errors}


@shared_task
def sync_orphaned_media_records_task():
    return sync_orphaned_media_records()


# ---------------------------------------------------------------------------
# Stuck-scan safety net - a video content-safety scan is queued for async
# resolution (safety.NUDENET_SCAN_QUEUED_REASON) at upload time, then
# resolved by scan_video_and_resolve_task. If that .delay() call silently
# failed, or a worker crashed before ever picking the task up, the scan
# would sit "pending"/incomplete forever with nothing to notice or retry
# it. This sweep re-enqueues any scan still carrying the queued marker
# after stale_after_minutes. Safe to re-run/duplicate: scan_video_and_
# resolve_task only ever proceeds if the scan's reason is STILL the queued
# marker (see its own docstring), so re-enqueuing an already-resolving or
# already-resolved scan is always a no-op, never a double-apply.
# ---------------------------------------------------------------------------

def resolve_stuck_video_scans(*, stale_after_minutes: int = 60) -> dict:
    from datetime import timedelta

    from .models import MediaSafetyScan
    from .safety import NUDENET_SCAN_QUEUED_REASON

    cutoff = timezone.now() - timedelta(minutes=stale_after_minutes)
    stuck = MediaSafetyScan.objects.filter(reason=NUDENET_SCAN_QUEUED_REASON, created_at__lt=cutoff)

    requeued = 0
    for scan in stuck.iterator():
        scan_video_and_resolve_task.delay(scan_id=str(scan.id))
        requeued += 1
    return {"requeued": requeued}


@shared_task
def resolve_stuck_video_scans_task():
    return resolve_stuck_video_scans()
