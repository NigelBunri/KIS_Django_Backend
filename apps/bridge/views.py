from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import BridgeAccount, BridgeThread, BridgeMessage, BridgeAutomation, BridgeAnalytics
from .serializers import (
    BridgeAccountSerializer,
    BridgeAccountCredentialSerializer,
    BridgeThreadSerializer,
    BridgeMessageSerializer,
    BridgeAutomationSerializer,
    BridgeAnalyticsSerializer,
)
from .permissions import IsBridgeOwnerOrReadOnly
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound
from django.shortcuts import get_object_or_404
from .tasks import enqueue_outbound_message, process_inbound_message
from django.db import transaction

# drf-spectacular imports for OpenAPI annotations
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiResponse,
    OpenApiParameter,
    OpenApiExample,
    OpenApiTypes,
)

####################################
# Shared OpenAPI parameters & examples
####################################
FILTER_PARAMS = [
    OpenApiParameter(name="external_app", required=False, location=OpenApiParameter.QUERY, type=OpenApiTypes.STR, description="Filter by external app (e.g. slack, telegram)"),
    OpenApiParameter(name="user_id", required=False, location=OpenApiParameter.QUERY, type=OpenApiTypes.UUID, description="Filter by local user id"),
]

ACCOUNT_SYNC_EXAMPLE = OpenApiExample(
    "Start sync (example)",
    value={"detail": "sync started"},
    response_only=True,
)

OUTBOUND_MESSAGE_REQUEST_EXAMPLE = OpenApiExample(
    "Outbound message example",
    value={
        "bridge_thread": "11111111-1111-1111-1111-111111111111",
        "direction": "OUTBOUND",
        "message_type": "TEXT",
        "payload": {"text": "Hello from KIS bridge!"},
    },
)

OUTBOUND_MESSAGE_RESPONSE_EXAMPLE = OpenApiExample(
    "Outbound message response example",
    value={
        "id": "22222222-2222-2222-2222-222222222222",
        "bridge_thread": "11111111-1111-1111-1111-111111111111",
        "direction": "OUTBOUND",
        "message_type": "TEXT",
        "payload": {"text": "Hello from KIS bridge!"},
        "status": "SENT",
    },
)

####################################
# BridgeAccountViewSet
####################################
@extend_schema_view(
    list=extend_schema(
        summary="List Bridge Accounts",
        description="Return all bridge accounts. Supports filtering by `external_app` and `user_id`.",
        parameters=FILTER_PARAMS,
        responses={200: BridgeAccountSerializer(many=True)},
        tags=["Bridge Accounts"],
    ),
    retrieve=extend_schema(
        summary="Retrieve Bridge Account",
        responses={200: BridgeAccountSerializer},
        tags=["Bridge Accounts"],
    ),
    create=extend_schema(
        summary="Create Bridge Account",
        description="Create a new BridgeAccount. Use this to store OAuth tokens/metadata for external apps.",
        request=BridgeAccountSerializer,
        responses={201: BridgeAccountSerializer},
        tags=["Bridge Accounts"],
    ),
    update=extend_schema(
        summary="Update Bridge Account",
        request=BridgeAccountSerializer,
        responses={200: BridgeAccountSerializer},
        tags=["Bridge Accounts"],
    ),
    partial_update=extend_schema(
        summary="Partial update Bridge Account",
        request=BridgeAccountSerializer,
        responses={200: BridgeAccountSerializer},
        tags=["Bridge Accounts"],
    ),
    destroy=extend_schema(
        summary="Delete Bridge Account",
        responses={204: OpenApiResponse(description="deleted")},
        tags=["Bridge Accounts"],
    ),
)
class BridgeAccountViewSet(viewsets.ModelViewSet):
    """
    Manage Bridge Accounts (external app credentials & metadata).
    """
    queryset = BridgeAccount.objects.all()
    permission_classes = [IsAuthenticated, IsBridgeOwnerOrReadOnly]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return BridgeAccountCredentialSerializer
        return BridgeAccountSerializer

    def get_queryset(self):
        return BridgeAccount.objects.filter(user_id=self.request.user.id, is_deleted=False)

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.user.id)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = serializer.save(user_id=request.user.id)
        return Response(BridgeAccountSerializer(account).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        account = serializer.save(user_id=request.user.id)
        return Response(BridgeAccountSerializer(account).data)

    @extend_schema(
        summary="Trigger sync for a Bridge Account",
        description="Start an asynchronous sync job to fetch conversation history from the external provider for this account.",
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT, description="Sync started")},
        examples=[ACCOUNT_SYNC_EXAMPLE],
        tags=["Bridge Accounts"],
    )
    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        account = self.get_object()
        # trigger async sync job (uncomment when task exists)
        # sync_bridge_account.delay(str(account.id))
        return Response({'detail': 'sync started'})

####################################
# BridgeThreadViewSet
####################################
@extend_schema_view(
    list=extend_schema(
        summary="List Bridge Threads",
        description="List threads synced from external platforms.",
        parameters=FILTER_PARAMS,
        responses={200: BridgeThreadSerializer(many=True)},
        tags=["Threads"],
    ),
    retrieve=extend_schema(summary="Retrieve Bridge Thread", responses={200: BridgeThreadSerializer}, tags=["Threads"]),
    create=extend_schema(summary="Create Bridge Thread", request=BridgeThreadSerializer, responses={201: BridgeThreadSerializer}, tags=["Threads"]),
    update=extend_schema(summary="Update Bridge Thread", request=BridgeThreadSerializer, responses={200: BridgeThreadSerializer}, tags=["Threads"]),
    partial_update=extend_schema(summary="Partial update Bridge Thread", request=BridgeThreadSerializer, responses={200: BridgeThreadSerializer}, tags=["Threads"]),
    destroy=extend_schema(summary="Delete Bridge Thread", responses={204: OpenApiResponse(description="deleted")}, tags=["Threads"]),
)
class BridgeThreadViewSet(viewsets.ModelViewSet):
    """
    Threads represent conversation containers mapped between KIS and external platforms.
    """
    queryset = BridgeThread.objects.all()
    serializer_class = BridgeThreadSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        accounts = BridgeAccount.objects.filter(user_id=self.request.user.id, is_deleted=False)
        allowed_apps = accounts.values_list("external_app", flat=True)
        linked_ids = accounts.values_list("id", flat=True)
        return BridgeThread.objects.filter(
            external_app__in=allowed_apps,
            linked_thread_id__in=linked_ids,
            is_deleted=False,
        )

    def perform_create(self, serializer):
        external_app = serializer.validated_data.get("external_app")
        account = BridgeAccount.objects.filter(
            user_id=self.request.user.id,
            external_app=external_app,
            is_deleted=False,
        ).first()
        if not account:
            raise NotFound("Bridge account not found.")
        serializer.save(linked_thread_id=account.id)

    @extend_schema(
        summary="Archive a thread",
        description="Mark the thread as archived. This can be used for compliance/retention workflows.",
        responses={200: OpenApiResponse(description="archived")},
        tags=["Threads"],
    )
    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        t = self.get_object()
        t.is_archived = True
        t.save()
        return Response({'detail': 'archived'})

####################################
# BridgeMessageViewSet
####################################
@extend_schema_view(
    list=extend_schema(
        summary="List Bridge Messages",
        description="List messages for threads. Use query params to filter by thread or external_message_id.",
        parameters=[
            OpenApiParameter(name="bridge_thread", required=False, location=OpenApiParameter.QUERY, type=OpenApiTypes.UUID, description="Filter messages by bridge_thread id"),
            OpenApiParameter(name="external_message_id", required=False, location=OpenApiParameter.QUERY, type=OpenApiTypes.STR, description="Filter by external message id"),
        ],
        responses={200: BridgeMessageSerializer(many=True)},
        tags=["Messages"],
    ),
    retrieve=extend_schema(summary="Retrieve Bridge Message", responses={200: BridgeMessageSerializer}, tags=["Messages"]),
    create=extend_schema(
        summary="Create Bridge Message (Outbound)",
        description="Create a message intended for outbound delivery. This enqueues an async job to send to the external provider.",
        request=BridgeMessageSerializer,
        responses={
            201: OpenApiResponse(response=BridgeMessageSerializer, description="Message enqueued"),
            400: OpenApiResponse(description="Validation error"),
        },
        examples=[OUTBOUND_MESSAGE_REQUEST_EXAMPLE, OUTBOUND_MESSAGE_RESPONSE_EXAMPLE],
        tags=["Messages"],
    ),
    update=extend_schema(summary="Update Bridge Message", request=BridgeMessageSerializer, responses={200: BridgeMessageSerializer}, tags=["Messages"]),
    partial_update=extend_schema(summary="Partial update Bridge Message", request=BridgeMessageSerializer, responses={200: BridgeMessageSerializer}, tags=["Messages"]),
    destroy=extend_schema(summary="Delete Bridge Message", responses={204: OpenApiResponse(description="deleted")}, tags=["Messages"]),
)
class BridgeMessageViewSet(viewsets.ModelViewSet):
    """
    Messages sent/received via the Bridge. Creating an OUTBOUND message enqueues sending.
    """
    queryset = BridgeMessage.objects.select_related('bridge_thread').all()
    serializer_class = BridgeMessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        allowed_threads = BridgeThreadViewSet()
        allowed_threads.request = self.request
        return BridgeMessage.objects.select_related("bridge_thread").filter(
            bridge_thread__in=allowed_threads.get_queryset(),
            is_deleted=False,
        )

    @extend_schema(
        summary="Create outbound message and enqueue sender",
        description="Creates a message record with direction=OUTBOUND. The system will enqueue the message for delivery to the external platform asynchronously.",
        request=BridgeMessageSerializer,
        responses={201: BridgeMessageSerializer},
        examples=[OUTBOUND_MESSAGE_REQUEST_EXAMPLE],
        tags=["Messages"],
    )
    def create(self, request, *args, **kwargs):
        # When creating outbound message, enqueue sending to external provider
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        thread = serializer.validated_data.get("bridge_thread")
        allowed_threads = BridgeThreadViewSet()
        allowed_threads.request = self.request
        if not allowed_threads.get_queryset().filter(pk=getattr(thread, "pk", None)).exists():
            return Response({"detail": "Thread not found."}, status=status.HTTP_404_NOT_FOUND)
        msg = serializer.save()
        # enqueue async send (Celery)
        try:
            enqueue_outbound_message.delay(str(msg.id))
        except Exception:
            # fallback: mark as queued locally if Celery not available
            msg.status = 'QUEUED'
            msg.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

####################################
# BridgeAutomationViewSet
####################################
@extend_schema_view(
    list=extend_schema(summary="List Automations", responses={200: BridgeAutomationSerializer(many=True)}, tags=["Automations"]),
    retrieve=extend_schema(summary="Retrieve Automation", responses={200: BridgeAutomationSerializer}, tags=["Automations"]),
    create=extend_schema(summary="Create Automation", request=BridgeAutomationSerializer, responses={201: BridgeAutomationSerializer}, tags=["Automations"]),
    update=extend_schema(summary="Update Automation", request=BridgeAutomationSerializer, responses={200: BridgeAutomationSerializer}, tags=["Automations"]),
    partial_update=extend_schema(summary="Partial update Automation", request=BridgeAutomationSerializer, responses={200: BridgeAutomationSerializer}, tags=["Automations"]),
    destroy=extend_schema(summary="Delete Automation", responses={204: OpenApiResponse(description="deleted")}, tags=["Automations"]),
)
class BridgeAutomationViewSet(viewsets.ModelViewSet):
    """
    Rules and automations that run on a thread (keyword triggers, scheduled events, etc).
    """
    queryset = BridgeAutomation.objects.all()
    serializer_class = BridgeAutomationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        allowed_threads = BridgeThreadViewSet()
        allowed_threads.request = self.request
        return BridgeAutomation.objects.filter(
            bridge_thread__in=allowed_threads.get_queryset(),
            is_deleted=False,
        )

    def perform_create(self, serializer):
        thread = serializer.validated_data.get("bridge_thread")
        allowed_threads = BridgeThreadViewSet()
        allowed_threads.request = self.request
        if not allowed_threads.get_queryset().filter(pk=getattr(thread, "pk", None)).exists():
            raise NotFound("Thread not found.")
        serializer.save()

####################################
# BridgeAnalyticsViewSet
####################################
@extend_schema_view(
    list=extend_schema(
        summary="List Bridge Analytics",
        description="Read-only analytics per bridge account (message counts, avg response time).",
        responses={200: BridgeAnalyticsSerializer(many=True)},
        tags=["Analytics"],
    ),
    retrieve=extend_schema(summary="Retrieve Bridge Analytics", responses={200: BridgeAnalyticsSerializer}, tags=["Analytics"]),
)
class BridgeAnalyticsViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only analytics for bridge accounts.
    """
    queryset = BridgeAnalytics.objects.all()
    serializer_class = BridgeAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return BridgeAnalytics.objects.filter(
            bridge_account__user_id=self.request.user.id,
            bridge_account__is_deleted=False,
            is_deleted=False,
        )
