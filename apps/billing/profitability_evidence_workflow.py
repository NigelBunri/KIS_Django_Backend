from __future__ import annotations

from typing import Any

from .profitability_revenue_ops import EVIDENCE_AREAS, get_revenue_ops_evidence_console_summary


APPROVAL_STATES: tuple[str, ...] = (
    "draft",
    "submitted",
    "needs_changes",
    "approved",
    "rejected",
    "expired",
    "revoked",
)


REVIEWER_ROLES: tuple[dict[str, str], ...] = (
    {"key": "legal_reviewer", "label": "Legal reviewer"},
    {"key": "pastoral_safety_reviewer", "label": "Pastoral and child-safety reviewer"},
    {"key": "tax_accounting_reviewer", "label": "Tax and accounting reviewer"},
    {"key": "payment_reviewer", "label": "Payment reviewer"},
    {"key": "privacy_security_reviewer", "label": "Privacy and security reviewer"},
    {"key": "release_manager", "label": "Release manager"},
)


AUDIT_EVENT_TYPES: tuple[str, ...] = (
    "evidence_record_created",
    "evidence_record_submitted",
    "evidence_record_reviewed",
    "evidence_record_approved",
    "evidence_record_rejected",
    "evidence_record_revoked",
    "evidence_record_expired",
    "private_media_reference_added",
    "private_media_reference_removed",
    "review_reminder_scheduled",
)


EVIDENCE_RECORD_MODEL_PLAN: dict[str, Any] = {
    "model_name": "RevenueLaunchEvidenceRecord",
    "migration_status": "planned_not_created",
    "recommended_fields": [
        {"name": "id", "type": "UUIDField", "privacy": "public_identifier"},
        {"name": "area", "type": "CharField", "privacy": "classification_only"},
        {"name": "title", "type": "CharField", "privacy": "safe_summary"},
        {"name": "status", "type": "CharField", "privacy": "approval_state"},
        {"name": "owner_role", "type": "CharField", "privacy": "role_only"},
        {"name": "reviewer", "type": "ForeignKey(User)", "privacy": "staff_reference"},
        {"name": "private_media_asset_id", "type": "UUIDField", "privacy": "private_reference_only"},
        {"name": "redacted_summary", "type": "TextField", "privacy": "safe_staff_summary"},
        {"name": "expires_at", "type": "DateTimeField", "privacy": "operational_metadata"},
        {"name": "created_by", "type": "ForeignKey(User)", "privacy": "staff_reference"},
        {"name": "created_at", "type": "DateTimeField", "privacy": "operational_metadata"},
        {"name": "updated_at", "type": "DateTimeField", "privacy": "operational_metadata"},
    ],
    "excluded_fields": [
        "raw_provider_payload",
        "payment_card_data",
        "bank_account_data",
        "private_health_record",
        "verification_document_bytes",
        "raw_storage_path",
        "secret_key",
    ],
}


AUDIT_MODEL_PLAN: dict[str, Any] = {
    "model_name": "RevenueLaunchEvidenceAuditEvent",
    "migration_status": "planned_not_created",
    "immutable": True,
    "recommended_fields": [
        {"name": "id", "type": "UUIDField"},
        {"name": "evidence_record", "type": "ForeignKey(RevenueLaunchEvidenceRecord)"},
        {"name": "event_type", "type": "CharField"},
        {"name": "actor", "type": "ForeignKey(User)"},
        {"name": "redacted_detail", "type": "JSONField"},
        {"name": "created_at", "type": "DateTimeField"},
    ],
    "redaction_required": True,
}


def _redacted_serializer_contract() -> dict[str, Any]:
    safe_fields = [
        "id",
        "area",
        "title",
        "status",
        "owner_role",
        "reviewer_display",
        "private_media_asset_id",
        "redacted_summary",
        "expires_at",
        "created_at",
        "updated_at",
    ]
    blocked_fields = EVIDENCE_RECORD_MODEL_PLAN["excluded_fields"]
    return {
        "serializer_name": "RevenueLaunchEvidenceRecordStaffSerializer",
        "status": "planned_not_created",
        "safe_fields": safe_fields,
        "blocked_fields": blocked_fields,
        "private_media_policy": "reference_only_signed_access_required",
        "raw_document_storage": False,
        "raw_provider_payload_storage": False,
    }


def get_revenue_evidence_workflow_plan(*, user=None) -> dict[str, Any]:
    console = get_revenue_ops_evidence_console_summary(user=user)
    areas = {
        item["key"]: {
            "label": item["label"],
            "owner": item["owner"],
            "allowed_states": APPROVAL_STATES,
            "default_state": "draft",
            "requires_private_media_reference": item["key"] in {
                "flutterwave_sandbox_proof",
                "invoice_receipt_proof",
                "rollback_proof",
            },
            "requires_expiry_review": item["key"] in {
                "legal_review",
                "tax_accounting_review",
                "privacy_analytics_policy",
                "enterprise_contract_policy",
            },
        }
        for item in EVIDENCE_AREAS
    }
    return {
        "enabled": False,
        "access": "staff_read_only",
        "workflow_mode": "planned_no_migrations_created",
        "evidence_console": {
            "go_no_go": console.get("go_no_go"),
            "readiness_percent": console.get("readiness_percent", 0),
        },
        "approval_states": APPROVAL_STATES,
        "reviewer_roles": REVIEWER_ROLES,
        "evidence_areas": areas,
        "model_plan": EVIDENCE_RECORD_MODEL_PLAN,
        "audit_model_plan": AUDIT_MODEL_PLAN,
        "audit_event_types": AUDIT_EVENT_TYPES,
        "redacted_serializer_contract": _redacted_serializer_contract(),
        "reminder_policy": {
            "status": "planned_not_scheduled",
            "review_windows_days": [30, 14, 7, 1],
            "expiry_requires_reapproval": True,
            "channels": ["staff_notification", "admin_dashboard"],
        },
        "guardrails": {
            "no_database_migration_created": True,
            "staff_only": True,
            "read_only": True,
            "private_media_references_only": True,
            "no_raw_documents": True,
            "no_raw_provider_payloads": True,
            "no_payment_instrument_collection": True,
            "no_live_charges": True,
            "no_entitlement_enforcement": True,
        },
        "next_readiness_steps": [
            "Create migrations only after staff access, audit, and private media rules are reviewed.",
            "Use private MediaAsset references and signed access only; never store raw documents in billing records.",
            "Add immutable audit events before allowing evidence creation or approval actions.",
            "Add expiry reminders only after notification privacy and staff routing are approved.",
        ],
    }
