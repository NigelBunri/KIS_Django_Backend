# apps/broadcasts/media_hooks.py
"""
Registers apps.broadcasts' Education domain rules onto the apps.media
purpose registry — Phase 3 of the Education System cleanup project,
following the same pattern as apps/statuses/media_hooks.py and
apps/commerce/media_hooks.py. Called once from apps/broadcasts/apps.py's
AppConfig.ready().

All three education purposes are create/update-with-media (allow_attach=
False, see apps/media/purposes.py) — media is bound to its owning row at
save time (apps.broadcasts.education_media.bind_education_media), not
attached to a pre-existing target via the generic attach endpoint — so
only access_authorizer is registered here, matching status's pattern
exactly (no target_authorizer/attach_handler for allow_attach=False
purposes).
"""

from __future__ import annotations

from django.apps import apps as django_apps

from apps.media.services.access import AccessDecision

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


def register() -> None:
    from apps.media import purposes

    purposes.register_access_authorizer("education_institution_logo", can_view_education_branding_media)
    purposes.register_access_authorizer("education_module_cover_image", can_view_education_branding_media)
    purposes.register_access_authorizer("education_material", can_view_education_material)
