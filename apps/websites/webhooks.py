"""
Fires WebsiteWebhook targets via an async Celery task (apps.websites.tasks.
deliver_website_webhook) queued from fire_webhook_event, so a slow or
unreachable target_url never blocks the real request (publish/unpublish/
form submit) that triggered it, and a transient failure gets retried
instead of silently dropped.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets

import requests

from common.url_safety import is_safe_external_url

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 4


def generate_webhook_secret() -> str:
    return secrets.token_hex(32)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def fire_webhook_event(website, event_type: str, payload: dict) -> None:
    from .tasks import deliver_website_webhook

    webhooks = website.webhooks.filter(event_type=event_type, is_active=True)
    for webhook in webhooks:
        deliver_website_webhook.delay(str(webhook.id), event_type, payload)


def _send_one(webhook, event_type: str, payload: dict) -> None:
    # SSRF guard: target_url is set by the website owner via a self-service
    # form, but the fetch itself runs from the KIS backend, which can reach
    # internal-only hosts a website owner has no business reaching (other
    # internal services, the cloud metadata endpoint, etc). Skip delivery
    # rather than raise — this is a policy rejection, not a failure worth
    # retrying.
    if not is_safe_external_url(webhook.target_url):
        logger.info(
            "Website webhook delivery skipped for %s (%s): target_url is not an allowed external address",
            webhook.id, event_type,
        )
        return
    body = json.dumps({"event": event_type, "website_id": str(webhook.website_id), "data": payload}).encode()
    signature = _sign(webhook.secret, body)
    # Let request exceptions propagate: the Celery task wrapping this call
    # retries on exactly this, instead of the old inline call that swallowed
    # failures and dropped the delivery for good on the first timeout.
    requests.post(
        webhook.target_url,
        data=body,
        headers={"Content-Type": "application/json", "X-KIS-Signature": signature},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
