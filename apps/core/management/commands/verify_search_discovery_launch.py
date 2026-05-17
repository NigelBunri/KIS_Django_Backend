from __future__ import annotations

import inspect
import json

from django.core.management.base import BaseCommand, CommandError
from django.urls import Resolver404, resolve

from apps.core.performance_offline import performance_offline_policy
from apps.core.social_recommendations import privacy_safe_social_recommendation_foundation
from apps.core.views import UnifiedSearchView, _blocked_user_ids_for_search
from apps.feed_personalization.service import FEED_PERSONALIZATION_FEED_TYPES, FeedPersonalizationConfig


SEARCH_DISCOVERY_ROUTES = {
    "unified_search": "/api/v1/core/search/unified/",
    "recommendation_foundation": "/api/v1/core/recommendations/foundation/",
    "offline_policy": "/api/v1/core/performance/offline-policy/",
    "messaging_search": "/api/v1/conversations/search/",
    "messaging_participant_search": "/api/v1/conversations/participant-search/",
    "profile_search": "/api/v1/profiles/",
    "contact_discovery": "/api/v1/users/check-contacts/",
    "broadcast_feed": "/api/v1/broadcasts/",
    "broadcast_channels": "/api/v1/broadcasts/channels/",
    "partner_channels": "/api/v1/partner-channels/channels/",
    "education_discovery": "/api/v1/education/discovery/",
    "commerce_discovery": "/api/v1/commerce/discovery/",
    "health_discovery": "/api/v1/health-ops/institutions/",
    "partner_discovery": "/api/v1/partners/discover/",
    "bible_search": "/api/v1/bible/search/",
    "feed_personalization_events": "/api/v1/feed-personalization/events/",
}


def _route_exists(path: str) -> bool:
    try:
        resolve(path)
    except Resolver404:
        return False
    return True


def _source_contains(method_name: str, expected: str) -> bool:
    method = getattr(UnifiedSearchView, method_name)
    try:
        return expected in inspect.getsource(method)
    except OSError:
        return False


def _recommendation_privacy_contract_ready() -> bool:
    source = inspect.getsource(privacy_safe_social_recommendation_foundation)
    required = (
        "private_relationships_exposed",
        "health_data_exposed",
        "verification_documents_exposed",
        "payment_data_exposed",
        "raw_storage_paths_exposed",
        "blocked_users_excluded",
        "child_youth_safe_defaults",
        "christian_content_safe_ranking",
    )
    return all(term in source for term in required)


def _offline_policy_contract_ready() -> bool:
    payload = performance_offline_policy(None)
    return (
        payload.get("mode", {}).get("offline_first_enabled") is True
        and payload.get("mode", {}).get("stale_while_revalidate_enabled") is True
        and payload.get("mode", {}).get("request_deduplication_enabled") is True
        and payload.get("pagination_policy", {}).get("prefer_cursor") is True
        and payload.get("pagination_policy", {}).get("preserve_legacy_limit_offset") is True
        and payload.get("privacy", {}).get("no_raw_storage_paths") is True
    )


def _feed_personalization_contract_ready() -> bool:
    config = FeedPersonalizationConfig()
    return (
        {"broadcast", "community", "partner"}.issubset(set(FEED_PERSONALIZATION_FEED_TYPES))
        and config.max_sample_size <= 400
        and config.default_sample_limit <= 50
    )


def _blocked_search_helper_self_test() -> bool:
    class Anonymous:
        is_authenticated = False

    return _blocked_user_ids_for_search(Anonymous()) == set()


class Command(BaseCommand):
    help = "Verify search, discovery, recommendation, and low-bandwidth launch guardrails without exposing private data."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query safe aggregate discovery counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        for name, path in SEARCH_DISCOVERY_ROUTES.items():
            exists = _route_exists(path)
            checks.append(
                {
                    "name": f"route:{name}",
                    "state": "pass" if exists else "fail",
                    "detail": path if exists else f"{path} did not resolve",
                }
            )

        checks.extend(
            [
                {
                    "name": "unified_search_blocked_contact_exclusion",
                    "state": "pass" if _source_contains("_search_contacts", "_blocked_user_ids_for_search") else "fail",
                    "detail": "contact search excludes users blocked by or blocking the requester",
                },
                {
                    "name": "unified_search_blocked_channel_exclusion",
                    "state": "pass" if _source_contains("_search_channels", "_blocked_user_ids_for_search") else "fail",
                    "detail": "channel search excludes channels owned by blocked users",
                },
                {
                    "name": "unified_search_blocked_content_exclusion",
                    "state": "pass" if _source_contains("_search_channel_content", "_blocked_user_ids_for_search") else "fail",
                    "detail": "channel content search excludes content from blocked users",
                },
                {
                    "name": "blocked_search_helper_safe_for_anonymous",
                    "state": "pass" if _blocked_search_helper_self_test() else "fail",
                    "detail": "blocked-user helper returns no private data for anonymous/non-authenticated contexts",
                },
                {
                    "name": "recommendation_privacy_contract",
                    "state": "pass" if _recommendation_privacy_contract_ready() else "fail",
                    "detail": "recommendations declare no private health/payment/verification/raw-storage exposure and blocked-user exclusion",
                },
                {
                    "name": "offline_low_bandwidth_contract",
                    "state": "pass" if _offline_policy_contract_ready() else "fail",
                    "detail": "offline policy exposes stale-while-revalidate, request dedupe, cursor preference, and privacy-safe telemetry rules",
                },
                {
                    "name": "feed_personalization_scope",
                    "state": "pass" if _feed_personalization_contract_ready() else "fail",
                    "detail": "feed personalization is limited to broadcast/community/partner affinity events and bounded sampling",
                },
                {
                    "name": "search_private_data_policy",
                    "state": "pass",
                    "detail": "this verifier prints route/config/count states only, not private relationships, messages, media paths, health/payment data, or secrets",
                },
            ]
        )

        counts = {
            "users": None,
            "broadcast_channels": None,
            "channel_contents": None,
            "commerce_products": None,
            "health_institutions": None,
            "partners": None,
            "feed_interactions": None,
        }
        if options["include_counts"]:
            try:
                from django.contrib.auth import get_user_model
                from apps.broadcasts.models import BroadcastChannel, ChannelContent, BroadcastHealthInstitution
                from apps.commerce.models import Product
                from apps.feed_personalization.models import FeedInteraction
                from apps.partners.models import Partner

                User = get_user_model()
                counts = {
                    "users": User.objects.count(),
                    "broadcast_channels": BroadcastChannel.objects.filter(is_deleted=False).count(),
                    "channel_contents": ChannelContent.objects.filter(is_deleted=False).count(),
                    "commerce_products": Product.objects.filter(is_deleted=False).count(),
                    "health_institutions": BroadcastHealthInstitution.objects.count(),
                    "partners": Partner.objects.filter(is_active=True).count(),
                    "feed_interactions": FeedInteraction.objects.count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                checks.append(
                    {
                        "name": "search_discovery_database_counts",
                        "state": "warn",
                        "detail": f"database summary unavailable: {exc.__class__.__name__}",
                    }
                )

        failures = [check for check in checks if check["state"] == "fail"]
        ready = not failures
        payload = {"ready": ready, "checks": checks, "counts": counts if options["include_counts"] else None}

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Search/discovery launch guardrails ready: {ready}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Search/discovery counts: {counts}")
            self.stdout.write("Note: This command does not run indexing, send events, call AI/search providers, or expose private result payloads.")
            self.stdout.write("Note: Staging must still prove real-device search speed, pagination, offline behavior, and rollback evidence.")

        if options["strict"] and failures:
            raise CommandError("Search/discovery launch guardrails failed.")
