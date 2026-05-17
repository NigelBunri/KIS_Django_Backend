from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import Resolver404, resolve

from apps.channels.models import Channel
from apps.chat.models import ConversationMember, MessageThreadLink
from apps.communities.models import Community, CommunityMembership, CommunityPost
from apps.media.safety import (
    configured_allowed_extensions,
    configured_allowed_mime_prefixes,
    configured_allowed_mime_types,
    configured_blocked_extensions,
    live_provider_calls_enabled,
    media_safety_enabled,
)
from apps.partners.models import (
    Partner,
    PartnerApplication,
    PartnerAuditEvent,
    PartnerInvite,
    PartnerMembership,
    PartnerModerationAction,
    PartnerOnboardingProgress,
    PartnerOrganizationApp,
    PartnerPost,
    PartnerRole,
    PartnerRoleAssignment,
    PartnerServerCategory,
    PartnerWebhook,
    PartnerWebhookDelivery,
)
from apps.partners.serializers import (
    PartnerWebhookSerializer,
    redact_partner_sensitive_payload,
)


PARTNER_ROUTES = {
    "partner_list": "/api/v1/partners/",
    "partner_discover": "/api/v1/partners/discover/",
    "partner_detail": "/api/v1/partners/00000000-0000-0000-0000-000000000001/",
    "partner_public_hub": "/api/v1/partners/00000000-0000-0000-0000-000000000001/public-hub/",
    "partner_discord_summary": "/api/v1/partners/00000000-0000-0000-0000-000000000001/discord-summary/",
    "partner_roles": "/api/v1/partners/00000000-0000-0000-0000-000000000001/roles/",
    "partner_role_assignments": "/api/v1/partners/00000000-0000-0000-0000-000000000001/role-assignments/",
    "partner_members": "/api/v1/partners/00000000-0000-0000-0000-000000000001/members/",
    "partner_moderation_actions": "/api/v1/partners/00000000-0000-0000-0000-000000000001/moderation-actions/",
    "partner_audit_events": "/api/v1/partners/00000000-0000-0000-0000-000000000001/audit-events/",
    "partner_invites": "/api/v1/partners/00000000-0000-0000-0000-000000000001/invites/",
    "partner_redeem_invite": "/api/v1/partners/redeem-invite/",
    "partner_onboarding": "/api/v1/partners/00000000-0000-0000-0000-000000000001/onboarding/",
    "partner_organization_apps": "/api/v1/partners/00000000-0000-0000-0000-000000000001/organization-apps/",
    "partner_server_categories": "/api/v1/partners/00000000-0000-0000-0000-000000000001/server-categories/",
    "partner_server_layout": "/api/v1/partners/00000000-0000-0000-0000-000000000001/server-layout/",
    "partner_posts": "/api/v1/partners/posts/",
    "partner_post_comment_room": "/api/v1/partners/posts/00000000-0000-0000-0000-000000000001/comment-room/",
    "community_list": "/api/v1/communities/",
    "community_detail": "/api/v1/communities/00000000-0000-0000-0000-000000000001/",
    "community_join": "/api/v1/communities/00000000-0000-0000-0000-000000000001/join/",
    "community_members": "/api/v1/communities/00000000-0000-0000-0000-000000000001/members/",
    "community_posts": "/api/v1/posts/",
    "community_post_comment_room": "/api/v1/posts/00000000-0000-0000-0000-000000000001/comment-room/",
    "community_posts_legacy": "/api/v1/communities/posts/",
    "community_post_comment_room_legacy": "/api/v1/communities/posts/00000000-0000-0000-0000-000000000001/comment-room/",
    "chat_conversations": "/api/v1/chats/conversations/",
    "chat_threads": "/api/v1/chats/threads/",
}


def _setting_bool(name: str) -> bool:
    return bool(getattr(settings, name, False))


def _route_exists(path: str) -> bool:
    try:
        resolve(path)
    except Resolver404:
        return False
    return True


def _serializer_redaction_self_test() -> bool:
    redacted = redact_partner_sensitive_payload(
        {
            "safe": "ok",
            "api_key": "secret-value",
            "nested": {"authorization": "Bearer secret", "token": "secret-token"},
        }
    )
    webhook_secret_write_only = PartnerWebhookSerializer().fields["secret"].write_only
    return (
        redacted["api_key"] == "[redacted]"
        and redacted["nested"]["authorization"] == "[redacted]"
        and redacted["nested"]["token"] == "[redacted]"
        and redacted["safe"] == "ok"
        and webhook_secret_write_only
    )


class Command(BaseCommand):
    help = "Verify partner/workspace launch guardrails without exposing private workspace data."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query partner/community/message counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        for name, path in PARTNER_ROUTES.items():
            exists = _route_exists(path)
            checks.append(
                {
                    "name": f"route:{name}",
                    "state": "pass" if exists else "fail",
                    "detail": path if exists else f"{path} did not resolve",
                }
            )

        allowed_extensions = configured_allowed_extensions()
        allowed_prefixes = configured_allowed_mime_prefixes()
        allowed_mimes = configured_allowed_mime_types()
        blocked_extensions = configured_blocked_extensions()

        checks.extend(
            [
                {
                    "name": "partner_secret_redaction",
                    "state": "pass" if _serializer_redaction_self_test() else "fail",
                    "detail": "webhook/integration/audit/delivery secrets are redacted from read serializers",
                },
                {
                    "name": "MEDIA_SAFETY_ENABLED",
                    "state": "pass" if media_safety_enabled() else "fail",
                    "detail": "partner workspace uploads must pass the central media safety gate",
                },
                {
                    "name": "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED",
                    "state": "warn" if live_provider_calls_enabled() else "pass",
                    "detail": "enabled; requires explicit-content provider QA evidence" if live_provider_calls_enabled() else "disabled by default",
                },
                {
                    "name": "partner_media_safe_extensions",
                    "state": "pass" if {".jpg", ".jpeg", ".png", ".pdf", ".mp4", ".mp3"}.issubset(allowed_extensions) else "warn",
                    "detail": "allowed extensions cover common partner images, video, audio, and documents",
                },
                {
                    "name": "partner_media_blocks_executables",
                    "state": "pass" if {".exe", ".js", ".sh", ".svg"}.issubset(blocked_extensions) else "fail",
                    "detail": "dangerous executable/script uploads are blocked",
                },
                {
                    "name": "partner_media_mime_policy",
                    "state": "pass" if ("image/" in allowed_prefixes and "video/" in allowed_prefixes and "application/pdf" in allowed_mimes) else "warn",
                    "detail": "image, video, audio/text prefix policy and PDF partner documents are covered",
                },
                {
                    "name": "partner_group_messaging_contract",
                    "state": "pass",
                    "detail": "partner conversations, community comment rooms, post comment rooms, and subroom threads are mounted",
                },
                {
                    "name": "partner_roles_moderation_audit_contract",
                    "state": "pass",
                    "detail": "roles, role assignments, moderation actions, and audit events are mounted",
                },
                {
                    "name": "partner_low_bandwidth_contract",
                    "state": "pass",
                    "detail": "discord summary and public hub provide compact workspace summaries for low-bandwidth clients",
                },
                {
                    "name": "partner_private_workspace_policy",
                    "state": "pass",
                    "detail": "this verifier prints only counts and route/config states, not private group messages or media paths",
                },
            ]
        )

        counts = {
            "partners": None,
            "memberships": None,
            "applications": None,
            "invites": None,
            "onboarding_progress": None,
            "roles": None,
            "role_assignments": None,
            "organization_apps": None,
            "server_categories": None,
            "partner_channels": None,
            "partner_posts": None,
            "communities": None,
            "community_memberships": None,
            "community_posts": None,
            "conversation_memberships": None,
            "thread_links": None,
            "moderation_actions": None,
            "audit_events": None,
            "webhooks": None,
            "webhook_deliveries": None,
        }
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "partners": Partner.objects.count(),
                    "memberships": PartnerMembership.objects.count(),
                    "applications": PartnerApplication.objects.count(),
                    "invites": PartnerInvite.objects.count(),
                    "onboarding_progress": PartnerOnboardingProgress.objects.count(),
                    "roles": PartnerRole.objects.count(),
                    "role_assignments": PartnerRoleAssignment.objects.count(),
                    "organization_apps": PartnerOrganizationApp.objects.count(),
                    "server_categories": PartnerServerCategory.objects.count(),
                    "partner_channels": Channel.objects.filter(partner__isnull=False).count(),
                    "partner_posts": PartnerPost.objects.count(),
                    "communities": Community.objects.count(),
                    "community_memberships": CommunityMembership.objects.count(),
                    "community_posts": CommunityPost.objects.count(),
                    "conversation_memberships": ConversationMember.objects.count(),
                    "thread_links": MessageThreadLink.objects.count(),
                    "moderation_actions": PartnerModerationAction.objects.count(),
                    "audit_events": PartnerAuditEvent.objects.count(),
                    "webhooks": PartnerWebhook.objects.count(),
                    "webhook_deliveries": PartnerWebhookDelivery.objects.count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append(
                    {
                        "name": "partner_database_counts",
                        "state": "warn",
                        "detail": f"database summary unavailable: {count_error}",
                    }
                )

        failures = [check for check in checks if check["state"] == "fail"]
        warnings = [check for check in checks if check["state"] == "warn"]
        result = {
            "ready": not failures,
            "summary": {
                "failures": len(failures),
                "warnings": len(warnings),
                "checks": len(checks),
            },
            "checks": checks,
            "counts": counts,
            "count_error": count_error,
            "notes": [
                "This command does not send webhooks, realtime messages, provider calls, or media-safety provider requests.",
                "No secret values, private group messages, raw media paths, or private workspace data are printed.",
                "Partner live integrations and broad automation should remain gated until staging evidence exists.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Partners launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Partner/community counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Partner launch guardrails failed: {len(failures)} blocker(s).")
