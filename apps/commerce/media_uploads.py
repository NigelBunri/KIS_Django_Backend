# apps/commerce/media_uploads.py
"""
Marketplace-specific wiring on top of the shared direct-to-S3 presigned
upload handshake (apps/media/upload_intent.py).

Two-step design, matching the note left in upload_intent.py's deferred
list this replaces: commerce ownership is never a plain
`obj.owner == request.user` check, so a generic confirm step can't safely
attach media to a shop/product/service/complaint by itself. Instead:

  1. initiate_commerce_upload() — validates `purpose` and resolves +
     authorizes the *target* (shop/product/service/order) using the exact
     same rules the old multipart endpoints already enforced, THEN creates
     a MediaUploadIntent via the shared upload_intent module.
  2. POST /api/v1/media/uploads/<uuid:upload_id>/confirm/ (existing,
     unmodified route) verifies the S3 object and returns a stable
     `mediaId` — see _confirm_only_media_descriptor in upload_intent.py.
  3. One of the attach_*() functions below re-validates the SAME
     per-target authorization (defense in depth — initiate-time and
     attach-time can be minutes apart) and binds the confirmed object key
     onto the real model field, exactly the way _attach_profile_image does
     (`field.name = object_key`, never re-uploading the bytes).

A MediaUploadIntent can only be attached once — resolve_confirmed_media()
enforces `attached_at IS NULL`, so the same confirmed upload can never be
replayed onto a second target.
"""

from __future__ import annotations

from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.tiers import get_user_tier_features, normalize_limit_value
from apps.media import upload_intent
from apps.media.models import MediaUploadIntent
from apps.media.services import lifecycle

from .models import (
    MarketplaceComplaint,
    MarketplaceOrder,
    MarketplaceOrderStatus,
    Product,
    ProductImage,
    Shop,
    ShopService,
    ShopServiceImage,
)
from .services import _provider_can_manage_shop

# Client-facing `purpose` -> upload_intent `context`. Kept as a distinct
# mapping (rather than using the context string directly as the wire value)
# so the commerce API's vocabulary can stay stable even if internal context
# naming ever changes.
PURPOSE_TO_CONTEXT = {
    "shop_logo": "commerce_shop_image",
    "product_main_image": "commerce_product_main_image",
    "product_gallery_image": "commerce_product_gallery_image",
    "service_image": "commerce_service_image",
    "service_gallery_image": "commerce_service_gallery_image",
    "complaint_attachment": "commerce_complaint_attachment",
}

PRODUCT_GALLERY_MAX_IMAGES_DEFAULT = 10
SERVICE_GALLERY_MAX_IMAGES_DEFAULT = 10


def _service_gallery_max_images() -> int:
    from django.conf import settings

    return int(getattr(settings, "COMMERCE_SERVICE_GALLERY_MAX_IMAGES", SERVICE_GALLERY_MAX_IMAGES_DEFAULT))


def _product_gallery_max_images() -> int:
    from django.conf import settings

    return int(getattr(settings, "COMMERCE_PRODUCT_GALLERY_MAX_IMAGES", PRODUCT_GALLERY_MAX_IMAGES_DEFAULT))


def _shop_owner_or_staff(user, shop: Shop) -> bool:
    return shop.owner_id == user.id or bool(getattr(user, "is_staff", False))


# --------------------------------------------------------------------------
# Target resolution + authorization (initiate time)
# --------------------------------------------------------------------------

def _resolve_target_for_initiate(*, user, purpose: str, shop_id, product_id, service_id, order_id) -> str:
    """Returns the target_id to store on the intent (empty string if the
    target doesn't exist yet, e.g. a brand-new shop/product/service being
    created). Raises NotFound/PermissionDenied/ValidationError exactly like
    the equivalent multipart view used to."""
    if purpose == "shop_logo":
        if not shop_id:
            # Creating a brand-new shop — nothing to authorize against yet;
            # ShopViewSet.perform_create already enforces the shop-limit
            # check when the shop itself is actually created.
            return ""
        shop = Shop.objects.filter(id=shop_id).first()
        if not shop:
            raise NotFound("Shop not found.")
        if not _shop_owner_or_staff(user, shop):
            raise PermissionDenied("Only shop owners or staff can upload a shop image.")
        return str(shop.id)

    if purpose == "product_main_image" or purpose == "product_gallery_image":
        if product_id:
            product = Product.objects.select_related("shop").filter(id=product_id).first()
            if not product:
                raise NotFound("Product not found.")
            if not _shop_owner_or_staff(user, product.shop):
                raise PermissionDenied("Only product owners or staff can upload product images.")
            return str(product.id)
        # New product not created yet — must at least own the shop it will
        # belong to (mirrors ProductViewSet.perform_create's check).
        if not shop_id:
            raise ValidationError({"shopId": "shopId or productId is required."})
        shop = Shop.objects.filter(id=shop_id).first()
        if not shop:
            raise NotFound("Shop not found.")
        if shop.owner_id != user.id:
            raise PermissionDenied("You can only add products to your own shop.")
        return ""

    if purpose == "service_image" or purpose == "service_gallery_image":
        if service_id:
            service = ShopService.objects.select_related("shop").filter(id=service_id).first()
            if not service:
                raise NotFound("Service not found.")
            if not _shop_owner_or_staff(user, service.shop):
                raise PermissionDenied("Only shop owners or staff can upload service images.")
            return str(service.id)
        if not shop_id:
            raise ValidationError({"shopId": "shopId or serviceId is required."})
        shop = Shop.objects.filter(id=shop_id).first()
        if not shop:
            raise NotFound("Shop not found.")
        if not _shop_owner_or_staff(user, shop):
            raise PermissionDenied("Only shop owners or staff can create services.")
        return ""

    if purpose == "complaint_attachment":
        if not order_id:
            raise ValidationError({"orderId": "orderId is required for a complaint attachment."})
        order = MarketplaceOrder.objects.select_related("shop").filter(id=order_id).first()
        if not order:
            raise NotFound("Order not found.")
        if order.buyer_id != user.id and not _provider_can_manage_shop(user, order.shop):
            raise PermissionDenied("You are not authorized to attach evidence to this order.")
        return str(order.id)

    raise ValidationError({"purpose": "Unknown or unsupported upload purpose."})


def initiate_commerce_upload(
    *,
    user,
    purpose: str,
    filename: str,
    content_type: str,
    size_bytes,
    shop_id: str | None = None,
    product_id: str | None = None,
    service_id: str | None = None,
    order_id: str | None = None,
) -> dict:
    context = PURPOSE_TO_CONTEXT.get(purpose)
    if not context:
        raise ValidationError({"purpose": "Unknown or unsupported upload purpose."})

    target_id = _resolve_target_for_initiate(
        user=user, purpose=purpose, shop_id=shop_id, product_id=product_id,
        service_id=service_id, order_id=order_id,
    )

    upload_intent.validate_initiate_payload(
        context=context, filename=filename, content_type=content_type, size_bytes=size_bytes,
    )
    features = get_user_tier_features(user)
    limit_mb = normalize_limit_value(features.get("media_storage_mb"), default=None)
    if limit_mb is not None:
        limit_bytes = int(limit_mb) * 1024 * 1024
        if int(size_bytes) > limit_bytes:
            raise ValidationError({"detail": "Media file exceeds your tier storage limit."})
    intent = upload_intent.create_upload_intent(
        user=user,
        context=context,
        filename=filename,
        content_type=str(content_type).strip().lower(),
        size_bytes=int(size_bytes),
        target_id=target_id,
    )
    return upload_intent.build_initiate_response(intent)


# --------------------------------------------------------------------------
# Confirmed-media resolution (attach time)
# --------------------------------------------------------------------------

def resolve_confirmed_media(*, user, media_id, expected_context: str) -> MediaUploadIntent:
    """Looks up a confirmed, not-yet-attached MediaUploadIntent owned by
    `user` for the expected context. Never trusts a storage key or object
    id supplied by the client for anything other than this opaque
    `media_id` — the actual S3 key is never read from the request."""
    if not media_id:
        raise ValidationError({"mediaId": "mediaId is required."})
    intent = MediaUploadIntent.objects.filter(id=media_id, owner_id=user.id).first()
    if not intent:
        raise NotFound("Confirmed media not found.")
    if intent.context != expected_context:
        raise ValidationError({"mediaId": "This media was not uploaded for this purpose."})
    if intent.status != MediaUploadIntent.STATUS_CONFIRMED:
        raise ValidationError({"mediaId": f"This media is not confirmed (status={intent.status})."})
    if intent.attached_at is not None:
        raise ValidationError({"mediaId": "This media has already been attached and cannot be reused."})
    return intent


def _schedule_old_object_cleanup(previous_name: str | None, new_name: str | None):
    """Best-effort delete of a replaced object, only ever called AFTER the
    new reference has already been saved successfully — mirrors
    _attach_profile_image's sequencing exactly (update DB first, delete old
    object second, never the other way around)."""
    if not previous_name or previous_name == new_name:
        return
    try:
        default_storage.delete(previous_name)
    except Exception:
        # Non-fatal — the DB is already correct; a stray old object is a
        # storage-cost issue for the cleanup sweep, not a correctness one.
        pass


# --------------------------------------------------------------------------
# Attach: shop image
# --------------------------------------------------------------------------

def attach_shop_image(*, user, shop_id, media_id) -> Shop:
    shop = Shop.objects.filter(id=shop_id).first()
    if not shop:
        raise NotFound("Shop not found.")
    if not _shop_owner_or_staff(user, shop):
        raise PermissionDenied("Only shop owners or staff can upload a shop image.")

    intent = resolve_confirmed_media(user=user, media_id=media_id, expected_context="commerce_shop_image")

    with transaction.atomic():
        previous_name = shop.image_file.name if shop.image_file else None
        shop.image_file.name = intent.object_key
        shop.save(update_fields=["image_file", "updated_at"])
        lifecycle.sync_attachment(intent=intent, target_type="commerce.Shop", target_id=str(shop.id))

    _schedule_old_object_cleanup(previous_name, intent.object_key)
    return shop


# --------------------------------------------------------------------------
# Attach: product main image + gallery
# --------------------------------------------------------------------------

def attach_product_main_image(*, user, product_id, media_id) -> Product:
    product = Product.objects.select_related("shop").filter(id=product_id).first()
    if not product:
        raise NotFound("Product not found.")
    if not _shop_owner_or_staff(user, product.shop):
        raise PermissionDenied("Only product owners or staff can modify listings.")

    intent = resolve_confirmed_media(user=user, media_id=media_id, expected_context="commerce_product_main_image")

    with transaction.atomic():
        previous_name = product.main_image.name if product.main_image else None
        product.main_image.name = intent.object_key
        product.save(update_fields=["main_image", "updated_at"])
        lifecycle.sync_attachment(intent=intent, target_type="commerce.Product", target_id=str(product.id))

    _schedule_old_object_cleanup(previous_name, intent.object_key)
    return product


def attach_product_gallery_image(*, user, product_id, media_id) -> ProductImage:
    product = Product.objects.select_related("shop").filter(id=product_id).first()
    if not product:
        raise NotFound("Product not found.")
    if not _shop_owner_or_staff(user, product.shop):
        raise PermissionDenied("Only product owners or staff can modify listings.")

    existing = product.gallery_images.filter(is_active=True)
    if existing.count() >= _product_gallery_max_images():
        raise ValidationError(
            {"detail": f"This product already has the maximum of {_product_gallery_max_images()} gallery images."}
        )

    intent = resolve_confirmed_media(user=user, media_id=media_id, expected_context="commerce_product_gallery_image")

    # Duplicate prevention: the same confirmed upload can only ever attach
    # once anyway (resolve_confirmed_media enforces attached_at IS NULL),
    # but this also guards against the same *object key* somehow appearing
    # twice (e.g. a retried attach call racing itself).
    if existing.filter(image_file=intent.object_key).exists():
        raise ValidationError({"mediaId": "This image is already in the gallery."})

    with transaction.atomic():
        from django.db.models import Max

        max_sort = product.gallery_images.aggregate(m=Max("sort_order")).get("m")
        image = ProductImage.objects.create(
            product=product, image_file=intent.object_key, sort_order=(max_sort or 0) + 1,
        )
        lifecycle.sync_attachment(intent=intent, target_type="commerce.Product", target_id=str(product.id))
    return image


def remove_product_gallery_image(*, user, product_id, image_id) -> None:
    product = Product.objects.select_related("shop").filter(id=product_id).first()
    if not product:
        raise NotFound("Product not found.")
    if not _shop_owner_or_staff(user, product.shop):
        raise PermissionDenied("Only product owners or staff can modify listings.")

    image = product.gallery_images.filter(id=image_id).first()
    if not image:
        raise NotFound("Gallery image not found.")
    object_name = image.image_file.name if image.image_file else None
    image.delete()
    if object_name:
        try:
            default_storage.delete(object_name)
        except Exception:
            pass


def reorder_product_gallery(*, user, product_id, ordered_image_ids: list[str]) -> list[ProductImage]:
    product = Product.objects.select_related("shop").filter(id=product_id).first()
    if not product:
        raise NotFound("Product not found.")
    if not _shop_owner_or_staff(user, product.shop):
        raise PermissionDenied("Only product owners or staff can modify listings.")

    images = {str(img.id): img for img in product.gallery_images.all()}
    ids = [str(i) for i in ordered_image_ids]
    if set(ids) != set(images.keys()):
        raise ValidationError({"order": "Must include exactly the current set of gallery image ids."})

    with transaction.atomic():
        for index, image_id in enumerate(ids):
            image = images[image_id]
            if image.sort_order != index:
                image.sort_order = index
                image.save(update_fields=["sort_order"])
    return list(product.gallery_images.order_by("sort_order", "id"))


# --------------------------------------------------------------------------
# Attach: service image
# --------------------------------------------------------------------------

def attach_service_image(*, user, service_id, media_id) -> ShopService:
    service = ShopService.objects.select_related("shop").filter(id=service_id).first()
    if not service:
        raise NotFound("Service not found.")
    if not _shop_owner_or_staff(user, service.shop):
        raise PermissionDenied("Only shop owners or staff can modify services.")

    intent = resolve_confirmed_media(user=user, media_id=media_id, expected_context="commerce_service_image")

    with transaction.atomic():
        previous_name = service.image_file.name if service.image_file else None
        service.image_file.name = intent.object_key
        service.save(update_fields=["image_file", "updated_at"])
        lifecycle.sync_attachment(intent=intent, target_type="commerce.ShopService", target_id=str(service.id))

    _schedule_old_object_cleanup(previous_name, intent.object_key)
    return service


def attach_service_gallery_image(*, user, service_id, media_id) -> ShopServiceImage:
    service = ShopService.objects.select_related("shop").filter(id=service_id).first()
    if not service:
        raise NotFound("Service not found.")
    if not _shop_owner_or_staff(user, service.shop):
        raise PermissionDenied("Only shop owners or staff can modify services.")

    existing = service.images.all()
    if existing.count() >= _service_gallery_max_images():
        raise ValidationError(
            {"detail": f"This service already has the maximum of {_service_gallery_max_images()} gallery images."}
        )

    intent = resolve_confirmed_media(user=user, media_id=media_id, expected_context="commerce_service_gallery_image")

    if existing.filter(image_file=intent.object_key).exists():
        raise ValidationError({"mediaId": "This image is already in the gallery."})

    with transaction.atomic():
        from django.db.models import Max

        max_order = service.images.aggregate(m=Max("order")).get("m")
        image = ShopServiceImage.objects.create(
            service=service, image_file=intent.object_key, order=(max_order or 0) + 1,
        )
        lifecycle.sync_attachment(intent=intent, target_type="commerce.ShopService", target_id=str(service.id))
    return image


def remove_service_gallery_image(*, user, service_id, image_id) -> None:
    service = ShopService.objects.select_related("shop").filter(id=service_id).first()
    if not service:
        raise NotFound("Service not found.")
    if not _shop_owner_or_staff(user, service.shop):
        raise PermissionDenied("Only shop owners or staff can modify services.")

    image = service.images.filter(id=image_id).first()
    if not image:
        raise NotFound("Gallery image not found.")
    object_name = image.image_file.name if image.image_file else None
    image.delete()
    if object_name:
        try:
            default_storage.delete(object_name)
        except Exception:
            pass


# --------------------------------------------------------------------------
# Attach: complaint attachment
# --------------------------------------------------------------------------

def attach_complaint_media(*, user, order_id, text: str, media_id: str | None) -> MarketplaceComplaint:
    """New confirm-then-attach path for complaint attachments. The legacy
    multipart `attachment=<file>` path on MarketplaceComplaintCreateSerializer
    is untouched for backward compatibility — see
    MarketplaceComplaintViewSet.create."""
    order = MarketplaceOrder.objects.select_related("shop").filter(id=order_id).first()
    if not order:
        raise NotFound("Order not found.")
    if order.buyer_id != user.id and not _provider_can_manage_shop(user, order.shop):
        raise ValidationError("You are not authorized to complain about this order.")

    # Resolve (and validate) the media BEFORE creating anything, so a bad
    # mediaId never leaves behind a complaint with a missing attachment.
    intent = None
    if media_id:
        intent = resolve_confirmed_media(user=user, media_id=media_id, expected_context="commerce_complaint_attachment")

    with transaction.atomic():
        complaint = MarketplaceComplaint.objects.create(order=order, user=user, text=text)
        if intent is not None:
            complaint.attachment.name = intent.object_key
            complaint.save(update_fields=["attachment"])
            lifecycle.sync_attachment(
                intent=intent, target_type="commerce.MarketplaceComplaint", target_id=str(complaint.id),
            )

        # Mirrors create_marketplace_complaint's order-status side effect exactly.
        if order.status != MarketplaceOrderStatus.COMPLAINT:
            order.status = MarketplaceOrderStatus.COMPLAINT
            order.metadata = {**(order.metadata or {}), "complaint_at": timezone.now().isoformat()}
            order.save(update_fields=["status", "metadata"])

    return complaint
