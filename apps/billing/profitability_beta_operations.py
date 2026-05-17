from __future__ import annotations

from typing import Any

from .profitability_beta_launch import get_profitability_beta_launch_plan


COHORT_OWNER_MAP: dict[str, dict[str, str]] = {
    "consumer_plus": {
        "support_owner_role": "consumer_support_lead",
        "rollback_owner_role": "release_manager",
        "incident_owner_role": "revenue_ops_lead",
    },
    "creator_channels": {
        "support_owner_role": "creator_success_lead",
        "rollback_owner_role": "channels_release_manager",
        "incident_owner_role": "trust_safety_lead",
    },
    "seller_pro": {
        "support_owner_role": "seller_support_lead",
        "rollback_owner_role": "commerce_release_manager",
        "incident_owner_role": "payment_ops_lead",
    },
    "education_institution_pro": {
        "support_owner_role": "education_success_lead",
        "rollback_owner_role": "education_release_manager",
        "incident_owner_role": "education_ops_lead",
    },
    "health_provider_growth": {
        "support_owner_role": "health_support_lead",
        "rollback_owner_role": "health_release_manager",
        "incident_owner_role": "privacy_security_lead",
    },
    "partner_workspace_pro": {
        "support_owner_role": "partner_success_lead",
        "rollback_owner_role": "partner_release_manager",
        "incident_owner_role": "community_safety_lead",
    },
    "verification_processing": {
        "support_owner_role": "verification_support_lead",
        "rollback_owner_role": "verification_release_manager",
        "incident_owner_role": "verification_ops_lead",
    },
    "promotion_packages": {
        "support_owner_role": "campaign_support_lead",
        "rollback_owner_role": "promotion_release_manager",
        "incident_owner_role": "trust_safety_lead",
    },
    "enterprise_kcan": {
        "support_owner_role": "enterprise_success_lead",
        "rollback_owner_role": "enterprise_release_manager",
        "incident_owner_role": "executive_sponsor",
    },
}


def _cohort_state(module: dict[str, Any]) -> tuple[str, str]:
    if module.get("state") == "beta_ready":
        return "ready", "module_ready_for_invite_review"
    if module.get("state") == "blocked":
        return "blocked", "production_go_no_go_or_release_gate_blocked"
    return "paused", "module_evidence_or_owner_readiness_incomplete"


def _build_cohort(module: dict[str, Any]) -> dict[str, Any]:
    state, reason = _cohort_state(module)
    owners = COHORT_OWNER_MAP.get(module["key"], {})
    missing_evidence = [
        item.get("area")
        for item in module.get("missing_or_blocked_evidence", [])
        if item.get("area")
    ]
    return {
        "key": module["key"],
        "label": module["label"],
        "state": state,
        "reason": reason,
        "module_state": module.get("state"),
        "audience": module.get("audience", ""),
        "invite_policy": {
            "mode": "manual_staff_invite_only",
            "public_invites_enabled": False,
            "max_initial_cohort_size": 25 if state == "ready" else 0,
            "eligible_when": [
                "Selected module is beta_ready.",
                "Participant is manually approved by staff.",
                "Support and rollback owners are assigned.",
                "Participant is not a child/youth account for monetization beta.",
                "No payment instrument is collected in-app.",
            ],
            "blocked_when": [
                "Evidence is missing, blocked, expired, or revoked.",
                "Production go/no-go reports blocked checks.",
                "Support owner, rollback owner, or incident owner is missing.",
                "Any legal, pastoral/child-safety, tax, privacy, or payment concern is open.",
            ],
        },
        "owner_tracking": {
            "support_owner_role": owners.get("support_owner_role", "module_support_lead"),
            "rollback_owner_role": owners.get("rollback_owner_role", "release_manager"),
            "incident_owner_role": owners.get("incident_owner_role", "revenue_ops_lead"),
            "owner_assignment_status": "planned_not_live",
            "requires_named_people_before_beta": True,
        },
        "support_readiness": {
            "playbook_key": module.get("support_playbook", ""),
            "status": "ready_for_review" if state == "ready" else "not_ready",
            "checklist": [
                "Confirm staff support inbox/queue owner.",
                "Prepare refund, cancellation, failed payment, and user confusion scripts.",
                "Prepare redacted evidence capture guidance.",
                "Confirm no support flow requests card, bank, health, or verification document data in chat.",
            ],
        },
        "rollback_readiness": {
            "playbook_key": module.get("rollback_playbook", ""),
            "status": "ready_for_review" if state == "ready" else "not_ready",
            "checklist": [
                "Keep all monetization flags disabled until explicit approval.",
                "Prepare one-command or provider-console rollback instructions outside this endpoint.",
                "Prepare user-safe pause copy.",
                "Confirm revenue evidence audit review owner.",
            ],
        },
        "incident_escalation": {
            "severity_levels": ["sev3_support", "sev2_payment_or_privacy", "sev1_child_safety_or_legal"],
            "first_response_target_minutes": 60,
            "escalate_immediately_for": [
                "Child/youth monetization issue.",
                "Private health, payment, or verification data exposure.",
                "Provider/payment discrepancy.",
                "Promotion labeling or misleading pricing complaint.",
                "Any request to convert, transfer, withdraw, or exchange KIS promotional credits.",
            ],
        },
        "missing_evidence_areas": missing_evidence,
        "frontend_indicator": {
            "label": "Ready" if state == "ready" else "Blocked" if state == "blocked" else "Paused",
            "tone": "success" if state == "ready" else "danger" if state == "blocked" else "warning",
        },
    }


def get_profitability_beta_operations_summary(*, user=None) -> dict[str, Any]:
    beta_plan = get_profitability_beta_launch_plan(user=user)
    cohorts = {
        key: _build_cohort(module)
        for key, module in beta_plan.get("modules", {}).items()
    }
    ready_count = sum(1 for cohort in cohorts.values() if cohort["state"] == "ready")
    paused_count = sum(1 for cohort in cohorts.values() if cohort["state"] == "paused")
    blocked_count = sum(1 for cohort in cohorts.values() if cohort["state"] == "blocked")
    total_count = len(cohorts)

    return {
        "enabled": False,
        "access": "staff_read_only",
        "mode": "beta_cohort_operations_plan_live_charges_gated",
        "go_no_go": "go_cohort_invite_review" if ready_count and not blocked_count else "no_go_cohort_blocked",
        "readiness_percent": int((ready_count / total_count) * 100) if total_count else 0,
        "ready_count": ready_count,
        "paused_count": paused_count,
        "blocked_count": blocked_count,
        "total_count": total_count,
        "cohorts": cohorts,
        "global_invite_rules": [
            "Invite-only; no self-serve beta signup or public waitlist.",
            "Staff must approve every beta participant manually.",
            "Do not include child/youth accounts in monetization beta cohorts.",
            "Do not collect payment instruments or run live provider charges.",
            "Do not enforce entitlements or hard-block existing free behavior.",
            "Every invite must have support, rollback, and incident owners assigned.",
        ],
        "operations_checklist": [
            "Select one low-risk module before expanding to other cohorts.",
            "Confirm approved, non-expired evidence for that module.",
            "Assign named support, rollback, incident, legal, pastoral/child-safety, tax, privacy, and payment owners.",
            "Prepare support scripts and user-safe pause copy.",
            "Run staging payment/webhook/receipt/refund/rollback proof again before invites.",
            "Log all beta decisions through revenue evidence records with redacted summaries.",
        ],
        "support_templates": {
            "failed_payment_question": "Explain that beta billing is not live and no payment method should be entered until official launch approval.",
            "refund_or_cancellation": "Route to support owner; do not request card, bank, health, or verification document details.",
            "promotion_or_sponsored_label": "Pause campaign review and escalate to trust/safety if copy may be misleading.",
            "privacy_or_child_safety": "Freeze the cohort immediately and escalate as sev1_child_safety_or_legal.",
        },
        "final_beta_readiness": {
            "incident_drill": {
                "state": "drill_missing" if total_count else "not_started",
                "required_before_invites": True,
                "summary": "Run one tabletop incident drill for payment confusion, privacy exposure, child-safety concern, and rollback freeze.",
            },
            "support_runbook": {
                "state": "runbook_ready_for_review" if cohorts else "not_started",
                "required_before_invites": True,
                "summary": "Support guidance is documented here only; normal user screens should use short labels.",
            },
            "rollback_simulation": {
                "state": "rollback_missing" if total_count else "not_started",
                "required_before_invites": True,
                "summary": "Run a no-charge rollback simulation before inviting any real beta cohort.",
            },
            "normal_user_copy_policy": {
                "state": "compact_copy_required",
                "summary": "Normal app screens should show short labels only: Upgrade, Beta, Coming soon, Requires review, or Locked.",
            },
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
        "source_beta_plan": {
            "status": beta_plan.get("go_no_go"),
            "readiness_percent": beta_plan.get("readiness_percent", 0),
            "ready_count": beta_plan.get("ready_count", 0),
            "total_count": beta_plan.get("total_count", 0),
        },
        "next_readiness_steps": [
            "Use this operations plan to choose one beta cohort after evidence is complete.",
            "Attach named owner evidence before sending any invite.",
            "Keep all live monetization flags disabled until a separate explicit launch approval.",
            "Create a final beta incident-response drill before Phase 26.",
        ],
    }
