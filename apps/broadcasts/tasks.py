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
def purge_expired_broadcasts_task():
    """Thin wrapper so cleanup_expired_broadcast_items - a real, working
    function with its own management command - is actually scheduled
    (see CELERY_BEAT_SCHEDULE) instead of only running when someone
    remembers to invoke the command by hand."""
    from .services import cleanup_expired_broadcast_items

    return {"deleted": cleanup_expired_broadcast_items()}


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
