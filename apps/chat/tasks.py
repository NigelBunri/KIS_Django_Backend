import json
import logging
import urllib.request

from celery import shared_task
from django.conf import settings

from .internal_signing import sign_internal_request

logger = logging.getLogger(__name__)


def _post_to_nest(path: str, payload: dict) -> None:
    base = getattr(settings, "NEST_INTERNAL_URL", "").rstrip("/")
    # Nest's InternalAuthGuard checks incoming X-Internal-Auth against its
    # own DJANGO_INTERNAL_TOKEN env var - the same shared secret already
    # used symmetrically for the Nest->Django direction (see
    # apps/chat/internal_auth.py). NEST_INTERNAL_TOKEN is a separate,
    # unrelated setting that Nest's codebase never reads at all - using it
    # here always failed with 401 invalid_token (confirmed via a real
    # production test during this session's closure verification).
    token = getattr(settings, "DJANGO_INTERNAL_TOKEN", "")
    if not base or not token:
        logger.warning("[chat.tasks] Missing NEST_INTERNAL_URL or DJANGO_INTERNAL_TOKEN; skipping notify")
        return

    # RealtimeInternalController is mounted at @Controller('internal') on
    # the Nest side - every route path passed to this helper (conversations/
    # created, users/:id/purge-messages, conversations/.../moderate-delete)
    # is relative to that prefix, which must be added here since NEST_INTERNAL_URL
    # itself is just the bare host:port (see apps/broadcasts/views.py's
    # notify_kisvideo webhook caller for the one call site that already got
    # this right - this was the only one that didn't, confirmed via a real
    # production 404 during Phase 4 status-reply E2E testing).
    url = f"{base}/internal/{path.lstrip('/')}"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        **sign_internal_request("POST", url, payload, secret=token),
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=3) as resp:
        resp.read()


def notify_nest_conversation_created(conversation_id: str, user_ids: list[str]) -> None:
    payload = {
        "conversationId": str(conversation_id),
        "userIds": [str(uid) for uid in user_ids],
    }
    _post_to_nest("conversations/created", payload)


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def notify_nest_conversation_created_task(self, conversation_id: str, user_ids: list[str]) -> None:
    try:
        notify_nest_conversation_created(conversation_id, user_ids)
    except Exception as exc:
        raise self.retry(exc=exc)
