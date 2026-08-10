"""
Django email backend that sends via the Resend HTTP API
(https://resend.com/docs/api-reference/emails/send-email) instead of SMTP.

Selected automatically by config/settings/production.py when
RESEND_API_KEY is configured — every existing call site that sends email
(apps.notifications.email_service, direct django.core.mail.send_mail calls,
etc.) is unaffected, since they all go through whatever EMAIL_BACKEND is
configured rather than talking to a provider directly.
"""
from __future__ import annotations

import logging

import requests
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"

# Bounded deliberately low: every current call site sends synchronously
# inside a request or webhook handler (see apps/notifications/email_service.py
# and its callers) — an unbounded or slow-provider call there would hang
# the HTTP response, not just delay an email.
RESEND_TIMEOUT_SECONDS = 8


class ResendAPIError(Exception):
    """Raised for a non-2xx response from Resend. Never includes the API key."""


class ResendEmailBackend(BaseEmailBackend):
    def __init__(self, *args, api_key: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        from django.conf import settings

        self.api_key = api_key or getattr(settings, "RESEND_API_KEY", "")

    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0
        if not self.api_key:
            if self.fail_silently:
                return 0
            raise ResendAPIError("RESEND_API_KEY is not configured.")

        sent_count = 0
        for message in email_messages:
            try:
                self._send_one(message)
                sent_count += 1
            except Exception as exc:
                logger.warning("Resend send failed: %s", exc.__class__.__name__)
                if not self.fail_silently:
                    raise
        return sent_count

    def _send_one(self, message) -> None:
        html_body = ""
        for content, mimetype in getattr(message, "alternatives", []) or []:
            if mimetype == "text/html":
                html_body = content
                break

        payload = {
            "from": message.from_email,
            "to": list(message.to),
            "subject": message.subject,
            "text": message.body,
        }
        if html_body:
            payload["html"] = html_body
        if message.cc:
            payload["cc"] = list(message.cc)
        if message.bcc:
            payload["bcc"] = list(message.bcc)
        if message.reply_to:
            payload["reply_to"] = list(message.reply_to)

        try:
            response = requests.post(
                RESEND_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=RESEND_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise ResendAPIError(f"Resend request failed: {exc.__class__.__name__}") from exc

        if response.status_code >= 400:
            # Resend error bodies are small JSON objects (e.g. {"message": "..."})
            # and do not echo the Authorization header back, so this is safe to
            # include — but never log/raise the request headers themselves.
            raise ResendAPIError(
                f"Resend API returned {response.status_code}: {response.text[:300]}"
            )
