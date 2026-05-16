from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.broadcasts.models import BroadcastHealthInstitution
from apps.verification.services import current_health_institution_verification_status

from .models import (
    AnalyticsMetricType,
    AvailabilityStatus,
    HealthDashboardAnalyticsRecord,
    HealthDashboardAvailabilityDay,
    HealthDashboardAvailabilityService,
    HealthDashboardAvailabilityTime,
    HealthDashboardBlockedTime,
    HealthDashboardCertification,
    HealthDashboardComplianceSummary,
    HealthDashboardContact,
    HealthDashboardCard,
    HealthDashboardFaq,
    HealthDashboardFinancialSummary,
    HealthDashboardGalleryItem,
    HealthDashboardHero,
    HealthDashboardInstitutionLandingPage,
    HealthDashboardInstitutionService,
    HealthDashboardInstitution,
    HealthDashboardOperatingHour,
    HealthDashboardRecurringRule,
    HealthDashboardScheduleEntry,
    HealthDashboardSection,
    HealthDashboardSectionField,
    HealthDashboardSeo,
    HealthDashboardSeoKeyword,
    HealthDashboardServiceAvailability,
    HealthDashboardServiceVisibility,
    HealthDashboardSlotTemplate,
    HealthDashboardSocialLink,
    SectionValueType,
)
from .serializers import (
    HealthDashboardLandingPageSerializer,
    HealthDashboardLandingPageUpsertSerializer,
)
from .services import (
    serialize_dashboard_service,
    sync_dashboard_services_from_broadcast,
    upsert_dashboard_services,
    upsert_landing_page,
)


MANAGE_ROLES = {"owner", "admin", "manager"}
SUPPORTED_TYPES = {"clinic", "hospital", "lab", "diagnostics", "pharmacy", "wellness_center"}
DEFAULT_MODULES_BY_TYPE = {
    "clinic": [
        "patient_intake_flow_analytics",
        "referral_tracking",
        "care_team_assignment_board",
        "chronic_patient_tracking",
        "repeat_visit_monitoring",
    ],
    "hospital": [
        "bed_occupancy_dashboard",
        "department_analytics",
        "emergency_response_metrics",
        "surgery_pipeline_tracker",
        "insurance_claims_monitoring",
        "clinical_event_logs",
    ],
    "lab": [
        "test_order_lifecycle_tracker",
        "sample_status_tracking",
        "result_turnaround_analytics",
        "equipment_usage_analytics",
        "lab_technician_performance",
    ],
    "diagnostics": [
        "imaging_slot_utilization",
        "radiologist_reporting_queue",
        "equipment_load_metrics",
        "report_turnaround_time",
        "referral_sources_heatmap",
    ],
    "pharmacy": [
        "inventory_health_dashboard",
        "low_stock_alerts",
        "expiry_tracking",
        "prescription_verification_logs",
        "refill_compliance_analytics",
        "revenue_per_medication_category",
    ],
    "wellness_center": [
        "program_enrollment_analytics",
        "habit_tracking_metrics",
        "client_progress_reports",
        "subscription_tracking",
        "wellness_challenge_leaderboard",
    ],
}


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _safe_int(value: Any, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _normalize_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "laboratory":
        text = "lab"
    if text == "diagnostics_center":
        text = "diagnostics"
    if text not in SUPPORTED_TYPES:
        return "clinic"
    return text


def _normalize_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    if role in {"owner", "admin", "manager", "staff", "analyst", "member", "unassigned"}:
        return role
    return "unassigned"


def _flatten_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _resolve_role(user, institution: BroadcastHealthInstitution) -> tuple[str, bool, bool]:
    if str(getattr(institution, "owner_user_id", "") or "") == str(getattr(user, "id", "") or ""):
        return "owner", True, True
    profile_owner_id = str(
        getattr(getattr(getattr(institution, "health_profile", None), "profile", None), "user_id", "") or ""
    )
    if profile_owner_id and profile_owner_id == str(getattr(user, "id", "") or ""):
        return "owner", True, True

    member_rows = list(institution.member_rows.all())
    for row in member_rows:
        if str(getattr(row, "user_id", "") or "") == str(getattr(user, "id", "") or ""):
            role = _normalize_role(getattr(row, "role", "member"))
            return role, True, role in MANAGE_ROLES

    user_phone = _flatten_phone(getattr(user, "phone", ""))
    if user_phone:
        for row in member_rows:
            if _flatten_phone(getattr(row, "phone", "")) == user_phone:
                role = _normalize_role(getattr(row, "role", "member"))
                return role, True, role in MANAGE_ROLES

    return "unassigned", False, False


def _resolve_broadcast_institution(user, institution_uid: str) -> tuple[BroadcastHealthInstitution, str, bool, bool]:
    normalized_id = str(institution_uid or "").strip()
    if not normalized_id:
        raise ValidationError({"institution_id": "Institution id is required."})

    rows = list(
        BroadcastHealthInstitution.objects.filter(institution_uid=normalized_id)
        .select_related("owner_user", "health_profile")
        .prefetch_related("member_rows", "service_rows")
        .order_by("-updated_at")
    )
    if not rows:
        raise ValidationError({"detail": "Institution not found."})

    picked = None
    picked_ctx = None
    for row in rows:
        role, is_member, can_manage = _resolve_role(user, row)
        if role == "owner":
            return row, role, is_member, can_manage
        if can_manage:
            picked = row
            picked_ctx = (role, is_member, can_manage)
            continue
        if is_member and picked is None:
            picked = row
            picked_ctx = (role, is_member, can_manage)

    if picked and picked_ctx:
        return picked, picked_ctx[0], picked_ctx[1], picked_ctx[2]

    raise PermissionDenied("You do not have access to this institution.")


def _resolve_dashboard_by_uid(institution_uid: str) -> HealthDashboardInstitution:
    normalized_id = str(institution_uid or "").strip()
    if not normalized_id:
        raise ValidationError({"institution_id": "Institution id is required."})

    dashboard = (
        HealthDashboardInstitution.objects.select_related("broadcast_institution", "landing_page")
        .filter(institution_uid=normalized_id)
        .order_by("-updated_at")
        .first()
    )
    if dashboard:
        return dashboard

    source_row = (
        BroadcastHealthInstitution.objects.filter(institution_uid=normalized_id)
        .select_related("owner_user", "health_profile")
        .prefetch_related("member_rows", "service_rows")
        .order_by("-updated_at")
        .first()
    )
    if not source_row:
        raise ValidationError({"detail": "Institution not found."})
    return _ensure_dashboard_row(source_row)


def _landing_page_url(institution_uid: str) -> str:
    normalized_id = str(institution_uid or "").strip()
    return f"/api/v1/health/institutions/{normalized_id}/landing-page/" if normalized_id else ""


def _serialize_institution_card(dashboard: HealthDashboardInstitution) -> dict[str, Any]:
    card, _ = HealthDashboardCard.objects.get_or_create(dashboard=dashboard)
    landing_page = getattr(dashboard, "landing_page", None)
    has_landing_page = landing_page is not None
    landing_page_published = bool(getattr(landing_page, "is_published", False))
    landing_page_url = _landing_page_url(dashboard.institution_uid) if landing_page_published else ""
    return {
        "institutionId": dashboard.institution_uid,
        "institutionName": dashboard.name,
        "institutionNameClickable": landing_page_published,
        "landingPageUrl": landing_page_url,
        "hasLandingPage": has_landing_page,
        "landingPagePublished": landing_page_published,
        "tagline": str(card.tagline or ""),
        "summary": str(card.short_description or ""),
        "accentColorKey": str(card.accent_color_key or ""),
        "institution_id": dashboard.institution_uid,
        "institution_name": dashboard.name,
        "institution_name_clickable": landing_page_published,
        "landing_page_url": landing_page_url,
        "has_landing_page": has_landing_page,
        "landing_page_published": landing_page_published,
    }


def _iter_accessible_broadcast_institutions(user) -> list[tuple[BroadcastHealthInstitution, str, bool, bool]]:
    user_id = str(getattr(user, "id", "") or "")
    user_phone = _flatten_phone(getattr(user, "phone", ""))

    qs = BroadcastHealthInstitution.objects.select_related("owner_user", "health_profile").prefetch_related(
        "member_rows",
        "service_rows",
    )
    rows = list(
        qs.filter(
            Q(owner_user=user)
            | Q(health_profile__profile__user=user)
            | Q(member_rows__user=user)
            | (Q(member_rows__phone__isnull=False) if user_phone else Q(pk__in=[]))
        ).distinct()
    )

    # Add phone-based matches if needed and not captured by user FK.
    if user_phone:
        phone_rows = list(qs.filter(member_rows__phone__icontains=user_phone).distinct())
        existing_ids = {str(row.id) for row in rows}
        for row in phone_rows:
            if str(row.id) not in existing_ids:
                rows.append(row)

    out: list[tuple[BroadcastHealthInstitution, str, bool, bool]] = []
    seen = set()
    for row in rows:
        key = str(row.id)
        if key in seen:
            continue
        seen.add(key)
        role, is_member, can_manage = _resolve_role(user, row)
        if user_id and role == "owner":
            out.append((row, role, is_member, can_manage))
            continue
        if is_member:
            out.append((row, role, is_member, can_manage))
    return out


def _ensure_dashboard_row(
    row: BroadcastHealthInstitution,
    *,
    sync_services: bool = False,
) -> HealthDashboardInstitution:
    defaults = {
        "institution_uid": str(row.institution_uid or "").strip(),
        "owner_user": row.owner_user or getattr(getattr(row.health_profile, "profile", None), "user", None),
        "institution_type": _normalize_type(row.institution_type),
        "name": str(row.name or "Health Institution").strip() or "Health Institution",
        "is_active": True,
    }
    dashboard, created = HealthDashboardInstitution.objects.get_or_create(
        broadcast_institution=row,
        defaults=defaults,
    )
    if not created:
        updates: list[str] = []
        for field, value in defaults.items():
            if getattr(dashboard, field) != value:
                setattr(dashboard, field, value)
                updates.append(field)
        if updates:
            dashboard.save(update_fields=[*updates, "updated_at"])

    HealthDashboardHero.objects.get_or_create(dashboard=dashboard)
    HealthDashboardContact.objects.get_or_create(dashboard=dashboard)
    HealthDashboardCard.objects.get_or_create(dashboard=dashboard)
    seo, _ = HealthDashboardSeo.objects.get_or_create(dashboard=dashboard)
    if seo.title is None:
        seo.title = ""
        seo.save(update_fields=["title", "updated_at"])
    HealthDashboardFinancialSummary.objects.get_or_create(dashboard=dashboard)
    HealthDashboardComplianceSummary.objects.get_or_create(dashboard=dashboard)

    should_sync_services = sync_services or created or not dashboard.institution_services.exists()
    if should_sync_services:
        sync_dashboard_services_from_broadcast(dashboard)
    return dashboard


def _serialize_service_row(row: HealthDashboardInstitutionService) -> dict[str, Any]:
    return serialize_dashboard_service(row) or {}


def _value_to_storage(value: Any) -> tuple[str, str]:
    if value is None:
        return SectionValueType.NULL, ""
    if isinstance(value, bool):
        return SectionValueType.BOOLEAN, "1" if value else "0"
    if isinstance(value, int):
        return SectionValueType.INTEGER, str(value)
    if isinstance(value, float):
        return SectionValueType.FLOAT, str(value)
    return SectionValueType.STRING, str(value)


def _flatten_section_data(value: Any, path: str = "") -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    if isinstance(value, dict):
        if not value:
            rows.append((path, SectionValueType.EMPTY_OBJECT, ""))
            return rows
        for key, child in value.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}" if path else key_text
            rows.extend(_flatten_section_data(child, next_path))
        return rows
    if isinstance(value, list):
        if not value:
            rows.append((path, SectionValueType.EMPTY_LIST, ""))
            return rows
        for index, child in enumerate(value):
            next_path = f"{path}.{index}" if path else str(index)
            rows.extend(_flatten_section_data(child, next_path))
        return rows
    value_type, value_text = _value_to_storage(value)
    rows.append((path, value_type, value_text))
    return rows


def _storage_to_value(value_type: str, value_text: str) -> Any:
    if value_type == SectionValueType.NULL:
        return None
    if value_type == SectionValueType.BOOLEAN:
        return str(value_text or "").strip() in {"1", "true", "True"}
    if value_type == SectionValueType.INTEGER:
        return _safe_int(value_text, 0)
    if value_type == SectionValueType.FLOAT:
        try:
            return float(value_text)
        except (TypeError, ValueError):
            return 0.0
    if value_type == SectionValueType.EMPTY_LIST:
        return []
    if value_type == SectionValueType.EMPTY_OBJECT:
        return {}
    return str(value_text or "")


def _deep_convert_numeric_dicts(value: Any) -> Any:
    if isinstance(value, dict):
        converted = {key: _deep_convert_numeric_dicts(child) for key, child in value.items()}
        if converted and all(str(key).isdigit() for key in converted.keys()):
            max_index = max(int(key) for key in converted.keys())
            out = [None] * (max_index + 1)
            for key, child in converted.items():
                out[int(key)] = child
            while out and out[-1] is None:
                out.pop()
            return out
        return converted
    if isinstance(value, list):
        return [_deep_convert_numeric_dicts(item) for item in value]
    return value


def _inflate_section_fields(rows: list[HealthDashboardSectionField]) -> dict[str, Any]:
    root: dict[str, Any] = {}
    for row in rows:
        path = str(row.field_path or "").strip()
        value = _storage_to_value(row.value_type, row.value_text)
        if path == "":
            if isinstance(value, dict):
                root.update(value)
            continue
        tokens = [token for token in path.split(".") if token != ""]
        cursor = root
        for token in tokens[:-1]:
            if token not in cursor or not isinstance(cursor[token], dict):
                cursor[token] = {}
            cursor = cursor[token]
        cursor[tokens[-1]] = value
    inflated = _deep_convert_numeric_dicts(root)
    if isinstance(inflated, dict):
        return inflated
    return {}


def _replace_sections(dashboard: HealthDashboardInstitution, raw_sections: Any):
    section_entries = raw_sections if isinstance(raw_sections, list) else []
    keep_ids: set[str] = set()
    for sort_order, entry in enumerate(section_entries):
        if not isinstance(entry, dict):
            continue
        section_uid = str(entry.get("id") or entry.get("section_id") or f"section-{uuid.uuid4().hex}").strip()
        if not section_uid:
            section_uid = f"section-{uuid.uuid4().hex}"
        if section_uid in keep_ids:
            continue
        keep_ids.add(section_uid)
        section, _created = HealthDashboardSection.objects.update_or_create(
            dashboard=dashboard,
            section_uid=section_uid,
            defaults={
                "name": str(entry.get("name") or "Section").strip() or "Section",
                "section_type": str(entry.get("type") or "text").strip() or "text",
                "sort_order": sort_order,
            },
        )
        HealthDashboardSectionField.objects.filter(section=section).delete()
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        flattened = _flatten_section_data(data)
        field_rows = [
            HealthDashboardSectionField(
                section=section,
                field_path=path,
                value_type=value_type,
                value_text=value_text,
            )
            for path, value_type, value_text in flattened
        ]
        if field_rows:
            HealthDashboardSectionField.objects.bulk_create(field_rows)

    HealthDashboardSection.objects.filter(dashboard=dashboard).exclude(section_uid__in=keep_ids).delete()


def _serialize_sections(dashboard: HealthDashboardInstitution) -> list[dict[str, Any]]:
    sections = list(
        dashboard.sections.all().prefetch_related("fields").order_by("sort_order", "created_at")
    )
    out: list[dict[str, Any]] = []
    for section in sections:
        fields = list(section.fields.all().order_by("created_at"))
        data = _inflate_section_fields(fields)
        out.append(
            {
                "id": str(section.section_uid or section.id),
                "name": str(section.name or "Section"),
                "type": str(section.section_type or "text"),
                "data": data,
            }
        )
    return out


def _serialize_profile_editor(dashboard: HealthDashboardInstitution) -> dict[str, Any]:
    hero = getattr(dashboard, "hero", None)
    contact = getattr(dashboard, "contact", None)
    seo = getattr(dashboard, "seo", None)
    landing_page = getattr(dashboard, "landing_page", None)
    landing_address = getattr(landing_page, "address", None) if landing_page else None

    seo_keywords = []
    if seo:
        seo_keywords = [row.keyword for row in seo.keywords.all().order_by("sort_order", "created_at")]

    faq_rows = dashboard.faqs.all().order_by("sort_order", "created_at")
    cert_rows = dashboard.certifications.all().order_by("sort_order", "created_at")
    gallery_rows = dashboard.gallery_items.all().order_by("sort_order", "created_at")
    social_rows = dashboard.social_links.all().order_by("sort_order", "created_at")
    hour_rows = dashboard.operating_hours.all().order_by("sort_order", "created_at")
    service_visibility = {
        row.service_uid: bool(row.is_visible)
        for row in dashboard.service_visibilities.all().order_by("created_at")
    }

    payload = {
        "hero": {
            "imageUrl": str(getattr(hero, "image_url", "") or ""),
            "title": str(getattr(hero, "title", "") or ""),
            "slogan": str(getattr(hero, "slogan", "") or ""),
            "ctaLabel": str(getattr(hero, "cta_label", "Book Now") or "Book Now"),
            "ctaUrl": str(getattr(hero, "cta_url", "") or ""),
        },
        "about": str(dashboard.about_text or ""),
        "gallery": [str(row.media_url or "") for row in gallery_rows],
        "servicesVisibility": service_visibility,
        "staffDisplayEnabled": bool(dashboard.staff_display_enabled),
        "certifications": [str(row.value or "") for row in cert_rows],
        "faqs": [
            {
                "question": str(row.question or ""),
                "answer": str(row.answer or ""),
            }
            for row in faq_rows
        ],
        "seo": {
            "title": str(getattr(seo, "title", "") or ""),
            "description": str(getattr(seo, "description", "") or ""),
            "keywords": seo_keywords,
        },
        "contact": {
            "phone": str(getattr(contact, "phone", "") or ""),
            "email": str(getattr(contact, "email", "") or ""),
            "address": str(getattr(landing_address, "line_one", "") or getattr(contact, "address", "") or ""),
        },
        "socialLinks": [str(row.url or "") for row in social_rows],
        "emergencyBanner": {
            "enabled": bool(dashboard.emergency_banner_enabled),
            "message": str(dashboard.emergency_banner_message or ""),
        },
        "operatingHours": [str(row.value or "") for row in hour_rows],
        "pricingVisibilityEnabled": bool(dashboard.pricing_visibility_enabled),
        "landingBackgroundImageUrl": str(
            getattr(landing_page, "background_image_url", "") or dashboard.landing_background_image_url or ""
        ),
        "landingBackgroundColorKey": str(
            getattr(landing_page, "background_color_key", "") or dashboard.landing_background_color_key or ""
        ),
        "landingLogoUrl": str(getattr(landing_page, "logo_url", "") or dashboard.landing_logo_url or ""),
        "sections": _serialize_sections(dashboard),
    }
    return payload


def _replace_profile_editor(dashboard: HealthDashboardInstitution, payload: dict[str, Any], actor=None):
    hero_payload = payload.get("hero") if isinstance(payload.get("hero"), dict) else {}
    contact_payload = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    seo_payload = payload.get("seo") if isinstance(payload.get("seo"), dict) else {}

    dashboard.about_text = str(payload.get("about") or "")
    dashboard.staff_display_enabled = _safe_bool(payload.get("staffDisplayEnabled"), True)
    dashboard.pricing_visibility_enabled = _safe_bool(payload.get("pricingVisibilityEnabled"), True)

    emergency = payload.get("emergencyBanner") if isinstance(payload.get("emergencyBanner"), dict) else {}
    dashboard.emergency_banner_enabled = _safe_bool(emergency.get("enabled"), False)
    dashboard.emergency_banner_message = str(emergency.get("message") or "")

    dashboard.landing_background_image_url = str(payload.get("landingBackgroundImageUrl") or "")
    dashboard.landing_background_color_key = str(payload.get("landingBackgroundColorKey") or "")
    dashboard.landing_logo_url = str(payload.get("landingLogoUrl") or "")
    dashboard.save(
        update_fields=[
            "about_text",
            "staff_display_enabled",
            "pricing_visibility_enabled",
            "emergency_banner_enabled",
            "emergency_banner_message",
            "landing_background_image_url",
            "landing_background_color_key",
            "landing_logo_url",
            "updated_at",
        ]
    )

    HealthDashboardHero.objects.update_or_create(
        dashboard=dashboard,
        defaults={
            "image_url": str(hero_payload.get("imageUrl") or ""),
            "title": str(hero_payload.get("title") or ""),
            "slogan": str(hero_payload.get("slogan") or ""),
            "cta_label": str(hero_payload.get("ctaLabel") or "Book Now"),
            "cta_url": str(hero_payload.get("ctaUrl") or ""),
        },
    )

    HealthDashboardContact.objects.update_or_create(
        dashboard=dashboard,
        defaults={
            "phone": str(contact_payload.get("phone") or ""),
            "email": str(contact_payload.get("email") or ""),
            "address": str(contact_payload.get("address") or ""),
        },
    )

    upsert_landing_page(
        dashboard,
        {
            "title": str(dashboard.name or ""),
            "description": str(dashboard.about_text or ""),
            "logo_url": str(payload.get("landingLogoUrl") or ""),
            "background_image_url": str(payload.get("landingBackgroundImageUrl") or ""),
            "background_color_key": str(payload.get("landingBackgroundColorKey") or ""),
            "contact": {
                "primary_phone": str(contact_payload.get("phone") or ""),
                "email": str(contact_payload.get("email") or ""),
            },
            "address": {
                "line_one": str(contact_payload.get("address") or ""),
            },
        },
        actor=actor,
        create=False,
    )

    seo, _created = HealthDashboardSeo.objects.update_or_create(
        dashboard=dashboard,
        defaults={
            "title": str(seo_payload.get("title") or ""),
            "description": str(seo_payload.get("description") or ""),
        },
    )

    keyword_entries = seo_payload.get("keywords") if isinstance(seo_payload.get("keywords"), list) else []
    HealthDashboardSeoKeyword.objects.filter(seo=seo).delete()
    keyword_rows = [
        HealthDashboardSeoKeyword(seo=seo, keyword=str(value or "").strip(), sort_order=index)
        for index, value in enumerate(keyword_entries)
        if str(value or "").strip()
    ]
    if keyword_rows:
        HealthDashboardSeoKeyword.objects.bulk_create(keyword_rows)

    faq_entries = payload.get("faqs") if isinstance(payload.get("faqs"), list) else []
    HealthDashboardFaq.objects.filter(dashboard=dashboard).delete()
    faq_rows = []
    for index, row in enumerate(faq_entries):
        if not isinstance(row, dict):
            continue
        question = str(row.get("question") or "").strip()
        if not question:
            continue
        faq_rows.append(
            HealthDashboardFaq(
                dashboard=dashboard,
                question=question,
                answer=str(row.get("answer") or ""),
                sort_order=index,
            )
        )
    if faq_rows:
        HealthDashboardFaq.objects.bulk_create(faq_rows)

    cert_entries = payload.get("certifications") if isinstance(payload.get("certifications"), list) else []
    HealthDashboardCertification.objects.filter(dashboard=dashboard).delete()
    cert_rows = [
        HealthDashboardCertification(dashboard=dashboard, value=str(value or "").strip(), sort_order=index)
        for index, value in enumerate(cert_entries)
        if str(value or "").strip()
    ]
    if cert_rows:
        HealthDashboardCertification.objects.bulk_create(cert_rows)

    gallery_entries = payload.get("gallery") if isinstance(payload.get("gallery"), list) else []
    HealthDashboardGalleryItem.objects.filter(dashboard=dashboard).delete()
    gallery_rows = [
        HealthDashboardGalleryItem(dashboard=dashboard, media_url=str(value or "").strip(), sort_order=index)
        for index, value in enumerate(gallery_entries)
        if str(value or "").strip()
    ]
    if gallery_rows:
        HealthDashboardGalleryItem.objects.bulk_create(gallery_rows)

    social_entries = payload.get("socialLinks") if isinstance(payload.get("socialLinks"), list) else []
    HealthDashboardSocialLink.objects.filter(dashboard=dashboard).delete()
    social_rows = [
        HealthDashboardSocialLink(dashboard=dashboard, url=str(value or "").strip(), sort_order=index)
        for index, value in enumerate(social_entries)
        if str(value or "").strip()
    ]
    if social_rows:
        HealthDashboardSocialLink.objects.bulk_create(social_rows)

    hour_entries = payload.get("operatingHours") if isinstance(payload.get("operatingHours"), list) else []
    HealthDashboardOperatingHour.objects.filter(dashboard=dashboard).delete()
    hour_rows = [
        HealthDashboardOperatingHour(dashboard=dashboard, value=str(value or "").strip(), sort_order=index)
        for index, value in enumerate(hour_entries)
        if str(value or "").strip()
    ]
    if hour_rows:
        HealthDashboardOperatingHour.objects.bulk_create(hour_rows)

    visibility_map = payload.get("servicesVisibility") if isinstance(payload.get("servicesVisibility"), dict) else {}
    HealthDashboardServiceVisibility.objects.filter(dashboard=dashboard).delete()
    visibility_rows = []
    for service_uid, raw_visible in visibility_map.items():
        service_id = str(service_uid or "").strip()
        if not service_id:
            continue
        visibility_rows.append(
            HealthDashboardServiceVisibility(
                dashboard=dashboard,
                service_uid=service_id,
                is_visible=_safe_bool(raw_visible, True),
            )
        )
    if visibility_rows:
        HealthDashboardServiceVisibility.objects.bulk_create(visibility_rows)

    _replace_sections(dashboard, payload.get("sections"))


def _split_times(value: Any) -> list[str]:
    if isinstance(value, list):
        values = [str(item or "").strip() for item in value]
    else:
        values = [segment.strip() for segment in str(value or "").split(",")]
    out: list[str] = []
    seen = set()
    for item in values:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _serialize_availability(dashboard: HealthDashboardInstitution) -> dict[str, Any]:
    days = list(
        dashboard.availability_days.all().prefetch_related("times", "services").order_by("date_value")
    )

    statuses: dict[str, str] = {}
    calendar_times: dict[str, str] = {}
    service_ids: dict[str, list[str]] = {}

    for day in days:
        date_key = day.date_value.isoformat()
        statuses[date_key] = day.status
        times = [row.time_value for row in day.times.all().order_by("sort_order", "created_at")]
        if times:
            calendar_times[date_key] = ", ".join(times)
        ids = [row.service_uid for row in day.services.all().order_by("created_at")]
        if ids:
            service_ids[date_key] = ids

    blocked_times = [
        {
            "id": f"blocked-{row.id}",
            "date": row.date_value.isoformat(),
            "start": row.start_time,
            "end": row.end_time,
            "reason": row.reason,
        }
        for row in dashboard.blocked_times.all().order_by("date_value", "created_at")
    ]

    recurring_rules = [
        {
            "day": row.day_key,
            "frequency": row.frequency,
            "start": row.start_time,
            "end": row.end_time,
        }
        for row in dashboard.recurring_rules.all().order_by("sort_order", "created_at")
    ]

    slots = [
        {
            "day": row.day_key,
            "start": row.start_time,
            "end": row.end_time,
        }
        for row in dashboard.slot_templates.all().order_by("sort_order", "created_at")
    ]

    service_availability = {
        row.service_uid: {
            "enabled": bool(row.enabled),
            "durationMin": int(row.duration_min),
            "slotGapMin": int(row.slot_gap_min),
        }
        for row in dashboard.service_availability_rows.all().order_by("created_at")
    }

    return {
        "timezone": "UTC",
        "calendar_statuses": statuses,
        "calendar_times": calendar_times,
        "calendar_service_ids": service_ids,
        "blocked_times": blocked_times,
        "slots": slots,
        "recurring_rules": recurring_rules,
        "service_availability": service_availability,
    }


def _replace_availability(dashboard: HealthDashboardInstitution, payload: dict[str, Any]):
    statuses = payload.get("calendar_statuses") if isinstance(payload.get("calendar_statuses"), dict) else {}
    if not statuses and isinstance(payload.get("calendarStatuses"), dict):
        statuses = payload.get("calendarStatuses")

    calendar_times = payload.get("calendar_times") if isinstance(payload.get("calendar_times"), dict) else {}
    if not calendar_times and isinstance(payload.get("calendarTimes"), dict):
        calendar_times = payload.get("calendarTimes")

    calendar_service_ids = payload.get("calendar_service_ids") if isinstance(payload.get("calendar_service_ids"), dict) else {}
    if not calendar_service_ids and isinstance(payload.get("calendarServiceIds"), dict):
        calendar_service_ids = payload.get("calendarServiceIds")

    all_dates = set(statuses.keys()) | set(calendar_times.keys()) | set(calendar_service_ids.keys())

    HealthDashboardAvailabilityTime.objects.filter(availability_day__dashboard=dashboard).delete()
    HealthDashboardAvailabilityService.objects.filter(availability_day__dashboard=dashboard).delete()
    HealthDashboardAvailabilityDay.objects.filter(dashboard=dashboard).delete()

    day_rows: list[HealthDashboardAvailabilityDay] = []
    for date_key in sorted(all_dates):
        parsed = parse_date(str(date_key))
        if not parsed:
            continue
        raw_status = str(statuses.get(date_key) or AvailabilityStatus.AVAILABLE)
        status_key = raw_status if raw_status in AvailabilityStatus.values else AvailabilityStatus.AVAILABLE
        day_rows.append(
            HealthDashboardAvailabilityDay(
                dashboard=dashboard,
                date_value=parsed,
                status=status_key,
            )
        )
    if day_rows:
        HealthDashboardAvailabilityDay.objects.bulk_create(day_rows)

    created_days = {
        row.date_value.isoformat(): row
        for row in HealthDashboardAvailabilityDay.objects.filter(dashboard=dashboard)
    }

    time_rows: list[HealthDashboardAvailabilityTime] = []
    service_rows: list[HealthDashboardAvailabilityService] = []

    for date_key, day in created_days.items():
        for index, time_value in enumerate(_split_times(calendar_times.get(date_key))):
            time_rows.append(
                HealthDashboardAvailabilityTime(
                    availability_day=day,
                    time_value=time_value,
                    sort_order=index,
                )
            )

        raw_ids = calendar_service_ids.get(date_key)
        ids = raw_ids if isinstance(raw_ids, list) else []
        seen = set()
        for service_uid in ids:
            service_id = str(service_uid or "").strip()
            if not service_id or service_id in seen:
                continue
            seen.add(service_id)
            service_rows.append(
                HealthDashboardAvailabilityService(
                    availability_day=day,
                    service_uid=service_id,
                )
            )

    if time_rows:
        HealthDashboardAvailabilityTime.objects.bulk_create(time_rows)
    if service_rows:
        HealthDashboardAvailabilityService.objects.bulk_create(service_rows)

    HealthDashboardBlockedTime.objects.filter(dashboard=dashboard).delete()
    blocked_rows = []
    blocked_values = payload.get("blocked_times") if isinstance(payload.get("blocked_times"), list) else []
    for row in blocked_values:
        if not isinstance(row, dict):
            continue
        parsed = parse_date(str(row.get("date") or ""))
        if not parsed:
            continue
        blocked_rows.append(
            HealthDashboardBlockedTime(
                dashboard=dashboard,
                date_value=parsed,
                start_time=str(row.get("start") or "00:00"),
                end_time=str(row.get("end") or "23:59"),
                reason=str(row.get("reason") or ""),
            )
        )
    if blocked_rows:
        HealthDashboardBlockedTime.objects.bulk_create(blocked_rows)

    HealthDashboardRecurringRule.objects.filter(dashboard=dashboard).delete()
    recurring_rows = []
    recurring_values = payload.get("recurring_rules") if isinstance(payload.get("recurring_rules"), list) else []
    for index, row in enumerate(recurring_values):
        if not isinstance(row, dict):
            continue
        recurring_rows.append(
            HealthDashboardRecurringRule(
                dashboard=dashboard,
                day_key=str(row.get("day") or ""),
                frequency=str(row.get("frequency") or "weekly"),
                start_time=str(row.get("start") or ""),
                end_time=str(row.get("end") or ""),
                sort_order=index,
            )
        )
    if recurring_rows:
        HealthDashboardRecurringRule.objects.bulk_create(recurring_rows)

    HealthDashboardSlotTemplate.objects.filter(dashboard=dashboard).delete()
    slot_rows = []
    slot_values = payload.get("slots") if isinstance(payload.get("slots"), list) else []
    for index, row in enumerate(slot_values):
        if not isinstance(row, dict):
            continue
        slot_rows.append(
            HealthDashboardSlotTemplate(
                dashboard=dashboard,
                day_key=str(row.get("day") or ""),
                start_time=str(row.get("start") or ""),
                end_time=str(row.get("end") or ""),
                sort_order=index,
            )
        )
    if slot_rows:
        HealthDashboardSlotTemplate.objects.bulk_create(slot_rows)

    service_availability = payload.get("service_availability") if isinstance(payload.get("service_availability"), dict) else {}
    if not service_availability and isinstance(payload.get("serviceAvailability"), dict):
        service_availability = payload.get("serviceAvailability")

    HealthDashboardServiceAvailability.objects.filter(dashboard=dashboard).delete()
    availability_rows = []
    for service_uid, row in service_availability.items():
        if not isinstance(row, dict):
            continue
        service_id = str(service_uid or "").strip()
        if not service_id:
            continue
        availability_rows.append(
            HealthDashboardServiceAvailability(
                dashboard=dashboard,
                service_uid=service_id,
                enabled=_safe_bool(row.get("enabled"), True),
                duration_min=_safe_int(row.get("durationMin"), 30, minimum=5, maximum=720),
                slot_gap_min=_safe_int(row.get("slotGapMin"), 10, minimum=0, maximum=300),
            )
        )
    if availability_rows:
        HealthDashboardServiceAvailability.objects.bulk_create(availability_rows)


def _serialize_schedule(dashboard: HealthDashboardInstitution) -> dict[str, Any]:
    now = timezone.now()
    entries = list(dashboard.schedule_entries.all().order_by("starts_at", "created_at"))
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    today = 0
    upcoming = 0
    past = 0
    rows = []
    for entry in entries:
        starts_at = entry.starts_at
        if today_start <= starts_at < tomorrow_start:
            today += 1
        elif starts_at >= tomorrow_start:
            upcoming += 1
        else:
            past += 1

        rows.append(
            {
                "id": str(entry.id),
                "title": entry.title,
                "service_uid": entry.service_uid,
                "patient_name": entry.patient_name,
                "status": entry.status,
                "starts_at": starts_at.isoformat(),
                "ends_at": entry.ends_at.isoformat() if entry.ends_at else None,
                "notes": entry.notes,
            }
        )

    return {
        "today": today,
        "upcoming": upcoming,
        "past": past,
        "entries": rows,
    }


def _replace_schedule(dashboard: HealthDashboardInstitution, payload: dict[str, Any]):
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    HealthDashboardScheduleEntry.objects.filter(dashboard=dashboard).delete()
    rows = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        starts_at = parse_datetime(str(row.get("starts_at") or row.get("start") or ""))
        if not starts_at:
            continue
        ends_at = parse_datetime(str(row.get("ends_at") or row.get("end") or ""))
        rows.append(
            HealthDashboardScheduleEntry(
                dashboard=dashboard,
                title=str(row.get("title") or "Schedule Entry"),
                service_uid=str(row.get("service_uid") or row.get("serviceId") or ""),
                patient_name=str(row.get("patient_name") or row.get("patientName") or ""),
                status=str(row.get("status") or "scheduled"),
                starts_at=starts_at,
                ends_at=ends_at,
                notes=str(row.get("notes") or ""),
            )
        )
    if rows:
        HealthDashboardScheduleEntry.objects.bulk_create(rows)


def _serialize_financial(summary: HealthDashboardFinancialSummary) -> dict[str, Any]:
    return {
        "totalRevenueCents": int(summary.total_revenue_cents),
        "insuranceRevenueCents": int(summary.insurance_revenue_cents),
        "directRevenueCents": int(summary.direct_revenue_cents),
        "pendingPaymentsCents": int(summary.pending_payments_cents),
        "refundsCents": int(summary.refunds_cents),
        "disputesCount": int(summary.disputes_count),
    }


def _update_financial(summary: HealthDashboardFinancialSummary, payload: dict[str, Any]):
    summary.total_revenue_cents = _safe_int(payload.get("totalRevenueCents"), summary.total_revenue_cents, minimum=0)
    summary.insurance_revenue_cents = _safe_int(
        payload.get("insuranceRevenueCents"), summary.insurance_revenue_cents, minimum=0
    )
    summary.direct_revenue_cents = _safe_int(payload.get("directRevenueCents"), summary.direct_revenue_cents, minimum=0)
    summary.pending_payments_cents = _safe_int(
        payload.get("pendingPaymentsCents"), summary.pending_payments_cents, minimum=0
    )
    summary.refunds_cents = _safe_int(payload.get("refundsCents"), summary.refunds_cents, minimum=0)
    summary.disputes_count = _safe_int(payload.get("disputesCount"), summary.disputes_count, minimum=0)
    summary.save(
        update_fields=[
            "total_revenue_cents",
            "insurance_revenue_cents",
            "direct_revenue_cents",
            "pending_payments_cents",
            "refunds_cents",
            "disputes_count",
            "updated_at",
        ]
    )


def _serialize_compliance(summary: HealthDashboardComplianceSummary) -> dict[str, Any]:
    return {
        "auditLogCount": int(summary.audit_log_count),
        "pendingCredentialReviews": int(summary.pending_credential_reviews),
        "licenseExpiringSoonCount": int(summary.license_expiring_soon_count),
        "activeConsents": int(summary.active_consents),
        "pendingDocuments": int(summary.pending_documents),
    }


def _update_compliance(summary: HealthDashboardComplianceSummary, payload: dict[str, Any]):
    summary.audit_log_count = _safe_int(payload.get("auditLogCount"), summary.audit_log_count, minimum=0)
    summary.pending_credential_reviews = _safe_int(
        payload.get("pendingCredentialReviews"), summary.pending_credential_reviews, minimum=0
    )
    summary.license_expiring_soon_count = _safe_int(
        payload.get("licenseExpiringSoonCount"), summary.license_expiring_soon_count, minimum=0
    )
    summary.active_consents = _safe_int(payload.get("activeConsents"), summary.active_consents, minimum=0)
    summary.pending_documents = _safe_int(payload.get("pendingDocuments"), summary.pending_documents, minimum=0)
    summary.save(
        update_fields=[
            "audit_log_count",
            "pending_credential_reviews",
            "license_expiring_soon_count",
            "active_consents",
            "pending_documents",
            "updated_at",
        ]
    )


def _time_range_to_start(time_range: str | None):
    now = timezone.now()
    value = str(time_range or "30d").strip().lower()
    if value == "7d":
        return now - timedelta(days=7)
    if value == "30d":
        return now - timedelta(days=30)
    if value == "90d":
        return now - timedelta(days=90)
    if value in {"180d", "6m"}:
        return now - timedelta(days=180)
    if value in {"1y", "365d"}:
        return now - timedelta(days=365)
    return now - timedelta(days=30)


def _serialize_analytics(dashboard: HealthDashboardInstitution, time_range: str | None = None) -> dict[str, Any]:
    since = _time_range_to_start(time_range)
    records = dashboard.analytics_records.filter(occurred_at__gte=since).order_by("occurred_at")

    bookings = []
    consultations = []
    schedules = []
    payments = []
    ratings = []
    views = 0

    for row in records:
        base = {
            "id": str(row.id),
            "label": row.label,
            "value": float(row.value_decimal),
            "created_at": row.occurred_at.isoformat(),
        }
        if row.metric_type == AnalyticsMetricType.BOOKING:
            bookings.append(base)
        elif row.metric_type == AnalyticsMetricType.CONSULTATION:
            consultations.append(base)
        elif row.metric_type == AnalyticsMetricType.SCHEDULE:
            schedules.append(base)
        elif row.metric_type == AnalyticsMetricType.PAYMENT:
            payments.append(
                {
                    "id": str(row.id),
                    "label": row.label,
                    "amount_cents": int(row.amount_cents),
                    "payment_method": row.payment_method,
                    "created_at": row.occurred_at.isoformat(),
                }
            )
        elif row.metric_type == AnalyticsMetricType.RATING:
            ratings.append(
                {
                    "id": str(row.id),
                    "serviceId": row.service_uid,
                    "service_id": row.service_uid,
                    "serviceName": row.label,
                    "service_name": row.label,
                    "userName": row.subject_name,
                    "user_name": row.subject_name,
                    "rating": float(row.value_decimal),
                    "created_at": row.occurred_at.isoformat(),
                }
            )
        elif row.metric_type == AnalyticsMetricType.TRAFFIC:
            views += _safe_int(row.value_decimal, 0)

    return {
        "bookings": bookings,
        "consultations": consultations,
        "schedules": schedules,
        "payments": payments,
        "ratings": ratings,
        "traffic": {"views": views},
    }


def _build_dashboard_schema(dashboard: HealthDashboardInstitution) -> dict[str, Any]:
    financial = getattr(dashboard, "financial_summary", None)
    compliance = getattr(dashboard, "compliance_summary", None)
    if not financial:
        financial = HealthDashboardFinancialSummary.objects.create(dashboard=dashboard)
    if not compliance:
        compliance = HealthDashboardComplianceSummary.objects.create(dashboard=dashboard)

    service_rows = list(dashboard.institution_services.all().prefetch_related("medium_rows").order_by("sort_order", "created_at"))
    services = [item for row in service_rows if (item := _serialize_service_row(row))]

    analytics_payload = _serialize_analytics(dashboard, "30d")
    bookings_count = len(analytics_payload.get("bookings") or [])
    completed_consults = len(analytics_payload.get("consultations") or [])
    schedule_summary = _serialize_schedule(dashboard)
    rating_rows = analytics_payload.get("ratings") or []
    avg_rating = 0.0
    if rating_rows:
        avg_rating = sum(float(row.get("rating") or 0) for row in rating_rows) / max(len(rating_rows), 1)

    payment_rows = analytics_payload.get("payments") or []
    insurance_total = 0
    cash_total = 0
    online_total = 0
    for row in payment_rows:
        method = str(row.get("payment_method") or "").strip().lower()
        cents = _safe_int(row.get("amount_cents"), 0)
        if method == "insurance":
            insurance_total += cents
        elif method == "cash":
            cash_total += cents
        else:
            online_total += cents

    analytics_header = {
        "revenue": {
            "today": int(financial.total_revenue_cents),
            "week": int(financial.total_revenue_cents),
            "month": int(financial.total_revenue_cents),
        },
        "bookingsCount": bookings_count,
        "completedConsultations": completed_consults,
        "pendingSchedules": schedule_summary.get("today", 0),
        "cancellationRate": 0,
        "conversion": {
            "views": int((analytics_payload.get("traffic") or {}).get("views") or 0),
            "bookings": bookings_count,
            "rate": 0,
        },
        "averageRating": round(avg_rating, 2),
        "patientReturnRate": 0,
        "paymentBreakdown": {
            "cash": cash_total,
            "insurance": insurance_total,
            "online": online_total,
        },
    }

    analytics_bundle = {
        "bookingsOverTime": [
            {"label": row.get("created_at", ""), "value": row.get("value", 0)}
            for row in analytics_payload.get("bookings") or []
        ],
        "revenueBreakdown": [
            {"label": "Insurance", "value": insurance_total},
            {"label": "Cash", "value": cash_total},
            {"label": "Online", "value": online_total},
        ],
        "serviceUsageDistribution": [
            {"label": row.get("label", ""), "value": row.get("value", 0)}
            for row in analytics_payload.get("consultations") or []
        ],
        "topServices": [
            {
                "id": str(row.get("id") or f"service-{index + 1}"),
                "label": str(row.get("label") or "Service"),
                "value": _safe_int(row.get("value"), 0),
            }
            for index, row in enumerate(analytics_payload.get("bookings") or [])
        ][:10],
        "topPatients": [
            {
                "id": str(row.get("id") or f"patient-{index + 1}"),
                "label": str(row.get("userName") or row.get("user_name") or "Patient"),
                "value": 1,
            }
            for index, row in enumerate(analytics_payload.get("ratings") or [])
        ][:10],
        "paymentMethodBreakdown": [
            {"id": "cash", "label": "Cash", "value": cash_total},
            {"id": "insurance", "label": "Insurance", "value": insurance_total},
            {"id": "online", "label": "Online", "value": online_total},
        ],
    }

    card_payload = _serialize_institution_card(dashboard)
    return {
        "institutionId": dashboard.institution_uid,
        "type": dashboard.institution_type,
        "healthCard": card_payload,
        "institutionNameClickable": bool(card_payload.get("institutionNameClickable")),
        "landingPageUrl": card_payload.get("landingPageUrl", ""),
        "hasLandingPage": bool(card_payload.get("hasLandingPage")),
        "analyticsHeader": analytics_header,
        "analytics": analytics_bundle,
        "landingPreview": {
            "hero": {
                "imageUrl": getattr(getattr(dashboard, "hero", None), "image_url", "") or "",
                "title": getattr(getattr(dashboard, "hero", None), "title", "") or dashboard.name,
                "slogan": getattr(getattr(dashboard, "hero", None), "slogan", "") or "",
                "ctaLabel": getattr(getattr(dashboard, "hero", None), "cta_label", "") or "Book Now",
                "ctaUrl": getattr(getattr(dashboard, "hero", None), "cta_url", "") or "",
            },
            "about": dashboard.about_text,
            "servicesOverview": [service.get("name", "") for service in services],
            "careTeamPreviewEnabled": bool(dashboard.staff_display_enabled),
            "gallery": [row.media_url for row in dashboard.gallery_items.all().order_by("sort_order", "created_at")],
            "testimonials": [],
            "certifications": [row.value for row in dashboard.certifications.all().order_by("sort_order", "created_at")],
            "operatingHours": [row.value for row in dashboard.operating_hours.all().order_by("sort_order", "created_at")],
            "emergencyNotice": dashboard.emergency_banner_message if dashboard.emergency_banner_enabled else "",
        },
        "services": services,
        "operationalModules": [
            {
                "id": value,
                "title": value.replace("_", " ").title(),
                "description": "",
                "enabled": True,
            }
            for value in DEFAULT_MODULES_BY_TYPE.get(dashboard.institution_type, [])
        ],
        "schedule": {
            "today": schedule_summary.get("today", 0),
            "upcoming": schedule_summary.get("upcoming", 0),
            "past": schedule_summary.get("past", 0),
        },
        "financial": _serialize_financial(financial),
        "compliance": _serialize_compliance(compliance),
        "createdAt": dashboard.created_at.isoformat(),
        "updatedAt": dashboard.updated_at.isoformat(),
    }


class HealthDashboardInstitutionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        results = []
        for institution, role, is_member, can_manage in _iter_accessible_broadcast_institutions(request.user):
            dashboard = _ensure_dashboard_row(institution)
            card = _serialize_institution_card(dashboard)
            verification_summary = current_health_institution_verification_status(institution)
            results.append(
                {
                    "institution_id": dashboard.institution_uid,
                    "institutionId": dashboard.institution_uid,
                    "name": dashboard.name,
                    "type": dashboard.institution_type,
                    "role": role,
                    "is_member": is_member,
                    "can_manage": can_manage,
                    "dashboard_id": str(dashboard.id),
                    "health_card": card,
                    "healthCard": card,
                    "verification_summary": verification_summary,
                    "verificationSummary": verification_summary,
                    "institution_name_clickable": bool(card.get("institutionNameClickable")),
                    "institutionNameClickable": bool(card.get("institutionNameClickable")),
                    "landing_page_url": card.get("landingPageUrl", ""),
                    "landingPageUrl": card.get("landingPageUrl", ""),
                    "has_landing_page": bool(card.get("hasLandingPage")),
                    "hasLandingPage": bool(card.get("hasLandingPage")),
                }
            )
        return Response({"results": results}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request):
        institution_uid = str(
            request.data.get("institutionId")
            or request.data.get("institution_id")
            or ""
        ).strip()
        if not institution_uid:
            raise ValidationError({"institutionId": "Institution id is required."})

        institution, _role, _is_member, can_manage = _resolve_broadcast_institution(request.user, institution_uid)
        if not can_manage:
            raise PermissionDenied("You do not have permission to initialize this dashboard.")

        dashboard = _ensure_dashboard_row(institution)
        forced_type = request.data.get("type")
        if forced_type is not None:
            dashboard.institution_type = _normalize_type(forced_type)
            dashboard.save(update_fields=["institution_type", "updated_at"])

        return Response({"dashboard": _build_dashboard_schema(dashboard)}, status=status.HTTP_201_CREATED)


class HealthDashboardInstitutionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _resolve(self, request, institution_id: str, require_manage: bool = False):
        institution, role, is_member, can_manage = _resolve_broadcast_institution(request.user, institution_id)
        if require_manage and not can_manage:
            raise PermissionDenied("You do not have permission to update this dashboard.")
        dashboard = _ensure_dashboard_row(institution)
        return institution, dashboard, role, is_member, can_manage

    def get(self, request, institution_id: str):
        _institution, dashboard, role, is_member, can_manage = self._resolve(request, institution_id)
        payload = _build_dashboard_schema(dashboard)
        verification_summary = current_health_institution_verification_status(_institution)
        payload["verification_summary"] = verification_summary
        payload["verificationSummary"] = verification_summary
        payload["viewer"] = {
            "role": role,
            "is_member": is_member,
            "can_manage": can_manage,
        }
        return Response(payload, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request, institution_id: str):
        institution, dashboard, _role, _is_member, _can_manage = self._resolve(request, institution_id, require_manage=True)

        if "name" in request.data:
            name = str(request.data.get("name") or "").strip()
            if name:
                institution.name = name
                institution.save(update_fields=["name", "updated_at"])
                dashboard.name = name

        if "type" in request.data:
            dashboard.institution_type = _normalize_type(request.data.get("type"))

        if "is_active" in request.data:
            dashboard.is_active = _safe_bool(request.data.get("is_active"), dashboard.is_active)

        dashboard.save(update_fields=["name", "institution_type", "is_active", "updated_at"])

        if isinstance(request.data.get("schedule"), dict):
            _replace_schedule(dashboard, request.data.get("schedule"))
        if isinstance(request.data.get("financial"), dict):
            financial, _ = HealthDashboardFinancialSummary.objects.get_or_create(dashboard=dashboard)
            _update_financial(financial, request.data.get("financial"))
        if isinstance(request.data.get("compliance"), dict):
            compliance, _ = HealthDashboardComplianceSummary.objects.get_or_create(dashboard=dashboard)
            _update_compliance(compliance, request.data.get("compliance"))

        return Response(_build_dashboard_schema(dashboard), status=status.HTTP_200_OK)


class HealthDashboardAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
        )
        time_range = request.query_params.get("time_range")
        return Response(_serialize_analytics(dashboard, time_range), status=status.HTTP_200_OK)


class HealthDashboardScheduleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
        )
        return Response(_serialize_schedule(dashboard), status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
            require_manage=True,
        )
        _replace_schedule(dashboard, request.data if isinstance(request.data, dict) else {})
        return Response(_serialize_schedule(dashboard), status=status.HTTP_200_OK)


class HealthDashboardServicesView(APIView):
    permission_classes = [IsAuthenticated]

    def _serialize(
        self,
        institution: BroadcastHealthInstitution,
        dashboard: HealthDashboardInstitution,
        *,
        refresh_services: bool = False,
    ):
        if refresh_services or not dashboard.institution_services.exists():
            sync_dashboard_services_from_broadcast(dashboard)
        rows = list(dashboard.institution_services.all().prefetch_related("medium_rows").order_by("sort_order", "created_at"))
        availability = {
            row.service_uid: {
                "enabled": bool(row.enabled),
                "durationMin": int(row.duration_min),
                "slotGapMin": int(row.slot_gap_min),
            }
            for row in dashboard.service_availability_rows.all().order_by("created_at")
        }
        return {
            "services": [item for row in rows if (item := _serialize_service_row(row))],
            "service_availability": availability,
        }

    def get(self, request, institution_id: str):
        institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
        )
        refresh_services = _safe_bool(request.query_params.get("refresh_services"), False)
        return Response(
            self._serialize(institution, dashboard, refresh_services=refresh_services),
            status=status.HTTP_200_OK,
        )

    @transaction.atomic
    def patch(self, request, institution_id: str):
        institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
            require_manage=True,
        )

        updates = request.data if isinstance(request.data, dict) else {}
        rows = updates.get("services") if isinstance(updates.get("services"), list) else []
        if rows:
            upsert_dashboard_services(dashboard, rows)

        service_availability = updates.get("service_availability") if isinstance(updates.get("service_availability"), dict) else {}
        if not service_availability and isinstance(updates.get("serviceAvailability"), dict):
            service_availability = updates.get("serviceAvailability")
        if isinstance(service_availability, dict):
            HealthDashboardServiceAvailability.objects.filter(dashboard=dashboard).delete()
            rows_to_create = []
            for service_uid, row in service_availability.items():
                if not isinstance(row, dict):
                    continue
                service_id = str(service_uid or "").strip()
                if not service_id:
                    continue
                rows_to_create.append(
                    HealthDashboardServiceAvailability(
                        dashboard=dashboard,
                        service_uid=service_id,
                        enabled=_safe_bool(row.get("enabled"), True),
                        duration_min=_safe_int(row.get("durationMin"), 30, minimum=5, maximum=720),
                        slot_gap_min=_safe_int(row.get("slotGapMin"), 10, minimum=0, maximum=300),
                    )
                )
            if rows_to_create:
                HealthDashboardServiceAvailability.objects.bulk_create(rows_to_create)

        return Response(self._serialize(institution, dashboard), status=status.HTTP_200_OK)


class HealthDashboardFinancialView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
        )
        summary, _ = HealthDashboardFinancialSummary.objects.get_or_create(dashboard=dashboard)
        return Response(_serialize_financial(summary), status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
            require_manage=True,
        )
        summary, _ = HealthDashboardFinancialSummary.objects.get_or_create(dashboard=dashboard)
        payload = request.data if isinstance(request.data, dict) else {}
        _update_financial(summary, payload)
        return Response(_serialize_financial(summary), status=status.HTTP_200_OK)


class HealthDashboardComplianceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
        )
        summary, _ = HealthDashboardComplianceSummary.objects.get_or_create(dashboard=dashboard)
        return Response(_serialize_compliance(summary), status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
            require_manage=True,
        )
        summary, _ = HealthDashboardComplianceSummary.objects.get_or_create(dashboard=dashboard)
        payload = request.data if isinstance(request.data, dict) else {}
        _update_compliance(summary, payload)
        return Response(_serialize_compliance(summary), status=status.HTTP_200_OK)


class HealthDashboardProfileEditorView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
            require_manage=True,
        )
        payload = _serialize_profile_editor(dashboard)
        return Response({"profile_editor": payload}, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
            require_manage=True,
        )
        payload = request.data if isinstance(request.data, dict) else {}
        _replace_profile_editor(dashboard, payload, actor=request.user)
        return Response({"profile_editor": _serialize_profile_editor(dashboard)}, status=status.HTTP_200_OK)


class HealthDashboardAvailabilityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
        )
        return Response({"availability": _serialize_availability(dashboard)}, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request, institution_id: str):
        _institution, dashboard, _role, _is_member, _can_manage = HealthDashboardInstitutionDetailView()._resolve(
            request,
            institution_id,
            require_manage=True,
        )
        payload = request.data if isinstance(request.data, dict) else {}
        _replace_availability(dashboard, payload)
        return Response({"availability": _serialize_availability(dashboard)}, status=status.HTTP_200_OK)


class HealthDashboardLandingPageView(APIView):
    permission_classes = [AllowAny]

    def _resolve_for_write(self, request, institution_id: str) -> HealthDashboardInstitution:
        if not getattr(request.user, "is_authenticated", False):
            raise PermissionDenied("Authentication is required.")

        institution, _role, _is_member, can_manage = _resolve_broadcast_institution(request.user, institution_id)
        if not can_manage:
            raise PermissionDenied("Only institution owners, admins, or managers can edit the landing page.")
        return _ensure_dashboard_row(institution)

    def _serialize(self, landing_page: HealthDashboardInstitutionLandingPage) -> dict[str, Any]:
        payload = HealthDashboardLandingPageSerializer(landing_page).data
        payload["institutionNameClickable"] = True
        payload["institution_name_clickable"] = True
        payload["landingPageUrl"] = _landing_page_url(landing_page.dashboard.institution_uid)
        payload["landing_page_url"] = payload["landingPageUrl"]
        return payload

    def get(self, request, institution_id: str):
        dashboard = _resolve_dashboard_by_uid(institution_id)
        landing_page = getattr(dashboard, "landing_page", None)
        if not landing_page:
            raise NotFound("Landing page not found.")

        if not landing_page.is_published:
            can_manage = False
            if getattr(request.user, "is_authenticated", False):
                try:
                    _institution, _role, _is_member, can_manage = _resolve_broadcast_institution(request.user, institution_id)
                except (PermissionDenied, ValidationError):
                    can_manage = False
            if not can_manage:
                raise NotFound("Landing page not found.")

        return Response(self._serialize(landing_page), status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request, institution_id: str):
        dashboard = self._resolve_for_write(request, institution_id)
        if hasattr(dashboard, "landing_page"):
            raise ValidationError({"detail": "Landing page already exists. Use PATCH to update it."})

        serializer = HealthDashboardLandingPageUpsertSerializer(data=request.data if isinstance(request.data, dict) else {})
        serializer.is_valid(raise_exception=True)
        landing_page = upsert_landing_page(dashboard, serializer.validated_data, actor=request.user, create=True)
        return Response(self._serialize(landing_page), status=status.HTTP_201_CREATED)

    @transaction.atomic
    def patch(self, request, institution_id: str):
        dashboard = self._resolve_for_write(request, institution_id)
        if not hasattr(dashboard, "landing_page"):
            raise NotFound("Landing page not found.")

        serializer = HealthDashboardLandingPageUpsertSerializer(data=request.data if isinstance(request.data, dict) else {}, partial=True)
        serializer.is_valid(raise_exception=True)
        landing_page = upsert_landing_page(dashboard, serializer.validated_data, actor=request.user, create=False)
        return Response(self._serialize(landing_page), status=status.HTTP_200_OK)
