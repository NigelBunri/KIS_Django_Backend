from __future__ import annotations

from typing import Any

from django.conf import settings

from .profitability_analytics import get_profitability_command_center_summary
from .profitability_entitlements import get_profitability_entitlement_catalog


LAUNCH_REVIEW_AREAS: tuple[dict[str, Any], ...] = (
    {
        "key": "legal_review",
        "label": "Legal review",
        "owner": "legal_counsel",
        "evidence_required": [
            "pricing terms approved",
            "promotional credit wording approved",
            "refund/cancellation policy approved",
            "consumer protection review completed",
        ],
    },
    {
        "key": "pastoral_child_safety_review",
        "label": "Pastoral and child-safety review",
        "owner": "pastoral_safety_board",
        "evidence_required": [
            "Christian principles copy reviewed",
            "child/youth monetization safeguards approved",
            "no manipulative upgrade patterns confirmed",
        ],
    },
    {
        "key": "tax_accounting_review",
        "label": "Tax and accounting review",
        "owner": "finance",
        "evidence_required": [
            "tax treatment by country documented",
            "invoice/receipt requirements approved",
            "revenue recognition policy approved",
        ],
    },
    {
        "key": "flutterwave_direct_payment_proof",
        "label": "Flutterwave/direct-payment proof",
        "owner": "payments",
        "evidence_required": [
            "sandbox payment link proof",
            "signed webhook proof",
            "success/failed/cancelled/duplicate callback proof",
            "provider dashboard callback URL evidence",
        ],
    },
    {
        "key": "refund_support_workflows",
        "label": "Refund and support workflows",
        "owner": "support_operations",
        "evidence_required": [
            "refund request workflow",
            "billing support escalation path",
            "dispute response template",
            "customer-safe failure states",
        ],
    },
    {
        "key": "entitlement_migration_grace_policy",
        "label": "Entitlement migration and grace policy",
        "owner": "product_engineering",
        "evidence_required": [
            "free-to-paid migration rules",
            "trial/grace-period behavior",
            "no surprise hard-block policy",
            "rollback behavior for entitlements",
        ],
    },
    {
        "key": "promotion_sponsored_label_policy",
        "label": "Promotion sponsored-label policy",
        "owner": "trust_safety",
        "evidence_required": [
            "sponsored label copy approved",
            "campaign moderation rules",
            "child/youth ad safety rules",
            "unsafe category exclusions",
        ],
    },
    {
        "key": "verification_fee_policy",
        "label": "Verification fee policy",
        "owner": "verification_operations",
        "evidence_required": [
            "manual/provider review cost policy",
            "rejection/refund wording",
            "badge renewal rules",
            "appeal path",
        ],
    },
    {
        "key": "enterprise_contract_policy",
        "label": "Enterprise contract policy",
        "owner": "enterprise_operations",
        "evidence_required": [
            "annual contract template",
            "implementation/support scope",
            "data processing terms",
            "approval workflow",
        ],
    },
    {
        "key": "privacy_safe_analytics_policy",
        "label": "Privacy-safe analytics policy",
        "owner": "privacy_security",
        "evidence_required": [
            "aggregate event schema",
            "consent/settings behavior",
            "retention policy",
            "private data exclusion proof",
        ],
    },
    {
        "key": "rollback_steps",
        "label": "Rollback steps",
        "owner": "release_management",
        "evidence_required": [
            "feature-flag rollback steps",
            "payment incident rollback",
            "entitlement enforcement rollback",
            "support communication template",
        ],
    },
)


def _setting_bool(name: str) -> bool:
    return bool(getattr(settings, name, False))


def _production_flag_state() -> dict[str, Any]:
    flags = {
        "KIS_PROFITABILITY_BILLING_ENABLED": _setting_bool("KIS_PROFITABILITY_BILLING_ENABLED"),
        "KIS_PROFITABILITY_ENTITLEMENTS_ENFORCED": _setting_bool("KIS_PROFITABILITY_ENTITLEMENTS_ENFORCED"),
        "KIS_PROFITABILITY_TRIALS_ENABLED": _setting_bool("KIS_PROFITABILITY_TRIALS_ENABLED"),
        "KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED": _setting_bool("KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED"),
        "KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED": _setting_bool("KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED"),
        "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED": _setting_bool("KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED"),
    }
    risky_enabled = [name for name, enabled in flags.items() if enabled]
    return {
        "flags": flags,
        "ready": not risky_enabled,
        "status": "safe_preview_flags_off" if not risky_enabled else "review_required_flags_on",
        "risky_enabled_flags": risky_enabled,
        "note": "All launch-sensitive monetization flags should remain off until go/no-go approval.",
    }


def get_profitability_launch_gate_summary(*, user=None) -> dict[str, Any]:
    """Return read-only monetization launch readiness without enabling billing."""
    entitlement_catalog = get_profitability_entitlement_catalog(user=user)
    command_center = get_profitability_command_center_summary(user=user)
    production_flags = _production_flag_state()

    checklist = {
        item["key"]: {
            "label": item["label"],
            "owner": item["owner"],
            "status": "evidence_required",
            "ready": False,
            "evidence_required": item["evidence_required"],
            "evidence_attached": [],
        }
        for item in LAUNCH_REVIEW_AREAS
    }
    checklist["production_feature_flag_state"] = {
        "label": "Production feature flag state",
        "owner": "release_management",
        "status": production_flags["status"],
        "ready": production_flags["ready"],
        "evidence_required": [
            "production env flag screenshot/redacted export",
            "release owner sign-off",
        ],
        "evidence_attached": [],
        "flags": production_flags["flags"],
        "risky_enabled_flags": production_flags["risky_enabled_flags"],
    }

    ready_count = sum(1 for item in checklist.values() if item.get("ready"))
    total_count = len(checklist)
    risky_flags = production_flags["risky_enabled_flags"]

    return {
        "enabled": False,
        "go_no_go": "no_go_preview_only",
        "readiness_percent": int((ready_count / total_count) * 100) if total_count else 0,
        "ready_count": ready_count,
        "total_count": total_count,
        "blockers": [
            "Live charges are disabled.",
            "Subscriptions are disabled.",
            "Entitlement enforcement is disabled.",
            "Promotion checkout is disabled.",
            "Enterprise lead capture is disabled.",
            "Conversion tracking is disabled.",
            *[f"Review enabled launch flag before release: {name}" for name in risky_flags],
        ],
        "checklist": checklist,
        "production_feature_flags": production_flags,
        "billing_status": entitlement_catalog.get("billing_status", {}),
        "profitability_command_center": {
            "tracking_live": command_center.get("tracking_live", False),
            "privacy_mode": command_center.get("privacy_mode"),
            "direct_usd_payment_readiness": command_center.get("direct_usd_payment_readiness", {}),
        },
        "guardrails": {
            "no_live_charges": True,
            "no_subscriptions_enabled": True,
            "no_entitlement_enforcement": True,
            "no_promotion_checkout": True,
            "no_enterprise_lead_capture": True,
            "no_conversion_tracking": True,
            "no_payment_instrument_collection": True,
            "no_private_health_payment_verification_data": True,
            "promotional_credits_non_cash": True,
        },
        "next_readiness_steps": [
            "Attach legal, pastoral/child-safety, tax/accounting, and privacy approvals.",
            "Attach Flutterwave sandbox payment and signed webhook evidence.",
            "Approve refund, support, entitlement grace, promotion label, verification fee, and enterprise contract policies.",
            "Keep all live monetization flags off until a signed release go/no-go decision exists.",
        ],
    }
