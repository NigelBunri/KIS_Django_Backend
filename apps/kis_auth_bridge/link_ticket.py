"""Mints the signed "link ticket" kis-auth's LinkTicketService verifies
(src/security/link-ticket.ts in the kis-auth repo — this is the minting
half of that exact format, kept in sync deliberately rather than via a
shared library, since it's five lines of HMAC and a shared dependency
would be more ceremony than the problem needs).

Format: base64url(JSON payload, no padding) + "." + hex(HMAC-SHA256(secret, encoded_payload)).
Uses the same KISAUTH_INTERNAL_HMAC_SECRET already shared for the
exchange/security-event channels — no new secret to provision.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time


class LinkTicketError(Exception):
    pass


LINK_TICKET_TTL_SECONDS = 300  # generous enough to cover the Google round trip


def mint_link_ticket(kis_user_id: str) -> str:
    secret = os.environ.get("KISAUTH_INTERNAL_HMAC_SECRET", "").strip()
    if not secret:
        raise LinkTicketError("KISAUTH_INTERNAL_HMAC_SECRET not configured")

    payload = {
        "kisUserId": str(kis_user_id),
        "nonce": secrets.token_urlsafe(16),
        "exp": int(time.time()) + LINK_TICKET_TTL_SECONDS,
    }
    encoded_payload = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    signature = hmac.new(
        secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{encoded_payload}.{signature}"
