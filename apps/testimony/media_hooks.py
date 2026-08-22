# apps/testimony/media_hooks.py
"""
Registers the testimony_media purpose's access_authorizer onto the
apps.media purpose registry — same pattern as apps/broadcasts/media_hooks.py
(education_material). Called once from apps/testimony/apps.py's
AppConfig.ready().

allow_attach=False (see apps/media/purposes.py): media is bound to its
owning UserTestimony row at save time
(UserTestimonySerializer._apply_attachment -> lifecycle.sync_attachment),
not attached to a pre-existing target via the generic attach endpoint — so
only access_authorizer is registered here.
"""

from __future__ import annotations

from apps.media.services.access import AccessDecision

from .models import UserTestimony


def can_view_testimony_media(user, asset) -> AccessDecision:
    """Testimonies are public content by design — TestimonyListCreateView's
    GET is IsAuthenticatedOrReadOnly, so even anonymous visitors can browse
    testimonies today. A testimony's own author can always view their
    attachment; anyone else only once the testimony is actually available
    (is_available=True — the same flag that already governs whether it
    shows up in the public listing)."""
    testimony = UserTestimony.objects.filter(id=asset.target_id).first()
    if testimony is None:
        return AccessDecision.deny("not_found")
    if user is not None and getattr(user, "is_authenticated", False) and testimony.user_id == user.id:
        return AccessDecision.allow()
    if testimony.is_available:
        return AccessDecision.allow()
    return AccessDecision.deny("not_authorized")


def register() -> None:
    from apps.media import purposes

    purposes.register_access_authorizer("testimony_media", can_view_testimony_media)
