# apps/chat/media_hooks.py
"""
Registers apps.chat's domain rules onto the apps.media purpose registry —
same pattern as apps/accounts/media_hooks.py. Called once from
apps/chat/apps.py's AppConfig.ready().

A sticker's entire point is to be reusable in any conversation the saver
is in, including ones the original creator/conversation's other
participants were never part of — so, unlike a normal chat attachment
(gated by AttachmentAccessService's per-conversation membership check in
Nest), access here is intentionally unconditional for any authenticated
KIS user. This matches the zero-ACL precedent the built-in sticker packs
(StickerPackListView) already set: stickers are low-sensitivity,
inherently shareable content, not private media. The underlying S3
bucket stays private either way — this only governs who may ask Django
for a fresh signed GET, never whether the bucket itself is public.
"""

from __future__ import annotations

from apps.media.services.access import AccessDecision


def can_view_sticker_media(user, asset) -> AccessDecision:
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("not_authenticated")
    return AccessDecision.allow()


def register() -> None:
    from apps.media import purposes

    purposes.register_access_authorizer("sticker", can_view_sticker_media)
