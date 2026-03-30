# commerce/views.py
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from rest_framework import mixins, viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied, ValidationError, NotFound
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
from .constants import KIS_COIN_CODE
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
    ServiceBookingReceipt,
    ServiceBookingPayment,
    ServiceBookingEscrow,
    ServiceBookingComplaint,
    Cart,
    CartItem,
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
    ServiceBookingRescheduleSerializer,
    ServiceBookingPaymentSerializer,
    ServiceBookingComplaintSerializer,
    CartSerializer,
    CartItemSerializer,
)

logger = logging.getLogger(__name__)

WALLET_CENT_SCALE = 100


def _wallet_amount(value: int | None) -> int:
    return max(0, int(value or 0) * WALLET_CENT_SCALE)


def _decimal_from_value(value):
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _to_cents(amount: Decimal) -> int:
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((quantized * Decimal("100")).to_integral_value())


def _resolve_service_package_option(service, package_name):
    candidate = str(package_name or "").strip()
    if not candidate:
        return None
    candidate_lower = candidate.lower()
    for entry in (getattr(service, 'packages', []) or []):
        name = str(entry.get('name') or '').strip()
        if not name or name.lower() != candidate_lower:
            continue
        price = _decimal_from_value(entry.get('price'))
        duration = int(entry.get('duration_minutes') or 0)
        return {
            'name': name,
            'price': price,
            'duration_minutes': duration,
        }
    return None


def _resolve_service_addon_options(service, addon_names):
    normalized = {str(item or "").strip().lower() for item in (addon_names or []) if str(item or "").strip()}
    if not normalized:
        return []
    selections = []
    for entry in (getattr(service, 'addons', []) or []):
        name = str(entry.get('name') or '').strip()
        if not name or name.lower() not in normalized:
            continue
        selections.append({
            'name': name,
            'price': _decimal_from_value(entry.get('price')),
            'duration_minutes': int(entry.get('duration_minutes') or 0),
        })
    return selections


def _apply_tax_if_needed(service, price):
    if not SERVICE_HANDLE_TAX_INCLUSIVE or getattr(service, 'tax_inclusive', True):
        return price
    if COMMERCE_DEFAULT_TAX_RATE_PCT <= 0:
        return price
    multiplier = Decimal('1') + (COMMERCE_DEFAULT_TAX_RATE_PCT / Decimal('100'))
    taxed = (price * multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    logger.info("SERVICE_HANDLE_TAX_INCLUSIVE applied for service %s rate=%s", service.id, COMMERCE_DEFAULT_TAX_RATE_PCT)
    return taxed


def _enforce_minimum_charge(service, price, negotiation_flow):
    min_charge = _decimal_from_value(getattr(service, 'minimum_charge', None))
    if min_charge <= 0:
        return price
    if SERVICE_ENFORCE_MINIMUM_CHARGE and not negotiation_flow:
        if price < min_charge:
            raise ValidationError({
                'detail': f"Minimum charge for this service is {min_charge}.",
            })
        return price
    if not negotiation_flow and price < min_charge:
        return min_charge
    return price


def _normalize_requirement_tokens(items):
    return [str(item).strip() for item in (items or []) if str(item).strip()]


def _validate_requirements_acknowledgement(service, provided):
    requirements = _normalize_requirement_tokens(getattr(service, 'requirements', []) or [])
    acknowledgements = _normalize_requirement_tokens(provided)
    if not SERVICE_ENFORCE_REQUIREMENTS or not requirements:
        return acknowledgements
    normalized_ack = {item.lower() for item in acknowledgements}
    missing = [req for req in requirements if req.lower() not in normalized_ack]
    if missing:
        logger.warning(
            "SERVICE_ENFORCE_REQUIREMENTS blocked booking for service %s missing=%s",
            service.id,
            missing,
        )
        raise ValidationError({
            'requirements_acknowledged': 'Please acknowledge all service requirements before booking.',
        })
    logger.info(
        "SERVICE_ENFORCE_REQUIREMENTS satisfied for service %s requirements=%s",
        service.id,
        requirements,
    )
    return acknowledgements


def _ensure_terms_accepted(service, accepted):
    terms_text = str(getattr(service, 'service_terms', '') or '').strip()
    if not SERVICE_REQUIRE_TERMS_ACCEPTANCE or not terms_text:
        return bool(accepted)
    if not accepted:
        logger.warning(
            "SERVICE_REQUIRE_TERMS_ACCEPTANCE blocked booking for service %s",
            service.id,
        )
        raise ValidationError({
            'terms_accepted': 'You must accept the service terms before booking.',
        })
    logger.info("SERVICE_REQUIRE_TERMS_ACCEPTANCE satisfied for service %s", service.id)
    return True


def _record_booking_receipt(booking, amount_cents, transaction_reference, phase):
    if not booking or amount_cents <= 0:
        return None
    return ServiceBookingReceipt.objects.create(
        booking=booking,
        amount_cents=amount_cents,
        currency=KIS_COIN_CODE,
        transaction_reference=transaction_reference or "",
        phase=phase,
    )


def _build_booking_metadata(
    service,
    package_option,
    addon_options,
    quote_flow,
    negotiation_flow,
    requested_price,
    requirements_ack,
    terms_accepted,
):
    metadata = {
        'pricing_model': str(getattr(service, 'pricing_model', '') or 'standard'),
        'tax_inclusive': bool(getattr(service, 'tax_inclusive', True)),
        'requirements_acknowledged': requirements_ack or [],
        'terms_accepted': bool(terms_accepted),
    }
    if package_option:
        metadata['package_selection'] = {
            'name': package_option['name'],
            'price': str(package_option['price']),
            'duration_minutes': package_option['duration_minutes'],
        }
    if addon_options:
        metadata['addon_selection'] = [
            {
                'name': item['name'],
                'price': str(item['price']),
                'duration_minutes': item['duration_minutes'],
            }
            for item in addon_options
        ]
    if quote_flow:
        metadata['quote_required'] = True
    if negotiation_flow:
        metadata['negotiation_requested'] = True
        metadata['requested_price'] = str(requested_price)
    return metadata


def _calculate_deposit_cents(service, price_cents):
    if price_cents <= 0:
        return 0
    deposit_value = getattr(service, 'deposit_amount', None)
    deposit_percent = getattr(service, 'deposit_percent', None)
    deposit_cents = 0
    if deposit_value is not None:
        deposit_dec = _decimal_from_value(deposit_value)
        deposit_cents = _to_cents(deposit_dec)
    elif deposit_percent is not None:
        percent = _decimal_from_value(deposit_percent)
        deposit_cents = int(price_cents * percent / Decimal('100'))
    else:
        deposit_cents = price_cents
    deposit_cents = min(max(deposit_cents, 0), price_cents)
    if deposit_cents <= 0:
        deposit_cents = price_cents
    return deposit_cents


def _record_reschedule_metadata(booking, previous, new_scheduled):
    metadata = dict(getattr(booking, 'metadata', {}) or {})
    history = metadata.get('reschedules', [])
    history.append({
        'from': previous.isoformat(),
        'to': new_scheduled.isoformat(),
        'updated_at': timezone.now().isoformat(),
    })
    metadata['reschedules'] = history
    booking.metadata = metadata

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
SERVICE_ENFORCE_BUFFERS = getattr(settings, "SERVICE_ENFORCE_BUFFERS", False)
SERVICE_ENFORCE_COVERAGE = getattr(settings, "SERVICE_ENFORCE_COVERAGE", False)
SERVICE_ENFORCE_TRAVEL_RADIUS = getattr(settings, "SERVICE_ENFORCE_TRAVEL_RADIUS", False)
SERVICE_ENFORCE_REMOTE_REGIONS = getattr(settings, "SERVICE_ENFORCE_REMOTE_REGIONS", False)
SERVICE_ENFORCE_GROUP_CAPACITY = getattr(settings, "SERVICE_ENFORCE_GROUP_CAPACITY", False)
SERVICE_ENABLE_QUOTES = getattr(settings, "SERVICE_ENABLE_QUOTES", False)
SERVICE_ENABLE_NEGOTIATION = getattr(settings, "SERVICE_ENABLE_NEGOTIATION", False)
SERVICE_ENABLE_PACKAGE_PRICING = getattr(settings, "SERVICE_ENABLE_PACKAGE_PRICING", False)
SERVICE_ENABLE_ADDONS = getattr(settings, "SERVICE_ENABLE_ADDONS", False)
SERVICE_ENFORCE_MINIMUM_CHARGE = getattr(settings, "SERVICE_ENFORCE_MINIMUM_CHARGE", False)
SERVICE_HANDLE_TAX_INCLUSIVE = getattr(settings, "SERVICE_HANDLE_TAX_INCLUSIVE", False)
_COMMERCE_DEFAULT_TAX_RATE_PCT = getattr(settings, "COMMERCE_DEFAULT_TAX_RATE_PCT", "0")
try:
    COMMERCE_DEFAULT_TAX_RATE_PCT = Decimal(str(_COMMERCE_DEFAULT_TAX_RATE_PCT) or "0")
except InvalidOperation:
    COMMERCE_DEFAULT_TAX_RATE_PCT = Decimal("0")
SERVICE_REQUIRE_TERMS_ACCEPTANCE = getattr(settings, "SERVICE_REQUIRE_TERMS_ACCEPTANCE", False)
SERVICE_ENFORCE_REQUIREMENTS = getattr(settings, "SERVICE_ENFORCE_REQUIREMENTS", False)
SERVICE_ENFORCE_REFUND_POLICY = getattr(settings, "SERVICE_ENFORCE_REFUND_POLICY", False)
SERVICE_ENFORCE_RESCHEDULE_POLICY = getattr(settings, "SERVICE_ENFORCE_RESCHEDULE_POLICY", False)


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


def _get_service_busy_range(service, scheduled_at):
    aware = _make_aware(scheduled_at)
    duration_minutes = max(0, getattr(service, "duration_minutes", 0) or 0)
    prep_minutes = max(0, getattr(service, "prep_buffer_minutes", 0) or 0)
    cleanup_minutes = max(0, getattr(service, "cleanup_buffer_minutes", 0) or 0)
    start = aware - timedelta(minutes=prep_minutes)
    end = aware + timedelta(minutes=duration_minutes + cleanup_minutes)
    return start, end


def _ensure_no_buffer_conflict(service, scheduled_at):
    if not SERVICE_ENFORCE_BUFFERS or service.group_booking_allowed:
        return
    logger.info("SERVICE_ENFORCE_BUFFERS active for service %s", service.id)
    start, end = _get_service_busy_range(service, scheduled_at)
    turnaround = timedelta(hours=max(0, getattr(service, "turnaround_hours", 0) or 0))
    existing_qs = ServiceBooking.objects.filter(service=service, status__in=ACTIVE_BOOKING_STATUSES)
    for booking in existing_qs:
        existing_start, existing_end = _get_service_busy_range(service, booking.scheduled_at)
        existing_end_extended = existing_end + turnaround
        if start < existing_end_extended and end > existing_start:
            logger.warning(
                "Booking rejected for service %s: buffer/turnaround conflict with existing slot %s-%s",
                service.id,
                existing_start.isoformat(),
                existing_end_extended.isoformat(),
            )
            raise ValidationError({"scheduled_at": "Requested slot conflicts with service buffers or turnaround time."})


def _normalize_location_payload(payload):
    if not payload or not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items() if value not in (None, "")}



def _validate_service_location(service, location_payload, distance_km, is_remote, remote_region):
    if not any((SERVICE_ENFORCE_COVERAGE, SERVICE_ENFORCE_TRAVEL_RADIUS, SERVICE_ENFORCE_REMOTE_REGIONS)):
        return
    location = _normalize_location_payload(location_payload)
    coverage = [str(item).strip().lower() for item in (getattr(service, "coverage", []) or []) if str(item).strip()]

    if is_remote:
        if SERVICE_ENFORCE_REMOTE_REGIONS and getattr(service, "remote_regions", None):
            logger.info("SERVICE_ENFORCE_REMOTE_REGIONS active for service %s", service.id)
            remote_hint = str(remote_region or location.get("region") or location.get("state") or location.get("country") or "").strip()
            if remote_hint:
                allowed_remote = [str(item).strip().lower() for item in service.remote_regions if str(item).strip()]
                remote_hint_lower = remote_hint.lower()
                if allowed_remote and not any(token in remote_hint_lower for token in allowed_remote):
                    logger.warning(
                        "Booking rejected for service %s: remote region '%s' not covered (%s)",
                        service.id,
                        remote_hint,
                        allowed_remote,
                    )
                    raise ValidationError({"remote_region": "Remote region is not covered by this service."})
        return

    if SERVICE_ENFORCE_COVERAGE and coverage and location:
        logger.info("SERVICE_ENFORCE_COVERAGE active for service %s", service.id)
        tokens = [value for value in [location.get("region"), location.get("city"), location.get("state"), location.get("country")] if value]
        normalized_tokens = [str(token).strip().lower() for token in tokens]
        if normalized_tokens and not any(any(token in entry for entry in coverage) for token in normalized_tokens):
            logger.warning(
                "Booking rejected for service %s: location %s not in coverage %s",
                service.id,
                normalized_tokens,
                coverage,
            )
            raise ValidationError({"location": "Your location is not within the configured coverage area."})

    if SERVICE_ENFORCE_TRAVEL_RADIUS and distance_km is not None:
        logger.info("SERVICE_ENFORCE_TRAVEL_RADIUS active for service %s", service.id)
        try:
            distance_value = Decimal(distance_km)
        except (InvalidOperation, TypeError, ValueError):
            return
        radius_value = Decimal(getattr(service, "travel_radius_km", 0) or 0)
        if radius_value > 0 and distance_value > radius_value:
            logger.warning(
                "Booking rejected for service %s: distance %s km exceeds radius %s km",
                service.id,
                distance_value,
                radius_value,
            )
            raise ValidationError({"distance_km": "The requested travel distance exceeds the service radius."})


def _validate_group_capacity(validated_data, service):
    if not SERVICE_ENFORCE_GROUP_CAPACITY:
        return
    logger.info("SERVICE_ENFORCE_GROUP_CAPACITY active for service %s", service.id)
    participant_count = validated_data.get("participant_count")
    if participant_count is not None:
        max_participants = getattr(service, "max_participants", 0) or 0
        if max_participants and participant_count > max_participants:
            logger.warning(
                "Booking rejected for service %s: participant_count %s > max_participants %s",
                service.id,
                participant_count,
                max_participants,
            )
            raise ValidationError({"participant_count": f"A maximum of {max_participants} participants is allowed."})
    staff_on_site = validated_data.get("staff_on_site")
    if staff_on_site is not None:
        min_staff = getattr(service, "staff_required", 0) or 0
        if min_staff and staff_on_site < min_staff:
            logger.warning(
                "Booking rejected for service %s: staff_on_site %s < staff_required %s",
                service.id,
                staff_on_site,
                min_staff,
            )
            raise ValidationError({"staff_on_site": f"At least {min_staff} staff members are required for this service."})


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


class CartViewSet(viewsets.ModelViewSet):
    queryset = Cart.objects.all().order_by('-updated_at')
    serializer_class = CartSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def perform_create(self, serializer):
        shop = serializer.validated_data.get('shop')
        if not shop:
            raise ValidationError({'shop': 'Shop is required.'})
        existing = Cart.objects.filter(user=self.request.user, shop=shop, status='active').first()
        if existing:
            raise ValidationError({'detail': 'An active cart already exists for this shop.'})
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'], url_path='current')
    def current(self, request):
        shop_id = request.query_params.get('shop_id')
        if not shop_id:
            raise ValidationError({'shop_id': 'shop_id query parameter is required.'})
        cart = Cart.objects.filter(user=request.user, shop_id=shop_id, status='active').first()
        if not cart:
            raise NotFound('No active cart found for this shop.')
        serializer = self.get_serializer(cart)
        return Response(serializer.data)


class CartItemViewSet(viewsets.ModelViewSet):
    queryset = CartItem.objects.all().order_by('-created_at')
    serializer_class = CartItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return super().get_queryset().filter(cart__user=self.request.user)

    def _validate_cart(self, cart: Cart):
        if cart.user_id != self.request.user.id:
            raise PermissionDenied('Cart does not belong to you.')
        if cart.status != 'active':
            raise ValidationError({'cart': 'Cart is not active.'})
        return cart

    def _validate_product(self, cart: Cart, product: Product):
        if product.shop_id != cart.shop_id:
            raise ValidationError({'product': 'Product must belong to the same shop as the cart.'})
        return product

    def perform_create(self, serializer):
        cart = serializer.validated_data.get('cart')
        product = serializer.validated_data.get('product')
        if not cart or not product:
            raise ValidationError({'detail': 'Cart and product are required.'})
        self._validate_cart(cart)
        self._validate_product(cart, product)
        serializer.validated_data.setdefault('price_snapshot', product.sale_price or product.price)
        serializer.validated_data.setdefault('stock_snapshot', product.stock_qty or 0)
        serializer.save()

    def perform_update(self, serializer):
        cart = serializer.validated_data.get('cart') or serializer.instance.cart
        if cart:
            self._validate_cart(cart)
        product = serializer.validated_data.get('product') or serializer.instance.product
        if cart and product:
            self._validate_product(cart, product)
        serializer.save()


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
        requirements_ack = _validate_requirements_acknowledgement(
            service,
            serializer.validated_data.get("requirements_acknowledged"),
        )
        terms_accepted = _ensure_terms_accepted(
            service,
            serializer.validated_data.get("terms_accepted"),
        )
        _validate_group_capacity(serializer.validated_data, service)
        _validate_service_location(
            service,
            serializer.validated_data.get("location"),
            serializer.validated_data.get("distance_km"),
            serializer.validated_data.get("is_remote"),
            serializer.validated_data.get("remote_region"),
        )
        _ensure_no_buffer_conflict(service, scheduled_at)
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

        selected_package = serializer.validated_data.get("selected_package")
        selected_addons = serializer.validated_data.get("selected_addons") or []
        requested_price = serializer.validated_data.get("requested_price")
        package_option = _resolve_service_package_option(service, selected_package)
        addon_options = _resolve_service_addon_options(service, selected_addons)
        package_price = package_option["price"] if SERVICE_ENABLE_PACKAGE_PRICING and package_option else Decimal("0")
        addon_price = sum(item["price"] for item in addon_options) if SERVICE_ENABLE_ADDONS and addon_options else Decimal("0")
        if SERVICE_ENABLE_PACKAGE_PRICING and package_option:
            logger.info("SERVICE_ENABLE_PACKAGE_PRICING applied for service %s package=%s", service.id, package_option["name"])
        if SERVICE_ENABLE_ADDONS and addon_options:
            logger.info(
                "SERVICE_ENABLE_ADDONS applied for service %s addons=%s",
                service.id,
                [item["name"] for item in addon_options],
            )
        quote_flow = SERVICE_ENABLE_QUOTES and bool(getattr(service, "quote_required", False))
        negotiation_flow = False
        requested_price_value = None
        if SERVICE_ENABLE_NEGOTIATION and getattr(service, "negotiable", False) and requested_price is not None:
            requested_price_value = _decimal_from_value(requested_price)
            negotiation_flow = True
            logger.info(
                "SERVICE_ENABLE_NEGOTIATION requested for service %s requested_price=%s",
                service.id,
                requested_price_value,
            )
        base_price = _decimal_from_value(service.price)
        total_price = requested_price_value if negotiation_flow else base_price + package_price + addon_price
        pricing_model = str(getattr(service, "pricing_model", "") or "").strip().lower()
        if pricing_model == "membership" and not negotiation_flow:
            min_charge_value = _decimal_from_value(getattr(service, "minimum_charge", None))
            if min_charge_value > 0:
                total_price = max(total_price, min_charge_value)
        total_price = _enforce_minimum_charge(service, total_price, negotiation_flow)
        total_price = _apply_tax_if_needed(service, total_price)
        price_cents = _to_cents(total_price)
        skip_payment = quote_flow or negotiation_flow
        if quote_flow:
            logger.info("SERVICE_ENABLE_QUOTES triggered for service %s", service.id)
        balance_cents = price_cents
        deposit_cents = 0
        if not skip_payment:
            deposit_cents = _calculate_deposit_cents(service, price_cents)
            balance_cents = max(price_cents - deposit_cents, 0)
        booking_metadata = _build_booking_metadata(
            service,
            package_option if SERVICE_ENABLE_PACKAGE_PRICING else None,
            addon_options if SERVICE_ENABLE_ADDONS else [],
            quote_flow,
            negotiation_flow,
            requested_price_value,
            requirements_ack,
            terms_accepted,
        )

        tx_ref = str(uuid.uuid4())
        wallet_locked = False
        try:
            if not skip_payment and deposit_cents > 0:
                lock_wallet_funds_for_booking(
                    user=request.user,
                    amount_cents=_wallet_amount(deposit_cents),
                    reference=tx_ref,
                    meta={"service_id": str(service.id)},
                )
                wallet_locked = True
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
                    metadata=booking_metadata,
                )
                if wallet_locked:
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
                    if deposit_cents > 0:
                        _record_booking_receipt(
                            booking,
                            deposit_cents,
                            tx_ref,
                            ServiceBookingReceipt.PHASE_DEPOSIT,
                        )
        except Exception as exc:
            logger.error(
                "ServiceBook/create failed user=%s service=%s error=%s",
                user_id,
                service_id,
                str(exc),
                exc_info=True,
            )
            if wallet_locked:
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
        service = booking.service
        window_hours = CANCELLATION_WINDOW_HOURS
        if SERVICE_ENFORCE_REFUND_POLICY:
            service_window = getattr(service, 'cancellation_window_hours', None)
            if service_window:
                window_hours = max(int(service_window), CANCELLATION_WINDOW_HOURS)
            logger.info("SERVICE_ENFORCE_REFUND_POLICY active for service %s window=%s", service.id, window_hours)
        cutoff = booking.scheduled_at - timedelta(hours=window_hours)
        if now > cutoff:
            detail = (str(service.refund_policy or "").strip()) or f"Cancel at least {window_hours} hours before the scheduled time for a refund."
            return Response({"message": detail}, status=status.HTTP_400_BAD_REQUEST)

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

    @action(detail=True, methods=["post"], url_path="reschedule")
    def reschedule(self, request, pk=None):
        booking = self.get_object()
        if not self._can_user_cancel_booking(request.user, booking):
            raise PermissionDenied("Only the payer, owner, manager, or staff can reschedule this booking.")
        if booking.status not in {ServiceBooking.STATUS_PENDING, ServiceBooking.STATUS_CONFIRMED}:
            return Response({"message": "This booking cannot be rescheduled."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = ServiceBookingRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_scheduled_at = _make_aware(serializer.validated_data["scheduled_at"])
        now = timezone.now()
        if new_scheduled_at <= now:
            return Response({"message": "Scheduled time must be in the future."}, status=status.HTTP_400_BAD_REQUEST)

        service = booking.service
        if SERVICE_ENFORCE_RESCHEDULE_POLICY:
            window_hours = max(int(getattr(service, 'reschedule_window_hours', 0) or 0), 0)
            if window_hours:
                cutoff = booking.scheduled_at - timedelta(hours=window_hours)
                logger.info("SERVICE_ENFORCE_RESCHEDULE_POLICY active for service %s window=%s", service.id, window_hours)
                if now > cutoff:
                    return Response(
                        {"message": f"Reschedule requests must be made at least {window_hours} hours before the original slot."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        _ensure_no_buffer_conflict(service, new_scheduled_at)
        try:
            _validate_service_schedule(service, new_scheduled_at)
        except ValidationError as exc:
            logger.warning(
                "ServiceBook/reschedule rejected schedule user=%s service=%s new_slot=%s errors=%s",
                request.user.id,
                service.id,
                new_scheduled_at,
                exc.detail,
            )
            raise

        limit = service.max_bookings_per_slot if service.group_booking_allowed else 1
        limit = max(1, limit or 1)
        slot_qs = ServiceBooking.objects.filter(
            service=service,
            scheduled_at=new_scheduled_at,
            status__in=ACTIVE_BOOKING_STATUSES,
        ).exclude(id=booking.id)
        if slot_qs.count() >= limit:
            return Response(
                {"detail": "The requested slot is already booked."},
                status=status.HTTP_409_CONFLICT,
            )

        previous = booking.scheduled_at
        booking.scheduled_at = new_scheduled_at
        _record_reschedule_metadata(booking, previous, new_scheduled_at)
        booking.save(update_fields=["scheduled_at", "metadata"])
        output = ServiceBookingSerializer(booking, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_200_OK)

    def _select_booking_receipt(self, booking, receipt_id=None, phase=None):
        receipts_qs = booking.receipts.all()
        if receipt_id:
            return receipts_qs.filter(id=receipt_id).first()
        normalized_phase = str(phase or "").strip().lower()
        if normalized_phase:
            return receipts_qs.filter(phase=normalized_phase).order_by("-created_at").first()
        return receipts_qs.order_by("-created_at").first()

    @action(detail=True, methods=["get"], url_path="receipt")
    def receipt(self, request, pk=None):
        booking = self.get_object()
        if not self._can_user_view_booking(request.user, booking):
            raise PermissionDenied("You are not allowed to view this booking receipt.")
        receipt = self._select_booking_receipt(
            booking,
            receipt_id=request.query_params.get("receipt_id"),
            phase=request.query_params.get("phase"),
        )
        html_url, pdf_url = build_booking_receipt_urls(request, booking, receipt)
        response = {"receipt_url": html_url, "receipt_pdf_url": pdf_url}
        if receipt:
            response.update({
                "receipt_id": str(receipt.id),
                "receipt_phase": receipt.phase,
                "receipt_amount_cents": receipt.amount_cents,
                "receipt_currency": receipt.currency,
            })
        return Response(response)

    @action(detail=True, methods=["post"], url_path="receipt/regenerate")
    def receipt_regenerate(self, request, pk=None):
        booking = self.get_object()
        if not self._can_user_view_booking(request.user, booking):
            raise PermissionDenied("You are not allowed to view this booking receipt.")
        receipt_id = request.data.get("receipt_id") or request.query_params.get("receipt_id")
        phase = request.data.get("phase") or request.query_params.get("phase")
        receipt = self._select_booking_receipt(booking, receipt_id=receipt_id, phase=phase)
        html_url, pdf_url = build_booking_receipt_urls(request, booking, receipt, force=True)
        response = {"receipt_url": html_url, "receipt_pdf_url": pdf_url}
        if receipt:
            response.update({
                "receipt_id": str(receipt.id),
                "receipt_phase": receipt.phase,
                "receipt_amount_cents": receipt.amount_cents,
                "receipt_currency": receipt.currency,
            })
        return Response(response)

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
                if remaining > 0:
                    _record_booking_receipt(
                        booking,
                        remaining,
                        tx_ref,
                        ServiceBookingReceipt.PHASE_REMAINING,
                    )
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
