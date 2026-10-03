from django.db.models import Q
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Shop
from .returns_models import Refund, ReturnRequest, ReturnStatus
from .returns_serializers import (
    CreateReturnRequestSerializer,
    RefundSerializer,
    ReturnRejectSerializer,
    ReturnRequestSerializer,
    ReturnReviewSerializer,
    ReturnShipSerializer,
)
from .returns_services import create_return_request, transition_return


def _managed_shop_ids(user):
    return [s.id for s in Shop.objects.filter(owner=user)] + [
        s.id for s in Shop.objects.filter(team_members__user=user, team_members__is_active=True)
    ]


class ReturnRequestViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin):
    """Buyers see their own return requests; sellers/staff see return
    requests against orders in shops they manage - scoped in get_queryset
    so an unrelated user's return-request id 404s via get_object() rather
    than leaking existence through a 403 from the service layer (same
    discoverability fix already applied to Fulfillment/Shipment - see
    shipping_views.py's _dispatch_transition comment)."""

    serializer_class = ReturnRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = ReturnRequest.objects.select_related("order", "order__shop", "buyer").prefetch_related(
            "items__order_item__product", "refunds"
        ).order_by("-created_at")
        user = self.request.user
        if getattr(user, "is_staff", False):
            return qs
        return qs.filter(Q(buyer=user) | Q(order__shop_id__in=_managed_shop_ids(user)))

    def create(self, request, *args, **kwargs):
        body = CreateReturnRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        return_request = create_return_request(
            order_id=data["order_id"], buyer=request.user, reason=data["reason"],
            explanation=data["explanation"], items=data["items"],
        )
        return Response(self.get_serializer(return_request).data, status=status.HTTP_201_CREATED)

    def _dispatch(self, request, target_status, **extra):
        # get_object() applies get_queryset()'s buyer/seller/staff scoping
        # first - see the class docstring for why this ordering matters.
        return_request = self.get_object()
        updated = transition_return(
            return_request_id=return_request.id, actor=request.user,
            target_status=target_status, **extra,
        )
        return Response(self.get_serializer(updated).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        body = ReturnReviewSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return self._dispatch(request, ReturnStatus.APPROVED, notes=body.validated_data["seller_notes"])

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return_request = self.get_object()
        body = ReturnRejectSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        updated = transition_return(
            return_request_id=return_request.id, actor=request.user,
            target_status=ReturnStatus.REJECTED, notes=body.validated_data["rejection_reason"],
        )
        return Response(self.get_serializer(updated).data)

    @action(detail=True, methods=["post"])
    def ship(self, request, pk=None):
        return_request = self.get_object()
        body = ReturnShipSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        updated = transition_return(
            return_request_id=return_request.id, actor=request.user,
            target_status=ReturnStatus.RETURN_SHIPPED,
            carrier_name=body.validated_data["carrier_name"],
            tracking_number=body.validated_data["tracking_number"],
        )
        return Response(self.get_serializer(updated).data)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        return self._dispatch(request, ReturnStatus.RECEIVED)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._dispatch(request, ReturnStatus.COMPLETED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._dispatch(request, ReturnStatus.CANCELLED)


class RefundViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    """Read-only - refunds are only ever created as a side effect of
    transition_return/cancel_marketplace_order, never directly via the API,
    so there is no create/update action to authorize here."""

    serializer_class = RefundSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Refund.objects.select_related("order", "order__shop").order_by("-created_at")
        user = self.request.user
        if getattr(user, "is_staff", False):
            return qs
        return qs.filter(Q(order__buyer=user) | Q(order__shop_id__in=_managed_shop_ids(user)))
