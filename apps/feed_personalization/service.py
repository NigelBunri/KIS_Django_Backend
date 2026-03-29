from __future__ import annotations

import functools
import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, TYPE_CHECKING

from collections.abc import Mapping as MappingABC

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

if TYPE_CHECKING:
    from .models import FeedAffinityProfile, FeedInteraction


FEED_PERSONALIZATION_FEED_TYPES = ("broadcast", "community", "partner")
RATE_LIMIT_SECONDS = getattr(settings, "FEED_PERSONALIZATION_RATE_LIMIT_SECONDS", 8)
MIN_EVENT_WEIGHT = getattr(settings, "FEED_PERSONALIZATION_MIN_WEIGHT", 0.02)
GLOBAL_POPULARITY_TTL = getattr(settings, "FEED_PERSONALIZATION_GLOBAL_POPULARITY_TTL", 300)
GLOBAL_POPULARITY_DECAY = getattr(settings, "FEED_PERSONALIZATION_GLOBAL_POPULARITY_DECAY", 0.92)
GLOBAL_POPULARITY_DEFAULT = getattr(settings, "FEED_PERSONALIZATION_DEFAULT_POPULARITY", 0.06)
PERSONALIZATION_HISTORY_CACHE_TTL = getattr(
    settings,
    "FEED_PERSONALIZATION_HISTORY_CACHE_TTL",
    120,
)


@functools.lru_cache(maxsize=1)
def _feed_models():
    from .models import FeedAffinityProfile, FeedInteraction

    return FeedAffinityProfile, FeedInteraction


@dataclass
class FeedPersonalizationConfig:
    """Configuration for the light-weight personalization heuristics."""

    recency_window_hours: float = 72.0
    recency_weight: float = 0.45
    engagement_weight: float = 0.35
    affinity_weight: float = 0.15
    priority_weight: float = 0.15
    max_sample_size: int = 400
    sample_multiplier: int = 4
    default_sample_limit: int = 50
    random_jitter: float = 0.001


class FeedPersonalizationService:
    """Score items based on freshness, engagement and affinity (no ML models)."""

    def __init__(self, config: Optional[FeedPersonalizationConfig] = None):
        self.config = config or FeedPersonalizationConfig()

    def rank_items(
        self,
        items: Sequence[Any],
        user,
        feed_type: str = "generic",
        metadata_map: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> list[Any]:
        if not items:
            return []

        candidates = list(items)
        if not candidates:
            return []

        normalized_feed_type = _normalize_feed_type(feed_type)
        if not _has_personalization_history(user, normalized_feed_type):
            rng = random.Random()
            rng.shuffle(candidates)
            return candidates

        now = timezone.now()
        metadata_map = metadata_map or {}
        scored_items = []

        rng = random.Random()
        for index, item in enumerate(candidates):
            key = str(self._get_value(item, "id", index))
            metadata = metadata_map.get(key, {})
            score = self._score_item(item, user, now, feed_type, metadata)
            tie_break = self._tie_break_value(item)
            jitter = rng.random() * self.config.random_jitter
            scored_items.append((score, tie_break + jitter, -index, item))

        scored_items.sort(reverse=True, key=lambda row: (row[0], row[1], row[2]))
        return [row[3] for row in scored_items]

    def _score_item(
        self,
        item: Any,
        user,
        now: datetime,
        feed_type: str,
        metadata: Mapping[str, Any],
    ) -> float:
        recency = self._recency_score(item, now)
        engagement = self._engagement_score(item)
        affinity = self._affinity_score(item, metadata, feed_type)
        priority = self._priority_score(item, feed_type)

        weights = [
            (self.config.recency_weight, recency),
            (self.config.engagement_weight, engagement),
            (self.config.affinity_weight, affinity),
            (self.config.priority_weight, priority),
        ]

        total_weight = sum(weight for weight, _ in weights)
        if total_weight <= 0:
            return 0.0

        return sum(weight * score for weight, score in weights) / total_weight

    def _recency_score(self, item: Any, now: datetime) -> float:
        timestamp = self._parse_datetime(
            self._get_value(item, "broadcasted_at") or self._get_value(item, "created_at")
        )
        if not timestamp:
            return 0.0
        age_hours = max((now - timestamp).total_seconds() / 3600.0, 0.0)
        normalized = min(age_hours / max(self.config.recency_window_hours, 1.0), 1.0)
        return max(0.0, 1.0 - normalized)

    def _engagement_score(self, item: Any) -> float:
        reactions = self._safe_number(item, "reaction_count")
        comments = self._safe_number(item, "comment_count")
        shares = self._safe_number(item, "share_count")
        views = self._safe_number(item, "view_count")
        live = self._safe_number(item, "live_viewers")
        viewer_reaction = 1 if self._get_value(item, "viewer_reaction") else 0

        raw_score = (
            reactions
            + (comments * 1.5)
            + (shares * 2.0)
            + (views * 0.02)
            + (live * 0.02)
            + viewer_reaction
        )

        if raw_score <= 0:
            return 0.0

        normalized = math.log1p(raw_score) / 3.5
        return min(1.0, normalized)

    def _affinity_score(self, item: Any, metadata: Mapping[str, Any], feed_type: str) -> float:
        source = metadata.get("source") or self._get_value(item, "source") or {}
        if not isinstance(source, MappingABC):
            return 0.0

        score = 0.0
        affinity_override = metadata.get("affinity_override")
        if isinstance(affinity_override, (int, float)):
            score += float(affinity_override)
            return min(1.0, score)

        if source.get("is_member"):
            score += 0.4
        if source.get("is_subscribed"):
            score += 0.35
        if source.get("can_open"):
            score += 0.2
        if source.get("is_followed"):
            score += 0.1

        profile = metadata.get("profile") or metadata.get("_profile")
        FeedAffinityProfile, _ = _feed_models()
        if isinstance(profile, FeedAffinityProfile):
            profile_value = getattr(profile, _affinity_field(feed_type), 0.0) or 0.0
            score += profile_value

        return min(1.0, score)

    def _priority_score(self, item: Any, feed_type: str) -> float:
        boosts = 0.0
        if self._get_value(item, "is_live"):
            boosts += 0.4
        if self._get_value(item, "is_pinned"):
            boosts += 0.35
        source_type = self._get_value(item, "source_type", "")
        if source_type == "broadcast_profile":
            boosts += 0.2
        if feed_type == "broadcast" and source_type == "channel":
            boosts += 0.15
        if feed_type != "broadcast" and self._get_value(item, "is_broadcast"):
            boosts += 0.2
        return min(1.0, boosts)

    def _tie_break_value(self, item: Any) -> float:
        timestamp = self._parse_datetime(
            self._get_value(item, "broadcasted_at") or self._get_value(item, "created_at")
        )
        if not timestamp:
            return 0.0
        return timestamp.timestamp()

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed:
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed)
                return parsed
        return None

    def _safe_number(self, item: Any, field: str) -> float:
        value = self._get_value(item, field, 0)
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _get_value(self, item: Any, field: str, default: Any = None) -> Any:
        if isinstance(item, MappingABC):
            return item.get(field, default)
        return getattr(item, field, default)


def _normalize_feed_type(feed_type: Optional[str]) -> Optional[str]:
    if not feed_type:
        return None
    candidate = feed_type.strip().lower()
    if not candidate or candidate == "generic":
        return None
    return candidate


def _history_cache_key(user_id: int | str, feed_type: Optional[str] = None) -> str:
    key = f"feed_personalization:history:{user_id}"
    if feed_type:
        key = f"{key}:{feed_type}"
    return key


def _has_personalization_history(user, feed_type: Optional[str]) -> bool:
    if not user or getattr(user, "id", None) is None:
        return False
    normalized = _normalize_feed_type(feed_type)
    cache_key = _history_cache_key(user.id, normalized)
    cached = cache.get(cache_key)
    if cached is not None:
        return bool(cached)
    _, FeedInteraction = _feed_models()
    filters = {"user": user}
    if normalized:
        filters["feed_type"] = normalized
    exists = FeedInteraction.objects.filter(**filters).exists()
    cache.set(cache_key, exists, PERSONALIZATION_HISTORY_CACHE_TTL)
    return exists


def _mark_personalization_history(user, feed_type: Optional[str]) -> None:
    if not user or getattr(user, "id", None) is None:
        return
    normalized = _normalize_feed_type(feed_type)
    cache_key = _history_cache_key(user.id, normalized)
    cache.set(cache_key, True, PERSONALIZATION_HISTORY_CACHE_TTL)

def _global_popularity_key(feed_type: str) -> str:
    return f"feed_personalization:popularity:{feed_type}"


def get_global_popularity(feed_type: str) -> float:
    return float(cache.get(_global_popularity_key(feed_type), GLOBAL_POPULARITY_DEFAULT))


def _increment_global_popularity(feed_type: str, weight: float) -> None:
    if feed_type not in FEED_PERSONALIZATION_FEED_TYPES:
        return
    key = _global_popularity_key(feed_type)
    current = cache.get(key, 0.0) or 0.0
    adjusted = min(1.0, (current * GLOBAL_POPULARITY_DECAY) + (weight * (1.0 - GLOBAL_POPULARITY_DECAY)))
    cache.set(key, adjusted, GLOBAL_POPULARITY_TTL)


def _seed_affinity_profile(profile: FeedAffinityProfile) -> list[str]:
    touched: list[str] = []
    for feed_type in FEED_PERSONALIZATION_FEED_TYPES:
        field = _affinity_field(feed_type)
        current = getattr(profile, field, 0.0) or 0.0
        if current <= 0.02:
            baseline = get_global_popularity(feed_type)
            if baseline > current:
                setattr(profile, field, baseline)
                touched.append(field)
    return touched


def _affinity_field(feed_type: str) -> str:
    return {
        "broadcast": "broadcast_score",
        "partner": "partner_score",
        "community": "community_score",
    }.get(feed_type, "broadcast_score")


def log_feed_interaction(user, feed_type: str, event: str, weight: float = 0.1) -> None:
    """Record a live event and gently update the per-user affinity profile."""
    if not user or not feed_type:
        return

    normalized_event = event or "feed_impression"
    normalized_weight = max(MIN_EVENT_WEIGHT, min(1.0, weight or MIN_EVENT_WEIGHT))
    if normalized_event == "feed_impression" and RATE_LIMIT_SECONDS > 0:
        rate_key = f"feed_personalization:rate:{user.id}:{feed_type}:{normalized_event}"
        if cache.get(rate_key):
            return
        cache.set(rate_key, 1, RATE_LIMIT_SECONDS)

    FeedAffinityProfile, FeedInteraction = _feed_models()
    with transaction.atomic():
        FeedInteraction.objects.create(
            user=user,
            feed_type=feed_type,
            event=normalized_event,
            weight=normalized_weight,
        )
        profile, _ = FeedAffinityProfile.objects.get_or_create(user=user)
        fields_to_save = _seed_affinity_profile(profile)
        field = _affinity_field(feed_type)
        current = getattr(profile, field, 0.0) or 0.0
        updated_value = max(0.0, min(1.0, (current * 0.8) + (normalized_weight * 0.2)))
        setattr(profile, field, updated_value)
        if field not in fields_to_save:
            fields_to_save.append(field)
        profile.save(update_fields=list(dict.fromkeys(fields_to_save)))

    _increment_global_popularity(feed_type, normalized_weight)
    _mark_personalization_history(user, feed_type)


def get_affinity_profile(user):
    if not user:
        return None
    FeedAffinityProfile, _ = _feed_models()
    profile, created = FeedAffinityProfile.objects.get_or_create(user=user)
    fields_to_save: list[str] = []
    if created:
        fields_to_save = _seed_affinity_profile(profile)
    if fields_to_save:
        profile.save(update_fields=fields_to_save)
    return profile


_DEFAULT_PERSONALIZATION_SERVICE = FeedPersonalizationService()


def rank_feed_items(
    items: Sequence[Any],
    user,
    feed_type: str = "generic",
    metadata_map: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> list[Any]:
    return _DEFAULT_PERSONALIZATION_SERVICE.rank_items(
        items, user=user, feed_type=feed_type, metadata_map=metadata_map
    )


def resolve_personalization_sample_limit(
    limit_param: Optional[str],
    *,
    default: int = FeedPersonalizationConfig.default_sample_limit,
    multiplier: int = FeedPersonalizationConfig.sample_multiplier,
    max_limit: int = FeedPersonalizationConfig.max_sample_size,
) -> int:
    try:
        requested = int(limit_param) if limit_param is not None else default
    except (TypeError, ValueError):
        requested = default
    requested = max(1, requested)
    return min(max_limit, requested * multiplier)
