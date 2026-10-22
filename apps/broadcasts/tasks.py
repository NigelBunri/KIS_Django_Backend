from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.billing.models import WalletTransaction
from apps.billing.services import release_locked_booking_funds
from apps.commerce.constants import KIS_COIN_CODE


@shared_task
def auto_complete_education_booking(booking_id: str):
    from .models import EducationBookingStatus, EducationInstitutionBooking

    # select_for_update() + transaction.atomic() so this can never race the
    # payer's own satisfaction confirmation (EducationInstitutionBooking
    # SatisfactionView) or an institution's manual completion/cancellation
    # (EducationInstitutionBookingActionView) — both do the same check-then-
    # release/refund-funds pattern on this booking, and CELERY_TASK_ACKS_LATE
    # (config/settings/base.py) means this task itself can be redelivered
    # and re-run after a worker crash mid-task. release_locked_booking_funds/
    # refund_locked_booking_funds (apps/billing/services.py) have no
    # idempotency guard of their own, so the lock here is the only thing
    # preventing a double payout. Found during Phase 4 of the Education
    # system production-hardening project.
    with transaction.atomic():
        try:
            # select_related limited to institution/user (both non-nullable
            # FKs) — Postgres rejects FOR UPDATE combined with
            # select_related() on a nullable FK, and provider_credit_
            # transaction is null=True on this model.
            booking = EducationInstitutionBooking.objects.select_for_update().select_related(
                "institution",
                "user",
            ).get(id=booking_id)
        except EducationInstitutionBooking.DoesNotExist:
            return {"status": "missing"}

        if booking.status != EducationBookingStatus.AWAITING_SATISFACTION:
            return {"status": "skipped", "current_status": booking.status}
        if booking.satisfaction_deadline and booking.satisfaction_deadline > timezone.now():
            return {"status": "waiting"}
        return _complete_awaiting_booking(booking)


def _complete_awaiting_booking(booking):
    from .models import EducationBookingStatus

    try:
        metadata = dict(booking.metadata) if isinstance(booking.metadata, dict) else {}
        payment_status = str(metadata.get("payment_status") or "").strip().lower()
        direct_provider_paid = payment_status in {"paid", "success", "succeeded", "settled"}
        if booking.amount_cents > 0 and not booking.provider_credit_transaction_id and not booking.wallet_transaction_id and not direct_provider_paid:
            return {"status": "failed", "error": "Provider payment must be confirmed before auto-completion."}
        if booking.amount_cents > 0 and booking.wallet_transaction_id and not booking.provider_credit_transaction_id:
            reference = f"education-booking-payout-{booking.id}"
            release_locked_booking_funds(
                payer=booking.user,
                provider=booking.institution.owner,
                amount_cents=int(booking.amount_cents or 0),
                reference=reference,
                meta={
                    "booking_id": str(booking.id),
                    "broadcast_id": str(booking.broadcast_id),
                    "institution_id": str(booking.institution_id),
                    "source": "education_booking_payout",
                    "auto_release": True,
                },
            )
            provider_tx = WalletTransaction.objects.create(
                user=booking.institution.owner,
                provider="internal",
                method="education_booking_payout",
                amount_cents=int(booking.amount_cents or 0),
                currency=booking.currency or KIS_COIN_CODE,
                status="success",
                tx_ref=reference,
                processed_at=timezone.now(),
                meta={
                    "booking_id": str(booking.id),
                    "broadcast_id": str(booking.broadcast_id),
                    "institution_id": str(booking.institution_id),
                    "source": "education_booking_payout",
                    "auto_release": True,
                },
            )
            booking.provider_credit_transaction = provider_tx

        metadata["auto_released_at"] = timezone.now().isoformat()
        booking.status = EducationBookingStatus.COMPLETED
        booking.metadata = metadata
        booking.save(update_fields=["status", "provider_credit_transaction", "metadata", "updated_at"])
        return {"status": "completed"}
    except (ValueError, ValidationError) as exc:
        return {"status": "failed", "error": str(exc)}


@shared_task
def sweep_stuck_education_bookings(limit: int = 500):
    """Periodic backstop for auto_complete_education_booking: that task is
    scheduled per-booking via apply_async(countdown=...) at the moment a
    provider marks a booking AWAITING_SATISFACTION — a one-off delayed
    task, unlike every other Education/media/billing sweep, which all run
    on a recurring beat schedule. A one-off task is lost for good if the
    broker drops it (e.g. a Redis restart without persistence) or if the
    countdown was scheduled before a deploy that changed the task's import
    path — with no periodic reconciliation, an escrowed booking would stay
    locked indefinitely with no code path ever revisiting it. This sweep
    finds every AWAITING_SATISFACTION booking already past its
    satisfaction_deadline and drives it through the same locked
    _complete_awaiting_booking() the per-booking task uses, so a lost
    one-off schedule self-heals within one sweep interval instead of
    silently stranding a payer's funds forever. Found during Phase 4 of the
    Education system production-hardening project — see
    CELERY_BEAT_SCHEDULE in config/settings/base.py for the schedule."""
    from .models import EducationBookingStatus, EducationInstitutionBooking

    stuck_ids = list(
        EducationInstitutionBooking.objects.filter(
            status=EducationBookingStatus.AWAITING_SATISFACTION,
            satisfaction_deadline__lte=timezone.now(),
        )
        .order_by("satisfaction_deadline")
        .values_list("id", flat=True)[:limit]
    )
    results = {"status": "completed", "failed": 0, "swept": 0}
    for booking_id in stuck_ids:
        outcome = auto_complete_education_booking(str(booking_id))
        if outcome.get("status") == "completed":
            results["swept"] += 1
        elif outcome.get("status") == "failed":
            results["failed"] += 1
    return results


@shared_task
def sweep_expired_channel_memberships_task(limit: int = 500):
    """Periodic sweep for paid ChannelMembership rows past their
    expires_at that are still ACTIVE. MembershipScreen.tsx labels every
    paid tier "/mo", and gift memberships already got a real 30-day
    expires_at (ChannelMembershipGiftRedeemView), but a directly-purchased
    membership's activation (apps.billing.views's Flutterwave/Stripe
    webhook handlers) never set one at all, so it stayed ACTIVE forever
    regardless of renewal. This is expiry only, not auto-renewal - there is
    no recurring re-charge of the original payment method yet, so a
    member must re-purchase after lapsing. See CELERY_BEAT_SCHEDULE in
    config/settings/base.py for the schedule, and sweep_expired_subscriptions
    in apps.billing.services for the equivalent AccountTier sweep this
    mirrors."""
    from django.utils import timezone as _timezone

    from .models import ChannelMembership

    expired_ids = list(
        ChannelMembership.objects.filter(
            status=ChannelMembership.Status.ACTIVE,
            expires_at__isnull=False,
            expires_at__lte=_timezone.now(),
        ).order_by("expires_at").values_list("id", flat=True)[:limit]
    )
    updated = ChannelMembership.objects.filter(id__in=expired_ids).update(
        status=ChannelMembership.Status.EXPIRED,
    )
    return {"candidates": len(expired_ids), "expired": updated}


@shared_task
def purge_expired_broadcasts_task():
    """Thin wrapper so cleanup_expired_broadcast_items - a real, working
    function with its own management command - is actually scheduled
    (see CELERY_BEAT_SCHEDULE) instead of only running when someone
    remembers to invoke the command by hand."""
    from .services import cleanup_expired_broadcast_items

    return {"deleted": cleanup_expired_broadcast_items()}


@shared_task
def send_live_stream_reminders_task(window_minutes: int = 15, limit: int = 500):
    """Finds scheduled live streams starting within the next
    `window_minutes` and sends a one-time "starting soon" reminder to
    subscribers. Mirrors the exact dedup pattern ChannelLiveStreamWebhookView
    already uses for the live-started notification (a metadata flag, not a
    separate tracking model) so a stream is never reminded twice across
    repeated beat ticks. Deliberately a single fixed window rather than
    multiple reminder tiers (e.g. 1h + 15m) - this is the first reminder
    this app has ever sent for live streams; a single well-chosen window is
    enough to start, more tiers can follow if creators ask for them."""
    from .models import BroadcastChannel, ChannelLiveStream
    from .views import _notify_channel_subscribers

    cutoff = timezone.now() + timedelta(minutes=window_minutes)
    candidates = list(
        ChannelLiveStream.objects.select_related("channel")
        .filter(
            status=ChannelLiveStream.Status.SCHEDULED,
            scheduled_start_at__isnull=False,
            scheduled_start_at__lte=cutoff,
            scheduled_start_at__gte=timezone.now(),
        )
        .order_by("scheduled_start_at")[:limit]
    )
    reminded = 0
    for live in candidates:
        if (live.metadata or {}).get("reminder_sent"):
            continue
        _notify_channel_subscribers(
            live.channel,
            notification_type="CHANNEL_LIVE_REMINDER",
            title=f"{live.channel.display_name} goes live soon",
            body=live.title,
            target_type="channel_live_stream",
            target_id=live.id,
        )
        live.metadata = {**(live.metadata or {}), "reminder_sent": True}
        live.save(update_fields=["metadata", "updated_at"])
        reminded += 1
    return {"candidates": len(candidates), "reminded": reminded}


@shared_task
def fail_stale_live_streams_task(timeout_minutes: int = 10, limit: int = 500):
    """Finds provider-backed live streams where the creator tapped Start
    (status=LIVE, started_at set) more than `timeout_minutes` ago but no
    webhook ever confirmed real ingest (metadata["subscribers_notified"] -
    the same flag ChannelLiveStreamWebhookView sets on its first confirmed
    "live" event - is still falsy). Marks these FAILED rather than leaving
    them stuck LIVE forever with a dead encoder. Deliberately excludes
    provider="disabled" streams: that path notifies subscribers immediately
    on Start (see ChannelLiveStreamStartView) because no webhook will ever
    arrive for it by design, so "no webhook yet" is never a failure signal
    there. Only notifies the creator, not subscribers - nothing was ever
    actually live, so there's nothing to tell the audience."""
    from .models import ChannelLiveStream

    cutoff = timezone.now() - timedelta(minutes=timeout_minutes)
    candidates = list(
        ChannelLiveStream.objects.select_related("channel")
        .exclude(provider="disabled")
        .exclude(provider="")
        .filter(
            status=ChannelLiveStream.Status.LIVE,
            started_at__isnull=False,
            started_at__lte=cutoff,
        )
        .order_by("started_at")[:limit]
    )
    failed = 0
    for live in candidates:
        if (live.metadata or {}).get("subscribers_notified"):
            continue
        live.status = ChannelLiveStream.Status.FAILED
        live.ended_at = live.ended_at or timezone.now()
        live.save(update_fields=["status", "ended_at", "updated_at"])
        if live.created_by_id:
            try:
                from apps.notifications import services as notification_services
                notification_services.create_notification(
                    user_id=live.created_by_id,
                    type="CHANNEL_LIVE_FAILED",
                    title="Your live stream couldn't start",
                    body=f"\"{live.title}\" never connected to the streaming server. Please try again.",
                    target_type="channel_live_stream",
                    target_id=live.id,
                    priority="HIGH",
                    dedup_key=f"CHANNEL_LIVE_FAILED:{live.id}",
                    context={"channel_id": str(live.channel_id), "live_id": str(live.id)},
                )
            except Exception:
                pass
        failed += 1
    return {"candidates": len(candidates), "failed": failed}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def push_asset_to_kisvideo(self, asset_id: str):
    """Fires the (potentially slow — reads the full object out of S3 and
    streams it to kisvideo) tus upload dance in the background, off the
    request/response path of ChannelContentAssetUploadView. Only called
    when KIS_VIDEO_SERVICE_ENABLED is on (see that view); a transient
    network failure retries a few times before giving up and marking the
    asset failed, rather than leaving it stuck at 'queued' forever.

    Re-checks the flag here too, not just at enqueue time: if an operator
    flips KIS_VIDEO_SERVICE_ENABLED off after a job is already queued in
    Redis but before a worker picks it up, this is the only place that can
    still stop it from actually calling out to kisvideo. If that happens,
    the asset is left exactly as it was (still 'queued') rather than
    guessed at — see the kisvideo rollback runbook for how to recover it
    manually."""
    from django.conf import settings

    from .kisvideo_provider import KisVideoProviderError, KisVideoProvider, sign_kisvideo_callback_token
    from .models import ChannelContent, ChannelContentAsset

    if not getattr(settings, "KIS_VIDEO_SERVICE_ENABLED", False):
        return {"status": "skipped_flag_disabled"}

    try:
        asset = ChannelContentAsset.objects.select_related("content__channel").get(id=asset_id)
    except ChannelContentAsset.DoesNotExist:
        return {"status": "missing"}

    channel = asset.content.channel
    owner_user_id = channel.owner_user_id or channel.owner_id

    callback_base = str(getattr(settings, "API_BASE_URL", "") or "").rstrip("/")
    token = sign_kisvideo_callback_token(str(asset.id))
    callback_url = f"{callback_base}/api/v1/broadcasts/internal/kisvideo-callback/?asset_id={asset.id}&token={token}"

    try:
        KisVideoProvider().create_transcode_job(
            storage_path=asset.storage_path,
            filename=asset.storage_path.rsplit("/", 1)[-1] or "upload",
            content_type=asset.mime_type or "application/octet-stream",
            owner_user_id=str(owner_user_id),
            callback_url=callback_url,
            caller_reference=str(asset.id),
        )
    except KisVideoProviderError as exc:
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            asset.processing_status = "failed"
            asset.save(update_fields=["processing_status"])
            asset.content.status = ChannelContent.Status.FAILED
            asset.content.save(update_fields=["status"])
            return {"status": "failed", "error": str(exc)}

    asset.processing_status = "transcoding"
    asset.save(update_fields=["processing_status"])
    return {"status": "submitted"}
