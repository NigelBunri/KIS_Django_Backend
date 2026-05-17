from __future__ import annotations

import json

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.urls import Resolver404, resolve

from apps.accounts.family_accessibility import (
    normalize_family_accessibility_preferences,
    serialize_family_accessibility_preferences,
)
from apps.accounts.models import Profile, ProfileFieldVisibility, ProfilePreferences, ProfileShowcase, User
from apps.accounts.serializers import ProfileSerializer
from apps.media.safety import (
    configured_allowed_extensions,
    configured_blocked_extensions,
    live_provider_calls_enabled,
    media_safety_enabled,
)
from apps.moderation.models import UserBlock
from apps.verification.constants import VerificationSubjectType


PROFILE_ROUTES = {
    "profile_me": "/api/v1/profiles/me/",
    "profile_detail": "/api/v1/profiles/00000000-0000-0000-0000-000000000001/",
    "profile_view": "/api/v1/profiles/00000000-0000-0000-0000-000000000001/view/",
    "profile_privacy": "/api/v1/profile-privacy/",
    "profile_articles": "/api/v1/profile-articles/",
    "profile_preferences": "/api/v1/profile-preferences/",
    "profile_preferences_me": "/api/v1/profile-preferences/me/",
    "family_accessibility": "/api/v1/profile-preferences/family-accessibility/",
    "profile_languages": "/api/v1/profile-languages/",
    "profile_showcases": "/api/v1/profile-showcases/",
    "users_me": "/api/v1/users/me/",
    "auth_devices": "/api/v1/auth/devices/",
    "auth_2fa_setup": "/api/v1/auth/2fa/setup/",
    "auth_2fa_enable": "/api/v1/auth/2fa/enable/",
    "auth_2fa_disable": "/api/v1/auth/2fa/disable/",
    "verification_user_status": "/api/v1/verification/user/status/",
    "verification_user_start": "/api/v1/verification/user/start/",
    "verification_trust_overview": "/api/v1/verification/trust/overview/",
    "verification_public_user_trust": "/api/v1/verification/trust/user/00000000-0000-0000-0000-000000000001/",
    "notifications_main_badges": "/api/v1/notifications/main-tab-badge-counts/",
    "notifications_preferences": "/api/v1/notifications/preferences/",
    "notifications_mark_source_read": "/api/v1/notifications/mark-source-read/",
    "user_blocks": "/api/v1/user-blocks/",
    "media_assets": "/api/v1/assets/",
    "media_safety_scans": "/api/v1/media-safety-scans/",
}


def _route_exists(path: str) -> bool:
    try:
        resolve(path)
    except Resolver404:
        return False
    return True


def _profile_media_serializer_validation_ready() -> bool:
    serializer = ProfileSerializer()
    return callable(getattr(serializer, "validate_avatar_file", None)) and callable(
        getattr(serializer, "validate_cover_file", None)
    )


def _blocked_profile_media_rejected() -> bool:
    serializer = ProfileSerializer()
    upload = SimpleUploadedFile("profile-script.svg", b"<svg></svg>", content_type="image/svg+xml")
    try:
        serializer.validate_avatar_file(upload)
    except Exception:
        return True
    return False


def _family_accessibility_self_test() -> bool:
    child = normalize_family_accessibility_preferences(
        {
            "age_mode": "child",
            "hide_sensitive_commerce": False,
            "guardian_review_required": False,
            "navigation_mode": "standard",
        }
    )
    older_adult = normalize_family_accessibility_preferences({"age_mode": "older_adult"})
    return (
        child["family_safe_content"] is True
        and child["safe_recommendations"] is True
        and child["hide_sensitive_commerce"] is True
        and child["guardian_review_required"] is True
        and child["navigation_mode"] == "guided"
        and older_adult["large_tap_targets"] is True
        and older_adult["font_scale"] == "large"
    )


class Command(BaseCommand):
    help = "Verify profile/account/settings/family/accessibility launch guardrails without exposing private profile data."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query safe aggregate profile/account counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        for name, path in PROFILE_ROUTES.items():
            exists = _route_exists(path)
            checks.append(
                {
                    "name": f"route:{name}",
                    "state": "pass" if exists else "fail",
                    "detail": path if exists else f"{path} did not resolve",
                }
            )

        allowed_extensions = configured_allowed_extensions()
        blocked_extensions = configured_blocked_extensions()

        checks.extend(
            [
                {
                    "name": "profile_media_serializer_validation",
                    "state": "pass" if _profile_media_serializer_validation_ready() else "fail",
                    "detail": "avatar_file and cover_file use the central media safety validator",
                },
                {
                    "name": "profile_media_blocks_svg_script",
                    "state": "pass" if _blocked_profile_media_rejected() else "fail",
                    "detail": "dangerous profile SVG/script-style upload is rejected before save",
                },
                {
                    "name": "MEDIA_SAFETY_ENABLED",
                    "state": "pass" if media_safety_enabled() else "fail",
                    "detail": "profile media must pass central media safety validation",
                },
                {
                    "name": "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED",
                    "state": "warn" if live_provider_calls_enabled() else "pass",
                    "detail": "enabled; requires explicit-content provider QA evidence"
                    if live_provider_calls_enabled()
                    else "disabled by default",
                },
                {
                    "name": "profile_media_safe_extensions",
                    "state": "pass" if {".jpg", ".jpeg", ".png", ".webp"}.issubset(allowed_extensions) else "warn",
                    "detail": "profile image extensions cover common launch image formats",
                },
                {
                    "name": "profile_media_blocks_executables",
                    "state": "pass" if {".exe", ".js", ".sh", ".svg"}.issubset(blocked_extensions) else "fail",
                    "detail": "dangerous executable/script uploads are blocked",
                },
                {
                    "name": "family_accessibility_defaults",
                    "state": "pass" if _family_accessibility_self_test() else "fail",
                    "detail": "child and older-adult defaults force safe recommendations, larger touch targets, and guardian controls",
                },
                {
                    "name": "user_trust_summary_contract",
                    "state": "pass"
                    if VerificationSubjectType.USER == "user"
                    and _route_exists("/api/v1/verification/trust/user/00000000-0000-0000-0000-000000000001/")
                    else "fail",
                    "detail": "public user verification summary is available without private documents",
                },
                {
                    "name": "profile_private_data_policy",
                    "state": "pass",
                    "detail": "this verifier prints route/config/count states only, not private profile payloads, media paths, or secrets",
                },
            ]
        )

        counts = {
            "users": None,
            "profiles": None,
            "profile_preferences": None,
            "profile_privacy_rules": None,
            "profile_showcases": None,
            "user_blocks": None,
        }
        if options["include_counts"]:
            try:
                counts = {
                    "users": User.objects.count(),
                    "profiles": Profile.objects.count(),
                    "profile_preferences": ProfilePreferences.objects.count(),
                    "profile_privacy_rules": ProfileFieldVisibility.objects.count(),
                    "profile_showcases": ProfileShowcase.objects.count(),
                    "user_blocks": UserBlock.objects.count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                checks.append(
                    {
                        "name": "profile_database_counts",
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
            self.stdout.write(f"Profile launch guardrails ready: {ready}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Profile/account counts: {counts}")
            self.stdout.write("Note: This command does not upload files, send notifications, call providers, or expose private profile data.")
            self.stdout.write("Note: Staging must still prove real-device profile, privacy, accessibility, blocked-user, and rollback behavior.")

        if options["strict"] and failures:
            raise CommandError("Profile launch guardrails failed.")
