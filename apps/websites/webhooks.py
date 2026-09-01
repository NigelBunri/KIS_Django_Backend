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

from common.url_safety import is_safe_external_url

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
    # SSRF guard: target_url is set by the website owner via a self-service
    # form, but the fetch itself runs from the KIS backend, which can reach
    # internal-only hosts a website owner has no business reaching (other
    # internal services, the cloud metadata endpoint, etc). Skip delivery
    # rather than raise, matching this function's existing "never block the
    # triggering request" contract.
    if not is_safe_external_url(webhook.target_url):
        logger.info(
            "Website webhook delivery skipped for %s (%s): target_url is not an allowed external address",
            webhook.id, event_type,
        )
        return
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
