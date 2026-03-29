from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import WalletTransaction
from .documents import ensure_receipt_documents


@receiver(post_save, sender=WalletTransaction)
def _ensure_transaction_receipt(sender, instance: WalletTransaction, **kwargs):
    ensure_receipt_documents(instance)
