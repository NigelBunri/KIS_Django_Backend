from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db.models import Count

from .models import DirectPaymentIntent
from .profitability_entitlements import get_profitability_entitlement_catalog


MODULE_REVENUE_POTENTIAL: dict[str, dict[str, Any]] = {
    "profile": {"primary_plans": ["consumer_plus", "family_plus"], "revenue_streams": ["subscriptions", "verification_interest"]},
    "bible": {"primary_plans": ["consumer_plus", "family_plus"], "revenue_streams": ["spiritual_growth_subscriptions"]},
    "messaging": {"primary_plans": ["consumer_plus", "partner_workspace_pro"], "revenue_streams": ["workspace_seats", "enterprise_support"]},
    "broadcast_channels": {"primary_plans": ["creator_pro", "creator_growth", "promotion_packages"], "revenue_streams": ["creator_subscriptions", "promotion_packages"]},
    "partners": {"primary_plans": ["partner_workspace_pro", "enterprise"], "revenue_streams": ["workspace_seats", "enterprise_contracts"]},
    "commerce": {"primary_plans": ["seller_pro", "promotion_packages"], "revenue_streams": ["seller_subscriptions", "transaction_fees", "promotions"]},
    "education": {"primary_plans": ["instructor_pro", "education_institution_pro"], "revenue_streams": ["institution_subscriptions", "course_commissions", "certificates"]},
    "health": {"primary_plans": ["health_provider_pro", "health_institution_growth"], "revenue_streams": ["provider_subscriptions", "service_payments"]},
    "verification": {"primary_plans": ["verification_processing"], "revenue_streams": ["processing_fees", "badge_renewal_reviews"]},
    "public_web": {"primary_plans": ["enterprise", "promotion_packages"], "revenue_streams": ["public_growth", "enterprise_contracts"]},
}


TRACKED_INTEREST_EVENTS = (
    "plan_interest",
    "upgrade_prompt_impression",
    "verification_fee_interest",
    "promotion_package_interest",
    "enterprise_packaging_interest",
)


def _direct_payment_status_counts() -> dict[str, int]:
    rows = (
        DirectPaymentIntent.objects.values("status")
        .annotate(total=Count("id"))
        .order_by()
    )
    return {str(row["status"] or "unknown"): int(row["total"] or 0) for row in rows}


def get_profitability_command_center_summary(*, user=None) -> dict[str, Any]:
    catalog = get_profitability_entitlement_catalog(user=user)
    direct_status_counts = _direct_payment_status_counts()
    total_direct_intents = sum(direct_status_counts.values())

    event_placeholders = {
        event_name: {
            "captured": 0,
            "status": "placeholder_not_tracking",
            "privacy": "aggregate_only_when_enabled",
        }
        for event_name in TRACKED_INTEREST_EVENTS
    }

    usage_summary = {
        key: {
            "free_limit": value.get("free_limit"),
            "unit": value.get("unit"),
            "current": value.get("current", 0),
            "enforced": False,
            "status": "not_tracked",
        }
        for key, value in catalog.get("usage_meters", {}).items()
    }

    module_summary = {
        key: {
            **value,
            "preview_only": True,
            "private_data_exposed": False,
            "live_revenue_enabled": False,
        }
        for key, value in MODULE_REVENUE_POTENTIAL.items()
    }

    return {
        "enabled": False,
        "tracking_live": False,
        "privacy_mode": "aggregate_placeholders_only",
        "plan_interest_events": event_placeholders,
        "upgrade_prompt_impressions": {
            "total": 0,
            "status": "placeholder_not_tracking",
        },
        "usage_meter_summary": usage_summary,
        "direct_usd_payment_readiness": {
            "provider": getattr(settings, "KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER", "flutterwave"),
            "provider_links_enabled": bool(getattr(settings, "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED", False)),
            "live_payment_provider_connected": False,
            "intent_status_counts": direct_status_counts,
            "total_intents": total_direct_intents,
            "private_payment_data_exposed": False,
        },
        "module_revenue_potential": module_summary,
        "conversion_funnel": {
            "upgrade_prompt_impression": 0,
            "plan_interest": 0,
            "checkout_started": 0,
            "payment_completed": 0,
            "status": "placeholder_not_tracking",
        },
        "guardrails": {
            "no_live_charges": True,
            "no_intrusive_tracking": True,
            "no_dark_patterns": True,
            "no_private_health_data": True,
            "no_private_verification_documents": True,
            "no_payment_instrument_data": True,
            "promotional_credits_non_cash": True,
        },
        "next_readiness_steps": [
            "Define privacy-safe event schema.",
            "Add explicit user consent and analytics settings before tracking.",
            "Aggregate event counts without storing private payloads.",
            "Wire revenue dashboard only after legal/product/privacy review.",
        ],
    }
