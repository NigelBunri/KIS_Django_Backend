"""Admin content-safety scan visibility — the ground-truth record of every
AI content-safety verdict (NudeNet), independent of whether a moderation
Flag was ever raised for it. Lets a GO/moderator directly view what the
scanner caught (image or video), not just aggregate metadata."""
from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.permissions import IsAdminControlUser

# Private messaging contexts (apps.media.safety.SAFE_UPLOAD_CONTEXTS) are
# excluded from every admin media-safety view for privacy — confirmed via
# apps.media.safety's own context list, not assumed. Live chat keeps
# whatever backend AI evaluation it already has; none of it is ever
# surfaced here, made subject to the human PASS/PENDING/BLOCK workflow
# below, or counted toward public broadcast eligibility.
CHAT_EXCLUDED_CONTEXTS = {"chat", "dm", "group"}

# target_type values (apps.media.tasks.ContentSafetyResolutionTarget) this
# admin surface currently knows how to apply a human moderation decision
# to. Deliberately narrow rather than silently no-op'ing for a target type
# with no real gate wired up yet — see apps.broadcasts.moderation_gate for
# the one that's actually implemented (BroadcastVideo).
MODERATABLE_TARGET_TYPES = {"broadcast_video"}


def _safe_int(val, default, lo=1, hi=250):
    try:
        return max(lo, min(int(val), hi))
    except (TypeError, ValueError):
        return default


def _resolve_storage_path(scan) -> str:
    result = scan.result if isinstance(scan.result, dict) else {}
    return str(result.get("storage_path") or scan.upload_id or "").strip()


class AdminMediaSafetyScanListView(APIView):
    """
    GET /control/admin/media-safety/scans/
    Every MediaSafetyScan row - the actual AI verdicts, whether or not a
    moderation Flag exists for it (many currently don't; see the fix in
    apps/media/tasks.py + apps/broadcasts/views.py). Query params:
    status, context, owner, page, per_page.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "content.moderate"

    def get(self, request):
        from django.core.paginator import Paginator

        from apps.media.models import MediaSafetyScan

        qs = MediaSafetyScan.objects.exclude(context__in=CHAT_EXCLUDED_CONTEXTS).order_by("-created_at")
        scan_status = request.query_params.get("status")
        if scan_status:
            qs = qs.filter(status=scan_status)
        context = request.query_params.get("context")
        if context:
            qs = qs.filter(context=context)
        owner_id = request.query_params.get("owner")
        if owner_id:
            qs = qs.filter(owner_id=owner_id)

        page_num = _safe_int(request.query_params.get("page", 1), 1, lo=1, hi=10000)
        per_page = _safe_int(request.query_params.get("per_page", 25), 25, lo=1, hi=100)
        paginator = Paginator(qs, per_page)
        page_obj = paginator.get_page(page_num)

        items = [_serialize_scan(s) for s in page_obj.object_list]
        return Response({
            "scans": items,
            "pagination": {
                "page": page_obj.number,
                "per_page": per_page,
                "total_pages": paginator.num_pages,
                "total_items": paginator.count,
            },
        })


class AdminMediaSafetyScanSummaryView(APIView):
    """GET /control/admin/media-safety/summary/ - counts by status, for a KPI strip."""
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "content.moderate"

    def get(self, request):
        from django.db.models import Count

        from apps.media.models import MediaSafetyScan

        base = MediaSafetyScan.objects.exclude(context__in=CHAT_EXCLUDED_CONTEXTS)
        by_status = list(base.values("status").annotate(count=Count("id")))
        return Response({
            "total": base.count(),
            "by_status": by_status,
        })


class AdminMediaSafetyScanMediaUrlView(APIView):
    """
    GET /control/admin/media-safety/scans/<scan_id>/media-url/
    Returns a short-lived signed URL to view the actual flagged file.
    Admin-only, deliberately short TTL - this is a one-glance review tool,
    not a persistent link into private/explicit user content.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "content.moderate"
    _TTL_SECONDS = 120

    def get(self, request, scan_id):
        from django.core.files.storage import default_storage

        from apps.media.models import MediaSafetyScan

        try:
            scan = MediaSafetyScan.objects.get(id=scan_id)
        except MediaSafetyScan.DoesNotExist:
            return Response({"detail": "Scan not found."}, status=status.HTTP_404_NOT_FOUND)
        if scan.context in CHAT_EXCLUDED_CONTEXTS:
            # Private-messaging content is never exposed through this admin
            # surface, regardless of how the request arrived at a real
            # scan_id (e.g. a stale/guessed id) — same exclusion as the list
            # view, enforced again here rather than trusted from the caller.
            return Response({"detail": "Scan not found."}, status=status.HTTP_404_NOT_FOUND)

        storage_path = _resolve_storage_path(scan)
        if not storage_path:
            return Response({"detail": "No stored file for this scan."}, status=status.HTTP_404_NOT_FOUND)
        if not default_storage.exists(storage_path):
            return Response({"detail": "The flagged file no longer exists in storage."}, status=status.HTTP_404_NOT_FOUND)

        # Matches apps.media.services.chat_voice_playback's presign pattern:
        # prefer a native presigned GET; storage backends without one (local
        # disk dev, some non-S3 providers) fall back to .url(), which the S3
        # backend itself already presigns for non-public buckets anyway.
        presign = getattr(default_storage, "generate_presigned_get", None)
        url = presign(storage_path, self._TTL_SECONDS) if callable(presign) else default_storage.url(storage_path)

        result = scan.result if isinstance(scan.result, dict) else {}
        return Response({
            "url": url,
            "mime_type": scan.mime_type or result.get("mime_type") or "",
            "expires_in": self._TTL_SECONDS,
        })


def _serialize_scan(scan):
    result = scan.result if isinstance(scan.result, dict) else {}
    target_type = str(result.get("resolution_target") or "")
    target_id = str(result.get("resolution_id") or "")
    moderation = None
    if target_type == "broadcast_video" and target_id:
        moderation = _serialize_broadcast_video_moderation(target_id)
    return {
        "id": str(scan.id),
        "owner_id": str(scan.owner_id) if scan.owner_id else None,
        "context": scan.context,
        "original_name": scan.original_name,
        "mime_type": scan.mime_type or result.get("mime_type") or "",
        "bytes": scan.bytes,
        "provider": scan.provider,
        "status": scan.status,
        "quarantine": scan.quarantine,
        "requires_review": scan.requires_review,
        "reason": scan.reason,
        "score": result.get("score"),
        "policy_version": scan.policy_version,
        "created_at": scan.created_at.isoformat() if scan.created_at else None,
        "has_media": bool(_resolve_storage_path(scan)),
        "target_type": target_type or None,
        "target_id": target_id or None,
        "moderatable": target_type in MODERATABLE_TARGET_TYPES and bool(target_id),
        "moderation": moderation,
    }


def _serialize_broadcast_video_moderation(video_id: str) -> dict | None:
    from apps.broadcasts.models import BroadcastVideo
    from apps.broadcasts.moderation_gate import is_broadcast_eligible

    try:
        video = BroadcastVideo.objects.get(id=video_id)
    except BroadcastVideo.DoesNotExist:
        return None
    return {
        "status": video.moderation_status,
        "passed_at": video.moderation_passed_at.isoformat() if video.moderation_passed_at else None,
        "expires_at": video.moderation_expires_at.isoformat() if video.moderation_expires_at else None,
        "reviewed_by_id": str(video.moderation_reviewed_by_id) if video.moderation_reviewed_by_id else None,
        "is_broadcast_eligible": is_broadcast_eligible(video),
    }


# Maps this admin surface's Pass/Pending/Block/Delete vocabulary onto
# apps.moderation.services.apply_media_safety_action's STAFF_ACTIONS
# ("approve"/"block"/"escalate"/...) - the REAL, already-live business
# logic (strike counting, notifications, 24h-deletion scheduling, appeal-
# compatible reversal) that this admin UI now drives, rather than a second,
# parallel implementation of the same decisions.
_ACTION_TO_STAFF_ACTION = {"pass": "approve", "pending": "escalate", "block": "block"}


class AdminMediaSafetyModerateView(APIView):
    """
    POST /control/admin/media-safety/moderate/
    Body: {target_type: "broadcast_video", target_id: "<uuid>", action: "pass"|"pending"|"block"|"delete"}

    The single authoritative human-moderation action endpoint for public
    broadcast content. Resolves the MediaSafetyScan behind this content (via
    the same resolution_target/resolution_id convention the scan itself
    carries) and dispatches through apps.moderation.services.
    apply_media_safety_action, so a Pass/Block here also applies the real
    strike/notification/deletion-scheduling consequences - not a separate,
    disconnected set of BroadcastVideo-only field writes. Falls back to
    apps.broadcasts.moderation_gate directly only when no scan can be found
    (e.g. an admin proactively acting on content with no linked scan at
    all), which skips those scan-level consequences since there is no scan
    to apply them to.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "content.moderate"

    ACTIONS = {"pass", "pending", "block", "delete"}

    def post(self, request):
        from admin_control.audit.logging import AuditLogger

        target_type = str(request.data.get("target_type", "")).strip()
        target_id = str(request.data.get("target_id", "")).strip()
        action = str(request.data.get("action", "")).strip().lower()
        notes = str(request.data.get("notes", "")).strip()

        if action not in self.ACTIONS:
            return Response(
                {"detail": f"Invalid action. Choose from: {sorted(self.ACTIONS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target_type not in MODERATABLE_TARGET_TYPES or not target_id:
            return Response(
                {"detail": f"No moderation gate is wired up for target_type={target_type!r} yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.broadcasts.models import BroadcastVideo
        from apps.broadcasts.moderation_gate import apply_moderation_decision
        from apps.media.models import MediaSafetyScan
        from apps.moderation.services import apply_media_safety_action

        if not BroadcastVideo.objects.filter(id=target_id).exists():
            return Response({"detail": "Content not found."}, status=status.HTTP_404_NOT_FOUND)

        # scan.result is a JSONField - resolution_target/resolution_id are
        # stored as plain strings there, so this is a value match, not an FK
        # lookup. Most recent scan for this content, in case more than one
        # exists (a re-upload/re-scan of the same content over time).
        scan = (
            MediaSafetyScan.objects.filter(
                result__resolution_target=target_type, result__resolution_id=target_id,
            )
            .order_by("-created_at")
            .first()
        )

        if scan is not None and action in _ACTION_TO_STAFF_ACTION:
            apply_media_safety_action(
                scan, action=_ACTION_TO_STAFF_ACTION[action], actor=request.user, notes=notes, request=request,
            )
        elif scan is not None and action == "delete":
            # "delete" has no direct STAFF_ACTIONS equivalent - applies the
            # same confirmed-violation consequence as "block" (strike,
            # notification), then additionally forces the deletion sweep to
            # run on its NEXT pass instead of waiting the full
            # MEDIA_BLOCKED_CONTENT_DELETION_HOURS window, since a human
            # explicitly chose to delete now rather than merely block.
            apply_media_safety_action(scan, action="block", actor=request.user, notes=notes, request=request)
            from django.utils import timezone
            scan.scheduled_deletion_at = timezone.now()
            scan.save(update_fields=["scheduled_deletion_at", "updated_at"])
            video = BroadcastVideo.objects.filter(id=target_id).first()
            if video is not None:
                apply_moderation_decision(video, action="delete", actor=request.user, notes=notes)
        else:
            # No linked scan at all - apply directly to the content. Skips
            # the strike/notification/deletion-scheduling consequences
            # above since there's no scan for them to attach to.
            video = BroadcastVideo.objects.filter(id=target_id).first()
            if video is not None:
                apply_moderation_decision(video, action=action, actor=request.user, notes=notes)

        result_payload = {
            "target_type": target_type,
            "target_id": target_id,
            "moderation": _serialize_broadcast_video_moderation(target_id),
        }

        AuditLogger.log(
            actor=request.user,
            action_type=f"media_safety.moderate.{action}",
            target_app="broadcasts",
            target_model=target_type,
            target_pk=target_id,
            severity="warning" if action in {"block", "delete"} else "info",
            metadata={"notes": notes, "scan_id": str(scan.id) if scan else None},
        )
        return Response(result_payload)
