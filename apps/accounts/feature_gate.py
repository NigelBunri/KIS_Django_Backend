from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

from .tiers import get_user_tier_features


def has_feature(user, key: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    features = get_user_tier_features(user)
    value = features.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return bool(value)


def require_feature(user, key: str, message: str | None = None) -> None:
    if not has_feature(user, key):
        raise PermissionDenied(message or f"Your current plan does not include {key.replace('_', ' ')}.")
