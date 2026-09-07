# apps/broadcasts/media_hooks.py
"""
Registers apps.broadcasts' Education domain rules, plus the channel-content-
video purpose, onto the apps.media purpose registry — Education is Phase 3
of the Education System cleanup project, following the same pattern as
apps/statuses/media_hooks.py and apps/commerce/media_hooks.py. Called once
from apps/broadcasts/apps.py's AppConfig.ready().

All four purposes here are create/update-with-media (allow_attach=False,
see apps/media/purposes.py) — media is bound to its owning row at save
time, not attached to a pre-existing target via the generic attach
endpoint — so only access_authorizer is registered here, matching status's
pattern exactly (no target_authorizer/attach_handler for allow_attach=
False purposes). channel_content_video goes further than the education
purposes: it never resolves a target at ALL through this system (see
can_view_channel_content_video's own docstring below).
"""

from __future__ import annotations

from django.apps import apps as django_apps

from apps.media.services.access import AccessDecision

from apps.media.models import MediaAsset

from .models import EducationInstitution, EducationInstitutionMembershipStatus


def _institution_from_target(target_type: str, target_id: str) -> EducationInstitution | None:
    if not target_type or not target_id:
        return None
    if target_type == "broadcasts.EducationInstitution":
        return EducationInstitution.objects.filter(id=target_id).first()
    try:
        model = django_apps.get_model(target_type)
    except LookupError:
        return None
    obj = model.objects.select_related("institution").filter(id=target_id).first()
    return getattr(obj, "institution", None) if obj else None


def _user_belongs_to_institution(user, institution: EducationInstitution) -> bool:
    if institution.owner_id == user.id:
        return True
    return institution.memberships.filter(
        user=user, status=EducationInstitutionMembershipStatus.ACTIVE,
    ).exists()


def can_view_education_branding_media(user, asset) -> AccessDecision:
    """Institution logo / module cover images — public_catalog visibility
    (see purposes.py): any authenticated user may view an active
    institution's branding, matching how institution/program/course
    listings are already publicly browsable in the Education discover
    pages. An inactive institution's branding is only visible to its own
    members (owner/staff/students) — same rule
    _get_education_institution_or_404 already applies to every other
    institution-scoped read."""
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("authentication_required")
    institution = _institution_from_target(asset.target_type, asset.target_id)
    if institution is None:
        return AccessDecision.deny("not_found")
    if institution.is_active:
        return AccessDecision.allow()
    if _user_belongs_to_institution(user, institution):
        return AccessDecision.allow()
    return AccessDecision.deny("not_authorized")


def can_view_education_material(user, asset) -> AccessDecision:
    """Material resource attachments — restricted visibility: only members
    of the owning institution, matching
    EducationInstitutionMaterialListView.get's existing membership check."""
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("authentication_required")
    institution = _institution_from_target(asset.target_type, asset.target_id)
    if institution is None:
        return AccessDecision.deny("not_found")
    if _user_belongs_to_institution(user, institution):
        return AccessDecision.allow()
    return AccessDecision.deny("not_authorized")


def can_view_channel_content_video(user, asset: MediaAsset) -> AccessDecision:
    """channel_content_video never goes through the generic attach flow at
    all - ChannelContentAssetUploadView binds a client-supplied storage_path
    directly onto a ChannelContentAsset row, never calling attach_media()/
    resolve_confirmed_intent() the way every other confirm-only purpose
    here does. So this MediaAsset's own target_type/target_id are always
    empty; there is no downstream row to resolve a real visibility rule
    from at this layer. The actual public/private visibility a viewer sees
    is entirely governed by ChannelContentAsset's own serializer
    (apps.broadcasts.serializers.ChannelContentAssetSerializer re-signs a
    fresh URL per read) and ChannelContent's own publish/visibility state -
    completely separate from this generic media-asset access check.
    can_user_access_media() already allows the owner unconditionally before
    ever reaching this function (see apps/media/services/access.py) - this
    only decides the non-owner case, and denies it: there is no scenario
    where a non-owner should download the RAW MediaAsset via the generic
    /api/v1/media/assets/{id}/... endpoints for this purpose."""
    return AccessDecision.deny("not_authorized")


def register() -> None:
    from apps.media import purposes

    purposes.register_access_authorizer("education_institution_logo", can_view_education_branding_media)
    purposes.register_access_authorizer("education_module_cover_image", can_view_education_branding_media)
    purposes.register_access_authorizer("education_material", can_view_education_material)
    purposes.register_access_authorizer("channel_content_video", can_view_channel_content_video)
