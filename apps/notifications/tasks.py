
from celery import shared_task
from django.utils import timezone
from . import firebase, models, services
import random
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def process_notification_delivery(self, notification_id):
    """Process pending deliveries for a notification. Implements retry/backoff and status updates."""
    try:
        notif = models.Notification.objects.get(id=notification_id)
    except models.Notification.DoesNotExist:
        return

    # suppression
    if services.should_suppress(notif.user_id, notif.type, context=notif.context_data):
        # mark as snoozed or postpone
        notif.is_snoozed = True
        notif.snoozed_until = timezone.now() + timezone.timedelta(hours=1)
        notif.save()
        return

    for delivery in notif.deliveries.filter(status="PENDING"):
        # simulate delivery for in-app: mark delivered
        try:
            if delivery.channel == "IN_APP":
                delivery.status = "SENT"
                delivery.delivered_at = timezone.now()
                delivery.save()
                notif.mark_delivered()
            elif delivery.channel == "PUSH":
                tokens = models.NotificationDeviceToken.objects.filter(
                    user_id=notif.user_id,
                    enabled=True,
                    is_deleted=False,
                )
                if not tokens.exists():
                    delivery.status = "PENDING"
                    delivery.last_error = "No active push token registered for this user."
                    delivery.save(update_fields=["status", "last_error", "updated_at"])
                    continue

                sent = False
                errors = []
                stale_token_ids = []
                for token in tokens:
                    ok, result, is_stale = firebase.send_push(token.push_token, notif)
                    if ok:
                        sent = True
                    else:
                        errors.append(result)
                        if is_stale:
                            stale_token_ids.append(token.id)
                if stale_token_ids:
                    # Permanently invalid — deactivate now rather than
                    # re-attempting (and re-failing) on every future
                    # notification to this user. Mirrors Nest's identical
                    # bulkDeactivate-on-stale-response behavior.
                    deactivated = models.NotificationDeviceToken.objects.filter(
                        id__in=stale_token_ids
                    ).update(enabled=False)
                    if deactivated:
                        logger.info("Deactivated %d stale push token(s).", deactivated)
                if sent:
                    delivery.status = "SENT"
                    delivery.delivered_at = timezone.now()
                    delivery.last_error = ""
                    delivery.save()
                    notif.mark_delivered()
                else:
                    delivery.status = "PENDING"
                    delivery.last_error = "; ".join(errors[:3]) or "Push provider delivery failed."
                    delivery.save(update_fields=["status", "last_error", "updated_at"])
            elif delivery.channel == "WEBHOOK":
                # call webhook endpoint (placeholder)
                # In production, sign payload, handle retries, and verify responses
                # simulate random failure
                if random.random() < 0.85:
                    delivery.status = "SENT"
                    delivery.delivered_at = timezone.now()
                    delivery.save()
                    notif.mark_delivered()
                else:
                    raise Exception("webhook error")
            elif delivery.channel == "EMAIL":
                from django.contrib.auth import get_user_model
                _User = get_user_model()
                try:
                    user_obj = _User.objects.filter(id=notif.user_id).first()
                    email_addr = getattr(user_obj, "email", None) if user_obj else None
                    if not email_addr:
                        delivery.status = "PENDING"
                        delivery.last_error = "No email address on file."
                        delivery.save(update_fields=["status", "last_error", "updated_at"])
                        continue
                    from apps.notifications.email_service import send_notification_email
                    sent = send_notification_email(
                        to_email=email_addr,
                        title=notif.title,
                        body=notif.body,
                        context=notif.context_data or {},
                    )
                    delivery.status = "SENT" if sent else "FAILED"
                    if sent:
                        delivery.delivered_at = timezone.now()
                        notif.mark_delivered()
                    else:
                        delivery.last_error = "SMTP delivery failed."
                    delivery.save()
                except Exception as exc:
                    delivery.retry_count += 1
                    delivery.last_error = str(exc)
                    delivery.status = "FAILED"
                    delivery.save()
            elif delivery.channel == "SMS":
                from django.contrib.auth import get_user_model
                _User = get_user_model()
                try:
                    user_obj = _User.objects.filter(id=notif.user_id).first()
                    phone = getattr(user_obj, "phone", None) if user_obj else None
                    if not phone:
                        delivery.status = "PENDING"
                        delivery.last_error = "No phone number on file."
                        delivery.save(update_fields=["status", "last_error", "updated_at"])
                        continue
                    import urllib.request as _req
                    import json as _json
                    from django.conf import settings as _s
                    api_key = getattr(_s, "INFOBIP_API_KEY", "") or ""
                    base = getattr(_s, "INFOBIP_BASE", "") or ""
                    if not api_key or not base:
                        delivery.status = "PENDING"
                        delivery.last_error = "SMS provider not configured."
                        delivery.save(update_fields=["status", "last_error", "updated_at"])
                        continue
                    url = f"{base.rstrip('/')}/sms/2/text/advanced"
                    payload = _json.dumps({
                        "messages": [{"from": "KIS", "destinations": [{"to": phone}], "text": f"{notif.title}: {notif.body[:160]}"}]
                    }).encode("utf-8")
                    req = _req.Request(url, data=payload, headers={"Authorization": f"App {api_key}", "Content-Type": "application/json"}, method="POST")
                    with _req.urlopen(req, timeout=10) as resp:
                        ok = resp.status in (200, 201)
                    delivery.status = "SENT" if ok else "FAILED"
                    if ok:
                        delivery.delivered_at = timezone.now()
                        notif.mark_delivered()
                    delivery.save()
                except Exception as exc:
                    delivery.retry_count += 1
                    delivery.last_error = str(exc)
                    delivery.status = "FAILED"
                    delivery.save()
            else:
                delivery.status = "PENDING"
                delivery.save()

        except Exception as exc:
            delivery.retry_count += 1
            delivery.last_error = str(exc)
            delivery.status = "FAILED"
            delivery.save()
            try:
                raise self.retry(exc=exc, countdown=min(60 * 2 ** delivery.retry_count, 3600))
            except Exception:
                # reached max retries or other issue
                logger.exception("Delivery failed for %s", delivery.id)
                continue


@shared_task
def compile_and_send_digests(period_start_iso: str, period_end_iso: str):
    """Aggregate notifications into digests and mark them for delivery."""
    from django.utils.dateparse import parse_datetime
    start = parse_datetime(period_start_iso)
    end = parse_datetime(period_end_iso)
    # For each user, aggregate notifications
    uids = models.Notification.objects.filter(created_at__gte=start, created_at__lt=end).values_list("user_id", flat=True).distinct()
    for uid in uids:
        notes = models.Notification.objects.filter(user_id=uid, created_at__gte=start, created_at__lt=end)
        payload = [{"id": str(n.id), "title": n.title, "summary": n.body[:200]} for n in notes]
        digest = models.NotificationDigest.objects.create(user_id=uid, period_start=start, period_end=end, notifications=payload)
        # Send digest email
        from django.contrib.auth import get_user_model
        _User = get_user_model()
        user_obj = _User.objects.filter(id=uid).first()
        email_addr = getattr(user_obj, "email", None) if user_obj else None
        if email_addr:
            from apps.notifications.email_service import send_notification_email
            lines = "\n".join(f"• {n['title']}: {n['summary']}" for n in payload[:20])
            if not send_notification_email(
                to_email=email_addr,
                title=f"Your KIS digest — {len(payload)} update(s)",
                body=lines,
            ):
                logger.warning("Digest email failed for user_id=%s digest_id=%s", uid, digest.id)
    return True


@shared_task
def compile_daily_digests():
    """Celery-beat entrypoint for compile_and_send_digests. That task takes
    an explicit (start, end) window rather than a schedule, so nothing
    could call it without a beat-cron wrapper computing one - it had no
    caller anywhere outside its own test. Runs once daily against the
    previous full day (00:00 to 00:00 UTC)."""
    end = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timezone.timedelta(days=1)
    return compile_and_send_digests(start.isoformat(), end.isoformat())
