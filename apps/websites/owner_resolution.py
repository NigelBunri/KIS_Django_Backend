"""
Generic owner resolution for Website.owner_type/owner_id.

Shop, HealthInstitution, EducationInstitution, and Partner are four
independent top-level models (not BroadcastChannel rows, despite
BroadcastChannel.OwnerType declaring SHOP/HEALTH/EDUCATION/PARTNER
values that are never actually constructed anywhere in this codebase).
No generic "resolve the owner of X" helper exists elsewhere, so this is
the first one — used by every apps.websites view/serializer/adapter that
needs to go from (owner_type, owner_id) to the real object or its owning
User.
"""
from django.apps import apps as django_apps

from apps.websites.models import WebsiteOwnerType

# (app_label, model_name) — resolved lazily via django.apps.apps.get_model
# to avoid import-order coupling with commerce/health_ops/broadcasts/partners.
_OWNER_MODEL_LOOKUP = {
    WebsiteOwnerType.SHOP: ("commerce", "Shop"),
    WebsiteOwnerType.HEALTH_INSTITUTION: ("health_ops", "HealthInstitution"),
    WebsiteOwnerType.EDUCATION_INSTITUTION: ("broadcasts", "EducationInstitution"),
    WebsiteOwnerType.PARTNER: ("partners", "Partner"),
    WebsiteOwnerType.BROADCAST_CHANNEL: ("broadcasts", "BroadcastChannel"),
}


def owner_model_for(owner_type: str):
    entry = _OWNER_MODEL_LOOKUP.get(owner_type)
    if not entry:
        return None
    app_label, model_name = entry
    return django_apps.get_model(app_label, model_name)


def resolve_owner_object(owner_type: str, owner_id):
    model = owner_model_for(owner_type)
    if model is None or not owner_id:
        return None
    return model.objects.filter(pk=owner_id).first()


def resolve_owner_user(owner_type: str, owner_instance):
    """The User who owns `owner_instance`. BroadcastChannel exposes this
    as `owner_user` (it's itself polymorphically owned, unlike the other
    four); everything else uses a plain `owner` FK."""
    if owner_instance is None:
        return None
    if owner_type == WebsiteOwnerType.BROADCAST_CHANNEL:
        return getattr(owner_instance, "owner_user", None)
    return getattr(owner_instance, "owner", None)


def _find_website(owner_type: str, owner_id):
    from apps.websites.models import Website

    return Website.objects.filter(owner_type=owner_type, owner_id=owner_id).first()


def _is_active_collaborator(user, owner_type: str, owner_id, *, role=None) -> bool:
    from apps.websites.models import WebsiteCollaborator

    website = _find_website(owner_type, owner_id)
    if website is None:
        return False
    qs = WebsiteCollaborator.objects.filter(website=website, user=user, is_active=True)
    if role is not None:
        qs = qs.filter(role=role)
    return qs.exists()


def user_can_manage_website(user, owner_type: str, owner_id) -> bool:
    """True for the literal owner OR an active WebsiteCollaborator of
    either role (owner/editor) — everyday editing (pages, sections,
    branding, publish). See user_can_administer_website for the stricter
    owner-only boundary (managing collaborators/invites themselves)."""
    if not getattr(user, "is_authenticated", False):
        return False
    owner_instance = resolve_owner_object(owner_type, owner_id)
    if owner_instance is not None:
        owner_user = resolve_owner_user(owner_type, owner_instance)
        if owner_user is not None and owner_user.id == user.id:
            return True
    return _is_active_collaborator(user, owner_type, owner_id)


def user_can_administer_website(user, owner_type: str, owner_id) -> bool:
    """The literal owner OR a collaborator with role=owner — required to
    manage collaborators/invites. An editor can edit everything on the
    site but can't add/remove other collaborators or generate invites."""
    if not getattr(user, "is_authenticated", False):
        return False
    owner_instance = resolve_owner_object(owner_type, owner_id)
    if owner_instance is not None:
        owner_user = resolve_owner_user(owner_type, owner_instance)
        if owner_user is not None and owner_user.id == user.id:
            return True
    from apps.websites.models import WebsiteCollaboratorRole

    return _is_active_collaborator(user, owner_type, owner_id, role=WebsiteCollaboratorRole.OWNER)
