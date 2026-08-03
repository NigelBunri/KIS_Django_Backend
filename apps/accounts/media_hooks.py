# apps/accounts/media_hooks.py
"""
Registers apps.accounts' domain rules onto the apps.media purpose registry
— Phase 2 of the KIS Universal Media Platform. Called once from
apps/accounts/apps.py's AppConfig.ready().

profile_avatar/profile_cover have no attach_handler — they auto-attach at
CONFIRM time (apps/media/upload_intent.py's _attach_profile_avatar/
_attach_profile_cover, unchanged since Phase 0/1) rather than through a
separate attach call, so there's nothing for the generic attach endpoint
to dispatch to for a fresh attach (see apps/media/purposes.py).

access_authorizer allows any viewer, including unauthenticated ones —
this matches CURRENT behavior exactly: ProfileViewSet uses
IsAuthenticatedOrReadOnly with no additional queryset/serializer-level
visibility gate on avatar_file/cover_file, so avatar_url/cover_url are
already unconditionally exposed to anyone who can GET a profile detail
today. Restricting this in the media layer would be a regression, not a
fix — Phase 2's explicit brief is to preserve current visibility.
"""

from __future__ import annotations

from apps.media.services.access import AccessDecision


def can_view_profile_media(user, asset) -> AccessDecision:
    return AccessDecision.allow()


def register() -> None:
    from apps.media import purposes

    purposes.register_access_authorizer("profile_avatar", can_view_profile_media)
    purposes.register_access_authorizer("profile_cover", can_view_profile_media)
