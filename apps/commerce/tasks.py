import logging

from celery import shared_task
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    ShopVerificationRequest,
    ProductAuthenticityCheck,
    Order,
    FraudSignal,
    AIRecommendation,
    Product,
    Shop,
    ServiceBooking,
)
from .services import (
    run_shop_verification_checks,
    run_product_auth_check,
    compute_fraud_for_order,
    build_recommendations,
    satisfy_marketplace_order,
)
from apps.verification.services import sync_shop_verification_request

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2)
def enqueue_shop_verification(self, request_id):
    req = ShopVerificationRequest.objects.get(id=request_id)
    req.status = 'IN_REVIEW'
    req.save()
    sync_shop_verification_request(verification_request=req)
    try:
        result = run_shop_verification_checks(req)
        req.status = result.get('status', 'APPROVED')
        req.risk_score = result.get('risk_score', 0.0)
        req.processed_at = timezone.now()
        req.save()
        if req.status == 'APPROVED':
            shop = req.shop
            shop.is_verified = True
            shop.verification_status = 'VERIFIED'
            tb = list(set(shop.trust_badges + result.get('badges', [])))
            tb = list(set(tb + ['kyc', 'verified-shop']))
            shop.trust_badges = tb
            shop.save(update_fields=['is_verified', 'verification_status', 'trust_badges', 'updated_at'])
        elif req.status == 'REJECTED' and not req.shop.is_verified:
            req.shop.verification_status = 'REJECTED'
            req.shop.save(update_fields=['verification_status', 'updated_at'])
        sync_shop_verification_request(verification_request=req)
        return result
    except Exception as exc:
        req.status = 'ERROR'
        req.save()
        sync_shop_verification_request(verification_request=req)
        raise self.retry(exc=exc, countdown=10)


@shared_task(bind=True, max_retries=2)
def enqueue_product_auth_check(self, check_id):
    pac = ProductAuthenticityCheck.objects.get(id=check_id)
    pac.status = 'PROCESSING'
    pac.save()
    try:
        result = run_product_auth_check(pac)
        pac.status = result.get('status', 'VERIFIED')
        pac.result = result.get('result', {})
        pac.confidence = result.get('confidence', 0.0)
        pac.checked_at = timezone.now()
        pac.save()
        prod = pac.product
        prod.authenticity_status = pac.status
        prod.authenticity_proof = pac.result.get('proof', {})
        prod.save()
        return result
    except Exception as exc:
        pac.status = 'ERROR'
        pac.save()
        raise self.retry(exc=exc, countdown=10)


@shared_task
def evaluate_fraud_score(order_id):
    from .models import OrderStatus
    order = Order.objects.select_related('user').prefetch_related('items').filter(id=order_id).first()
    if not order:
        return {'error': 'order_not_found'}
    score, details = compute_fraud_for_order(order)
    FraudSignal.objects.create(
        source='fraud_engine',
        entity_type='order',
        entity_id=order.id,
        score=score,
        details=details,
    )
    if score >= 0.75:
        # Auto-hold: cancel and flag for manual review
        order.status = OrderStatus.CANCELLED
        order.metadata = {
            **(order.metadata or {}),
            'fraud_hold': True,
            'fraud_score': score,
            'fraud_details': details,
        }
        order.save(update_fields=['status', 'metadata', 'updated_at'])
    elif score >= 0.50:
        # Flag for manual review without cancelling
        order.metadata = {
            **(order.metadata or {}),
            'fraud_review_required': True,
            'fraud_score': score,
        }
        order.save(update_fields=['metadata', 'updated_at'])
    return {'score': score, 'action': 'hold' if score >= 0.75 else 'review' if score >= 0.50 else 'pass'}


@shared_task
def compute_recommendations(user_id):
    recs = build_recommendations(user_id)
    created = []
    for r in recs:
        ai = AIRecommendation.objects.create(
            user_id=user_id,
            target_type=r['type'],
            target_id=r['id'],
            score=r['score'],
            reason=r.get('reason', ''),
        )
        created.append(str(ai.id))
    return created


@shared_task
def delete_cancelled_marketplace_order(order_id: str):
    from .models import MarketplaceOrder, MarketplaceOrderStatus

    try:
        order = MarketplaceOrder.objects.get(
            id=order_id,
            status__in=[MarketplaceOrderStatus.CANCELLED, MarketplaceOrderStatus.SATISFIED],
        )
        order.delete()
        return {'status': 'deleted'}
    except MarketplaceOrder.DoesNotExist:
        return {'status': 'missing'}


@shared_task
def auto_satisfy_marketplace_order(order_id: str):
    from .models import MarketplaceComplaint, MarketplaceOrder, MarketplaceOrderStatus

    try:
        order = MarketplaceOrder.objects.get(id=order_id)
    except MarketplaceOrder.DoesNotExist:
        return {'status': 'missing'}
    if order.status != MarketplaceOrderStatus.AWAITING_SATISFACTION:
        return {'status': 'skipped', 'current_status': order.status}
    if MarketplaceComplaint.objects.filter(order=order).exists():
        return {'status': 'skipped', 'current_status': 'complaint'}
    metadata = order.metadata if isinstance(order.metadata, dict) else {}
    deadline_raw = metadata.get('awaiting_satisfaction_until') or metadata.get('satisfaction_deadline')
    deadline = None
    if deadline_raw:
        try:
            deadline = timezone.datetime.fromisoformat(str(deadline_raw).replace('Z', '+00:00'))
            if timezone.is_naive(deadline):
                deadline = timezone.make_aware(deadline, timezone.get_current_timezone())
        except ValueError:
            deadline = None
    if deadline and deadline > timezone.now():
        return {'status': 'pending_window', 'available_at': deadline.isoformat()}
    try:
        satisfy_marketplace_order(order)
        order.delete()
        return {'status': 'satisfied_deleted'}
    except ValidationError as exc:
        logger.warning('Auto satisfaction failed for order %s: %s', order_id, exc)
        return {'status': 'failed', 'error': str(exc)}


@shared_task(bind=True)
def send_service_booking_reminders(self):
    from apps.notifications import services as notification_services
    from apps.accounts.notification_i18n import get_notification_string, get_user_language
    from django.contrib.auth import get_user_model
    _User = get_user_model()
    now = timezone.now()
    window_end = now + timezone.timedelta(hours=24)
    upcoming = (
        ServiceBooking.objects.select_related('service')
        .filter(
            status=ServiceBooking.STATUS_CONFIRMED,
            scheduled_at__gte=now,
            scheduled_at__lte=window_end,
            reminder_sent_at__isnull=True,
        )
        .order_by('scheduled_at')
    )
    reminders_sent = 0
    for booking in upcoming:
        scheduled_label = booking.scheduled_at.strftime('%Y-%m-%d %H:%M %Z')
        user_obj = _User.objects.filter(id=booking.user_id).first()
        lang = get_user_language(user_obj) if user_obj else 'en'
        reminder_label = get_notification_string(lang, 'booking_reminder')
        notification_services.create_notification(
            user_id=booking.user_id,
            type='commerce.service_booking.reminder',
            title=reminder_label,
            body=f"Reminder: your appointment for {booking.service.name} is scheduled for {scheduled_label}.",
            target_type='service_booking',
            target_id=booking.id,
            dedup_key=f'commerce:service_booking:{booking.id}:reminder',
        )
        booking.reminder_sent_at = now
        booking.save(update_fields=['reminder_sent_at'])
        reminders_sent += 1
    return {'reminders_sent': reminders_sent}


@shared_task(bind=True)
def cleanup_expired_service_bookings(self):
    cutoff = timezone.now() - timezone.timedelta(days=5)
    expired_qs = ServiceBooking.objects.filter(scheduled_at__lte=cutoff)
    total = expired_qs.count()
    if total:
        expired_qs.delete()
        logger.info('Deleted %s service bookings that were older than 5 days', total)
    return {'deleted': total}
