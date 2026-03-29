from django.utils import timezone

from apps.broadcasts.models import BroadcastItem


def cleanup_expired_broadcast_items() -> int:
    """
    Remove broadcast items whose expiration date has passed.
    """
    now = timezone.now()
    expired_qs = BroadcastItem.objects.filter(expires_at__lte=now)
    expired_count = expired_qs.count()
    if expired_count:
        expired_qs.delete()
    return expired_count
