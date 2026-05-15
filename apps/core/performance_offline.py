from __future__ import annotations

from django.conf import settings

from apps.accounts.family_accessibility import serialize_family_accessibility_preferences


def performance_offline_policy(user=None) -> dict:
    family = serialize_family_accessibility_preferences(user) if user is not None else None
    low_bandwidth_default = bool(
        family
        and family["preferences"].get("age_mode") in {"child", "older_adult"}
    )
    cache_backend = str(
        getattr(settings, "CACHES", {})
        .get("default", {})
        .get("BACKEND", "")
    )
    redis_backed = "redis" in cache_backend.lower()

    return {
        "version": "phase_22_performance_offline_foundation",
        "mode": {
            "low_bandwidth_default": low_bandwidth_default,
            "offline_first_enabled": True,
            "stale_while_revalidate_enabled": True,
            "request_deduplication_enabled": True,
            "retry_backoff_enabled": True,
            "telemetry_enabled": False,
        },
        "cache_policy": {
            "profile_ttl_seconds": 300,
            "dashboard_ttl_seconds": 300,
            "broadcast_channel_ttl_seconds": 180,
            "commerce_ttl_seconds": 180,
            "education_ttl_seconds": 600,
            "health_ttl_seconds": 120,
            "bible_offline_ttl_seconds": 2592000,
            "notifications_ttl_seconds": 60,
            "max_offline_payload_kb": 512,
            "redis_backed_server_cache": redis_backed,
        },
        "media_policy": {
            "prefer_thumbnails": True,
            "prefer_low_bandwidth_variants": True,
            "avoid_autoplay_on_low_bandwidth": True,
            "lazy_load_video": True,
            "lazy_load_documents": True,
            "placeholder_on_missing_thumbnail": True,
        },
        "pagination_policy": {
            "default_page_size": 20,
            "max_page_size": 50,
            "prefer_cursor": True,
            "preserve_legacy_limit_offset": True,
        },
        "retry_policy": {
            "base_delay_ms": 800,
            "max_delay_ms": 10000,
            "max_attempts": 3,
            "jitter": True,
            "silent_background_retry": True,
        },
        "telemetry_policy": {
            "redacted": True,
            "no_secrets": True,
            "no_raw_documents": True,
            "no_private_health_records": True,
            "no_payment_instrument_data": True,
            "no_raw_storage_paths": True,
            "events": [
                "network_state_changed",
                "offline_cache_hit",
                "offline_cache_miss",
                "request_deduped",
                "retry_scheduled",
                "low_bandwidth_mode_changed",
                "startup_ready",
            ],
        },
        "domain_readiness": {
            "messaging": {
                "cache_required": True,
                "dedupe_required": True,
                "silent_retry_required": True,
            },
            "channels": {
                "thumbnail_fallback_required": True,
                "cursor_pagination_required": True,
                "low_bandwidth_media_required": True,
            },
            "bible": {
                "offline_scripture_required": True,
                "reading_plan_local_queue_required": True,
            },
            "commerce": {
                "cart_offline_cache_required": True,
                "payment_state_refresh_required": True,
            },
            "education": {
                "lesson_progress_local_queue_required": True,
                "download_manifest_required": True,
            },
            "health": {
                "private_cache_minimized": True,
                "critical_summary_fallback_required": True,
            },
            "partners": {
                "workspace_cache_required": True,
                "unread_state_refresh_required": True,
            },
            "notifications": {
                "badge_stale_while_revalidate_required": True,
                "push_token_refresh_required": True,
            },
        },
        "privacy": {
            "public_safe": True,
            "no_secrets": True,
            "no_private_health_data": True,
            "no_payment_data": True,
            "no_verification_documents": True,
            "no_raw_storage_paths": True,
        },
    }
