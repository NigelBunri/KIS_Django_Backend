from __future__ import annotations

from typing import Any, Iterable


def normalize_health_engine_label(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


HEALTH_ENGINE_CONTACT_NOTICE = (
    "If you want this service, contact KIS from the CC partner account, then open the KIS Features group."
)

REMOVED_HEALTH_MEDIUM_NAMES = ("My test medium",)
COMING_SOON_HEALTH_MEDIUM_NAMES = (
    "Emergency Dispatch Engine",
    "Home Logistics Engine",
    "Imaging Order Engine",
    "Lab Order Engine",
    "Surgery Scheduling Engine",
)
BLOCKED_SERVICE_MEDIUM_NAMES = (*REMOVED_HEALTH_MEDIUM_NAMES, *COMING_SOON_HEALTH_MEDIUM_NAMES)
BLOCKED_BOOKING_ENGINE_KEYS = ("lab", "surgery", "emergency", "logistics")

_REMOVED_MEDIUM_SET = {normalize_health_engine_label(value) for value in REMOVED_HEALTH_MEDIUM_NAMES}
_BLOCKED_SERVICE_MEDIUM_SET = {
    normalize_health_engine_label(value) for value in BLOCKED_SERVICE_MEDIUM_NAMES
}
_BLOCKED_BOOKING_ENGINE_SET = {normalize_health_engine_label(value) for value in BLOCKED_BOOKING_ENGINE_KEYS}


def clean_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value or "").strip() for value in values if str(value or "").strip()]


def is_removed_health_medium_name(value: Any) -> bool:
    return normalize_health_engine_label(value) in _REMOVED_MEDIUM_SET


def is_blocked_service_medium_name(value: Any) -> bool:
    return normalize_health_engine_label(value) in _BLOCKED_SERVICE_MEDIUM_SET


def filter_booking_engine_keys(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        if normalize_health_engine_label(cleaned) in _BLOCKED_BOOKING_ENGINE_SET:
            continue
        if cleaned not in out:
            out.append(cleaned)
    return out


def is_service_medium_allowed(
    medium_id: Any = "",
    medium_name: Any = "",
    *,
    blocked_medium_ids: set[str] | None = None,
) -> bool:
    normalized_id = str(medium_id or "").strip()
    if normalized_id and normalized_id in (blocked_medium_ids or set()):
        return False
    return not is_blocked_service_medium_name(medium_name)


def filter_service_medium_pairs(
    raw_ids: Any,
    raw_names: Any,
    *,
    blocked_medium_ids: Iterable[Any] | None = None,
) -> tuple[list[tuple[str, str]], bool, bool]:
    medium_ids = clean_string_list(raw_ids)
    medium_names = clean_string_list(raw_names)
    max_len = max(len(medium_ids), len(medium_names))
    had_mediums = max_len > 0
    removed_any = False
    blocked_ids = {str(value or "").strip() for value in (blocked_medium_ids or []) if str(value or "").strip()}
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for index in range(max_len):
        medium_id = medium_ids[index] if index < len(medium_ids) else ""
        medium_name = medium_names[index] if index < len(medium_names) else ""
        if not medium_id and not medium_name:
            continue
        if not is_service_medium_allowed(medium_id, medium_name, blocked_medium_ids=blocked_ids):
            removed_any = True
            continue
        key = (medium_id, medium_name)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)

    return pairs, had_mediums, removed_any


def should_drop_service_after_medium_cleanup(
    *,
    had_mediums: bool,
    removed_any: bool,
    remaining_pairs: list[tuple[str, str]],
) -> bool:
    return had_mediums and removed_any and not remaining_pairs
