"""
Fires WebsiteWebhook targets synchronously, inline, at the point of the
triggering event — this deployment runs no Celery worker/beat process at
all (2026-08-06 systems audit), so anything queued through Celery would
simply never execute. A short timeout and a broad except keep a slow or
unreachable target from ever blocking or failing the real request
(publish/unpublish/form submit) it's attached to.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 4


def generate_webhook_secret() -> str:
    return secrets.token_hex(32)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def fire_webhook_event(website, event_type: str, payload: dict) -> None:
    webhooks = website.webhooks.filter(event_type=event_type, is_active=True)
    for webhook in webhooks:
        _send_one(webhook, event_type, payload)


def _send_one(webhook, event_type: str, payload: dict) -> None:
    body = json.dumps({"event": event_type, "website_id": str(webhook.website_id), "data": payload}).encode()
    signature = _sign(webhook.secret, body)
    try:
        requests.post(
            webhook.target_url,
            data=body,
            headers={"Content-Type": "application/json", "X-KIS-Signature": signature},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.info("Website webhook delivery failed for %s (%s): %s", webhook.id, event_type, exc)
