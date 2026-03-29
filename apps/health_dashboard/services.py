from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from django.db import transaction

from apps.broadcasts.health_engine_policy import (
    filter_service_medium_pairs,
    is_blocked_service_medium_name,
    should_drop_service_after_medium_cleanup,
)
from apps.broadcasts.models import BroadcastHealthInstitutionService, Medium

from .models import (
    HealthDashboardInstitution,
    HealthDashboardInstitutionLandingPage,
    HealthDashboardInstitutionService,
    HealthDashboardInstitutionServiceMedium,
    HealthDashboardLandingPageAddress,
    HealthDashboardLandingPageContact,
    HealthDashboardLandingPageImage,
    HealthDashboardLandingPageOperatingHour,
    HealthDashboardLandingPageService,
    HealthDashboardLandingPageSocialLink,
)

USD_CENTS_PER_KISC = 10000


def _cents_to_kisc_text(cents_value: Any) -> str:
    try:
        cents = int(cents_value or 0)
    except (TypeError, ValueError):
        cents = 0
    safe = max(0, cents)
    amount = (Decimal(safe) / Decimal(USD_CENTS_PER_KISC)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    text = format(amount, "f").rstrip("0").rstrip(".")
    return text or "0"


def _kisc_to_cents_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    if parsed < 0:
        return 0
    return int((parsed * Decimal(USD_CENTS_PER_KISC)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _get_blocked_service_medium_ids() -> set[str]:
    return {
        str(row.id)
        for row in Medium.objects.only("id", "name")
        if is_blocked_service_medium_name(getattr(row, "name", ""))
    }


def _normalize_medium_payload(
    raw_ids: Any,
    raw_names: Any,
    *,
    blocked_medium_ids: set[str] | None = None,
) -> tuple[list[tuple[str, str]], bool, bool]:
    return filter_service_medium_pairs(
        raw_ids,
        raw_names,
        blocked_medium_ids=blocked_medium_ids or set(),
    )


def _replace_medium_rows(service: HealthDashboardInstitutionService, medium_pairs: list[tuple[str, str]]):
    HealthDashboardInstitutionServiceMedium.objects.filter(service=service).delete()
    rows = [
        HealthDashboardInstitutionServiceMedium(
            service=service,
            medium_uid=medium_uid,
            medium_name=medium_name,
            sort_order=index,
        )
        for index, (medium_uid, medium_name) in enumerate(medium_pairs)
    ]
    if rows:
        HealthDashboardInstitutionServiceMedium.objects.bulk_create(rows)


@transaction.atomic
def sync_dashboard_services_from_broadcast(dashboard: HealthDashboardInstitution):
    broadcast_rows = list(dashboard.broadcast_institution.service_rows.all().order_by("created_at"))
    blocked_medium_ids = _get_blocked_service_medium_ids()
    existing = {
        str(row.service_uid): row
        for row in dashboard.institution_services.all().order_by("created_at")
    }
    keep: set[str] = set()

    for index, source_row in enumerate(broadcast_rows):
        service_uid = str(source_row.service_uid or "").strip() or f"service-{index + 1}"
        medium_pairs, had_mediums, removed_any = _normalize_medium_payload(
            source_row.medium_ids,
            source_row.medium_names,
            blocked_medium_ids=blocked_medium_ids,
        )
        if should_drop_service_after_medium_cleanup(
            had_mediums=had_mediums,
            removed_any=removed_any,
            remaining_pairs=medium_pairs,
        ):
            continue
        keep.add(service_uid)
        defaults = {
            "name": str(source_row.name or f"Service {index + 1}"),
            "description": str(source_row.description or ""),
            "active": bool(source_row.active),
            "base_price_cents": source_row.base_price_cents,
            "sort_order": index,
            "source_broadcast_service": source_row,
        }

        row = existing.get(service_uid)
        if row:
            for field, value in defaults.items():
                setattr(row, field, value)
            row.save(
                update_fields=[
                    "name",
                    "description",
                    "active",
                    "base_price_cents",
                    "sort_order",
                    "source_broadcast_service",
                    "updated_at",
                ]
            )
        else:
            row = HealthDashboardInstitutionService.objects.create(
                dashboard=dashboard,
                service_uid=service_uid,
                **defaults,
            )
        _replace_medium_rows(row, medium_pairs)

    dashboard.institution_services.exclude(service_uid__in=keep).delete()


def serialize_dashboard_service(service: HealthDashboardInstitutionService) -> dict[str, Any] | None:
    medium_rows = list(service.medium_rows.all().order_by("sort_order", "created_at"))
    medium_pairs, had_mediums, removed_any = _normalize_medium_payload(
        [row.medium_uid for row in medium_rows],
        [row.medium_name for row in medium_rows],
        blocked_medium_ids=_get_blocked_service_medium_ids(),
    )
    if should_drop_service_after_medium_cleanup(
        had_mediums=had_mediums,
        removed_any=removed_any,
        remaining_pairs=medium_pairs,
    ):
        return None
    medium_ids = [medium_uid for medium_uid, _medium_name in medium_pairs if str(medium_uid or "").strip()]
    medium_names = [medium_name for _medium_uid, medium_name in medium_pairs if str(medium_name or "").strip()]

    payload: dict[str, Any] = {
        "id": str(service.service_uid or service.id),
        "name": str(service.name or ""),
        "description": str(service.description or ""),
        "active": bool(service.active),
    }
    if service.base_price_cents is not None:
        payload["basePriceCents"] = int(service.base_price_cents)
        payload["basePriceKisc"] = _cents_to_kisc_text(service.base_price_cents)
        payload["base_price_kisc"] = payload["basePriceKisc"]
    if medium_ids:
        payload["medium_ids"] = medium_ids
        payload["mediumIds"] = medium_ids
    if medium_names:
        payload["medium_names"] = medium_names
        payload["mediumNames"] = medium_names
    return payload


@transaction.atomic
def upsert_dashboard_services(dashboard: HealthDashboardInstitution, payload_rows: list[dict[str, Any]]):
    blocked_medium_ids = _get_blocked_service_medium_ids()
    existing_rows = {
        str(row.service_uid): row
        for row in dashboard.institution_services.all()
    }
    existing_broadcast_rows = {
        str(row.service_uid): row
        for row in dashboard.broadcast_institution.service_rows.all()
    }

    keep: set[str] = set()
    for index, row in enumerate(payload_rows):
        if not isinstance(row, dict):
            continue

        service_uid = str(row.get("id") or row.get("service_id") or row.get("serviceId") or "").strip()
        if not service_uid:
            service_uid = f"service-{index + 1}"
        parsed_cents = row.get("basePriceCents")
        if parsed_cents is None or str(parsed_cents).strip() == "":
            parsed_cents = row.get("base_price_cents")
        if parsed_cents is None or str(parsed_cents).strip() == "":
            parsed_cents = _kisc_to_cents_or_none(row.get("basePriceKisc") if "basePriceKisc" in row else row.get("base_price_kisc"))
        if parsed_cents is not None and str(parsed_cents).strip() != "":
            try:
                parsed_cents = max(0, int(float(parsed_cents)))
            except (TypeError, ValueError):
                parsed_cents = None
        else:
            parsed_cents = None
        is_active = bool(row.get("active", True))
        name = str(row.get("name") or f"Service {index + 1}")
        description = str(row.get("description") or "")
        medium_pairs, had_mediums, removed_any = _normalize_medium_payload(
            row.get("medium_ids") or row.get("mediumIds") or [],
            row.get("medium_names") or row.get("mediumNames") or [],
            blocked_medium_ids=blocked_medium_ids,
        )
        if should_drop_service_after_medium_cleanup(
            had_mediums=had_mediums,
            removed_any=removed_any,
            remaining_pairs=medium_pairs,
        ):
            continue

        keep.add(service_uid)

        service_row = existing_rows.get(service_uid)
        if service_row:
            service_row.name = name
            service_row.description = description
            service_row.active = is_active
            service_row.base_price_cents = parsed_cents
            service_row.sort_order = index
            service_row.save(
                update_fields=[
                    "name",
                    "description",
                    "active",
                    "base_price_cents",
                    "sort_order",
                    "updated_at",
                ]
            )
        else:
            service_row = HealthDashboardInstitutionService.objects.create(
                dashboard=dashboard,
                service_uid=service_uid,
                name=name,
                description=description,
                active=is_active,
                base_price_cents=parsed_cents,
                sort_order=index,
            )

        _replace_medium_rows(service_row, medium_pairs)

        # Keep backward compatibility with existing broadcast consumers.
        medium_ids = [medium_uid for medium_uid, _ in medium_pairs if medium_uid]
        medium_names = [medium_name for _, medium_name in medium_pairs if medium_name]
        broadcast_row = existing_broadcast_rows.get(service_uid)
        if broadcast_row:
            broadcast_row.name = name
            broadcast_row.description = description
            broadcast_row.active = is_active
            broadcast_row.base_price_cents = parsed_cents
            broadcast_row.medium_ids = medium_ids
            broadcast_row.medium_names = medium_names
            broadcast_row.save(
                update_fields=[
                    "name",
                    "description",
                    "active",
                    "base_price_cents",
                    "medium_ids",
                    "medium_names",
                    "updated_at",
                ]
            )
            if service_row.source_broadcast_service_id != broadcast_row.id:
                service_row.source_broadcast_service = broadcast_row
                service_row.save(update_fields=["source_broadcast_service", "updated_at"])
        else:
            broadcast_row = BroadcastHealthInstitutionService.objects.create(
                institution=dashboard.broadcast_institution,
                service_uid=service_uid,
                name=name,
                description=description,
                active=is_active,
                base_price_cents=parsed_cents,
                medium_ids=medium_ids,
                medium_names=medium_names,
            )
            service_row.source_broadcast_service = broadcast_row
            service_row.save(update_fields=["source_broadcast_service", "updated_at"])

    dashboard.institution_services.exclude(service_uid__in=keep).delete()
    dashboard.broadcast_institution.service_rows.exclude(service_uid__in=keep).delete()


def upsert_landing_page(
    dashboard: HealthDashboardInstitution,
    validated_payload: dict[str, Any],
    *,
    actor,
    create: bool = False,
) -> HealthDashboardInstitutionLandingPage:
    landing = getattr(dashboard, "landing_page", None)
    if create and landing is not None:
        raise ValueError("Landing page already exists for this institution.")
    if landing is None:
        landing = HealthDashboardInstitutionLandingPage.objects.create(
            dashboard=dashboard,
            title=str(dashboard.name or ""),
            description=str(dashboard.about_text or ""),
            created_by=actor,
            updated_by=actor,
        )

    scalar_fields = (
        "title",
        "description",
        "hero_headline",
        "hero_subheadline",
        "hero_cta_label",
        "hero_cta_url",
        "logo_url",
        "background_image_url",
        "background_color_key",
        "is_published",
    )
    updated = []
    for field in scalar_fields:
        if field in validated_payload:
            setattr(landing, field, validated_payload.get(field))
            updated.append(field)
    landing.updated_by = actor
    updated.append("updated_by")
    landing.save(update_fields=[*updated, "updated_at"] if updated else ["updated_at"])

    if "contact" in validated_payload:
        contact_payload = validated_payload.get("contact") or {}
        HealthDashboardLandingPageContact.objects.update_or_create(
            landing_page=landing,
            defaults={
                "primary_phone": str(contact_payload.get("primary_phone") or ""),
                "secondary_phone": str(contact_payload.get("secondary_phone") or ""),
                "email": str(contact_payload.get("email") or ""),
                "website_url": str(contact_payload.get("website_url") or ""),
                "whatsapp_phone": str(contact_payload.get("whatsapp_phone") or ""),
            },
        )

    if "address" in validated_payload:
        address_payload = validated_payload.get("address") or {}
        HealthDashboardLandingPageAddress.objects.update_or_create(
            landing_page=landing,
            defaults={
                "line_one": str(address_payload.get("line_one") or ""),
                "line_two": str(address_payload.get("line_two") or ""),
                "city": str(address_payload.get("city") or ""),
                "state": str(address_payload.get("state") or ""),
                "postal_code": str(address_payload.get("postal_code") or ""),
                "country": str(address_payload.get("country") or ""),
                "landmark": str(address_payload.get("landmark") or ""),
            },
        )

    if "services" in validated_payload:
        service_rows = validated_payload.get("services") or []
        HealthDashboardLandingPageService.objects.filter(landing_page=landing).delete()
        rows = []
        for index, row in enumerate(service_rows):
            if not isinstance(row, dict):
                continue
            service_uid = str(row.get("institution_service_uid") or "").strip()
            linked_service = None
            if service_uid:
                linked_service = dashboard.institution_services.filter(service_uid=service_uid).first()
            title = str(row.get("title") or getattr(linked_service, "name", "") or "").strip()
            if not title:
                continue
            rows.append(
                HealthDashboardLandingPageService(
                    landing_page=landing,
                    institution_service=linked_service,
                    title=title,
                    description=str(row.get("description") or getattr(linked_service, "description", "") or ""),
                    price_cents=row.get("price_cents")
                    if row.get("price_cents") is not None
                    else getattr(linked_service, "base_price_cents", None),
                    is_active=bool(
                        row.get("is_active")
                        if row.get("is_active") is not None
                        else getattr(linked_service, "active", True)
                    ),
                    sort_order=index,
                )
            )
        if rows:
            HealthDashboardLandingPageService.objects.bulk_create(rows)

    if "images" in validated_payload:
        image_rows = validated_payload.get("images") or []
        HealthDashboardLandingPageImage.objects.filter(landing_page=landing).delete()
        rows = []
        for index, row in enumerate(image_rows):
            if not isinstance(row, dict):
                continue
            image_url = str(row.get("image_url") or "").strip()
            if not image_url:
                continue
            rows.append(
                HealthDashboardLandingPageImage(
                    landing_page=landing,
                    image_url=image_url,
                    caption=str(row.get("caption") or ""),
                    sort_order=index,
                )
            )
        if rows:
            HealthDashboardLandingPageImage.objects.bulk_create(rows)

    if "social_links" in validated_payload:
        social_rows = validated_payload.get("social_links") or []
        HealthDashboardLandingPageSocialLink.objects.filter(landing_page=landing).delete()
        rows = []
        for index, row in enumerate(social_rows):
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            rows.append(
                HealthDashboardLandingPageSocialLink(
                    landing_page=landing,
                    platform=str(row.get("platform") or ""),
                    url=url,
                    sort_order=index,
                )
            )
        if rows:
            HealthDashboardLandingPageSocialLink.objects.bulk_create(rows)

    if "operating_hours" in validated_payload:
        hour_rows = validated_payload.get("operating_hours") or []
        HealthDashboardLandingPageOperatingHour.objects.filter(landing_page=landing).delete()
        rows = []
        for index, row in enumerate(hour_rows):
            if not isinstance(row, dict):
                continue
            day_key = str(row.get("day_key") or "").strip()
            if not day_key:
                continue
            rows.append(
                HealthDashboardLandingPageOperatingHour(
                    landing_page=landing,
                    day_key=day_key,
                    opens_at=str(row.get("opens_at") or ""),
                    closes_at=str(row.get("closes_at") or ""),
                    is_closed=bool(row.get("is_closed", False)),
                    sort_order=index,
                )
            )
        if rows:
            HealthDashboardLandingPageOperatingHour.objects.bulk_create(rows)

    return landing
