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

        qs = MediaSafetyScan.objects.all().order_by("-created_at")
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

        by_status = list(MediaSafetyScan.objects.values("status").annotate(count=Count("id")))
        return Response({
            "total": MediaSafetyScan.objects.count(),
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
    }
