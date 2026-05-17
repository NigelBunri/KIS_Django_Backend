from __future__ import annotations

from typing import Any

from django.conf import settings

from .profitability_entitlements import get_profitability_entitlement_catalog
from .profitability_launch_gate import get_profitability_launch_gate_summary


SUBSCRIPTION_LIFECYCLE_STATES: tuple[dict[str, Any], ...] = (
    {
        "key": "trial_ready",
        "label": "Trial readiness",
        "status": "planned_not_enabled",
        "required_controls": [
            "trial start/end timestamps",
            "trial reminder notifications",
            "no surprise charge policy",
            "trial cancellation support copy",
        ],
    },
    {
        "key": "active_subscription",
        "label": "Active subscription",
        "status": "planned_not_enabled",
        "required_controls": [
            "provider subscription reference",
            "plan entitlement snapshot",
            "invoice/receipt link",
            "renewal date visibility",
        ],
    },
    {
        "key": "grace_period",
        "label": "Grace period",
        "status": "planned_not_enabled",
        "required_controls": [
            "failed-payment notice",
            "grace end timestamp",
            "non-destructive entitlement downgrade",
            "support escalation path",
        ],
    },
    {
        "key": "cancelled",
        "label": "Cancellation",
        "status": "planned_not_enabled",
        "required_controls": [
            "cancel at period end",
            "immediate cancellation rules",
            "confirmation receipt",
            "reactivation path",
        ],
    },
    {
        "key": "refunded",
        "label": "Refunded",
        "status": "planned_not_enabled",
        "required_controls": [
            "refund reason",
            "provider refund reference",
            "support note",
            "entitlement rollback policy",
        ],
    },
)


PROVIDER_SANDBOX_CHECKS: tuple[dict[str, Any], ...] = (
    {
        "key": "flutterwave_sandbox_keys",
        "label": "Flutterwave sandbox credentials",
        "status": "not_connected_for_billing",
        "evidence_required": ["redacted sandbox key proof", "sandbox account owner sign-off"],
    },
    {
        "key": "payment_link_generation",
        "label": "Payment link generation",
        "status": "planned_not_enabled",
        "evidence_required": ["sandbox payment link for subscription", "sandbox payment link for one-time fee"],
    },
    {
        "key": "webhook_signature_verification",
        "label": "Webhook signature verification",
        "status": "planned_not_enabled",
        "evidence_required": ["signed success callback", "signed failed callback", "duplicate callback proof"],
    },
    {
        "key": "reconciliation",
        "label": "Payment reconciliation",
        "status": "planned_not_enabled",
        "evidence_required": ["provider status poll proof", "idempotency proof", "unmatched callback handling"],
    },
)


ONE_TIME_BILLING_READINESS: tuple[dict[str, Any], ...] = (
    {
        "key": "promotion_campaign_billing",
        "label": "Promotion campaign billing",
        "status": "preview_only",
        "required_controls": ["campaign moderation approval", "sponsored label proof", "refund policy"],
    },
    {
        "key": "verification_processing_fee",
        "label": "Verification processing fee",
        "status": "preview_only",
        "required_controls": ["manual/provider review fee policy", "rejection handling", "appeal path"],
    },
    {
        "key": "enterprise_annual_contracts",
        "label": "Enterprise annual contracts",
        "status": "manual_contract_planning",
        "required_controls": ["contract template", "invoice workflow", "implementation/support scope"],
    },
)


def _setting_bool(name: str) -> bool:
    return bool(getattr(settings, name, False))


def get_profitability_subscription_lifecycle_summary(*, user=None) -> dict[str, Any]:
    """Return safe billing lifecycle readiness without enabling billing or collecting instruments."""
    catalog = get_profitability_entitlement_catalog(user=user)
    launch_gate = get_profitability_launch_gate_summary(user=user)
    provider_links_enabled = _setting_bool("KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED")
    billing_enabled = _setting_bool("KIS_PROFITABILITY_BILLING_ENABLED")
    entitlements_enforced = _setting_bool("KIS_PROFITABILITY_ENTITLEMENTS_ENFORCED")
    trials_enabled = _setting_bool("KIS_PROFITABILITY_TRIALS_ENABLED")

    return {
        "enabled": False,
        "mode": "sandbox_readiness_preview_only",
        "provider": getattr(settings, "KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER", "flutterwave"),
        "provider_links_enabled": provider_links_enabled,
        "billing_enabled": billing_enabled,
        "entitlements_enforced": entitlements_enforced,
        "trials_enabled": trials_enabled,
        "payment_instruments_collected": False,
        "production_provider_connected": False,
        "subscription_lifecycle_states": {
            item["key"]: {
                **item,
                "enabled": False,
                "live_provider_action": False,
            }
            for item in SUBSCRIPTION_LIFECYCLE_STATES
        },
        "provider_sandbox_checks": {
            item["key"]: {
                **item,
                "ready": False,
                "evidence_attached": [],
            }
            for item in PROVIDER_SANDBOX_CHECKS
        },
        "one_time_billing_readiness": {
            item["key"]: {
                **item,
                "enabled": False,
                "live_provider_action": False,
            }
            for item in ONE_TIME_BILLING_READINESS
        },
        "invoice_receipt_readiness": {
            "status": "planned_not_enabled",
            "required_controls": [
                "USD-only invoice template",
                "receipt numbering policy",
                "tax display policy",
                "refund receipt policy",
            ],
            "private_payment_data_exposed": False,
        },
        "support_escalation": {
            "status": "planned_not_enabled",
            "queues": [
                "billing_failed_payment",
                "refund_request",
                "subscription_cancellation",
                "verification_fee_review",
                "promotion_campaign_billing",
                "enterprise_contract_support",
            ],
            "private_payloads_exposed": False,
        },
        "launch_gate": {
            "go_no_go": launch_gate.get("go_no_go"),
            "readiness_percent": launch_gate.get("readiness_percent", 0),
            "production_feature_flags": launch_gate.get("production_feature_flags", {}),
        },
        "catalog_snapshot": {
            "plans": catalog.get("plans", []),
            "usage_meters": catalog.get("usage_meters", {}),
            "billing_status": catalog.get("billing_status", {}),
        },
        "guardrails": {
            "no_live_charges": True,
            "no_production_provider_connection": True,
            "no_payment_instrument_collection": True,
            "no_entitlement_enforcement": True,
            "no_kis_credit_cash_value": True,
            "usd_direct_provider_first": True,
        },
        "next_readiness_steps": [
            "Capture Flutterwave sandbox payment-link and signed webhook evidence.",
            "Define subscription state machine and non-destructive entitlement downgrade rules.",
            "Approve invoice, refund, cancellation, grace-period, trial, and support policies.",
            "Keep production provider calls and entitlement enforcement disabled until launch sign-off.",
        ],
    }
