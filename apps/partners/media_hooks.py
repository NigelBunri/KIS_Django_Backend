# apps/partners/media_hooks.py
"""Registers apps.partners' domain rules onto the apps.media purpose
registry — same shape as apps.tasks.media_hooks. Called once from
apps/partners/apps.py's AppConfig.ready()."""
from __future__ import annotations

from apps.media.services.access import AccessDecision


def can_view_partner_resource_media(user, asset) -> AccessDecision:
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("authentication_required")
    if not asset.target_id:
        return AccessDecision.deny("not_found")

    from .models import PartnerResource
    from .services import partner_user_can_access

    resource = PartnerResource.objects.filter(id=asset.target_id).select_related("partner").first()
    if not resource:
        return AccessDecision.deny("not_found")
    if partner_user_can_access(resource.partner, user):
        return AccessDecision.allow()
    return AccessDecision.deny("not_authorized")


def register() -> None:
    from apps.media import purposes

    purposes.register_access_authorizer("partner_resource", can_view_partner_resource_media)
