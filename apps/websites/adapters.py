"""
Lazy, read-through adapters that seed a new Website + Home WebsitePage
from an owner's existing legacy landing data — Shop's ShopLandingPage,
Health's HealthDashboardInstitutionLandingPage, Education's
EducationInstitution.branding, Partner's PartnerSetting
(key="landing_page_builder"). Run exactly once per owner, the first time
they open the new builder (see views.WebsiteMineView).

None of these adapters write back to, or delete, the legacy tables —
they only read from them. Website.seeded_from_legacy is set True
afterward (even when a real adaptation was skipped, e.g. Health's
ambiguous-match case) so this never re-runs and never overwrites an
owner's own subsequent edits to the new model.
"""
import logging
import uuid

from django.apps import apps as django_apps
from django.db import transaction
from django.utils.text import slugify

from apps.websites.models import Website, WebsiteOwnerType, WebsitePage, WebsiteStatus

logger = logging.getLogger(__name__)


def _unique_website_slug(base: str) -> str:
    base = slugify(base or "site") or "site"
    slug = base
    n = 2
    while Website.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _section(section_type: str, data: dict) -> dict:
    return {"id": str(uuid.uuid4()), "type": section_type, "data": data}


def _create_website_and_home(*, owner_type, owner_id, name, slug_base, branding, status, sections, seeded: bool, created_by=None):
    with transaction.atomic():
        website = Website.objects.create(
            owner_type=owner_type,
            owner_id=owner_id,
            slug=_unique_website_slug(slug_base),
            name=name or "",
            branding=branding or {},
            status=status,
            created_by=created_by,
            seeded_from_legacy=True,
            seeded_from_legacy_at=_now(),
        )
        WebsitePage.objects.create(
            website=website,
            slug="",
            title="Home",
            is_home=True,
            sort_order=0,
            sections=sections,
            status=status,
        )
    return website


def _now():
    from django.utils import timezone
    return timezone.now()


def _adapt_shop(owner_id, created_by=None):
    Shop = django_apps.get_model("commerce", "Shop")
    shop = Shop.objects.filter(pk=owner_id).first()
    if not shop:
        return None
    landing = getattr(shop, "landing_page", None)
    sections = []
    branding = {}
    status = WebsiteStatus.DRAFT
    if landing:
        hero_data = {
            "headline": landing.headline,
            "subheadline": landing.subheadline,
            "image_url": landing.hero_image_url,
            "cta_text": landing.hero_cta_text,
            "cta_url": landing.hero_cta_url,
        }
        if any(hero_data.values()):
            sections.append(_section("hero", hero_data))
        testimonials = list(landing.testimonials.all().order_by("sort_order", "created_at"))
        if testimonials:
            sections.append(_section("testimonials", {
                "items": [
                    {"quote": t.quote, "author": t.author, "role": t.role, "rating": t.rating}
                    for t in testimonials
                ],
            }))
        legacy_sections = landing.builder_data.get("sections") if isinstance(landing.builder_data, dict) else None
        if isinstance(legacy_sections, list):
            for raw in legacy_sections:
                if isinstance(raw, dict) and raw.get("type"):
                    sections.append(_section(str(raw["type"]), raw.get("data") or {}))
        branding = {"logo_url": shop.branding.get("logo_url") if isinstance(shop.branding, dict) else ""}
        status = WebsiteStatus.PUBLISHED if landing.is_published else WebsiteStatus.DRAFT
    return _create_website_and_home(
        owner_type=WebsiteOwnerType.SHOP, owner_id=owner_id, name=shop.name, slug_base=shop.slug or shop.name,
        branding=branding, status=status, sections=sections, seeded=bool(landing), created_by=created_by,
    )


def _adapt_partner(owner_id, created_by=None):
    Partner = django_apps.get_model("partners", "Partner")
    PartnerSetting = django_apps.get_model("partners", "PartnerSetting")
    partner = Partner.objects.filter(pk=owner_id).first()
    if not partner:
        return None
    setting = PartnerSetting.objects.filter(partner_id=owner_id, key="landing_page_builder").first()
    sections = []
    branding = {}
    if setting and isinstance(setting.config, dict):
        # Confirmed (RN ProfileLandingEditorScreen.tsx: loadDraft) that
        # Partner's config is parsed through the same extractDraft() used
        # for Shop/Education — same generic {sections, hero, ...} shape,
        # not a distinct schema.
        config = setting.config
        legacy_sections = config.get("sections")
        if isinstance(legacy_sections, list):
            for raw in legacy_sections:
                if isinstance(raw, dict) and raw.get("type"):
                    sections.append(_section(str(raw["type"]), raw.get("data") or {}))
        hero_data = {
            "headline": config.get("landingHeadline") or config.get("headline") or "",
            "subheadline": config.get("landingSubheadline") or config.get("subheadline") or "",
            "image_url": config.get("landingBackgroundImageUrl") or "",
        }
        if any(hero_data.values()) and not sections:
            sections.insert(0, _section("hero", hero_data))
        branding = {
            "logo_url": config.get("landingLogoUrl") or "",
            "background_image_url": config.get("landingBackgroundImageUrl") or "",
            "background_color_key": config.get("landingBackgroundColorKey") or "",
        }
    return _create_website_and_home(
        owner_type=WebsiteOwnerType.PARTNER, owner_id=owner_id, name=partner.name, slug_base=partner.slug or partner.name,
        branding=branding, status=WebsiteStatus.DRAFT, sections=sections, seeded=bool(setting), created_by=created_by,
    )


def _adapt_education(owner_id, created_by=None):
    EducationInstitution = django_apps.get_model("broadcasts", "EducationInstitution")
    institution = EducationInstitution.objects.filter(pk=owner_id).first()
    if not institution:
        return None
    branding_src = institution.branding if isinstance(institution.branding, dict) else {}
    logo_url = branding_src.get("logo_url") or branding_src.get("logoUrl") or ""
    banner_url = (
        branding_src.get("banner_image_url") or branding_src.get("bannerImageUrl")
        or branding_src.get("cover_image_url") or branding_src.get("coverImageUrl") or ""
    )
    sections = []
    if institution.name or institution.description or banner_url:
        sections.append(_section("hero", {
            "headline": institution.name,
            "subheadline": institution.description,
            "image_url": banner_url,
        }))
    return _create_website_and_home(
        owner_type=WebsiteOwnerType.EDUCATION_INSTITUTION, owner_id=owner_id, name=institution.name,
        slug_base=institution.name,
        branding={"logo_url": logo_url, "background_image_url": banner_url},
        status=WebsiteStatus.DRAFT, sections=sections, seeded=bool(branding_src), created_by=created_by,
    )


def _adapt_health(owner_id, created_by=None):
    HealthInstitution = django_apps.get_model("health_ops", "HealthInstitution")
    HealthDashboardInstitution = django_apps.get_model("health_dashboard", "HealthDashboardInstitution")
    institution = HealthInstitution.objects.filter(pk=owner_id).first()
    if not institution:
        return None

    # No FK exists between health_ops.HealthInstitution (what Website.
    # owner_id points at, and what actually owns HealthService rows) and
    # health_dashboard.HealthDashboardInstitution (what the legacy landing
    # page hangs off) — only a shared owner_user. Match on that; if it's
    # ambiguous (0 or >1 matches), skip adaptation rather than guess.
    candidates = list(HealthDashboardInstitution.objects.filter(owner_user_id=institution.owner_id))
    if len(candidates) != 1:
        logger.warning(
            "website_builder.health_adapter.ambiguous_match",
            extra={"health_institution_id": str(owner_id), "candidate_count": len(candidates)},
        )
        return _create_website_and_home(
            owner_type=WebsiteOwnerType.HEALTH_INSTITUTION, owner_id=owner_id, name=institution.name,
            slug_base=institution.slug or institution.name, branding={}, status=WebsiteStatus.DRAFT,
            sections=[], seeded=False, created_by=created_by,
        )

    dashboard = candidates[0]
    landing = getattr(dashboard, "landing_page", None)
    sections = []
    branding = {
        "logo_url": dashboard.landing_logo_url or "",
        "background_image_url": dashboard.landing_background_image_url or "",
        "background_color_key": dashboard.landing_background_color_key or "",
    }
    status = WebsiteStatus.DRAFT

    if landing:
        branding.update({
            "logo_url": landing.logo_url or branding["logo_url"],
            "background_image_url": landing.background_image_url or branding["background_image_url"],
            "background_color_key": landing.background_color_key or branding["background_color_key"],
        })
        hero_data = {
            "headline": landing.hero_headline or landing.title,
            "subheadline": landing.hero_subheadline or landing.description,
            "cta_text": landing.hero_cta_label,
            "cta_url": landing.hero_cta_url,
        }
        if any(hero_data.values()):
            sections.append(_section("hero", hero_data))

        contact = getattr(landing, "contact", None)
        if contact:
            sections.append(_section("contact_info", {
                "phone": contact.primary_phone, "secondary_phone": contact.secondary_phone,
                "email": contact.email, "website_url": contact.website_url, "whatsapp": contact.whatsapp_phone,
            }))
        address = getattr(landing, "address", None)
        if address:
            sections.append(_section("map", {
                "line_one": address.line_one, "line_two": address.line_two, "city": address.city,
                "state": address.state, "postal_code": address.postal_code, "country": address.country,
            }))
        # HealthDashboardLandingPageService.institution_service points at
        # health_dashboard.HealthDashboardInstitutionService, NOT
        # health_ops.HealthService (what resolve_health_services /
        # kis_content links target) — no reliable id mapping exists
        # between the two, so these come over as a static section rather
        # than a live kis_content link, to avoid silently resolving to
        # the wrong (or no) service.
        services = list(landing.services.filter(is_active=True).order_by("sort_order"))
        if services:
            sections.append(_section("programs_services", {
                "items": [
                    {"title": s.title, "description": s.description, "price_cents": s.price_cents}
                    for s in services
                ],
            }))
        images = list(landing.images.all().order_by("sort_order"))
        if images:
            sections.append(_section("gallery", {"items": [{"image_url": i.image_url, "caption": i.caption} for i in images]}))
        status = WebsiteStatus.PUBLISHED if landing.is_published else WebsiteStatus.DRAFT

    return _create_website_and_home(
        owner_type=WebsiteOwnerType.HEALTH_INSTITUTION, owner_id=owner_id, name=institution.name,
        slug_base=institution.slug or institution.name, branding=branding, status=status,
        sections=sections, seeded=True, created_by=created_by,
    )


def _adapt_broadcast_channel(owner_id, created_by=None):
    BroadcastChannel = django_apps.get_model("broadcasts", "BroadcastChannel")
    channel = BroadcastChannel.objects.filter(pk=owner_id).first()
    if not channel:
        return None
    return _create_website_and_home(
        owner_type=WebsiteOwnerType.BROADCAST_CHANNEL, owner_id=owner_id, name=channel.display_name,
        slug_base=channel.handle, branding={"logo_url": channel.avatar_url}, status=WebsiteStatus.DRAFT,
        sections=[], seeded=False, created_by=created_by,
    )


_ADAPTERS = {
    WebsiteOwnerType.SHOP: _adapt_shop,
    WebsiteOwnerType.HEALTH_INSTITUTION: _adapt_health,
    WebsiteOwnerType.EDUCATION_INSTITUTION: _adapt_education,
    WebsiteOwnerType.PARTNER: _adapt_partner,
    WebsiteOwnerType.BROADCAST_CHANNEL: _adapt_broadcast_channel,
}


def get_or_seed_website(owner_type: str, owner_id, created_by=None) -> "Website | None":
    """Idempotent: returns the existing Website if one already exists for
    this owner, otherwise runs the matching adapter exactly once.

    Deliberately does NOT tier-gate — callers (apps.websites.views) must
    check apps.websites.permissions.check_websites_quota themselves
    before calling this for an owner with no existing Website, so viewing
    an already-created website never breaks just because the owner's
    plan changed after the fact."""
    existing = Website.objects.filter(owner_type=owner_type, owner_id=owner_id).first()
    if existing:
        return existing
    adapter = _ADAPTERS.get(owner_type)
    if adapter is None:
        return None
    return adapter(owner_id, created_by=created_by)
