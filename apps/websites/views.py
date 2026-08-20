from datetime import timedelta

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.public_web import public_web_base_url, public_web_enabled, resolve_stale_media_url, safe_public_description
from apps.notifications.email_service import send_website_form_notification_email
from apps.websites import adapters
from apps.websites.analytics import classify_device, extract_referrer_host, hash_visitor_session
from apps.websites.branding import validate_branding
from apps.websites.custom_domains import (
    check_hostname_status,
    custom_domains_enabled,
    deregister_custom_hostname,
    register_custom_hostname,
    validate_domain_format,
)
from apps.websites.forms import HONEYPOT_KEY, score_submission, validate_submission_data
from apps.websites.kis_content_resolvers import (
    resolve_kis_content_item_detail,
    resolve_kis_content_section,
    resolve_kis_content_section_page,
)
from apps.websites.kis_video import resolve_kis_video, search_owner_kis_videos
from apps.websites.models import (
    Website,
    WebsiteAnalyticsEvent,
    WebsiteCollaborator,
    WebsiteCollaboratorRole,
    WebsiteCustomDomainStatus,
    WebsiteFormSubmission,
    WebsiteInvite,
    WebsiteOwnerType,
    WebsitePage,
    WebsiteStatus,
    WebsiteTemplate,
    WebsiteWebhook,
    WebsiteWebhookEvent,
)
from apps.websites.owner_resolution import (
    resolve_owner_object,
    resolve_owner_user,
    user_can_administer_website,
    user_can_manage_website,
)
from apps.websites.webhooks import fire_webhook_event, generate_webhook_secret
from apps.websites.permissions import (
    check_collaborator_seat_quota,
    check_kis_content_sections_quota,
    check_pages_quota,
    check_websites_quota,
    require_custom_branding_allowed,
    require_website_publish_allowed,
)
from apps.websites.preview_tokens import sign_website_preview_token, verify_website_preview_token
from apps.websites.serializers import WebsitePageSerializer, WebsiteSerializer


def _website_public_base_url() -> str:
    configured = str(getattr(settings, "KIS_WEBSITE_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    return configured or "https://kingdomimpactventures.org"


def _require_public_web_enabled():
    if not public_web_enabled():
        raise ValidationError({"detail": "Website builder public pages are not enabled."})


def _require_manage_permission(user, website: Website):
    if not user_can_manage_website(user, website.owner_type, website.owner_id):
        raise PermissionDenied("You do not manage this website.")


def _require_administer_permission(user, website: Website):
    if not user_can_administer_website(user, website.owner_type, website.owner_id):
        raise PermissionDenied("Only the website owner can manage collaborators and invites.")


_SECTION_IMAGE_STRING_FIELDS = ("image_url", "backgroundImageUrl", "imageUrl", "video_url", "thumbnail_url")
_SECTION_IMAGE_LIST_FIELDS = ("images",)
# field name -> the per-item key holding an image URL. "items" (kis_content-
# style sections) uses snake_case image_url; "slides" (slideshow) uses the
# RN editor's own camelCase imageUrl, same convention as its sibling
# top-level *ImageUrl fields above.
_SECTION_IMAGE_ITEM_LIST_FIELDS = {"items": "image_url", "slides": "imageUrl"}


def _resolve_section_data_media(data: dict) -> dict:
    if not isinstance(data, dict):
        return data
    resolved = dict(data)
    for field in _SECTION_IMAGE_STRING_FIELDS:
        if isinstance(resolved.get(field), str) and resolved.get(field):
            resolved[field] = resolve_stale_media_url(resolved[field])
    for field in _SECTION_IMAGE_LIST_FIELDS:
        if isinstance(resolved.get(field), list):
            resolved[field] = [
                resolve_stale_media_url(item) if isinstance(item, str) else item for item in resolved[field]
            ]
    for field, image_key in _SECTION_IMAGE_ITEM_LIST_FIELDS.items():
        if isinstance(resolved.get(field), list):
            resolved[field] = [
                {**item, image_key: resolve_stale_media_url(item[image_key])}
                if isinstance(item, dict) and isinstance(item.get(image_key), str) and item.get(image_key)
                else item
                for item in resolved[field]
            ]
    return resolved


def _serialize_public_section(website: Website, section: dict) -> dict:
    if not isinstance(section, dict):
        return {}
    payload = {
        "id": section.get("id"),
        "type": section.get("type"),
        "data": _resolve_section_data_media(section.get("data") or {}),
        "responsive": section.get("responsive") if isinstance(section.get("responsive"), dict) else {},
    }
    if section.get("type") == "kis_content":
        page = resolve_kis_content_section_page(
            owner_type=website.owner_type, owner_id=website.owner_id, section_data=section.get("data") or {},
        )
        payload["resolved_items"] = page["items"]
        payload["has_more"] = page["has_more"]
    if section.get("type") == "kis_video":
        data = section.get("data") or {}
        payload["resolved_video"] = resolve_kis_video(data.get("source"), data.get("target_id"))
    return payload


def _public_page_payload(website: Website, page: WebsitePage) -> dict:
    base_url = _website_public_base_url()
    canonical = f"{base_url}/page/{website.slug}" if page.is_home else f"{base_url}/page/{website.slug}/{page.slug}"
    seo = page.seo if isinstance(page.seo, dict) else {}
    return {
        "id": str(page.id),
        "slug": page.slug,
        "title": page.title,
        "is_home": page.is_home,
        "sections": [_serialize_public_section(website, s) for s in (page.sections or [])],
        "seo": {
            "title": seo.get("title") or page.title,
            "description": safe_public_description(seo.get("description") or ""),
            "canonical_url": canonical,
            "share_image_url": seo.get("share_image_url") or "",
        },
        "updated_at": page.updated_at.isoformat() if page.updated_at else None,
    }


def _public_site_payload(website: Website) -> dict:
    base_url = _website_public_base_url()
    default_seo = website.default_seo if isinstance(website.default_seo, dict) else {}
    pages = website.pages.filter(status=WebsiteStatus.PUBLISHED).order_by("sort_order", "created_at")
    return {
        "slug": website.slug,
        "name": website.name,
        "owner_type": website.owner_type,
        "branding": website.branding or {},
        "seo": {
            "title": default_seo.get("title") or website.name,
            "description": safe_public_description(default_seo.get("description") or ""),
            "share_image_url": default_seo.get("share_image_url") or "",
        },
        "canonical_url": f"{base_url}/page/{website.slug}",
        "pages": [
            {"slug": p.slug, "title": p.title, "is_home": p.is_home}
            for p in pages
        ],
        "updated_at": website.updated_at.isoformat() if website.updated_at else None,
    }


# ---------------------------------------------------------------------
# Public read API (AllowAny) — extends the broadcasts public-web system's
# settings/sanitization (apps.core.public_web), separate route family
# since this is a distinct public domain (kingdomimpactventures.org, not
# kis.app).
# ---------------------------------------------------------------------

class WebsitePublicSiteView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, website_slug):
        _require_public_web_enabled()
        website = get_object_or_404(Website, slug=website_slug)
        preview_token = request.query_params.get("preview_token")
        is_preview = verify_website_preview_token(preview_token, website.id)
        if website.status != WebsiteStatus.PUBLISHED and not is_preview:
            raise Http404("Website not found.")
        return Response(_public_site_payload(website), status=status.HTTP_200_OK)


class WebsitePublicPageView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, website_slug, page_slug):
        _require_public_web_enabled()
        website = get_object_or_404(Website, slug=website_slug)
        preview_token = request.query_params.get("preview_token")
        is_preview = verify_website_preview_token(preview_token, website.id)
        if website.status != WebsiteStatus.PUBLISHED and not is_preview:
            raise Http404("Website not found.")
        lookup_slug = "" if page_slug == "home" else page_slug
        page = get_object_or_404(WebsitePage, website=website, slug=lookup_slug)
        if page.status != WebsiteStatus.PUBLISHED and not is_preview:
            raise Http404("Page not found.")
        return Response(_public_page_payload(website, page), status=status.HTTP_200_OK)


class WebsitePublicKisContentLoadMoreView(APIView):
    """Backs the "Load more" button on a public kis_content section —
    re-resolves live (never a cached/stale page) at a given offset. Public
    (AllowAny) since the section itself is already publicly visible on the
    page; this exposes no more than what resolve_kis_content_section_page
    already returns for the page's own initial render."""

    permission_classes = [AllowAny]

    def get(self, request, website_slug, page_slug, section_id):
        _require_public_web_enabled()
        website = get_object_or_404(Website, slug=website_slug)
        if website.status != WebsiteStatus.PUBLISHED:
            raise Http404("Website not found.")
        lookup_slug = "" if page_slug == "home" else page_slug
        page = get_object_or_404(WebsitePage, website=website, slug=lookup_slug)
        if page.status != WebsiteStatus.PUBLISHED:
            raise Http404("Page not found.")

        section = next(
            (s for s in (page.sections or []) if isinstance(s, dict) and s.get("id") == section_id), None,
        )
        if not section or section.get("type") != "kis_content":
            raise Http404("Section not found.")

        offset = request.query_params.get("offset") or 0
        result = resolve_kis_content_section_page(
            owner_type=website.owner_type, owner_id=website.owner_id,
            section_data=section.get("data") or {}, offset=offset,
        )
        return Response(result)


class WebsitePublicKisContentDetailView(APIView):
    """Backs the on-site product/course/service detail page — a fuller
    payload than the card summary (see resolve_kis_content_item_detail):
    untruncated description, gallery images, stock/availability, etc.
    Public (AllowAny), scoped to the website's own owner exactly like
    every other kis_content resolver — a product/course/service only
    resolves here if it actually belongs to this published website's
    owner and is itself public/published, never by guessing an id
    belonging to someone else's shop or institution."""

    permission_classes = [AllowAny]

    def get(self, request, website_slug, target_type, item_id):
        _require_public_web_enabled()
        website = get_object_or_404(Website, slug=website_slug)
        if website.status != WebsiteStatus.PUBLISHED:
            raise Http404("Website not found.")

        item = resolve_kis_content_item_detail(
            target_type=target_type, owner_type=website.owner_type, owner_id=website.owner_id, item_id=item_id,
        )
        if not item:
            raise Http404("Item not found.")
        return Response({
            "item": item,
            "site": {"slug": website.slug, "name": website.name},
        })


class WebsitePublicSitemapPlanView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from apps.core.public_web import public_indexing_enabled

        if not public_web_enabled():
            return Response({"indexing_enabled": False, "robots": "noindex,nofollow", "sites": []})
        indexing = public_indexing_enabled()
        sites = []
        for website in Website.objects.filter(status=WebsiteStatus.PUBLISHED).order_by("-updated_at")[:200]:
            pages = website.pages.filter(status=WebsiteStatus.PUBLISHED).order_by("sort_order")[:200]
            sites.append({
                "slug": website.slug,
                "updated_at": website.updated_at.isoformat() if website.updated_at else None,
                "pages": [
                    {"slug": p.slug or "home", "updated_at": p.updated_at.isoformat() if p.updated_at else None}
                    for p in pages
                ],
            })
        return Response({
            "indexing_enabled": indexing,
            "robots": "index,follow" if indexing else "noindex,nofollow",
            "sites": sites,
        })


def _notify_owner_of_form_submission(website: Website, page: WebsitePage, section_data: dict, cleaned: dict):
    owner_instance = resolve_owner_object(website.owner_type, website.owner_id)
    owner_user = resolve_owner_user(website.owner_type, owner_instance)
    to_email = getattr(owner_user, "email", "") if owner_user else ""
    if not to_email:
        return
    field_labels = {f.get("key"): f.get("label") or f.get("key") for f in (section_data.get("fields") or [])}
    labeled = {field_labels.get(k, k): v for k, v in cleaned.items()}
    send_website_form_notification_email(
        to_email=to_email,
        website_name=website.name or website.slug,
        page_title=page.title,
        form_title=section_data.get("title") or "Website",
        fields=labeled,
    )


class WebsitePublicFormSubmitView(APIView):
    """AllowAny + IP-throttled — a visitor submitting a `form` section on a
    published page never authenticates. Honeypot hits are accepted with a
    fake success and never persisted or notified, so a bot gets no signal
    it was caught; everything else is scored (apps.websites.forms.
    score_submission) and stored either way so the owner can still see
    borderline submissions rather than having them silently vanish."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "website_form_submit"

    def post(self, request, website_slug, page_slug, section_id):
        _require_public_web_enabled()
        website = get_object_or_404(Website, slug=website_slug)
        if website.status != WebsiteStatus.PUBLISHED:
            raise Http404("Website not found.")
        lookup_slug = "" if page_slug == "home" else page_slug
        page = get_object_or_404(WebsitePage, website=website, slug=lookup_slug)
        if page.status != WebsiteStatus.PUBLISHED:
            raise Http404("Page not found.")

        section = next(
            (s for s in (page.sections or []) if isinstance(s, dict) and s.get("id") == section_id), None,
        )
        if not section or section.get("type") != "form":
            raise Http404("Form not found.")

        submitted = request.data if isinstance(request.data, dict) else {}
        if submitted.get(HONEYPOT_KEY):
            return Response({"success": True}, status=status.HTTP_201_CREATED)

        section_data = section.get("data") or {}
        cleaned = validate_submission_data(section_data, submitted)
        spam_score = score_submission(submitted)

        submission = WebsiteFormSubmission.objects.create(
            website=website, page=page, section_id=section_id, data=cleaned, spam_score=spam_score,
        )

        if spam_score < 0.5:
            _notify_owner_of_form_submission(website, page, section_data, cleaned)
            fire_webhook_event(website, WebsiteWebhookEvent.FORM_SUBMITTED, {
                "page_slug": page.slug or "home", "section_id": section_id, "data": cleaned,
            })

        return Response({"success": True, "id": str(submission.id)}, status=status.HTTP_201_CREATED)


class WebsitePublicAnalyticsBeaconView(APIView):
    """Fire-and-forget page-view beacon called from the public site on
    load. Always returns 200/204 quickly, even when the site/page can't
    be resolved — a tracking beacon isn't something client code should
    ever need to handle an error branch for."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "website_analytics_beacon"

    def post(self, request):
        if not public_web_enabled():
            return Response(status=status.HTTP_204_NO_CONTENT)

        site_slug = str(request.data.get("site_slug") or "").strip()
        page_slug = str(request.data.get("page_slug") or "").strip()
        referrer = str(request.data.get("referrer") or "")

        website = Website.objects.filter(slug=site_slug, status=WebsiteStatus.PUBLISHED).first()
        if website is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        page = None
        if page_slug:
            lookup_slug = "" if page_slug == "home" else page_slug
            page = WebsitePage.objects.filter(website=website, slug=lookup_slug, status=WebsiteStatus.PUBLISHED).first()

        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip_address = forwarded.split(",")[0].strip() if forwarded else str(request.META.get("REMOTE_ADDR") or "")
        user_agent = str(request.META.get("HTTP_USER_AGENT") or "")

        WebsiteAnalyticsEvent.objects.create(
            website=website, page=page,
            path=f"/page/{site_slug}" + (f"/{page_slug}" if page_slug and page_slug != "home" else ""),
            referrer_host=extract_referrer_host(referrer),
            device_type=classify_device(user_agent),
            session_hash=hash_visitor_session(ip_address, user_agent),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebsiteAnalyticsSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, website_id):
        from django.db.models import Count
        from django.db.models.functions import TruncDate

        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)

        days = min(max(int(request.query_params.get("days") or 30), 1), 90)
        since = timezone.now() - timedelta(days=days)
        events = WebsiteAnalyticsEvent.objects.filter(website=website, created_at__gte=since)

        daily = (
            events.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )
        unique_visitors = events.values("session_hash").distinct().count()
        top_pages = (
            events.filter(page__isnull=False)
            .values("page_id", "page__title", "page__slug")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        top_referrers = (
            events.exclude(referrer_host="")
            .values("referrer_host")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        device_breakdown = (
            events.values("device_type").annotate(count=Count("id")).order_by("-count")
        )

        return Response({
            "days": days,
            "total_views": events.count(),
            "unique_visitors": unique_visitors,
            "daily": [{"date": d["day"].isoformat(), "count": d["count"]} for d in daily],
            "top_pages": [
                {"page_id": str(p["page_id"]), "title": p["page__title"], "slug": p["page__slug"] or "home", "count": p["count"]}
                for p in top_pages
            ],
            "top_referrers": [
                {"referrer_host": r["referrer_host"], "count": r["count"]} for r in top_referrers
            ],
            "device_breakdown": [
                {"device_type": d["device_type"], "count": d["count"]} for d in device_breakdown
            ],
        })


class WebsiteFormResponsesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        page_id = request.query_params.get("page_id")
        submissions = website.form_submissions.select_related("page").order_by("-created_at")
        if page_id:
            submissions = submissions.filter(page_id=page_id)
        submissions = submissions[:500]
        return Response([
            {
                "id": str(s.id),
                "page_id": str(s.page_id),
                "page_title": s.page.title,
                "section_id": s.section_id,
                "data": s.data,
                "spam_score": s.spam_score,
                "created_at": s.created_at.isoformat(),
            }
            for s in submissions
        ])


class WebsiteWebhookListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        return Response([
            {
                "id": str(w.id), "event_type": w.event_type, "target_url": w.target_url,
                "is_active": w.is_active, "created_at": w.created_at.isoformat(),
            }
            for w in website.webhooks.order_by("-created_at")
        ])

    def post(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        event_type = request.data.get("event_type")
        target_url = str(request.data.get("target_url") or "").strip()
        if event_type not in WebsiteWebhookEvent.values:
            raise ValidationError({"event_type": f"event_type must be one of {WebsiteWebhookEvent.values}."})
        if not target_url.startswith("https://"):
            raise ValidationError({"target_url": "target_url must be an https:// URL."})

        webhook = WebsiteWebhook.objects.create(
            website=website, event_type=event_type, target_url=target_url,
            secret=generate_webhook_secret(), created_by=request.user,
        )
        # The only time this endpoint (or any endpoint) ever returns the
        # secret — used to compute X-KIS-Signature, so the owner needs it
        # exactly once to verify deliveries on their own receiving end.
        return Response({
            "id": str(webhook.id), "event_type": webhook.event_type, "target_url": webhook.target_url,
            "secret": webhook.secret, "is_active": webhook.is_active,
        }, status=status.HTTP_201_CREATED)


class WebsiteWebhookDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, website_id, webhook_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        webhook = get_object_or_404(WebsiteWebhook, id=webhook_id, website=website)
        webhook.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _serialize_collaborator(c: WebsiteCollaborator) -> dict:
    return {
        "id": str(c.id),
        "user_id": str(c.user_id),
        "user_name": getattr(c.user, "display_name", None) or getattr(c.user, "phone", "") or str(c.user_id),
        "role": c.role,
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat(),
    }


class WebsiteCollaboratorListView(APIView):
    """Admin-only (owner or role=owner collaborator) — see
    apps.websites.owner_resolution.user_can_administer_website."""

    permission_classes = [IsAuthenticated]

    def get(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_administer_permission(request.user, website)
        collaborators = website.collaborators.filter(is_active=True).select_related("user").order_by("-created_at")
        return Response([_serialize_collaborator(c) for c in collaborators])


class WebsiteCollaboratorDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, website_id, collaborator_id):
        website = get_object_or_404(Website, id=website_id)
        _require_administer_permission(request.user, website)
        collaborator = get_object_or_404(WebsiteCollaborator, id=collaborator_id, website=website)
        collaborator.is_active = False
        collaborator.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


def _serialize_invite(invite: WebsiteInvite) -> dict:
    return {
        "id": str(invite.id),
        "code": invite.code,
        "role": invite.role,
        "max_uses": invite.max_uses,
        "use_count": invite.use_count,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
        "is_active": invite.is_active,
        "is_redeemable": invite.is_redeemable(),
        "created_at": invite.created_at.isoformat(),
    }


class WebsiteInviteListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_administer_permission(request.user, website)
        return Response([_serialize_invite(i) for i in website.invites.order_by("-created_at")])

    def post(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_administer_permission(request.user, website)
        role = request.data.get("role") or WebsiteCollaboratorRole.EDITOR
        if role not in WebsiteCollaboratorRole.values:
            raise ValidationError({"role": f"role must be one of {WebsiteCollaboratorRole.values}."})
        max_uses = request.data.get("max_uses")
        invite = WebsiteInvite.objects.create(
            website=website, role=role,
            max_uses=int(max_uses) if max_uses else None,
            created_by=request.user,
        )
        return Response(_serialize_invite(invite), status=status.HTTP_201_CREATED)


class WebsiteInviteRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, website_id, invite_id):
        website = get_object_or_404(Website, id=website_id)
        _require_administer_permission(request.user, website)
        invite = get_object_or_404(WebsiteInvite, id=invite_id, website=website)
        invite.is_active = False
        invite.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebsiteInviteRedeemView(APIView):
    """Self-service: an already-authenticated user redeems a code they
    were given out-of-band (mirrors apps.partners.views.redeem_invite,
    the one complete working invite pattern in this codebase)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from django.db import transaction

        code = str(request.data.get("code") or "").strip().upper()
        if not code:
            raise ValidationError({"code": "code is required."})

        with transaction.atomic():
            invite = WebsiteInvite.objects.select_for_update().filter(code=code).first()
            if invite is None:
                raise Http404("Invite not found.")
            if not invite.is_redeemable():
                raise ValidationError({"code": "This invite is no longer valid."})

            website = invite.website
            owner_instance = resolve_owner_object(website.owner_type, website.owner_id)
            owner_user = resolve_owner_user(website.owner_type, owner_instance) if owner_instance else None
            if owner_user is not None and owner_user.id == request.user.id:
                raise ValidationError({"code": "You already own this website."})

            existing = WebsiteCollaborator.objects.filter(website=website, user=request.user).first()
            if existing is None or not existing.is_active:
                if owner_user is not None:
                    check_collaborator_seat_quota(owner_user, website)
                if existing is None:
                    WebsiteCollaborator.objects.create(
                        website=website, user=request.user, role=invite.role, invited_by=invite.created_by,
                    )
                else:
                    existing.is_active = True
                    existing.role = invite.role
                    existing.invited_by = invite.created_by
                    existing.save(update_fields=["is_active", "role", "invited_by", "updated_at"])

            invite.use_count += 1
            invite.save(update_fields=["use_count", "updated_at"])

        return Response({
            "website_id": str(website.id), "website_slug": website.slug, "role": invite.role,
            "owner_type": website.owner_type, "owner_id": str(website.owner_id),
        })


# ---------------------------------------------------------------------
# Authenticated owner CRUD
# ---------------------------------------------------------------------

class WebsiteMineView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner_type = request.query_params.get("owner_type")
        owner_id = request.query_params.get("owner_id")
        if owner_type not in WebsiteOwnerType.values or not owner_id:
            raise ValidationError({"detail": "owner_type and owner_id are required."})
        if not user_can_manage_website(request.user, owner_type, owner_id):
            raise PermissionDenied("You do not manage this owner.")
        existing = Website.objects.filter(owner_type=owner_type, owner_id=owner_id).first()
        if not existing:
            check_websites_quota(request.user)
        owner_instance = resolve_owner_object(owner_type, owner_id)
        if owner_instance is None:
            raise ValidationError({"detail": "Owner not found."})
        template_id = request.query_params.get("template_id")
        website = adapters.get_or_seed_website(owner_type, owner_id, created_by=request.user, template_id=template_id)
        if website is None:
            raise ValidationError({"detail": "Unable to resolve or create a website for this owner."})
        return Response(WebsiteSerializer(website).data, status=status.HTTP_200_OK)

    post = get


class WebsiteTemplateListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner_type = request.query_params.get("owner_type")
        if owner_type not in WebsiteOwnerType.values:
            raise ValidationError({"detail": "owner_type is required."})
        templates = WebsiteTemplate.objects.filter(owner_type=owner_type, is_active=True)
        return Response([
            {"id": str(t.id), "name": t.name, "description": t.description, "thumbnail_url": t.thumbnail_url}
            for t in templates
        ])


class WebsiteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        return Response(WebsiteSerializer(website).data)

    def patch(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        branding_payload = request.data.get("branding")
        if branding_payload:
            require_custom_branding_allowed(request.user, branding_payload)
            validate_branding(branding_payload)
        serializer = WebsiteSerializer(website, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data)


def _custom_domain_payload(website: Website) -> dict:
    return {
        "custom_domain": website.custom_domain,
        "status": website.custom_domain_status,
        "enabled": custom_domains_enabled(),
        "cname_target": getattr(settings, "CLOUDFLARE_FALLBACK_ORIGIN_HOSTNAME", "kingdomimpactventures.org") if website.custom_domain else None,
        "txt_record": website.custom_domain_txt_record or None,
    }


class WebsiteCustomDomainView(APIView):
    """Application code for Cloudflare for SaaS custom hostnames — see
    apps.websites.custom_domains's module docstring for why every branch
    here degrades to a clear 400 rather than acting when
    CLOUDFLARE_API_TOKEN/CLOUDFLARE_ZONE_ID aren't configured (they
    aren't, on this deployment, as of writing this)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)

        if website.custom_domain and website.custom_domain_cloudflare_id and custom_domains_enabled():
            try:
                new_status = check_hostname_status(website.custom_domain_cloudflare_id)
                if new_status != website.custom_domain_status:
                    website.custom_domain_status = new_status
                    website.save(update_fields=["custom_domain_status", "updated_at"])
            except Exception:
                pass  # best-effort refresh; stale status is fine, a crash here isn't

        return Response(_custom_domain_payload(website))

    def post(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)

        if not custom_domains_enabled():
            raise ValidationError({"detail": "Custom domains aren't available on this deployment yet."})

        domain = validate_domain_format(str(request.data.get("custom_domain") or ""))
        if Website.objects.filter(custom_domain=domain).exclude(id=website.id).exists():
            raise ValidationError({"custom_domain": "This domain is already in use by another website."})

        registration = register_custom_hostname(domain)
        website.custom_domain = domain
        website.custom_domain_status = WebsiteCustomDomainStatus.PENDING
        website.custom_domain_cloudflare_id = registration["cloudflare_id"]
        website.custom_domain_txt_record = registration["txt_record"]
        website.updated_by = request.user
        website.save(update_fields=[
            "custom_domain", "custom_domain_status", "custom_domain_cloudflare_id",
            "custom_domain_txt_record", "updated_by", "updated_at",
        ])

        return Response(_custom_domain_payload(website), status=status.HTTP_201_CREATED)

    def delete(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)

        if website.custom_domain_cloudflare_id and custom_domains_enabled():
            try:
                deregister_custom_hostname(website.custom_domain_cloudflare_id)
            except Exception:
                pass  # best-effort — clear our own record either way

        website.custom_domain = None
        website.custom_domain_status = WebsiteCustomDomainStatus.NONE
        website.custom_domain_cloudflare_id = ""
        website.custom_domain_txt_record = {}
        website.save(update_fields=[
            "custom_domain", "custom_domain_status", "custom_domain_cloudflare_id",
            "custom_domain_txt_record", "updated_at",
        ])
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebsitePublicSiteByDomainView(APIView):
    """Resolves a custom domain to its Website's slug — called by the
    website repo's host-based routing (see the plan's Batch E section)
    to know which site to render when a request arrives on a domain
    that isn't kingdomimpactventures.org itself."""

    permission_classes = [AllowAny]

    def get(self, request, domain):
        website = get_object_or_404(
            Website, custom_domain=domain.strip().lower(), custom_domain_status=WebsiteCustomDomainStatus.ACTIVE,
            status=WebsiteStatus.PUBLISHED,
        )
        return Response({"slug": website.slug})


class WebsitePublishView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        require_website_publish_allowed(request.user)
        website.status = WebsiteStatus.PUBLISHED
        website.published_at = timezone.now()
        website.updated_by = request.user
        website.save(update_fields=["status", "published_at", "updated_by", "updated_at"])
        fire_webhook_event(website, WebsiteWebhookEvent.PUBLISHED, {"slug": website.slug})
        return Response(WebsiteSerializer(website).data)


class WebsiteUnpublishView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        website.status = WebsiteStatus.UNPUBLISHED
        website.unpublished_at = timezone.now()
        website.updated_by = request.user
        website.save(update_fields=["status", "unpublished_at", "updated_by", "updated_at"])
        fire_webhook_event(website, WebsiteWebhookEvent.UNPUBLISHED, {"slug": website.slug})
        return Response(WebsiteSerializer(website).data)


class WebsitePageListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        pages = website.pages.all().order_by("sort_order", "created_at")
        return Response(WebsitePageSerializer(pages, many=True).data)

    def post(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        check_pages_quota(request.user, website)
        slug = str(request.data.get("slug") or "").strip().strip("/")
        title = str(request.data.get("title") or "").strip()
        if not title:
            raise ValidationError({"title": "Page title is required."})
        if WebsitePage.objects.filter(website=website, slug=slug).exists():
            raise ValidationError({"slug": "A page with this slug already exists on this website."})
        sections = request.data.get("sections") or []
        kis_content_count = sum(1 for s in sections if isinstance(s, dict) and s.get("type") == "kis_content")
        if kis_content_count:
            check_kis_content_sections_quota(request.user, WebsitePage(website=website, sections=[]), adding=kis_content_count)
        page = WebsitePage.objects.create(
            website=website, slug=slug, title=title, sort_order=int(request.data.get("sort_order") or 0),
            sections=sections, seo=request.data.get("seo") or {}, created_by=request.user, updated_by=request.user,
        )
        return Response(WebsitePageSerializer(page).data, status=status.HTTP_201_CREATED)


class WebsitePageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_page(self, website_id, page_id):
        website = get_object_or_404(Website, id=website_id)
        page = get_object_or_404(WebsitePage, id=page_id, website=website)
        return website, page

    def get(self, request, website_id, page_id):
        website, page = self._get_page(website_id, page_id)
        _require_manage_permission(request.user, website)
        return Response(WebsitePageSerializer(page).data)

    def patch(self, request, website_id, page_id):
        website, page = self._get_page(website_id, page_id)
        _require_manage_permission(request.user, website)
        new_sections = request.data.get("sections")
        if new_sections is not None:
            existing_kis_count = sum(1 for s in (page.sections or []) if isinstance(s, dict) and s.get("type") == "kis_content")
            new_kis_count = sum(1 for s in new_sections if isinstance(s, dict) and s.get("type") == "kis_content")
            if new_kis_count > existing_kis_count:
                check_kis_content_sections_quota(request.user, page, adding=new_kis_count - existing_kis_count)
        serializer = WebsitePageSerializer(page, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data)

    def delete(self, request, website_id, page_id):
        website, page = self._get_page(website_id, page_id)
        _require_manage_permission(request.user, website)
        if page.is_home:
            raise ValidationError({"detail": "The Home page cannot be deleted."})
        page.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebsitePagePublishView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, website_id, page_id):
        website = get_object_or_404(Website, id=website_id)
        page = get_object_or_404(WebsitePage, id=page_id, website=website)
        _require_manage_permission(request.user, website)
        require_website_publish_allowed(request.user)
        page.status = WebsiteStatus.PUBLISHED
        page.published_at = timezone.now()
        page.updated_by = request.user
        page.save(update_fields=["status", "published_at", "updated_by", "updated_at"])
        return Response(WebsitePageSerializer(page).data)


class WebsitePageUnpublishView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, website_id, page_id):
        website = get_object_or_404(Website, id=website_id)
        page = get_object_or_404(WebsitePage, id=page_id, website=website)
        _require_manage_permission(request.user, website)
        page.status = WebsiteStatus.UNPUBLISHED
        page.updated_by = request.user
        page.save(update_fields=["status", "updated_by", "updated_at"])
        return Response(WebsitePageSerializer(page).data)


class WebsitePreviewTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, website_id):
        website = get_object_or_404(Website, id=website_id)
        _require_manage_permission(request.user, website)
        token = sign_website_preview_token(website.id, request.user.id)
        from apps.websites.preview_tokens import WEBSITE_PREVIEW_TOKEN_TTL_SECONDS

        preview_url = f"{_website_public_base_url()}/page/{website.slug}?preview_token={token}"
        return Response({
            "token": token, "preview_url": preview_url, "expires_in": WEBSITE_PREVIEW_TOKEN_TTL_SECONDS,
        })


class WebsiteKisContentSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, target_type):
        owner_type = request.query_params.get("owner_type")
        owner_id = request.query_params.get("owner_id")
        if owner_type not in WebsiteOwnerType.values or not owner_id:
            raise ValidationError({"detail": "owner_type and owner_id are required."})
        if not user_can_manage_website(request.user, owner_type, owner_id):
            raise PermissionDenied("You do not manage this owner.")
        q = str(request.query_params.get("q") or "").strip().lower()
        items = resolve_kis_content_section(
            owner_type=owner_type, owner_id=owner_id,
            section_data={"target_type": target_type, "presentation": {"limit": 50}},
        )
        if q:
            items = [i for i in items if q in str(i.get("title") or "").lower()]
        return Response({"results": items})


class WebsiteKisVideoSearchView(APIView):
    """Lets an owner browse their OWN KIS video content to embed via a
    `kis_video` section — never a platform-wide search. See
    apps.websites.kis_video's module docstring for which owner types
    actually have video content to search (Broadcast Channel, Health
    Institution only — Education and Marketplace have none)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner_type = request.query_params.get("owner_type")
        owner_id = request.query_params.get("owner_id")
        if owner_type not in WebsiteOwnerType.values or not owner_id:
            raise ValidationError({"detail": "owner_type and owner_id are required."})
        if not user_can_manage_website(request.user, owner_type, owner_id):
            raise PermissionDenied("You do not manage this owner.")
        q = str(request.query_params.get("q") or "").strip()
        results = search_owner_kis_videos(owner_type=owner_type, owner_id=owner_id, q=q)
        return Response({"results": results})
