"""
Resolves a single specific KIS video for the `kis_video` section type —
distinct from `kis_content` (which resolves grids of listing items) and
from `embed` (third-party-provider iframes). This plays KIS's own video
content directly, via an HTML5 <video> element pointed straight at its
own storage URL (default_storage.url(...) — a direct S3 URL, same as
every other video already served on the platform) — never proxied,
never a third-party iframe.

Only two domains have a real, dedicated video-content model in this
codebase as of writing this: Broadcast Channel posts
(broadcasts.ChannelContent/ChannelContentAsset) and Health Institution
service videos (health_ops.VideoEngineItem, tied to a HealthService via
ServiceEngineMap). Education (EducationInstitutionBroadcast/Lesson) and
Marketplace (commerce.Product) have no video field or video-content
model at all — not a website-builder limitation, an actual gap in those
domains' schemas. KIS_VIDEO_SOURCES reflects only what's real; adding
Education/Market support means building video storage for those domains
first, which is out of scope here.
"""
from django.apps import apps as django_apps

from apps.core.public_web import safe_public_description, safe_public_media_url
from apps.websites.kis_content_resolvers import _post_is_public_safe

KIS_VIDEO_SOURCES = ("broadcast_content", "health_engine_item")


def _resolve_broadcast_content_video(target_id) -> dict | None:
    ChannelContent = django_apps.get_model("broadcasts", "ChannelContent")
    content = ChannelContent.objects.select_related("channel").filter(id=target_id).first()
    if content is None or not _post_is_public_safe(content):
        return None
    if content.content_type not in ("video", "short_video"):
        return None
    asset = content.assets.filter(asset_type__in=("video", "short_video")).order_by("sort_order").first()
    video_url = safe_public_media_url(asset.url if asset else "")
    if not video_url:
        return None
    return {
        "title": content.title or "",
        "description": safe_public_description(content.description, content.text_plain),
        "video_url": video_url,
        "thumbnail_url": safe_public_media_url(content.thumbnail_url),
        "duration_seconds": content.duration_seconds or (asset.duration_seconds if asset else None),
    }


def _resolve_health_engine_item_video(target_id) -> dict | None:
    VideoEngineItem = django_apps.get_model("health_ops", "VideoEngineItem")
    item = VideoEngineItem.objects.filter(id=target_id, is_active=True).first()
    if item is None:
        return None
    video_url = safe_public_media_url(item.source_url)
    if not video_url:
        return None
    return {
        "title": item.title or "",
        "description": safe_public_description(item.description, ""),
        "video_url": video_url,
        "thumbnail_url": safe_public_media_url(item.thumbnail_url),
        "duration_seconds": item.duration_seconds,
    }


_RESOLVERS = {
    "broadcast_content": _resolve_broadcast_content_video,
    "health_engine_item": _resolve_health_engine_item_video,
}


def resolve_kis_video(source: str, target_id: str) -> dict | None:
    resolver = _RESOLVERS.get(source)
    if resolver is None or not target_id:
        return None
    return resolver(target_id)


def search_owner_kis_videos(*, owner_type, owner_id, q: str = "", limit: int = 50) -> list[dict]:
    """Videos the given owner can actually embed — their OWN content
    only, resolved the same way resolve_kis_video would at render time,
    never a platform-wide search."""
    from apps.websites.models import WebsiteOwnerType

    results: list[dict] = []
    q = (q or "").strip()

    if owner_type == WebsiteOwnerType.BROADCAST_CHANNEL:
        ChannelContent = django_apps.get_model("broadcasts", "ChannelContent")
        qs = ChannelContent.objects.filter(
            channel_id=owner_id, content_type__in=("video", "short_video"),
            status="published", visibility="public", is_deleted=False,
        ).order_by("-published_at")
        if q:
            qs = qs.filter(title__icontains=q)
        for content in qs[:limit]:
            resolved = _resolve_broadcast_content_video(content.id)
            if resolved:
                results.append({"source": "broadcast_content", "target_id": str(content.id), **resolved})

    elif owner_type == WebsiteOwnerType.HEALTH_INSTITUTION:
        VideoEngineItem = django_apps.get_model("health_ops", "VideoEngineItem")
        qs = VideoEngineItem.objects.filter(
            engine_map__service__institution_id=owner_id, is_active=True,
        ).order_by("sort_order")
        if q:
            qs = qs.filter(title__icontains=q)
        for item in qs[:limit]:
            resolved = _resolve_health_engine_item_video(item.id)
            if resolved:
                results.append({"source": "health_engine_item", "target_id": str(item.id), **resolved})

    return results
