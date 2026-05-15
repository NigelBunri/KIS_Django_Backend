from __future__ import annotations

from typing import Any


AGE_MODES = {"child", "youth", "adult", "older_adult"}
NAVIGATION_MODES = {"standard", "simplified", "guided"}
FONT_SCALE_MODES = {"standard", "large", "extra_large"}
MOTION_MODES = {"system", "reduced"}
CONTRAST_MODES = {"standard", "high"}


DEFAULT_FAMILY_ACCESSIBILITY_PREFERENCES = {
    "age_mode": "adult",
    "navigation_mode": "standard",
    "font_scale": "standard",
    "motion": "system",
    "contrast": "standard",
    "family_safe_content": True,
    "safe_recommendations": True,
    "hide_sensitive_commerce": False,
    "hide_public_comments_for_child": True,
    "guardian_review_required": False,
    "bible_family_journeys": True,
    "learning_family_mode": True,
    "large_tap_targets": True,
    "simplified_labels": False,
}


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def normalize_family_accessibility_preferences(value: dict | None) -> dict:
    raw = value if isinstance(value, dict) else {}
    normalized = dict(DEFAULT_FAMILY_ACCESSIBILITY_PREFERENCES)
    normalized["age_mode"] = raw.get("age_mode") if raw.get("age_mode") in AGE_MODES else normalized["age_mode"]
    normalized["navigation_mode"] = raw.get("navigation_mode") if raw.get("navigation_mode") in NAVIGATION_MODES else normalized["navigation_mode"]
    normalized["font_scale"] = raw.get("font_scale") if raw.get("font_scale") in FONT_SCALE_MODES else normalized["font_scale"]
    normalized["motion"] = raw.get("motion") if raw.get("motion") in MOTION_MODES else normalized["motion"]
    normalized["contrast"] = raw.get("contrast") if raw.get("contrast") in CONTRAST_MODES else normalized["contrast"]
    for key, default in DEFAULT_FAMILY_ACCESSIBILITY_PREFERENCES.items():
        if isinstance(default, bool):
            normalized[key] = _bool(raw.get(key), default)
    if normalized["age_mode"] == "child":
        normalized.update(
            {
                "family_safe_content": True,
                "safe_recommendations": True,
                "hide_sensitive_commerce": True,
                "hide_public_comments_for_child": True,
                "guardian_review_required": True,
                "navigation_mode": "guided" if normalized["navigation_mode"] == "standard" else normalized["navigation_mode"],
                "large_tap_targets": True,
                "simplified_labels": True,
            }
        )
    if normalized["age_mode"] == "older_adult":
        normalized.update({"large_tap_targets": True, "font_scale": "large" if normalized["font_scale"] == "standard" else normalized["font_scale"]})
    return normalized


def serialize_family_accessibility_preferences(user) -> dict:
    preferences = dict(getattr(user, "preferences", {}) or {})
    stored = preferences.get("family_accessibility") if isinstance(preferences.get("family_accessibility"), dict) else {}
    normalized = normalize_family_accessibility_preferences(stored)
    return {
        "preferences": normalized,
        "accessibility": {
            "min_touch_target": 56 if normalized["age_mode"] == "older_adult" else 52 if normalized["age_mode"] == "child" else 48,
            "font_scale_multiplier": 1.18 if normalized["font_scale"] == "large" else 1.3 if normalized["font_scale"] == "extra_large" else 1,
            "reduced_motion": normalized["motion"] == "reduced",
            "high_contrast": normalized["contrast"] == "high",
            "simplified_navigation": normalized["navigation_mode"] in {"simplified", "guided"},
        },
        "family_safety": {
            "christian_principles_visible": True,
            "pornography_blocked_everywhere": True,
            "media_safety_gate_required": True,
            "safe_recommendations": normalized["safe_recommendations"],
            "child_youth_defaults": normalized["age_mode"] in {"child", "youth"},
            "guardian_review_required": normalized["guardian_review_required"],
        },
        "journeys": {
            "bible_family_journeys": normalized["bible_family_journeys"],
            "learning_family_mode": normalized["learning_family_mode"],
            "recommended_bible_entry": "Bible",
            "recommended_learning_entry": "Education",
        },
    }


def update_family_accessibility_preferences(user, updates: dict | None) -> dict:
    preferences = dict(getattr(user, "preferences", {}) or {})
    current = preferences.get("family_accessibility") if isinstance(preferences.get("family_accessibility"), dict) else {}
    merged = normalize_family_accessibility_preferences({**current, **(updates or {})})
    preferences["family_accessibility"] = merged
    user.preferences = preferences
    user.save(update_fields=["preferences", "updated_at"])
    return serialize_family_accessibility_preferences(user)
