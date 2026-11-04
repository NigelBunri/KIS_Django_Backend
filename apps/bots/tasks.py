# apps/bots/tasks.py
from celery import shared_task

from . import services


@shared_task(name="apps.bots.tasks.retry_pending_bot_webhook_deliveries")
def retry_pending_bot_webhook_deliveries_task() -> int:
    """Celery-beat entry point — see settings.CELERY_BEAT_SCHEDULE's
    'retry-pending-bot-webhook-deliveries' entry. Mirrors the retry-sweep
    pattern already used for other periodic KIS maintenance tasks
    (apps.media.tasks)."""
    return services.retry_pending_bot_webhook_deliveries()
