# apps/statuses/media_hooks.py
"""
Registers apps.statuses' domain rules onto the apps.media purpose registry
— Phase 2 of the KIS Universal Media Platform. Called once from
apps/statuses/apps.py's AppConfig.ready().

Status media has no attach_handler (see apps/media/purposes.py's
status_image/video/audio entries: allow_attach=False) — media is bound to
a StatusItem at creation time (StatusCreateSerializer.create(), Phase 1B),
not attached to a pre-existing target, so there's nothing for the generic
attach endpoint to dispatch to. Only access_authorizer is registered here,
reusing apps.statuses.services.can_view_status — the exact function
StatusViewSet's own visibility checks (list/search/mark_view/media_url)
already run, not a second implementation of status privacy rules.
"""

from __future__ import annotations

from apps.media.services.access import AccessDecision

from .models import StatusItem
from .services import can_view_status, get_blocked_user_ids


def can_view_status_media(user, asset) -> AccessDecision:
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("authentication_required")

    if not asset.target_id:
        # Not yet attached to any StatusItem (upload confirmed but no
        # status created from it yet) — only the owner can reach this via
        # can_user_access_media's owner shortcut; anyone else has nothing
        # to authorize against.
        return AccessDecision.deny("not_found")

    try:
        status_item = (
            StatusItem.objects.select_related("user")
            .prefetch_related("audience_targets")
            .get(id=asset.target_id)
        )
    except (StatusItem.DoesNotExist, ValueError, TypeError):
        return AccessDecision.deny("not_found")

    if not status_item.is_active():
        return AccessDecision.deny("not_available")

    blocked_user_ids = get_blocked_user_ids(user)
    allowed = can_view_status(status_item, viewer_id=str(user.id), blocked_user_ids=blocked_user_ids)
    return AccessDecision.allow() if allowed else AccessDecision.deny("not_authorized")


def register() -> None:
    from apps.media import purposes

    for name in ("status_image", "status_video", "status_audio"):
        purposes.register_access_authorizer(name, can_view_status_media)
