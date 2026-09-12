"""Audit trail and suspicious activity views."""
from django.core.paginator import Paginator
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.audit.logging import AuditLogger
from admin_control.models import AdminAuditEntry, SuspiciousActivityFlag
from admin_control.permissions import IsAdminControlUser
from admin_control.serializers import (
    AuditActionSerializer,
    AuditEntrySerializer,
    SuspiciousActivityFlagSerializer,
)


def _recent_warning_notifications(limit: int = 100) -> list[dict]:
    """The actual moderation warning/suspension notifications sent to
    users (apps.moderation.services.apply_ai_flag_consequence), with who
    received each one - an admin reviewing suspicious activity should see
    what was actually communicated, not just that "a warning was issued"
    in an audit log line. Reuses apps.notifications' real Notification
    rows rather than a second, parallel record of what was sent."""
    from apps.accounts.models import User
    from apps.notifications.models import Notification

    rows = list(
        Notification.objects.filter(type__in=["MODERATION_WARNING", "MODERATION_SUSPENSION"])
        .order_by("-created_at")
        .values("id", "user_id", "type", "title", "body", "created_at")[:limit]
    )
    user_ids = {row["user_id"] for row in rows}
    users_by_id = {
        u.id: u for u in User.objects.filter(id__in=user_ids).only("id", "email", "display_name", "phone")
    }
    results = []
    for row in rows:
        user = users_by_id.get(row["user_id"])
        results.append({
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "user_display_name": (user.display_name or user.email or user.phone) if user else None,
            "type": row["type"],
            "title": row["title"],
            "body": row["body"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })
    return results


class AuditTrailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminControlUser]

    def get(self, request):
        actor_id = request.query_params.get("actor")
        severity = request.query_params.get("severity")
        page = max(1, int(request.query_params.get("page", 1)))
        per_page = max(1, int(request.query_params.get("per_page", 25)))
        queryset = AdminAuditEntry.objects.all()
        if actor_id:
            queryset = queryset.filter(actor_id=actor_id)
        if severity:
            queryset = queryset.filter(severity=severity)
        queryset = queryset.order_by("-created_at")
        paginator = Paginator(queryset, per_page)
        page_obj = paginator.get_page(page)
        serializer = AuditEntrySerializer(page_obj.object_list, many=True)
        return Response(
            {
                "entries": serializer.data,
                "pagination": {
                    "page": page_obj.number,
                    "per_page": per_page,
                    "total_pages": paginator.num_pages,
                    "total_items": paginator.count,
                },
            }
        )


class SuspiciousActivityView(APIView):
    permission_classes = [IsAuthenticated, IsAdminControlUser]

    def get(self, request):
        resolved = request.query_params.get("resolved")
        queryset = SuspiciousActivityFlag.objects.all()
        if resolved in {"true", "false"}:
            queryset = queryset.filter(resolved=(resolved == "true"))
        queryset = queryset.order_by("-created_at")
        serializer = SuspiciousActivityFlagSerializer(queryset, many=True)
        return Response({
            "flags": serializer.data,
            "warning_notifications": _recent_warning_notifications(),
        })

    def patch(self, request):
        flag_id = request.data.get("id")
        if not flag_id:
            return Response({"detail": "flag id is required"}, status=400)
        try:
            flag = SuspiciousActivityFlag.objects.get(pk=flag_id)
        except SuspiciousActivityFlag.DoesNotExist:
            return Response({"detail": "not found"}, status=404)
        flag.resolved = request.data.get("resolved", True)
        if flag.resolved and not flag.acknowledged_at:
            flag.acknowledged_at = timezone.now()
        flag.save(update_fields=["resolved", "acknowledged_at"])
        serializer = SuspiciousActivityFlagSerializer(flag)
        return Response(serializer.data)


class AuditActionView(APIView):
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "audit.log"
    required_app_label = "admin_control"

    def post(self, request):
        serializer = AuditActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = AuditLogger.log(
            actor=request.user if request.user and request.user.is_authenticated else None,
            action_type=serializer.validated_data["action_type"],
            target_app=serializer.validated_data.get("target_app"),
            target_model=serializer.validated_data.get("target_model"),
            target_pk=serializer.validated_data.get("target_pk"),
            severity=serializer.validated_data.get("severity"),
            metadata=serializer.validated_data.get("metadata"),
        )
        response_serialized = AuditEntrySerializer(entry)
        return Response(response_serialized.data, status=201)
