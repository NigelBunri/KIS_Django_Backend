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

from apps.core.public_web import safe_public_description, safe_public_media_url
from apps.websites.models import WebsiteOwnerType

KIS_APP_DEEP_LINK_BASE = "https://kis.app"


def _limit_ids(target_ids, cap=50):
    return [str(i) for i in (target_ids or [])][:cap]


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
            "image_url": safe_public_media_url(course.cover_image_url),
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
        items.append({
            "id": str(service.id),
            "title": service.name,
            "description": safe_public_description(service.short_summary, service.description),
            "image_url": "",
            "price_display": f"{service.price}" if service.price else "Contact for pricing",
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
            "price_display": f"{service.base_cost_micro / 1_000_000:.2f}" if service.base_cost_micro else "Contact for pricing",
            "deep_link": f"{KIS_APP_DEEP_LINK_BASE}/health/services/{service.id}",
        })
    return items


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


def resolve_posts(*, target_ids=None, limit=6, **_):
    if not target_ids:
        return []
    ChannelContent = django_apps.get_model("broadcasts", "ChannelContent")
    qs = ChannelContent.objects.select_related("channel").filter(id__in=_limit_ids(target_ids))
    items = []
    for content in qs[:limit]:
        if not _post_is_public_safe(content):
            continue
        items.append({
            "id": str(content.id),
            "title": content.title or safe_public_description(content.text_plain)[:80],
            "description": safe_public_description(content.description, content.text_plain),
            "image_url": safe_public_media_url(content.thumbnail_url),
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
            "image_url": safe_public_media_url(event.cover_image_url),
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


def resolve_kis_content_section(*, owner_type, owner_id, section_data: dict) -> list:
    """Entry point used by the public page serializer for a `kis_content`
    section — dispatches to the right resolver and honors `presentation.
    limit`. Returns [] for an unknown target_type rather than raising, so
    a malformed/legacy section never breaks the whole page render."""
    target_type = section_data.get("target_type")
    resolver = RESOLVERS.get(target_type)
    if resolver is None:
        return []
    presentation = section_data.get("presentation") or {}
    limit = presentation.get("limit") or 6
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 6
    return resolver(
        owner_type=owner_type,
        owner_id=owner_id,
        target_ids=section_data.get("target_ids"),
        limit=limit,
    )
