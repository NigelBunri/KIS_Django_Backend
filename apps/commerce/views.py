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
from django.http import FileResponse
from django.utils import timezone

import logging

from apps.accounts.tiers import get_user_tier, get_feature_limit, normalize_limit_value
from apps.accounts.notification_i18n import get_notification_string, get_user_language
from apps.notifications import services as notification_services
from apps.billing.documents import build_booking_receipt_urls
from apps.billing.direct_payments import create_direct_payment_intent
from apps.billing.services import lock_wallet_funds_for_booking, release_locked_booking_funds, refund_locked_booking_funds
from apps.core.money import parse_decimal_amount
from apps.media.safety import validate_upload_file_safety

from . import media_uploads
from .availability import format_date_key, get_day_key, normalize_availability_payload
from .category_catalog import ensure_catalog_categories
from .documents import render_marketplace_receipt_pdf
from .models import (
    Shop,
    ShopPayoutAccountStatus,
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
    ProductRating,
    ProductReview,
    ProductQuestion,
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
    MarketplaceOrder,
    MarketplaceOrderStatus,
    MarketplaceComplaint,
    CatalogCategory,
)
from .services import (
    place_marketplace_order,
    cancel_marketplace_order_by_id,
    satisfy_marketplace_order_by_id,
    provider_complete_marketplace_order_by_id,
    create_marketplace_complaint_from_data,
    _provider_can_manage_shop,
)
from apps.verification.serializers import validate_private_evidence_metadata
from apps.verification.services import sync_shop_verification_request
from .serializers import (
    ShopSerializer,
    ShopVerificationRequestSerializer,
    ProductSerializer,
    ProductImageSerializer,
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
    ProductReviewSerializer,
    ProductQuestionSerializer,
    CatalogCategorySerializer,
    ShopServiceSerializer,
    ServiceImageSerializer,
    ShopTeamMemberSerializer,
    ServiceBookingSerializer,
    ServiceBookingCreateSerializer,
    ServiceBookingRescheduleSerializer,
    ServiceBookingPaymentSerializer,
    ServiceBookingComplaintSerializer,
    CartSerializer,
    CartItemSerializer,
    MarketplaceOrderSerializer,
    MarketplaceOrderCreateSerializer,
    MarketplaceComplaintSerializer,
    MarketplaceComplaintCreateSerializer,
)

logger = logging.getLogger(__name__)

def _wallet_amount(value: int | None) -> int:
    return max(0, int(value or 0))


def _decimal_from_value(value):
    parsed = parse_decimal_amount(value)
    if parsed is None:
        return Decimal("0")
    return parsed


def _to_cents(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _commerce_wallet_checkout_enabled() -> bool:
    return bool(getattr(settings, "KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED", False))


def _commerce_default_payment_provider() -> str:
    return str(getattr(settings, "KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER", "flutterwave") or "flutterwave").strip().lower()


def _is_legacy_wallet_method(value: object) -> bool:
    return str(value or "").strip().lower() in {"wallet", "kisc", "kis_wallet", "wallet_balance"}


def _recalculate_cart_subtotal(cart: Cart | None) -> None:
    if not cart:
        return
    subtotal = Decimal("0")
    for item in cart.items.all():
        subtotal += Decimal(item.price_snapshot or 0) * Decimal(item.quantity or 0)
    cart.subtotal = subtotal.quantize(Decimal("0.01"))
    cart.save(update_fields=["subtotal", "updated_at"])


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
    if not _service_flag("SERVICE_HANDLE_TAX_INCLUSIVE", False) or getattr(service, 'tax_inclusive', True):
        return price
    tax_rate_pct = _commerce_default_tax_rate_pct()
    if tax_rate_pct <= 0:
        return price
    multiplier = Decimal('1') + (tax_rate_pct / Decimal('100'))
    taxed = (price * multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    logger.info("SERVICE_HANDLE_TAX_INCLUSIVE applied for service %s rate=%s", service.id, tax_rate_pct)
    return taxed


def _enforce_minimum_charge(service, price, negotiation_flow):
    min_charge = _decimal_from_value(getattr(service, 'minimum_charge', None))
    if min_charge <= 0:
        return price
    if _service_flag("SERVICE_ENFORCE_MINIMUM_CHARGE", False) and not negotiation_flow:
        if price < min_charge:
            raise ValidationError({
                'detail': f"Minimum charge for this service is {min_charge}.",
            })
        return price
    if not negotiation_flow and price < min_charge:
        return min_charge
    return price


def _normalize_requirement_tokens(items):
    normalized = []
    for item in (items or []):
        if isinstance(item, dict):
            candidate = str(item.get('label') or item.get('name') or '').strip()
        else:
            candidate = str(item).strip()
        if candidate:
            normalized.append(candidate)
    return normalized


def _validate_requirements_acknowledgement(service, provided):
    requirements = _normalize_requirement_tokens(getattr(service, 'requirements', []) or [])
    acknowledgements = _normalize_requirement_tokens(provided)
    if not _service_flag("SERVICE_ENFORCE_REQUIREMENTS", False) or not requirements:
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
    if not _service_flag("SERVICE_REQUIRE_TERMS_ACCEPTANCE", False) or not terms_text:
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
        currency="USD",
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
    *,
    location=None,
    distance_km=None,
    is_remote=False,
    remote_region="",
    participant_count=None,
    staff_on_site=None,
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
    normalized_location = _normalize_location_payload(location)
    if normalized_location:
        metadata['location'] = normalized_location
    if distance_km is not None:
        metadata['distance_km'] = str(distance_km)
    if is_remote:
        metadata['is_remote'] = True
    if remote_region:
        metadata['remote_region'] = str(remote_region)
    if participant_count is not None:
        metadata['participant_count'] = int(participant_count)
    if staff_on_site is not None:
        metadata['staff_on_site'] = int(staff_on_site)
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


def _presigned_get_expiry_seconds() -> int:
    # Mirrors S3MediaStorage._env("AWS_S3_PRESIGNED_EXPIRY_SECONDS", "3600")
    # exactly (same env var, same default) — informational only, the URL
    # itself is already correctly time-limited by the storage backend
    # regardless of what this reports.
    import os

    return int(os.environ.get("AWS_S3_PRESIGNED_EXPIRY_SECONDS", "3600").strip() or "3600")


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
def _service_flag(name, default=False):
    return getattr(settings, name, default)


def _commerce_default_tax_rate_pct():
    raw_value = getattr(settings, "COMMERCE_DEFAULT_TAX_RATE_PCT", "0")
    try:
        return Decimal(str(raw_value) or "0")
    except InvalidOperation:
        return Decimal("0")


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

    min_hours = max(int(getattr(service, "min_notice_hours", 0) or 0), 0)
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
    if not _service_flag("SERVICE_ENFORCE_BUFFERS", False) or service.group_booking_allowed:
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
    enforce_coverage = _service_flag("SERVICE_ENFORCE_COVERAGE", False)
    enforce_travel_radius = _service_flag("SERVICE_ENFORCE_TRAVEL_RADIUS", False)
    enforce_remote_regions = _service_flag("SERVICE_ENFORCE_REMOTE_REGIONS", False)
    if not any((enforce_coverage, enforce_travel_radius, enforce_remote_regions)):
        return
    location = _normalize_location_payload(location_payload)
    coverage = [str(item).strip().lower() for item in (getattr(service, "coverage", []) or []) if str(item).strip()]

    if is_remote:
        if enforce_remote_regions and getattr(service, "remote_regions", None):
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

    if enforce_coverage and coverage and location:
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

    if enforce_travel_radius and distance_km is not None:
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
    if not _service_flag("SERVICE_ENFORCE_GROUP_CAPACITY", False):
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
class CommerceUploadInitiateView(APIView):
    """POST /api/v1/commerce/uploads/initiate/

    Marketplace-specific entry point to the shared direct-to-S3 presigned
    upload handshake (apps/media/upload_intent.py) — see
    apps/commerce/media_uploads.py for why this needs its own initiate view
    rather than reusing the generic one: commerce ownership varies by
    purpose (shop owner-or-staff, product/service via their shop, order
    buyer-or-shop-manager) and must be checked against a specific
    shop/product/service/order BEFORE a presigned URL is ever issued.

    Confirmation still goes through the existing, unmodified
    POST /api/v1/media/uploads/<uuid:upload_id>/confirm/ — see
    apps.media.upload_intent._confirm_only_media_descriptor.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        body = request.data
        result = media_uploads.initiate_commerce_upload(
            user=request.user,
            purpose=str(body.get('purpose') or '').strip(),
            filename=body.get('filename'),
            content_type=body.get('contentType') or body.get('content_type'),
            size_bytes=body.get('sizeBytes') or body.get('size_bytes'),
            shop_id=body.get('shopId') or body.get('shop_id'),
            product_id=body.get('productId') or body.get('product_id'),
            service_id=body.get('serviceId') or body.get('service_id'),
            order_id=body.get('orderId') or body.get('order_id'),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class ShopViewSet(viewsets.ModelViewSet):
    queryset = Shop.objects.all().order_by('-created_at')
    serializer_class = ShopSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        owner_id = self.request.query_params.get("owner")
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        if self.action == 'list' and not self.request.user.is_staff:
            if self.request.user.is_authenticated:
                qs = qs.filter(Q(status=Shop.STATUS_ACTIVE) | Q(owner=self.request.user))
            else:
                qs = qs.filter(status=Shop.STATUS_ACTIVE)
        return qs

    def get_object(self):
        obj = super().get_object()
        if self.request.method not in permissions.SAFE_METHODS:
            if not _provider_can_manage_shop(self.request.user, obj) and not self.request.user.is_staff:
                raise PermissionDenied("Only shop owners, team managers, partner managers, or staff can modify shops.")
        return obj

    def perform_create(self, serializer):
        shop_limit = _normalize_limit(get_feature_limit(self.request.user, "shops_limit", 0))
        if shop_limit is not None and shop_limit == 0:
            raise PermissionDenied(
                "Marketplace stores are available on Business plans and above. "
                "Upgrade to Business or higher to create a shop."
            )
        existing = Shop.objects.filter(owner=self.request.user, is_deleted=False).count()
        if shop_limit is not None and existing >= shop_limit:
            raise ValidationError({"detail": f"Your current plan allows up to {shop_limit} shop{'s' if shop_limit != 1 else ''}. Upgrade to create more."})
        image_file = serializer.validated_data.get("image_file")
        _enforce_media_size_limit(self.request.user, image_file, field_name="image_file")
        shop = serializer.save(owner=self.request.user)
        self._attach_pending_media(shop)

    def perform_update(self, serializer):
        image_file = serializer.validated_data.get("image_file")
        _enforce_media_size_limit(self.request.user, image_file, field_name="image_file")
        super().perform_update(serializer)
        self._attach_pending_media(serializer.instance)

    def _attach_pending_media(self, shop):
        # Presigned-upload counterpart to the legacy `image_file` multipart
        # field above: lets a client finish uploading a shop image via
        # initiate -> S3 PUT -> confirm BEFORE the shop exists, then create
        # (or update) the shop referencing the resulting mediaId in the
        # same JSON request — no fake shop id required.
        media_id = self.request.data.get("image_media_id")
        if media_id:
            media_uploads.attach_shop_image(user=self.request.user, shop_id=shop.id, media_id=media_id)

    @doc_decorator(
        summary="Attach a confirmed shop image",
        description="Bind a confirmed direct-to-S3 upload (see /api/v1/commerce/uploads/initiate/) as this shop's image.",
        request=None,
        responses={200: OpenApiResponse(description="Attached") if SPECTACULAR else "Attached"},
    )
    @action(detail=True, methods=['post'], url_path='image/attach')
    def image_attach(self, request, pk=None):
        shop = self.get_object()
        media_id = request.data.get('mediaId') or request.data.get('media_id')
        updated = media_uploads.attach_shop_image(user=request.user, shop_id=shop.id, media_id=media_id)
        return Response(self.get_serializer(updated).data)

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
        documents = data.get('documents', [])
        validate_private_evidence_metadata({"documents": documents})
        svr = ShopVerificationRequest.objects.create(shop=shop, requested_by=request.user, documents=documents)
        shop.verification_status = 'PENDING'
        shop.save(update_fields=['verification_status', 'updated_at'])
        sync_shop_verification_request(verification_request=svr, actor=request.user)
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


class ShopPayoutAccountConnectView(APIView):
    """Connects the shop's Flutterwave subaccount for direct-to-shop
    settlement splitting — mirrors
    EducationInstitutionPayoutAccountConnectView (apps/broadcasts/views.py)
    exactly, using the shared apps.billing.payout_accounts helper."""

    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, shop_id: str):
        from apps.billing.payout_accounts import create_flutterwave_subaccount

        shop = get_object_or_404(Shop, id=shop_id)
        if not _provider_can_manage_shop(request.user, shop) and not request.user.is_staff:
            raise PermissionDenied("Only the shop owner, team manager, or partner manager can connect a payout account.")

        account_bank = str(request.data.get("account_bank") or "").strip()
        account_number = str(request.data.get("account_number") or "").strip()
        business_name = str(request.data.get("business_name") or shop.name).strip()
        country = str(request.data.get("country") or "NG").strip().upper()
        if not account_bank or not account_number:
            raise ValidationError({"detail": "Bank and account number are required."})

        shop.payout_account_status = ShopPayoutAccountStatus.PENDING
        shop.save(update_fields=["payout_account_status", "updated_at"])

        try:
            subaccount_id = create_flutterwave_subaccount(
                account_bank=account_bank,
                account_number=account_number,
                business_name=business_name,
                business_email=shop.owner.email or "",
                country=country,
            )
        except ValidationError:
            shop.payout_account_status = ShopPayoutAccountStatus.NOT_CONNECTED
            shop.save(update_fields=["payout_account_status", "updated_at"])
            raise

        shop.flutterwave_subaccount_id = subaccount_id
        shop.payout_account_status = ShopPayoutAccountStatus.ACTIVE
        shop.payout_account_name = business_name
        shop.payout_bank_last4 = account_number[-4:] if len(account_number) >= 4 else account_number
        shop.save(
            update_fields=[
                "flutterwave_subaccount_id",
                "payout_account_status",
                "payout_account_name",
                "payout_bank_last4",
                "updated_at",
            ]
        )
        return Response(
            {
                "payout_account_status": shop.payout_account_status,
                "payout_account_name": shop.payout_account_name,
                "payout_bank_last4": shop.payout_bank_last4,
            },
            status=status.HTTP_200_OK,
        )


class ShopStripeConnectAccountView(APIView):
    """Connects the shop's Stripe Express account — the second supported
    payout rail alongside ShopPayoutAccountConnectView's Flutterwave
    subaccount, using the shared apps.billing.stripe_connect helpers. GET
    re-queries Stripe directly for current status (never trusts stale
    frontend state); POST creates the account on first call (idempotent —
    reuses shop.stripe_account_id on repeat calls) and returns a fresh
    onboarding link."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, shop_id: str):
        from apps.billing.stripe_connect import refresh_account_status

        shop = get_object_or_404(Shop, id=shop_id)
        if not _provider_can_manage_shop(request.user, shop) and not request.user.is_staff:
            raise PermissionDenied("Only the shop owner, team manager, or partner manager can view payout account status.")

        if shop.stripe_account_id:
            fields = refresh_account_status(shop.stripe_account_id)
            for field_name, value in fields.items():
                setattr(shop, field_name, value)
            shop.save(update_fields=[*fields.keys(), "updated_at"])

        return Response(
            {
                "stripe_account_id": shop.stripe_account_id,
                "stripe_charges_enabled": shop.stripe_charges_enabled,
                "stripe_payouts_enabled": shop.stripe_payouts_enabled,
                "stripe_details_submitted": shop.stripe_details_submitted,
            }
        )

    @transaction.atomic
    def post(self, request, shop_id: str):
        from apps.billing.stripe_connect import create_account_onboarding_link, create_stripe_express_account, onboarding_redirect_urls

        shop = get_object_or_404(Shop, id=shop_id)
        if not _provider_can_manage_shop(request.user, shop) and not request.user.is_staff:
            raise PermissionDenied("Only the shop owner, team manager, or partner manager can connect a payout account.")

        if not shop.stripe_account_id:
            country = str(request.data.get("country") or "US").strip().upper()
            shop.stripe_account_id = create_stripe_express_account(email=shop.owner.email or "", country=country)
            shop.save(update_fields=["stripe_account_id", "updated_at"])

        refresh_url, return_url = onboarding_redirect_urls()
        onboarding_url = create_account_onboarding_link(
            account_id=shop.stripe_account_id, refresh_url=refresh_url, return_url=return_url,
        )
        return Response(
            {"onboarding_url": onboarding_url, "stripe_account_id": shop.stripe_account_id},
            status=status.HTTP_200_OK,
        )


class ShopPartnerConnectView(APIView):
    """Attaches/detaches this shop to a Partner organization. Requires the
    caller to be able to manage BOTH the shop and the target partner - not
    just one - so a low-level shop team member can't hijack the shop into
    an unrelated partner they happen to manage, and a partner manager can't
    attach a shop they have no rights over."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, shop_id: str):
        from apps.partners.models import Partner
        from apps.partners.services import partner_user_can_manage

        shop = get_object_or_404(Shop, id=shop_id)
        if not _provider_can_manage_shop(request.user, shop) and not request.user.is_staff:
            raise PermissionDenied("Only the shop owner, team manager, or partner manager can connect a partner.")

        partner_id = str(request.data.get("partner_id") or "").strip()
        if not partner_id:
            raise ValidationError({"partner_id": "This field is required."})
        partner = get_object_or_404(Partner, id=partner_id)
        if not partner_user_can_manage(partner, request.user):
            raise PermissionDenied("You do not have permission to attach shops to this partner.")

        shop.partner = partner
        shop.save(update_fields=["partner"])
        return Response(ShopSerializer(shop, context={"request": request}).data, status=status.HTTP_200_OK)

    def delete(self, request, shop_id: str):
        shop = get_object_or_404(Shop, id=shop_id)
        if not _provider_can_manage_shop(request.user, shop) and not request.user.is_staff:
            raise PermissionDenied("Only the shop owner, team manager, or partner manager can disconnect a partner.")

        shop.partner = None
        shop.save(update_fields=["partner"])
        return Response(ShopSerializer(shop, context={"request": request}).data, status=status.HTTP_200_OK)


class CommerceDiscoveryView(APIView):
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get(self, request):
        query = str(request.query_params.get('q') or '').strip()
        product_qs = Product.objects.select_related('shop').filter(is_active=True, is_deleted=False)
        service_qs = ShopService.objects.select_related('shop').filter(is_active=True, is_deleted=False, status='published')
        shop_qs = Shop.objects.filter(is_deleted=False, status=Shop.STATUS_ACTIVE)
        if query:
            product_qs = product_qs.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(shop__name__icontains=query))
            service_qs = service_qs.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(shop__name__icontains=query))
            shop_qs = shop_qs.filter(Q(name__icontains=query) | Q(description__icontains=query))
        trending_qs = product_qs.filter(is_featured=True).order_by('-created_at')[:12]
        if trending_qs.count() < 6:
            trending_qs = product_qs.order_by('-created_at')[:12]
        popular_shops_qs = shop_qs.filter(is_verified=True).order_by('-followers_count', '-rating_avg')[:8]
        if popular_shops_qs.count() < 3:
            popular_shops_qs = shop_qs.order_by('-followers_count', '-rating_avg')[:8]
        service_qs = service_qs.order_by('-is_featured', '-created_at')[:8]
        trending_data = ProductSerializer(trending_qs, many=True, context={'request': request}).data
        shops_data = ShopSerializer(popular_shops_qs, many=True, context={'request': request}).data
        return Response({
            'currency': 'USD',
            'payment_provider': _commerce_default_payment_provider(),
            'legacy_wallet_checkout_enabled': _commerce_wallet_checkout_enabled(),
            'trending_products': trending_data,
            'popular_shops': shops_data,
            'drops': [],
            'featured_drop': None,
            'sections': {
                'featured_products': trending_data,
                'trusted_shops': shops_data,
                'service_spotlight': ShopServiceSerializer(service_qs, many=True, context={'request': request}).data,
            },
        })


@class_doc_decorator('Shop Verification Requests')
class ShopVerificationRequestViewSet(viewsets.ModelViewSet):
    queryset = ShopVerificationRequest.objects.all().order_by('-created_at')
    serializer_class = ShopVerificationRequestSerializer
    permission_classes = [permissions.IsAdminUser]

    @doc_decorator(
        summary="Review shop verification request",
        description="Approve or reject a shop verification request. This endpoint is intended for manual reviewers.",
        request=None,
        responses={200: OpenApiResponse(description="Reviewed") if SPECTACULAR else "Reviewed"}
    )
    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        if not request.user.is_staff:
            raise PermissionDenied("Only staff reviewers can review shop verification requests.")
        req = self.get_object()
        action_name = request.data.get('action')
        notes = request.data.get('notes', '')
        if action_name == 'approve':
            req.status = 'APPROVED'
            req.shop.is_verified = True
            req.shop.verification_status = 'VERIFIED'
            req.shop.trust_badges = sorted(set((req.shop.trust_badges or []) + ['kyc', 'verified-shop']))
            req.shop.save(update_fields=['is_verified', 'verification_status', 'trust_badges', 'updated_at'])
        elif action_name == 'reject':
            req.status = 'REJECTED'
            if not req.shop.is_verified:
                req.shop.verification_status = 'REJECTED'
                req.shop.save(update_fields=['verification_status', 'updated_at'])
        else:
            raise ValidationError({'action': 'Use approve or reject.'})
        req.reviewer_notes = notes
        req.processed_at = timezone.now()
        req.save()
        case = sync_shop_verification_request(verification_request=req, actor=request.user, notes=notes)
        return Response({'status': 'reviewed', 'new_status': req.status, 'verification_case_id': str(case.id) if case else None})


@class_doc_decorator('Products')
class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().order_by('-created_at')
    serializer_class = ProductSerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related("shop", "shop__owner").prefetch_related(
            "catalog_categories",
            "gallery_images",
        )
        shop_id = self.request.query_params.get("shop")
        if shop_id:
            qs = qs.filter(shop_id=shop_id)
        owner_id = self.request.query_params.get("owner")
        if owner_id:
            qs = qs.filter(shop__owner_id=owner_id)
        query = str(self.request.query_params.get("q") or self.request.query_params.get("search") or "").strip()
        if query:
            qs = qs.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(sku__icontains=query)
                | Q(shop__name__icontains=query)
                | Q(catalog_categories__name__icontains=query)
            ).distinct()
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(Q(catalog_categories__id=category) | Q(catalog_categories__slug=category)).distinct()
        min_price = _decimal_from_value(self.request.query_params.get("min_price"))
        max_price_raw = self.request.query_params.get("max_price")
        if min_price > 0:
            qs = qs.filter(Q(sale_price__gte=min_price) | Q(sale_price__isnull=True, price__gte=min_price))
        if max_price_raw not in (None, ""):
            max_price = _decimal_from_value(max_price_raw)
            qs = qs.filter(Q(sale_price__lte=max_price) | Q(sale_price__isnull=True, price__lte=max_price))
        if self.request.query_params.get("verified_seller") in {"1", "true", "yes"}:
            qs = qs.filter(shop__is_verified=True)
        return qs

    def get_object(self):
        obj = super().get_object()
        if self.request.method not in permissions.SAFE_METHODS:
            if not _provider_can_manage_shop(self.request.user, obj.shop) and not self.request.user.is_staff:
                raise PermissionDenied("Only product owners, team managers, partner managers, or staff can modify listings.")
        return obj

    def perform_create(self, serializer):
        shop = serializer.validated_data.get("shop")
        if not shop or not _provider_can_manage_shop(self.request.user, shop):
            raise PermissionDenied("You can only add products to a shop you manage.")

        product_limit_raw = get_feature_limit(self.request.user, "products_per_shop_limit", 0)
        product_limit = _normalize_limit(product_limit_raw)
        if product_limit == 0:
            raise PermissionDenied("Marketplace listings require a Business+ tier.")

        existing = Product.objects.filter(shop=shop, is_deleted=False).count()
        if product_limit is not None and existing >= product_limit:
            raise ValidationError({"detail": f"Your tier allows up to {product_limit} products per shop."})

        main_image = serializer.validated_data.get("main_image")
        if main_image:
            validate_upload_file_safety(main_image, context="commerce")
        for upload in self.request.FILES.getlist("images"):
            validate_upload_file_safety(upload, context="commerce")

        product = serializer.save()
        self._attach_pending_media(product)

    def perform_update(self, serializer):
        main_image = serializer.validated_data.get("main_image")
        if main_image:
            validate_upload_file_safety(main_image, context="commerce")
        for upload in self.request.FILES.getlist("images"):
            validate_upload_file_safety(upload, context="commerce")
        super().perform_update(serializer)
        self._attach_pending_media(serializer.instance)

    def _attach_pending_media(self, product):
        # Presigned-upload counterpart to the legacy `main_image`/`images`
        # multipart fields above: lets a client finish uploading photos via
        # initiate -> S3 PUT -> confirm BEFORE the product exists, then
        # create (or update) the product referencing the resulting
        # mediaIds in the same JSON request — no fake product id required.
        main_media_id = self.request.data.get("main_image_media_id")
        if main_media_id:
            media_uploads.attach_product_main_image(user=self.request.user, product_id=product.id, media_id=main_media_id)
        gallery_media_ids = self.request.data.get("gallery_media_ids") or []
        if isinstance(gallery_media_ids, str):
            gallery_media_ids = [gallery_media_ids]
        for media_id in gallery_media_ids:
            media_uploads.attach_product_gallery_image(user=self.request.user, product_id=product.id, media_id=media_id)

    @doc_decorator(
        summary="Attach a confirmed product main image",
        request=None,
        responses={200: OpenApiResponse(description="Attached") if SPECTACULAR else "Attached"},
    )
    @action(detail=True, methods=['post'], url_path='main-image/attach')
    def main_image_attach(self, request, pk=None):
        product = self.get_object()
        media_id = request.data.get('mediaId') or request.data.get('media_id')
        updated = media_uploads.attach_product_main_image(user=request.user, product_id=product.id, media_id=media_id)
        return Response(self.get_serializer(updated).data)

    @doc_decorator(
        summary="Attach a confirmed product gallery image",
        request=None,
        responses={201: OpenApiResponse(description="Attached") if SPECTACULAR else "Attached"},
    )
    @action(detail=True, methods=['post'], url_path='gallery/attach')
    def gallery_attach(self, request, pk=None):
        product = self.get_object()
        media_id = request.data.get('mediaId') or request.data.get('media_id')
        image = media_uploads.attach_product_gallery_image(user=request.user, product_id=product.id, media_id=media_id)
        return Response(ProductImageSerializer(image, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)

    @doc_decorator(
        summary="Remove a product gallery image",
        request=None,
        responses={204: OpenApiResponse(description="Removed") if SPECTACULAR else "Removed"},
    )
    # UUID-only pattern is deliberate, not cosmetic: without it this regex
    # (originally `[^/.]+`) also matches the literal "reorder"/"attach"
    # segments of the sibling gallery_reorder/gallery_attach actions below,
    # and — depending on router registration order — can intercept those
    # POST requests before they ever reach the intended action, returning a
    # confusing 405 instead of running the reorder/attach.
    @action(detail=True, methods=['delete'], url_path=r'gallery/(?P<image_id>[0-9a-fA-F-]{36})')
    def gallery_remove(self, request, pk=None, image_id=None):
        product = self.get_object()
        media_uploads.remove_product_gallery_image(user=request.user, product_id=product.id, image_id=image_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @doc_decorator(
        summary="Reorder product gallery images",
        request=None,
        responses={200: OpenApiResponse(description="Reordered") if SPECTACULAR else "Reordered"},
    )
    @action(detail=True, methods=['post'], url_path='gallery/reorder')
    def gallery_reorder(self, request, pk=None):
        product = self.get_object()
        order = request.data.get('order') or []
        images = media_uploads.reorder_product_gallery(user=request.user, product_id=product.id, ordered_image_ids=order)
        return Response(ProductImageSerializer(images, many=True, context=self.get_serializer_context()).data)

    def perform_destroy(self, instance):
        shop = getattr(instance, 'shop', None)
        if shop and not _provider_can_manage_shop(self.request.user, shop) and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only the shop owner, team manager, or partner manager can delete this product.")
        instance.is_deleted = True
        instance.is_active = False
        instance.save(update_fields=["is_deleted", "is_active"])

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

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

    @action(detail=True, methods=['post', 'delete'], url_path='broadcast')
    def broadcast(self, request, pk=None):
        product = self.get_object()
        if not _provider_can_manage_shop(request.user, product.shop):
            raise PermissionDenied("Only the shop owner, team manager, or partner manager can broadcast this product.")

        from apps.broadcasts.models import BroadcastItem, BroadcastSourceType
        if request.method == 'DELETE':
            updated = BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.MARKET_PRODUCT,
                source_id=str(product.id),
                is_deleted=False,
            ).update(is_deleted=True)
            if updated:
                return Response({"detail": "Product broadcast removed."}, status=200)
            raise NotFound("Product is not currently broadcasted.")
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
    serializer_class = ProductAuthenticityCheckSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = ProductAuthenticityCheck.objects.all().order_by('-created_at')
        if self.request.user.is_staff:
            return qs
        return qs.filter(requested_by=self.request.user)


@class_doc_decorator('Orders')
class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Order.objects.select_related('shop', 'user').order_by('-created_at')
        if self.request.user.is_staff:
            return qs
        return qs.filter(user=self.request.user)

    def get_object(self):
        obj = super().get_object()
        if obj.user_id != self.request.user.id and not self.request.user.is_staff:
            raise PermissionDenied("You do not have access to this order.")
        return obj

    @doc_decorator(
        summary="Initiate payment for an order",
        description="Creates a Flutterwave DirectPaymentIntent and returns the checkout URL.",
        request=None,
        responses={200: OpenApiResponse(description="Payment initiated") if SPECTACULAR else "Payment initiated"},
    )
    @action(detail=True, methods=['post'])
    def pay(self, request, pk=None):
        order = self.get_object()

        if order.user_id != request.user.id:
            raise PermissionDenied("You can only pay for your own orders.")

        if order.status != OrderStatus.TEMPORAL:
            return Response(
                {'detail': f'Order is in {order.status} state and cannot be paid again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Idempotency: return existing pending intent if present
        meta = order.metadata or {}
        if meta.get('direct_payment_intent_id') and meta.get('payment_url'):
            return Response({
                'status': 'pending',
                'payment_url': meta['payment_url'],
                'tx_ref': meta.get('payment_reference', ''),
                'direct_payment_intent_id': meta['direct_payment_intent_id'],
            })

        amount_cents = int(order.total_cents or 0)
        if amount_cents <= 0:
            return Response({'detail': 'Order has no payable amount.'}, status=status.HTTP_400_BAD_REQUEST)

        provider = str(
            request.data.get('provider')
            or getattr(settings, 'KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER', 'flutterwave')
        ).strip().lower()

        try:
            intent = create_direct_payment_intent(
                user=request.user,
                target_type='order',
                target_id=order.id,
                provider=provider,
                idempotency_key=str(order.id),
                metadata={'source': 'order', 'shop_id': str(order.shop_id)},
            )
        except Exception as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Persist intent reference for webhook reconciliation
        order.metadata = {
            **meta,
            'payment_reference': intent.tx_ref,
            'direct_payment_intent_id': str(intent.id),
            'payment_url': intent.payment_url,
            'payment_status': 'pending',
            'payment_provider': intent.provider,
        }
        order.save(update_fields=['metadata', 'updated_at'])

        # Create audit Payment record in pending state
        amount_decimal = Decimal(amount_cents) / Decimal('100')
        Payment.objects.create(
            order=order,
            provider=intent.provider or provider,
            method='provider_checkout',
            amount=amount_decimal,
            currency=order.currency,
            status='pending',
            role='buyer',
            provider_ref=intent.tx_ref,
            notes=f'DirectPaymentIntent {intent.id}',
        )

        evaluate_fraud_score.delay(str(order.id))

        return Response({
            'status': 'pending',
            'payment_url': intent.payment_url,
            'tx_ref': intent.tx_ref,
            'direct_payment_intent_id': str(intent.id),
        })


@class_doc_decorator('Payments')
class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only — payments are created automatically via order.pay."""
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Payment.objects.select_related('order').order_by('-created_at')
        if self.request.user.is_staff:
            return qs
        return qs.filter(order__user=self.request.user)


@class_doc_decorator('Promotions')
class PromotionViewSet(viewsets.ModelViewSet):
    serializer_class = PromotionSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        return Promotion.objects.filter(is_deleted=False).order_by('-created_at')

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied("Only staff can create promotions.")
        serializer.save()

    def perform_update(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied("Only staff can update promotions.")
        serializer.save()

    def perform_destroy(self, instance):
        if not self.request.user.is_staff:
            raise PermissionDenied("Only staff can delete promotions.")
        instance.delete()


@class_doc_decorator('Subscriptions')
class SubscriptionViewSet(viewsets.ModelViewSet):
    serializer_class = SubscriptionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Subscription.objects.order_by('-created_at')
        if self.request.user.is_staff:
            return qs
        return qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


POINT_EARNING_RULES = [
    {
        'key': 'profile_complete',
        'title': 'Complete your profile',
        'description': 'Fill in all profile fields and add a profile photo',
        'points': 50,
        'category': 'profile',
        'icon': 'person',
        'repeatable': False,
    },
    {
        'key': 'email_verified',
        'title': 'Verify your email',
        'description': 'Confirm your email address to secure your account',
        'points': 25,
        'category': 'profile',
        'icon': 'mail',
        'repeatable': False,
    },
    {
        'key': 'daily_login',
        'title': 'Daily app engagement',
        'description': 'Open and actively use the app each day',
        'points': 5,
        'category': 'engagement',
        'icon': 'calendar',
        'repeatable': True,
    },
    {
        'key': 'first_purchase',
        'title': 'First marketplace purchase',
        'description': 'Complete your first order in the KIS marketplace',
        'points': 100,
        'category': 'market',
        'icon': 'cart',
        'repeatable': False,
    },
    {
        'key': 'first_booking',
        'title': 'Book a service or artist',
        'description': 'Book a service or artist through KIS for the first time',
        'points': 75,
        'category': 'market',
        'icon': 'bookmark',
        'repeatable': False,
    },
    {
        'key': 'leave_review',
        'title': 'Leave a review',
        'description': 'Write a genuine review for a product or service you purchased',
        'points': 30,
        'category': 'community',
        'icon': 'star',
        'repeatable': True,
    },
    {
        'key': 'invite_friend',
        'title': 'Invite a friend',
        'description': 'Refer a friend who creates an account and uses KIS',
        'points': 200,
        'category': 'community',
        'icon': 'people',
        'repeatable': True,
    },
    {
        'key': 'education_course',
        'title': 'Complete an education course',
        'description': 'Finish any education course, lesson, or certification program',
        'points': 150,
        'category': 'education',
        'icon': 'school',
        'repeatable': True,
    },
    {
        'key': 'broadcast_post',
        'title': 'Share quality content',
        'description': 'Post meaningful content to your broadcast feed that your community engages with',
        'points': 20,
        'category': 'broadcast',
        'icon': 'megaphone',
        'repeatable': True,
    },
    {
        'key': 'partner_active',
        'title': 'Active partner organisation',
        'description': 'Keep your partner organisation active, verified, and in good standing',
        'points': 100,
        'category': 'partner',
        'icon': 'business',
        'repeatable': True,
    },
    {
        'key': 'health_activity',
        'title': 'Log a health activity',
        'description': 'Record a health check, appointment, or wellness activity',
        'points': 15,
        'category': 'health',
        'icon': 'heart',
        'repeatable': True,
    },
]

POINT_REDEMPTION_OPTIONS = [
    {
        'key': 'tier_upgrade',
        'title': 'Upgrade your account tier',
        'description': 'Use points to reduce the cost of upgrading to a higher KIS account tier. Every 100 points = 1% off, up to 50% discount.',
        'points_per_unit': 100,
        'discount_value': '1% off tier upgrade price',
        'max_discount_percent': 50,
        'icon': 'arrow-up-circle',
    },
    {
        'key': 'artist_discount',
        'title': 'Artist & creator discount',
        'description': 'Redeem points to reduce the cost of booking artists, musicians, or creators through the KIS marketplace. Every 50 points = 1% off, up to 30% discount.',
        'points_per_unit': 50,
        'discount_value': '1% off artist booking price',
        'max_discount_percent': 30,
        'icon': 'musical-notes',
    },
]


@class_doc_decorator('Loyalty')
class LoyaltyPointViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LoyaltyPointSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = LoyaltyPoint.objects.order_by('-created_at')
        if self.request.user.is_staff:
            return qs
        return qs.filter(user=self.request.user)

    @action(detail=False, methods=['get'])
    def balance(self, request):
        from django.db.models import Sum
        total = LoyaltyPoint.objects.filter(user=request.user).aggregate(
            total=Sum('points')
        )['total'] or 0
        tier_name = None
        try:
            tier_obj = getattr(request.user, 'account', None)
            if tier_obj:
                tier_name = getattr(getattr(tier_obj, 'tier', None), 'name', None)
        except Exception:
            pass
        recent = list(
            LoyaltyPoint.objects.filter(user=request.user)
            .order_by('-created_at')
            .values('id', 'points', 'reason', 'earned_at', 'expires_at')[:10]
        )
        return Response({
            'points': int(total),
            'tier': tier_name,
            'earning_rules': POINT_EARNING_RULES,
            'redemption_options': POINT_REDEMPTION_OPTIONS,
            'recent_history': recent,
        })

    @action(detail=False, methods=['get'])
    def rules(self, request):
        return Response({
            'earning_rules': POINT_EARNING_RULES,
            'redemption_options': POINT_REDEMPTION_OPTIONS,
        })

    @action(detail=False, methods=['post'])
    def redeem(self, request):
        """Spend KIS Coins (loyalty points) from the authenticated user's balance."""
        raw_points = request.data.get('points')
        try:
            points = int(raw_points)
        except (TypeError, ValueError):
            return Response({'detail': 'Provide a valid integer number of points to redeem.'}, status=400)
        if points <= 0:
            return Response({'detail': 'Points must be a positive integer.'}, status=400)
        from django.db.models import Sum
        total = LoyaltyPoint.objects.filter(user=request.user).aggregate(total=Sum('points'))['total'] or 0
        if points > total:
            return Response({'detail': f'You only have {int(total)} coins available.'}, status=400)
        # Record a negative adjustment
        LoyaltyPoint.objects.create(user=request.user, points=-points, reason='User redemption')
        new_total = total - points
        return Response({'detail': 'Coins redeemed.', 'points_spent': points, 'balance': int(new_total)})


@class_doc_decorator('Follows')
class ShopFollowViewSet(viewsets.ModelViewSet):
    serializer_class = ShopFollowSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ShopFollow.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@class_doc_decorator('Shares')
class ProductShareViewSet(viewsets.ModelViewSet):
    serializer_class = ProductShareSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ProductShare.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@class_doc_decorator('Recommendations')
class AIRecommendationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AIRecommendationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = AIRecommendation.objects.order_by('-created_at')
        if self.request.user.is_staff:
            return qs
        return qs.filter(user_id=self.request.user.id)

    @doc_decorator(
        summary="Compute recommendations",
        description="Enqueue personalised recommendation computation for the current user.",
        request=None,
        responses={200: OpenApiResponse(description="Enqueued") if SPECTACULAR else "Enqueued"},
    )
    @action(detail=False, methods=['post'])
    def compute(self, request):
        compute_recommendations.delay(str(request.user.id))
        return Response({'status': 'enqueued'})


@class_doc_decorator('Audit Logs')
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.all().order_by('-created_at')
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAdminUser]


@class_doc_decorator('Fraud Signals')
class FraudSignalViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FraudSignal.objects.all().order_by('-created_at')
    serializer_class = FraudSignalSerializer
    permission_classes = [permissions.IsAdminUser]


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
            cart = Cart.objects.filter(user=request.user, shop_id=shop_id).order_by('-updated_at').first()
            if not cart:
                raise NotFound('No cart found for this shop.')
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
        if not product.is_active or product.is_deleted:
            raise ValidationError({'product': 'Product is unavailable.'})
        return product

    def _validate_quantity(self, product: Product, quantity: int):
        requested = max(1, int(quantity or 1))
        attrs = product.attributes if isinstance(product.attributes, dict) else {}
        if int(product.stock_qty or 0) < requested and not bool(attrs.get('allow_backorder')):
            raise ValidationError({'quantity': 'Requested quantity is not available.'})
        return requested

    def perform_create(self, serializer):
        cart = serializer.validated_data.get('cart')
        product = serializer.validated_data.get('product')
        if not cart or not product:
            raise ValidationError({'detail': 'Cart and product are required.'})
        self._validate_cart(cart)
        self._validate_product(cart, product)
        serializer.validated_data['quantity'] = self._validate_quantity(product, serializer.validated_data.get('quantity'))
        serializer.validated_data.setdefault('price_snapshot', product.sale_price or product.price)
        serializer.validated_data.setdefault('stock_snapshot', product.stock_qty or 0)
        item = serializer.save()
        _recalculate_cart_subtotal(item.cart)

    def perform_update(self, serializer):
        cart = serializer.validated_data.get('cart') or serializer.instance.cart
        if cart:
            self._validate_cart(cart)
        product = serializer.validated_data.get('product') or serializer.instance.product
        if cart and product:
            self._validate_product(cart, product)
            serializer.validated_data['quantity'] = self._validate_quantity(
                product,
                serializer.validated_data.get('quantity', serializer.instance.quantity),
            )
        item = serializer.save()
        _recalculate_cart_subtotal(item.cart)

    def perform_destroy(self, instance):
        cart = instance.cart
        super().perform_destroy(instance)
        _recalculate_cart_subtotal(cart)


@class_doc_decorator('Product Ratings')
class ProductRatingViewSet(viewsets.ModelViewSet):
    queryset = ProductRating.objects.all().order_by('-created_at')
    serializer_class = ProductRatingSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@class_doc_decorator('Product Reviews')
class ProductReviewViewSet(viewsets.ModelViewSet):
    queryset = ProductReview.objects.select_related('product', 'user').filter(is_deleted=False).order_by('-created_at')
    serializer_class = ProductReviewSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        product_id = self.request.query_params.get('product')
        if product_id:
            qs = qs.filter(product_id=product_id)
        user = self.request.user
        if not getattr(user, 'is_staff', False):
            qs = qs.filter(status=ProductReview.STATUS_PUBLISHED)
        return qs

    def perform_create(self, serializer):
        product = serializer.validated_data.get('product')
        if not product or not getattr(product, 'is_active', False):
            raise ValidationError({'product': 'Product is unavailable.'})
        serializer.save(
            user=self.request.user,
            status=ProductReview.STATUS_PUBLISHED,
            metadata={'source': 'commerce_product_detail'},
        )

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def helpful(self, request, pk=None):
        review = self.get_object()
        review.helpful_count = (review.helpful_count or 0) + 1
        review.save(update_fields=['helpful_count', 'updated_at'])
        return Response({'helpful_count': review.helpful_count})


@class_doc_decorator('Product Questions')
class ProductQuestionViewSet(viewsets.ModelViewSet):
    queryset = ProductQuestion.objects.select_related('product', 'product__shop', 'user', 'answered_by').filter(is_deleted=False).order_by('-created_at')
    serializer_class = ProductQuestionSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        product_id = self.request.query_params.get('product')
        if product_id:
            qs = qs.filter(product_id=product_id)
        user = self.request.user
        if not getattr(user, 'is_staff', False):
            qs = qs.exclude(status=ProductQuestion.STATUS_HIDDEN)
        return qs

    def perform_create(self, serializer):
        product = serializer.validated_data.get('product')
        if not product or not getattr(product, 'is_active', False):
            raise ValidationError({'product': 'Product is unavailable.'})
        serializer.save(user=self.request.user, metadata={'source': 'commerce_product_detail'})

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def answer(self, request, pk=None):
        question = self.get_object()
        if not _provider_can_manage_shop(request.user, question.product.shop) and not request.user.is_staff:
            raise PermissionDenied('Only the seller, managers, or staff can answer this question.')
        answer = str(request.data.get('answer') or '').strip()
        if len(answer) < 2:
            raise ValidationError({'answer': 'Provide a clear answer.'})
        question.answer = answer
        question.answered_by = request.user
        question.answered_at = timezone.now()
        question.status = ProductQuestion.STATUS_ANSWERED
        question.save(update_fields=['answer', 'answered_by', 'answered_at', 'status', 'updated_at'])
        return Response(self.get_serializer(question).data)


@class_doc_decorator('Product Categories')
class ProductCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CatalogCategory.objects.select_related('parent').all().order_by('category_type', 'sort_order', 'name')
    serializer_class = CatalogCategorySerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    http_method_names = ['get', 'head']

    def get_queryset(self):
        ensure_catalog_categories()
        qs = super().get_queryset()
        category_type = self.request.query_params.get('category_type')
        if category_type in {'product', 'service'}:
            qs = qs.filter(category_type=category_type)
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
        if not self.request.user.is_staff:
            seat_limit = normalize_limit_value(get_feature_limit(self.request.user, "team_seats", None), default=0)
            if seat_limit is not None and seat_limit <= 0:
                raise PermissionDenied("Team collaboration is available on Business Pro plans and above. Upgrade to add team members.")
            if seat_limit is not None:
                used = ShopTeamMember.objects.filter(shop__owner=self.request.user, is_active=True).count()
                if used >= seat_limit:
                    raise PermissionDenied(
                        f"Your plan allows up to {seat_limit} team seat{'s' if seat_limit != 1 else ''}. Upgrade to add more members."
                    )
        serializer.save()

    # perform_create above already gates who can add a member, but nothing
    # gated PATCH/DELETE on an existing row — get_queryset()'s shop=
    # filter only applies to list(), not to the detail lookup used by
    # retrieve/update/destroy, so any authenticated user who knew (or
    # enumerated) a ShopTeamMember id could edit its role or remove it
    # regardless of which shop it belonged to. Mirrors ShopServiceViewSet's
    # identical get_object() guard just above.
    def get_object(self):
        obj = super().get_object()
        if self.request.method not in permissions.SAFE_METHODS:
            if not _provider_can_manage_shop(self.request.user, obj.shop) and not self.request.user.is_staff:
                raise PermissionDenied("Only shop owners, team managers, partner managers, or staff can modify team members.")
        return obj

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
        if not shop or (not _provider_can_manage_shop(self.request.user, shop) and not self.request.user.is_staff):
            raise PermissionDenied("Only shop owners, team managers, partner managers, or staff can create services.")
        image_file = serializer.validated_data.get("image_file")
        if image_file:
            validate_upload_file_safety(image_file, context="commerce")
        for upload in self.request.FILES.getlist("images"):
            validate_upload_file_safety(upload, context="commerce")
        service = serializer.save()
        self._attach_pending_media(service)

    def get_object(self):
        obj = super().get_object()
        if self.request.method not in permissions.SAFE_METHODS:
            if not _provider_can_manage_shop(self.request.user, obj.shop) and not self.request.user.is_staff:
                raise PermissionDenied("Only shop owners, team managers, partner managers, or staff can modify services.")
        return obj

    def perform_update(self, serializer):
        image_file = serializer.validated_data.get("image_file")
        if image_file:
            validate_upload_file_safety(image_file, context="commerce")
        for upload in self.request.FILES.getlist("images"):
            validate_upload_file_safety(upload, context="commerce")
        super().perform_update(serializer)
        self._attach_pending_media(serializer.instance)

    def _attach_pending_media(self, service):
        media_id = self.request.data.get("image_media_id")
        if media_id:
            media_uploads.attach_service_image(user=self.request.user, service_id=service.id, media_id=media_id)
        gallery_media_ids = self.request.data.get("gallery_media_ids") or []
        if isinstance(gallery_media_ids, str):
            gallery_media_ids = [gallery_media_ids]
        for gallery_media_id in gallery_media_ids:
            media_uploads.attach_service_gallery_image(user=self.request.user, service_id=service.id, media_id=gallery_media_id)

    @doc_decorator(
        summary="Attach a confirmed service image",
        request=None,
        responses={200: OpenApiResponse(description="Attached") if SPECTACULAR else "Attached"},
    )
    @action(detail=True, methods=['post'], url_path='image/attach')
    def image_attach(self, request, pk=None):
        service = self.get_object()
        media_id = request.data.get('mediaId') or request.data.get('media_id')
        updated = media_uploads.attach_service_image(user=request.user, service_id=service.id, media_id=media_id)
        return Response(self.get_serializer(updated).data)

    @doc_decorator(
        summary="Attach a confirmed service gallery image",
        request=None,
        responses={201: OpenApiResponse(description="Attached") if SPECTACULAR else "Attached"},
    )
    @action(detail=True, methods=['post'], url_path='gallery/attach')
    def gallery_attach(self, request, pk=None):
        service = self.get_object()
        media_id = request.data.get('mediaId') or request.data.get('media_id')
        image = media_uploads.attach_service_gallery_image(user=request.user, service_id=service.id, media_id=media_id)
        return Response(ServiceImageSerializer(image, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)

    @doc_decorator(
        summary="Remove a service gallery image",
        request=None,
        responses={204: OpenApiResponse(description="Removed") if SPECTACULAR else "Removed"},
    )
    @action(detail=True, methods=['delete'], url_path=r'gallery/(?P<image_id>[0-9a-fA-F-]{36})')
    def gallery_remove(self, request, pk=None, image_id=None):
        service = self.get_object()
        media_uploads.remove_service_gallery_image(user=request.user, service_id=service.id, image_id=image_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post', 'delete'], url_path='broadcast')
    def broadcast(self, request, pk=None):
        service = self.get_object()
        if not _provider_can_manage_shop(request.user, service.shop):
            raise PermissionDenied("Only the shop owner, team manager, or partner manager can broadcast this service.")

        from apps.broadcasts.models import BroadcastItem, BroadcastSourceType
        if request.method == 'DELETE':
            updated = BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.MARKET_SERVICE,
                source_id=str(service.id),
                is_deleted=False,
            ).update(is_deleted=True)
            if updated:
                return Response({"detail": "Service broadcast removed."}, status=200)
            raise NotFound("Service is not currently broadcasted.")
        from datetime import timedelta

        item, _ = BroadcastItem.objects.update_or_create(
            source_type=BroadcastSourceType.MARKET_SERVICE,
            source_id=str(service.id),
            defaults={
                "broadcasted_by": request.user,
                "broadcasted_at": timezone.now(),
                "expires_at": timezone.now() + timedelta(days=10),
                "is_deleted": False,
            },
        )
        return Response({"detail": "Service broadcasted.", "broadcast_id": str(item.id)}, status=200)


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
        return qs.filter(
            Q(user=self.request.user)
            | Q(shop__owner=self.request.user)
            | Q(
                shop__team_members__user=self.request.user,
                shop__team_members__role__in={ShopRole.MANAGER, ShopRole.ADMIN},
                shop__team_members__is_active=True,
            )
        ).distinct()

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

        # A dropped connection or the client's own automatic retry-on-timeout
        # must not create two separate bookings for the same slot request.
        # Without this, a retry after an apparent timeout hits the slot-
        # capacity check below against the caller's OWN just-created
        # booking and gets "409 slot already booked" — the booking actually
        # succeeded, but the user is told it failed. Mirrors
        # MarketplaceOrderViewSet.create's identical idempotency pattern.
        idempotency_key = str(
            request.headers.get("X-Idempotency-Key")
            or request.data.get("idempotency_key")
            or ""
        ).strip()
        if idempotency_key:
            existing_booking = (
                ServiceBooking.objects.filter(
                    user=request.user,
                    service_id=service_id,
                    metadata__idempotency_key=idempotency_key,
                )
                .order_by("-created_at")
                .first()
            )
            if existing_booking:
                output = ServiceBookingSerializer(existing_booking, context=self.get_serializer_context())
                return Response(output.data, status=status.HTTP_200_OK)

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
        package_pricing_enabled = _service_flag("SERVICE_ENABLE_PACKAGE_PRICING", False)
        addons_enabled = _service_flag("SERVICE_ENABLE_ADDONS", False)
        quotes_enabled = _service_flag("SERVICE_ENABLE_QUOTES", False)
        negotiation_enabled = _service_flag("SERVICE_ENABLE_NEGOTIATION", False)
        package_price = package_option["price"] if package_pricing_enabled and package_option else Decimal("0")
        addon_price = sum(item["price"] for item in addon_options) if addons_enabled and addon_options else Decimal("0")
        if package_pricing_enabled and package_option:
            logger.info("SERVICE_ENABLE_PACKAGE_PRICING applied for service %s package=%s", service.id, package_option["name"])
        if addons_enabled and addon_options:
            logger.info(
                "SERVICE_ENABLE_ADDONS applied for service %s addons=%s",
                service.id,
                [item["name"] for item in addon_options],
            )
        quote_flow = quotes_enabled and bool(getattr(service, "quote_required", False))
        negotiation_flow = False
        requested_price_value = None
        if negotiation_enabled and getattr(service, "negotiable", False) and requested_price is not None:
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
            package_option if package_pricing_enabled else None,
            addon_options if addons_enabled else [],
            quote_flow,
            negotiation_flow,
            requested_price_value,
            requirements_ack,
            terms_accepted,
            location=serializer.validated_data.get("location"),
            distance_km=serializer.validated_data.get("distance_km"),
            is_remote=serializer.validated_data.get("is_remote"),
            remote_region=serializer.validated_data.get("remote_region"),
            participant_count=serializer.validated_data.get("participant_count"),
            staff_on_site=serializer.validated_data.get("staff_on_site"),
        )

        tx_ref = str(uuid.uuid4())
        wallet_locked = False
        requested_payment_method = (
            serializer.validated_data.get("payment_method")
            or request.data.get("payment_method")
            or request.data.get("paymentMethod")
            or _commerce_default_payment_provider()
        )
        requested_payment_method = str(requested_payment_method or _commerce_default_payment_provider()).strip().lower()
        if _is_legacy_wallet_method(requested_payment_method) and not _commerce_wallet_checkout_enabled():
            return Response(
                {
                    "detail": "Commerce wallet/KIS Coin checkout is disabled. Use USD checkout with Flutterwave or another configured payment provider.",
                    "code": "legacy_commerce_wallet_checkout_disabled",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            if (
                not skip_payment
                and deposit_cents > 0
                and _is_legacy_wallet_method(requested_payment_method)
                and _commerce_wallet_checkout_enabled()
            ):
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
                    metadata={
                        **booking_metadata,
                        "currency": "USD",
                        "payment_method": requested_payment_method,
                        "payment_provider": requested_payment_method,
                        "payment_required": bool(not skip_payment and deposit_cents > 0),
                        **({"idempotency_key": idempotency_key} if idempotency_key else {}),
                    },
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
                        currency="USD",
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
                elif not skip_payment and deposit_cents > 0:
                    payment = ServiceBookingPayment.objects.create(
                        booking=booking,
                        amount_cents=deposit_cents,
                        currency="USD",
                        payment_method=requested_payment_method,
                        payment_status=ServiceBookingPayment.STATUS_PENDING,
                        transaction_reference=tx_ref,
                        notes="USD provider checkout pending.",
                    )
                    intent = create_direct_payment_intent(
                        user=request.user,
                        target_type="service_booking_payment",
                        target_id=payment.id,
                        provider=requested_payment_method,
                        metadata={"source": "service_booking_deposit", "booking_id": str(booking.id)},
                    )
                    booking.metadata = {
                        **(booking.metadata or {}),
                        "payment_reference": intent.tx_ref,
                        "direct_payment_intent_id": str(intent.id),
                        "payment_url": intent.payment_url,
                    }
                    booking.payment_tx_ref = intent.tx_ref
                    booking.save(update_fields=["payment_tx_ref", "metadata"])
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
        if not self._can_user_manage_booking(request.user, booking):
            raise PermissionDenied("Only the provider, managers, or staff can mark this booking as completed.")
        booking.status = ServiceBooking.STATUS_AWAITING_SATISFACTION
        booking.mark_provider_completed()
        booking.save(update_fields=["status", "provider_completed_at", "satisfaction_deadline"])
        escrow = getattr(booking, "escrow", None)
        if escrow:
            escrow.status = ServiceBookingEscrow.STATUS_AWAITING_SATISFACTION
            escrow.save(update_fields=["status"])
        from django.contrib.auth import get_user_model as _get_user_model
        _booking_recipient = _get_user_model().objects.filter(id=booking.user_id).first()
        _booking_user_lang = get_user_language(_booking_recipient)
        notification_services.create_notification(
            user_id=str(booking.user_id),
            type="commerce.service_booking.completed",
            title=get_notification_string(_booking_user_lang, 'booking_confirmed'),
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
        return bool(shop) and _provider_can_manage_shop(user, shop)

    def _can_user_manage_booking(self, user, booking):
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        shop = getattr(booking, "shop", None)
        return bool(shop) and _provider_can_manage_shop(user, shop)

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
        return bool(shop) and _provider_can_manage_shop(user, shop)

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
        if _service_flag("SERVICE_ENFORCE_REFUND_POLICY", False):
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
        if _service_flag("SERVICE_ENFORCE_RESCHEDULE_POLICY", False):
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
        if receipt:
            html_url, pdf_url = build_booking_receipt_urls(request, booking, receipt)
        else:
            # No ServiceBookingReceipt exists yet — payment hasn't completed
            # for this booking, so there is nothing real to link to. Do not
            # synthesize a receipt document for an unpaid booking.
            html_url, pdf_url = None, None
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
        if receipt:
            html_url, pdf_url = build_booking_receipt_urls(request, booking, receipt, force=True)
        else:
            html_url, pdf_url = None, None
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
        requested_payment_method = str(
            request.data.get("payment_method")
            or request.data.get("paymentMethod")
            or _commerce_default_payment_provider()
        ).strip().lower()
        legacy_wallet_payment = _is_legacy_wallet_method(requested_payment_method)
        if legacy_wallet_payment and not _commerce_wallet_checkout_enabled():
            return Response(
                {
                    "detail": "Commerce wallet/KIS Coin checkout is disabled. Use USD checkout with Flutterwave or another configured payment provider.",
                    "code": "legacy_commerce_wallet_checkout_disabled",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if legacy_wallet_payment and _commerce_wallet_checkout_enabled():
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
                if legacy_wallet_payment and escrow:
                    escrow.amount_cents += remaining
                    escrow.save(update_fields=["amount_cents"])
                elif legacy_wallet_payment:
                    ServiceBookingEscrow.objects.create(
                        booking=booking,
                        payer=booking.user,
                        provider=booking.shop.owner,
                        amount_cents=(booking.deposit_cents or 0) + remaining,
                        status=ServiceBookingEscrow.STATUS_PENDING,
                        payment_reference=tx_ref,
                    )
                payment = getattr(booking, "payment", None)
                direct_intent = None
                if payment:
                    payment.amount_cents += remaining
                    transaction_ref = payment.transaction_reference or ""
                    payment.transaction_reference = (
                        f"{transaction_ref},{tx_ref}" if transaction_ref else tx_ref
                    )
                    payment.currency = "USD"
                    payment.payment_method = "wallet" if legacy_wallet_payment else requested_payment_method
                    payment.payment_status = ServiceBookingPayment.STATUS_PAID if legacy_wallet_payment else ServiceBookingPayment.STATUS_PENDING
                    payment.paid_at = timezone.now() if legacy_wallet_payment else None
                    payment.notes = "" if legacy_wallet_payment else "USD provider checkout pending."
                    payment.save(update_fields=["amount_cents", "currency", "payment_method", "transaction_reference", "payment_status", "paid_at", "notes"])
                else:
                    payment = ServiceBookingPayment.objects.create(
                        booking=booking,
                        amount_cents=remaining,
                        currency="USD",
                        payment_method="wallet" if legacy_wallet_payment else requested_payment_method,
                        payment_status=ServiceBookingPayment.STATUS_PAID if legacy_wallet_payment else ServiceBookingPayment.STATUS_PENDING,
                        paid_at=timezone.now() if legacy_wallet_payment else None,
                        transaction_reference=tx_ref,
                        notes="" if legacy_wallet_payment else "USD provider checkout pending.",
                    )
                if not legacy_wallet_payment:
                    direct_intent = create_direct_payment_intent(
                        user=request.user,
                        target_type="service_booking_payment",
                        target_id=payment.id,
                        provider=requested_payment_method,
                        metadata={"source": "service_booking_remaining", "booking_id": str(booking.id)},
                    )
                    payment.transaction_reference = direct_intent.tx_ref
                    payment.save(update_fields=["transaction_reference", "updated_at"])
                if legacy_wallet_payment:
                    booking.deposit_cents = (booking.deposit_cents or 0) + remaining
                    booking.balance_cents = 0
                booking.payment_tx_ref = direct_intent.tx_ref if direct_intent else tx_ref
                booking.metadata = {
                    **(booking.metadata or {}),
                    "remaining_payment_method": requested_payment_method,
                    "remaining_payment_provider": requested_payment_method,
                    "remaining_payment_required": not legacy_wallet_payment,
                    **(
                        {
                            "payment_reference": direct_intent.tx_ref,
                            "direct_payment_intent_id": str(direct_intent.id),
                            "payment_url": direct_intent.payment_url,
                        }
                        if direct_intent
                        else {}
                    ),
                }
                booking.save(update_fields=["deposit_cents", "balance_cents", "payment_tx_ref", "metadata"])
                if legacy_wallet_payment and remaining > 0:
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
            if legacy_wallet_payment:
                refund_locked_booking_funds(
                    payer=request.user,
                    amount_cents=_wallet_amount(remaining),
                    reference=tx_ref,
                )
            raise ValidationError({"detail": "Unable to complete the remaining payment."})

        return Response(
            {"status": "remaining_paid" if legacy_wallet_payment else "payment_pending", "payment_method": requested_payment_method},
            status=status.HTTP_200_OK,
        )


@class_doc_decorator('Service Booking Complaints')
class ServiceBookingComplaintViewSet(viewsets.ModelViewSet):
    queryset = ServiceBookingComplaint.objects.all().order_by('-created_at')
    serializer_class = ServiceBookingComplaintSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_staff:
            return qs
        return qs.filter(
            Q(submitted_by=user)
            | Q(booking__user=user)
            | Q(booking__shop__owner=user)
            | Q(
                booking__shop__team_members__user=user,
                booking__shop__team_members__role__in={ShopRole.MANAGER, ShopRole.ADMIN},
                booking__shop__team_members__is_active=True,
            )
        ).distinct()


class ServiceBookingPaymentSatisfyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, payment_id):
        payment = get_object_or_404(ServiceBookingPayment, pk=payment_id)
        booking = payment.booking
        if booking.user_id != request.user.id:
            raise PermissionDenied("Only the payer can mark a payment as satisfied.")
        if str(payment.payment_status or "").lower() != ServiceBookingPayment.STATUS_PAID:
            return Response(
                {"detail": "Payment must be paid before satisfaction can be confirmed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        escrow = getattr(booking, "escrow", None)
        if escrow and escrow.status == ServiceBookingEscrow.STATUS_RELEASED:
            pass
        elif escrow:
            # Legacy wallet-lock rail: funds were locked in the payer's
            # wallet at booking time and must be explicitly released now.
            try:
                release_locked_booking_funds(
                    payer=booking.user,
                    provider=booking.shop.owner,
                    amount_cents=_wallet_amount(escrow.amount_cents),
                    reference=payment.transaction_reference,
                    meta={"booking_id": str(booking.id)},
                )
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)})
        # else: default USD/direct-payment path (no escrow ever created) —
        # settlement already happened at the Flutterwave webhook, either
        # via the shop's connected subaccount split or, if none is
        # connected, directly into KIS's own account. There is no wallet
        # lock to release here; attempting one used to raise
        # "Insufficient locked funds." for every booking on this path.
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


class MarketplaceOrderViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
):
    queryset = MarketplaceOrder.objects.all().order_by('-created_at')
    serializer_class = MarketplaceOrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def _provider_filter(self, user):
        provider_roles = [ShopRole.ADMIN, ShopRole.MANAGER]
        return (
            Q(shop__owner=user) |
            Q(
                shop__team_members__user=user,
                shop__team_members__is_active=True,
                shop__team_members__role__in=provider_roles,
            )
        )

    def get_queryset(self):
        user = self.request.user
        provider_filter = self._provider_filter(user)
        return (
            MarketplaceOrder.objects.filter(Q(buyer=user) | provider_filter)
            .order_by('-created_at')
            .select_related('buyer', 'shop')
            .prefetch_related('items__product')
            .distinct()
        )

    def get_serializer_class(self):
        if self.action == 'create':
            return MarketplaceOrderCreateSerializer
        return MarketplaceOrderSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        metadata = dict(validated.get('metadata') or {})
        metadata.setdefault('source', 'product_details')
        if validated.get('payment_method'):
            metadata['payment_method'] = validated.get('payment_method')

        # A dropped connection or automatic client retry during checkout
        # must not create two separate orders (and two separate payment
        # intents) for the same cart. Mirrors OrderViewSet.pay's existing
        # "retry returns the same pending intent" pattern, but at order
        # creation: a client-supplied key scopes the dedupe to one buyer +
        # shop, reusing the metadata JSON field rather than a migration.
        idempotency_key = str(
            request.headers.get('X-Idempotency-Key')
            or request.data.get('idempotency_key')
            or ''
        ).strip()
        if idempotency_key:
            existing = (
                MarketplaceOrder.objects.filter(
                    buyer=request.user,
                    shop_id=validated['shop_id'],
                    metadata__idempotency_key=idempotency_key,
                )
                .order_by('-created_at')
                .first()
            )
            if existing:
                output = MarketplaceOrderSerializer(existing, context={'request': request})
                return Response(output.data, status=status.HTTP_200_OK)
            metadata['idempotency_key'] = idempotency_key

        try:
            order = place_marketplace_order(
                buyer=request.user,
                shop_id=validated['shop_id'],
                items=validated['items'],
                metadata=metadata,
            )
        except ValidationError as exc:
            return Response({'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)
        output = MarketplaceOrderSerializer(order, context={'request': request})
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if order.buyer_id != request.user.id:
            raise PermissionDenied('Only the buyer may cancel this order.')
        try:
            order = cancel_marketplace_order_by_id(order_id=order.id, buyer=request.user)
        except ValidationError as exc:
            return Response({'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['post'])
    def satisfy(self, request, pk=None):
        order = self.get_object()
        if order.buyer_id != request.user.id:
            raise PermissionDenied('Only the buyer may satisfy this order.')
        try:
            order = satisfy_marketplace_order_by_id(order_id=order.id, buyer=request.user)
        except (ValidationError, ValueError) as exc:
            detail = exc.detail if hasattr(exc, 'detail') else str(exc)
            return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        order = self.get_object()
        user = request.user
        if not _provider_can_manage_shop(user, order.shop):
            raise PermissionDenied('Only the provider or managers may complete this order.')
        try:
            order = provider_complete_marketplace_order_by_id(order_id=order.id, provider=user)
        except ValidationError as exc:
            return Response({'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['get'])
    def receipt(self, request, pk=None):
        order = self.get_object()
        paid_statuses = {
            MarketplaceOrderStatus.AWAITING_SATISFACTION,
            MarketplaceOrderStatus.SATISFIED,
            MarketplaceOrderStatus.COMPLETED,
        }
        is_paid = order.status in paid_statuses or (order.metadata or {}).get('payment_status') == 'paid'
        if not is_paid:
            return Response(
                {'detail': 'A receipt is only available once payment for this order is complete.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        buffer = render_marketplace_receipt_pdf(order)
        return FileResponse(buffer, as_attachment=True, filename=f'marketplace_receipt_{order.id}.pdf')

    def destroy(self, request, *args, **kwargs):
        order = self.get_object()
        if order.buyer_id != request.user.id:
            raise PermissionDenied('Only the buyer may delete this order.')
        allowed = {MarketplaceOrderStatus.CANCELLED, MarketplaceOrderStatus.SATISFIED}
        if order.status not in allowed:
            return Response(
                {'detail': 'Only canceled or satisfied orders can be deleted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        order.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MarketplaceProviderOrderViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    serializer_class = MarketplaceOrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def _provider_filter(self, user):
        provider_roles = [ShopRole.ADMIN, ShopRole.MANAGER]
        return Q(
            shop__owner=user
        ) | Q(
            shop__team_members__user=user,
            shop__team_members__is_active=True,
            shop__team_members__role__in=provider_roles,
        )

    def get_queryset(self):
        user = self.request.user
        return (
            MarketplaceOrder.objects.filter(self._provider_filter(user))
            .distinct()
            .order_by('-created_at')
            .select_related('shop')
            .prefetch_related('items__product')
        )

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        order = provider_complete_marketplace_order_by_id(order_id=pk, provider=request.user)
        serializer = self.get_serializer(order)
        return Response(serializer.data)


class MarketplaceComplaintViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
    queryset = MarketplaceComplaint.objects.all().order_by('-created_at')
    serializer_class = MarketplaceComplaintSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return (
            MarketplaceComplaint.objects.filter(Q(user=user) | Q(order__shop__owner=user))
            .distinct()
            .select_related('order', 'user')
            .order_by('-created_at')
        )

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        # New path: attachment already uploaded direct-to-S3 and confirmed
        # (see /api/v1/commerce/uploads/initiate/), referenced by mediaId.
        # Preferred going forward — bytes never pass through Django.
        attachment_media_id = request.data.get('attachment_media_id') or request.data.get('attachmentMediaId')
        if attachment_media_id:
            complaint = media_uploads.attach_complaint_media(
                user=request.user,
                order_id=request.data.get('order_id') or request.data.get('orderId'),
                text=str(request.data.get('text') or '').strip(),
                media_id=attachment_media_id,
            )
            output = MarketplaceComplaintSerializer(complaint, context=self.get_serializer_context())
            return Response(output.data, status=status.HTTP_201_CREATED)

        # Legacy path: raw multipart file. Kept for backward compatibility
        # with any client build that hasn't picked up the presigned flow.
        serializer = MarketplaceComplaintCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attachment = serializer.validated_data.get('attachment')
        if attachment:
            validate_upload_file_safety(attachment, context="commerce")
        complaint = create_marketplace_complaint_from_data(
            order_id=serializer.validated_data['order_id'],
            user=request.user,
            text=serializer.validated_data['text'],
            attachment=attachment,
        )
        output = MarketplaceComplaintSerializer(complaint, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_201_CREATED)

    @doc_decorator(
        summary="Authorized download URL for a complaint attachment",
        description=(
            "Returns a fresh short-lived presigned GET URL for this complaint's "
            "attachment. Never returns a permanent or public URL; the caller must "
            "be the complaint's author or an authorized manager of the order's shop."
        ),
        request=None,
        responses={200: OpenApiResponse(description="Download URL") if SPECTACULAR else "Download URL"},
    )
    @action(detail=True, methods=['get'], url_path='attachment-download-url')
    def attachment_download_url(self, request, pk=None):
        # get_queryset() already scopes to Q(user=user) | Q(order__shop__owner=user)
        # — reusing it here (rather than re-deriving authorization) means this
        # endpoint can never be more permissive than the existing list/detail view.
        complaint = get_object_or_404(self.get_queryset(), pk=pk)
        if not complaint.attachment:
            raise NotFound("This complaint has no attachment.")
        # Storage.url() on the private-bucket backend already returns a
        # freshly-signed, short-lived GET URL (AWS_S3_PRESIGNED_EXPIRY_SECONDS,
        # default 3600s) — see apps/media/storage_backends.py S3MediaStorage.url.
        # No separate presigning code needed; the authorization above IS the
        # gate that matters.
        return Response({
            'downloadUrl': complaint.attachment.url,
            'expiresInSeconds': _presigned_get_expiry_seconds(),
        })
