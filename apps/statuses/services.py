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


def get_blocked_user_ids(user) -> set[str]:
    from apps.moderation.models import UserBlock

    blocked_by_me = UserBlock.objects.filter(blocker=user).values_list("blocked_id", flat=True)
    blocked_me = UserBlock.objects.filter(blocked=user).values_list("blocker_id", flat=True)
    return {str(value) for value in blocked_by_me} | {str(value) for value in blocked_me}
