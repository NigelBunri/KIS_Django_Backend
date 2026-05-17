from __future__ import annotations

from typing import Any

from .profitability_revenue_readiness import get_revenue_launch_readiness_summary


STAGING_PROOF_WORKFLOWS: tuple[dict[str, Any], ...] = (
    {
        "key": "flutterwave_sandbox_payment_link",
        "area": "flutterwave_sandbox_proof",
        "title": "Flutterwave sandbox payment link proof",
        "owner_role": "payments",
        "required_reviewer_role": "payment_reviewer",
        "private_media_required": True,
        "redacted_summary_template": (
            "Captured Flutterwave sandbox payment-link evidence. Include only redacted provider reference, "
            "test amount/currency, callback URL confirmation, and private MediaAsset id for screenshots/logs."
        ),
        "checklist": [
            "Sandbox credentials confirmed without exposing values.",
            "Payment link generated in staging only.",
            "Provider dashboard callback URL captured.",
            "No production provider call made.",
        ],
    },
    {
        "key": "signed_webhook_replay",
        "area": "flutterwave_sandbox_proof",
        "title": "Signed webhook replay proof",
        "owner_role": "payments",
        "required_reviewer_role": "payment_reviewer",
        "private_media_required": True,
        "redacted_summary_template": (
            "Captured signed webhook replay evidence for success, failed, cancelled, duplicate, and unmatched "
            "sandbox callbacks. Store only redacted event ids and private MediaAsset references."
        ),
        "checklist": [
            "Success callback verified.",
            "Failed/cancelled callbacks verified.",
            "Duplicate callback idempotency verified.",
            "Unmatched callback quarantine verified.",
        ],
    },
    {
        "key": "invoice_receipt_sample",
        "area": "invoice_receipt_proof",
        "title": "USD invoice and receipt proof",
        "owner_role": "finance_engineering",
        "required_reviewer_role": "tax_accounting_reviewer",
        "private_media_required": True,
        "redacted_summary_template": (
            "Captured USD-only invoice/receipt samples with no payment instrument data. Include private "
            "MediaAsset id for redacted screenshots or PDFs."
        ),
        "checklist": [
            "USD-only display confirmed.",
            "Receipt number policy represented.",
            "Tax display reviewed.",
            "No KIS credit exchange wording present.",
        ],
    },
    {
        "key": "refund_support_workflow",
        "area": "refund_support_proof",
        "title": "Refund and support workflow proof",
        "owner_role": "support_operations",
        "required_reviewer_role": "support_reviewer",
        "private_media_required": False,
        "redacted_summary_template": (
            "Captured refund/support workflow proof. Include response path, escalation owner, customer-safe "
            "failure copy, and rollback trigger without private payment payloads."
        ),
        "checklist": [
            "Refund request path documented.",
            "Failed-payment support queue documented.",
            "Escalation owner assigned.",
            "Customer-safe cancellation/refund copy reviewed.",
        ],
    },
    {
        "key": "rollback_drill",
        "area": "rollback_proof",
        "title": "Monetization rollback drill proof",
        "owner_role": "release_management",
        "required_reviewer_role": "release_manager",
        "private_media_required": True,
        "redacted_summary_template": (
            "Captured rollback drill evidence for disabling monetization flags, stopping provider traffic, "
            "pausing entitlement enforcement, and notifying support."
        ),
        "checklist": [
            "Billing flags rollback verified.",
            "Provider traffic stop plan verified.",
            "Entitlement enforcement remains disabled.",
            "Support communication template reviewed.",
        ],
    },
    {
        "key": "private_media_signed_access",
        "area": "privacy_analytics_policy",
        "title": "Private media signed-access proof",
        "owner_role": "privacy_security",
        "required_reviewer_role": "privacy_security_reviewer",
        "private_media_required": True,
        "redacted_summary_template": (
            "Captured private MediaAsset signed-access proof. Include only private MediaAsset id, access outcome, "
            "expiry behavior, and denial proof for non-owner/non-staff access."
        ),
        "checklist": [
            "Private MediaAsset id captured.",
            "Signed access expires as expected.",
            "Unauthorized access denied.",
            "No raw storage path exposed.",
        ],
    },
)


def get_staging_monetization_proof_workflows(*, user=None) -> dict[str, Any]:
    readiness = get_revenue_launch_readiness_summary(user=user)
    return {
        "enabled": False,
        "access": "staff_read_only",
        "mode": "staging_evidence_capture_templates_only",
        "workflows": {
            item["key"]: {
                **item,
                "live_provider_call": False,
                "stores_raw_payload": False,
                "stores_payment_instrument": False,
            }
            for item in STAGING_PROOF_WORKFLOWS
        },
        "readiness": {
            "go_no_go": readiness.get("go_no_go"),
            "readiness_percent": readiness.get("readiness_percent", 0),
            "ready_count": readiness.get("ready_count", 0),
            "total_count": readiness.get("total_count", 0),
        },
        "guardrails": {
            "staging_only": True,
            "templates_only": True,
            "no_live_charges": True,
            "no_production_provider_calls": True,
            "no_entitlement_enforcement": True,
            "private_media_references_only": True,
            "no_raw_provider_payloads": True,
            "no_payment_instrument_collection": True,
            "no_private_health_payment_verification_data": True,
        },
        "next_readiness_steps": [
            "Run approved staging sandbox checks outside this endpoint.",
            "Create revenue launch evidence records using redacted summaries and private MediaAsset ids only.",
            "Assign reviewer roles before approval.",
            "Keep monetization flags disabled until all required evidence is approved and non-expired.",
        ],
    }
