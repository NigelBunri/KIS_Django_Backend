# apps/bots/tasks.py
from celery import shared_task

from . import services


@shared_task
def retry_pending_bot_webhook_deliveries() -> int:
    """Celery-beat entry point — see settings.CELERY_BEAT_SCHEDULE's
    'retry-pending-bot-webhook-deliveries' entry. Mirrors the retry-sweep
    pattern already used for other periodic KIS maintenance tasks
    (apps.media.tasks) — plain @shared_task with no name= override, so
    the task's registered name is the default "apps.bots.tasks.
    retry_pending_bot_webhook_deliveries", matching the dotted path the
    beat schedule and apps.core's verify_celery_launch guardrail both
    expect to actually import. The previous name=-override + "_task"
    suffixed function let Celery's own registry resolve the task fine
    (that's all Celery itself needs), but broke that stricter import-by-
    dotted-path guardrail check, which has no reason to know about a
    name= override."""
    return services.retry_pending_bot_webhook_deliveries()
