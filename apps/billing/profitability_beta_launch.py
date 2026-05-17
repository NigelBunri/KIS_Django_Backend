from __future__ import annotations

from typing import Any

from .profitability_production_go_no_go import get_profitability_production_go_no_go_summary
from .profitability_revenue_readiness import get_revenue_launch_readiness_summary


BETA_MODULES: tuple[dict[str, Any], ...] = (
    {
        "key": "consumer_plus",
        "label": "Consumer Plus",
        "audience": "Small internal beta cohort of adult users only.",
        "required_evidence_areas": (
            "legal_review",
            "pastoral_child_safety_review",
            "tax_accounting_review",
            "invoice_receipt_proof",
            "refund_support_proof",
            "privacy_analytics_policy",
            "rollback_proof",
        ),
        "support_playbook": "consumer_subscription_support",
        "rollback_playbook": "disable_consumer_plus_flags",
    },
    {
        "key": "creator_channels",
        "label": "Creator Channels",
        "audience": "Verified creators and channel owners approved by staff.",
        "required_evidence_areas": (
            "legal_review",
            "pastoral_child_safety_review",
            "flutterwave_sandbox_proof",
            "invoice_receipt_proof",
            "promotion_sponsored_label_policy",
            "refund_support_proof",
            "rollback_proof",
        ),
        "support_playbook": "creator_billing_and_promotion_support",
        "rollback_playbook": "pause_creator_monetization_surfaces",
    },
    {
        "key": "seller_pro",
        "label": "Seller Pro",
        "audience": "Verified shops with completed USD payment staging evidence.",
        "required_evidence_areas": (
            "legal_review",
            "tax_accounting_review",
            "flutterwave_sandbox_proof",
            "invoice_receipt_proof",
            "refund_support_proof",
            "promotion_sponsored_label_policy",
            "rollback_proof",
        ),
        "support_playbook": "seller_payment_and_order_support",
        "rollback_playbook": "disable_seller_pro_prompts_and_promotions",
    },
    {
        "key": "education_institution_pro",
        "label": "Education Institution Pro",
        "audience": "Verified education institutions with staff onboarding.",
        "required_evidence_areas": (
            "legal_review",
            "pastoral_child_safety_review",
            "tax_accounting_review",
            "flutterwave_sandbox_proof",
            "invoice_receipt_proof",
            "refund_support_proof",
            "privacy_analytics_policy",
            "rollback_proof",
        ),
        "support_playbook": "education_payment_and_certificate_support",
        "rollback_playbook": "pause_education_paid_feature_prompts",
    },
    {
        "key": "health_provider_growth",
        "label": "Health Provider Growth",
        "audience": "Verified health institutions after privacy and support review.",
        "required_evidence_areas": (
            "legal_review",
            "tax_accounting_review",
            "flutterwave_sandbox_proof",
            "invoice_receipt_proof",
            "refund_support_proof",
            "privacy_analytics_policy",
            "rollback_proof",
        ),
        "support_playbook": "health_payment_privacy_support",
        "rollback_playbook": "pause_health_growth_prompts",
    },
    {
        "key": "partner_workspace_pro",
        "label": "Partner Workspace Pro",
        "audience": "Verified ministries, partners, and organizations selected by staff.",
        "required_evidence_areas": (
            "legal_review",
            "pastoral_child_safety_review",
            "invoice_receipt_proof",
            "refund_support_proof",
            "enterprise_contract_policy",
            "rollback_proof",
        ),
        "support_playbook": "partner_workspace_support",
        "rollback_playbook": "pause_partner_workspace_monetization",
    },
    {
        "key": "verification_processing",
        "label": "Verification Processing",
        "audience": "Staff-selected verification cases with provider/manual review cost proof.",
        "required_evidence_areas": (
            "legal_review",
            "tax_accounting_review",
            "verification_fee_policy",
            "invoice_receipt_proof",
            "refund_support_proof",
            "privacy_analytics_policy",
            "rollback_proof",
        ),
        "support_playbook": "verification_fee_support",
        "rollback_playbook": "disable_verification_fee_prompts",
    },
    {
        "key": "promotion_packages",
        "label": "Promotion Packages",
        "audience": "Verified creators, sellers, institutions, and partners after campaign review.",
        "required_evidence_areas": (
            "legal_review",
            "pastoral_child_safety_review",
            "flutterwave_sandbox_proof",
            "promotion_sponsored_label_policy",
            "refund_support_proof",
            "rollback_proof",
        ),
        "support_playbook": "promotion_campaign_support",
        "rollback_playbook": "pause_promotion_checkout_and_campaign_delivery",
    },
    {
        "key": "enterprise_kcan",
        "label": "Enterprise / KCAN Network",
        "audience": "Invite-only organizations with approved annual contract evidence.",
        "required_evidence_areas": (
            "legal_review",
            "tax_accounting_review",
            "enterprise_contract_policy",
            "invoice_receipt_proof",
            "refund_support_proof",
            "privacy_analytics_policy",
            "rollback_proof",
        ),
        "support_playbook": "enterprise_success_support",
        "rollback_playbook": "pause_enterprise_packaging_and_contract_workflows",
    },
)


def _evidence_area_state(readiness: dict[str, Any], area: str) -> dict[str, Any]:
    value = readiness.get("areas", {}).get(area, {})
    return {
        "area": area,
        "label": value.get("label") or area.replace("_", " ").title(),
        "state": value.get("state") or "missing",
        "ready": bool(value.get("ready")),
        "latest_record_id": value.get("latest_record_id", ""),
        "required_reviewer_role": value.get("required_reviewer_role", ""),
    }


def _module_state(module: dict[str, Any], readiness: dict[str, Any], production: dict[str, Any]) -> dict[str, Any]:
    area_states = [_evidence_area_state(readiness, area) for area in module["required_evidence_areas"]]
    missing_or_blocked = [item for item in area_states if not item["ready"]]
    production_blocked = list(production.get("blocked_checks") or [])
    if missing_or_blocked:
        state = "beta_not_ready"
        reason = "required_evidence_incomplete"
    elif production_blocked:
        state = "blocked"
        reason = "production_go_no_go_blocked"
    else:
        state = "beta_ready"
        reason = "eligible_for_staff_limited_beta_review"

    return {
        **module,
        "state": state,
        "reason": reason,
        "ready": state == "beta_ready",
        "required_evidence": area_states,
        "missing_or_blocked_evidence": missing_or_blocked,
        "production_blockers": production_blocked,
        "live_charges_enabled": False,
        "entitlements_enforced": False,
        "payment_instruments_collected": False,
        "eligibility_summary": [
            "Invite-only beta participant approved by staff.",
            "All module evidence areas approved and non-expired.",
            "Production go/no-go checker has no blocked checks.",
            "Support owner and rollback owner are assigned before launch.",
            "No child/youth monetization or manipulative upgrade pressure.",
        ],
    }


def get_profitability_beta_launch_plan(*, user=None) -> dict[str, Any]:
    readiness = get_revenue_launch_readiness_summary(user=user)
    production = get_profitability_production_go_no_go_summary(user=user)
    modules = {
        module["key"]: _module_state(module, readiness, production)
        for module in BETA_MODULES
    }
    ready_count = sum(1 for module in modules.values() if module["state"] == "beta_ready")
    blocked_count = sum(1 for module in modules.values() if module["state"] == "blocked")
    not_ready_count = sum(1 for module in modules.values() if module["state"] == "beta_not_ready")
    total_count = len(modules)

    return {
        "enabled": False,
        "access": "staff_read_only",
        "mode": "limited_beta_plan_live_charges_gated",
        "go_no_go": "go_beta_ready" if ready_count == total_count else "no_go_beta_blocked",
        "readiness_percent": int((ready_count / total_count) * 100) if total_count else 0,
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "not_ready_count": not_ready_count,
        "total_count": total_count,
        "modules": modules,
        "eligibility_rules": [
            "Beta is invite-only and staff-approved; no public checkout or public lead capture.",
            "Only modules with approved, non-expired evidence may be considered for beta.",
            "Production payment/provider, entitlement enforcement, promotion checkout, and enterprise lead flags must remain off.",
            "Every beta module needs named support, rollback, privacy, pastoral/child-safety, and legal owners.",
            "KIS promotional credits remain non-cash, non-transferable, non-withdrawable, and not exchange-rated.",
        ],
        "support_playbooks": {
            "consumer_subscription_support": [
                "Triage upgrade questions without collecting payment details in app.",
                "Escalate refund/grace cases to revenue operations staff.",
                "Record redacted support evidence only.",
            ],
            "creator_billing_and_promotion_support": [
                "Review creator identity, channel safety, and campaign labels before beta.",
                "Keep campaign delivery disabled until paid evidence is approved.",
            ],
            "seller_payment_and_order_support": [
                "Confirm USD-only payment copy and seller support contacts.",
                "Do not allow wallet/KISC settlement or conversion.",
            ],
            "health_payment_privacy_support": [
                "Avoid diagnosis or payment pressure in support copy.",
                "Do not expose private health/payment data in evidence.",
            ],
            "enterprise_success_support": [
                "Use manual account management only until enterprise lead capture is approved.",
                "Store contract readiness as redacted evidence summaries.",
            ],
        },
        "rollback_playbooks": {
            "disable_flags": [
                "Keep all monetization flags false by default.",
                "Disable beta indicators and upgrade surfaces if any blocker appears.",
            ],
            "support_freeze": [
                "Freeze new beta invitations.",
                "Notify staff owners and preserve read-only history.",
            ],
            "payment_provider_freeze": [
                "Do not create live provider links.",
                "Use staging/sandbox proof only until explicit production approval.",
            ],
            "audit_review": [
                "Review revenue evidence audit events.",
                "Attach redacted incident summaries to evidence records.",
            ],
        },
        "guardrails": {
            "no_live_charges": True,
            "no_production_provider_calls": True,
            "no_entitlement_enforcement": True,
            "no_payment_instrument_collection": True,
            "no_promotion_checkout": True,
            "no_enterprise_lead_capture": True,
            "no_private_health_payment_verification_data": True,
            "staff_only": True,
            "read_only": True,
        },
        "production_go_no_go": {
            "status": production.get("go_no_go"),
            "readiness_percent": production.get("readiness_percent", 0),
            "blocked_checks": production.get("blocked_checks", []),
        },
        "evidence_readiness": {
            "status": readiness.get("go_no_go"),
            "readiness_percent": readiness.get("readiness_percent", 0),
            "ready_count": readiness.get("ready_count", 0),
            "total_count": readiness.get("total_count", 0),
        },
        "next_readiness_steps": [
            "Approve the minimum evidence areas for one low-risk beta module before inviting users.",
            "Assign support and rollback owners for the selected beta cohort.",
            "Run staging payment, webhook, receipt, refund, rollback, and private-media proof before beta sign-off.",
            "Keep live charges and entitlement enforcement gated until a separate explicit production approval phase.",
        ],
    }
