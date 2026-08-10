# billing/tasks.py
from celery import shared_task
from .services import sweep_expired_subscriptions


@shared_task
def expire_subscriptions():
    """Periodic sweep for subscriptions past their ends_at that are still
    marked active — see apps.billing.services.sweep_expired_subscriptions
    for what this actually does and why it exists. Schedule via Celery
    Beat; apps/billing/management/commands/reconcile_subscriptions.py wraps
    the same function for manual/cron use where Beat isn't configured."""
    return sweep_expired_subscriptions()
