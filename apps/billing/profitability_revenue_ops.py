from __future__ import annotations

from typing import Any

from .profitability_launch_gate import get_profitability_launch_gate_summary
from .profitability_subscription_lifecycle import get_profitability_subscription_lifecycle_summary


EVIDENCE_AREAS: tuple[dict[str, Any], ...] = (
    {
        "key": "legal_review",
        "label": "Legal review",
        "owner": "legal_counsel",
        "evidence_types": ["approval memo", "terms review", "refund/cancellation wording"],
    },
    {
        "key": "pastoral_child_safety_review",
        "label": "Pastoral and child-safety review",
        "owner": "pastoral_safety_board",
        "evidence_types": ["Christian principles review", "child/youth monetization safeguards", "anti-dark-pattern approval"],
    },
    {
        "key": "tax_accounting_review",
        "label": "Tax and accounting review",
        "owner": "finance",
        "evidence_types": ["tax treatment memo", "receipt/invoice requirements", "revenue recognition review"],
    },
    {
        "key": "flutterwave_sandbox_proof",
        "label": "Flutterwave sandbox proof",
        "owner": "payments",
        "evidence_types": ["sandbox payment link", "signed webhook replay", "provider dashboard callback URL"],
    },
    {
        "key": "invoice_receipt_proof",
        "label": "Invoice and receipt proof",
        "owner": "finance_engineering",
        "evidence_types": ["USD invoice sample", "receipt sample", "refund receipt sample"],
    },
    {
        "key": "refund_support_proof",
        "label": "Refund and support proof",
        "owner": "support_operations",
        "evidence_types": ["refund playbook", "failed-payment support flow", "billing escalation owner"],
    },
    {
        "key": "entitlement_grace_policy",
        "label": "Entitlement grace policy",
        "owner": "product_engineering",
        "evidence_types": ["grace-period rules", "downgrade policy", "rollback behavior"],
    },
    {
        "key": "promotion_sponsored_label_policy",
        "label": "Promotion sponsored-label policy",
        "owner": "trust_safety",
        "evidence_types": ["sponsored label proof", "campaign moderation rules", "child/youth ad safety"],
    },
    {
        "key": "verification_fee_policy",
        "label": "Verification fee policy",
        "owner": "verification_operations",
        "evidence_types": ["processing fee policy", "rejection/refund language", "appeal path"],
    },
    {
        "key": "enterprise_contract_policy",
        "label": "Enterprise contract policy",
        "owner": "enterprise_operations",
        "evidence_types": ["annual contract template", "support/SLA scope", "data processing terms"],
    },
    {
        "key": "privacy_analytics_policy",
        "label": "Privacy analytics policy",
        "owner": "privacy_security",
        "evidence_types": ["aggregate event schema", "consent/settings proof", "retention policy"],
    },
    {
        "key": "rollback_proof",
        "label": "Rollback proof",
        "owner": "release_management",
        "evidence_types": ["feature-flag rollback drill", "payment incident rollback", "support communication template"],
    },
)


def get_revenue_ops_evidence_console_summary(*, user=None) -> dict[str, Any]:
    """Return staff-only read-only revenue-operations evidence readiness."""
    launch_gate = get_profitability_launch_gate_summary(user=user)
    lifecycle = get_profitability_subscription_lifecycle_summary(user=user)

    evidence = {
        item["key"]: {
            **item,
            "status": "evidence_required",
            "ready": False,
            "evidence_count": 0,
            "evidence_attached": [],
            "private_data_exposed": False,
        }
        for item in EVIDENCE_AREAS
    }
    ready_count = sum(1 for item in evidence.values() if item["ready"])

    return {
        "enabled": False,
        "access": "staff_read_only",
        "go_no_go": "no_go_evidence_required",
        "ready_count": ready_count,
        "total_count": len(evidence),
        "readiness_percent": int((ready_count / len(evidence)) * 100) if evidence else 0,
        "evidence_areas": evidence,
        "launch_gate": {
            "go_no_go": launch_gate.get("go_no_go"),
            "readiness_percent": launch_gate.get("readiness_percent", 0),
            "risky_enabled_flags": launch_gate.get("production_feature_flags", {}).get("risky_enabled_flags", []),
        },
        "subscription_lifecycle": {
            "mode": lifecycle.get("mode"),
            "provider": lifecycle.get("provider"),
            "provider_links_enabled": lifecycle.get("provider_links_enabled", False),
            "production_provider_connected": lifecycle.get("production_provider_connected", False),
        },
        "guardrails": {
            "staff_only": True,
            "read_only": True,
            "no_live_charges": True,
            "no_payment_instrument_collection": True,
            "no_entitlement_enforcement": True,
            "no_private_health_payment_verification_data": True,
            "no_raw_provider_payloads": True,
            "promotional_credits_non_cash": True,
        },
        "next_readiness_steps": [
            "Define evidence storage model with private attachments and audit history.",
            "Require signed approval for legal, pastoral/child-safety, tax/accounting, privacy, and rollback areas.",
            "Attach Flutterwave sandbox payment-link and webhook replay proof before monetization launch.",
            "Keep the console read-only until access control, audit logging, and private media evidence storage are reviewed.",
        ],
    }
