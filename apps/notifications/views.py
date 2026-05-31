# notifications/views.py
import uuid

from django.utils import timezone
from django.db.models import Count, Q
from rest_framework import viewsets, status, serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.views import APIView

# Swagger / OpenAPI helpers (drf-yasg)
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from . import models, serializers as srl, services
from .badge_counts import get_main_tab_badge_counts
from .permissions import IsOwnerOrReadOnly
from .realtime import notify_main_tab_badges_updated

# -------------------------
# Small request/response serializers for docs
# -------------------------
class BulkMarkReadRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), help_text="List of notification UUIDs to mark read")


class CreateNotificationRequestSerializer(serializers.Serializer):
    user_id = serializers.UUIDField(required=False, help_text="Target user ID (if omitted, default to caller when allowed)")
    type = serializers.CharField(max_length=128)
    template_key = serializers.CharField(max_length=200, required=False, allow_blank=True)
    context = serializers.DictField(required=False)
    channel = serializers.CharField(max_length=32, required=False)
    channels = serializers.ListField(child=serializers.CharField(max_length=32), required=False)
    priority = serializers.CharField(max_length=16, required=False, default="MEDIUM")
    dedup_key = serializers.CharField(max_length=255, required=False, allow_null=True)
    title = serializers.CharField(required=False, allow_blank=True)
    body = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(required=False, allow_blank=True)
    target_type = serializers.CharField(max_length=64, required=False, allow_blank=True)
    target_id = serializers.UUIDField(required=False, allow_null=True)


class RegisterDeviceTokenRequestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=128)
    platform = serializers.CharField(max_length=40, required=False, allow_blank=True)
    push_token = serializers.CharField()
    token_type = serializers.CharField(max_length=20, required=False, default="fcm")
    apns_token = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.DictField(required=False)


class UnregisterDeviceTokenRequestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=128, required=False)
    push_token = serializers.CharField(required=False)


class MarkReadResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    is_read = serializers.BooleanField()
    read_at = serializers.DateTimeField(allow_null=True)


class UnreadCountResponseSerializer(serializers.Serializer):
    unread_count = serializers.IntegerField()


class MainTabBadgeCountsResponseSerializer(serializers.Serializer):
    counts = serializers.DictField(child=serializers.IntegerField())
    sources = serializers.DictField(child=serializers.CharField())
    total = serializers.IntegerField()


class MarkSourceReadRequestSerializer(serializers.Serializer):
    source = serializers.CharField(required=False, allow_blank=True)
    target_type = serializers.CharField(required=False, allow_blank=True)
    target_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    types = serializers.ListField(child=serializers.CharField(), required=False)


class NotificationPreferencesRequestSerializer(serializers.Serializer):
    quiet_hours = serializers.DictField(required=False)
    digest = serializers.DictField(required=False)
    source = serializers.CharField(required=False, allow_blank=True)
    source_enabled = serializers.BooleanField(required=False)
    source_priority = serializers.CharField(required=False, allow_blank=True)
    source_channels = serializers.ListField(child=serializers.CharField(), required=False)


# -------------------------
# Notification CRUD & Actions
# -------------------------
class NotificationViewSet(viewsets.ModelViewSet):
    """
    Notification endpoints.

    Features:
      - List & fetch notifications (users only see their own)
      - Mark single notification read
      - Bulk mark-as-read
      - Create notification (internal services may call this endpoint)
    """
    queryset = models.Notification.objects.all()
    serializer_class = srl.NotificationSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrReadOnly]
    lookup_field = "id"

    def get_queryset(self):
        """
        Limit notifications to the current user (exclude deleted).
        """
        user = self.request.user
        qs = models.Notification.objects.filter(user_id=user.id, is_deleted=False)
        q = str(self.request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q) | Q(type__icontains=q) | Q(target_type__icontains=q))
        priority = str(self.request.query_params.get("priority") or "").strip().upper()
        if priority:
            qs = qs.filter(priority=priority)
        source = str(self.request.query_params.get("source") or "").strip().lower()
        if source:
            qs = qs.filter(Q(context_data__source__iexact=source) | Q(context_data__badge_source__iexact=source))
        urgency = str(self.request.query_params.get("urgency") or "").strip().lower()
        if urgency:
            qs = qs.filter(context_data__urgency_label__iexact=urgency)
        unread = str(self.request.query_params.get("unread") or "").strip().lower()
        if unread in {"1", "true", "yes"}:
            qs = qs.filter(is_read=False)
        if unread in {"0", "false", "no"}:
            qs = qs.filter(is_read=True)
        return qs.order_by("-created_at")

    @swagger_auto_schema(
        operation_id="notifications_delete",
        operation_description="Soft delete a notification for the authenticated user.",
        responses={204: "Notification deleted"},
    )
    def destroy(self, request, *args, **kwargs):
        """
        Soft delete the specified notification.
        """
        notif = self.get_object()
        if not notif.is_deleted:
            notif.is_deleted = True
            notif.save(update_fields=["is_deleted", "updated_at"])
            notify_main_tab_badges_updated(
                [str(request.user.id)],
                source="notifications",
                reason="deleted",
                extra={"notification_id": str(notif.id)},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------
    # Single mark-read action
    # ------------------------------------------
    @swagger_auto_schema(
        operation_id="notifications_mark_read",
        operation_description="Mark a notification as read. Returns basic read-state fields.",
        responses={200: MarkReadResponseSerializer()}
    )
    @action(detail=True, methods=["post"])
    def mark_read(self, request, id=None):
        """
        Mark the specified notification as read.
        """
        notif = self.get_object()
        notif.mark_read()
        notify_main_tab_badges_updated(
            [str(request.user.id)],
            source="notifications",
            reason="mark_read",
            extra={"notification_id": str(notif.id)},
        )
        resp = {"id": notif.id, "is_read": notif.is_read, "read_at": notif.read_at}
        return Response(MarkReadResponseSerializer(resp).data)

    # ------------------------------------------
    # Bulk mark-read
    # ------------------------------------------
    @swagger_auto_schema(
        operation_id="notifications_bulk_mark_read",
        operation_description="Bulk mark notifications as read for the authenticated user.",
        request_body=BulkMarkReadRequestSerializer,
        responses={200: openapi.Response("Updated count", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={"updated": openapi.Schema(type=openapi.TYPE_INTEGER)}
        ))}
    )
    @action(detail=False, methods=["post"])  # bulk mark read
    def bulk_mark_read(self, request):
        """
        Bulk mark notifications as read. Accepts JSON: {\"ids\": ["uuid1","uuid2", ...]}
        """
        serializer = BulkMarkReadRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data.get("ids", [])
        updated = models.Notification.objects.filter(user_id=request.user.id, id__in=ids).update(is_read=True, read_at=timezone.now())
        if updated:
            notify_main_tab_badges_updated(
                [str(request.user.id)],
                source="notifications",
                reason="bulk_mark_read",
                extra={"updated": int(updated)},
            )
        return Response({"updated": updated})

    @swagger_auto_schema(
        operation_id="notifications_mark_all_read",
        operation_description="Mark all notifications as read for the authenticated user.",
        responses={200: openapi.Response("Updated count", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={"updated": openapi.Schema(type=openapi.TYPE_INTEGER)}
        ))}
    )
    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        updated = models.Notification.objects.filter(user_id=request.user.id, is_deleted=False, is_read=False).update(
            is_read=True,
            read_at=timezone.now(),
        )
        if updated:
            notify_main_tab_badges_updated(
                [str(request.user.id)],
                source="notifications",
                reason="mark_all_read",
                extra={"updated": int(updated)},
        )
        return Response({"updated": updated})

    @swagger_auto_schema(
        operation_id="notifications_mark_source_read",
        operation_description=(
            "Mark notifications read for a source/target group. Used by tab surfaces when content is opened "
            "so backend-backed badge counts decrement immediately."
        ),
        request_body=MarkSourceReadRequestSerializer,
        responses={200: openapi.Response("Updated count", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={"updated": openapi.Schema(type=openapi.TYPE_INTEGER)}
        ))},
    )
    @action(detail=False, methods=["post"], url_path="mark-source-read")
    def mark_source_read(self, request):
        serializer = MarkSourceReadRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        source = str(data.get("source") or "").strip().lower()
        target_type = str(data.get("target_type") or "").strip()
        target_id_raw = data.get("target_id")
        target_id = None
        if target_id_raw:
            try:
                target_id = uuid.UUID(str(target_id_raw))
            except (TypeError, ValueError):
                return Response({"updated": 0})
        types = [str(value).strip() for value in data.get("types") or [] if str(value).strip()]

        qs = models.Notification.objects.filter(user_id=request.user.id, is_deleted=False, is_read=False)
        filters = []
        if target_type:
            target_aliases = {
                "bible_daily_passage": ("bible_daily_passage", "bible", "daily", "reading"),
                "bible_meditation_post": ("bible_meditation_post", "bible", "meditation", "devotional"),
                "bible_reading_event": ("bible_reading_event", "reading", "schedule"),
                "channel_content": ("channel_content", "channel", "broadcast"),
                "education_content": ("education_content", "course", "lesson", "education"),
                "course": ("course", "education_content", "education"),
                "lesson": ("lesson", "education_content", "education"),
                "health_institution": ("health_institution", "health", "institution"),
                "health_appointment": ("health_appointment", "appointment", "health"),
                "health_session": ("health_session", "session", "health"),
                "product": ("product", "market_product", "shop_product"),
                "shop_service": ("shop_service", "service", "market_service"),
                "service_booking": ("service_booking", "booking", "shop_service"),
                "shop": ("shop", "market_shop"),
                "partner_community": ("partner_community", "community", "partner"),
                "partner_group": ("partner_group", "community_group", "group"),
                "conversation": ("conversation", "chat", "message"),
            }.get(target_type.lower(), (target_type,))
            target_query = Q()
            for alias in target_aliases:
                target_query |= Q(target_type__iexact=alias)
            filters.append(target_query)
        if target_id:
            filters.append(Q(target_id=target_id))
        if types:
            filters.append(Q(type__in=types))
        if source:
            source_tokens = {
                "bible": ("bible", "reading", "meditation", "devotional", "daily"),
                "broadcast": ("broadcast", "channel", "course", "lesson", "product", "market", "shop", "event", "education", "health", "institution"),
                "messages": ("message", "chat", "conversation"),
                "partners": ("partner", "community"),
                "profile": ("profile", "account", "verification", "general"),
                "education": ("education", "course", "lesson", "school", "institution"),
                "health": ("health", "medical", "appointment", "institution"),
                "market": ("market", "shop", "product", "service", "order"),
            }.get(source, (source,))
            source_query = Q()
            for token in source_tokens:
                source_query |= (
                    Q(type__icontains=token)
                    | Q(title__icontains=token)
                    | Q(body__icontains=token)
                    | Q(target_type__icontains=token)
                    | Q(context_data__source__iexact=token)
                    | Q(context_data__badge_source__iexact=token)
                )
            filters.append(source_query)

        if not filters:
            return Response({"updated": 0})

        combined = filters[0]
        for query in filters[1:]:
            combined &= query

        updated = qs.filter(combined).update(is_read=True, read_at=timezone.now())
        if updated:
            notify_main_tab_badges_updated(
                [str(request.user.id)],
                source=source or target_type or "notifications",
                reason="source_mark_read",
                extra={"updated": int(updated), "target_type": target_type, "target_id": str(target_id or "")},
            )
        return Response({"updated": updated})

    @swagger_auto_schema(
        operation_id="notifications_unread_count",
        operation_description="Return unread notification count for the authenticated user.",
        responses={200: UnreadCountResponseSerializer()},
    )
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = models.Notification.objects.filter(user_id=request.user.id, is_deleted=False, is_read=False).count()
        return Response({"unread_count": count})

    @swagger_auto_schema(
        operation_id="notifications_main_tab_badge_counts",
        operation_description="Return backend-backed unread badge counts for the main mobile bottom tabs.",
        responses={200: MainTabBadgeCountsResponseSerializer()},
    )
    @action(detail=False, methods=["get"], url_path="main-tab-badge-counts")
    def main_tab_badge_counts(self, request):
        payload = get_main_tab_badge_counts(request.user).as_dict()
        return Response(MainTabBadgeCountsResponseSerializer(payload).data)

    @action(detail=False, methods=["get"], url_path="attention-summary")
    def attention_summary(self, request):
        qs = self.get_queryset()
        unread = qs.filter(is_read=False)
        by_priority = list(unread.values("priority").annotate(count=Count("id")).order_by("priority"))
        by_source = list(unread.values("context_data__badge_source").annotate(count=Count("id")).order_by("context_data__badge_source"))
        by_urgency = list(unread.values("context_data__urgency_label").annotate(count=Count("id")).order_by("context_data__urgency_label"))
        urgent_now = unread.filter(priority__in=["URGENT", "HIGH"]).count()
        return Response(
            {
                "unread_count": unread.count(),
                "urgent_now": urgent_now,
                "by_priority": by_priority,
                "by_source": by_source,
                "by_urgency": by_urgency,
                "preferences": services.user_notification_preferences(request.user.id),
            }
        )

    @action(detail=False, methods=["get", "post"], url_path="attention-preferences")
    def attention_preferences(self, request):
        if request.method == "GET":
            return Response(services.user_notification_preferences(request.user.id))
        serializer = NotificationPreferencesRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "quiet_hours" in data or "digest" in data:
            rule, _ = models.NotificationRule.objects.get_or_create(
                user_id=request.user.id,
                type=None,
                target_type=None,
                target_id=None,
                defaults={"schedule_json": {}, "channels_json": ["IN_APP", "PUSH"]},
            )
            schedule = rule.schedule_json if isinstance(rule.schedule_json, dict) else {}
            if "quiet_hours" in data:
                schedule["quiet_hours"] = data.get("quiet_hours") or {}
            if "digest" in data:
                schedule["digest"] = data.get("digest") or {}
            rule.schedule_json = schedule
            rule.enabled = True
            rule.save(update_fields=["schedule_json", "enabled", "updated_at"])
        source = str(data.get("source") or "").strip().lower()
        if source:
            source_rule, _ = models.NotificationRule.objects.get_or_create(
                user_id=request.user.id,
                type=None,
                target_type=None,
                target_id=None,
                condition_json={"source": source},
                defaults={"schedule_json": {}, "channels_json": ["IN_APP", "PUSH"]},
            )
            if "source_enabled" in data:
                source_rule.enabled = bool(data["source_enabled"])
            if data.get("source_priority"):
                source_rule.priority = str(data["source_priority"]).strip().upper()
            if "source_channels" in data:
                source_rule.channels_json = [str(value).strip().upper() for value in data["source_channels"] if str(value).strip()]
            source_rule.save(update_fields=["enabled", "priority", "channels_json", "updated_at"])
        notify_main_tab_badges_updated(
            [str(request.user.id)],
            source="notifications",
            reason="attention_preferences_updated",
            extra={},
        )
        return Response(services.user_notification_preferences(request.user.id))

    # ------------------------------------------
    # Create notification (internal service entrypoint)
    # ------------------------------------------
    @swagger_auto_schema(
        operation_id="notifications_create",
        operation_description=(
            "Create a notification and schedule deliveries. This endpoint is intended for internal services. "
            "If `user_id` is omitted the implementation may default to request.user where appropriate."
        ),
        request_body=CreateNotificationRequestSerializer,
        responses={201: srl.NotificationSerializer()}
    )
    @action(detail=False, methods=["post"])  # create via API (internal services call this)
    def create_notification(self, request):
        """
        Create a notification record and enqueue delivery processing.
        """
        serializer = CreateNotificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        user_id = payload.get("user_id") or request.user.id
        if str(user_id) != str(request.user.id) and not request.user.is_staff:
            return Response({"detail": "You may only create notifications for yourself."}, status=status.HTTP_403_FORBIDDEN)

        notif = services.create_notification(
            user_id=user_id,
            type=payload.get("type"),
            template_key=payload.get("template_key"),
            context=payload.get("context"),
            channel=payload.get("channel"),
            channels=payload.get("channels"),
            priority=payload.get("priority", "MEDIUM"),
            dedup_key=payload.get("dedup_key"),
            title=payload.get("title"),
            body=payload.get("body"),
            source=payload.get("source"),
            target_type=payload.get("target_type"),
            target_id=payload.get("target_id"),
        )

        # Use the NotificationSerializer for full output
        return Response(srl.NotificationSerializer(notif).data, status=status.HTTP_201_CREATED)


# -------------------------
# Notification Template management
# -------------------------
class MentionNotificationView(APIView):
    from apps.accounts.jwt_auth import DeviceBoundJWTAuthentication
    authentication_classes = [DeviceBoundJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        mentioned_ids = request.data.get("mentioned_user_ids", [])
        context = request.data.get("context", {})
        if isinstance(context, str):
            context = {}
        sender_name = str(context.get("sender_name", "Someone"))
        preview = str(context.get("preview", ""))[:200]
        conversation_id = str(context.get("conversation_id", ""))
        message_id = str(context.get("message_id", ""))

        if not mentioned_ids or not isinstance(mentioned_ids, list):
            return Response({"detail": "mentioned_user_ids required."}, status=400)

        from django.contrib.auth import get_user_model
        User = get_user_model()
        created = 0
        for uid in mentioned_ids[:20]:
            try:
                user = User.objects.get(id=uid)
            except (User.DoesNotExist, Exception):
                continue
            if str(user.id) == str(request.user.id):
                continue  # don't notify yourself

            notif = models.Notification.objects.create(
                user_id=user.id,
                type="mention",
                title=f"{sender_name} mentioned you",
                body=preview or f"{sender_name} mentioned you in a conversation.",
                context_data={
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "sender_id": str(request.user.id),
                    "sender_name": sender_name,
                },
            )
            # trigger async delivery
            try:
                from .tasks import process_notification_delivery
                process_notification_delivery.delay(str(notif.id))
            except Exception:
                pass
            created += 1

        return Response({"created": created})


# -------------------------
# Notification Template management
# -------------------------
class NotificationTemplateViewSet(viewsets.ModelViewSet):
    """
    CRUD for notification templates used to render titles & bodies.
    """
    queryset = models.NotificationTemplate.objects.all()
    serializer_class = srl.NotificationTemplateSerializer
    permission_classes = [IsAdminUser]


# -------------------------
# Notification Rules (per-user preferences & suppression)
# -------------------------
class NotificationRuleViewSet(viewsets.ModelViewSet):
    """
    Manage notification rules (quiet hours, channel preferences, condition-driven rules).
    """
    queryset = models.NotificationRule.objects.all()
    serializer_class = srl.NotificationRuleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Users only see their own rules (admins could extend this).
        """
        user = self.request.user
        if user.is_staff:
            return models.NotificationRule.objects.filter(is_deleted=False)
        return models.NotificationRule.objects.filter(user_id=user.id)

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.user.id)


class NotificationDeviceTokenViewSet(viewsets.ModelViewSet):
    serializer_class = srl.NotificationDeviceTokenSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return models.NotificationDeviceToken.objects.filter(user_id=self.request.user.id, is_deleted=False)

    @swagger_auto_schema(
        operation_id="notifications_device_token_register",
        operation_description="Register or refresh the authenticated user's mobile push token.",
        request_body=RegisterDeviceTokenRequestSerializer,
        responses={200: srl.NotificationDeviceTokenSerializer()},
    )
    @action(detail=False, methods=["post"], url_path="register")
    def register(self, request):
        serializer = RegisterDeviceTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        token, _ = models.NotificationDeviceToken.objects.update_or_create(
            user_id=request.user.id,
            device_id=payload["device_id"],
            push_token=payload["push_token"],
            defaults={
                "platform": payload.get("platform", ""),
                "token_type": payload.get("token_type", "fcm"),
                "apns_token": payload.get("apns_token", ""),
                "enabled": True,
                "last_seen_at": timezone.now(),
                "metadata": payload.get("metadata", {}),
                "is_deleted": False,
            },
        )
        return Response(self.get_serializer(token).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_id="notifications_device_token_unregister",
        operation_description="Disable one push token or all tokens for a device owned by the authenticated user.",
        request_body=UnregisterDeviceTokenRequestSerializer,
        responses={200: openapi.Response("Disabled count", schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={"updated": openapi.Schema(type=openapi.TYPE_INTEGER)}
        ))},
    )
    @action(detail=False, methods=["post"], url_path="unregister")
    def unregister(self, request):
        serializer = UnregisterDeviceTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        qs = models.NotificationDeviceToken.objects.filter(user_id=request.user.id, is_deleted=False)
        if payload.get("device_id"):
            qs = qs.filter(device_id=payload["device_id"])
        if payload.get("push_token"):
            qs = qs.filter(push_token=payload["push_token"])
        updated = qs.update(enabled=False, is_deleted=True, updated_at=timezone.now())
        return Response({"updated": updated})
