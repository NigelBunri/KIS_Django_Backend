from __future__ import annotations

import datetime
from functools import reduce
from operator import or_

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.accounts.models import AccountTier, Subscription, UsageQuota
from .tier_presets import TIER_PRESETS


# Canonical tier hierarchy — matches TIER_PRESETS order exactly.
# 'basic' was renamed to 'free' during tier system rollout; handled via alias below.
TIER_HIERARCHY = [
    "free",
    "pro",
    "business",
    "business pro",
    "partner",
    "partner pro",
]

# Legacy or alternative tier names that map to a canonical name above.
TIER_NAME_ALIASES: dict[str, str] = {
    "basic": "free",  # renamed during tier system rollout
}

UNLIMITED_TOKENS = {"unlimited", "infinite", "none", "no-limit", "no_limit", "∞"}


def get_user_tier(user) -> AccountTier | None:
    sub = Subscription.objects.filter(user=user, status="active").select_related("tier").first()
    if sub and sub.tier:
        return sub.tier
    if hasattr(user, "tier"):
        # Resolve aliases (e.g. 'basic' → 'free') before querying the DB.
        canonical = _normalize_tier_name(user.tier)
        if canonical:
            return AccountTier.objects.filter(name__iexact=canonical).first()
    return None


def _normalize_tier_name(name: str | None) -> str:
    normalized = (name or "").strip().lower()
    return TIER_NAME_ALIASES.get(normalized, normalized)


def _tier_weight(name: str | None) -> int:
    """
    Rank for a tier given only its NAME (not an AccountTier instance) — e.g.
    resolving the denormalized User.tier string. Prefers the database-backed
    AccountTier.rank column (the single authoritative source); falls back to
    TIER_HIERARCHY's list position only for a name with no matching row
    (unseeded state, or a legacy/test-only tier name).
    """
    normalized = _normalize_tier_name(name)
    if not normalized:
        # No tier name at all (e.g. a user record with an unset tier field)
        # must never be treated as a higher/paid tier — rank 0 (free/base),
        # not the "unmatched name" sentinel below, which is reserved for a
        # genuinely unrecognized but non-empty tier name.
        return 0
    db_rank = (
        AccountTier.objects.filter(name__iexact=normalized).values_list("rank", flat=True).first()
    )
    if db_rank is not None:
        return db_rank
    try:
        return TIER_HIERARCHY.index(normalized)
    except ValueError:
        return len(TIER_HIERARCHY)


def tier_rank(name: str | None) -> int:
    """Public wrapper — the one place any caller should resolve a tier's rank by name."""
    return _tier_weight(name)


def is_paid_tier_name(name: str | None) -> bool:
    """True for any tier ranked above the free/base tier (rank 0)."""
    return tier_rank(name) > 0


def _ordered_account_tiers() -> list[AccountTier]:
    # Rank comes straight off each already-fetched instance — no per-row
    # lookup, unlike _tier_weight()'s name-only path above.
    return sorted(
        AccountTier.objects.all(),
        key=lambda tier: (tier.rank, tier.created_at or datetime.datetime.min),
    )


def get_aggregated_tier_features(tier: AccountTier) -> dict:
    if not tier:
        return {}
    target_weight = tier.rank
    features: dict = {}
    for candidate in _ordered_account_tiers():
        if candidate.rank > target_weight:
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


PUBLIC_TIER_NAMES = tuple(
    preset["name"].strip()
    for preset in TIER_PRESETS
    if preset.get("name")
)

PUBLIC_TIER_NAMES_LOWER = {name.lower() for name in PUBLIC_TIER_NAMES}


def public_account_tiers_qs() -> QuerySet[AccountTier]:
    if not PUBLIC_TIER_NAMES:
        return AccountTier.objects.none()
    predicate = reduce(
        or_,
        (Q(name__iexact=name) for name in PUBLIC_TIER_NAMES),
        Q(),
    )
    return AccountTier.objects.filter(predicate)


def is_public_tier_name(name: str | None) -> bool:
    return (str(name or "").strip().lower()) in PUBLIC_TIER_NAMES_LOWER


def ensure_default_account_tiers() -> None:
    """
    Idempotent AND self-healing: the single authoritative seed path for
    AccountTier, called on every /api/v1/tiers/ request. Previously this
    short-circuited entirely once all 6 tier NAMES existed, so a later edit
    to TIER_PRESETS' price/features/rank never propagated to already-seeded
    rows. Now each preset is compared field-by-field and only written when
    something actually drifted, so it stays both cheap (no writes once
    settled) and correct (drift doesn't silently persist forever).
    """
    existing = {(tier.name or "").strip().lower(): tier for tier in AccountTier.objects.all()}

    for preset in TIER_PRESETS:
        key = (preset["name"] or "").strip().lower()
        desired = {
            "price_cents": preset["price_cents"],
            "features_json": preset["features_json"],
            "rank": preset.get("rank", 0),
            "billing_period_days": preset.get("billing_period_days", 30),
        }
        current = existing.get(key)
        if current is not None and all(getattr(current, field) == value for field, value in desired.items()):
            continue
        AccountTier.objects.update_or_create(name=preset["name"], defaults=desired)
