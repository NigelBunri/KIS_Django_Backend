import json
import logging
import urllib.request
from typing import Iterable

from django.conf import settings

from apps.chat.internal_signing import sign_internal_request

logger = logging.getLogger(__name__)

MAIN_TAB_BADGES_UPDATED_EVENT = "main_tab_badges.updated"


def notify_main_tab_badges_updated(
    user_ids: Iterable[str],
    *,
    source: str,
    reason: str | None = None,
    extra: dict | None = None,
) -> None:
    """
    Best-effort realtime badge invalidation.

    Django remains the source of truth for counts; Nest owns connected sockets.
    If Nest is unavailable or internal env is not configured, the mutating API
    must still succeed and clients will refresh on focus/fallback events.
    """
    base = getattr(settings, "NEST_INTERNAL_URL", "").rstrip("/")
    token = getattr(settings, "NEST_INTERNAL_TOKEN", "")
    clean_user_ids = sorted({str(user_id) for user_id in user_ids if user_id})
    if not base or not token or not clean_user_ids:
        return

    url = f"{base}/main-tab-badges/updated"
    payload = {
        "event": MAIN_TAB_BADGES_UPDATED_EVENT,
        "userIds": clean_user_ids,
        "source": str(source or "unknown"),
        "reason": reason or "",
        "extra": extra or {},
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        **sign_internal_request("POST", url, payload, secret=token),
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            resp.read()
    except Exception as exc:
        logger.debug("Unable to emit main tab badge realtime event: %s", exc)
