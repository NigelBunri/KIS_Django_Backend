# commerce/views.py
import uuid
from decimal import Decimal
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from rest_framework import mixins, viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

import logging

from apps.accounts.tiers import get_user_tier, get_feature_limit, normalize_limit_value
from apps.notifications import services as notification_services
from apps.billing.documents import build_booking_receipt_urls
from apps.billing.services import lock_wallet_funds_for_booking, release_locked_booking_funds, refund_locked_booking_funds

from .availability import format_date_key, get_day_key, normalize_availability_payload
from .models import (
    Shop,
    ShopVerificationRequest,
    Product,
    ProductAuthenticityCheck,
    Order,
    Payment,
    Promotion,
    Subscription,
    LoyaltyPoint,
    ShopFollow,
    ProductShare,
    ProductSubscription,
    AIRecommendation,
    AuditLog,
    FraudSignal,
    ShopCategory,
    ProductRating,
    ShopService,
    ShopRole,
    ShopTeamMember,
    ServiceBooking,
    ServiceBookingPayment,
    ServiceBookingEscrow,
    ServiceBookingComplaint,
)
from .serializers import (
    ShopSerializer,
    ShopVerificationRequestSerializer,
    ProductSerializer,
    ProductAuthenticityCheckSerializer,
    OrderSerializer,
    PaymentSerializer,
    PromotionSerializer,
    SubscriptionSerializer,
    LoyaltyPointSerializer,
    ShopFollowSerializer,
    ProductShareSerializer,
    AIRecommendationSerializer,
    AuditLogSerializer,
    FraudSignalSerializer,
    ProductRatingSerializer,
    ProductCategorySerializer,
    ShopServiceSerializer,
    ShopTeamMemberSerializer,
    ServiceBookingSerializer,
    ServiceBookingCreateSerializer,
    ServiceBookingPaymentSerializer,
    ServiceBookingComplaintSerializer,
)

logger = logging.getLogger(__name__)

WALLET_CENT_SCALE = 100


def _wallet_amount(value: int | None) -> int:
    return max(0, int(value or 0) * WALLET_CENT_SCALE)

from .tasks import (enqueue_shop_verification, enqueue_product_auth_check, evaluate_fraud_score, compute_recommendations)

# --- OpenAPI / Swagger compatibility layer (supports drf-spectacular and drf-yasg) ---
try:
    # drf-spectacular
    from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, OpenApiParameter
    SPECTACULAR = True
except Exception:
    SPECTACULAR = False

try:
    # drf-yasg
    from drf_yasg.utils import swagger_auto_schema
    from drf_yasg import openapi
    YASG = True
except Exception:
    YASG = False


def doc_decorator(summary=None, description=None, request=None, responses=None, parameters=None):
    """
    Return a decorator that wraps either drf-spectacular or drf-yasg schema decorators,
    or a no-op decorator if neither is installed.
    """
    if SPECTACULAR:
        return extend_schema(summary=summary, description=description, request=request, responses=responses, parameters=parameters)
    if YASG:
        def _map_params(params):
            if not params:
                return None
            return params
        return swagger_auto_schema(operation_summary=summary, operation_description=description,
                                  request_body=request, responses=responses, manual_parameters=_map_params(parameters))
    def _noop(func):
        return func
    return _noop


def class_doc_decorator(tag_name: str):
    """
    Class-level decorator for tagging viewsets (drf-spectacular). No-op for drf-yasg.
    """
    if SPECTACULAR:
        return extend_schema_view(
            list=extend_schema(tags=[tag_name]),
            retrieve=extend_schema(tags=[tag_name]),
            create=extend_schema(tags=[tag_name]),
            update=extend_schema(tags=[tag_name]),
            partial_update=extend_schema(tags=[tag_name]),
            destroy=extend_schema(tags=[tag_name])
        )
    def _noop(cls):
        return cls
    return _noop


def _normalize_limit(value):
    return normalize_limit_value(value, default=None)


def _enforce_media_size_limit(user, file_obj, field_name="image_file"):
    if not file_obj:
        return
    limit_mb = normalize_limit_value(get_feature_limit(user, "media_storage_mb", None), default=None)
    if limit_mb is None:
        return
    limit_bytes = int(limit_mb) * 1024 * 1024
    if file_obj.size > limit_bytes:
        raise ValidationError({field_name: "File exceeds your tier media limit."})


CANCELLATION_WINDOW_HOURS = 2
ACTIVE_BOOKING_STATUSES = {
    ServiceBooking.STATUS_PENDING,
    ServiceBooking.STATUS_CONFIRMED,
    ServiceBooking.STATUS_AWAITING_SATISFACTION,
    ServiceBooking.STATUS_COMPLETED,
    ServiceBooking.STATUS_DISPUTE,
}


def _get_service_timezone(service):
    tz_name = getattr(service, "timezone", None) or settings.TIME_ZONE or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _make_aware(dt):
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone=timezone.get_default_timezone())
    return dt


def _localize_to_service(service, scheduled_at):
    aware = _make_aware(scheduled_at)
    tz = _get_service_timezone(service)
    return aware.astimezone(tz)


def _get_blackout_dates(service):
    raw = getattr(service, "blackout_dates", []) or []
    normalized = set()
    for entry in raw:
        if isinstance(entry, date):
            normalized.add(entry)
            continue
        try:
            normalized.add(date.fromisoformat(str(entry)))
        except Exception:
            continue
    return normalized


def _validate_service_schedule(service, scheduled_at):
    now = timezone.now()
    scheduled = _make_aware(scheduled_at)
    if scheduled <= now:
        raise ValidationError({"scheduled_at": "Scheduled time must be in the future."})

    min_hours = min(getattr(service, "min_notice_hours", 0) or 0, CANCELLATION_WINDOW_HOURS)
    min_hours = max(min_hours, CANCELLATION_WINDOW_HOURS)
    if min_hours:
        cutoff = now + timedelta(hours=min_hours)
        if scheduled < cutoff:
            raise ValidationError({"scheduled_at": f"Bookings must be made at least {min_hours} hours ahead of time."})

    max_days = getattr(service, "max_advance_booking_days", 0) or 0
    if max_days:
        limit = now + timedelta(days=max_days)
        if scheduled > limit:
            raise ValidationError({"scheduled_at": f"Bookings can only be made up to {max_days} days ahead."})

    localized = _localize_to_service(service, scheduled)
    local_date = localized.date()
    blackout_dates = _get_blackout_dates(service)
    if local_date in blackout_dates:
        raise ValidationError({"scheduled_at": "Selected date is unavailable."})

    availability = normalize_availability_payload(service.availability or {})
    date_range = availability.get("date_range") or {}
    if date_range:
        try:
            start = date.fromisoformat(date_range.get("start_date") or "")
            end = date.fromisoformat(date_range.get("end_date") or "")
        except Exception:
            start, end = None, None
        if start and end and (local_date < start or local_date > end):
            raise ValidationError({"scheduled_at": "Selected date is unavailable."})

    date_key = format_date_key(localized)
    entry = availability.get("specific_dates", {}).get(date_key)
    if entry is None:
        day_key = get_day_key(localized)
        entry = availability.get("days", {}).get(day_key, {})
    if not entry.get("enabled", True):
        raise ValidationError({"scheduled_at": "Selected date is unavailable."})

    if not entry.get("all_day"):
        allowed_times = set(entry.get("times") or [])
        if allowed_times:
            scheduled_time = localized.strftime("%H:%M")
            if scheduled_time not in allowed_times:
                raise ValidationError({"scheduled_at": "The requested time is unavailable for this slot."})


# --- ViewSets with OpenAPI annotations ---
@class_doc_decorator('Shops')
class ShopViewSet(viewsets.ModelViewSet):
    queryset = Shop.objects.all().order_by('-created_at')
    serializer_class = ShopSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        owner_id = self.request.query_params.get("owner")
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        return qs

    def get_object(self):
        obj = super().get_object()
        if obj.owner_id != self.request.user.id and not self.request.user.is_staff:
            raise PermissionDenied("Only shop owners or staff can modify shops.")
        return obj

    def perform_create(self, serializer):
        tier = get_user_tier(self.request.user)
        tier_name = (tier.name if tier else getattr(self.request.user, "tier", "") or "").lower()
        is_business = "business" in tier_name or "partner" in tier_name
        if not is_business:
            raise PermissionDenied("Marketplace stores are available to Business+ tiers only.")
        shop_limit = _normalize_limit(get_feature_limit(self.request.user, "shops_limit", 0))
        existing = Shop.objects.filter(owner=self.request.user, is_deleted=False).count()
        if shop_limit is not None and existing >= shop_limit:
            raise ValidationError({"detail": f"Your tier allows up to {shop_limit} shops."})
        image_file = serializer.validated_data.get("image_file")
        if not image_file:
            raise ValidationError({"image_file": "Shop image is required."})
        _enforce_media_size_limit(self.request.user, image_file, field_name="image_file")
        serializer.save(owner=self.request.user)

    def perform_update(self, serializer):
        image_file = serializer.validated_data.get("image_file")
        if not image_file and not serializer.instance.image_file:
            raise ValidationError({"image_file": "Shop image is required."})
        _enforce_media_size_limit(self.request.user, image_file, field_name="image_file")
        super().perform_update(serializer)

    @doc_decorator(
        summary="Request shop verification",
        description="Submit a verification request (KYC/business documents) for a shop. Enqueues an async verification job.",
        request=ShopVerificationRequestSerializer,
        responses={200: OpenApiResponse(description="Verification requested") if SPECTACULAR else "Verification requested"}
    )
    @action(detail=True, methods=['post'])
    def request_verification(self, request, pk=None):
        shop = self.get_object()
        data = request.data
        svr = ShopVerificationRequest.objects.create(shop=shop, requested_by=request.user, documents=data.get('documents', []))
        enqueue_shop_verification.delay(str(svr.id))
        return Response({'status': 'verification_requested', 'id': svr.id})

    @doc_decorator(
        summary="Join a shop",
        description="Become a member of a shop to unlock notifications and special drops.",
        responses={200: "Joined"}
    )
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def join(self, request, pk=None):
        shop = self.get_object()
        ShopFollow.objects.get_or_create(user=request.user, shop=shop)
        return Response({'joined': True}, status=status.HTTP_200_OK)


@class_doc_decorator('Shop Verification Requests')
class ShopVerificationRequestViewSet(viewsets.ModelViewSet):
    queryset = ShopVerificationRequest.objects.all().order_by('-created_at')
    serializer_class = ShopVerificationRequestSerializer

    @doc_decorator(
        summary="Review shop verification request",
        description="Approve or reject a shop verification request. This endpoint is intended for manual reviewers.",
        request=None,
        responses={200: OpenApiResponse(description="Reviewed") if SPECTACULAR else "Reviewed"}
    )
    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        req = self.get_object()
        action_name = request.data.get('action')
        notes = request.data.get('notes', '')
        if action_name == 'approve':
            req.status = 'APPROVED'
            req.shop.is_verified = True
            req.shop.trust_badges = list(set(req.shop.trust_badges + ['kyc']))
            req.shop.save()
        elif action_name == 'reject':
            req.status = 'REJECTED'
        req.reviewer_notes = notes
        req.processed_at = timezone.now()
        req.save()
        return Response({'status': 'reviewed', 'new_status': req.status})


@class_doc_decorator('Products')
class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().order_by('-created_at')
    serializer_class = ProductSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        shop_id = self.request.query_params.get("shop")
        if shop_id:
            qs = qs.filter(shop_id=shop_id)
        owner_id = self.request.query_params.get("owner")
        if owner_id:
            qs = qs.filter(shop__owner_id=owner_id)
        return qs

    def get_object(self):
        obj = super().get_object()
        if obj.shop.owner_id != self.request.user.id and not self.request.user.is_staff:
            raise PermissionDenied("Only product owners or staff can modify listings.")
        return obj

    def perform_create(self, serializer):
        shop = serializer.validated_data.get("shop")
        if not shop or shop.owner_id != self.request.user.id:
            raise PermissionDenied("You can only add products to your own shop.")

        image_file = serializer.validated_data.get("image_file")
        if not image_file:
            raise ValidationError({"image_file": "Product image is required."})
        _enforce_media_size_limit(self.request.user, image_file, field_name="image_file")

        product_limit_raw = get_feature_limit(self.request.user, "products_per_shop_limit", 0)
        product_limit = _normalize_limit(product_limit_raw)
        if product_limit == 0:
            raise PermissionDenied("Marketplace listings require a Business+ tier.")

        existing = Product.objects.filter(shop=shop, is_deleted=False).count()
        if product_limit is not None and existing >= product_limit:
            raise ValidationError({"detail": f"Your tier allows up to {product_limit} products per shop."})

        serializer.save()

    def perform_update(self, serializer):
        image_file = serializer.validated_data.get("image_file")
        if not image_file and not serializer.instance.image_file:
            raise ValidationError({"image_file": "Product image is required."})
        _enforce_media_size_limit(self.request.user, image_file, field_name="image_file")
        super().perform_update(serializer)

    @doc_decorator(
        summary="Request product authenticity check",
        description="Submit a product for AI/heuristic authenticity checks. Enqueues an async job.",
        request=ProductAuthenticityCheckSerializer,
        responses={200: OpenApiResponse(description="Auth check requested") if SPECTACULAR else "Auth check requested"}
    )
    @action(detail=True, methods=['post'])
    def check_authenticity(self, request, pk=None):
        product = self.get_object()
        pac = ProductAuthenticityCheck.objects.create(product=product, requested_by=request.user, provider=request.data.get('provider', 'local_ai'))
        enqueue_product_auth_check.delay(str(pac.id))
        return Response({'status': 'auth_check_requested', 'id': pac.id})

    @action(detail=True, methods=['post'], url_path='broadcast')
    def broadcast(self, request, pk=None):
        product = self.get_object()
        if product.shop.owner_id != request.user.id:
            raise PermissionDenied("Only the shop owner can broadcast this product.")

        from apps.broadcasts.models import BroadcastItem, BroadcastSourceType
        from datetime import timedelta

        item, _ = BroadcastItem.objects.update_or_create(
            source_type=BroadcastSourceType.MARKET_PRODUCT,
            source_id=str(product.id),
            defaults={
                "broadcasted_by": request.user,
                "broadcasted_at": timezone.now(),
                "expires_at": timezone.now() + timedelta(days=10),
                "is_deleted": False,
            },
        )
        return Response({"detail": "Product broadcasted.", "broadcast_id": str(item.id)}, status=200)

    @doc_decorator(
        summary="Subscribe to a product",
        description="Register for product alerts and feeds.",
        responses={200: "Subscribed"}
    )
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def subscribe(self, request, pk=None):
        product = self.get_object()
        ProductSubscription.objects.get_or_create(user=request.user, product=product)
        return Response({'subscribed': True}, status=status.HTTP_200_OK)


@class_doc_decorator('Product Authenticity Checks')
class ProductAuthenticityCheckViewSet(viewsets.ModelViewSet):
    queryset = ProductAuthenticityCheck.objects.all().order_by('-created_at')
    serializer_class = ProductAuthenticityCheckSerializer


@class_doc_decorator('Orders')
class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all().order_by('-created_at')
    serializer_class = OrderSerializer

    @doc_decorator(
        summary="Pay for order",
        description="Create a payment for an order and mark it paid (free-tier stub). Triggers async fraud evaluation.",
        request=PaymentSerializer,
        responses={200: OpenApiResponse(description="Paid") if SPECTACULAR else "Paid"}
    )
    @action(detail=True, methods=['post'])
    def pay(self, request, pk=None):
        order = self.get_object()
        payment = Payment.objects.create(order=order, provider=request.data.get('provider', 'local'), method=request.data.get('method', 'card'), amount=order.total, currency=order.currency)
        # For free tier: mark success instantly (stub)
        payment.status = 'SUCCESS'
        payment.captured_at = timezone.now()
        payment.save()
        order.status = 'PAID'
        order.paid_at = timezone.now()
        order.save()
        # run fraud evaluation async
        evaluate_fraud_score.delay(str(order.id))
        return Response({'status': 'paid', 'payment_id': payment.id})


@class_doc_decorator('Payments')
class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.all().order_by('-created_at')
    serializer_class = PaymentSerializer


@class_doc_decorator('Promotions')
class PromotionViewSet(viewsets.ModelViewSet):
    queryset = Promotion.objects.all().order_by('-created_at')
    serializer_class = PromotionSerializer


@class_doc_decorator('Subscriptions')
class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.all().order_by('-created_at')
    serializer_class = SubscriptionSerializer


@class_doc_decorator('Loyalty')
class LoyaltyPointViewSet(viewsets.ModelViewSet):
    queryset = LoyaltyPoint.objects.all().order_by('-created_at')
    serializer_class = LoyaltyPointSerializer


@class_doc_decorator('Follows')
class ShopFollowViewSet(viewsets.ModelViewSet):
    queryset = ShopFollow.objects.all()
    serializer_class = ShopFollowSerializer


@class_doc_decorator('Shares')
class ProductShareViewSet(viewsets.ModelViewSet):
    queryset = ProductShare.objects.all()
    serializer_class = ProductShareSerializer


@class_doc_decorator('Recommendations')
class AIRecommendationViewSet(viewsets.ModelViewSet):
    queryset = AIRecommendation.objects.all()
    serializer_class = AIRecommendationSerializer

    @doc_decorator(
        summary="Compute recommendations",
        description="Enqueue recommendation computation for a user (async).",
        request=AIRecommendationSerializer,
        responses={200: OpenApiResponse(description="Enqueued") if SPECTACULAR else "Enqueued"}
    )
    @action(detail=False, methods=['post'])
    def compute(self, request):
        user_id = request.data.get('user_id')
        compute_recommendations.delay(user_id)
        return Response({'status': 'enqueued'})


@class_doc_decorator('Audit Logs')
class AuditLogViewSet(viewsets.ModelViewSet):
    queryset = AuditLog.objects.all().order_by('-created_at')
    serializer_class = AuditLogSerializer


@class_doc_decorator('Fraud Signals')
class FraudSignalViewSet(viewsets.ModelViewSet):
    queryset = FraudSignal.objects.all().order_by('-created_at')
    serializer_class = FraudSignalSerializer


@class_doc_decorator('Product Ratings')
class ProductRatingViewSet(viewsets.ModelViewSet):
    queryset = ProductRating.objects.all().order_by('-created_at')
    serializer_class = ProductRatingSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@class_doc_decorator('Product Categories')
class ProductCategoryViewSet(viewsets.ModelViewSet):
    queryset = ShopCategory.objects.all().order_by('-created_at')
    serializer_class = ProductCategorySerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        qs = qs.filter(category_type__in=['product', 'both'])
        shop_id = self.request.query_params.get('shop')
        if shop_id:
            qs = qs.filter(shop_id=shop_id)
        return qs


def _build_owner_member_entry(shop, request):
    owner = getattr(shop, "owner", None)
    if not owner:
        return None
    owner_id = str(owner.id)
    display_name = (
        getattr(owner, "display_name", "") or getattr(owner, "username", "") or getattr(owner, "phone", "") or ""
    )
    user_details = {
        "id": owner_id,
        "display_name": display_name,
        "phone": getattr(owner, "phone", None),
        "email": getattr(owner, "email", None),
    }
    is_current_user = False
    request_user = getattr(request, "user", None)
    if request_user and getattr(request_user, "is_authenticated", False):
        is_current_user = request_user.id == owner.id
    created_at = getattr(shop, "created_at", None)
    updated_at = getattr(shop, "updated_at", None)
    return {
        "id": f"owner-{shop.id}",
        "shop": str(shop.id),
        "user": owner_id,
        "user_details": user_details,
        "role": "owner",
        "role_display": "Owner",
        "is_active": True,
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "is_current_user": is_current_user,
    }


def _contains_owner_entry(records, owner_id):
    for entry in records:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("user")) == owner_id:
            return True
    return False


def _try_insert_owner_entry(records, owner_entry):
    owner_id = owner_entry.get("user")
    if not owner_id or _contains_owner_entry(records, owner_id):
        return False
    records.insert(0, owner_entry)
    return True


@class_doc_decorator('Shop Members')
class ShopTeamMemberViewSet(viewsets.ModelViewSet):
    queryset = ShopTeamMember.objects.all()
    serializer_class = ShopTeamMemberSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        shop_id = self.request.query_params.get('shop')
        if shop_id:
            qs = qs.filter(shop_id=shop_id)
        return qs

    def perform_create(self, serializer):
        shop = serializer.validated_data.get("shop")
        if not shop or (shop.owner_id != self.request.user.id and not self.request.user.is_staff):
            raise PermissionDenied("Only shop owners or staff can add members.")
        serializer.save()

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        self._attach_owner_entry(response, request)
        return response

    def _attach_owner_entry(self, response, request):
        owner_entry = self._resolve_owner_entry(request)
        if not owner_entry or not response.data:
            return
        data = response.data
        inserted = False
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, list):
                inserted = _try_insert_owner_entry(results, owner_entry)
                if inserted and isinstance(data.get("count"), int):
                    data["count"] = data.get("count", 0) + 1
            elif isinstance(data.get("data"), list):
                inserted = _try_insert_owner_entry(data["data"], owner_entry)
        elif isinstance(data, list):
            _try_insert_owner_entry(data, owner_entry)

    def _resolve_owner_entry(self, request):
        shop_id = request.query_params.get("shop")
        if not shop_id:
            return None
        shop = Shop.objects.filter(pk=shop_id).first()
        return _build_owner_member_entry(shop, request)


class ShopMembersByShopView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, shop_id):
        shop = get_object_or_404(Shop, pk=shop_id)
        members_qs = ShopTeamMember.objects.filter(shop=shop, is_active=True)
        serializer = ShopTeamMemberSerializer(members_qs, many=True, context={"request": request})
        data = list(serializer.data or [])
        owner_entry = _build_owner_member_entry(shop, request)
        if owner_entry:
            _try_insert_owner_entry(data, owner_entry)
        return Response(data)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        data = list(serializer.data or [])
        shop_id = request.query_params.get("shop")
        if shop_id:
            try:
                shop = Shop.objects.get(pk=shop_id)
            except Shop.DoesNotExist:
                return Response(data)
            owner_entry = _build_owner_member_entry(shop, request)
            if owner_entry:
                _try_insert_owner_entry(data, owner_entry)
        return Response(data)


@class_doc_decorator('Shop Services')
class ShopServiceViewSet(viewsets.ModelViewSet):
    queryset = ShopService.objects.all().order_by('-created_at')
    serializer_class = ShopServiceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        shop_id = self.request.query_params.get("shop")
        if shop_id:
            qs = qs.filter(shop_id=shop_id)
        return qs

    def perform_create(self, serializer):
        shop = serializer.validated_data.get("shop")
        if not shop or (shop.owner_id != self.request.user.id and not self.request.user.is_staff):
            raise PermissionDenied("Only shop owners or staff can create services.")
        serializer.save()

    def get_object(self):
        obj = super().get_object()
        if self.request.method not in permissions.SAFE_METHODS:
            if obj.shop.owner_id != self.request.user.id and not self.request.user.is_staff:
                raise PermissionDenied("Only shop owners or staff can modify services.")
        return obj


@class_doc_decorator('Service Bookings')
class ServiceBookingViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    queryset = ServiceBooking.objects.all().select_related('service', 'shop', 'payment', 'escrow')
    serializer_class = ServiceBookingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "create":
            return ServiceBookingCreateSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_staff:
            return qs
        return qs.filter(Q(user=self.request.user) | Q(shop__owner=self.request.user))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        user_id = getattr(request.user, "id", None)
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.warning(
                "ServiceBook/create invalid payload user=%s service=%s errors=%s",
                user_id,
                request.data.get("service_id"),
                exc.detail,
                exc_info=False,
            )
            raise
        service_id = serializer.validated_data["service_id"]
        scheduled_at = serializer.validated_data["scheduled_at"]
        instructions = serializer.validated_data.get("instructions", "")

        service = get_object_or_404(
            ShopService,
            pk=service_id,
            is_active=True,
            status="published",
        )
        if not service.shop or not service.shop.owner:
            raise ValidationError({"service_id": "Selected service is unavailable."})
        try:
            _validate_service_schedule(service, scheduled_at)
        except ValidationError as exc:
            logger.warning(
                "ServiceBook/create rejected schedule user=%s service=%s slot=%s errors=%s",
                user_id,
                service_id,
                scheduled_at,
                exc.detail,
            )
            raise

        limit = service.max_bookings_per_slot if service.group_booking_allowed else 1
        limit = max(1, limit or 1)
        slot_qs = ServiceBooking.objects.filter(
            service=service,
            scheduled_at=scheduled_at,
            status__in=ACTIVE_BOOKING_STATUSES,
        )
        if slot_qs.count() >= limit:
            logger.warning(
                "ServiceBook/create conflict user=%s service=%s schedule=%s limit=%s",
                user_id,
                service_id,
                scheduled_at,
                limit,
            )
            return Response(
                {"detail": "The requested slot is already booked."},
                status=status.HTTP_409_CONFLICT,
            )

        price_cents = int((service.price or Decimal("0")) * Decimal("100"))
        deposit_cents = 0
        if service.deposit_amount is not None:
            deposit_cents = int((service.deposit_amount or Decimal("0")) * Decimal("100"))
        elif service.deposit_percent is not None:
            percent = Decimal(service.deposit_percent or 0)
            deposit_cents = int(price_cents * percent / Decimal("100"))
        else:
            deposit_cents = price_cents
        deposit_cents = min(max(deposit_cents, 0), price_cents)
        if deposit_cents <= 0:
            deposit_cents = max(price_cents, 0)
        balance_cents = max(price_cents - deposit_cents, 0)

        tx_ref = str(uuid.uuid4())
        try:
            lock_wallet_funds_for_booking(
                user=request.user,
                amount_cents=_wallet_amount(deposit_cents),
                reference=tx_ref,
                meta={"service_id": str(service.id)},
            )
        except ValueError as exc:
            logger.warning(
                "ServiceBook/create insufficient funds user=%s service=%s amount=%s error=%s",
                user_id,
                service_id,
                deposit_cents,
                str(exc),
            )
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        booking = None
        try:
            with transaction.atomic():
                booking = ServiceBooking.objects.create(
                    service=service,
                    shop=service.shop,
                    user=request.user,
                    scheduled_at=scheduled_at,
                    price_cents=price_cents,
                    deposit_cents=deposit_cents,
                    balance_cents=balance_cents,
                    instructions=instructions or "",
                    payment_tx_ref=tx_ref,
                    remote_meeting_link=service.remote_meeting_link or "",
                )
                ServiceBookingEscrow.objects.create(
                    booking=booking,
                    payer=request.user,
                    provider=service.shop.owner,
                    amount_cents=deposit_cents,
                    status=ServiceBookingEscrow.STATUS_PENDING,
                    payment_reference=tx_ref,
                )
                ServiceBookingPayment.objects.create(
                    booking=booking,
                    amount_cents=deposit_cents,
                    payment_method="wallet",
                    payment_status=ServiceBookingPayment.STATUS_PAID,
                    paid_at=timezone.now(),
                    transaction_reference=tx_ref,
                )
        except Exception as exc:
            logger.error(
                "ServiceBook/create failed user=%s service=%s error=%s",
                user_id,
                service_id,
                str(exc),
                exc_info=True,
            )
            if deposit_cents > 0:
                refund_locked_booking_funds(
                    payer=request.user,
                    amount_cents=_wallet_amount(deposit_cents),
                    reference=tx_ref,
                )
            raise

        output = ServiceBookingSerializer(booking, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="mark-completed")
    def mark_completed(self, request, pk=None):
        booking = self.get_object()
        if booking.shop.owner_id != request.user.id and not request.user.is_staff:
            raise PermissionDenied("Only the provider can mark this booking as completed.")
        booking.status = ServiceBooking.STATUS_AWAITING_SATISFACTION
        booking.mark_provider_completed()
        booking.save(update_fields=["status", "provider_completed_at", "satisfaction_deadline"])
        escrow = getattr(booking, "escrow", None)
        if escrow:
            escrow.status = ServiceBookingEscrow.STATUS_AWAITING_SATISFACTION
            escrow.save(update_fields=["status"])
        notification_services.create_notification(
            user_id=str(booking.user_id),
            type="commerce.service_booking.completed",
            title="Service marked complete",
            body=f"{booking.service.name} was marked complete by the provider.",
            target_type="service_booking",
            target_id=str(booking.id),
            dedup_key=f"service_booking:{booking.id}:completed",
        )
        return Response({"status": "awaiting_satisfaction"}, status=status.HTTP_200_OK)

    def _can_user_cancel_booking(self, user, booking):
        if not user or not user.is_authenticated:
            return False
        if booking.user_id == user.id:
            return True
        if user.is_staff:
            return True
        shop = getattr(booking, "shop", None)
        if shop and shop.owner_id == user.id:
            return True
        return ShopTeamMember.objects.filter(
            shop=shop,
            user=user,
            role__in={ShopRole.MANAGER, ShopRole.ADMIN},
            is_active=True,
        ).exists()

    def _can_user_view_booking(self, user, booking):
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        if booking.user_id == user.id:
            return True
        provider = getattr(booking, "provider_user", None)
        if provider and provider.id == user.id:
            return True
        shop = getattr(booking, "shop", None)
        if shop and shop.owner_id == user.id:
            return True
        return ShopTeamMember.objects.filter(
            shop=shop,
            user=user,
            role__in={ShopRole.MANAGER, ShopRole.ADMIN},
            is_active=True,
        ).exists()

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        booking = self.get_object()
        if not self._can_user_cancel_booking(request.user, booking):
            raise PermissionDenied("Only the payer, owner, manager, or staff can cancel this booking.")
        if booking.status not in {ServiceBooking.STATUS_PENDING, ServiceBooking.STATUS_CONFIRMED}:
            return Response({"message": "This booking cannot be canceled."}, status=status.HTTP_400_BAD_REQUEST)
        now = timezone.now()
        if booking.scheduled_at <= now:
            return Response({"message": "The service date/time has already passed."}, status=status.HTTP_400_BAD_REQUEST)
        cutoff = booking.scheduled_at - timedelta(hours=CANCELLATION_WINDOW_HOURS)
        if now > cutoff:
            return Response({"message": "Cancel at least two hours before the scheduled time for a refund."}, status=status.HTTP_400_BAD_REQUEST)

        escrow = getattr(booking, "escrow", None)
        amount_to_refund = escrow.amount_cents if escrow else booking.deposit_cents or 0
        if amount_to_refund > 0:
            try:
                refund_locked_booking_funds(
                    payer=booking.user,
                    amount_cents=_wallet_amount(amount_to_refund),
                    reference=booking.payment_tx_ref or str(uuid.uuid4()),
                )
            except ValueError:
                pass
        booking.status = ServiceBooking.STATUS_CANCELLED
        booking.save(update_fields=["status"])
        payment = getattr(booking, "payment", None)
        if payment:
            payment.payment_status = ServiceBookingPayment.STATUS_REFUNDED
            payment.save(update_fields=["payment_status"])
        if escrow:
            escrow.status = ServiceBookingEscrow.STATUS_REFUNDED
            escrow.refunded_at = now
            escrow.refunded_by = request.user
            escrow.save(update_fields=["status", "refunded_at", "refunded_by"])
        return Response({"status": "cancelled"})

    @action(detail=True, methods=["get"], url_path="receipt")
    def receipt(self, request, pk=None):
        booking = self.get_object()
        if not self._can_user_view_booking(request.user, booking):
            raise PermissionDenied("You are not allowed to view this booking receipt.")
        html_url, pdf_url = build_booking_receipt_urls(request, booking)
        return Response({"receipt_url": html_url, "receipt_pdf_url": pdf_url})

    @action(detail=True, methods=["post"], url_path="pay-remaining")
    def pay_remaining(self, request, pk=None):
        booking = self.get_object()
        user_id = getattr(request.user, "id", None)
        if booking.user_id != user_id and not request.user.is_staff:
            logger.warning(
                "ServiceBook/pay_remaining denied user=%s booking_user=%s booking=%s",
                user_id,
                booking.user_id,
                booking.id,
            )
            raise PermissionDenied("Only the payer can complete the remaining payment.")
        price_cents = booking.price_cents or 0
        deposit_paid = booking.deposit_cents or 0
        remaining = max(price_cents - deposit_paid, 0)
        if remaining <= 0:
            return Response({"detail": "There is no remaining amount to pay."}, status=status.HTTP_400_BAD_REQUEST)

        tx_ref = str(uuid.uuid4())
        try:
            lock_wallet_funds_for_booking(
                user=request.user,
                amount_cents=_wallet_amount(remaining),
                reference=tx_ref,
                meta={"service_id": str(booking.service_id), "phase": "remaining"},
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                escrow = getattr(booking, "escrow", None)
                if escrow:
                    escrow.amount_cents += remaining
                    escrow.save(update_fields=["amount_cents"])
                else:
                    ServiceBookingEscrow.objects.create(
                        booking=booking,
                        payer=booking.user,
                        provider=booking.shop.owner,
                        amount_cents=(booking.deposit_cents or 0) + remaining,
                        status=ServiceBookingEscrow.STATUS_PENDING,
                        payment_reference=tx_ref,
                    )
                payment = getattr(booking, "payment", None)
                if payment:
                    payment.amount_cents += remaining
                    transaction_ref = payment.transaction_reference or ""
                    payment.transaction_reference = (
                        f"{transaction_ref},{tx_ref}" if transaction_ref else tx_ref
                    )
                    payment.save(update_fields=["amount_cents", "transaction_reference"])
                booking.deposit_cents = (booking.deposit_cents or 0) + remaining
                booking.balance_cents = 0
                booking.payment_tx_ref = tx_ref
                booking.save(update_fields=["deposit_cents", "balance_cents", "payment_tx_ref"])
        except Exception as exc:
            logger.error(
                "ServiceBook/pay_remaining failed user=%s booking=%s error=%s",
                getattr(request.user, "id", None),
                booking.id,
                str(exc),
                exc_info=True,
            )
            refund_locked_booking_funds(
                payer=request.user,
                amount_cents=_wallet_amount(remaining),
                reference=tx_ref,
            )
            raise ValidationError({"detail": "Unable to complete the remaining payment."})

        return Response({"status": "remaining_paid"}, status=status.HTTP_200_OK)


@class_doc_decorator('Service Booking Complaints')
class ServiceBookingComplaintViewSet(viewsets.ModelViewSet):
    queryset = ServiceBookingComplaint.objects.all().order_by('-created_at')
    serializer_class = ServiceBookingComplaintSerializer
    permission_classes = [permissions.IsAuthenticated]


class ServiceBookingPaymentSatisfyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, payment_id):
        payment = get_object_or_404(ServiceBookingPayment, pk=payment_id)
        booking = payment.booking
        if booking.user_id != request.user.id:
            raise PermissionDenied("Only the payer can mark a payment as satisfied.")
        escrow = getattr(booking, "escrow", None)
        amount = escrow.amount_cents if escrow else booking.deposit_cents or 0
        if escrow and escrow.status == ServiceBookingEscrow.STATUS_RELEASED:
            pass
        elif amount > 0:
            try:
                release_locked_booking_funds(
                    payer=booking.user,
                    provider=booking.shop.owner,
                    amount_cents=_wallet_amount(amount),
                    reference=payment.transaction_reference,
                    meta={"booking_id": str(booking.id)},
                )
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)})
        now = timezone.now()
        payment.payment_status = ServiceBookingPayment.STATUS_SATISFIED
        payment.satisfied_at = now
        payment.save(update_fields=["payment_status", "satisfied_at"])
        if escrow:
            escrow.status = ServiceBookingEscrow.STATUS_RELEASED
            escrow.released_at = now
            escrow.released_by = request.user
            escrow.save(update_fields=["status", "released_at", "released_by"])
        booking.status = ServiceBooking.STATUS_COMPLETED
        booking.payer_satisfied_at = now
        booking.save(update_fields=["status", "payer_satisfied_at"])
        return Response({"status": "satisfied"}, status=status.HTTP_200_OK)
