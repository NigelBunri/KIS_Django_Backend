from celery import shared_task
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.billing.models import WalletTransaction
from apps.billing.services import release_locked_booking_funds
from apps.commerce.constants import KIS_COIN_CODE


@shared_task
def auto_complete_education_booking(booking_id: str):
    from .models import EducationBookingStatus, EducationInstitutionBooking

    try:
        booking = EducationInstitutionBooking.objects.select_related(
            "institution",
            "user",
            "provider_credit_transaction",
        ).get(id=booking_id)
    except EducationInstitutionBooking.DoesNotExist:
        return {"status": "missing"}

    if booking.status != EducationBookingStatus.AWAITING_SATISFACTION:
        return {"status": "skipped", "current_status": booking.status}
    if booking.satisfaction_deadline and booking.satisfaction_deadline > timezone.now():
        return {"status": "waiting"}

    try:
        if booking.amount_cents > 0 and not booking.provider_credit_transaction_id:
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

        metadata = dict(booking.metadata) if isinstance(booking.metadata, dict) else {}
        metadata["auto_released_at"] = timezone.now().isoformat()
        booking.status = EducationBookingStatus.COMPLETED
        booking.metadata = metadata
        booking.save(update_fields=["status", "provider_credit_transaction", "metadata", "updated_at"])
        return {"status": "completed"}
    except (ValueError, ValidationError) as exc:
        return {"status": "failed", "error": str(exc)}
