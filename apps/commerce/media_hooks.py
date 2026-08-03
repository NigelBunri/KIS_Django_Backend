# apps/commerce/media_hooks.py
"""
Registers apps.commerce's domain rules onto the apps.media purpose
registry — Phase 2 of the KIS Universal Media Platform. Called once from
apps/commerce/apps.py's AppConfig.ready().

Attach handlers here are thin adapters over the EXISTING attach_* functions
in apps/commerce/media_uploads.py — not a second implementation. Each
resolves the confirmed MediaAsset's source MediaUploadIntent and calls the
exact same function the legacy /commerce/.../attach/ endpoints already
call (Pattern B: both routes share one underlying handler, so a bug fix or
behavior change in the underlying function is automatically picked up by
both).

Target authorizers reuse media_uploads._shop_owner_or_staff — the same
ownership rule _resolve_target_for_initiate already enforces at initiate
time — re-checked here at attach time as defense in depth, exactly like
the legacy attach_* functions already do.
"""

from __future__ import annotations

from rest_framework.exceptions import NotFound, PermissionDenied

from apps.media.services.access import AccessDecision

from . import media_uploads
from .models import MarketplaceComplaint, Product, Shop, ShopService
from .services import _provider_can_manage_shop

# --------------------------------------------------------------------------
# authorize_target — may an authenticated user attach media onto this
# specific shop/product/service instance?
# --------------------------------------------------------------------------

def authorize_shop_target(*, user, target_type, target_id):
    shop = Shop.objects.filter(id=target_id).first()
    if not shop:
        raise NotFound("Shop not found.")
    if not media_uploads._shop_owner_or_staff(user, shop):
        raise PermissionDenied("Only shop owners or staff can attach a shop image.")


def authorize_product_target(*, user, target_type, target_id):
    product = Product.objects.select_related("shop").filter(id=target_id).first()
    if not product:
        raise NotFound("Product not found.")
    if not media_uploads._shop_owner_or_staff(user, product.shop):
        raise PermissionDenied("Only product owners or staff can modify listings.")


def authorize_service_target(*, user, target_type, target_id):
    service = ShopService.objects.select_related("shop").filter(id=target_id).first()
    if not service:
        raise NotFound("Service not found.")
    if not media_uploads._shop_owner_or_staff(user, service.shop):
        raise PermissionDenied("Only shop owners or staff can modify services.")


# --------------------------------------------------------------------------
# attach_handler — thin adapters over the existing attach_* functions.
# --------------------------------------------------------------------------

def attach_shop_image_generic(*, user, asset, target_type, target_id, slot=None, position=None):
    intent = asset.source_intent
    media_uploads.attach_shop_image(user=user, shop_id=target_id, media_id=str(intent.id))


def attach_product_main_image_generic(*, user, asset, target_type, target_id, slot=None, position=None):
    intent = asset.source_intent
    media_uploads.attach_product_main_image(user=user, product_id=target_id, media_id=str(intent.id))


def attach_product_gallery_image_generic(*, user, asset, target_type, target_id, slot=None, position=None):
    intent = asset.source_intent
    media_uploads.attach_product_gallery_image(user=user, product_id=target_id, media_id=str(intent.id))


def attach_service_image_generic(*, user, asset, target_type, target_id, slot=None, position=None):
    intent = asset.source_intent
    media_uploads.attach_service_image(user=user, service_id=target_id, media_id=str(intent.id))


def attach_service_gallery_image_generic(*, user, asset, target_type, target_id, slot=None, position=None):
    intent = asset.source_intent
    media_uploads.attach_service_gallery_image(user=user, service_id=target_id, media_id=str(intent.id))


# --------------------------------------------------------------------------
# access_authorizer — catalog images: authenticated, and either the target
# is still active/public in the catalog or the viewer owns/staffs it. This
# preserves current behavior: apps/commerce/views.py's ShopViewSet/
# ProductViewSet have no explicit permission_classes and so fall back to
# DEFAULT_PERMISSION_CLASSES=IsAuthenticated (config/settings/base.py) —
# there is no anonymous marketplace browsing to preserve today, confirmed
# by that default and by ShopServiceViewSet/MarketplaceComplaintViewSet
# both explicitly requiring IsAuthenticated too. See the Phase 2 report's
# "public catalog delivery decision" section.
# --------------------------------------------------------------------------

def can_view_shop_image(user, asset) -> AccessDecision:
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("authentication_required")
    shop = Shop.objects.filter(id=asset.target_id).first() if asset.target_id else None
    if shop is None:
        return AccessDecision.deny("not_found")
    if shop.status == Shop.STATUS_ACTIVE or media_uploads._shop_owner_or_staff(user, shop):
        return AccessDecision.allow()
    return AccessDecision.deny("not_authorized")


def can_view_product_image(user, asset) -> AccessDecision:
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("authentication_required")
    product = Product.objects.select_related("shop").filter(id=asset.target_id).first() if asset.target_id else None
    if product is None:
        return AccessDecision.deny("not_found")
    if product.shop.status == Shop.STATUS_ACTIVE or media_uploads._shop_owner_or_staff(user, product.shop):
        return AccessDecision.allow()
    return AccessDecision.deny("not_authorized")


def can_view_service_image(user, asset) -> AccessDecision:
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("authentication_required")
    service = ShopService.objects.select_related("shop").filter(id=asset.target_id).first() if asset.target_id else None
    if service is None:
        return AccessDecision.deny("not_found")
    if service.shop.status == Shop.STATUS_ACTIVE or media_uploads._shop_owner_or_staff(user, service.shop):
        return AccessDecision.allow()
    return AccessDecision.deny("not_authorized")


def can_view_complaint_media(user, asset) -> AccessDecision:
    """Buyer or authorized provider staff — the same rule
    MarketplaceComplaintViewSet.get_queryset() and attach_complaint_media()
    already enforce."""
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("authentication_required")
    complaint = (
        MarketplaceComplaint.objects.select_related("order", "order__shop").filter(id=asset.target_id).first()
        if asset.target_id else None
    )
    if complaint is None:
        return AccessDecision.deny("not_found")
    if complaint.user_id == user.id or _provider_can_manage_shop(user, complaint.order.shop):
        return AccessDecision.allow()
    return AccessDecision.deny("not_authorized")


def register() -> None:
    from apps.media import purposes

    purposes.register_target_authorizer("commerce_shop_image", authorize_shop_target)
    purposes.register_attach_handler("commerce_shop_image", attach_shop_image_generic)
    purposes.register_access_authorizer("commerce_shop_image", can_view_shop_image)

    purposes.register_target_authorizer("commerce_product_main_image", authorize_product_target)
    purposes.register_attach_handler("commerce_product_main_image", attach_product_main_image_generic)
    purposes.register_access_authorizer("commerce_product_main_image", can_view_product_image)

    purposes.register_target_authorizer("commerce_product_gallery_image", authorize_product_target)
    purposes.register_attach_handler("commerce_product_gallery_image", attach_product_gallery_image_generic)
    purposes.register_access_authorizer("commerce_product_gallery_image", can_view_product_image)

    purposes.register_target_authorizer("commerce_service_image", authorize_service_target)
    purposes.register_attach_handler("commerce_service_image", attach_service_image_generic)
    purposes.register_access_authorizer("commerce_service_image", can_view_service_image)

    purposes.register_target_authorizer("commerce_service_gallery_image", authorize_service_target)
    purposes.register_attach_handler("commerce_service_gallery_image", attach_service_gallery_image_generic)
    purposes.register_access_authorizer("commerce_service_gallery_image", can_view_service_image)

    # No target authorizer / attach handler: commerce_complaint_attachment
    # is create-with-media (attach_complaint_media creates the
    # MarketplaceComplaint itself) — see apps/media/purposes.py.
    purposes.register_access_authorizer("commerce_complaint_attachment", can_view_complaint_media)
