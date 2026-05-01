# notifications/views.py
from django.utils import timezone
from rest_framework import viewsets, status, serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser, IsAuthenticated

# Swagger / OpenAPI helpers (drf-yasg)
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from . import models, serializers as srl, services
from .permissions import IsOwnerOrReadOnly

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
        return models.Notification.objects.filter(user_id=user.id, is_deleted=False)

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
            target_type=payload.get("target_type"),
            target_id=payload.get("target_id"),
        )

        # Use the NotificationSerializer for full output
        return Response(srl.NotificationSerializer(notif).data, status=status.HTTP_201_CREATED)


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
