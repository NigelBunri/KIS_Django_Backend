from __future__ import annotations

import datetime
from django.utils import timezone

from apps.accounts.models import AccountTier, Subscription, UsageQuota


TIER_HIERARCHY = [
    "free",
    "basic",
    "pro",
    "business",
    "business pro",
    "partner",
    "partner pro",
]

UNLIMITED_TOKENS = {"unlimited", "infinite", "none", "no-limit", "no_limit", "∞"}


def get_user_tier(user) -> AccountTier | None:
    sub = Subscription.objects.filter(user=user, status="active").select_related("tier").first()
    if sub and sub.tier:
        return sub.tier
    if hasattr(user, "tier"):
        return AccountTier.objects.filter(name__iexact=user.tier).first()
    return None


def _normalize_tier_name(name: str | None) -> str:
    return (name or "").strip().lower()


def _tier_weight(name: str | None) -> int:
    normalized = _normalize_tier_name(name)
    try:
        return TIER_HIERARCHY.index(normalized)
    except ValueError:
        return len(TIER_HIERARCHY)


def _ordered_account_tiers() -> list[AccountTier]:
    return sorted(
        AccountTier.objects.all(),
        key=lambda tier: (_tier_weight(tier.name), tier.created_at or datetime.datetime.min),
    )


def get_aggregated_tier_features(tier: AccountTier) -> dict:
    if not tier:
        return {}
    target_weight = _tier_weight(tier.name)
    features: dict = {}
    for candidate in _ordered_account_tiers():
        if _tier_weight(candidate.name) > target_weight:
            break
        features.update(candidate.features_json or {})
    return features


def get_user_tier_features(user) -> dict:
    tier = get_user_tier(user)
    return get_aggregated_tier_features(tier) if tier else {}


def get_feature_limit(user, key: str, default=None):
    features = get_user_tier_features(user)
    value = features.get(key, default)
    return value


def normalize_limit_value(value, default=None):
    """
    Normalize feature limits to:
      - int: finite limit
      - None: unlimited
      - default: unknown/unset fallback
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return None if value else 0
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    cleaned = str(value).strip()
    if not cleaned:
        return default
    lowered = cleaned.lower()
    if lowered in UNLIMITED_TOKENS:
        return None
    if lowered.isdigit():
        return int(lowered)
    try:
        return max(int(float(lowered)), 0)
    except (TypeError, ValueError):
        return default


def get_or_reset_quota(user) -> UsageQuota:
    quota, _ = UsageQuota.objects.get_or_create(user=user, defaults={"quotas_json": {}})
    if not quota.last_reset_at:
        quota.last_reset_at = timezone.now()
        quota.save(update_fields=["last_reset_at", "updated_at"])
        return quota
    if quota.last_reset_at.date() != timezone.now().date():
        quota.quotas_json = {}
        quota.last_reset_at = timezone.now()
        quota.save(update_fields=["quotas_json", "last_reset_at", "updated_at"])
    return quota


def consume_quota(user, key: str, amount: int = 1) -> bool:
    quota = get_or_reset_quota(user)
    remaining = int(quota.quotas_json.get(key, 0))
    if remaining < amount:
        return False
    quota.quotas_json[key] = remaining - amount
    quota.save(update_fields=["quotas_json", "updated_at"])
    return True
