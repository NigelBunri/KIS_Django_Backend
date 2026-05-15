from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from django.db.models import QuerySet, Sum

from apps.accounts.family_accessibility import serialize_family_accessibility_preferences


def _safe_count(queryset: QuerySet | None) -> int:
    if queryset is None:
        return 0
    try:
        return int(queryset.count())
    except Exception:
        return 0


def _safe_sum(queryset: QuerySet | None, field: str) -> int:
    if queryset is None:
        return 0
    try:
        return int(queryset.aggregate(total=Sum(field)).get("total") or 0)
    except Exception:
        return 0


def _safe_bool(value: Any) -> bool:
    return bool(value) if value is not None else False


def _surface(
    *,
    key: str,
    title: str,
    subtitle: str,
    route: str,
    metrics: dict[str, Any] | None = None,
    readiness: dict[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "subtitle": subtitle,
        "route": route,
        "metrics": metrics or {},
        "readiness": readiness or {},
    }


def _readiness_score(flags: dict[str, bool]) -> int:
    if not flags:
        return 0
    passed = sum(1 for value in flags.values() if value)
    return round((passed / len(flags)) * 100)


@dataclass
class DashboardCollector:
    key: str
    collect: Callable[[Any], dict[str, Any]]


def _collect_creator_channels(user) -> dict[str, Any]:
    from apps.broadcasts.models import BroadcastChannel, ChannelContent

    channels = BroadcastChannel.objects.filter(owner_user=user, is_deleted=False)
    contents = ChannelContent.objects.filter(channel__in=channels, is_deleted=False)
    public_channels = channels.filter(is_public=True)
    published_contents = contents.filter(status=ChannelContent.Status.PUBLISHED)
    verified_channels = channels.filter(is_verified=True)
    subscriber_count = _safe_sum(channels, "subscriber_count")
    content_count = _safe_count(contents)
    readiness = {
        "analytics": True,
        "content": content_count > 0,
        "moderation": True,
        "verification": _safe_count(verified_channels) > 0,
        "payments": True,
        "members": subscriber_count > 0,
        "accessibility_family_safety": True,
        "launch": _safe_count(public_channels) > 0,
    }
    return {
        "count": _safe_count(channels),
        "verified": _safe_count(verified_channels),
        "surfaces": [
            _surface(
                key=f"channel:{channel.id}",
                title=channel.display_name,
                subtitle=f"@{channel.handle}",
                route=f"broadcast.channel:{channel.id}",
                metrics={
                    "subscribers": int(channel.subscriber_count or 0),
                    "contents": int(channel.content_count or 0),
                    "published": _safe_count(published_contents.filter(channel=channel)),
                },
                readiness={
                    "verified": bool(channel.is_verified),
                    "public": bool(channel.is_public),
                    "studio_ready": True,
                },
            )
            for channel in channels.order_by("-updated_at")[:5]
        ],
        "readiness": readiness,
    }


def _collect_shops(user) -> dict[str, Any]:
    from apps.commerce.models import Product, Shop, ShopService

    shops = Shop.objects.filter(owner=user)
    products = Product.objects.filter(shop__in=shops)
    services = ShopService.objects.filter(shop__in=shops)
    verified_shops = shops.filter(is_verified=True)
    readiness = {
        "analytics": True,
        "content": _safe_count(products) + _safe_count(services) > 0,
        "moderation": True,
        "verification": _safe_count(verified_shops) > 0,
        "payments": True,
        "members": _safe_sum(shops, "followers_count") > 0,
        "accessibility_family_safety": True,
        "launch": _safe_count(shops) > 0,
    }
    return {
        "count": _safe_count(shops),
        "verified": _safe_count(verified_shops),
        "surfaces": [
            _surface(
                key=f"shop:{shop.id}",
                title=shop.name,
                subtitle="Commerce shop",
                route=f"market.shop:{shop.id}",
                metrics={
                    "products": _safe_count(products.filter(shop=shop)),
                    "services": _safe_count(services.filter(shop=shop)),
                    "followers": int(shop.followers_count or 0),
                    "rating_count": int(shop.rating_count or 0),
                },
                readiness={
                    "verified": bool(shop.is_verified),
                    "usd_payments": True,
                    "promotional_credits_non_cash": True,
                },
            )
            for shop in shops.order_by("-updated_at")[:5]
        ],
        "readiness": readiness,
    }


def _collect_education(user) -> dict[str, Any]:
    from apps.broadcasts.models import (
        EducationInstitution,
        EducationInstitutionCourse,
        EducationInstitutionEnrollment,
    )

    institutions = EducationInstitution.objects.filter(owner=user, is_active=True)
    courses = EducationInstitutionCourse.objects.filter(institution__in=institutions)
    enrollments = EducationInstitutionEnrollment.objects.filter(institution__in=institutions)
    verified_count = 0
    for institution in institutions[:25]:
        if _safe_bool((institution.metadata or {}).get("verified")):
            verified_count += 1
    readiness = {
        "analytics": True,
        "content": _safe_count(courses) > 0,
        "moderation": True,
        "verification": verified_count > 0,
        "payments": True,
        "members": _safe_count(enrollments) > 0,
        "accessibility_family_safety": True,
        "launch": _safe_count(institutions) > 0,
    }
    return {
        "count": _safe_count(institutions),
        "verified": verified_count,
        "surfaces": [
            _surface(
                key=f"education:{institution.id}",
                title=institution.name,
                subtitle="Education institution",
                route=f"education.institution:{institution.id}",
                metrics={
                    "courses": _safe_count(courses.filter(institution=institution)),
                    "enrollments": _safe_count(enrollments.filter(institution=institution)),
                },
                readiness={
                    "verified": bool((institution.metadata or {}).get("verified")),
                    "usd_payments": True,
                    "family_learning": True,
                },
            )
            for institution in institutions.order_by("-updated_at")[:5]
        ],
        "readiness": readiness,
    }


def _collect_health(user) -> dict[str, Any]:
    from apps.health_ops.models import HealthInstitution, HealthService

    institutions = HealthInstitution.objects.filter(owner=user, is_active=True)
    services = HealthService.objects.filter(institution__in=institutions, is_active=True)
    readiness = {
        "analytics": True,
        "content": _safe_count(services) > 0,
        "moderation": True,
        "verification": False,
        "payments": True,
        "members": True,
        "accessibility_family_safety": True,
        "launch": _safe_count(institutions) > 0,
    }
    return {
        "count": _safe_count(institutions),
        "verified": 0,
        "surfaces": [
            _surface(
                key=f"health:{institution.id}",
                title=institution.name,
                subtitle="Health provider",
                route=f"health.institution:{institution.id}",
                metrics={"services": _safe_count(services.filter(institution=institution))},
                readiness={
                    "verified": False,
                    "usd_payments": True,
                    "patient_privacy_safe": True,
                },
            )
            for institution in institutions.order_by("-updated_at")[:5]
        ],
        "readiness": readiness,
    }


def _collect_partners(user) -> dict[str, Any]:
    from apps.partners.models import Partner, PartnerMembership, PartnerMembershipStatus, PartnerPost

    owned = Partner.objects.filter(owner=user, is_active=True)
    memberships = PartnerMembership.objects.filter(user=user, status=PartnerMembershipStatus.MEMBER)
    managed_ids = set(str(item) for item in owned.values_list("id", flat=True))
    managed_ids.update(str(item) for item in memberships.values_list("partner_id", flat=True))
    partners = Partner.objects.filter(id__in=managed_ids, is_active=True)
    posts = PartnerPost.objects.filter(partner__in=partners)
    readiness = {
        "analytics": True,
        "content": _safe_count(posts) > 0,
        "moderation": True,
        "verification": False,
        "payments": True,
        "members": _safe_count(memberships) > 0,
        "accessibility_family_safety": True,
        "launch": _safe_count(partners) > 0,
    }
    return {
        "count": _safe_count(partners),
        "verified": 0,
        "surfaces": [
            _surface(
                key=f"partner:{partner.id}",
                title=partner.name,
                subtitle="Partner workspace",
                route=f"partners.workspace:{partner.id}",
                metrics={
                    "posts": _safe_count(posts.filter(partner=partner)),
                    "memberships": _safe_count(memberships.filter(partner=partner)),
                },
                readiness={
                    "verified": False,
                    "roles_permissions": True,
                    "family_safe_media": True,
                },
            )
            for partner in partners.order_by("-updated_at")[:5]
        ],
        "readiness": readiness,
    }


def _collect_partners_safe(user) -> dict[str, Any]:
    try:
        return _collect_partners(user)
    except Exception:
        return {"count": 0, "verified": 0, "surfaces": [], "readiness": {}}


def unified_platform_dashboard_summary(user) -> dict[str, Any]:
    collectors = [
        DashboardCollector("channels", _collect_creator_channels),
        DashboardCollector("shops", _collect_shops),
        DashboardCollector("education", _collect_education),
        DashboardCollector("health", _collect_health),
        DashboardCollector("partners", _collect_partners_safe),
    ]
    sections: dict[str, Any] = {}
    all_surfaces: list[dict[str, Any]] = []
    readiness_totals: dict[str, list[bool]] = {}

    for collector in collectors:
        try:
            section = collector.collect(user)
        except Exception:
            section = {"count": 0, "verified": 0, "surfaces": [], "readiness": {}}
        sections[collector.key] = section
        all_surfaces.extend(section.get("surfaces") or [])
        for key, value in (section.get("readiness") or {}).items():
            readiness_totals.setdefault(key, []).append(bool(value))

    readiness = {
        key: any(values)
        for key, values in readiness_totals.items()
    }
    readiness["secrets_hidden"] = True
    readiness["raw_documents_hidden"] = True
    readiness["raw_payment_data_hidden"] = True
    readiness["private_health_data_hidden"] = True

    family = serialize_family_accessibility_preferences(user)
    counts = {
        "dashboards": len(all_surfaces),
        "channels": sections["channels"]["count"],
        "shops": sections["shops"]["count"],
        "education_institutions": sections["education"]["count"],
        "health_institutions": sections["health"]["count"],
        "partners": sections["partners"]["count"],
        "verified_surfaces": sum(int(sections[key].get("verified") or 0) for key in sections),
    }

    return {
        "version": "phase_20_dashboard_foundation",
        "counts": counts,
        "sections": sections,
        "surfaces": all_surfaces[:12],
        "readiness": {
            **readiness,
            "score": _readiness_score(readiness),
        },
        "placeholders": {
            "analytics": True,
            "content": True,
            "moderation": True,
            "verification": True,
            "payments": True,
            "members": True,
            "accessibility_family_safety": True,
            "launch_readiness": True,
        },
        "privacy": {
            "public_safe": True,
            "no_secrets": True,
            "no_raw_documents": True,
            "no_raw_storage_paths": True,
            "no_private_health_records": True,
            "no_payment_instrument_data": True,
        },
        "family_accessibility": {
            "age_mode": family["preferences"]["age_mode"],
            "navigation_mode": family["preferences"]["navigation_mode"],
            "min_touch_target": family["accessibility"]["min_touch_target"],
            "family_safe_content": family["preferences"]["family_safe_content"],
        },
    }
