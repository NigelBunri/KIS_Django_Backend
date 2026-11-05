"""
KIS Bot Platform — Python example (rule-based, no AI provider required).

Demonstrates, in order:
  1. Obtaining an access token (OAuth2 client_credentials).
  2. Making an authenticated API request (resource discovery).
  3. Receiving a signed webhook (a tiny Flask app).
  4. Verifying the webhook signature.
  5. Handling retries/duplicate events (an in-memory de-dupe set - use a
     real database/Redis set in production so a restart or a second
     instance doesn't lose the de-dupe history).
  6. Returning the correct HTTP response.
  7. A simple rule-based reaction (send a welcome message on
     member.joined) - no AI involved at all.

Install: pip install requests flask
Run:     python python_example.py
"""
import hashlib
import hmac
import os
import time

import requests
from flask import Flask, request, jsonify

KIS_API_BASE = "https://api.kingdomimpactventures.org"
CLIENT_ID = os.environ["KIS_BOT_CLIENT_ID"]
CLIENT_SECRET = os.environ["KIS_BOT_CLIENT_SECRET"]
WEBHOOK_SECRET = os.environ["KIS_BOT_WEBHOOK_SECRET"]  # from PUT /bots/{id}/webhook/

_token_cache = {"access_token": None, "expires_at": 0}


def get_access_token() -> str:
    """# 1. Obtaining an access token."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 30:
        return _token_cache["access_token"]
    resp = requests.post(
        f"{KIS_API_BASE}/api/v1/bots/oauth/token/",
        json={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    _token_cache["access_token"] = body["access_token"]
    _token_cache["expires_at"] = time.time() + body["expires_in"]
    return body["access_token"]


def list_my_resources() -> list[dict]:
    """# 2. Making an authenticated API request."""
    resp = requests.get(
        f"{KIS_API_BASE}/api/v1/bots/external/resources/",
        headers={"Authorization": f"Bearer {get_access_token()}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def send_message(grant_id: str, text: str) -> dict:
    resp = requests.post(
        f"{KIS_API_BASE}/api/v1/bots/external/resources/{grant_id}/send-message/",
        headers={"Authorization": f"Bearer {get_access_token()}"},
        json={"text": text},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def verify_signature(raw_body: bytes, timestamp: str, signature: str) -> bool:
    """# 4. Verifying the webhook signature. Constant-time comparison,
    and a timestamp-age check to prevent replaying a captured request
    outside the tolerance window."""
    if abs(time.time() - int(timestamp)) > 300:  # 5 minutes
        return False
    signed_payload = f"{timestamp}.".encode("utf-8") + raw_body
    expected = hmac.new(WEBHOOK_SECRET.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# 5. Handling retries/duplicate events - replace with a real
# database/Redis set in production (this dict is lost on restart and
# not shared across multiple instances of this process).
_seen_event_ids: set[str] = set()

app = Flask(__name__)


@app.route("/kis-webhook", methods=["POST"])
def kis_webhook():
    """# 3. Receiving a signed webhook."""
    timestamp = request.headers.get("X-KIS-Timestamp", "")
    signature = request.headers.get("X-KIS-Signature", "")
    if not verify_signature(request.get_data(), timestamp, signature):
        return jsonify({"detail": "invalid signature"}), 401

    body = request.get_json()
    event_id = body["event_id"]
    if event_id in _seen_event_ids:
        # Already processed this exact occurrence - acknowledge
        # without redoing the side effect.
        return jsonify({"status": "duplicate_ignored"}), 200
    _seen_event_ids.add(event_id)

    event = body["event"]
    payload = body["payload"]

    # 7. A simple rule-based reaction - no AI provider involved.
    if event == "member.joined":
        grant_id = os.environ["KIS_BOT_GROUP_GRANT_ID"]
        send_message(grant_id, "Welcome! Glad you're here.")

    # 6. Returning the correct HTTP response - any 2xx acknowledges
    # the delivery; KIS retries anything outside 200-299.
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    print("My bot's connected resources:", list_my_resources())
    app.run(port=8080)
