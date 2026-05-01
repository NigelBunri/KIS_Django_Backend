from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from rest_framework.exceptions import ValidationError


@dataclass(frozen=True)
class FeedEntryResolution:
    profile: dict[str, Any]
    feeds: list[dict[str, Any]]
    index: int
    entry: dict[str, Any]


def get_feed_entries(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(profile, dict):
        return []
    feeds = profile.get("feeds") or []
    if not isinstance(feeds, list):
        return []
    return [deepcopy(item) for item in feeds if isinstance(item, dict)]


def with_feed_entries(
    profile: dict[str, Any] | None,
    feeds: list[dict[str, Any]],
) -> dict[str, Any]:
    next_profile = dict(profile or {})
    next_profile["feeds"] = [deepcopy(item) for item in feeds if isinstance(item, dict)]
    return next_profile


def resolve_feed_entry(
    profile: dict[str, Any] | None,
    entry_id: str,
) -> FeedEntryResolution:
    feeds = get_feed_entries(profile)
    for index, entry in enumerate(feeds):
        if str(entry.get("id")) == str(entry_id):
            return FeedEntryResolution(
                profile=with_feed_entries(profile, feeds),
                feeds=feeds,
                index=index,
                entry=deepcopy(entry),
            )
    raise ValidationError({"detail": "Feed item not found."})


def append_feed_entry(
    profile: dict[str, Any] | None,
    entry: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    feeds = get_feed_entries(profile)
    feeds.append(deepcopy(entry))
    return with_feed_entries(profile, feeds), feeds


def replace_feed_entry(
    profile: dict[str, Any] | None,
    entry_id: str,
    updater: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    resolved = resolve_feed_entry(profile, entry_id)
    updated_entry = updater(deepcopy(resolved.entry))
    if not isinstance(updated_entry, dict):
        raise ValidationError({"detail": "Feed entry update produced invalid data."})
    feeds = list(resolved.feeds)
    feeds[resolved.index] = deepcopy(updated_entry)
    return with_feed_entries(profile, feeds), feeds, updated_entry


def delete_feed_entry(
    profile: dict[str, Any] | None,
    entry_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    resolved = resolve_feed_entry(profile, entry_id)
    feeds = list(resolved.feeds)
    removed = feeds.pop(resolved.index)
    return with_feed_entries(profile, feeds), feeds, removed
