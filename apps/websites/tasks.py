# websites/tasks.py
#
# Async delivery for website webhooks. Previously fired synchronously
# inline on the triggering request (publish/unpublish/form submit) because
# an earlier systems audit (2026-08-06) found no Celery worker running in
# this deployment. That's no longer true — Celery worker/beat is confirmed
# running in prod (2026-09-11 audit) and is already used throughout the
# codebase (apps.accounts.tasks, apps.bible.tasks, etc.), so there's no
# reason left to block a user-facing request on an external HTTP call to a
# third-party target_url the website owner controls.
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def deliver_website_webhook(webhook_id: str, event_type: str, payload: dict) -> None:
    from .models import WebsiteWebhook
    from .webhooks import _send_one

    try:
        webhook = WebsiteWebhook.objects.get(id=webhook_id, is_active=True)
    except WebsiteWebhook.DoesNotExist:
        logger.info("Website webhook %s no longer exists/active; skipping delivery", webhook_id)
        return
    _send_one(webhook, event_type, payload)
