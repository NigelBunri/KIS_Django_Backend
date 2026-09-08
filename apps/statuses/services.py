# apps/statuses/services.py
"""
Status visibility logic shared between StatusViewSet (existing REST routes)
and apps.media's generic access chokepoint (Phase 2 of the KIS Universal
Media Platform — see apps/statuses/media_hooks.py).

can_view_status() is extracted verbatim from
StatusViewSet._can_view_status(), which never actually touched `self` — it
was already a pure function of its arguments. The ViewSet method is now a
thin wrapper delegating here, so every existing call site (list, search,
mark_view, media_url, ...) keeps working unchanged, exercised by the same
StatusPrivacyContractTests that already covered this logic.
"""

from __future__ import annotations

from datetime import timedelta

from apps.accounts.models import UserContact

from .models import StatusItem, StatusModerationStatus, StatusVisibility


def can_view_status(
    status_item: StatusItem,
    *,
    viewer_id: str,
    blocked_user_ids: set[str],
    author_contact_ids: dict[str, set[str]] | None = None,
    mutual_contact_ids: set[str] | None = None,
) -> bool:
    author_id = str(status_item.user_id)
    if author_id == viewer_id:
        return True
    # Content-safety gate, centralized here so every read path that calls
    # this function (list, search, mark_view, media_url) gets it
    # automatically: a status still awaiting an async scan (video) or one
    # a scan actually flagged must never be visible to anyone but its own
    # author, who already returned True above. This is what makes
    # "quarantined content cannot silently become publicly visible" true
    # for the async (video) path — a synchronously-scanned image/audio/
    # text status never reaches this point in a non-PASSED state at all
    # (StatusCreateSerializer rejects it before the row is even created).
    if status_item.moderation_status in (
        StatusModerationStatus.PENDING_REVIEW,
        StatusModerationStatus.BLOCKED,
    ):
        return False
    if author_id in blocked_user_ids or viewer_id in blocked_user_ids:
        return False

    target_ids = {
        str(target.target_user_id)
        for target in getattr(status_item, "_prefetched_objects_cache", {}).get("audience_targets", [])
    }
    if not target_ids:
        target_ids = {
            str(value)
            for value in status_item.audience_targets.values_list("target_user_id", flat=True)
        }

    contacts_for_author = (author_contact_ids or {}).get(author_id)
    if contacts_for_author is None:
        contacts_for_author = set(
            str(value)
            for value in UserContact.objects.filter(
                user_id=author_id,
                contact_user__isnull=False,
            ).values_list("contact_user_id", flat=True)
        )

    if status_item.visibility == StatusVisibility.CONTACTS:
        return viewer_id in contacts_for_author
    if status_item.visibility == StatusVisibility.CONTACTS_EXCEPT:
        return viewer_id in contacts_for_author and viewer_id not in target_ids
    if status_item.visibility == StatusVisibility.ONLY_SHARE_WITH:
        return viewer_id in target_ids
    return False


def purge_expired_statuses(*, limit: int = 500, grace_days: int = 7) -> dict:
    """Manual/cron fallback for hard-deleting old StatusItem rows and their
    media - mirrors apps/media/management/commands/expire_media_uploads.py's
    documented purpose: this exists for deployments that don't run Celery
    Beat (this repo has no confirmed-running Beat schedule at all - see the
    Phase 4 report), not as a second cleanup mechanism competing with one.

    Before this, expires_at and is_deleted were read-only filters used by
    every query in this app but nothing ever actually removed a row or its
    file once either condition was true - status rows and media accumulate
    forever. Two buckets, both purged the same way (file removed best-
    effort, then the row hard-deleted, cascading away its
    StatusItemView/StatusAudienceTarget rows):

    - Already soft-deleted (is_deleted=True, via the owner-delete flow in
      StatusViewSet.destroy) - their content is already gone from every
      read path, so nothing is lost by removing the tombstone row too.
    - Expired more than `grace_days` ago - already invisible to every read
      path (expires_at is filtered everywhere), so the grace period isn't
      protecting visibility, just giving a window before the row/file are
      irreversibly gone (matches the same grace-period convention
      expire_media_uploads.py uses for unattached intents).
    """
    from django.db.models import Q
    from django.utils import timezone

    cutoff = timezone.now() - timedelta(days=grace_days)
    candidates = StatusItem.objects.filter(
        Q(is_deleted=True) | Q(expires_at__lte=cutoff)
    ).order_by("created_at")[:limit]

    purged = 0
    file_cleanup_failures = 0
    for item in candidates:
        if item.file:
            try:
                item.file.delete(save=False)
            except Exception:
                file_cleanup_failures += 1
        item.delete()
        purged += 1

    return {"purged_count": purged, "file_cleanup_failures": file_cleanup_failures}


def get_blocked_user_ids(user) -> set[str]:
    from apps.moderation.models import UserBlock

    blocked_by_me = UserBlock.objects.filter(blocker=user).values_list("blocked_id", flat=True)
    blocked_me = UserBlock.objects.filter(blocked=user).values_list("blocker_id", flat=True)
    return {str(value) for value in blocked_by_me} | {str(value) for value in blocked_me}


class StatusReplyDeliveryError(Exception):
    """Raised when Nest.js could not be reached, or rejected the request, to
    actually create the reply message. Distinct from a plain ValidationError
    so the view can respond 502 (upstream failure) rather than 400/403
    (caller's fault)."""


def deliver_status_reply_message(*, conversation_id: str, sender_id: str, text: str) -> dict:
    """Creates the actual reply message via Nest.js/MongoDB — the only place
    real chat content lives (Django has no Message model at all; see
    RealtimeInternalController.sendMessageAsUser on the Nest side). This is
    a genuine synchronous request/response call, not fire-and-forget: the
    caller needs the real message_id back, and a failure here must be a
    real failure, not a silently-swallowed one, since the alternative
    (pretending the reply succeeded when nothing was actually delivered)
    would be worse than a visible error.

    Reuses the same HMAC-signed internal-request scheme every other
    Django->Nest internal call in this codebase already uses (see
    apps.chat.internal_signing.sign_internal_request), against
    RealtimeInternalController's InternalAuthGuard on the Nest side.
    """
    import json
    import urllib.error
    import urllib.request

    from django.conf import settings

    from apps.chat.internal_signing import sign_internal_request

    base = str(getattr(settings, "NEST_INTERNAL_URL", "")).strip().rstrip("/")
    token = str(getattr(settings, "NEST_INTERNAL_TOKEN", "")).strip()
    if not base or not token:
        raise StatusReplyDeliveryError("Reply delivery is not configured.")

    # RealtimeInternalController is @Controller('internal') on the Nest
    # side - missing this prefix 404s (confirmed via a real production
    # test during this session's closure verification: B's reply to A's
    # status returned a 502 from Django wrapping a 404 from Nest).
    url = f"{base}/internal/messages/send-as-user"
    payload = {
        "conversationId": str(conversation_id),
        "senderId": str(sender_id),
        "text": text,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        **sign_internal_request("POST", url, payload, secret=token),
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise StatusReplyDeliveryError("Could not reach the messaging service.") from exc

    try:
        result = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StatusReplyDeliveryError("Messaging service returned an invalid response.") from exc

    if not result.get("ok"):
        raise StatusReplyDeliveryError("Messaging service rejected the reply.")
    return result
