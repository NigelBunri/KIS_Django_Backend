"""
Server-side RBAC permission classes for the Partners app.
These enforce restrictions at the backend level — never rely on frontend-only hiding.
"""
from rest_framework.permissions import BasePermission

from apps.partners.seed import KCAN_PARTNER_SLUG, GO_EMAIL, GO_PHONE


def is_platform_go(user) -> bool:
    """Return True iff the user is the immutable GO (General Overseer)."""
    if not user or not user.is_authenticated:
        return False
    return (
        (getattr(user, 'email', '') or '').lower() == GO_EMAIL.lower()
        or getattr(user, 'phone', '') == GO_PHONE
    )


def is_kcan_admin(user) -> bool:
    """Return True iff the user has platform-level admin access."""
    if not user or not user.is_authenticated:
        return False
    return bool(user.is_superuser or user.is_staff or is_platform_go(user))


class IsKCANAdmin(BasePermission):
    """Only platform superusers / staff / GO can pass."""
    message = "KCAN admin access required."

    def has_permission(self, request, view):
        return is_kcan_admin(request.user)


class IsPartnerOwnerOrAdmin(BasePermission):
    """
    Grants access if the user is a platform admin OR owns / has an admin role
    in the partner being accessed (partner id expected in view.kwargs['pk'] or
    view.kwargs.get('partner_pk')).
    """
    message = "Partner owner or admin access required."

    def has_permission(self, request, view):
        if is_kcan_admin(request.user):
            return True
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if is_kcan_admin(request.user):
            return True
        # obj is a Partner instance
        from apps.partners.models import Partner, PartnerMembership
        if isinstance(obj, Partner):
            if obj.owner_id == request.user.id:
                return True
            membership = PartnerMembership.objects.filter(
                partner=obj, user=request.user, role__in=['admin', 'owner']
            ).first()
            return membership is not None
        return False


class IsPartnerMember(BasePermission):
    """Pass for any authenticated partner member (lowest bar)."""
    message = "You must be a member of this partner."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if is_kcan_admin(request.user):
            return True
        from apps.partners.models import Partner, PartnerMembership
        if isinstance(obj, Partner):
            return PartnerMembership.objects.filter(partner=obj, user=request.user).exists()
        return False


class PartnerScopedPermission(BasePermission):
    """
    Partner Pro scoping: a user may only access their own partner's resources,
    never global / KCAN. Platform GO/admins bypass this.
    """
    message = "You can only manage your own organization's resources."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if is_kcan_admin(request.user):
            return True
        from apps.partners.models import Partner, PartnerMembership
        partner = None
        if isinstance(obj, Partner):
            partner = obj
        elif hasattr(obj, 'partner'):
            partner = obj.partner
        elif hasattr(obj, 'partner_id'):
            try:
                partner = Partner.objects.get(id=obj.partner_id)
            except Partner.DoesNotExist:
                return False

        if partner and partner.slug == KCAN_PARTNER_SLUG:
            # Non-admins can NEVER access KCAN resources
            return False

        if partner:
            return PartnerMembership.objects.filter(
                partner=partner, user=request.user, role__in=['admin', 'owner', 'editor']
            ).exists()
        return False
