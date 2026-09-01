# accounts/tasks.py
#
# Thin @shared_task wrapper around the actual purge logic, matching the
# established house pattern (apps.rewards.tasks / apps.billing.tasks).
# Registered in CELERY_BEAT_SCHEDULE, config/settings/base.py.
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .models import GDPRRequest
from .security_events import log_security_event

logger = logging.getLogger(__name__)


def _purge_nest_chat_data(user_id: str) -> None:
    """Best-effort: tells NestJS to scrub this user's Mongo chat messages
    (internal/users/:userId/purge-messages, see RealtimeInternalController
    on the Nest side). Django hard-deleting the user does nothing to that
    data - it lives entirely outside Postgres - so without this call
    "delete account" would leave every message they ever sent fully intact.
    Reuses the same signed internal-call helper apps.chat.tasks already
    uses for Django->Nest calls, rather than a second implementation."""
    from apps.chat.tasks import _post_to_nest

    try:
        _post_to_nest(f"users/{user_id}/purge-messages", {})
    except Exception:
        logger.exception("Failed to purge Nest chat data for purged user %s", user_id)


def purge_accounts_past_grace_period() -> dict:
    """Hard-deletes every account whose grace-period deletion window (see
    apps.accounts.views.schedule_account_deletion) has elapsed. Real logic
    lives here rather than only in the task so it stays independently
    callable/testable without Celery."""
    now = timezone.now()
    due = GDPRRequest.objects.filter(
        type="account_deletion", status="pending", scheduled_for__lte=now,
    ).select_related("user")

    purged = 0
    errors = 0
    for gdpr_request in due:
        user = gdpr_request.user
        try:
            user_id = str(user.id)
            # Mark completed before deleting: GDPRRequest.user is CASCADE, so
            # once the user row is gone this row is gone with it - the
            # AuditLog event below (actor_id is a plain UUID, not an FK) is
            # what actually survives as the durable record of the purge.
            gdpr_request.status = "completed"
            gdpr_request.completed_at = now
            gdpr_request.save(update_fields=["status", "completed_at", "updated_at"])
            user.delete()
            _purge_nest_chat_data(user_id)
            log_security_event(
                None,
                "security.account.deletion_purged",
                severity="warning",
                user_id=user_id,
            )
            purged += 1
        except Exception:
            errors += 1
            logger.exception(
                "Failed to purge account past grace period (gdpr_request_id=%s)",
                gdpr_request.id,
            )

    return {"purged": purged, "errors": errors}


@shared_task
def purge_accounts_past_grace_period_task():
    return purge_accounts_past_grace_period()
