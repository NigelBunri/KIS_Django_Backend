"""Admin bot-platform oversight — platform-wide KIS Bot Platform
visibility for superadmins. Mirrors partner_oversight.py's own shape
exactly (same permission class, same list/detail/stats convention,
same pagination helper) - see that file for the established pattern
this one follows.

Privacy posture (Phase 5 requirement, deliberate): nothing here ever
returns knowledge-source extracted_text, memory-entry content, an AI
connection's api_key, a webhook secret, or a credential's plaintext/
hash. Only metadata (counts, statuses, timestamps) crosses this
boundary - see _serialize_bot(full=True) below for exactly what is
and isn't included.
"""
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


class AdminBotListView(APIView):
    """
    GET /control/admin/bots/
    Platform-wide bot list. Query params: q, status, owner_id, page, per_page.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "bots.view"

    def get(self, request):
        from apps.bots.models import Bot, BotStatus

        qs = Bot.objects.select_related("owner").exclude(status=BotStatus.DELETED).order_by("-created_at")

        q = request.query_params.get("q", "").strip()
        if q:
            from django.db.models import Q as DQ
            qs = qs.filter(DQ(name__icontains=q) | DQ(handle__icontains=q) | DQ(owner__email__icontains=q))

        status_filter = request.query_params.get("status")
        if status_filter in BotStatus.values:
            qs = qs.filter(status=status_filter)

        owner_id = request.query_params.get("owner_id")
        if owner_id:
            qs = qs.filter(owner_id=owner_id)

        page_num = _safe_int(request.query_params.get("page", 1), 1, lo=1, hi=10000)
        per_page = _safe_int(request.query_params.get("per_page", 25), 25, lo=1, hi=100)
        paginator = Paginator(qs, per_page)
        page_obj = paginator.get_page(page_num)

        return Response({
            "bots": [_serialize_bot(b) for b in page_obj.object_list],
            "pagination": {
                "page": page_obj.number,
                "per_page": per_page,
                "total_pages": paginator.num_pages,
                "total_items": paginator.count,
            },
        })


class AdminBotDetailView(APIView):
    """
    GET   /control/admin/bots/<bot_id>/  — full investigation view.
    PATCH /control/admin/bots/<bot_id>/  — suspend/reinstate (status change only).
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "bots.view"

    def get(self, request, bot_id):
        bot = _get_bot_or_404(bot_id)
        if isinstance(bot, Response):
            return bot

        from apps.bots.models import (
            BotActivityLog, BotAIConnection, BotApprovalRequest, BotKnowledgeSource,
            BotMemoryConfig, BotMemoryEntry, BotResourceGrant, BotWebhookDelivery,
            BotWorkflow, BotWorkflowRun,
        )
        from apps.bots.services import resolve_resource_display_name

        grants = BotResourceGrant.objects.filter(bot=bot).order_by("-created_at")
        resource_connections = [
            {
                "id": str(g.id), "resource_type": g.resource_type, "resource_id": g.resource_id,
                "resource_display_name": resolve_resource_display_name(g.resource_type, g.resource_id),
                "scopes": g.scopes, "is_active": g.is_active,
                "granted_by_id": str(g.granted_by_id) if g.granted_by_id else None,
                "created_at": g.created_at.isoformat(), "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
            }
            for g in grants[:50]
        ]

        workflows = list(
            BotWorkflow.objects.filter(bot=bot).order_by("-created_at")
            .values("id", "name", "trigger_type", "status", "version", "created_at", "last_run_at")[:50]
        )

        recent_runs = list(
            BotWorkflowRun.objects.filter(workflow__bot=bot).select_related("workflow")
            .order_by("-started_at")
            .values("id", "workflow__name", "trigger_event", "status", "started_at")[:20]
        )

        webhook_deliveries = list(
            BotWebhookDelivery.objects.filter(subscription__bot=bot).order_by("-created_at")
            .values("id", "event", "status", "attempt_count", "response_code", "created_at")[:20]
        )

        credential = getattr(bot, "credential", None)
        credential_meta = None
        if credential:
            credential_meta = {
                "client_id": credential.client_id, "is_active": credential.is_active,
                "created_at": credential.created_at.isoformat(),
                "rotated_at": credential.rotated_at.isoformat() if credential.rotated_at else None,
                "revoked_at": credential.revoked_at.isoformat() if credential.revoked_at else None,
                "last_used_at": credential.last_used_at.isoformat() if credential.last_used_at else None,
            }

        ai_connection = getattr(bot, "ai_connection", None)
        ai_meta = None
        if ai_connection:
            ai_meta = {
                "provider": ai_connection.provider, "model": ai_connection.model,
                "is_active": ai_connection.is_active, "has_api_key": bool(ai_connection.api_key_encrypted),
                "daily_request_limit": ai_connection.daily_request_limit,
            }

        from django.db.models import Count as _Count

        knowledge_qs = BotKnowledgeSource.objects.filter(bot=bot)
        knowledge_meta = {
            "total_sources": knowledge_qs.count(),
            "by_status": dict(knowledge_qs.values_list("status").annotate(c=_Count("id"))),
        }

        memory_config = BotMemoryConfig.objects.filter(bot=bot).first()
        memory_meta = {
            "enabled": memory_config.enabled if memory_config else False,
            "allow_message_content": memory_config.allow_message_content if memory_config else False,
            "retention_days": memory_config.retention_days if memory_config else None,
            "entry_count": BotMemoryEntry.objects.filter(bot=bot).count(),
        }

        abuse_history = list(
            BotActivityLog.objects.filter(
                bot=bot, action__in=["abuse_detected", "suspended_by_admin", "auth_failed", "action_rate_limited"],
            ).order_by("-created_at").values("id", "action", "created_at", "metadata")[:20]
        )

        pending_approvals_count = BotApprovalRequest.objects.filter(bot=bot, status="pending").count()

        AuditLogger.log(
            actor=request.user, action_type="bot.admin_viewed",
            target_app="bots", target_model="Bot", target_pk=str(bot.id),
        )

        return Response({
            "bot": _serialize_bot(bot, full=True),
            "resource_connections": resource_connections,
            "workflows": workflows,
            "recent_runs": recent_runs,
            "pending_approvals_count": pending_approvals_count,
            "webhook_deliveries": webhook_deliveries,
            "credential": credential_meta,
            "ai_connection": ai_meta,
            "knowledge": knowledge_meta,
            "memory": memory_meta,
            "abuse_history": abuse_history,
        })

    def patch(self, request, bot_id):
        from apps.bots import services
        from apps.bots.models import BotStatus

        bot = _get_bot_or_404(bot_id)
        if isinstance(bot, Response):
            return bot

        new_status = request.data.get("status")
        if new_status not in (BotStatus.SUSPENDED, BotStatus.ACTIVE, BotStatus.PAUSED, BotStatus.DISCONNECTED):
            return Response({"detail": "status must be one of suspended, active, paused, disconnected."}, status=status.HTTP_400_BAD_REQUEST)

        reason = str(request.data.get("reason") or "")[:500]
        services.set_status_admin(bot, request.user, new_status, reason=reason)
        AuditLogger.log(
            actor=request.user, action_type="bot.admin_status_changed",
            target_app="bots", target_model="Bot", target_pk=str(bot.id),
            metadata={"new_status": new_status, "reason": reason},
        )
        bot.refresh_from_db()
        return Response({"bot": _serialize_bot(bot)})


class AdminBotCredentialRevokeView(APIView):
    """
    POST /control/admin/bots/<bot_id>/credential/revoke/
    Immediately revokes the bot's OAuth2 credential - takes effect on
    the very next authentication attempt (apps.bots.auth checks
    is_active on every call, not just at issuance time).
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "bots.view"

    def post(self, request, bot_id):
        from apps.bots.models import BotCredential

        bot = _get_bot_or_404(bot_id)
        if isinstance(bot, Response):
            return bot

        updated = BotCredential.objects.filter(bot=bot, is_active=True).update(
            is_active=False, revoked_at=timezone.now(),
        )
        AuditLogger.log(
            actor=request.user, action_type="bot.admin_credential_revoked",
            target_app="bots", target_model="BotCredential", target_pk=str(bot.id),
            metadata={"revoked": bool(updated)},
        )
        return Response({"revoked": bool(updated)})


class AdminBotNoteView(APIView):
    """
    POST /control/admin/bots/<bot_id>/notes/
    Records an administrative note - stored as an immutable audit
    entry (admin_control's own AdminAuditEntry), not a separately
    editable/deletable row, so it can't be quietly altered later.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "bots.view"

    def post(self, request, bot_id):
        bot = _get_bot_or_404(bot_id)
        if isinstance(bot, Response):
            return bot

        note = str(request.data.get("note") or "").strip()
        if not note:
            return Response({"detail": "note is required."}, status=status.HTTP_400_BAD_REQUEST)

        entry = AuditLogger.log(
            actor=request.user, action_type="bot.admin_note",
            target_app="bots", target_model="Bot", target_pk=str(bot.id),
            metadata={"note": note[:2000]},
        )
        return Response({"id": str(entry.id), "note": note, "created_at": entry.created_at.isoformat()}, status=status.HTTP_201_CREATED)


class AdminBotStatsView(APIView):
    """
    GET /control/admin/bots/stats/
    Platform-wide KIS Bot Platform KPIs + 30-day trend series.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "bots.view"

    def get(self, request):
        from datetime import timedelta
        from django.db.models import Count
        from apps.bots.models import (
            Bot, BotAIUsageLog, BotAIUsageLogStatus, BotApprovalRequest, BotApprovalStatus,
            BotStatus, BotWebhookDelivery, BotWebhookDeliveryStatus, BotWorkflowRun, BotWorkflowRunStatus,
        )

        now = timezone.now()
        bots_qs = Bot.objects.exclude(status=BotStatus.DELETED)
        total_bots = bots_qs.count()
        status_counts = dict(bots_qs.values_list("status").annotate(c=Count("id")))

        since_7d = now - timedelta(days=7)
        since_30d = now - timedelta(days=30)

        runs_qs = BotWorkflowRun.objects.all()
        execution_volume_30d = runs_qs.filter(started_at__gte=since_30d).count()
        failed_execution_volume_30d = runs_qs.filter(started_at__gte=since_30d, status=BotWorkflowRunStatus.FAILED).count()

        webhook_failures_30d = BotWebhookDelivery.objects.filter(
            created_at__gte=since_30d,
            status__in=[BotWebhookDeliveryStatus.FAILED, BotWebhookDeliveryStatus.DEAD_LETTER],
        ).count()

        ai_usage_30d = BotAIUsageLog.objects.filter(created_at__gte=since_30d).count()
        ai_errors_30d = BotAIUsageLog.objects.filter(created_at__gte=since_30d, status=BotAIUsageLogStatus.ERROR).count()

        pending_approvals = BotApprovalRequest.objects.filter(status=BotApprovalStatus.PENDING).count()

        creation_series = []
        execution_series = []
        for i in range(30, 0, -1):
            day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            creation_series.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "new_bots": bots_qs.filter(created_at__gte=day_start, created_at__lt=day_end).count(),
            })
            execution_series.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "executions": runs_qs.filter(started_at__gte=day_start, started_at__lt=day_end).count(),
                "failed": runs_qs.filter(started_at__gte=day_start, started_at__lt=day_end, status=BotWorkflowRunStatus.FAILED).count(),
            })

        AuditLogger.log(actor=request.user, action_type="bot.admin_stats_viewed", target_app="bots")

        return Response({
            "total_bots": total_bots,
            "active_bots": status_counts.get(BotStatus.ACTIVE, 0),
            "paused_bots": status_counts.get(BotStatus.PAUSED, 0),
            "suspended_bots": status_counts.get(BotStatus.SUSPENDED, 0),
            "disconnected_bots": status_counts.get(BotStatus.DISCONNECTED, 0),
            "execution_volume_30d": execution_volume_30d,
            "failed_execution_volume_30d": failed_execution_volume_30d,
            "webhook_failures_30d": webhook_failures_30d,
            "ai_usage_30d": ai_usage_30d,
            "ai_errors_30d": ai_errors_30d,
            "pending_approvals": pending_approvals,
            "creation_series_30d": creation_series,
            "execution_series_30d": execution_series,
            "generated_at": now.isoformat(),
        })


class AdminBotAbuseMonitoringView(APIView):
    """
    GET /control/admin/bots/abuse/
    Platform-wide abuse/security signal feed - rate-limit hits,
    repeated auth failures, auto-suspensions, denied actions. No
    event/message content, only the activity-log action type and
    non-sensitive metadata already recorded at write time.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "bots.view"

    def get(self, request):
        from apps.bots.models import BotActivityLog

        abuse_actions = [
            "abuse_detected", "suspended_by_admin", "auth_failed",
            "action_rate_limited", "action_denied", "credential_revoked", "resource_revoked",
        ]
        qs = BotActivityLog.objects.filter(action__in=abuse_actions).select_related("bot").order_by("-created_at")

        page_num = _safe_int(request.query_params.get("page", 1), 1, lo=1, hi=10000)
        per_page = _safe_int(request.query_params.get("per_page", 50), 50, lo=1, hi=200)
        paginator = Paginator(qs, per_page)
        page_obj = paginator.get_page(page_num)

        events = [
            {
                "id": str(e.id), "bot_id": str(e.bot_id), "bot_name": e.bot.name if e.bot_id else None,
                "action": e.action, "created_at": e.created_at.isoformat(), "metadata": e.metadata,
            }
            for e in page_obj.object_list
        ]
        return Response({
            "events": events,
            "pagination": {
                "page": page_obj.number, "per_page": per_page,
                "total_pages": paginator.num_pages, "total_items": paginator.count,
            },
        })


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_bot_or_404(bot_id):
    from apps.bots.models import Bot
    try:
        return Bot.objects.select_related("owner").get(id=bot_id)
    except Exception:
        return Response({"detail": "Bot not found."}, status=status.HTTP_404_NOT_FOUND)


def _serialize_bot(bot, full: bool = False):
    base = {
        "id": str(bot.id),
        "name": bot.name,
        "handle": bot.handle,
        "kind": bot.kind,
        "status": bot.status,
        "created_at": bot.created_at.isoformat() if bot.created_at else None,
        "owner": {
            "id": str(bot.owner_id) if bot.owner_id else None,
            "email": bot.owner.email if hasattr(bot, "owner") and bot.owner else None,
            "phone": bot.owner.phone if hasattr(bot, "owner") and bot.owner else None,
            "tier": bot.owner.tier if hasattr(bot, "owner") and bot.owner else None,
        } if bot.owner_id else None,
    }
    if full:
        base["description"] = getattr(bot, "description", "")
        base["partner_id"] = str(bot.partner_id) if getattr(bot, "partner_id", None) else None
        base["deleted_at"] = bot.deleted_at.isoformat() if getattr(bot, "deleted_at", None) else None
    return base
