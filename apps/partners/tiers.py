# apps/partners/tiers.py
"""
Workspace-level (Partner) tier resolution — the partner-scoped counterpart
to apps.accounts.tiers/apps.accounts.feature_gate, which resolve a USER's
personal tier. See PartnerSubscription's docstring (apps/partners/models.py)
for why these are deliberately separate: a partner-scoped feature (webhooks,
automation, integrations, insight/analytics, access control) must be gated
by the ORGANIZATION's own plan, not whichever staff member happens to be
making the request.

Reuses apps.accounts.tiers.get_aggregated_tier_features directly — that
function already takes a plain AccountTier instance, not a user, so there is
nothing partner-specific to duplicate; only the "which AccountTier applies"
resolution step differs (PartnerSubscription instead of personal
Subscription).
"""

from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import AccountTier
from apps.accounts.tiers import get_aggregated_tier_features

from .models import Partner, PartnerSubscription


def get_partner_tier(partner: Partner) -> AccountTier | None:
    sub = (
        PartnerSubscription.objects.filter(partner=partner, status=PartnerSubscription.STATUS_ACTIVE)
        .select_related("tier")
        .first()
    )
    return sub.tier if sub and sub.tier else None


def get_partner_tier_features(partner: Partner) -> dict:
    tier = get_partner_tier(partner)
    return get_aggregated_tier_features(tier) if tier else {}


def has_partner_feature(partner: Partner, key: str) -> bool:
    features = get_partner_tier_features(partner)
    value = features.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return bool(value)


def require_partner_feature(partner: Partner, key: str, message: str | None = None) -> None:
    if not has_partner_feature(partner, key):
        raise PermissionDenied(
            message or f"This organization's current plan does not include {key.replace('_', ' ')}."
        )


def get_partner_feature_limit(partner: Partner, key: str, default=None):
    return get_partner_tier_features(partner).get(key, default)
