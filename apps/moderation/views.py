# moderation/views.py
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from . import models, serializers
from .services import apply_media_safety_action, record_moderation_audit

try:
    from apps.broadcasts.models import ChannelModerationRecord, ChannelContent, ChannelContentComment
    from apps.broadcasts.serializers import ChannelModerationRecordSerializer
except Exception:  # pragma: no cover - optional app import guard.
    ChannelModerationRecord = None
    ChannelContent = None
    ChannelContentComment = None
    ChannelModerationRecordSerializer = None

try:
    from apps.media.models import MediaSafetyScan
    from apps.media.serializers import MediaSafetyScanSerializer
except Exception:  # pragma: no cover - optional app import guard.
    MediaSafetyScan = None
    MediaSafetyScanSerializer = None

# -------------------------
# Moderation Flag Management
# -------------------------
class FlagViewSet(viewsets.ModelViewSet):
    queryset = models.Flag.objects.all()
    serializer_class = serializers.FlagSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = models.Flag.objects.all().order_by("-created_at")
        if not self.request.user.is_staff:
            qs = qs.filter(reporter_id=self.request.user.id)
        status_filter = (self.request.query_params.get("status") or "").strip().upper()
        target_type = (self.request.query_params.get("target_type") or "").strip().upper()
        severity = (self.request.query_params.get("severity") or "").strip().upper()
        reporter_id = (self.request.query_params.get("reporter_id") or "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        if target_type:
            qs = qs.filter(target_type=target_type)
        if severity:
            qs = qs.filter(severity=severity)
        if reporter_id:
            qs = qs.filter(reporter_id=reporter_id)
        return qs

    @swagger_auto_schema(
        operation_description="Mark a flag as reviewed by moderator.",
        responses={200: serializers.FlagSerializer}
    )
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        if not request.user.is_staff:
            return Response({"detail": "Moderator access required."}, status=status.HTTP_403_FORBIDDEN)
        flag = self.get_object()
        flag.status = "REVIEWED"
        flag.reviewed_at = timezone.now()
        flag.save()
        models.AuditLog.objects.create(
            actor_id=request.user.id,
            action="moderation.flag.review",
            target_type="FLAG",
            target_id=flag.id,
            metadata={"target_type": flag.target_type, "target_id": str(flag.target_id)},
        )
        return Response(serializers.FlagSerializer(flag).data)

    @swagger_auto_schema(
        operation_description="Resolve a flag and optionally schedule an automatic moderation action.",
        responses={200: serializers.FlagSerializer}
    )
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        if not request.user.is_staff:
            return Response({"detail": "Moderator access required."}, status=status.HTTP_403_FORBIDDEN)
        flag = self.get_object()
        flag.status = "ACTIONED"
        flag.resolved_at = timezone.now()
        flag.save()
        models.AuditLog.objects.create(
            actor_id=request.user.id,
            action="moderation.flag.resolve",
            target_type="FLAG",
            target_id=flag.id,
            metadata={"target_type": flag.target_type, "target_id": str(flag.target_id)},
        )
        return Response(serializers.FlagSerializer(flag).data)

    def perform_create(self, serializer):
        source = serializer.validated_data.get("source") or "USER"
        reporter_id = serializer.validated_data.get("reporter_id") or self.request.user.id
        if not self.request.user.is_staff:
            source = "USER"
            reporter_id = self.request.user.id
        flag = serializer.save(
            source=source,
            reporter_id=reporter_id,
        )
        models.AuditLog.objects.create(
            actor_id=self.request.user.id,
            action="moderation.flag.create",
            target_type="FLAG",
            target_id=flag.id,
            metadata={"flag_target_type": flag.target_type, "flag_target_id": str(flag.target_id)},
        )

    @action(detail=False, methods=["get"], url_path="queue-summary")
    def queue_summary(self, request):
        qs = self.get_queryset().filter(status="PENDING")
        by_target_type = list(qs.values("target_type").annotate(count=Count("id")).order_by("target_type"))
        by_severity = list(qs.values("severity").annotate(count=Count("id")).order_by("severity"))
        return Response(
            {
                "pending_count": qs.count(),
                "by_target_type": by_target_type,
                "by_severity": by_severity,
            },
            status=status.HTTP_200_OK,
        )


# -------------------------
# Moderation Actions
# -------------------------
class ModerationActionViewSet(viewsets.ModelViewSet):
    queryset = models.ModerationAction.objects.all()
    serializer_class = serializers.ModerationActionSerializer
    permission_classes = [IsAdminUser]


# -------------------------
# Audit Logs
# -------------------------
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.AuditLog.objects.all().order_by("-created_at")
    serializer_class = serializers.AuditLogSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        qs = super().get_queryset()
        actor_id = (self.request.query_params.get("actor_id") or "").strip()
        action = (self.request.query_params.get("action") or "").strip()
        target_type = (self.request.query_params.get("target_type") or "").strip().upper()
        if actor_id:
            qs = qs.filter(actor_id=actor_id)
        if action:
            qs = qs.filter(action__icontains=action)
        if target_type:
            qs = qs.filter(target_type=target_type)
        return qs


# -------------------------
# Reputation
# -------------------------
class UserReputationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.UserReputation.objects.all()
    serializer_class = serializers.UserReputationSerializer
    permission_classes = [IsAdminUser]


# -------------------------
# Moderation Rules
# -------------------------
class ModerationRuleViewSet(viewsets.ModelViewSet):
    queryset = models.ModerationRule.objects.all()
    serializer_class = serializers.ModerationRuleSerializer
    permission_classes = [IsAdminUser]


# -------------------------
# Safety Alerts
# -------------------------
class SafetyAlertViewSet(viewsets.ModelViewSet):
    queryset = models.SafetyAlert.objects.all()
    serializer_class = serializers.SafetyAlertSerializer
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Acknowledge a safety alert.",
        responses={200: serializers.SafetyAlertSerializer}
    )
    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        alert.acknowledged_at = timezone.now()
        alert.save()
        return Response(serializers.SafetyAlertSerializer(alert).data)


class UserBlockViewSet(viewsets.ModelViewSet):
    queryset = models.UserBlock.objects.all()
    serializer_class = serializers.UserBlockSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        blocked = serializer.validated_data["blocked"]
        reason = serializer.validated_data.get("reason", "")
        block, created = models.UserBlock.objects.get_or_create(
            blocker=request.user,
            blocked=blocked,
            defaults={"reason": reason},
        )
        if not created and reason and block.reason != reason:
            block.reason = reason
            block.save(update_fields=["reason", "updated_at"])
        if created:
            models.AuditLog.objects.create(
                actor_id=request.user.id,
                action="moderation.user_block.create",
                target_type="USER",
                target_id=block.blocked_id,
                metadata={"block_id": str(block.id)},
            )
        data = self.get_serializer(block).data
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(data, status=response_status)

    def perform_create(self, serializer):
        block = serializer.save(blocker=self.request.user)
        models.AuditLog.objects.create(
            actor_id=self.request.user.id,
            action="moderation.user_block.create",
            target_type="USER",
            target_id=block.blocked_id,
            metadata={"block_id": str(block.id)},
        )

    def get_queryset(self):
        return models.UserBlock.objects.filter(blocker=self.request.user)


def _staff_queue_media_rows(limit: int):
    if MediaSafetyScan is None or MediaSafetyScanSerializer is None:
        return []
    query = MediaSafetyScan.objects.select_related("owner", "asset").filter(is_deleted=False).filter(
        Q(status__in=["pending_review", "blocked", "failed"]) | Q(quarantine=True) | Q(requires_review=True)
    )
    rows = []
    for scan in query.order_by("-created_at")[:limit]:
        rows.append(
            {
                "kind": "media_safety_scan",
                "id": str(scan.id),
                "target_type": "media_safety_scan",
                "target_id": str(scan.id),
                "status": scan.status,
                "severity": "HIGH" if scan.status in {"blocked", "failed"} else "MEDIUM",
                "reason": scan.reason or "Media requires family-safety review.",
                "source": "MEDIA_SAFETY",
                "created_at": scan.created_at.isoformat(),
                "context": scan.context,
                "metadata": {
                    "owner_id": str(scan.owner_id or ""),
                    "upload_id": scan.upload_id,
                    "mime_type": scan.mime_type,
                    "bytes": scan.bytes,
                    "quarantine": scan.quarantine,
                    "requires_review": scan.requires_review,
                    "policy_version": scan.policy_version,
                },
                "raw": MediaSafetyScanSerializer(scan).data,
            }
        )
    return rows


def _staff_queue_flag_rows(limit: int):
    rows = []
    query = models.Flag.objects.filter(is_deleted=False).filter(status__in=["PENDING", "REVIEWED"]).order_by("-created_at")
    for flag in query[:limit]:
        rows.append(
            {
                "kind": "flag",
                "id": str(flag.id),
                "target_type": str(flag.target_type).lower(),
                "target_id": str(flag.target_id),
                "status": flag.status,
                "severity": flag.severity,
                "reason": flag.reason,
                "source": flag.source,
                "created_at": flag.created_at.isoformat(),
                "context": (flag.tags or {}).get("source", ""),
                "metadata": flag.tags or {},
                "raw": serializers.FlagSerializer(flag).data,
            }
        )
    return rows


def _staff_queue_channel_rows(limit: int):
    if ChannelModerationRecord is None or ChannelModerationRecordSerializer is None:
        return []
    query = ChannelModerationRecord.objects.select_related("channel", "content", "comment", "reporter", "actor").filter(
        status__in=["open", "reviewing"]
    )
    rows = []
    for record in query.order_by("-created_at")[:limit]:
        rows.append(
            {
                "kind": "channel_moderation_record",
                "id": str(record.id),
                "target_type": record.target_type,
                "target_id": str(record.target_id),
                "status": record.status,
                "severity": "MEDIUM",
                "reason": record.reason,
                "source": "CHANNEL",
                "created_at": record.created_at.isoformat(),
                "context": "channel",
                "metadata": {
                    "channel_id": str(record.channel_id),
                    "content_id": str(record.content_id or ""),
                    "comment_id": str(record.comment_id or ""),
                    **(record.metadata if isinstance(record.metadata, dict) else {}),
                },
                "raw": ChannelModerationRecordSerializer(record).data,
            }
        )
    return rows


class StaffModerationOperationsQueueView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        limit = min(max(int(request.query_params.get("limit") or 50), 1), 100)
        source = str(request.query_params.get("source") or "all").strip().lower()
        rows = []
        if source in {"all", "flags"}:
            rows.extend(_staff_queue_flag_rows(limit))
        if source in {"all", "media", "media_safety"}:
            rows.extend(_staff_queue_media_rows(limit))
        if source in {"all", "channels", "channel"}:
            rows.extend(_staff_queue_channel_rows(limit))
        rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        rows = rows[:limit]
        return Response(
            {
                "results": rows,
                "summary": {
                    "total": len(rows),
                    "flags": sum(1 for row in rows if row["kind"] == "flag"),
                    "media_safety": sum(1 for row in rows if row["kind"] == "media_safety_scan"),
                    "channels": sum(1 for row in rows if row["kind"] == "channel_moderation_record"),
                },
            }
        )


class StaffModerationOperationActionView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = serializers.StaffModerationActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_type = serializer.validated_data["target_type"]
        target_id = serializer.validated_data["target_id"]
        action = serializer.validated_data["action"]
        notes = serializer.validated_data.get("notes") or ""

        if target_type == "media_safety_scan":
            if MediaSafetyScan is None or MediaSafetyScanSerializer is None:
                return Response({"detail": "Media safety is unavailable."}, status=status.HTTP_400_BAD_REQUEST)
            scan = MediaSafetyScan.objects.get(id=target_id)
            scan = apply_media_safety_action(scan, action=action, actor=request.user, notes=notes, request=request)
            return Response({"ok": True, "target_type": target_type, "result": MediaSafetyScanSerializer(scan).data})

        if target_type == "flag":
            flag = models.Flag.objects.get(id=target_id)
            if action == "dismiss":
                flag.status = "DISMISSED"
                flag.resolved_at = timezone.now()
            elif action == "review":
                flag.status = "REVIEWED"
                flag.reviewed_at = timezone.now()
            else:
                flag.status = "ACTIONED"
                flag.resolved_at = timezone.now()
                if action == "escalate":
                    flag.escalation_level = "ADMIN"
            flag.save(update_fields=["status", "reviewed_at", "resolved_at", "escalation_level", "updated_at"])
            record_moderation_audit(
                actor=request.user,
                action=f"moderation.flag.{action}",
                target_type="FLAG",
                target_id=flag.id,
                metadata={"notes": notes[:2000], "flag_target_type": flag.target_type, "flag_target_id": str(flag.target_id)},
                request=request,
            )
            return Response({"ok": True, "target_type": target_type, "result": serializers.FlagSerializer(flag).data})

        if target_type == "channel_moderation_record":
            if ChannelModerationRecord is None or ChannelModerationRecordSerializer is None:
                return Response({"detail": "Channel moderation is unavailable."}, status=status.HTTP_400_BAD_REQUEST)
            record = ChannelModerationRecord.objects.select_related("content", "comment").get(id=target_id)
            if action == "block":
                if record.comment_id and ChannelContentComment is not None:
                    ChannelContentComment.objects.filter(id=record.comment_id).update(is_deleted=True)
                if record.content_id and ChannelContent is not None:
                    ChannelContent.objects.filter(id=record.content_id).update(is_deleted=True, visibility="private", status="archived")
                record.action = "remove"
                record.status = "actioned"
            elif action == "approve":
                record.action = "keep"
                record.status = "dismissed"
            elif action == "dismiss":
                record.action = "keep"
                record.status = "dismissed"
            elif action == "escalate":
                record.action = "none"
                record.status = "reviewing"
            else:
                record.status = "reviewing"
            record.actor = request.user
            record.notes = notes[:2000]
            record.resolved_at = timezone.now() if record.status in {"actioned", "dismissed"} else None
            record.save(update_fields=["action", "status", "actor", "notes", "resolved_at", "updated_at"])
            record_moderation_audit(
                actor=request.user,
                action=f"channel_moderation.staff.{action}",
                target_type=record.target_type,
                target_id=record.target_id,
                metadata={"record_id": str(record.id), "notes": notes[:2000]},
                request=request,
            )
            return Response({"ok": True, "target_type": target_type, "result": ChannelModerationRecordSerializer(record).data})

        return Response({"detail": "Unsupported moderation target."}, status=status.HTTP_400_BAD_REQUEST)
