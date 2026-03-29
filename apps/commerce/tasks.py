import logging

from celery import shared_task
from django.utils import timezone

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
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2)
def enqueue_shop_verification(self, request_id):
    req = ShopVerificationRequest.objects.get(id=request_id)
    req.status = 'IN_REVIEW'
    req.save()
    try:
        result = run_shop_verification_checks(req)
        req.status = result.get('status', 'APPROVED')
        req.risk_score = result.get('risk_score', 0.0)
        req.processed_at = timezone.now()
        req.save()
        if req.status == 'APPROVED':
            shop = req.shop
            shop.is_verified = True
            tb = list(set(shop.trust_badges + result.get('badges', [])))
            shop.trust_badges = tb
            shop.save()
        return result
    except Exception as exc:
        req.status = 'ERROR'
        req.save()
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
    order = Order.objects.get(id=order_id)
    score, details = compute_fraud_for_order(order)
    FraudSignal.objects.create(
        source='fraud_engine',
        entity_type='order',
        entity_id=order.id,
        score=score,
        details=details,
    )
    if score > 0.8:
        order.status = 'PENDING'
        order.save()
    return {'score': score}


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


@shared_task(bind=True)
def send_service_booking_reminders(self):
    from apps.notifications import services as notification_services
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
        notification_services.create_notification(
            user_id=booking.user_id,
            type='commerce.service_booking.reminder',
            title='Appointment reminder',
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
