from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from .models import RevenueLaunchEvidenceRecord
from .profitability_revenue_ops import EVIDENCE_AREAS


REVIEWER_ROLE_BY_AREA: dict[str, str] = {
    "legal_review": "legal_reviewer",
    "pastoral_child_safety_review": "pastoral_safety_reviewer",
    "tax_accounting_review": "tax_accounting_reviewer",
    "flutterwave_sandbox_proof": "payment_reviewer",
    "invoice_receipt_proof": "tax_accounting_reviewer",
    "refund_support_proof": "support_reviewer",
    "entitlement_grace_policy": "product_reviewer",
    "promotion_sponsored_label_policy": "trust_safety_reviewer",
    "verification_fee_policy": "verification_reviewer",
    "enterprise_contract_policy": "enterprise_reviewer",
    "privacy_analytics_policy": "privacy_security_reviewer",
    "rollback_proof": "release_manager",
}


def _user_role_keys(user) -> set[str]:
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    roles: set[str] = set()
    try:
        roles.update(str(name).strip() for name in user.groups.values_list("name", flat=True))
    except Exception:
        pass
    metadata = getattr(user, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("revenue_reviewer_roles", "staff_roles", "roles"):
            value = metadata.get(key)
            if isinstance(value, str):
                roles.add(value.strip())
            elif isinstance(value, (list, tuple, set)):
                roles.update(str(item).strip() for item in value)
    return {role for role in roles if role}


def user_can_review_revenue_evidence(user, area: str) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    required_role = REVIEWER_ROLE_BY_AREA.get(area)
    if not required_role:
        return False
    return required_role in _user_role_keys(user)


def evidence_record_is_expired(record: RevenueLaunchEvidenceRecord, *, now=None) -> bool:
    if not record.expires_at:
        return False
    current = now or timezone.now()
    return record.expires_at <= current


def get_revenue_launch_readiness_summary(*, user=None) -> dict[str, Any]:
    now = timezone.now()
    records = list(
        RevenueLaunchEvidenceRecord.objects
        .filter(is_deleted=False)
        .select_related("reviewer", "created_by")
        .order_by("-created_at")
    )
    by_area: dict[str, list[RevenueLaunchEvidenceRecord]] = {}
    for record in records:
        by_area.setdefault(record.area, []).append(record)

    areas: dict[str, dict[str, Any]] = {}
    approved_ready_count = 0
    blocked_count = 0
    expired_count = 0

    for area in EVIDENCE_AREAS:
        key = area["key"]
        area_records = by_area.get(key, [])
        latest = area_records[0] if area_records else None
        approved = [
            record
            for record in area_records
            if record.status == "approved" and not evidence_record_is_expired(record, now=now)
        ]
        expired = [
            record
            for record in area_records
            if evidence_record_is_expired(record, now=now) or record.status == "expired"
        ]
        blocked = any(record.status in {"rejected", "revoked", "needs_changes"} for record in area_records)
        state = "approved" if approved else "expired" if expired else "blocked" if blocked else "missing"
        if state == "approved":
            approved_ready_count += 1
        if state == "expired":
            expired_count += 1
        if state == "blocked":
            blocked_count += 1

        areas[key] = {
            "label": area["label"],
            "owner": area["owner"],
            "required_reviewer_role": REVIEWER_ROLE_BY_AREA.get(key, ""),
            "state": state,
            "ready": state == "approved",
            "can_current_user_review": user_can_review_revenue_evidence(user, key),
            "record_count": len(area_records),
            "approved_count": len(approved),
            "expired_count": len(expired),
            "latest_record_id": str(latest.id) if latest else "",
            "latest_status": latest.status if latest else "",
            "latest_expires_at": latest.expires_at.isoformat() if latest and latest.expires_at else "",
            "reminder": {
                "required": bool(latest and latest.expires_at and latest.expires_at <= now + timedelta(days=14)),
                "status": "planned_not_dispatched",
                "windows_days": [30, 14, 7, 1],
            },
        }

    total = len(EVIDENCE_AREAS)
    readiness_percent = int((approved_ready_count / total) * 100) if total else 0
    return {
        "enabled": False,
        "access": "staff_read_only",
        "go_no_go": "go" if readiness_percent == 100 and blocked_count == 0 and expired_count == 0 else "no_go_evidence_incomplete",
        "readiness_percent": readiness_percent,
        "ready_count": approved_ready_count,
        "total_count": total,
        "blocked_count": blocked_count,
        "expired_count": expired_count,
        "missing_count": max(total - approved_ready_count - blocked_count - expired_count, 0),
        "areas": areas,
        "reviewer_roles": REVIEWER_ROLE_BY_AREA,
        "guardrails": {
            "staff_only": True,
            "read_only_summary": True,
            "role_checked_reviews": True,
            "expiry_aware": True,
            "no_live_charges": True,
            "no_payment_instrument_collection": True,
            "no_private_health_payment_verification_data": True,
        },
        "next_readiness_steps": [
            "Assign reviewer roles through staff groups or user metadata before approvals.",
            "Approve every required area with non-expired evidence before launch.",
            "Add reminder dispatch only after staff notification routing is approved.",
            "Keep monetization flags disabled until readiness is 100% and release sign-off is complete.",
        ],
    }
