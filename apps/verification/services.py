from __future__ import annotations

import uuid
from typing import Iterable

from django.db.models import Count, Q
from django.db import transaction
from django.utils import timezone

from .constants import (
    PUBLIC_BADGE_LABELS,
    VerificationBadgeCode,
    VerificationBadgeStatus,
    VerificationCaseStatus,
    VerificationSubjectType,
)
from .models import VerificationAuditEvent, VerificationBadge, VerificationCase, VerificationSubject
from .providers import get_provider_adapter, redact_provider_payload


def normalize_subject_id(subject_id) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(subject_id))
    except (TypeError, ValueError, AttributeError):
        return None


def get_or_create_subject(
    *,
    subject_type: str,
    subject_id,
    owner=None,
    display_name: str = "",
    country: str = "",
    metadata: dict | None = None,
) -> VerificationSubject | None:
    normalized_id = normalize_subject_id(subject_id)
    if not normalized_id:
        return None
    defaults = {
        "owner": owner,
        "display_name": display_name[:255],
        "country": (country or "")[:2].upper(),
        "metadata": metadata or {},
    }
    subject, created = VerificationSubject.objects.get_or_create(
        subject_type=subject_type,
        subject_id=normalized_id,
        defaults=defaults,
    )
    changed = []
    if not created:
        if owner is not None and subject.owner_id != getattr(owner, "id", owner):
            subject.owner = owner
            changed.append("owner")
        if display_name and subject.display_name != display_name[:255]:
            subject.display_name = display_name[:255]
            changed.append("display_name")
        if country and subject.country != country[:2].upper():
            subject.country = country[:2].upper()
            changed.append("country")
        if changed:
            subject.save(update_fields=[*changed, "updated_at"])
    return subject


def public_badges_for_subject(subject_type: str, subject_id) -> list[dict]:
    normalized_id = normalize_subject_id(subject_id)
    if not normalized_id:
        return []
    now = timezone.now()
    qs = (
        VerificationBadge.objects.filter(
            subject__subject_type=subject_type,
            subject__subject_id=normalized_id,
            status=VerificationBadgeStatus.ACTIVE,
            public=True,
        )
        .filter(models_q_not_expired(now))
        .order_by("code")
    )
    return [serialize_public_badge(badge) for badge in qs]


def public_badges_for_subjects(subject_type: str, subject_ids: Iterable) -> dict[str, list[dict]]:
    normalized_ids = [value for value in (normalize_subject_id(item) for item in subject_ids) if value]
    if not normalized_ids:
        return {}
    now = timezone.now()
    rows = (
        VerificationBadge.objects.select_related("subject")
        .filter(
            subject__subject_type=subject_type,
            subject__subject_id__in=normalized_ids,
            status=VerificationBadgeStatus.ACTIVE,
            public=True,
        )
        .filter(models_q_not_expired(now))
        .order_by("code")
    )
    result: dict[str, list[dict]] = {}
    for badge in rows:
        key = str(badge.subject.subject_id)
        result.setdefault(key, []).append(serialize_public_badge(badge))
    return result


def serialize_public_badge(badge: VerificationBadge) -> dict:
    return {
        "code": badge.code,
        "label": badge.label,
        "level": badge.level,
        "issued_at": badge.issued_at.isoformat() if badge.issued_at else None,
        "expires_at": badge.expires_at.isoformat() if badge.expires_at else None,
    }


def _safe_iso(value):
    return value.isoformat() if value else None


def _days_until(value):
    if not value:
        return None
    delta = value - timezone.now()
    if delta.total_seconds() < 0:
        return 0
    return max(0, delta.days + (1 if delta.seconds or delta.microseconds else 0))


def _trust_tier_for(subject: VerificationSubject | None, badges: list[dict]) -> str:
    if not subject:
        return "unverified"
    status_value = str(subject.current_status or "").lower()
    badge_codes = {str(item.get("code") or "") for item in badges}
    if VerificationBadgeCode.OFFICIAL_PARTNER in badge_codes:
        return "official"
    if any(code in badge_codes for code in {
        VerificationBadgeCode.ID_VERIFIED,
        VerificationBadgeCode.LICENSED_PROVIDER,
        VerificationBadgeCode.ACCREDITED_EDUCATION,
        VerificationBadgeCode.TRUSTED_MERCHANT,
    }):
        return "verified_high"
    if badges:
        return "verified"
    if status_value in {VerificationCaseStatus.SUBMITTED, VerificationCaseStatus.IN_REVIEW, VerificationCaseStatus.PROVIDER_PENDING}:
        return "in_review"
    if status_value == VerificationCaseStatus.NEEDS_MORE_INFO:
        return "needs_info"
    if status_value == VerificationCaseStatus.REJECTED:
        return "not_approved"
    return "unverified"


def public_trust_summary(subject_type: str, subject_id, *, include_staff: bool = False) -> dict:
    """Return a public-safe trust surface for any centralized verification subject.

    This intentionally excludes evidence metadata, raw provider payloads, reviewer
    notes, revoke reasons, document names, storage paths, IP addresses, and user
    agent strings. Staff-only aggregate counts are safe for dashboards.
    """
    normalized_id = normalize_subject_id(subject_id)
    if not normalized_id:
        return {
            "subject_type": subject_type,
            "subject_id": str(subject_id or ""),
            "display_name": "",
            "verified": False,
            "status": "",
            "trust_tier": "unverified",
            "trust_label": "Unverified",
            "badges": [],
            "badge_count": 0,
            "last_verified_at": None,
            "next_review_at": None,
            "expiry": {"expires_soon": False, "days_until": None},
            "privacy": {
                "public_safe": True,
                "raw_documents_exposed": False,
                "provider_payload_exposed": False,
                "storage_paths_exposed": False,
            },
        }
    subject = VerificationSubject.objects.filter(subject_type=subject_type, subject_id=normalized_id).first()
    if not subject:
        return {
            "subject_type": subject_type,
            "subject_id": str(normalized_id),
            "display_name": "",
            "verified": False,
            "status": "",
            "trust_tier": "unverified",
            "trust_label": "Unverified",
            "badges": [],
            "badge_count": 0,
            "last_verified_at": None,
            "next_review_at": None,
            "expiry": {"expires_soon": False, "days_until": None},
            "privacy": {
                "public_safe": True,
                "raw_documents_exposed": False,
                "provider_payload_exposed": False,
                "storage_paths_exposed": False,
            },
        }

    now = timezone.now()
    active_badge_qs = (
        subject.badges.filter(status=VerificationBadgeStatus.ACTIVE, public=True)
        .filter(models_q_not_expired(now))
        .order_by("code")
    )
    badges = [serialize_public_badge(badge) for badge in active_badge_qs]
    next_expiry = (
        subject.badges.filter(status=VerificationBadgeStatus.ACTIVE, public=True, expires_at__isnull=False)
        .filter(expires_at__gt=now)
        .order_by("expires_at")
        .values_list("expires_at", flat=True)
        .first()
    )
    latest_case = subject.cases.order_by("-created_at").only("id", "status", "level", "provider", "submitted_at", "reviewed_at", "expires_at").first()
    next_review_at = next_expiry or getattr(latest_case, "expires_at", None)
    days_until = _days_until(next_review_at)
    tier = _trust_tier_for(subject, badges)
    trust_labels = {
        "official": "Official verified",
        "verified_high": "Strongly verified",
        "verified": "Verified",
        "in_review": "Verification in review",
        "needs_info": "Needs more information",
        "not_approved": "Not approved",
        "unverified": "Unverified",
    }
    payload = {
        "subject_type": subject.subject_type,
        "subject_id": str(subject.subject_id),
        "display_name": subject.display_name,
        "verified": bool(badges),
        "status": subject.current_status,
        "level": subject.current_level,
        "trust_tier": tier,
        "trust_label": trust_labels.get(tier, "Unverified"),
        "badges": badges,
        "badge_count": len(badges),
        "last_verified_at": _safe_iso(subject.last_verified_at),
        "next_review_at": _safe_iso(next_review_at),
        "latest_case": {
            "id": str(latest_case.id),
            "status": latest_case.status,
            "level": latest_case.level,
            "provider": latest_case.provider,
            "submitted_at": _safe_iso(latest_case.submitted_at),
            "reviewed_at": _safe_iso(latest_case.reviewed_at),
        }
        if latest_case
        else None,
        "expiry": {
            "expires_soon": days_until is not None and days_until <= 30,
            "days_until": days_until,
        },
        "privacy": {
            "public_safe": True,
            "raw_documents_exposed": False,
            "provider_payload_exposed": False,
            "storage_paths_exposed": False,
            "revoke_reasons_exposed": False,
        },
    }
    if include_staff:
        payload["staff_evidence"] = {
            "case_count": subject.cases.count(),
            "active_badge_count": len(badges),
            "revoked_badge_count": subject.badges.filter(status=VerificationBadgeStatus.REVOKED).count(),
            "expired_badge_count": subject.badges.filter(status=VerificationBadgeStatus.EXPIRED).count(),
            "audit_event_count": subject.audit_events.count(),
            "open_case_count": subject.cases.filter(status__in=STAFF_QUEUE_STATUSES).count(),
        }
    return payload


def verification_summary(subject_type: str, subject_id) -> dict:
    normalized_id = normalize_subject_id(subject_id)
    if not normalized_id:
        return {"verified": False, "badges": []}
    subject = VerificationSubject.objects.filter(
        subject_type=subject_type,
        subject_id=normalized_id,
    ).first()
    if not subject:
        return {"verified": False, "badges": []}
    badges = public_badges_for_subject(subject_type, normalized_id)
    return {
        "verified": bool(badges),
        "status": subject.current_status,
        "level": subject.current_level,
        "last_verified_at": subject.last_verified_at.isoformat() if subject.last_verified_at else None,
        "badges": badges,
    }


def user_subject_for(user) -> VerificationSubject | None:
    display_name = getattr(user, "display_name", "") or getattr(user, "email", "") or getattr(user, "phone", "")
    return get_or_create_subject(
        subject_type=VerificationSubjectType.USER,
        subject_id=getattr(user, "id", None),
        owner=user,
        display_name=display_name,
        country=getattr(user, "country", "") or "",
    )


def current_user_verification_status(user) -> dict:
    subject = user_subject_for(user)
    summary = verification_summary(VerificationSubjectType.USER, getattr(user, "id", None))
    latest_case = None
    if subject:
        latest_case = subject.cases.order_by("-created_at").first()
    summary["case"] = serialize_case_status(latest_case) if latest_case else None
    return summary


def _public_channel_trust(channel) -> dict:
    badges = []
    for item in getattr(channel, "verification_badges", []) or []:
        code = str(item.get("code") if isinstance(item, dict) else item).strip()
        if not code:
            continue
        badges.append({"code": code, "label": PUBLIC_BADGE_LABELS.get(code, code.replace("_", " ").title()), "level": "", "issued_at": None, "expires_at": None})
    verified = bool(getattr(channel, "is_verified", False) or badges)
    return {
        "surface": "channel",
        "subject_type": "broadcast_channel",
        "subject_id": str(getattr(channel, "id", "")),
        "display_name": getattr(channel, "display_name", "") or getattr(channel, "handle", ""),
        "handle": getattr(channel, "handle", ""),
        "verified": verified,
        "status": "approved" if verified else "unverified",
        "trust_tier": "verified" if verified else "unverified",
        "trust_label": "Verified channel" if verified else "Channel",
        "badges": badges,
        "badge_count": len(badges),
        "privacy": {
            "public_safe": True,
            "raw_documents_exposed": False,
            "provider_payload_exposed": False,
            "storage_paths_exposed": False,
        },
    }


def unified_identity_trust_overview(user, *, include_staff: bool = False) -> dict:
    subjects = VerificationSubject.objects.filter(owner=user).order_by("subject_type", "display_name", "created_at")
    summaries = [public_trust_summary(subject.subject_type, subject.subject_id, include_staff=include_staff) for subject in subjects]
    by_type = {}
    for item in summaries:
        key = item["subject_type"]
        row = by_type.setdefault(key, {"total": 0, "verified": 0, "open": 0, "expiring": 0})
        row["total"] += 1
        row["verified"] += 1 if item.get("verified") else 0
        row["open"] += 1 if item.get("trust_tier") in {"in_review", "needs_info"} else 0
        row["expiring"] += 1 if item.get("expiry", {}).get("expires_soon") else 0

    channels = []
    try:
        from apps.broadcasts.models import BroadcastChannel

        channels = [_public_channel_trust(channel) for channel in BroadcastChannel.objects.filter(owner_user=user, is_deleted=False).order_by("display_name")[:25]]
    except Exception:
        channels = []

    kcan_summary = None
    try:
        from apps.partners.models import Partner

        partner = Partner.objects.filter(slug="kcan").first()
        if partner:
            kcan_summary = public_trust_summary(VerificationSubjectType.PARTNER, partner.id, include_staff=include_staff)
            kcan_summary["surface"] = "bible_kcan_publisher"
    except Exception:
        kcan_summary = None

    active_badges = VerificationBadge.objects.filter(subject__owner=user, status=VerificationBadgeStatus.ACTIVE, public=True).filter(models_q_not_expired(timezone.now())).count()
    expiring_cases, expiring_badges = expiring_verification_items(days=30)
    user_expiring_badges = [badge for badge in expiring_badges[:200] if badge.subject.owner_id == getattr(user, "id", None)]
    payload = {
        "generated_at": timezone.now().isoformat(),
        "viewer": {"is_staff": bool(getattr(user, "is_staff", False))},
        "counts": {
            "subjects": len(summaries),
            "verified_subjects": sum(1 for item in summaries if item.get("verified")),
            "active_public_badges": active_badges,
            "expiring_badges_30d": len(user_expiring_badges),
            "channels": len(channels),
            "verified_channels": sum(1 for item in channels if item.get("verified")),
        },
        "by_type": by_type,
        "subjects": summaries,
        "channels": channels,
        "bible_kcan_publisher": kcan_summary,
        "privacy": {
            "public_safe": True,
            "raw_documents_exposed": False,
            "provider_payload_exposed": False,
            "storage_paths_exposed": False,
            "staff_only_evidence_visible": bool(include_staff),
        },
        "surfaces_ready": {
            "profiles": True,
            "channels": True,
            "partners": True,
            "shops": True,
            "health": True,
            "education": True,
            "bible_kcan": bool(kcan_summary),
            "commerce_sellers": True,
            "broadcasts": True,
        },
    }
    if include_staff:
        open_cases = filter_staff_verification_cases({}).count()
        pending_expiry_cases = expiring_cases.count()
        pending_expiry_badges = expiring_badges.count()
        payload["staff_evidence"] = {
            "open_case_count": open_cases,
            "expiring_case_count_30d": pending_expiry_cases,
            "expiring_badge_count_30d": pending_expiry_badges,
            "recent_audit_count": VerificationAuditEvent.objects.filter(created_at__gte=timezone.now() - timezone.timedelta(days=7)).count(),
            "suspicious_signals": suspicious_verification_signals(),
        }
    return payload


def _provider_case_lookup_id(value: str | None) -> str:
    text = str(value or "").strip()
    if text.startswith("sandbox:"):
        return text.rsplit(":", 1)[-1]
    return text


def _apply_provider_handoff(case: VerificationCase, adapter, *, manual_message: str) -> VerificationCase:
    provider_result = adapter.start_case(case)
    if provider_result.get("sandbox_handoff_ready"):
        case.status = VerificationCaseStatus.PROVIDER_PENDING
        case.provider_status = "sandbox_pending"
    else:
        case.provider_status = "configured" if provider_result.get("configured") else "not_configured"
    case.provider_case_id = provider_result.get("reference", "")
    case.provider_payload = {
        "provider": provider_result.get("provider"),
        "configured": provider_result.get("configured"),
        "live_calls_enabled": bool(provider_result.get("live_calls_enabled")),
        "sandbox_enabled": bool(provider_result.get("sandbox_enabled")),
        "sandbox_handoff_ready": bool(provider_result.get("sandbox_handoff_ready")),
        "sandbox_network_enabled": bool(provider_result.get("sandbox_network_enabled")),
        "live_call_made": bool(provider_result.get("live_call_made")),
        "provider_request": redact_provider_payload(provider_result.get("provider_request") or {}),
        "provider_response": redact_provider_payload(provider_result.get("provider_response") or {}),
    }
    case.public_summary = {
        "next_action": provider_result.get("next_action"),
        "message": (
            "Verification request is waiting for provider sandbox callback."
            if provider_result.get("sandbox_handoff_ready")
            else manual_message
        ),
    }
    case.save(update_fields=["status", "provider_status", "provider_case_id", "provider_payload", "public_summary", "updated_at"])
    case.subject.current_status = case.status
    case.subject.current_level = case.level
    case.subject.save(update_fields=["current_status", "current_level", "updated_at"])
    return case


def shop_subject_for(shop) -> VerificationSubject | None:
    owner = getattr(shop, "owner", None)
    return get_or_create_subject(
        subject_type=VerificationSubjectType.SHOP,
        subject_id=getattr(shop, "id", None),
        owner=owner,
        display_name=getattr(shop, "name", "") or "",
        country=getattr(owner, "country", "") or "",
        metadata={"legacy_model": "commerce.Shop"},
    )


def current_shop_verification_status(shop) -> dict:
    summary = verification_summary(VerificationSubjectType.SHOP, getattr(shop, "id", None))
    normalized_id = normalize_subject_id(getattr(shop, "id", None))
    subject = None
    if normalized_id:
        subject = VerificationSubject.objects.filter(
            subject_type=VerificationSubjectType.SHOP,
            subject_id=normalized_id,
        ).first()
    latest_case = None
    if subject:
        latest_case = subject.cases.order_by("-created_at").first()
    summary["case"] = serialize_case_status(latest_case) if latest_case else None
    return summary


def partner_subject_for(partner) -> VerificationSubject | None:
    owner = getattr(partner, "owner", None)
    profile = getattr(partner, "organization_profile", None)
    legal_name = getattr(profile, "legal_name", "") if profile else ""
    display_name = legal_name or getattr(partner, "name", "") or ""
    return get_or_create_subject(
        subject_type=VerificationSubjectType.PARTNER,
        subject_id=getattr(partner, "id", None),
        owner=owner,
        display_name=display_name,
        country=getattr(owner, "country", "") or "",
        metadata={"legacy_model": "partners.Partner"},
    )


def current_partner_verification_status(partner) -> dict:
    summary = verification_summary(VerificationSubjectType.PARTNER, getattr(partner, "id", None))
    normalized_id = normalize_subject_id(getattr(partner, "id", None))
    subject = None
    if normalized_id:
        subject = VerificationSubject.objects.filter(
            subject_type=VerificationSubjectType.PARTNER,
            subject_id=normalized_id,
        ).first()
    latest_case = None
    if subject:
        latest_case = subject.cases.order_by("-created_at").first()
    summary["case"] = serialize_case_status(latest_case) if latest_case else None
    return summary


def health_institution_subject_for(institution) -> VerificationSubject | None:
    owner = getattr(institution, "owner", None) or getattr(institution, "owner_user", None)
    display_name = getattr(institution, "name", "") or ""
    metadata = {
        "legacy_model": f"{institution.__class__.__module__}.{institution.__class__.__name__}",
        "institution_type": getattr(institution, "institution_type", "") or "",
    }
    institution_uid = getattr(institution, "institution_uid", "")
    if institution_uid:
        metadata["institution_uid"] = str(institution_uid)
    return get_or_create_subject(
        subject_type=VerificationSubjectType.HEALTH_INSTITUTION,
        subject_id=getattr(institution, "id", None),
        owner=owner,
        display_name=display_name,
        country=getattr(owner, "country", "") or "",
        metadata=metadata,
    )


def current_health_institution_verification_status(institution) -> dict:
    summary = verification_summary(VerificationSubjectType.HEALTH_INSTITUTION, getattr(institution, "id", None))
    normalized_id = normalize_subject_id(getattr(institution, "id", None))
    subject = None
    if normalized_id:
        subject = VerificationSubject.objects.filter(
            subject_type=VerificationSubjectType.HEALTH_INSTITUTION,
            subject_id=normalized_id,
        ).first()
    latest_case = None
    if subject:
        latest_case = subject.cases.order_by("-created_at").first()
    summary["case"] = serialize_case_status(latest_case) if latest_case else None
    return summary


def sanitize_health_evidence_metadata(metadata: dict | None) -> dict:
    metadata = metadata or {}
    safe = {
        "legal_registration": _safe_reference_list(metadata.get("legal_registration")),
        "address": _safe_reference_list(metadata.get("address")),
        "domain_phone": _safe_reference_list(metadata.get("domain_phone")),
        "medical_license": _safe_reference_list(metadata.get("medical_license")),
        "accreditation": _safe_reference_list(metadata.get("accreditation")),
        "staff_authorization": _safe_reference_list(metadata.get("staff_authorization")),
        "expiry": _safe_reference_list(metadata.get("expiry")),
        "private_references_only": True,
    }
    extra = metadata.get("extra")
    if isinstance(extra, dict):
        safe["extra"] = {
            str(key): value
            for key, value in extra.items()
            if key not in {"raw", "raw_document", "document_base64", "base64", "image_base64", "document_data"}
            and not (isinstance(value, str) and value.strip().lower().startswith("data:"))
        }
    return {key: value for key, value in safe.items() if value not in (None, [], {})}


@transaction.atomic
def start_health_institution_verification_case(*, institution, actor, evidence_metadata: dict | None = None, provider: str = "") -> VerificationCase | None:
    subject = health_institution_subject_for(institution)
    if not subject:
        return None
    adapter = get_provider_adapter(provider)
    now = timezone.now()
    case = VerificationCase.objects.create(
        subject=subject,
        requested_by=actor,
        level="licensed_health",
        status=VerificationCaseStatus.SUBMITTED,
        provider=adapter.name,
        provider_status="not_configured",
        evidence_metadata=sanitize_health_evidence_metadata(evidence_metadata),
        submitted_at=now,
        public_summary={
            "next_action": "manual_review",
            "message": "Health institution verification request received. Manual review is required for licensing/accreditation.",
        },
    )
    _apply_provider_handoff(
        case,
        adapter,
        manual_message="Health institution verification request received. Manual review is required for licensing/accreditation.",
    )
    record_audit_event(subject=subject, case=case, actor=actor, action="health_institution_case.started", provider=case.provider)
    return case


@transaction.atomic
def review_health_institution_case(*, case: VerificationCase, actor, action: str, notes: str = "", badge_codes: list[str] | None = None):
    if case.subject.subject_type != VerificationSubjectType.HEALTH_INSTITUTION:
        raise ValueError("Verification case is not a health institution case.")
    now = timezone.now()
    case.reviewed_by = actor
    case.reviewed_at = now
    if notes:
        case.reviewer_notes = notes
    issued_badges = []
    if action == "approve":
        case.status = VerificationCaseStatus.APPROVED
        case.subject.current_status = VerificationCaseStatus.APPROVED
        case.subject.current_level = case.level
        case.subject.last_verified_at = now
        case.subject.save(update_fields=["current_status", "current_level", "last_verified_at", "updated_at"])
        issued_badges = issue_health_institution_badges(case=case, actor=actor, badge_codes=badge_codes)
    elif action == "reject":
        case.status = VerificationCaseStatus.REJECTED
        case.subject.current_status = VerificationCaseStatus.REJECTED
        case.subject.save(update_fields=["current_status", "updated_at"])
    elif action == "needs_more_info":
        case.status = VerificationCaseStatus.NEEDS_MORE_INFO
        case.subject.current_status = VerificationCaseStatus.NEEDS_MORE_INFO
        case.subject.save(update_fields=["current_status", "updated_at"])
    else:
        raise ValueError("Unsupported review action.")
    case.save(update_fields=["reviewed_by", "reviewed_at", "reviewer_notes", "status", "updated_at"])
    record_audit_event(
        subject=case.subject,
        case=case,
        actor=actor,
        action=f"health_institution_case.{action}",
        metadata={"badge_codes": [badge.code for badge in issued_badges]},
    )
    return case, issued_badges


def issue_health_institution_badges(*, case: VerificationCase, actor=None, badge_codes: list[str] | None = None) -> list[VerificationBadge]:
    allowed = {
        VerificationBadgeCode.VERIFIED_HEALTH_INSTITUTION,
        VerificationBadgeCode.LICENSED_PROVIDER,
    }
    selected = [code for code in (badge_codes or []) if code in allowed] or [
        VerificationBadgeCode.VERIFIED_HEALTH_INSTITUTION,
        VerificationBadgeCode.LICENSED_PROVIDER,
    ]
    issued = []
    now = timezone.now()
    for code in selected:
        badge, _created = VerificationBadge.objects.update_or_create(
            subject=case.subject,
            code=code,
            defaults={
                "case": case,
                "label": PUBLIC_BADGE_LABELS.get(code, code.replace("_", " ").title()),
                "level": case.level,
                "status": VerificationBadgeStatus.ACTIVE,
                "public": True,
                "issued_by": actor if getattr(actor, "is_authenticated", False) else None,
                "issued_at": now,
                "revoked_at": None,
                "revoke_reason": "",
            },
        )
        issued.append(badge)
    return issued


def education_institution_subject_for(institution) -> VerificationSubject | None:
    owner = getattr(institution, "owner", None)
    return get_or_create_subject(
        subject_type=VerificationSubjectType.EDUCATION_INSTITUTION,
        subject_id=getattr(institution, "id", None),
        owner=owner,
        display_name=getattr(institution, "name", "") or "",
        country=getattr(owner, "country", "") or "",
        metadata={
            "legacy_model": f"{institution.__class__.__module__}.{institution.__class__.__name__}",
            "institution_type": getattr(institution, "institution_type", "") or "",
        },
    )


def current_education_institution_verification_status(institution) -> dict:
    summary = verification_summary(VerificationSubjectType.EDUCATION_INSTITUTION, getattr(institution, "id", None))
    normalized_id = normalize_subject_id(getattr(institution, "id", None))
    subject = None
    if normalized_id:
        subject = VerificationSubject.objects.filter(
            subject_type=VerificationSubjectType.EDUCATION_INSTITUTION,
            subject_id=normalized_id,
        ).first()
    latest_case = None
    if subject:
        latest_case = subject.cases.order_by("-created_at").first()
    summary["case"] = serialize_case_status(latest_case) if latest_case else None
    return summary


def sanitize_education_evidence_metadata(metadata: dict | None) -> dict:
    metadata = metadata or {}
    safe = {
        "legal_registration": _safe_reference_list(metadata.get("legal_registration")),
        "domain_address_phone": _safe_reference_list(metadata.get("domain_address_phone")),
        "accreditation": _safe_reference_list(metadata.get("accreditation")),
        "certification": _safe_reference_list(metadata.get("certification")),
        "certificate_issuer_trust": _safe_reference_list(metadata.get("certificate_issuer_trust")),
        "staff_authorization": _safe_reference_list(metadata.get("staff_authorization")),
        "expiry": _safe_reference_list(metadata.get("expiry")),
        "private_references_only": True,
    }
    extra = metadata.get("extra")
    if isinstance(extra, dict):
        safe["extra"] = {
            str(key): value
            for key, value in extra.items()
            if key not in {"raw", "raw_document", "document_base64", "base64", "image_base64", "document_data"}
            and not (isinstance(value, str) and value.strip().lower().startswith("data:"))
        }
    return {key: value for key, value in safe.items() if value not in (None, [], {})}


@transaction.atomic
def start_education_institution_verification_case(*, institution, actor, evidence_metadata: dict | None = None, provider: str = "") -> VerificationCase | None:
    subject = education_institution_subject_for(institution)
    if not subject:
        return None
    adapter = get_provider_adapter(provider)
    now = timezone.now()
    case = VerificationCase.objects.create(
        subject=subject,
        requested_by=actor,
        level="accredited_education",
        status=VerificationCaseStatus.SUBMITTED,
        provider=adapter.name,
        provider_status="not_configured",
        evidence_metadata=sanitize_education_evidence_metadata(evidence_metadata),
        submitted_at=now,
        public_summary={
            "next_action": "manual_review",
            "message": "Education institution verification request received. Manual review is required for accreditation/certification.",
        },
    )
    _apply_provider_handoff(
        case,
        adapter,
        manual_message="Education institution verification request received. Manual review is required for accreditation/certification.",
    )
    record_audit_event(subject=subject, case=case, actor=actor, action="education_institution_case.started", provider=case.provider)
    return case


@transaction.atomic
def review_education_institution_case(*, case: VerificationCase, actor, action: str, notes: str = "", badge_codes: list[str] | None = None):
    if case.subject.subject_type != VerificationSubjectType.EDUCATION_INSTITUTION:
        raise ValueError("Verification case is not an education institution case.")
    now = timezone.now()
    case.reviewed_by = actor
    case.reviewed_at = now
    if notes:
        case.reviewer_notes = notes
    issued_badges = []
    if action == "approve":
        case.status = VerificationCaseStatus.APPROVED
        case.subject.current_status = VerificationCaseStatus.APPROVED
        case.subject.current_level = case.level
        case.subject.last_verified_at = now
        case.subject.save(update_fields=["current_status", "current_level", "last_verified_at", "updated_at"])
        issued_badges = issue_education_institution_badges(case=case, actor=actor, badge_codes=badge_codes)
    elif action == "reject":
        case.status = VerificationCaseStatus.REJECTED
        case.subject.current_status = VerificationCaseStatus.REJECTED
        case.subject.save(update_fields=["current_status", "updated_at"])
    elif action == "needs_more_info":
        case.status = VerificationCaseStatus.NEEDS_MORE_INFO
        case.subject.current_status = VerificationCaseStatus.NEEDS_MORE_INFO
        case.subject.save(update_fields=["current_status", "updated_at"])
    else:
        raise ValueError("Unsupported review action.")
    case.save(update_fields=["reviewed_by", "reviewed_at", "reviewer_notes", "status", "updated_at"])
    record_audit_event(
        subject=case.subject,
        case=case,
        actor=actor,
        action=f"education_institution_case.{action}",
        metadata={"badge_codes": [badge.code for badge in issued_badges]},
    )
    return case, issued_badges


def issue_education_institution_badges(*, case: VerificationCase, actor=None, badge_codes: list[str] | None = None) -> list[VerificationBadge]:
    allowed = {
        VerificationBadgeCode.VERIFIED_EDUCATION_INSTITUTION,
        VerificationBadgeCode.ACCREDITED_EDUCATION,
    }
    selected = [code for code in (badge_codes or []) if code in allowed] or [
        VerificationBadgeCode.VERIFIED_EDUCATION_INSTITUTION,
        VerificationBadgeCode.ACCREDITED_EDUCATION,
    ]
    issued = []
    now = timezone.now()
    for code in selected:
        badge, _created = VerificationBadge.objects.update_or_create(
            subject=case.subject,
            code=code,
            defaults={
                "case": case,
                "label": PUBLIC_BADGE_LABELS.get(code, code.replace("_", " ").title()),
                "level": case.level,
                "status": VerificationBadgeStatus.ACTIVE,
                "public": True,
                "issued_by": actor if getattr(actor, "is_authenticated", False) else None,
                "issued_at": now,
                "revoked_at": None,
                "revoke_reason": "",
            },
        )
        issued.append(badge)
    return issued


def sanitize_partner_evidence_metadata(metadata: dict | None) -> dict:
    metadata = metadata or {}
    safe = {
        "representative_authorization": _safe_reference_list(metadata.get("representative_authorization")),
        "company_registration": _safe_reference_list(metadata.get("company_registration")),
        "beneficial_owners": _safe_reference_list(metadata.get("beneficial_owners")),
        "tax_or_registry": _safe_reference_list(metadata.get("tax_or_registry")),
        "address": _safe_reference_list(metadata.get("address")),
        "private_references_only": True,
    }
    extra = metadata.get("extra")
    if isinstance(extra, dict):
        safe["extra"] = {
            str(key): value
            for key, value in extra.items()
            if key not in {"raw", "raw_document", "document_base64", "base64", "image_base64", "document_data"}
            and not (isinstance(value, str) and value.strip().lower().startswith("data:"))
        }
    return {key: value for key, value in safe.items() if value not in (None, [], {})}


def _safe_reference_list(value) -> list[dict]:
    if value is None:
        return []
    rows = value if isinstance(value, list) else [value]
    allowed_keys = {
        "type",
        "private_media_id",
        "media_id",
        "file_id",
        "storage_key",
        "filename",
        "mime_type",
        "size",
        "checksum",
        "country",
        "registry_number",
        "role",
        "name",
        "percentage",
        "issued_at",
        "expires_at",
    }
    result = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        safe_item = {
            str(key): item.get(key)
            for key in allowed_keys
            if item.get(key) not in (None, "")
        }
        if safe_item:
            result.append(safe_item)
    return result


@transaction.atomic
def start_partner_verification_case(*, partner, actor, evidence_metadata: dict | None = None, provider: str = "") -> VerificationCase | None:
    subject = partner_subject_for(partner)
    if not subject:
        return None
    adapter = get_provider_adapter(provider)
    now = timezone.now()
    case = VerificationCase.objects.create(
        subject=subject,
        requested_by=actor,
        level="partner_verified",
        status=VerificationCaseStatus.SUBMITTED,
        provider=adapter.name,
        provider_status="not_configured",
        evidence_metadata=sanitize_partner_evidence_metadata(evidence_metadata),
        submitted_at=now,
        public_summary={
            "next_action": "manual_review",
            "message": "Partner KYB request received. Manual review is available until live provider integration is enabled.",
        },
    )
    _apply_provider_handoff(
        case,
        adapter,
        manual_message="Partner KYB request received. Manual review is available until live provider integration is enabled.",
    )
    record_audit_event(subject=subject, case=case, actor=actor, action="partner_case.started", provider=case.provider)
    return case


@transaction.atomic
def review_partner_case(*, case: VerificationCase, actor, action: str, notes: str = "", badge_codes: list[str] | None = None):
    if case.subject.subject_type != VerificationSubjectType.PARTNER:
        raise ValueError("Verification case is not a partner case.")
    now = timezone.now()
    case.reviewed_by = actor
    case.reviewed_at = now
    if notes:
        case.reviewer_notes = notes
    issued_badges = []
    if action == "approve":
        case.status = VerificationCaseStatus.APPROVED
        case.subject.current_status = VerificationCaseStatus.APPROVED
        case.subject.current_level = case.level
        case.subject.last_verified_at = now
        case.subject.save(update_fields=["current_status", "current_level", "last_verified_at", "updated_at"])
        issued_badges = issue_partner_badges(case=case, actor=actor, badge_codes=badge_codes)
    elif action == "reject":
        case.status = VerificationCaseStatus.REJECTED
        case.subject.current_status = VerificationCaseStatus.REJECTED
        case.subject.save(update_fields=["current_status", "updated_at"])
    elif action == "needs_more_info":
        case.status = VerificationCaseStatus.NEEDS_MORE_INFO
        case.subject.current_status = VerificationCaseStatus.NEEDS_MORE_INFO
        case.subject.save(update_fields=["current_status", "updated_at"])
    else:
        raise ValueError("Unsupported review action.")
    case.save(update_fields=["reviewed_by", "reviewed_at", "reviewer_notes", "status", "updated_at"])
    record_audit_event(
        subject=case.subject,
        case=case,
        actor=actor,
        action=f"partner_case.{action}",
        metadata={"badge_codes": [badge.code for badge in issued_badges]},
    )
    return case, issued_badges


def issue_partner_badges(*, case: VerificationCase, actor=None, badge_codes: list[str] | None = None) -> list[VerificationBadge]:
    allowed = {
        VerificationBadgeCode.VERIFIED_PARTNER,
        VerificationBadgeCode.VERIFIED_ORGANIZATION,
        VerificationBadgeCode.OFFICIAL_PARTNER,
    }
    selected = [code for code in (badge_codes or []) if code in allowed] or [
        VerificationBadgeCode.VERIFIED_PARTNER,
        VerificationBadgeCode.VERIFIED_ORGANIZATION,
    ]
    issued = []
    now = timezone.now()
    for code in selected:
        badge, _created = VerificationBadge.objects.update_or_create(
            subject=case.subject,
            code=code,
            defaults={
                "case": case,
                "label": PUBLIC_BADGE_LABELS.get(code, code.replace("_", " ").title()),
                "level": case.level,
                "status": VerificationBadgeStatus.ACTIVE,
                "public": True,
                "issued_by": actor if getattr(actor, "is_authenticated", False) else None,
                "issued_at": now,
                "revoked_at": None,
                "revoke_reason": "",
            },
        )
        issued.append(badge)
    return issued


def find_shop_verification_case(verification_request) -> VerificationCase | None:
    subject = shop_subject_for(verification_request.shop)
    if not subject:
        return None
    return VerificationCase.objects.filter(
        subject=subject,
        provider="commerce",
        provider_case_id=shop_request_provider_case_id(verification_request),
    ).first()


def shop_request_provider_case_id(verification_request) -> str:
    return f"shop-verification-request:{verification_request.id}"


def sanitize_shop_evidence_metadata(documents) -> dict:
    safe_documents = []
    for item in documents or []:
        if not isinstance(item, dict):
            continue
        safe_item = {}
        for key in (
            "type",
            "private_media_id",
            "media_id",
            "file_id",
            "storage_key",
            "filename",
            "mime_type",
            "size",
            "checksum",
        ):
            value = item.get(key)
            if value not in (None, ""):
                safe_item[key] = value
        meta = item.get("meta")
        if isinstance(meta, dict):
            safe_meta = {
                str(key): value
                for key, value in meta.items()
                if key not in {"raw", "raw_document", "document_base64", "base64", "image_base64", "document_data"}
                and not (isinstance(value, str) and value.strip().lower().startswith("data:"))
            }
            if safe_meta:
                safe_item["meta"] = safe_meta
        if safe_item:
            safe_documents.append(safe_item)
    return {
        "document_count": len(documents or []),
        "documents": safe_documents,
        "private_references_only": True,
    }


@transaction.atomic
def sync_shop_verification_request(*, verification_request, actor=None, notes: str = "") -> VerificationCase | None:
    subject = shop_subject_for(verification_request.shop)
    if not subject:
        return None

    legacy_status = str(getattr(verification_request, "status", "") or "PENDING").upper()
    status_map = {
        "PENDING": VerificationCaseStatus.SUBMITTED,
        "IN_REVIEW": VerificationCaseStatus.IN_REVIEW,
        "APPROVED": VerificationCaseStatus.APPROVED,
        "REJECTED": VerificationCaseStatus.REJECTED,
        "ERROR": VerificationCaseStatus.REJECTED,
    }
    case_status = status_map.get(legacy_status, VerificationCaseStatus.SUBMITTED)
    now = timezone.now()
    evidence_metadata = sanitize_shop_evidence_metadata(getattr(verification_request, "documents", []) or [])
    evidence_metadata["legacy_request_id"] = str(verification_request.id)

    case, created = VerificationCase.objects.get_or_create(
        subject=subject,
        provider="commerce",
        provider_case_id=shop_request_provider_case_id(verification_request),
        defaults={
            "requested_by": verification_request.requested_by,
            "level": "shop_kyb_verified",
            "status": case_status,
            "provider_status": legacy_status.lower(),
            "risk_score": verification_request.risk_score,
            "evidence_metadata": evidence_metadata,
            "reviewer_notes": notes or verification_request.reviewer_notes,
            "submitted_at": verification_request.created_at or now,
            "reviewed_at": verification_request.processed_at if case_status in {VerificationCaseStatus.APPROVED, VerificationCaseStatus.REJECTED} else None,
            "public_summary": {
                "legacy_status": legacy_status,
                "source": "commerce_shop_verification",
            },
        },
    )
    if not created:
        case.status = case_status
        case.provider_status = legacy_status.lower()
        case.risk_score = verification_request.risk_score
        case.evidence_metadata = evidence_metadata
        case.reviewer_notes = notes or verification_request.reviewer_notes
        if case_status in {VerificationCaseStatus.APPROVED, VerificationCaseStatus.REJECTED}:
            case.reviewed_at = verification_request.processed_at or now
            if actor is not None and getattr(actor, "is_authenticated", False):
                case.reviewed_by = actor
        case.public_summary = {
            "legacy_status": legacy_status,
            "source": "commerce_shop_verification",
        }
        case.save(
            update_fields=[
                "status",
                "provider_status",
                "risk_score",
                "evidence_metadata",
                "reviewer_notes",
                "reviewed_at",
                "reviewed_by",
                "public_summary",
                "updated_at",
            ]
        )

    subject.current_status = case_status
    subject.current_level = case.level
    if case_status == VerificationCaseStatus.APPROVED:
        subject.last_verified_at = verification_request.processed_at or now
    subject.save(update_fields=["current_status", "current_level", "last_verified_at", "updated_at"])

    if case_status == VerificationCaseStatus.APPROVED:
        issue_shop_badges(case=case, actor=actor)
        sync_legacy_shop_from_central_case(case)

    record_audit_event(
        subject=subject,
        case=case,
        actor=actor or verification_request.requested_by,
        action="shop_case.synced",
        provider="commerce",
        metadata={"legacy_request_id": str(verification_request.id), "legacy_status": legacy_status},
    )
    return case


def issue_shop_badges(*, case: VerificationCase, actor=None) -> list[VerificationBadge]:
    issued = []
    now = timezone.now()
    for code in (VerificationBadgeCode.VERIFIED_SHOP, VerificationBadgeCode.TRUSTED_MERCHANT):
        badge, _created = VerificationBadge.objects.update_or_create(
            subject=case.subject,
            code=code,
            defaults={
                "case": case,
                "label": PUBLIC_BADGE_LABELS.get(code, code.replace("_", " ").title()),
                "level": case.level,
                "status": VerificationBadgeStatus.ACTIVE,
                "public": True,
                "issued_by": actor if getattr(actor, "is_authenticated", False) else None,
                "issued_at": now,
                "revoked_at": None,
                "revoke_reason": "",
            },
        )
        issued.append(badge)
    return issued


def sync_legacy_shop_from_central_case(case: VerificationCase):
    shop = getattr(case.subject, "metadata", {}).get("legacy_model")
    if shop != "commerce.Shop":
        return None
    try:
        from apps.commerce.models import Shop

        target = Shop.objects.get(id=case.subject.subject_id)
    except Exception:
        return None
    if case.status != VerificationCaseStatus.APPROVED:
        return target
    badges = set(target.trust_badges or [])
    badges.update({"kyc", "verified-shop", "trusted-merchant"})
    target.is_verified = True
    target.verification_status = "VERIFIED"
    target.trust_badges = sorted(badges)
    target.save(update_fields=["is_verified", "verification_status", "trust_badges", "updated_at"])
    return target


def serialize_case_status(case: VerificationCase | None) -> dict | None:
    if not case:
        return None
    return {
        "id": str(case.id),
        "level": case.level,
        "status": case.status,
        "provider": case.provider,
        "provider_status": case.provider_status,
        "submitted_at": case.submitted_at.isoformat() if case.submitted_at else None,
        "reviewed_at": case.reviewed_at.isoformat() if case.reviewed_at else None,
        "public_summary": case.public_summary or {},
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


@transaction.atomic
def start_user_verification_case(*, user, level: str, provider: str = "", evidence_metadata: dict | None = None) -> VerificationCase:
    subject = user_subject_for(user)
    adapter = get_provider_adapter(provider)
    provider_name = adapter.name
    now = timezone.now()
    case = VerificationCase.objects.create(
        subject=subject,
        requested_by=user,
        level=level,
        status=VerificationCaseStatus.SUBMITTED,
        provider=provider_name,
        evidence_metadata=evidence_metadata or {},
        submitted_at=now,
    )
    _apply_provider_handoff(
        case,
        adapter,
        manual_message="Verification request received. Manual review is available until live provider integration is enabled.",
    )
    record_audit_event(subject=subject, case=case, actor=user, action="user_case.started", provider=provider_name)
    return case


@transaction.atomic
def submit_case_evidence(*, case: VerificationCase, actor, evidence_metadata: dict) -> VerificationCase:
    case.evidence_metadata = evidence_metadata
    case.status = VerificationCaseStatus.SUBMITTED
    if not case.submitted_at:
        case.submitted_at = timezone.now()
    case.save(update_fields=["evidence_metadata", "status", "submitted_at", "updated_at"])
    case.subject.current_status = VerificationCaseStatus.SUBMITTED
    case.subject.save(update_fields=["current_status", "updated_at"])
    record_audit_event(subject=case.subject, case=case, actor=actor, action="user_case.evidence_submitted")
    return case


@transaction.atomic
def review_user_case(*, case: VerificationCase, actor, action: str, notes: str = "", badge_codes: list[str] | None = None):
    now = timezone.now()
    case.reviewed_by = actor
    case.reviewed_at = now
    if notes:
        case.reviewer_notes = notes

    issued_badges: list[VerificationBadge] = []
    if action == "approve":
        case.status = VerificationCaseStatus.APPROVED
        case.subject.current_status = VerificationCaseStatus.APPROVED
        case.subject.current_level = case.level
        case.subject.last_verified_at = now
        case.subject.save(update_fields=["current_status", "current_level", "last_verified_at", "updated_at"])
        allowed_codes = {VerificationBadgeCode.VERIFIED_USER, VerificationBadgeCode.ID_VERIFIED}
        selected_codes = [code for code in (badge_codes or []) if code in allowed_codes] or [
            VerificationBadgeCode.VERIFIED_USER,
            VerificationBadgeCode.ID_VERIFIED,
        ]
        for code in selected_codes:
            badge, _created = VerificationBadge.objects.update_or_create(
                subject=case.subject,
                code=code,
                defaults={
                    "case": case,
                    "label": PUBLIC_BADGE_LABELS.get(code, code.replace("_", " ").title()),
                    "level": case.level,
                    "status": VerificationBadgeStatus.ACTIVE,
                    "public": True,
                    "issued_by": actor,
                    "issued_at": now,
                    "revoked_at": None,
                    "revoke_reason": "",
                },
            )
            issued_badges.append(badge)
    elif action == "reject":
        case.status = VerificationCaseStatus.REJECTED
        case.subject.current_status = VerificationCaseStatus.REJECTED
        case.subject.save(update_fields=["current_status", "updated_at"])
    elif action == "needs_more_info":
        case.status = VerificationCaseStatus.NEEDS_MORE_INFO
        case.subject.current_status = VerificationCaseStatus.NEEDS_MORE_INFO
        case.subject.save(update_fields=["current_status", "updated_at"])
    else:
        raise ValueError("Unsupported review action.")

    case.save(update_fields=["reviewed_by", "reviewed_at", "reviewer_notes", "status", "updated_at"])
    record_audit_event(
        subject=case.subject,
        case=case,
        actor=actor,
        action=f"user_case.{action}",
        metadata={"badge_codes": [badge.code for badge in issued_badges]},
    )
    return case, issued_badges


def record_audit_event(*, subject=None, case=None, actor=None, action: str, provider: str = "", request=None, metadata=None):
    ip_address = ""
    user_agent = ""
    if request is not None:
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip_address = (xff.split(",", 1)[0].strip() or request.META.get("REMOTE_ADDR", ""))[:45]
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:2000]
    return VerificationAuditEvent.objects.create(
        subject=subject,
        case=case,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        provider=provider,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata=metadata or {},
    )


def _provider_webhook_status(payload: dict) -> str:
    candidates = [
        payload.get("status"),
        payload.get("review_status"),
        payload.get("reviewStatus"),
        payload.get("event"),
        payload.get("type"),
        payload.get("result"),
    ]
    text = " ".join(str(item or "").strip().lower() for item in candidates if item)
    if any(term in text for term in ("approved", "passed", "completed", "success", "accepted")):
        return "approved"
    if any(term in text for term in ("reject", "failed", "declined", "denied")):
        return "rejected"
    if any(term in text for term in ("more_info", "needs_more", "resubmit", "retry", "pending_document")):
        return "needs_more_info"
    if any(term in text for term in ("pending", "review", "processing")):
        return "provider_pending"
    return "received"


def _review_case_from_provider_status(case: VerificationCase, mapped: str):
    issued_badges = []
    if mapped == "approved":
        action = "approve"
    elif mapped == "rejected":
        action = "reject"
    elif mapped == "needs_more_info":
        action = "needs_more_info"
    else:
        action = ""

    if action:
        if case.subject.subject_type == VerificationSubjectType.USER:
            case, issued_badges = review_user_case(case=case, actor=None, action=action, notes="")
        elif case.subject.subject_type == VerificationSubjectType.PARTNER:
            case, issued_badges = review_partner_case(case=case, actor=None, action=action, notes="")
        elif case.subject.subject_type == VerificationSubjectType.HEALTH_INSTITUTION:
            case, issued_badges = review_health_institution_case(case=case, actor=None, action=action, notes="")
        elif case.subject.subject_type == VerificationSubjectType.EDUCATION_INSTITUTION:
            case, issued_badges = review_education_institution_case(case=case, actor=None, action=action, notes="")
        elif case.subject.subject_type == VerificationSubjectType.SHOP:
            if action == "approve":
                case.status = VerificationCaseStatus.APPROVED
                case.subject.current_status = VerificationCaseStatus.APPROVED
                case.subject.current_level = case.level
                case.subject.last_verified_at = timezone.now()
                case.save(update_fields=["status", "updated_at"])
                case.subject.save(update_fields=["current_status", "current_level", "last_verified_at", "updated_at"])
                issued_badges = issue_shop_badges(case=case, actor=None)
                sync_legacy_shop_from_central_case(case)
            elif action in {"reject", "needs_more_info"}:
                case.status = VerificationCaseStatus.REJECTED if action == "reject" else VerificationCaseStatus.NEEDS_MORE_INFO
                case.subject.current_status = case.status
                case.save(update_fields=["status", "updated_at"])
                case.subject.save(update_fields=["current_status", "updated_at"])
    elif mapped == "provider_pending":
        case.status = VerificationCaseStatus.PROVIDER_PENDING
        case.subject.current_status = VerificationCaseStatus.PROVIDER_PENDING
        case.save(update_fields=["status", "updated_at"])
        case.subject.save(update_fields=["current_status", "updated_at"])
    return case, issued_badges


def _provider_webhook_reference(payload: dict) -> str:
    for key in ("case_id", "caseId", "provider_case_id", "providerCaseId", "reference", "external_id", "externalId"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


@transaction.atomic
def apply_provider_webhook_event(*, provider: str, payload: dict, request=None) -> dict:
    safe_payload = redact_provider_payload(payload if isinstance(payload, dict) else {})
    reference = _provider_webhook_reference(payload if isinstance(payload, dict) else {})
    lookup_reference = _provider_case_lookup_id(reference)
    case = None
    if lookup_reference:
        normalized_case_id = normalize_subject_id(lookup_reference)
        if normalized_case_id:
            case = VerificationCase.objects.select_related("subject").filter(id=normalized_case_id).first()
        if case is None:
            case = VerificationCase.objects.select_related("subject").filter(
                provider=(provider or "")[:32],
                provider_case_id=reference,
            ).first()
    mapped = _provider_webhook_status(payload if isinstance(payload, dict) else {})
    if case is None:
        record_audit_event(
            action="webhook.unmatched",
            provider=(provider or "")[:32],
            request=request,
            metadata={"status": mapped, "payload": safe_payload, "reference_present": bool(reference)},
        )
        return {"accepted": True, "matched": False, "mapped_status": mapped}

    case.provider_payload = {
        **(case.provider_payload or {}),
        "last_webhook": safe_payload,
        "last_webhook_status": mapped,
        "live_call_made": False,
    }
    case.provider_status = mapped
    case.save(update_fields=["provider_payload", "provider_status", "updated_at"])

    case, issued_badges = _review_case_from_provider_status(case, mapped)

    record_audit_event(
        subject=case.subject,
        case=case,
        action="webhook.mapped",
        provider=(provider or "")[:32],
        request=request,
        metadata={"mapped_status": mapped, "badge_codes": [badge.code for badge in issued_badges], "payload": safe_payload},
    )
    return {
        "accepted": True,
        "matched": True,
        "case_id": str(case.id),
        "mapped_status": mapped,
        "case_status": case.status,
        "badge_codes": [badge.code for badge in issued_badges],
    }


def models_q_not_expired(now):
    from django.db.models import Q

    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)


STAFF_QUEUE_STATUSES = {
    VerificationCaseStatus.SUBMITTED,
    VerificationCaseStatus.IN_REVIEW,
    VerificationCaseStatus.PROVIDER_PENDING,
    VerificationCaseStatus.NEEDS_MORE_INFO,
}


def staff_verification_case_queryset():
    return VerificationCase.objects.select_related(
        "subject",
        "subject__owner",
        "requested_by",
        "reviewed_by",
    ).prefetch_related("badges")


def filter_staff_verification_cases(params):
    qs = staff_verification_case_queryset().order_by("-created_at")
    status_value = params.get("status")
    subject_type = params.get("subject_type")
    provider = params.get("provider")
    q = (params.get("q") or "").strip()
    if status_value:
        qs = qs.filter(status=status_value)
    else:
        qs = qs.filter(status__in=STAFF_QUEUE_STATUSES)
    if subject_type:
        qs = qs.filter(subject__subject_type=subject_type)
    if provider:
        qs = qs.filter(provider=provider)
    if q:
        q_filter = (
            Q(subject__display_name__icontains=q)
            | Q(provider_case_id__icontains=q)
            | Q(provider_applicant_id__icontains=q)
            | Q(requested_by__email__icontains=q)
            | Q(requested_by__phone__icontains=q)
        )
        normalized_q = normalize_subject_id(q)
        if normalized_q:
            q_filter |= Q(subject__subject_id=normalized_q)
        qs = qs.filter(q_filter)
    return qs


@transaction.atomic
def staff_set_case_status(*, case: VerificationCase, actor, status_value: str, notes: str = "", request=None) -> VerificationCase:
    allowed = {
        VerificationCaseStatus.IN_REVIEW,
        VerificationCaseStatus.NEEDS_MORE_INFO,
        VerificationCaseStatus.CANCELLED,
        VerificationCaseStatus.EXPIRED,
    }
    if status_value not in allowed:
        raise ValueError("Unsupported verification case status.")
    case.status = status_value
    if notes:
        case.reviewer_notes = notes
    if status_value in {VerificationCaseStatus.CANCELLED, VerificationCaseStatus.EXPIRED}:
        case.reviewed_by = actor
        case.reviewed_at = timezone.now()
    case.save(update_fields=["status", "reviewer_notes", "reviewed_by", "reviewed_at", "updated_at"])
    case.subject.current_status = status_value
    case.subject.save(update_fields=["current_status", "updated_at"])
    record_audit_event(
        subject=case.subject,
        case=case,
        actor=actor,
        action="staff.case_status_updated",
        request=request,
        metadata={"status": status_value, "notes_present": bool(notes)},
    )
    return case


@transaction.atomic
def staff_issue_badge(
    *,
    actor,
    subject_type: str,
    subject_id=None,
    case_id=None,
    code: str,
    label: str = "",
    level: str = "",
    public: bool = True,
    expires_at=None,
    reason: str = "",
    request=None,
) -> VerificationBadge:
    case = None
    subject = None
    if case_id:
        case = VerificationCase.objects.select_related("subject").get(id=case_id)
        subject = case.subject
    else:
        normalized_id = normalize_subject_id(subject_id)
        if not normalized_id:
            raise ValueError("Invalid verification subject id.")
        subject = VerificationSubject.objects.get(subject_type=subject_type, subject_id=normalized_id)
    now = timezone.now()
    badge, _created = VerificationBadge.objects.update_or_create(
        subject=subject,
        code=code,
        defaults={
            "case": case,
            "label": label or PUBLIC_BADGE_LABELS.get(code, code.replace("_", " ").title()),
            "level": level or (case.level if case else subject.current_level),
            "status": VerificationBadgeStatus.ACTIVE,
            "public": public,
            "issued_by": actor if getattr(actor, "is_authenticated", False) else None,
            "issued_at": now,
            "expires_at": expires_at,
            "revoked_at": None,
            "revoke_reason": "",
            "metadata": {"staff_reason": reason} if reason else {},
        },
    )
    subject.current_status = VerificationCaseStatus.APPROVED
    subject.current_level = badge.level or subject.current_level
    subject.last_verified_at = now
    subject.save(update_fields=["current_status", "current_level", "last_verified_at", "updated_at"])
    record_audit_event(
        subject=subject,
        case=case,
        actor=actor,
        action="staff.badge_issued",
        request=request,
        metadata={"badge_code": badge.code, "public": badge.public, "expires_at": badge.expires_at.isoformat() if badge.expires_at else None},
    )
    return badge


@transaction.atomic
def staff_revoke_badge(*, badge: VerificationBadge, actor, reason: str = "", request=None) -> VerificationBadge:
    now = timezone.now()
    badge.status = VerificationBadgeStatus.REVOKED
    badge.revoked_at = now
    badge.revoke_reason = reason or "Revoked by verification staff."
    badge.save(update_fields=["status", "revoked_at", "revoke_reason", "updated_at"])
    active_badges = VerificationBadge.objects.filter(
        subject=badge.subject,
        status=VerificationBadgeStatus.ACTIVE,
        public=True,
    ).filter(models_q_not_expired(now)).exists()
    if not active_badges:
        badge.subject.current_status = VerificationCaseStatus.REJECTED
        badge.subject.save(update_fields=["current_status", "updated_at"])
    record_audit_event(
        subject=badge.subject,
        case=badge.case,
        actor=actor,
        action="staff.badge_revoked",
        request=request,
        metadata={"badge_code": badge.code, "reason_present": bool(reason)},
    )
    return badge


def expiring_verification_items(*, days: int = 30):
    now = timezone.now()
    deadline = now + timezone.timedelta(days=max(1, min(int(days or 30), 365)))
    badges = VerificationBadge.objects.select_related("subject").filter(
        status=VerificationBadgeStatus.ACTIVE,
        expires_at__isnull=False,
        expires_at__gte=now,
        expires_at__lte=deadline,
    ).order_by("expires_at")
    cases = staff_verification_case_queryset().filter(
        expires_at__isnull=False,
        expires_at__gte=now,
        expires_at__lte=deadline,
    ).order_by("expires_at")
    return cases, badges


def _verification_reminder_owner_id(item) -> str:
    subject = getattr(item, "subject", None)
    owner_id = getattr(subject, "owner_id", None)
    requested_by_id = getattr(item, "requested_by_id", None)
    return str(owner_id or requested_by_id or "")


def _verification_days_until_expiry(expires_at) -> int | None:
    if not expires_at:
        return None
    delta = expires_at - timezone.now()
    if delta.total_seconds() < 0:
        return 0
    return max(0, delta.days + (1 if delta.seconds or delta.microseconds else 0))


def _select_reminder_window(days_until: int | None, windows: list[int]) -> int | None:
    if days_until is None:
        return None
    for window in sorted({max(1, int(day)) for day in windows}, reverse=True):
        if days_until <= window:
            return window
    return None


def schedule_verification_expiry_notifications(*, days_list: list[int] | None = None, dry_run: bool = True, limit: int = 500):
    """Create in-app/push verification expiry reminders through the central notification service.

    The command/service stores only ids, badge codes, subject type, and dates in notification context.
    Evidence metadata and provider secrets are deliberately excluded.
    """
    from django.conf import settings

    windows = days_list or list(getattr(settings, "VERIFICATION_EXPIRY_REMINDER_DAYS", [30, 14, 7, 1]) or [30, 14, 7, 1])
    windows = sorted({max(1, min(int(day), 365)) for day in windows}, reverse=True)
    max_days = max(windows) if windows else 30
    cases, badges = expiring_verification_items(days=max_days)
    candidates = []
    for kind, rows in (("case", cases), ("badge", badges)):
        for item in rows[: max(1, min(int(limit or 500), 1000))]:
            owner_id = _verification_reminder_owner_id(item)
            expires_at = getattr(item, "expires_at", None)
            days_until = _verification_days_until_expiry(expires_at)
            window = _select_reminder_window(days_until, windows)
            if not owner_id or not window:
                continue
            subject = getattr(item, "subject", None)
            candidates.append(
                {
                    "kind": kind,
                    "id": str(item.id),
                    "owner_id": owner_id,
                    "window_days": window,
                    "days_until": days_until,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                    "subject_type": getattr(subject, "subject_type", ""),
                    "subject_id": str(getattr(subject, "subject_id", "") or ""),
                    "display_name": getattr(subject, "display_name", "") or "Verification",
                    "badge_code": getattr(item, "code", ""),
                    "dedup_key": f"verification:expiry:{kind}:{item.id}:{window}",
                }
            )
    if dry_run:
        return {"dry_run": True, "matched": len(candidates), "created": 0, "windows": windows, "candidates": candidates[:50]}

    from apps.notifications.services import create_notification

    created = 0
    for row in candidates:
        label = row["badge_code"].replace("_", " ").title() if row["badge_code"] else "verification"
        create_notification(
            user_id=row["owner_id"],
            type="verification.expiry_reminder",
            title="Verification renewal reminder",
            body=f"{row['display_name']} {label} expires in {row['days_until']} day(s). Please review your verification evidence.",
            target_type=f"verification_{row['kind']}",
            target_id=row["id"],
            priority="HIGH" if int(row["days_until"] or 0) <= 7 else "MEDIUM",
            dedup_key=row["dedup_key"],
            context={
                "kind": row["kind"],
                "verification_id": row["id"],
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "badge_code": row["badge_code"],
                "days_until": row["days_until"],
                "expires_at": row["expires_at"],
            },
            channels=["IN_APP", "PUSH"],
        )
        created += 1
    return {"dry_run": False, "matched": len(candidates), "created": created, "windows": windows}


@transaction.atomic
def expire_overdue_verification_badges(*, actor=None, request=None, dry_run: bool = True):
    now = timezone.now()
    qs = VerificationBadge.objects.select_related("subject", "case").filter(
        status=VerificationBadgeStatus.ACTIVE,
        expires_at__isnull=False,
        expires_at__lte=now,
    )
    ids = list(qs.values_list("id", flat=True)[:500])
    if dry_run:
        return {"matched": len(ids), "expired": 0, "dry_run": True}
    expired = 0
    for badge in VerificationBadge.objects.select_related("subject", "case").filter(id__in=ids):
        badge.status = VerificationBadgeStatus.EXPIRED
        badge.save(update_fields=["status", "updated_at"])
        expired += 1
        record_audit_event(
            subject=badge.subject,
            case=badge.case,
            actor=actor,
            action="staff.badge_expired",
            request=request,
            metadata={"badge_code": badge.code, "expires_at": badge.expires_at.isoformat() if badge.expires_at else None},
        )
    return {"matched": len(ids), "expired": expired, "dry_run": False}


def provider_callback_inspection(*, provider: str = "", limit: int = 100):
    qs = VerificationAuditEvent.objects.select_related("subject", "case", "actor").filter(
        action__startswith="webhook.",
    ).order_by("-created_at")
    if provider:
        qs = qs.filter(provider=provider[:32])
    return qs[: max(1, min(int(limit or 100), 250))]


def suspicious_verification_signals():
    since = timezone.now() - timezone.timedelta(days=7)
    many_cases = (
        VerificationCase.objects.filter(created_at__gte=since)
        .values("subject_id", "subject__subject_type", "subject__display_name")
        .annotate(case_count=Count("id"))
        .filter(case_count__gte=3)
        .order_by("-case_count")[:50]
    )
    rejected_webhooks = (
        VerificationAuditEvent.objects.filter(created_at__gte=since, action="webhook.rejected")
        .values("provider", "ip_address")
        .annotate(rejected_count=Count("id"))
        .filter(rejected_count__gte=3)
        .order_by("-rejected_count")[:50]
    )
    rejected_cases = (
        VerificationCase.objects.filter(created_at__gte=since, status=VerificationCaseStatus.REJECTED)
        .values("subject__subject_type", "provider")
        .annotate(rejected_count=Count("id"))
        .order_by("-rejected_count")[:50]
    )
    return {
        "window_days": 7,
        "many_cases_per_subject": list(many_cases),
        "rejected_webhooks_by_provider_ip": list(rejected_webhooks),
        "rejected_cases_by_subject_type_provider": list(rejected_cases),
    }
