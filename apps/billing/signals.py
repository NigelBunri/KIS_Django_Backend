import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import WalletTransaction
from .documents import ensure_receipt_documents

logger = logging.getLogger(__name__)


@receiver(post_save, sender=WalletTransaction)
def _ensure_transaction_receipt(sender, instance: WalletTransaction, **kwargs):
    # Found while adding the referral-commission webhook hook
    # (apps.referrals.commission_services): this signal fires
    # unconditionally on every WalletTransaction save, including the one
    # inside FlutterwaveWebhookView's successful-payment branch - with no
    # error handling at all, a transient storage outage here (S3
    # unreachable, credentials misconfigured) would 500 the ENTIRE
    # webhook response for an otherwise-successfully-processed payment
    # (subscription already upgraded, referral already qualified,
    # commission already recorded - all of that had already committed by
    # the time this signal fires). Generating a receipt document is a
    # best-effort side effect of a successful payment, not a precondition
    # for one; it must never be able to turn a real success into an
    # apparent failure.
    try:
        ensure_receipt_documents(instance)
    except Exception:
        logger.exception(
            "ensure_receipt_documents failed for WalletTransaction id=%s tx_ref=%s - "
            "payment processing itself is unaffected; the receipt can be regenerated "
            "on next access since ensure_receipt_documents is idempotent.",
            instance.id, instance.tx_ref,
        )
