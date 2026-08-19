from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.public_web import public_web_base_url, public_web_enabled, safe_public_description
from apps.websites import adapters
from apps.websites.branding import validate_branding
from apps.websites.kis_content_resolvers import resolve_kis_content_section
from apps.websites.models import Website, WebsiteOwnerType, WebsitePage, WebsiteStatus
from apps.websites.owner_resolution import resolve_owner_object, user_can_manage_website
from apps.websites.permissions import (
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


def _serialize_public_section(website: Website, section: dict) -> dict:
    if not isinstance(section, dict):
        return {}
    payload = {"id": section.get("id"), "type": section.get("type"), "data": section.get("data") or {}}
    if section.get("type") == "kis_content":
        payload["resolved_items"] = resolve_kis_content_section(
            owner_type=website.owner_type, owner_id=website.owner_id, section_data=section.get("data") or {},
        )
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
        website = adapters.get_or_seed_website(owner_type, owner_id, created_by=request.user)
        if website is None:
            raise ValidationError({"detail": "Unable to resolve or create a website for this owner."})
        return Response(WebsiteSerializer(website).data, status=status.HTTP_200_OK)

    post = get


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
