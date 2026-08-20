"""
Live resolution for `kis_content` website sections.

Each resolver takes the section's `target_type` config (explicit
`target_ids` or an auto `filter`) plus the owning Website's
(owner_type, owner_id), and returns a sanitized list of dicts — computed
fresh on every read, never persisted into WebsitePage.sections, which is
what guarantees a website section reflects live KIS state rather than a
stale copy. Every resolver filters to public/published/active rows only,
scoped to the calling owner where the underlying model supports it — a
Shop's website can only link that Shop's own products/services, not
another shop's.

Course/product/shop_service/health_service are owner-scoped (the
underlying models have a direct institution/shop FK). broadcast_channel
and post are looked up by explicit target_id only, filtered to
public+non-deleted (a website can link out to any public channel/post,
not just the owner's own — mirrors how the RN app already lets you
share/embed any public channel). event and testimonial only have a
scoped model for one owner type each today (EducationInstitutionEvent,
ShopLandingTestimonial) — for other owner types they return an empty
list rather than fabricating a source, which is a known, documented
Phase 1 limitation (see resolver docstrings).
"""
from django.apps import apps as django_apps

from apps.commerce.constants import KIS_COIN_CODE
from apps.core.public_web import resolve_stale_media_url, safe_public_description, safe_public_media_url
from apps.websites.models import WebsiteOwnerType

KIS_APP_DEEP_LINK_BASE = "https://kis.app"


def _limit_ids(target_ids, cap=50):
    return [str(i) for i in (target_ids or [])][:cap]


def _resolve_stored_media_url(value: str) -> str:
    """Course/event cover_image_url is a free-text CharField holding one
    of two shapes: a client-pasted external URL, or one of our own S3
    object keys stored verbatim by _education_cover_image_from_payload —
    always "private/<key_prefix>/<user>/<uuid>.<ext>" (see
    apps.media.upload_intent._generate_object_key), never a real URL.
    Passing that raw key straight into safe_public_media_url always
    failed the scheme check (no "http"/"https") and silently dropped the
    image. Mirrors apps.broadcasts.serializers._resolve_education_media_
    display_url's own private/-prefix branch, which is the same fix
    already applied for the authenticated course/institution API."""
    text = str(value or "").strip()
    if not text.startswith("private/"):
        return text
    try:
        from django.core.files.storage import default_storage

        return default_storage.url(text)
    except Exception:
        return text


def resolve_courses(*, owner_type, owner_id, target_ids=None, limit=6, **_):
    if owner_type != WebsiteOwnerType.EDUCATION_INSTITUTION:
        return []
    EducationInstitutionCourse = django_apps.get_model("broadcasts", "EducationInstitutionCourse")
    qs = EducationInstitutionCourse.objects.filter(
        institution_id=owner_id, status="published", visibility="public",
    )
    if target_ids:
        qs = qs.filter(id__in=_limit_ids(target_ids))
    items = []
    for course in qs.order_by("-created_at")[:limit]:
        # Purchasing/enrolling operates on an EducationInstitutionBroadcast
        # (a specific content item under the course), not the course row
        # itself — there is no course-level purchase endpoint in this
        # codebase. Best-effort: the first published, priced broadcast
        # under this course is treated as "the thing you enroll in to buy
        # this course." Courses with no such broadcast yet simply aren't
        # checkout-able from the website (checkout_content_id is null) —
        # the deep_link into the app still works either way.
        enrollment_content_id = None
        primary_broadcast = course.broadcasts.filter(
            status="published",
        ).exclude(price_amount__isnull=True).order_by("created_at").first()
        if primary_broadcast is None:
            primary_broadcast = course.broadcasts.filter(status="published").order_by("created_at").first()
        if primary_broadcast is not None:
            enrollment_content_id = str(primary_broadcast.id)

        items.append({
            "id": str(course.id),
            "title": course.title,
            "description": safe_public_description(course.summary, course.description),
            "image_url": safe_public_media_url(_resolve_stored_media_url(course.cover_image_url)),
            "price_display": "Free" if course.is_free else f"{course.price_amount} {course.price_currency}",
            "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/education/courses/{course.id}",
            "checkout_content_id": enrollment_content_id,
        })
    return items


def resolve_products(*, owner_type, owner_id, target_ids=None, limit=6, **_):
    if owner_type != WebsiteOwnerType.SHOP:
        return []
    Product = django_apps.get_model("commerce", "Product")
    qs = Product.objects.filter(shop_id=owner_id, is_active=True)
    if target_ids:
        qs = qs.filter(id__in=_limit_ids(target_ids))
    items = []
    for product in qs.order_by("-created_at")[:limit]:
        image_url = ""
        try:
            image_url = safe_public_media_url(product.main_image.url) if product.main_image else ""
        except (ValueError, AttributeError):
            image_url = ""
        items.append({
            "id": str(product.id),
            "title": product.name,
            "description": safe_public_description(product.description),
            "image_url": image_url,
            "price_display": f"{product.sale_price or product.price} {product.currency}",
            "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/market/products/{product.id}",
            "shop_id": str(product.shop_id),
        })
    return items


def resolve_shop_services(*, owner_type, owner_id, target_ids=None, limit=6, **_):
    if owner_type != WebsiteOwnerType.SHOP:
        return []
    ShopService = django_apps.get_model("commerce", "ShopService")
    qs = ShopService.objects.filter(shop_id=owner_id, visibility="public", status="published")
    if target_ids:
        qs = qs.filter(id__in=_limit_ids(target_ids))
    items = []
    for service in qs.order_by("-created_at")[:limit]:
        image_url = ""
        try:
            image_url = safe_public_media_url(service.image_file.url) if service.image_file else ""
        except (ValueError, AttributeError):
            image_url = ""
        items.append({
            "id": str(service.id),
            "title": service.name,
            "description": safe_public_description(service.short_summary, service.description),
            "image_url": image_url,
            "price_display": f"{service.price} {KIS_COIN_CODE}" if service.price else "Contact for pricing",
            "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/market/services/{service.id}",
        })
    return items


def resolve_health_services(*, owner_type, owner_id, target_ids=None, limit=6, **_):
    if owner_type != WebsiteOwnerType.HEALTH_INSTITUTION:
        return []
    HealthService = django_apps.get_model("health_ops", "HealthService")
    qs = HealthService.objects.filter(institution_id=owner_id, is_active=True)
    if target_ids:
        qs = qs.filter(id__in=_limit_ids(target_ids))
    items = []
    for service in qs.order_by("name")[:limit]:
        items.append({
            "id": str(service.id),
            "title": service.name,
            "description": safe_public_description(service.description),
            "image_url": "",
            "price_display": (
                f"{service.base_cost_micro / 1_000_000:.2f} {KIS_COIN_CODE}" if service.base_cost_micro else "Contact for pricing"
            ),
            "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/health/services/{service.id}",
        })
    return items


def resolve_product_detail(*, owner_type, owner_id, item_id, **_):
    """Full detail-page payload for a single product — the card summary
    from resolve_products plus gallery images, stock, and category, so
    the website's product detail page (like the app's own product screen)
    isn't stuck with a 260-char blurb and one thumbnail."""
    if owner_type != WebsiteOwnerType.SHOP:
        return None
    Product = django_apps.get_model("commerce", "Product")
    product = Product.objects.filter(shop_id=owner_id, id=item_id, is_active=True).first()
    if not product:
        return None
    image_url = ""
    try:
        image_url = safe_public_media_url(product.main_image.url) if product.main_image else ""
    except (ValueError, AttributeError):
        image_url = ""
    gallery = []
    for image in product.gallery_images.filter(is_active=True).order_by("sort_order"):
        try:
            url = safe_public_media_url(image.image_file.url) if image.image_file else ""
        except (ValueError, AttributeError):
            url = ""
        if url:
            gallery.append(url)
    return {
        "id": str(product.id),
        "title": product.name,
        "description": safe_public_description(product.description, limit=2000),
        "image_url": image_url,
        "gallery": gallery,
        "price_display": f"{product.sale_price or product.price} {product.currency}",
        "compare_at_price_display": f"{product.price} {product.currency}" if product.sale_price else "",
        "in_stock": product.inventory_type != "PHYSICAL" or product.stock_qty > 0,
        "categories": [c.name for c in product.catalog_categories.all()],
        "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/market/products/{product.id}",
        "shop_id": str(product.shop_id),
    }


def resolve_course_detail(*, owner_type, owner_id, item_id, **_):
    if owner_type != WebsiteOwnerType.EDUCATION_INSTITUTION:
        return None
    EducationInstitutionCourse = django_apps.get_model("broadcasts", "EducationInstitutionCourse")
    course = EducationInstitutionCourse.objects.filter(
        institution_id=owner_id, id=item_id, status="published", visibility="public",
    ).select_related("institution").first()
    if not course:
        return None
    enrollment_content_id = None
    primary_broadcast = course.broadcasts.filter(
        status="published",
    ).exclude(price_amount__isnull=True).order_by("created_at").first()
    if primary_broadcast is None:
        primary_broadcast = course.broadcasts.filter(status="published").order_by("created_at").first()
    if primary_broadcast is not None:
        enrollment_content_id = str(primary_broadcast.id)
    return {
        "id": str(course.id),
        "title": course.title,
        "description": safe_public_description(course.summary, course.description, limit=2000),
        "image_url": safe_public_media_url(_resolve_stored_media_url(course.cover_image_url)),
        "gallery": [],
        "price_display": "Free" if course.is_free else f"{course.price_amount} {course.price_currency}",
        "duration_minutes": course.duration_minutes,
        "seat_limit": course.seat_limit,
        "institution_name": course.institution.name if course.institution else "",
        "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/education/courses/{course.id}",
        "checkout_content_id": enrollment_content_id,
    }


def resolve_shop_service_detail(*, owner_type, owner_id, item_id, **_):
    if owner_type != WebsiteOwnerType.SHOP:
        return None
    ShopService = django_apps.get_model("commerce", "ShopService")
    service = ShopService.objects.filter(shop_id=owner_id, id=item_id, visibility="public", status="published").first()
    if not service:
        return None
    image_url = ""
    try:
        image_url = safe_public_media_url(service.image_file.url) if service.image_file else ""
    except (ValueError, AttributeError):
        image_url = ""
    return {
        "id": str(service.id),
        "title": service.name,
        "description": safe_public_description(service.short_summary, service.description, limit=2000),
        "image_url": image_url,
        "gallery": [],
        "price_display": f"{service.price} {KIS_COIN_CODE}" if service.price else "Contact for pricing",
        "service_type": service.service_type,
        "negotiable": service.negotiable,
        "quote_required": service.quote_required,
        "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/market/services/{service.id}",
        "shop_id": str(service.shop_id),
    }


_DETAIL_RESOLVERS = {
    "product": resolve_product_detail,
    "course": resolve_course_detail,
    "shop_service": resolve_shop_service_detail,
}


def resolve_kis_content_item_detail(*, target_type, owner_type, owner_id, item_id):
    resolver = _DETAIL_RESOLVERS.get(target_type)
    if not resolver:
        return None
    return resolver(owner_type=owner_type, owner_id=owner_id, item_id=item_id)


def resolve_broadcast_channels(*, target_ids=None, limit=6, **_):
    if not target_ids:
        return []
    BroadcastChannel = django_apps.get_model("broadcasts", "BroadcastChannel")
    qs = BroadcastChannel.objects.filter(id__in=_limit_ids(target_ids), is_public=True, is_deleted=False)
    items = []
    for channel in qs[:limit]:
        items.append({
            "id": str(channel.id),
            "title": channel.display_name,
            "description": safe_public_description(channel.description),
            "image_url": safe_public_media_url(channel.avatar_url),
            "price_display": "",
            "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/channels/{channel.handle}",
        })
    return items


def _post_is_public_safe(content) -> bool:
    metadata = content.metadata if isinstance(content.metadata, dict) else {}
    if metadata.get("child_sensitive") or metadata.get("private_context") or metadata.get("contains_private_data"):
        return False
    ChannelContent = type(content)
    return (
        content.visibility == ChannelContent.Visibility.PUBLIC
        and content.status == ChannelContent.Status.PUBLISHED
        and not content.channel.is_deleted
        and content.channel.is_public
    )


def _post_image_url(content) -> str:
    """content.thumbnail_url is frequently empty — the actual uploaded
    image for a post lives on its ChannelContentAsset rows (content.
    assets), not on ChannelContent itself. Prefer the first image-type
    asset's thumbnail (falls back to its own url for a plain image asset
    with no separate thumbnail), then content.thumbnail_url, then any
    OTHER asset's thumbnail_url specifically — never an arbitrary asset's
    raw .url, since for a video/file asset that's the video/document file
    itself, not an image, and would render as a broken <img>."""
    asset = content.assets.filter(asset_type="image").order_by("sort_order").first()
    if asset:
        return resolve_stale_media_url(asset.thumbnail_url or asset.url)
    if content.thumbnail_url:
        return resolve_stale_media_url(content.thumbnail_url)
    any_asset_with_thumbnail = content.assets.exclude(thumbnail_url="").order_by("sort_order").first()
    if any_asset_with_thumbnail:
        return resolve_stale_media_url(any_asset_with_thumbnail.thumbnail_url)
    return ""


def resolve_posts(*, target_ids=None, limit=6, **_):
    if not target_ids:
        return []
    ChannelContent = django_apps.get_model("broadcasts", "ChannelContent")
    qs = ChannelContent.objects.select_related("channel").prefetch_related("assets").filter(id__in=_limit_ids(target_ids))
    items = []
    for content in qs[:limit]:
        if not _post_is_public_safe(content):
            continue
        items.append({
            "id": str(content.id),
            "title": content.title or safe_public_description(content.text_plain)[:80],
            "description": safe_public_description(content.description, content.text_plain),
            "image_url": safe_public_media_url(_post_image_url(content)),
            "price_display": "",
            "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/posts/{content.id}",
        })
    return items


def resolve_events(*, owner_type, owner_id, target_ids=None, limit=6, **_):
    """Phase 1 limitation: only Education institutions have a
    scoped event model (EducationInstitutionEvent). Shop/Health/Partner/
    Broadcast owners get an empty list here rather than a fabricated
    source — apps.events.Event exists but is owned per-user, not per-
    institution, so it isn't a safe scope match for "this business's
    events"."""
    if owner_type != WebsiteOwnerType.EDUCATION_INSTITUTION:
        return []
    EducationInstitutionEvent = django_apps.get_model("broadcasts", "EducationInstitutionEvent")
    qs = EducationInstitutionEvent.objects.filter(institution_id=owner_id)
    if target_ids:
        qs = qs.filter(id__in=_limit_ids(target_ids))
    items = []
    for event in qs.order_by("starts_at")[:limit]:
        items.append({
            "id": str(event.id),
            "title": event.title,
            "description": safe_public_description(event.summary, event.description),
            "image_url": safe_public_media_url(_resolve_stored_media_url(event.cover_image_url)),
            "price_display": "",
            "starts_at": event.starts_at.isoformat() if event.starts_at else None,
            "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/education/events/{event.id}",
        })
    return items


def resolve_testimonials(*, owner_type, owner_id, target_ids=None, limit=6, **_):
    """Phase 1 limitation: only Shop has a scoped testimonial model
    (ShopLandingTestimonial). Other owner types get an empty list —
    static `testimonials` sections (not `kis_content`) remain available
    to every owner type for hand-entered quotes."""
    if owner_type != WebsiteOwnerType.SHOP:
        return []
    ShopLandingTestimonial = django_apps.get_model("commerce", "ShopLandingTestimonial")
    qs = ShopLandingTestimonial.objects.filter(landing_page__shop_id=owner_id)
    if target_ids:
        qs = qs.filter(id__in=_limit_ids(target_ids))
    items = []
    for testimonial in qs.order_by("sort_order", "created_at")[:limit]:
        items.append({
            "id": str(testimonial.id),
            "title": testimonial.author or "Anonymous",
            "description": safe_public_description(testimonial.quote),
            "image_url": "",
            "price_display": "",
            "rating": testimonial.rating,
            "deep_link": "",
        })
    return items


RESOLVERS = {
    "course": resolve_courses,
    "product": resolve_products,
    "shop_service": resolve_shop_services,
    "health_service": resolve_health_services,
    "broadcast_channel": resolve_broadcast_channels,
    "post": resolve_posts,
    "event": resolve_events,
    "testimonial": resolve_testimonials,
}


def _clamp_limit(value) -> int:
    try:
        return max(1, min(int(value), 50))
    except (TypeError, ValueError):
        return 6


def _apply_ordering(items: list, ordering: str, target_ids) -> list:
    """Resolvers themselves only ever fetch newest-first from the DB —
    ordering beyond that (and pagination, see resolve_kis_content_section_page)
    happens here, over each resolver's own fixed-cap fetch (see below),
    not a live unbounded DB-level operation. Fine at this feature's actual
    scale (the same 50-row cap target_ids has always been limited to);
    this was never built to paginate thousands of rows."""
    if ordering == "alphabetical":
        return sorted(items, key=lambda item: (item.get("title") or "").lower())
    if ordering == "manual" and target_ids:
        order_index = {str(tid): idx for idx, tid in enumerate(target_ids)}
        return sorted(items, key=lambda item: order_index.get(item.get("id"), len(order_index)))
    return items  # "recent" (default) — resolvers already order by -created_at


def _resolve_ordered_items(*, owner_type, owner_id, section_data: dict) -> list:
    target_type = section_data.get("target_type")
    resolver = RESOLVERS.get(target_type)
    if resolver is None:
        return []
    target_ids = section_data.get("target_ids")
    filter_config = section_data.get("filter") or {}
    ordering = filter_config.get("ordering") or "recent"
    # Always fetch the resolver's own max (50, matching the target_ids
    # cap) regardless of the section's configured display limit — ordering
    # and pagination both need the full candidate set to be correct, not
    # just the first page's worth.
    items = resolver(owner_type=owner_type, owner_id=owner_id, target_ids=target_ids, limit=50)
    return _apply_ordering(items, ordering, target_ids)


def resolve_kis_content_section(*, owner_type, owner_id, section_data: dict) -> list:
    """Entry point used by the public page serializer for a `kis_content`
    section — dispatches to the right resolver, honors `presentation.
    limit` and `filter.ordering`. Returns [] for an unknown target_type
    rather than raising, so a malformed/legacy section never breaks the
    whole page render. See resolve_kis_content_section_page for the
    has_more-aware variant used by the public "load more" pagination."""
    presentation = section_data.get("presentation") or {}
    limit = _clamp_limit(presentation.get("limit"))
    items = _resolve_ordered_items(owner_type=owner_type, owner_id=owner_id, section_data=section_data)
    return items[:limit]


def resolve_kis_content_section_page(*, owner_type, owner_id, section_data: dict, offset: int = 0) -> dict:
    """Same resolution as resolve_kis_content_section, plus has_more —
    used for the public page's initial render (offset=0, to know whether
    to show a "Load more" button at all) and the load-more beacon itself
    (offset>0)."""
    presentation = section_data.get("presentation") or {}
    limit = _clamp_limit(presentation.get("limit"))
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    items = _resolve_ordered_items(owner_type=owner_type, owner_id=owner_id, section_data=section_data)
    page_items = items[offset:offset + limit]
    return {"items": page_items, "has_more": offset + limit < len(items)}
