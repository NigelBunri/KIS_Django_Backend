from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings

PROMOTIONAL_CREDIT_SAFETY_COPY = (
    "KIS promotional credits are reward/subsidy credits only. They are not cash, "
    "not transferable, not withdrawable, and not exchange-rated."
)


@dataclass(frozen=True)
class ProfitabilityPlanDefinition:
    id: str
    audience: str
    name: str
    price_label: str
    billing_mode: str
    enabled: bool = False


PROFITABILITY_PLANS: tuple[ProfitabilityPlanDefinition, ...] = (
    ProfitabilityPlanDefinition("consumer_plus", "consumer", "Consumer Plus", "$4.99/mo or $49/yr", "subscription"),
    ProfitabilityPlanDefinition("family_plus", "consumer", "Family Plus", "$7.99/mo or $79/yr", "subscription"),
    ProfitabilityPlanDefinition("creator_pro", "creator", "Creator Pro", "$9.99/mo or $99/yr", "subscription"),
    ProfitabilityPlanDefinition("creator_growth", "creator", "Creator Growth", "$29.99/mo or $299/yr", "subscription"),
    ProfitabilityPlanDefinition("institution_starter", "institution", "Institution Starter", "$19.99/mo or $199/yr", "subscription"),
    ProfitabilityPlanDefinition("institution_growth", "institution", "Institution Growth", "$59.99/mo or $599/yr", "subscription"),
    ProfitabilityPlanDefinition("partner_workspace_pro", "partner", "Partner Workspace Pro", "$29.99/mo or $299/yr", "subscription"),
    ProfitabilityPlanDefinition("seller_pro", "seller", "Seller Pro", "$14.99/mo or $149/yr", "subscription"),
    ProfitabilityPlanDefinition("instructor_pro", "education", "Instructor Pro", "$14.99/mo or $149/yr", "subscription"),
    ProfitabilityPlanDefinition("education_institution_pro", "education", "Education Institution Pro", "$49.99/mo or $499/yr", "subscription"),
    ProfitabilityPlanDefinition("health_provider_pro", "health", "Health Provider Pro", "$39.99/mo or $399/yr", "subscription"),
    ProfitabilityPlanDefinition("health_institution_growth", "health", "Health Institution Growth", "$79.99/mo or $799/yr", "subscription"),
    ProfitabilityPlanDefinition("verification_processing", "verification", "Verification Processing", "From $9.99 per institution review", "processing_fee"),
    ProfitabilityPlanDefinition("promotion_packages", "promotion", "Promotion Packages", "From $5 per campaign", "campaign_fee"),
    ProfitabilityPlanDefinition("enterprise", "enterprise", "Enterprise", "Custom annual contract", "annual_contract"),
)


PROFITABILITY_FEATURE_FLAGS: dict[str, dict[str, Any]] = {
    "profitability.billing_live": {
        "enabled": False,
        "env_setting": "KIS_PROFITABILITY_BILLING_ENABLED",
        "description": "Allows live billing surfaces. Must remain false until launch approval.",
    },
    "profitability.entitlements_enforced": {
        "enabled": False,
        "env_setting": "KIS_PROFITABILITY_ENTITLEMENTS_ENFORCED",
        "description": "Allows server-side plan enforcement. Must remain false during preview phases.",
    },
    "profitability.trials_enabled": {
        "enabled": False,
        "env_setting": "KIS_PROFITABILITY_TRIALS_ENABLED",
        "description": "Allows real trial lifecycle state. Preview metadata only for now.",
    },
    "profitability.promotions_checkout_enabled": {
        "enabled": False,
        "env_setting": "KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED",
        "description": "Allows paid promotion checkout after campaign moderation is ready.",
    },
    "profitability.enterprise_leads_enabled": {
        "enabled": False,
        "env_setting": "KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED",
        "description": "Allows enterprise lead capture. Disabled to avoid lead spam and unreviewed contracts.",
    },
}


PROFITABILITY_ENTITLEMENTS: dict[str, dict[str, Any]] = {
    "consumer.saved_content.expanded": {"plan_ids": ["consumer_plus", "family_plus"], "meter": "saved_content"},
    "consumer.family_controls.advanced": {"plan_ids": ["family_plus"], "meter": "family_members"},
    "creator.channels.limit": {"plan_ids": ["creator_pro", "creator_growth"], "meter": "channels"},
    "creator.scheduled_posts": {"plan_ids": ["creator_pro", "creator_growth"], "meter": "scheduled_posts"},
    "creator.analytics.advanced": {"plan_ids": ["creator_growth"], "meter": "analytics_exports"},
    "institution.staff_seats": {"plan_ids": ["institution_starter", "institution_growth"], "meter": "staff_seats"},
    "partner.workspace_seats": {"plan_ids": ["partner_workspace_pro", "enterprise"], "meter": "workspace_seats"},
    "seller.featured_listings": {"plan_ids": ["seller_pro", "promotion_packages"], "meter": "featured_listings"},
    "education.certificates": {"plan_ids": ["education_institution_pro"], "meter": "certificates"},
    "health.provider_dashboard": {"plan_ids": ["health_provider_pro", "health_institution_growth"], "meter": "care_workflows"},
    "verification.processing": {"plan_ids": ["verification_processing"], "meter": "verification_cases"},
    "promotion.campaigns": {"plan_ids": ["promotion_packages"], "meter": "campaigns"},
    "enterprise.network": {"plan_ids": ["enterprise"], "meter": "branches"},
}


USAGE_METERS: dict[str, dict[str, Any]] = {
    "saved_content": {"free_limit": 100, "unit": "items", "enforced": False},
    "family_members": {"free_limit": 1, "unit": "household profiles", "enforced": False},
    "channels": {"free_limit": 1, "unit": "channels", "enforced": False},
    "scheduled_posts": {"free_limit": 0, "unit": "scheduled posts", "enforced": False},
    "analytics_exports": {"free_limit": 0, "unit": "exports", "enforced": False},
    "staff_seats": {"free_limit": 1, "unit": "staff seats", "enforced": False},
    "workspace_seats": {"free_limit": 5, "unit": "workspace seats", "enforced": False},
    "featured_listings": {"free_limit": 0, "unit": "featured listings", "enforced": False},
    "certificates": {"free_limit": 0, "unit": "certificates", "enforced": False},
    "care_workflows": {"free_limit": 1, "unit": "care workflows", "enforced": False},
    "verification_cases": {"free_limit": 0, "unit": "review cases", "enforced": False},
    "campaigns": {"free_limit": 0, "unit": "campaigns", "enforced": False},
    "branches": {"free_limit": 1, "unit": "branches", "enforced": False},
}


def _flag_enabled(flag_key: str, *, default: bool = False) -> bool:
    config = PROFITABILITY_FEATURE_FLAGS.get(flag_key) or {}
    setting_name = config.get("env_setting")
    if not setting_name:
        return default
    return bool(getattr(settings, str(setting_name), default))


def _plan_payload(plan: ProfitabilityPlanDefinition) -> dict[str, Any]:
    return {
        "id": plan.id,
        "audience": plan.audience,
        "name": plan.name,
        "price_label": plan.price_label,
        "billing_mode": plan.billing_mode,
        "enabled": False,
        "billing_status": "preview_only",
        "trial_ready": False,
    }


def get_profitability_entitlement_catalog(*, user=None) -> dict[str, Any]:
    """Return safe billing readiness metadata without granting or enforcing access."""
    flags = {
        key: {
            **value,
            "enabled": _flag_enabled(key, default=False),
        }
        for key, value in PROFITABILITY_FEATURE_FLAGS.items()
    }

    return {
        "enabled": False,
        "enforcement_enabled": False,
        "billing_live": False,
        "user_id": str(getattr(user, "id", "") or "") if user and getattr(user, "is_authenticated", False) else "",
        "plans": [_plan_payload(plan) for plan in PROFITABILITY_PLANS],
        "feature_flags": flags,
        "entitlements": {
            key: {
                **value,
                "enabled": False,
                "enforced": False,
                "status": "preview_only",
            }
            for key, value in PROFITABILITY_ENTITLEMENTS.items()
        },
        "usage_meters": {
            key: {
                **value,
                "current": 0,
                "status": "not_tracked",
            }
            for key, value in USAGE_METERS.items()
        },
        "billing_status": {
            "provider": "",
            "live_provider_connected": False,
            "subscriptions_enabled": False,
            "trials_enabled": False,
            "promotion_checkout_enabled": False,
            "enterprise_leads_enabled": False,
        },
        "policy": {
            "preview_only": True,
            "hard_blocks_existing_free_behavior": False,
            "promotional_credit_safety_copy": PROMOTIONAL_CREDIT_SAFETY_COPY,
            "notes": [
                "No live charges are enabled.",
                "No entitlement enforcement is enabled.",
                "No KIS promotional credit cash value, transfer, withdrawal, or exchange rate is enabled.",
            ],
        },
    }


def can_use_profitability_feature(feature_key: str, *, user=None) -> dict[str, Any]:
    entitlement = PROFITABILITY_ENTITLEMENTS.get(feature_key)
    if not entitlement:
        return {"allowed": True, "reason": "unknown_feature_preview_passthrough", "enforced": False}
    return {
        "allowed": True,
        "reason": "preview_only_no_hard_block",
        "enforced": False,
        "plan_ids": entitlement.get("plan_ids", []),
        "meter": entitlement.get("meter", ""),
    }
