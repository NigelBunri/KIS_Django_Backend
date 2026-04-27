# moderation/views.py
from django.db.models import Count
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from . import models, serializers

# -------------------------
# Moderation Flag Management
# -------------------------
class FlagViewSet(viewsets.ModelViewSet):
    queryset = models.Flag.objects.all()
    serializer_class = serializers.FlagSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = models.Flag.objects.all().order_by("-created_at")
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
        flag = serializer.save(
            source=serializer.validated_data.get("source") or "USER",
            reporter_id=serializer.validated_data.get("reporter_id") or self.request.user.id,
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
    permission_classes = [IsAuthenticated]


# -------------------------
# Audit Logs
# -------------------------
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.AuditLog.objects.all().order_by("-created_at")
    serializer_class = serializers.AuditLogSerializer
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]


# -------------------------
# Moderation Rules
# -------------------------
class ModerationRuleViewSet(viewsets.ModelViewSet):
    queryset = models.ModerationRule.objects.all()
    serializer_class = serializers.ModerationRuleSerializer
    permission_classes = [IsAuthenticated]


# -------------------------
# Safety Alerts
# -------------------------
class SafetyAlertViewSet(viewsets.ModelViewSet):
    queryset = models.SafetyAlert.objects.all()
    serializer_class = serializers.SafetyAlertSerializer
    permission_classes = [IsAuthenticated]

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
