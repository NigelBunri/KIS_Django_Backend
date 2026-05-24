"""Admin content moderation views — global queue, takedowns, sanctions."""
from __future__ import annotations

from django.core.paginator import Paginator
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.audit.logging import AuditLogger
from admin_control.permissions import IsAdminControlUser


def _safe_int(val, default, lo=1, hi=250):
    try:
        return max(lo, min(int(val), hi))
    except (TypeError, ValueError):
        return default


class AdminContentQueueView(APIView):
    """
    GET /control/admin/content/queue/
    Returns all pending/escalated moderation flags platform-wide.
    Query params: severity, target_type, status, page, per_page
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "content.moderate"

    def get(self, request):
        from apps.moderation.models import Flag

        qs = Flag.objects.all().order_by("-created_at")

        flag_status = request.query_params.get("status", "PENDING")
        if flag_status:
            qs = qs.filter(status=flag_status.upper())
        severity = request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        target_type = request.query_params.get("target_type")
        if target_type:
            qs = qs.filter(target_type=target_type)

        page_num = _safe_int(request.query_params.get("page", 1), 1, lo=1, hi=10000)
        per_page = _safe_int(request.query_params.get("per_page", 25), 25, lo=1, hi=100)
        paginator = Paginator(qs, per_page)
        page_obj = paginator.get_page(page_num)

        items = [_serialize_flag(f) for f in page_obj.object_list]
        return Response({
            "flags": items,
            "pagination": {
                "page": page_obj.number,
                "per_page": per_page,
                "total_pages": paginator.num_pages,
                "total_items": paginator.count,
            },
        })


class AdminContentQueueSummaryView(APIView):
    """
    GET /control/admin/content/summary/
    Returns counts by severity and type.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "content.moderate"

    def get(self, request):
        from django.db.models import Count
        from apps.moderation.models import Flag

        by_severity = list(Flag.objects.filter(status="PENDING").values("severity").annotate(count=Count("id")))
        by_type = list(Flag.objects.filter(status="PENDING").values("target_type").annotate(count=Count("id")))
        total_pending = Flag.objects.filter(status="PENDING").count()
        total_critical = Flag.objects.filter(status="PENDING", severity="critical").count()
        actioned_today = Flag.objects.filter(
            status__in=["ACTIONED", "DISMISSED"],
            updated_at__date=timezone.now().date(),
        ).count()
        return Response({
            "total_pending": total_pending,
            "total_critical": total_critical,
            "actioned_today": actioned_today,
            "by_severity": by_severity,
            "by_type": by_type,
        })


class AdminContentActionView(APIView):
    """
    POST /control/admin/content/flags/<flag_id>/action/
    Body: {action: "dismiss" | "takedown" | "warn" | "restrict" | "suspend" | "ban", notes}
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "content.moderate"

    def post(self, request, flag_id):
        from apps.moderation.models import Flag, ModerationAction

        try:
            flag = Flag.objects.get(id=flag_id)
        except Flag.DoesNotExist:
            return Response({"detail": "Flag not found."}, status=status.HTTP_404_NOT_FOUND)

        action = str(request.data.get("action", "")).strip().lower()
        notes = str(request.data.get("notes", "")).strip()
        valid_actions = {"dismiss", "takedown", "warn", "restrict", "suspend", "ban"}
        if action not in valid_actions:
            return Response(
                {"detail": f"Invalid action. Choose from: {sorted(valid_actions)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if action == "dismiss":
            flag.status = "DISMISSED"
        else:
            flag.status = "ACTIONED"

        flag.reviewed_at = timezone.now()
        flag.save(update_fields=["status", "reviewed_at"])

        if action in {"warn", "restrict", "suspend", "ban"}:
            moderation_action_map = {
                "warn": "WARN",
                "restrict": "TEMP_RESTRICT",
                "suspend": "SUSPEND",
                "ban": "BAN",
            }
            ModerationAction.objects.create(
                flag=flag,
                action=moderation_action_map[action],
                notes=notes,
                performed_by_id=request.user.id,
            )
            # Update target user status if ban/suspend
            if action in {"suspend", "ban"} and flag.target_type == "user":
                _apply_user_status(flag.target_id, "banned" if action == "ban" else "suspended")

        if action == "takedown":
            _apply_content_takedown(flag)

        AuditLogger.log(
            actor=request.user,
            action_type=f"content.{action}",
            target_app="moderation",
            target_model="Flag",
            target_pk=str(flag.id),
            severity="warning" if action in {"ban", "suspend"} else "info",
            metadata={"notes": notes, "target_type": flag.target_type, "target_id": str(flag.target_id or "")},
        )

        return Response({
            "flag": _serialize_flag(flag),
            "action_taken": action,
            "notes": notes,
        })


class AdminContentTrendView(APIView):
    """
    GET /control/admin/content/trends/
    30-day content volume and moderation activity trends.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "content.moderate"

    def get(self, request):
        from datetime import timedelta
        from django.db.models import Count
        from apps.moderation.models import Flag

        now = timezone.now()
        series = []
        for i in range(30, 0, -1):
            day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            new_flags = Flag.objects.filter(created_at__gte=day_start, created_at__lt=day_end).count()
            actioned = Flag.objects.filter(
                updated_at__gte=day_start,
                updated_at__lt=day_end,
                status__in=["ACTIONED", "DISMISSED"],
            ).count()
            series.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "new_flags": new_flags,
                "actioned": actioned,
            })

        by_type = list(
            Flag.objects.filter(created_at__gte=now - timedelta(days=30))
            .values("target_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        return Response({
            "series_30d": series,
            "by_type_30d": by_type,
            "generated_at": now.isoformat(),
        })


# ── helpers ──────────────────────────────────────────────────────────────────

def _serialize_flag(flag):
    return {
        "id": str(flag.id),
        "target_type": flag.target_type,
        "target_id": str(flag.target_id) if flag.target_id else None,
        "source": getattr(flag, "source", "user"),
        "severity": flag.severity,
        "status": flag.status,
        "reason": getattr(flag, "reason", ""),
        "tags": getattr(flag, "tags", []),
        "ai_score": getattr(flag, "ai_score", None),
        "reporter_id": str(flag.reporter_id) if flag.reporter_id else None,
        "reporter_email": None,
        "reviewed_at": flag.reviewed_at.isoformat() if getattr(flag, "reviewed_at", None) else None,
        "created_at": flag.created_at.isoformat() if flag.created_at else None,
    }


def _apply_user_status(target_id, new_status: str):
    from apps.accounts.models import User
    try:
        User.objects.filter(id=target_id).update(status=new_status)
    except Exception:
        pass


def _apply_content_takedown(flag):
    """Mark the flagged content as deleted/hidden based on target_type."""
    target_type = flag.target_type
    target_id = flag.target_id
    if not target_id:
        return
    try:
        if target_type == "post":
            from apps.partners.models import PartnerPost
            PartnerPost.objects.filter(id=target_id).update(is_deleted=True)
        elif target_type == "status":
            from apps.statuses.models import Status
            Status.objects.filter(id=target_id).update(is_deleted=True)
        elif target_type == "channel":
            from apps.channels.models import Channel
            Channel.objects.filter(id=target_id).update(is_archived=True)
    except Exception:
        pass
