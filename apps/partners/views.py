# apps/partners/views.py
import csv
import json
import os
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.partners.models import (
    Partner,
    PartnerPost,
    PartnerPostStatus,
    PartnerPostComment,
    PartnerPostReaction,
    PartnerFeatureFlag,
    PartnerJoinConfig,
    PartnerMembership,
    PartnerMembershipStatus,
    PartnerApplication,
    PartnerInvite,
    PartnerJobPost,
    PartnerApplicationStatus,
    PartnerOnboardingProgress,
    PartnerPolicy,
    PartnerRole,
    PartnerRoleAssignment,
    PartnerAuditEvent,
    PartnerIntegration,
    PartnerWebhook,
    PartnerWebhookDelivery,
    PartnerAutomationRule,
    PartnerReportSnapshot,
    PartnerExportJob,
    PartnerExportStatus,
    PartnerAccessRequest,
    PartnerAccessRequestStatus,
    PartnerAccessReview,
    PartnerAccessReviewStatus,
    PartnerExportSchedule,
    PartnerExportScheduleFrequency,
    PartnerSetting,
    PartnerOrganizationProfile,
    PartnerOrganizationApp,
    PartnerOrganizationAppStatus,
    PartnerOrganizationAppTab,
    PartnerOrganizationAppContentBlock,
    PartnerOrganizationAppType,
    PartnerOrganizationAppAccessLog,
    PartnerProfileLink,
    PartnerServerCategory,
    PartnerModerationAction,
    PartnerOrganizationLink,
    PartnerOrganizationLinkType,
    PartnerDepartment,
    PartnerDepartmentMembership,
    PartnerDepartmentNote,
    PartnerLocation,
    PartnerResource,
    PartnerCalendarEvent,
    PartnerCalendarEventVisibility,
    PartnerCalendarRsvp,
    PartnerCalendarRsvpStatus,
    SupportTicket,
    SupportTicketStatus,
    SupportTicketReply,
    PartnerPostTemplate,
    PartnerSurvey,
    PartnerSurveyStatus,
    PartnerSurveyQuestion,
    PartnerSurveyQuestionType,
    PartnerSurveyResponse,
    PartnerSurveyAnswer,
    PartnerBudget,
    PartnerBudgetExpense,
    PartnerVolunteerShift,
    PartnerVolunteerSignup,
    PartnerVolunteerSignupStatus,
    PartnerDonation,
)
from apps.feed_personalization import (
    get_affinity_profile,
    log_feed_interaction,
    rank_feed_items,
    resolve_personalization_sample_limit,
)
from apps.partners.settings_catalog import PARTNER_SETTINGS_SECTIONS
from apps.chat.models import (
    BaseConversationRole,
    Conversation,
    ConversationMember,
    ConversationNotificationLevel,
    ConversationSettings,
    ConversationType,
    ConversationSendPolicy,
    ConversationJoinPolicy as ChatConversationJoinPolicy,
)
from apps.chat.discussion import ensure_conversation_member, ensure_post_comment_conversation
from apps.partners.serializers import (
    PartnerListSerializer,
    PartnerDetailSerializer,
    PartnerCreateSerializer,
    PartnerDiscoverSerializer,
    PartnerApplicationSerializer,
    PartnerApplicationDetailSerializer,
    PartnerInviteSerializer,
    PartnerJobPostSerializer,
    PartnerOnboardingProgressSerializer,
    PartnerOrganizationAppAccessLogSerializer,
    PartnerOrganizationAppContentBlockSerializer,
    PartnerOrganizationAppTabSerializer,
    PartnerPostSerializer,
    PartnerPostCreateSerializer,
    PartnerPostCommentSerializer,
    PartnerPolicySerializer,
    PartnerRoleSerializer,
    PartnerRoleAssignmentSerializer,
    PartnerDepartmentSerializer,
    PartnerDepartmentMemberSerializer,
    PartnerDepartmentNoteSerializer,
    PartnerLocationSerializer,
    PartnerResourceSerializer,
    PartnerCalendarEventSerializer,
    PartnerCalendarRsvpSerializer,
    SupportTicketSerializer,
    SupportTicketReplySerializer,
    PartnerPostTemplateSerializer,
    PartnerSurveySerializer,
    PartnerSurveyQuestionSerializer,
    PartnerSurveyResponseSerializer,
    PartnerBudgetSerializer,
    PartnerBudgetExpenseSerializer,
    PartnerVolunteerShiftSerializer,
    PartnerVolunteerSignupSerializer,
    PartnerDonationSerializer,
    PartnerAuditEventSerializer,
    PartnerIntegrationSerializer,
    PartnerWebhookSerializer,
    PartnerWebhookDeliverySerializer,
    PartnerAutomationRuleSerializer,
    PartnerReportSnapshotSerializer,
    PartnerExportJobSerializer,
    PartnerAccessRequestSerializer,
    PartnerAccessReviewSerializer,
    PartnerExportScheduleSerializer,
    PartnerSettingSerializer,
    PartnerOrganizationProfileSerializer,
    PartnerOrganizationAppSerializer,
    PartnerProfileLinkSerializer,
    PartnerServerCategorySerializer,
    PartnerModerationActionSerializer,
    PartnerMemberDirectoryEntrySerializer,
)
from apps.partners.seed import KCAN_PARTNER_SLUG, LEGACY_DEFAULT_PARTNER_SLUGS
from apps.partners.permissions import is_kcan_admin, IsKCANAdmin, PartnerScopedPermission
from apps.moderation.models import UserBlock
from apps.accounts.feature_gate import require_feature
from .tiers import require_partner_feature, get_partner_tier_features
from apps.accounts.tiers import get_user_tier_features, normalize_limit_value
from apps.partners.services import (
    ensure_partner_policy,
    apply_partner_policy,
    ensure_default_organization_app,
    log_organization_app_access,
    partner_user_can_access,
    partner_user_can_manage,
    get_partner_user_roles,
    user_has_partner_permission,
    log_partner_audit,
    evaluate_partner_dlp,
    dispatch_partner_webhooks,
    run_partner_automation_rules,
    build_partner_summary,
    next_export_run_at,
    retry_partner_webhook_delivery,
    deactivate_partner_profile,
    reactivate_partner_profile,
    get_partner_organization_apps_for_user,
    filter_partner_channels_for_user,
    build_partner_discord_summary,
    active_partner_member_ids,
    notify_nest_of_partner_event,
)
from apps.verification.constants import VerificationSubjectType
from apps.verification.models import VerificationCase
from apps.verification.serializers import PartnerVerificationReviewSerializer, PartnerVerificationStartSerializer
from apps.verification.services import (
    current_partner_verification_status,
    review_partner_case,
    serialize_case_status,
    start_partner_verification_case,
)

import logging

logger = logging.getLogger(__name__)

LANDING_PAGE_BUILDER_KEY = "landing_page_builder"
LANDING_BUILDER_KEYS = {
    "sections",
    "section_groups",
    "sectionGroups",
    "landingBackgroundImageUrl",
    "landingBackgroundColorKey",
    "landingLogoUrl",
    "landing_style",
    "landingStyle",
}
LANDING_BUILDER_CONTAINER_KEYS = (
    "landing_page_builder",
    "landingPageBuilder",
    "profile_editor",
    "profileEditor",
    "landing_preview",
    "landingPreview",
)


def _safe_json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, dict):
        safe_obj = {}
        for key, val in value.items():
            if not isinstance(key, str):
                continue
            safe_obj[key] = _safe_json_value(val)
        return safe_obj
    return str(value)


def _collect_landing_builder_candidate(source):
    if not isinstance(source, dict):
        return {}
    candidate = {}
    for key in LANDING_BUILDER_KEYS:
        if key in source:
            candidate[key] = source.get(key)
    for container_key in LANDING_BUILDER_CONTAINER_KEYS:
        nested = source.get(container_key)
        if not isinstance(nested, dict):
            continue
        for key, value in nested.items():
            if key not in candidate:
                candidate[key] = value
    return candidate


def _normalize_landing_builder_config(raw_config):
    candidate = _collect_landing_builder_candidate(raw_config)
    if not candidate:
        return {}

    sections = candidate.get("sections")
    if not isinstance(sections, list):
        sections = []

    section_groups = candidate.get("section_groups")
    if not isinstance(section_groups, list):
        section_groups = candidate.get("sectionGroups")
    if not isinstance(section_groups, list):
        section_groups = []

    normalized = {"sections": _safe_json_value(sections)}
    if section_groups:
        normalized["section_groups"] = _safe_json_value(section_groups)
        normalized["sectionGroups"] = normalized["section_groups"]

    landing_background_image = str(candidate.get("landingBackgroundImageUrl") or "").strip()
    landing_background_color = str(candidate.get("landingBackgroundColorKey") or "").strip()
    landing_logo = str(candidate.get("landingLogoUrl") or "").strip()
    if landing_background_image:
        normalized["landingBackgroundImageUrl"] = landing_background_image
    if landing_background_color:
        normalized["landingBackgroundColorKey"] = landing_background_color
    if landing_logo:
        normalized["landingLogoUrl"] = landing_logo

    if "landing_style" in candidate:
        normalized["landing_style"] = _safe_json_value(candidate.get("landing_style"))
    if "landingStyle" in candidate:
        normalized["landingStyle"] = _safe_json_value(candidate.get("landingStyle"))
    return normalized


def _linkable_models() -> dict:
    """Called fresh each time (avoids import-order coupling with
    commerce/health_ops/broadcasts) — one entry per
    PartnerOrganizationLinkType."""
    from django.apps import apps as django_apps

    return {
        PartnerOrganizationLinkType.SHOP: django_apps.get_model("commerce", "Shop"),
        PartnerOrganizationLinkType.HEALTH_INSTITUTION: django_apps.get_model("health_ops", "HealthInstitution"),
        PartnerOrganizationLinkType.EDUCATION_INSTITUTION: django_apps.get_model("broadcasts", "EducationInstitution"),
        PartnerOrganizationLinkType.BROADCAST_CHANNEL: django_apps.get_model("broadcasts", "BroadcastChannel"),
    }


def _organization_display_name(owner_type: str, org) -> str:
    # BroadcastChannel has no `name` field — it's `display_name`. Every
    # other linkable model (Shop, HealthInstitution, EducationInstitution)
    # uses plain `name`.
    if owner_type == PartnerOrganizationLinkType.BROADCAST_CHANNEL:
        return getattr(org, "display_name", "") or ""
    return getattr(org, "name", "") or ""


def _resolve_linkable_organization(owner_type: str, owner_id: str, user):
    model = _linkable_models().get(owner_type)
    if model is None:
        return None
    org = model.objects.filter(pk=owner_id).first()
    if org is None:
        return None
    owner_field = "owner_user" if owner_type == PartnerOrganizationLinkType.BROADCAST_CHANNEL else "owner"
    if getattr(org, f"{owner_field}_id", None) != user.id:
        return None
    return org


def _serialize_organization_link(link: "PartnerOrganizationLink") -> dict:
    model = _linkable_models().get(link.owner_type)
    org = model.objects.filter(pk=link.owner_id).first() if model else None
    return {
        "id": str(link.id),
        "owner_type": link.owner_type,
        "owner_id": str(link.owner_id),
        "name": _organization_display_name(link.owner_type, org) if org else "",
        "exists": org is not None,
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


class PartnerViewSet(viewsets.ModelViewSet):
    """
    /api/v1/partners/partners/

    - list:       GET     /api/v1/partners/partners/
    - create:     POST    /api/v1/partners/partners/
    - retrieve:   GET     /api/v1/partners/partners/{id}/
    - update:     PUT/PATCH /api/v1/partners/partners/{id}/
    - deactivate: POST    /api/v1/partners/partners/{id}/deactivate/
    """
    permission_classes = [IsAuthenticated]
    queryset = Partner.objects.select_related("owner", "main_conversation")
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action == "global_organization_apps":
            return [AllowAny()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == "list":
            return PartnerListSerializer
        if self.action == "create":
            return PartnerCreateSerializer
        if self.action == "discover":
            return PartnerDiscoverSerializer
        if self.action == "apply":
            return PartnerApplicationSerializer
        return PartnerDetailSerializer

    def get_queryset(self):
        """
        Default visibility:
        - Return partners where the user is the owner, OR
        - the user has an active PartnerMembership, OR
        - the user is a legacy member of the partner's main conversation.

        Public join flows need access to the partner object before a membership exists.
        """
        user = self.request.user

        base_qs = Partner.objects.select_related("owner", "main_conversation")
        if user.is_staff:
            return base_qs
        if self.action in {"apply", "subscribe"}:
            return base_qs.filter(is_active=True)

        return (
            base_qs
            .filter(
                models.Q(owner=user)
                | models.Q(
                    memberships__user=user,
                    memberships__status__in=(
                        PartnerMembershipStatus.MEMBER,
                        PartnerMembershipStatus.SUBSCRIBER,
                    ),
                    memberships__is_banned=False,
                )
                | models.Q(
                    main_conversation__memberships__user=user,
                    main_conversation__memberships__left_at__isnull=True,
                )
            )
            .distinct()
        )

    def create(self, request, *args, **kwargs):
        require_feature(request.user, "partner_accounts")
        features = get_user_tier_features(request.user)
        allowed = normalize_limit_value(features.get("partner_accounts"), default=0)
        if allowed is not None and allowed > 0:
            existing = Partner.objects.filter(owner=request.user).count()
            if existing >= allowed:
                raise ValidationError(
                    {
                        "detail": "You have reached your partner allocation for this tier. Upgrade to Partner Pro for unlimited partner accounts."
                    }
                )
        return super().create(request, *args, **kwargs)

    def _user_can_access_partner(self, partner: Partner, user) -> bool:
        return partner_user_can_access(partner, user)

    def _member_role(self, partner: Partner, user):
        if not partner.main_conversation_id:
            return None
        member = partner.main_conversation.memberships.filter(
            user=user,
            left_at__isnull=True,
        ).first()
        return member.base_role if member else None

    def _ui_role(self, partner: Partner, user) -> str:
        roles = get_partner_user_roles(partner, user)
        for role in ("owner", "admin", "manager", "analyst", "viewer", "member"):
            if role in roles:
                return role
        return "member"

    def _user_can_manage_partner(self, partner: Partner, user) -> bool:
        return partner_user_can_manage(partner, user)

    def _user_partner_roles(self, partner: Partner, user) -> set[str]:
        return get_partner_user_roles(partner, user)

    def _user_can_manage_organization_apps(self, partner: Partner, user) -> bool:
        return partner_user_can_manage(partner, user)

    def _user_can_promote_global_apps(self, partner: Partner, user) -> bool:
        return bool(
            user
            and user.is_authenticated
            and partner.slug == KCAN_PARTNER_SLUG
            and (user.is_staff or self._user_can_manage_organization_apps(partner, user))
        )

    def _require_permission(self, partner: Partner, user, codename: str) -> None:
        if self._user_can_manage_partner(partner, user):
            return
        if user_has_partner_permission(partner, user, codename):
            return
        raise PermissionDenied("You do not have permission to perform this action.")

    def _require_partner_feature(self, partner: Partner, key: str, message: str | None = None) -> None:
        # Gated by the ORGANIZATION's own workspace-level plan, not
        # whichever staff member happens to be making this request — see
        # PartnerSubscription's docstring for why this used to be
        # request.user's personal tier and why that was wrong.
        require_partner_feature(partner, key, message)

    def _ensure_conversation_member(self, partner: Partner, user, base_role: str) -> None:
        if not partner.main_conversation_id:
            return
        ConversationMember.objects.update_or_create(
            conversation_id=partner.main_conversation_id,
            user=user,
            defaults={"base_role": base_role, "left_at": None},
        )

    def _auto_assign_membership(self, partner: Partner, user, config: dict | None):
        if not config:
            return
        communities = config.get("communities") or []
        groups = config.get("groups") or []
        channels = config.get("channels") or []
        from apps.communities.models import CommunityMembership, CommunityRole
        from apps.groups.models import GroupMembership, GroupRole
        from apps.channels.models import Channel

        for community_id in communities:
            CommunityMembership.objects.update_or_create(
                community_id=community_id,
                user=user,
                defaults={"role": CommunityRole.MEMBER, "left_at": None, "is_banned": False},
            )

        for group_id in groups:
            GroupMembership.objects.update_or_create(
                group_id=group_id,
                user=user,
                defaults={"role": GroupRole.MEMBER},
            )

        for channel_id in channels:
            channel = Channel.objects.filter(id=channel_id).select_related("conversation").first()
            if channel:
                ConversationMember.objects.get_or_create(
                    conversation=channel.conversation,
                    user=user,
                    defaults={"base_role": BaseConversationRole.MEMBER},
                )

    def _get_membership(self, partner: Partner, user) -> PartnerMembership | None:
        if not user or user.is_anonymous:
            return None
        return PartnerMembership.objects.filter(partner=partner, user=user).first()

    def _activate_partner_membership(
        self,
        *,
        partner: Partner,
        user,
        status_value: str,
        membership_role: str,
    ) -> PartnerMembership:
        membership, _ = PartnerMembership.objects.update_or_create(
            partner=partner,
            user=user,
            defaults={
                "status": status_value,
                "role": membership_role,
                "is_banned": False,
                "banned_at": None,
                "removed_at": None,
            },
        )
        base_role = BaseConversationRole.MEMBER
        if status_value == PartnerMembershipStatus.SUBSCRIBER or membership_role == "subscriber":
            base_role = BaseConversationRole.READONLY
        self._ensure_conversation_member(partner, user, base_role)
        return membership

    def _partner_onboarding_config(self, partner: Partner) -> dict:
        config = getattr(partner, "join_config", None)
        criteria = getattr(config, "criteria", None) or {}
        role_options = criteria.get("role_options")
        roles = []
        if isinstance(role_options, list) and role_options:
            allowed_ids = {str(item) for item in role_options}
            allowed_names = {str(item).strip().lower() for item in role_options}
            for role in partner.roles.order_by("name"):
                if str(role.id) in allowed_ids or role.name.strip().lower() in allowed_names:
                    roles.append({"id": str(role.id), "name": role.name, "description": role.description})

        default_channels = criteria.get("default_channels")
        if not isinstance(default_channels, list):
            default_channels = []

        return {
            "rules_text": criteria.get("rules_text") or criteria.get("rules") or "",
            "welcome_message": criteria.get("welcome_message") or "",
            "default_channel_ids": [str(channel_id) for channel_id in default_channels if channel_id],
            "role_options": roles,
        }

    def _upsert_onboarding_progress(
        self,
        *,
        partner: Partner,
        user,
        invite: PartnerInvite | None = None,
        defaults: dict | None = None,
    ) -> PartnerOnboardingProgress:
        payload = defaults or {}
        progress, _ = PartnerOnboardingProgress.objects.get_or_create(
            partner=partner,
            user=user,
            defaults={
                "invite": invite,
                "selected_role_ids": payload.get("selected_role_ids", []),
                "selected_channel_ids": payload.get("selected_channel_ids", []),
                "onboarding_snapshot": payload.get("onboarding_snapshot", self._partner_onboarding_config(partner)),
            },
        )
        changed_fields: list[str] = []
        if invite and progress.invite_id != invite.id:
            progress.invite = invite
            changed_fields.append("invite")
        if payload:
            for field_name in ("selected_role_ids", "selected_channel_ids", "onboarding_snapshot"):
                if field_name in payload:
                    setattr(progress, field_name, payload[field_name])
                    changed_fields.append(field_name)
        if changed_fields:
            changed_fields.append("updated_at")
            progress.save(update_fields=changed_fields)
        return progress

    def _member_directory_queryset(self, partner: Partner):
        """Un-evaluated queryset — callers MUST slice/paginate this before
        iterating (see members() action) rather than materializing it in
        full. A partner with thousands of members loading every row (plus
        every PartnerRoleAssignment org-wide) on every directory request —
        or, worse, on every single member-role PATCH, see
        _member_directory_entry_for_user below — is exactly the kind of
        "works today, falls over once real orgs are big" bug this was."""
        return (
            PartnerMembership.objects.filter(partner=partner)
            .select_related("user", "user__profile")
            .order_by("role", "created_at")
        )

    def _role_names_for_users(self, partner: Partner, user_ids) -> dict:
        role_lookup: dict[int, set[str]] = {}
        assignments = (
            PartnerRoleAssignment.objects.filter(partner=partner, user_id__in=list(user_ids))
            .select_related("role")
            .order_by("role__name")
        )
        for assignment in assignments:
            role_lookup.setdefault(assignment.user_id, set()).add(assignment.role.name)
        return role_lookup

    def _member_directory_entry(self, membership: "PartnerMembership", role_lookup: dict) -> dict:
        user = membership.user
        profile = getattr(user, "profile", None)
        return {
            "user_id": str(user.id),
            "display_name": getattr(user, "display_name", None),
            "username": getattr(user, "username", None),
            "avatar_url": getattr(profile, "avatar_url", None) if profile else None,
            "membership_status": membership.status,
            "membership_role": membership.role,
            "role_names": sorted(role_lookup.get(user.id, set())),
            "is_muted": membership.is_muted,
            "is_banned": membership.is_banned,
            "timed_out_until": membership.timed_out_until,
            "joined_at": membership.created_at,
        }

    def _member_directory_entries(self, partner: Partner, memberships) -> list:
        """`memberships` must already be a bounded slice (a paginated page,
        or a small explicit list) — role assignments are looked up only for
        the users actually present in it, never org-wide."""
        memberships = list(memberships)
        role_lookup = self._role_names_for_users(partner, (m.user_id for m in memberships))
        return [self._member_directory_entry(m, role_lookup) for m in memberships]

    def _member_directory_entry_for_user(self, partner: Partner, user_id) -> dict | None:
        membership = (
            PartnerMembership.objects.filter(partner=partner, user_id=user_id)
            .select_related("user", "user__profile")
            .first()
        )
        if not membership:
            return None
        role_lookup = self._role_names_for_users(partner, [user_id])
        return self._member_directory_entry(membership, role_lookup)

    def _landing_builder_config(self, partner: Partner) -> dict:
        setting = PartnerSetting.objects.filter(partner=partner, key=LANDING_PAGE_BUILDER_KEY).first()
        if not setting:
            return {}
        return _normalize_landing_builder_config(setting.config or {})

    def _public_hub_payload(self, partner: Partner) -> dict:
        profile, _ = PartnerOrganizationProfile.objects.get_or_create(
            partner=partner,
            defaults={"display_name": partner.name},
        )
        apps = get_partner_organization_apps_for_user(partner, self.request.user)
        active_member_count = PartnerMembership.objects.filter(
            partner=partner,
            status__in=(PartnerMembershipStatus.MEMBER, PartnerMembershipStatus.SUBSCRIBER),
            is_banned=False,
        ).count()
        return {
            "partner_id": str(partner.id),
            "name": partner.name,
            "slug": partner.slug,
            "description": partner.description,
            "avatar_url": partner.avatar_url,
            "profile": PartnerOrganizationProfileSerializer(profile).data,
            "landing_builder": self._landing_builder_config(partner),
            "public_metrics": {
                "active_members": active_member_count,
                "channels": partner.channels.filter(is_archived=False).count(),
                "apps": len(apps),
                "categories": partner.server_categories.count(),
            },
            "apps": [
                {
                    "id": str(app.id),
                    "name": app.name,
                    "type": app.type,
                    "description": app.description,
                    "module": app.module,
                    "badge_label": app.badge_label,
                    "link": app.link,
                }
                for app in apps
            ],
        }

    def _experience_templates_payload(self, partner: Partner) -> list[dict]:
        profile = getattr(partner, "organization_profile", None)
        industry = (getattr(profile, "industry", "") or "").strip().lower()
        apps = list(partner.organization_apps.filter(is_active=True))
        active_types = {app.type for app in apps}

        templates = [
            {
                "key": "community-campus",
                "title": "Community Campus",
                "description": "Learning-first partner experience with onboarding, cohorts, and education apps.",
                "fits": bool("education" in active_types or "school" in industry or "education" in industry),
                "accent": "emerald",
            },
            {
                "key": "ministry-broadcast",
                "title": "Ministry Broadcast Hub",
                "description": "Broadcast-led partner experience with announcements, follow-up, and events.",
                "fits": bool("church" in industry or "ministry" in industry or "broadcast" in active_types),
                "accent": "amber",
            },
            {
                "key": "care-network",
                "title": "Care Network",
                "description": "Health and support oriented server with protected teams and intake routing.",
                "fits": bool("health" in active_types or "care" in industry or "health" in industry),
                "accent": "cyan",
            },
            {
                "key": "commerce-club",
                "title": "Commerce Club",
                "description": "Commerce-oriented server with storefront, partner teams, and sales automation.",
                "fits": bool("commerce" in active_types or "retail" in industry or "commerce" in industry),
                "accent": "rose",
            },
        ]
        return templates

    def _team_structure_payload(self, partner: Partner) -> dict:
        memberships = (
            PartnerMembership.objects.filter(partner=partner)
            .select_related("user")
            .order_by("role", "created_at")
        )
        buckets: dict[str, list[dict]] = {}
        role_assignments = (
            PartnerRoleAssignment.objects.filter(partner=partner)
            .select_related("role", "user")
            .order_by("role__name")
        )
        assignment_lookup: dict[int, list[str]] = {}
        for assignment in role_assignments:
            assignment_lookup.setdefault(assignment.user_id, []).append(assignment.role.name)

        for membership in memberships:
            lane = membership.role or "member"
            buckets.setdefault(lane, []).append(
                {
                    "user_id": str(membership.user_id),
                    "display_name": getattr(membership.user, "display_name", None) or getattr(membership.user, "username", None),
                    "status": membership.status,
                    "role_assignments": assignment_lookup.get(membership.user_id, []),
                    "is_banned": membership.is_banned,
                }
            )

        return {
            "owner_id": str(partner.owner_id),
            "lanes": [{"key": key, "members": value} for key, value in buckets.items()],
        }

    def _differentiator_insights_payload(self, partner: Partner) -> dict:
        memberships = PartnerMembership.objects.filter(partner=partner)
        active_members = memberships.filter(
            status__in=(PartnerMembershipStatus.MEMBER, PartnerMembershipStatus.SUBSCRIBER),
            is_banned=False,
        )
        onboarding_total = PartnerOnboardingProgress.objects.filter(partner=partner).count()
        onboarding_completed = PartnerOnboardingProgress.objects.filter(partner=partner, completed_at__isnull=False).count()
        role_health = (
            memberships.values("role")
            .annotate(count=models.Count("id"))
            .order_by("-count", "role")
        )
        app_access = (
            PartnerOrganizationAppAccessLog.objects.filter(app__partner=partner)
            .values("app__name")
            .annotate(count=models.Count("id"))
            .order_by("-count", "app__name")[:5]
        )
        active_member_ids = set(active_members.values_list("user_id", flat=True))
        assigned_member_ids = set(
            PartnerRoleAssignment.objects.filter(partner=partner).values_list("user_id", flat=True)
        )

        return {
            "onboarding_funnel": {
                "started": onboarding_total,
                "completed": onboarding_completed,
                "completion_rate": round((onboarding_completed / onboarding_total) * 100, 1) if onboarding_total else 0,
            },
            "team_activation": {
                "active_members": len(active_member_ids),
                "assigned_members": len(assigned_member_ids),
                "assignment_rate": round((len(assigned_member_ids & active_member_ids) / len(active_member_ids)) * 100, 1)
                if active_member_ids
                else 0,
            },
            "role_health": [{"role": item["role"], "count": item["count"]} for item in role_health],
            "app_adoption": [{"app": item["app__name"], "count": item["count"]} for item in app_access],
        }

    def _automation_recipe_payload(self, partner: Partner) -> list[dict]:
        active_apps = {app.type for app in partner.organization_apps.filter(is_active=True)}
        return [
            {
                "key": "onboarding-followup",
                "title": "Onboarding Follow-up",
                "description": "Trigger a follow-up when onboarding is started but not completed.",
                "trigger": "member.joined",
                "conditions": {"onboarding_completed": False},
                "actions": [{"type": "notify_admins"}, {"type": "tag_member", "value": "needs_onboarding"}],
                "fits": True,
            },
            {
                "key": "education-activation",
                "title": "Education Activation",
                "description": "Route new members into education cohorts and starter channels.",
                "trigger": "member.joined",
                "conditions": {"app_type": "education"},
                "actions": [{"type": "assign_app_module", "value": "education"}, {"type": "subscribe_default_channels"}],
                "fits": "education" in active_apps,
            },
            {
                "key": "broadcast-escalation",
                "title": "Broadcast Escalation",
                "description": "Escalate high-value announcement interactions to the operations team.",
                "trigger": "post.broadcast",
                "conditions": {"engagement_threshold": 25},
                "actions": [{"type": "notify_role", "value": "Manager"}, {"type": "snapshot_report", "value": "engagement"}],
                "fits": "broadcast" in active_apps,
            },
        ]

    def _validate_profile_key(self, profile_key: str) -> str:
        valid_keys = {choice[0] for choice in PartnerProfileLink.PROFILE_CHOICES}
        if profile_key not in valid_keys:
            raise ValidationError({"profile_key": "Invalid profile key."})
        return profile_key

    def _ensure_profile_links(self, partner: Partner):
        existing = {link.profile_key: link for link in partner.profile_links.all()}
        for profile_key, _label in PartnerProfileLink.PROFILE_CHOICES:
            if profile_key not in existing:
                existing[profile_key] = PartnerProfileLink.objects.create(
                    partner=partner,
                    profile_key=profile_key,
                )
        return [existing[key] for key, _ in PartnerProfileLink.PROFILE_CHOICES]

    def _get_profile_link(self, partner: Partner, profile_key: str) -> PartnerProfileLink:
        profile_key = self._validate_profile_key(profile_key)
        link, _created = PartnerProfileLink.objects.get_or_create(
            partner=partner,
            profile_key=profile_key,
        )
        return link

    @action(detail=False, methods=["get"], url_path="discover")
    def discover(self, request):
        """
        Discover all partner accounts with join options.
        """
        qs = Partner.objects.select_related("owner", "main_conversation").filter(
            is_active=True,
        ).filter(
            models.Q(join_config__allow_public_listing=True)
            | models.Q(join_config__isnull=True)
        )

        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                models.Q(name__icontains=q)
                | models.Q(description__icontains=q)
                | models.Q(slug__icontains=q)
            )

        method = (request.query_params.get("method") or "").strip().lower()
        if method:
            qs = qs.filter(join_config__methods__contains=[method])

        open_only = (request.query_params.get("open") or "").strip().lower()
        if open_only in ("1", "true", "yes"):
            qs = qs.filter(join_config__auto_approve=True)

        return Response(
            PartnerDiscoverSerializer(qs.order_by("name"), many=True, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    def perform_create(self, serializer):
        # Uses PartnerCreateSerializer.create(), which handles conversation creation
        serializer.save()

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        """
        Soft-deactivate a partner.

        Later you can add RBAC (e.g. only owner or global admin).
        """
        partner = self.get_object()
        if partner.owner != request.user:
            return Response(
                {"detail": "Only the partner owner can deactivate this partner (for now)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        deactivate_partner_profile(partner, by_user=request.user, source=Partner.DeactivationSource.USER)

        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.deactivate",
            target_type="partner",
            target_id=str(partner.id),
            request=request,
        )

        return Response(
            {"detail": "Partner deactivated."},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="reactivate")
    def reactivate(self, request, pk=None):
        partner = self.get_object()
        if partner.owner != request.user:
            return Response({"detail": "Only the owner can reactivate this partner."}, status=status.HTTP_403_FORBIDDEN)
        if partner.is_active:
            return Response({"detail": "Partner is already active."}, status=status.HTTP_400_BAD_REQUEST)
        if partner.deactivation_source == Partner.DeactivationSource.SYSTEM:
            return Response(
                {"detail": "This profile cannot be reactivated manually."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reactivate_partner_profile(partner)
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.reactivate",
            target_type="partner",
            target_id=str(partner.id),
            request=request,
        )
        return Response({"detail": "Partner reactivated."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="remove")
    def delete(self, request, pk=None):
        partner = self.get_object()
        if partner.owner != request.user:
            return Response({"detail": "Only the owner can delete this partner."}, status=status.HTTP_403_FORBIDDEN)
        partner.delete()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.delete",
            target_type="partner",
            target_id=str(partner.id),
            request=request,
        )
        return Response({"detail": "Partner deleted."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "put"], url_path="policy")
    def policy(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.policy.edit")
        policy = ensure_partner_policy(partner)
        if request.method == "GET":
            return Response(
                PartnerPolicySerializer(policy).data,
                status=status.HTTP_200_OK,
            )
        serializer = PartnerPolicySerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        apply_partner_policy(partner)
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.policy.update",
            target_type="partner_policy",
            target_id=str(policy.id),
            metadata={"keys": list((request.data or {}).keys())},
            request=request,
        )
        dispatch_partner_webhooks(
            partner=partner,
            event="policy.updated",
            payload={
                "policy_id": str(policy.id),
                "actor_id": str(request.user.id),
            },
        )
        run_partner_automation_rules(
            partner=partner,
            event="policy.updated",
            payload={"policy_id": str(policy.id), "actor_id": str(request.user.id)},
            actor=request.user,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="audit-events")
    def audit_events(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.audit.view")
        qs = PartnerAuditEvent.objects.filter(partner=partner).order_by("-created_at")
        limit = int(request.query_params.get("limit") or 100)
        qs = qs[: min(limit, 500)]
        return Response(
            PartnerAuditEventSerializer(qs, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "post"], url_path="roles")
    def roles(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.roles.manage")
            serializer = PartnerRoleSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(partner=partner)
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.role.create",
                target_type="partner_role",
                target_id=str(serializer.instance.id),
                metadata={"name": serializer.instance.name},
                request=request,
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        self._require_permission(partner, request.user, "partner.roles.view")
        qs = PartnerRole.objects.filter(partner=partner).order_by("name")
        return Response(PartnerRoleSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch", "delete"], url_path=r"roles/(?P<role_id>[^/.]+)")
    def role_detail(self, request, pk=None, role_id=None):
        """create/list on roles() above had no update/delete counterpart —
        once a custom role existed there was no way to rename it, change
        its permissions, or remove it without going through Django admin."""
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.roles.manage")
        role = PartnerRole.objects.filter(id=role_id, partner=partner).first()
        if not role:
            return Response({"detail": "Role not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "DELETE":
            if PartnerRoleAssignment.objects.filter(role=role).exists():
                return Response(
                    {"detail": "Unassign this role from all members before deleting it."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            role_name = role.name
            role.delete()
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.role.delete",
                target_type="partner_role",
                target_id=str(role_id),
                request=request,
            )
            notify_nest_of_partner_event(
                partner_id=str(partner.id),
                event="partner.role_deleted",
                user_ids=active_partner_member_ids(partner),
                data={"roleId": str(role_id), "roleName": role_name},
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = PartnerRoleSerializer(role, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.role.update",
            target_type="partner_role",
            target_id=str(role.id),
            metadata={"fields": list((request.data or {}).keys())},
            request=request,
        )
        notify_nest_of_partner_event(
            partner_id=str(partner.id),
            event="partner.role_updated",
            user_ids=active_partner_member_ids(partner),
            data={"roleId": str(role.id), "roleName": role.name},
        )
        return Response(PartnerRoleSerializer(role).data, status=status.HTTP_200_OK)

    # ── Departments & Units (Org Setup > units_departments) ────────────────
    @action(detail=True, methods=["get", "post"], url_path="departments")
    def departments(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.departments.manage")
            serializer = PartnerDepartmentSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            department = serializer.save(partner=partner)
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.department.create",
                target_type="partner_department", target_id=str(department.id),
                metadata={"name": department.name}, request=request,
            )
            return Response(PartnerDepartmentSerializer(department).data, status=status.HTTP_201_CREATED)
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        qs = PartnerDepartment.objects.filter(partner=partner).select_related("lead")
        return Response(PartnerDepartmentSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch", "delete"], url_path=r"departments/(?P<department_id>[^/.]+)")
    def department_detail(self, request, pk=None, department_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.departments.manage")
        department = PartnerDepartment.objects.filter(id=department_id, partner=partner).first()
        if not department:
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            department.delete()
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.department.delete",
                target_type="partner_department", target_id=str(department_id), request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = PartnerDepartmentSerializer(department, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.department.update",
            target_type="partner_department", target_id=str(department.id),
            metadata={"fields": list((request.data or {}).keys())}, request=request,
        )
        return Response(PartnerDepartmentSerializer(department).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path=r"departments/(?P<department_id>[^/.]+)/members")
    def department_members(self, request, pk=None, department_id=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        department = PartnerDepartment.objects.filter(id=department_id, partner=partner).first()
        if not department:
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
        memberships = department.memberships.select_related("user")
        return Response(PartnerDepartmentMemberSerializer(memberships, many=True).data, status=status.HTTP_200_OK)

    # ── Locations & Branches (Org Setup > org_locations) ────────────────────
    @action(detail=True, methods=["get", "post"], url_path="locations")
    def locations(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.locations.manage")
            serializer = PartnerLocationSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if serializer.validated_data.get("is_primary"):
                PartnerLocation.objects.filter(partner=partner, is_primary=True).update(is_primary=False)
            location = serializer.save(partner=partner)
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.location.create",
                target_type="partner_location", target_id=str(location.id),
                metadata={"name": location.name}, request=request,
            )
            return Response(PartnerLocationSerializer(location).data, status=status.HTTP_201_CREATED)
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        qs = PartnerLocation.objects.filter(partner=partner)
        return Response(PartnerLocationSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch", "delete"], url_path=r"locations/(?P<location_id>[^/.]+)")
    def location_detail(self, request, pk=None, location_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.locations.manage")
        location = PartnerLocation.objects.filter(id=location_id, partner=partner).first()
        if not location:
            return Response({"detail": "Location not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            location.delete()
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.location.delete",
                target_type="partner_location", target_id=str(location_id), request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = PartnerLocationSerializer(location, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get("is_primary"):
            PartnerLocation.objects.filter(partner=partner, is_primary=True).exclude(id=location.id).update(is_primary=False)
        serializer.save()
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.location.update",
            target_type="partner_location", target_id=str(location.id),
            metadata={"fields": list((request.data or {}).keys())}, request=request,
        )
        return Response(PartnerLocationSerializer(location).data, status=status.HTTP_200_OK)

    # ── Leadership & Org Tree ────────────────────────────────────────────
    @action(detail=True, methods=["get"], url_path="leadership")
    def leadership(self, request, pk=None):
        """org_tree_view, leadership_directory, reporting_lines,
        span_of_control, role_alignment, leadership_scorecards — all
        derived from PartnerDepartment (already real, from Org Setup) plus
        Task completion for each lead (already real, from apps.tasks).
        team_health/mentorship_routes/skills_matrix/capacity_planning/
        cross_team_projects/diversity_dashboard/conflict_resolution/
        role_requirements/org_announcements have no real data source
        anywhere in the product yet and are returned as
        unavailable_metrics rather than faked."""
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")

        departments = list(
            PartnerDepartment.objects.filter(partner=partner)
            .select_related("lead")
            .prefetch_related("memberships__user")
        )

        org_tree = []
        leadership_directory = []
        reporting_lines = []
        unaligned_departments = []
        lead_ids = []
        for dept in departments:
            member_rows = [m.user for m in dept.memberships.all()]
            org_tree.append({
                "department_id": dept.id, "department_name": dept.name,
                "lead_id": str(dept.lead_id) if dept.lead_id else None,
                "lead_name": dept.lead.display_name if dept.lead else None,
                "member_count": len(member_rows),
            })
            if dept.lead:
                lead_ids.append(dept.lead_id)
                leadership_directory.append({
                    "user_id": str(dept.lead_id),
                    "display_name": dept.lead.display_name or dept.lead.username or "",
                    "avatar_url": getattr(dept.lead, "avatar_url", None),
                    "department_name": dept.name,
                    "direct_reports": len(member_rows),
                })
                for member in member_rows:
                    reporting_lines.append({
                        "user_id": str(member.id), "display_name": member.display_name or member.username or "",
                        "reports_to_id": str(dept.lead_id), "reports_to_name": dept.lead.display_name or dept.lead.username or "",
                        "department_name": dept.name,
                    })
            else:
                unaligned_departments.append({"department_id": dept.id, "department_name": dept.name})

        span_of_control = sorted(leadership_directory, key=lambda row: row["direct_reports"], reverse=True)

        # Leadership Scorecards — each lead's own task completion rate
        # across the whole org (reusing apps.tasks, not department-scoped
        # since tasks aren't linked to departments).
        leadership_scorecards = []
        if lead_ids:
            from apps.tasks.models import Task, TaskStatus as TaskStatusChoices

            for lead_id in set(lead_ids):
                lead_tasks = Task.objects.filter(partner=partner, assigned_to_id=lead_id, is_deleted=False)
                total = lead_tasks.count()
                completed = lead_tasks.filter(status=TaskStatusChoices.COMPLETED).count()
                lead_user = next((d.lead for d in departments if d.lead_id == lead_id), None)
                leadership_scorecards.append({
                    "user_id": str(lead_id),
                    "display_name": (lead_user.display_name or lead_user.username or "") if lead_user else "",
                    "tasks_assigned": total,
                    "tasks_completed": completed,
                    "completion_rate": round(completed / total, 2) if total else None,
                })

        return Response(
            {
                "org_tree": org_tree,
                "leadership_directory": leadership_directory,
                "reporting_lines": reporting_lines,
                "span_of_control": span_of_control,
                "role_alignment": {"unaligned_departments": unaligned_departments, "total_departments": len(departments)},
                "leadership_scorecards": leadership_scorecards,
                "unavailable_metrics": [
                    "team_health", "mentorship_routes", "skills_matrix", "capacity_planning",
                    "cross_team_projects", "diversity_dashboard", "conflict_resolution",
                    "role_requirements", "org_announcements",
                ],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "post"], url_path="department-notes")
    def department_notes(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.departments.manage")
            department_id = request.data.get("department")
            department = PartnerDepartment.objects.filter(id=department_id, partner=partner).first()
            if not department:
                raise ValidationError({"department": "Department not found."})
            serializer = PartnerDepartmentNoteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            note = serializer.save(department=department, created_by=request.user)
            return Response(PartnerDepartmentNoteSerializer(note).data, status=status.HTTP_201_CREATED)
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        qs = PartnerDepartmentNote.objects.filter(department__partner=partner).select_related("created_by", "department")
        department_id = request.query_params.get("department_id")
        if department_id:
            qs = qs.filter(department_id=department_id)
        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        return Response(PartnerDepartmentNoteSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["delete"], url_path=r"department-notes/(?P<note_id>[^/.]+)")
    def department_note_detail(self, request, pk=None, note_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.departments.manage")
        note = PartnerDepartmentNote.objects.filter(id=note_id, department__partner=partner).first()
        if not note:
            return Response({"detail": "Note not found."}, status=status.HTTP_404_NOT_FOUND)
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── Resource Library & Knowledge Base ───────────────────────────────
    @action(detail=True, methods=["get", "post"], url_path="resources")
    def resources(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.resources.manage")
            serializer = PartnerResourceSerializer(data=request.data, context={"request": request})
            serializer.is_valid(raise_exception=True)
            resource = serializer.save(partner=partner, created_by=request.user)
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.resource.create",
                target_type="partner_resource", target_id=str(resource.id),
                metadata={"title": resource.title, "kind": resource.kind}, request=request,
            )
            return Response(PartnerResourceSerializer(resource).data, status=status.HTTP_201_CREATED)
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        qs = PartnerResource.objects.filter(partner=partner).select_related("asset", "created_by")
        kind = request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        return Response(PartnerResourceSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["delete"], url_path=r"resources/(?P<resource_id>[^/.]+)")
    def resource_detail(self, request, pk=None, resource_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.resources.manage")
        resource = PartnerResource.objects.filter(id=resource_id, partner=partner).first()
        if not resource:
            return Response({"detail": "Resource not found."}, status=status.HTTP_404_NOT_FOUND)
        resource.delete()
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.resource.delete",
            target_type="partner_resource", target_id=str(resource_id), request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── Events Calendar ──────────────────────────────────────────────────
    @action(detail=True, methods=["get", "post"], url_path="calendar-events")
    def calendar_events(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.calendar.manage")
            serializer = PartnerCalendarEventSerializer(data=request.data, context={"request": request})
            serializer.is_valid(raise_exception=True)
            event = serializer.save(partner=partner, created_by=request.user)
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.calendar_event.create",
                target_type="partner_calendar_event", target_id=str(event.id),
                metadata={"title": event.title}, request=request,
            )
            return Response(
                PartnerCalendarEventSerializer(event, context={"request": request}).data, status=status.HTTP_201_CREATED,
            )
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        qs = PartnerCalendarEvent.objects.filter(partner=partner).select_related("created_by", "department").prefetch_related("rsvps")
        if not self._user_can_manage_partner(partner, request.user):
            qs = qs.exclude(visibility=PartnerCalendarEventVisibility.ADMINS_ONLY)
        upcoming_only = request.query_params.get("upcoming")
        if upcoming_only == "1":
            qs = qs.filter(end_at__gte=timezone.now())
        return Response(
            PartnerCalendarEventSerializer(qs, many=True, context={"request": request}).data, status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["patch", "delete"], url_path=r"calendar-events/(?P<event_id>[^/.]+)")
    def calendar_event_detail(self, request, pk=None, event_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.calendar.manage")
        event = PartnerCalendarEvent.objects.filter(id=event_id, partner=partner).first()
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            event.delete()
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.calendar_event.delete",
                target_type="partner_calendar_event", target_id=str(event_id), request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = PartnerCalendarEventSerializer(
            event, data=request.data, partial=True, context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.calendar_event.update",
            target_type="partner_calendar_event", target_id=str(event.id),
            metadata={"fields": list((request.data or {}).keys())}, request=request,
        )
        return Response(PartnerCalendarEventSerializer(event, context={"request": request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path=r"calendar-events/(?P<event_id>[^/.]+)/rsvp")
    def calendar_event_rsvp(self, request, pk=None, event_id=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        event = PartnerCalendarEvent.objects.filter(id=event_id, partner=partner).first()
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)
        if event.visibility == PartnerCalendarEventVisibility.ADMINS_ONLY and not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to RSVP to this event.")
        rsvp_status = request.data.get("status")
        if rsvp_status not in dict(PartnerCalendarRsvpStatus.choices):
            raise ValidationError({"status": "Invalid RSVP status."})
        rsvp, _ = PartnerCalendarRsvp.objects.update_or_create(
            event=event, user=request.user, defaults={"status": rsvp_status},
        )
        return Response(PartnerCalendarRsvpSerializer(rsvp).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path=r"calendar-events/(?P<event_id>[^/.]+)/attendees")
    def calendar_event_attendees(self, request, pk=None, event_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.calendar.manage")
        event = PartnerCalendarEvent.objects.filter(id=event_id, partner=partner).first()
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)
        rsvps = event.rsvps.select_related("user")
        return Response(PartnerCalendarRsvpSerializer(rsvps, many=True).data, status=status.HTTP_200_OK)

    # ── Support Inbox & Helpdesk ─────────────────────────────────────────
    @action(detail=True, methods=["get", "post"], url_path="support-tickets")
    def support_tickets(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            if not self._user_can_access_partner(partner, request.user):
                raise PermissionDenied("Not allowed to submit a ticket here.")
            serializer = SupportTicketSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            ticket = serializer.save(partner=partner, requester=request.user)
            log_partner_audit(
                partner=partner, actor=request.user, action="support_ticket.create",
                target_type="support_ticket", target_id=str(ticket.id),
                metadata={"subject": ticket.subject}, request=request,
            )
            return Response(SupportTicketSerializer(ticket).data, status=status.HTTP_201_CREATED)
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        is_admin = self._user_can_manage_partner(partner, request.user)
        qs = SupportTicket.objects.filter(partner=partner).select_related("requester", "assignee", "department")
        if not is_admin:
            qs = qs.filter(requester=request.user)
        ticket_status = request.query_params.get("status")
        if ticket_status:
            qs = qs.filter(status=ticket_status)
        assignee_id = request.query_params.get("assignee")
        if assignee_id:
            qs = qs.filter(assignee_id=assignee_id)
        return Response(SupportTicketSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    def _get_accessible_ticket(self, partner, ticket_id, user, *, require_manage=False):
        ticket = SupportTicket.objects.filter(id=ticket_id, partner=partner).select_related("requester").first()
        if not ticket:
            return None, Response({"detail": "Ticket not found."}, status=status.HTTP_404_NOT_FOUND)
        is_admin = self._user_can_manage_partner(partner, user)
        if require_manage and not is_admin:
            raise PermissionDenied("Not allowed to manage this ticket.")
        if not require_manage and not is_admin and ticket.requester_id != user.id:
            raise PermissionDenied("Not allowed to view this ticket.")
        return ticket, None

    @action(detail=True, methods=["get", "patch", "delete"], url_path=r"support-tickets/(?P<ticket_id>[^/.]+)")
    def support_ticket_detail(self, request, pk=None, ticket_id=None):
        partner = self.get_object()
        require_manage = request.method in ("PATCH", "DELETE")
        ticket, error = self._get_accessible_ticket(partner, ticket_id, request.user, require_manage=require_manage)
        if error:
            return error
        if request.method == "DELETE":
            ticket.delete()
            log_partner_audit(
                partner=partner, actor=request.user, action="support_ticket.delete",
                target_type="support_ticket", target_id=str(ticket_id), request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        if request.method == "PATCH":
            serializer = SupportTicketSerializer(
                ticket, data=request.data, partial=True,
            )
            serializer.is_valid(raise_exception=True)
            was_open = ticket.status not in (SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED)
            serializer.save()
            ticket.refresh_from_db()
            now_closed = ticket.status in (SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED)
            if was_open and now_closed and not ticket.resolved_at:
                ticket.resolved_at = timezone.now()
                ticket.save(update_fields=["resolved_at"])
            elif not now_closed and ticket.resolved_at:
                ticket.resolved_at = None
                ticket.save(update_fields=["resolved_at"])
            log_partner_audit(
                partner=partner, actor=request.user, action="support_ticket.update",
                target_type="support_ticket", target_id=str(ticket.id),
                metadata={"fields": list((request.data or {}).keys())}, request=request,
            )
            return Response(SupportTicketSerializer(ticket).data, status=status.HTTP_200_OK)
        return Response(SupportTicketSerializer(ticket).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path=r"support-tickets/(?P<ticket_id>[^/.]+)/replies")
    def support_ticket_replies(self, request, pk=None, ticket_id=None):
        partner = self.get_object()
        ticket, error = self._get_accessible_ticket(partner, ticket_id, request.user)
        if error:
            return error
        is_admin = self._user_can_manage_partner(partner, request.user)
        if request.method == "POST":
            is_internal_note = bool(request.data.get("is_internal_note")) and is_admin
            body = (request.data.get("body") or "").strip()
            if not body:
                raise ValidationError({"body": "This field is required."})
            reply = SupportTicketReply.objects.create(
                ticket=ticket, author=request.user, body=body, is_internal_note=is_internal_note,
            )
            log_partner_audit(
                partner=partner, actor=request.user, action="support_ticket.reply",
                target_type="support_ticket", target_id=str(ticket.id), request=request,
            )
            return Response(SupportTicketReplySerializer(reply).data, status=status.HTTP_201_CREATED)
        qs = ticket.replies.select_related("author")
        if not is_admin:
            qs = qs.filter(is_internal_note=False)
        return Response(SupportTicketReplySerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="support-inbox-summary")
    def support_inbox_summary(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.support_inbox.manage")
        qs = SupportTicket.objects.filter(partner=partner)
        counts = {choice: qs.filter(status=choice).count() for choice in SupportTicketStatus.values}
        unassigned = qs.filter(assignee__isnull=True).exclude(
            status__in=[SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED],
        ).count()
        return Response({"counts": counts, "unassigned": unassigned, "total": qs.count()}, status=status.HTTP_200_OK)

    # ── Post Templates ───────────────────────────────────────────────────
    @action(detail=True, methods=["get", "post"], url_path="post-templates")
    def post_templates(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.templates.manage")
        if request.method == "POST":
            serializer = PartnerPostTemplateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            template = serializer.save(partner=partner, created_by=request.user)
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.post_template.create",
                target_type="partner_post_template", target_id=str(template.id),
                metadata={"title": template.title}, request=request,
            )
            return Response(PartnerPostTemplateSerializer(template).data, status=status.HTTP_201_CREATED)
        qs = PartnerPostTemplate.objects.filter(partner=partner).select_related("created_by")
        return Response(PartnerPostTemplateSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch", "delete"], url_path=r"post-templates/(?P<template_id>[^/.]+)")
    def post_template_detail(self, request, pk=None, template_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.templates.manage")
        template = PartnerPostTemplate.objects.filter(id=template_id, partner=partner).first()
        if not template:
            return Response({"detail": "Template not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            template.delete()
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.post_template.delete",
                target_type="partner_post_template", target_id=str(template_id), request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = PartnerPostTemplateSerializer(template, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.post_template.update",
            target_type="partner_post_template", target_id=str(template.id),
            metadata={"fields": list((request.data or {}).keys())}, request=request,
        )
        return Response(PartnerPostTemplateSerializer(template).data, status=status.HTTP_200_OK)

    # ── Feedback Hub & Surveys ───────────────────────────────────────────
    def _create_survey_questions(self, survey, questions_data):
        for index, question_data in enumerate(questions_data):
            PartnerSurveyQuestion.objects.create(
                survey=survey,
                text=question_data.get("text", ""),
                question_type=question_data.get("question_type", PartnerSurveyQuestionType.TEXT),
                options=question_data.get("options", []),
                required=question_data.get("required", True),
                order=question_data.get("order", index),
            )

    @action(detail=True, methods=["get", "post"], url_path="surveys")
    def surveys(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.surveys.manage")
            serializer = PartnerSurveySerializer(data=request.data, context={"request": request})
            serializer.is_valid(raise_exception=True)
            survey = serializer.save(partner=partner, created_by=request.user)
            self._create_survey_questions(survey, request.data.get("questions") or [])
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.survey.create",
                target_type="partner_survey", target_id=str(survey.id),
                metadata={"title": survey.title}, request=request,
            )
            return Response(PartnerSurveySerializer(survey, context={"request": request}).data, status=status.HTTP_201_CREATED)
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        qs = PartnerSurvey.objects.filter(partner=partner).prefetch_related("questions", "responses")
        if not self._user_can_manage_partner(partner, request.user):
            qs = qs.filter(status=PartnerSurveyStatus.OPEN)
        return Response(PartnerSurveySerializer(qs, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "patch", "delete"], url_path=r"surveys/(?P<survey_id>[^/.]+)")
    def survey_detail(self, request, pk=None, survey_id=None):
        partner = self.get_object()
        survey = PartnerSurvey.objects.filter(id=survey_id, partner=partner).prefetch_related("questions", "responses").first()
        if not survey:
            return Response({"detail": "Survey not found."}, status=status.HTTP_404_NOT_FOUND)
        is_admin = self._user_can_manage_partner(partner, request.user)
        if request.method == "GET":
            if not is_admin and survey.status != PartnerSurveyStatus.OPEN:
                raise PermissionDenied("This survey is not currently open.")
            return Response(PartnerSurveySerializer(survey, context={"request": request}).data, status=status.HTTP_200_OK)
        if not is_admin:
            raise PermissionDenied("Not allowed to manage this survey.")
        if request.method == "DELETE":
            survey.delete()
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.survey.delete",
                target_type="partner_survey", target_id=str(survey_id), request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = PartnerSurveySerializer(survey, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if "questions" in request.data:
            survey.questions.all().delete()
            self._create_survey_questions(survey, request.data.get("questions") or [])
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.survey.update",
            target_type="partner_survey", target_id=str(survey.id),
            metadata={"fields": list((request.data or {}).keys())}, request=request,
        )
        survey.refresh_from_db()
        return Response(PartnerSurveySerializer(survey, context={"request": request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path=r"surveys/(?P<survey_id>[^/.]+)/respond")
    def survey_respond(self, request, pk=None, survey_id=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to respond to this survey.")
        survey = PartnerSurvey.objects.filter(id=survey_id, partner=partner).prefetch_related("questions").first()
        if not survey:
            return Response({"detail": "Survey not found."}, status=status.HTTP_404_NOT_FOUND)
        if survey.status != PartnerSurveyStatus.OPEN:
            return Response({"detail": "This survey is not currently open."}, status=status.HTTP_400_BAD_REQUEST)
        if PartnerSurveyResponse.objects.filter(survey=survey, respondent=request.user).exists():
            return Response({"detail": "You have already responded to this survey."}, status=status.HTTP_400_BAD_REQUEST)
        answers_payload = request.data.get("answers") or []
        answers_by_question = {str(a.get("question")): a.get("value") for a in answers_payload}
        for question in survey.questions.all():
            if question.required and str(question.id) not in answers_by_question:
                raise ValidationError({"answers": f"Question '{question.text}' is required."})
        survey_response = PartnerSurveyResponse.objects.create(survey=survey, respondent=request.user)
        for question in survey.questions.all():
            value = answers_by_question.get(str(question.id))
            if value is None:
                continue
            PartnerSurveyAnswer.objects.create(response=survey_response, question=question, value=value)
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.survey.respond",
            target_type="partner_survey", target_id=str(survey.id), request=request,
        )
        return Response(
            PartnerSurveyResponseSerializer(survey_response).data, status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], url_path=r"surveys/(?P<survey_id>[^/.]+)/results")
    def survey_results(self, request, pk=None, survey_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.surveys.manage")
        survey = PartnerSurvey.objects.filter(id=survey_id, partner=partner).prefetch_related("questions", "responses__answers").first()
        if not survey:
            return Response({"detail": "Survey not found."}, status=status.HTTP_404_NOT_FOUND)
        results = []
        for question in survey.questions.all():
            answers = PartnerSurveyAnswer.objects.filter(question=question).select_related("response")
            entry = {
                "question_id": question.id,
                "text": question.text,
                "question_type": question.question_type,
                "response_count": answers.count(),
            }
            if question.question_type == PartnerSurveyQuestionType.SINGLE_CHOICE:
                counts: dict = {}
                for answer in answers:
                    choice_id = (answer.value or {}).get("choice_id")
                    if choice_id:
                        counts[choice_id] = counts.get(choice_id, 0) + 1
                entry["choice_counts"] = counts
            elif question.question_type == PartnerSurveyQuestionType.MULTIPLE_CHOICE:
                counts = {}
                for answer in answers:
                    for choice_id in (answer.value or {}).get("choice_ids", []):
                        counts[choice_id] = counts.get(choice_id, 0) + 1
                entry["choice_counts"] = counts
            elif question.question_type == PartnerSurveyQuestionType.RATING:
                values = [
                    v for a in answers
                    if (v := (a.value or {}).get("value")) is not None
                ]
                entry["average_rating"] = round(sum(values) / len(values), 2) if values else None
                entry["rating_count"] = len(values)
            else:
                entry["text_answers"] = [
                    (a.value or {}).get("text", "") for a in answers if (a.value or {}).get("text")
                ]
            results.append(entry)
        return Response(
            {
                "survey_id": survey.id,
                "title": survey.title,
                "total_responses": survey.responses.count(),
                "questions": results,
            },
            status=status.HTTP_200_OK,
        )

    # ── Budget Tracking ──────────────────────────────────────────────────
    @action(detail=True, methods=["get", "post"], url_path="budgets")
    def budgets(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.budgets.manage")
        if request.method == "POST":
            serializer = PartnerBudgetSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            budget = serializer.save(partner=partner, created_by=request.user)
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.budget.create",
                target_type="partner_budget", target_id=str(budget.id),
                metadata={"name": budget.name}, request=request,
            )
            return Response(PartnerBudgetSerializer(budget).data, status=status.HTTP_201_CREATED)
        qs = PartnerBudget.objects.filter(partner=partner).select_related("department", "created_by")
        department_id = request.query_params.get("department")
        if department_id:
            qs = qs.filter(department_id=department_id)
        return Response(PartnerBudgetSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch", "delete"], url_path=r"budgets/(?P<budget_id>[^/.]+)")
    def budget_detail(self, request, pk=None, budget_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.budgets.manage")
        budget = PartnerBudget.objects.filter(id=budget_id, partner=partner).first()
        if not budget:
            return Response({"detail": "Budget not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            budget.delete()
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.budget.delete",
                target_type="partner_budget", target_id=str(budget_id), request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = PartnerBudgetSerializer(budget, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.budget.update",
            target_type="partner_budget", target_id=str(budget.id),
            metadata={"fields": list((request.data or {}).keys())}, request=request,
        )
        return Response(PartnerBudgetSerializer(budget).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path=r"budgets/(?P<budget_id>[^/.]+)/expenses")
    def budget_expenses(self, request, pk=None, budget_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.budgets.manage")
        budget = PartnerBudget.objects.filter(id=budget_id, partner=partner).first()
        if not budget:
            return Response({"detail": "Budget not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "POST":
            serializer = PartnerBudgetExpenseSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            expense = serializer.save(budget=budget, recorded_by=request.user)
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.budget_expense.create",
                target_type="partner_budget_expense", target_id=str(expense.id),
                metadata={"amount": str(expense.amount)}, request=request,
            )
            return Response(PartnerBudgetExpenseSerializer(expense).data, status=status.HTTP_201_CREATED)
        qs = budget.expenses.select_related("recorded_by")
        return Response(PartnerBudgetExpenseSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["delete"], url_path=r"budgets/(?P<budget_id>[^/.]+)/expenses/(?P<expense_id>[^/.]+)")
    def budget_expense_detail(self, request, pk=None, budget_id=None, expense_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.budgets.manage")
        expense = PartnerBudgetExpense.objects.filter(id=expense_id, budget_id=budget_id, budget__partner=partner).first()
        if not expense:
            return Response({"detail": "Expense not found."}, status=status.HTTP_404_NOT_FOUND)
        expense.delete()
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.budget_expense.delete",
            target_type="partner_budget_expense", target_id=str(expense_id), request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── Volunteer Roster ─────────────────────────────────────────────────
    @action(detail=True, methods=["get", "post"], url_path="volunteer-shifts")
    def volunteer_shifts(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.volunteers.manage")
            serializer = PartnerVolunteerShiftSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            shift = serializer.save(partner=partner, created_by=request.user)
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.volunteer_shift.create",
                target_type="partner_volunteer_shift", target_id=str(shift.id),
                metadata={"title": shift.title}, request=request,
            )
            return Response(PartnerVolunteerShiftSerializer(shift, context={"request": request}).data, status=status.HTTP_201_CREATED)
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization.")
        qs = PartnerVolunteerShift.objects.filter(partner=partner).prefetch_related("signups")
        upcoming_only = request.query_params.get("upcoming")
        if upcoming_only == "1":
            qs = qs.filter(ends_at__gte=timezone.now())
        return Response(PartnerVolunteerShiftSerializer(qs, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch", "delete"], url_path=r"volunteer-shifts/(?P<shift_id>[^/.]+)")
    def volunteer_shift_detail(self, request, pk=None, shift_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.volunteers.manage")
        shift = PartnerVolunteerShift.objects.filter(id=shift_id, partner=partner).first()
        if not shift:
            return Response({"detail": "Shift not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            shift.delete()
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.volunteer_shift.delete",
                target_type="partner_volunteer_shift", target_id=str(shift_id), request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = PartnerVolunteerShiftSerializer(shift, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.volunteer_shift.update",
            target_type="partner_volunteer_shift", target_id=str(shift.id),
            metadata={"fields": list((request.data or {}).keys())}, request=request,
        )
        return Response(PartnerVolunteerShiftSerializer(shift, context={"request": request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path=r"volunteer-shifts/(?P<shift_id>[^/.]+)/signup")
    def volunteer_shift_signup(self, request, pk=None, shift_id=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to sign up for this shift.")
        shift = PartnerVolunteerShift.objects.filter(id=shift_id, partner=partner).first()
        if not shift:
            return Response({"detail": "Shift not found."}, status=status.HTTP_404_NOT_FOUND)
        action_type = request.data.get("action", "signup")
        if action_type == "cancel":
            PartnerVolunteerSignup.objects.filter(shift=shift, volunteer=request.user).update(
                status=PartnerVolunteerSignupStatus.CANCELLED,
            )
            return Response(
                {"detail": "Signup cancelled.", "my_status": PartnerVolunteerSignupStatus.CANCELLED}, status=status.HTTP_200_OK,
            )
        existing = PartnerVolunteerSignup.objects.filter(shift=shift, volunteer=request.user).first()
        if existing and existing.status != PartnerVolunteerSignupStatus.CANCELLED:
            return Response({"detail": "Already signed up."}, status=status.HTTP_400_BAD_REQUEST)
        active_count = shift.signups.exclude(status=PartnerVolunteerSignupStatus.CANCELLED).count()
        if active_count >= shift.slots_total:
            return Response({"detail": "This shift is full."}, status=status.HTTP_400_BAD_REQUEST)
        if existing:
            existing.status = PartnerVolunteerSignupStatus.SIGNED_UP
            existing.signed_up_at = timezone.now()
            existing.save(update_fields=["status", "signed_up_at"])
            signup = existing
        else:
            signup = PartnerVolunteerSignup.objects.create(shift=shift, volunteer=request.user)
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.volunteer_shift.signup",
            target_type="partner_volunteer_shift", target_id=str(shift.id), request=request,
        )
        return Response(PartnerVolunteerSignupSerializer(signup).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path=r"volunteer-shifts/(?P<shift_id>[^/.]+)/roster")
    def volunteer_shift_roster(self, request, pk=None, shift_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.volunteers.manage")
        shift = PartnerVolunteerShift.objects.filter(id=shift_id, partner=partner).first()
        if not shift:
            return Response({"detail": "Shift not found."}, status=status.HTTP_404_NOT_FOUND)
        signups = shift.signups.exclude(status=PartnerVolunteerSignupStatus.CANCELLED).select_related("volunteer")
        return Response(PartnerVolunteerSignupSerializer(signups, many=True).data, status=status.HTTP_200_OK)

    # ── Donation Tracking ────────────────────────────────────────────────
    @action(detail=True, methods=["get", "post"], url_path="donations")
    def donations(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.donations.manage")
        if request.method == "POST":
            serializer = PartnerDonationSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            donation = serializer.save(partner=partner, recorded_by=request.user)
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.donation.create",
                target_type="partner_donation", target_id=str(donation.id),
                metadata={"amount": str(donation.amount), "receipt_number": donation.receipt_number}, request=request,
            )
            return Response(PartnerDonationSerializer(donation).data, status=status.HTTP_201_CREATED)
        qs = PartnerDonation.objects.filter(partner=partner).select_related("donor", "recorded_by")
        fund = request.query_params.get("fund")
        if fund:
            qs = qs.filter(fund=fund)
        date_from = request.query_params.get("from")
        if date_from:
            qs = qs.filter(received_at__gte=date_from)
        date_to = request.query_params.get("to")
        if date_to:
            qs = qs.filter(received_at__lte=date_to)
        return Response(PartnerDonationSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch", "delete"], url_path=r"donations/(?P<donation_id>[^/.]+)")
    def donation_detail(self, request, pk=None, donation_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.donations.manage")
        donation = PartnerDonation.objects.filter(id=donation_id, partner=partner).first()
        if not donation:
            return Response({"detail": "Donation not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            donation.delete()
            log_partner_audit(
                partner=partner, actor=request.user, action="partner.donation.delete",
                target_type="partner_donation", target_id=str(donation_id), request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = PartnerDonationSerializer(donation, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner, actor=request.user, action="partner.donation.update",
            target_type="partner_donation", target_id=str(donation.id),
            metadata={"fields": list((request.data or {}).keys())}, request=request,
        )
        return Response(PartnerDonationSerializer(donation).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="donations-summary")
    def donations_summary(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.donations.manage")
        from django.db.models import Sum, Count

        qs = PartnerDonation.objects.filter(partner=partner)
        date_from = request.query_params.get("from")
        if date_from:
            qs = qs.filter(received_at__gte=date_from)
        date_to = request.query_params.get("to")
        if date_to:
            qs = qs.filter(received_at__lte=date_to)
        totals = qs.aggregate(total_amount=Sum("amount"), donation_count=Count("id"))
        by_fund = list(
            qs.exclude(fund="").values("fund").annotate(total=Sum("amount"), count=Count("id")).order_by("-total"),
        )
        by_method = list(
            qs.values("method").annotate(total=Sum("amount"), count=Count("id")).order_by("-total"),
        )
        return Response(
            {
                "total_amount": totals["total_amount"] or 0,
                "donation_count": totals["donation_count"] or 0,
                "by_fund": by_fund,
                "by_method": by_method,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "post"], url_path="role-assignments")
    def role_assignments(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.roles.manage")
            serializer = PartnerRoleAssignmentSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(partner=partner)
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.role.assign",
                target_type="partner_role_assignment",
                target_id=str(serializer.instance.id),
                metadata={"user": str(serializer.instance.user_id)},
                request=request,
            )
            dispatch_partner_webhooks(
                partner=partner,
                event="role.changed",
                payload={
                    "action": "assigned",
                    "assignment_id": str(serializer.instance.id),
                    "user_id": str(serializer.instance.user_id),
                    "role_id": str(serializer.instance.role_id),
                    "actor_id": str(request.user.id),
                },
            )
            run_partner_automation_rules(
                partner=partner,
                event="role.changed",
                payload={
                    "action": "assigned",
                    "assignment_id": str(serializer.instance.id),
                    "user_id": str(serializer.instance.user_id),
                    "role_id": str(serializer.instance.role_id),
                    "actor_id": str(request.user.id),
                },
                actor=request.user,
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        self._require_permission(partner, request.user, "partner.roles.view")
        qs = PartnerRoleAssignment.objects.filter(partner=partner).order_by("-created_at")
        return Response(
            PartnerRoleAssignmentSerializer(qs, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="role-assignments/remove")
    def role_assignments_remove(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.roles.manage")
        assignment_id = request.data.get("assignmentId") or request.data.get("assignment_id")
        if not assignment_id:
            raise ValidationError("assignmentId is required.")
        assignment = PartnerRoleAssignment.objects.filter(id=assignment_id, partner=partner).first()
        if not assignment:
            return Response({"detail": "Assignment not found."}, status=status.HTTP_404_NOT_FOUND)
        payload = {
            "action": "removed",
            "assignment_id": str(assignment.id),
            "user_id": str(assignment.user_id),
            "role_id": str(assignment.role_id),
            "actor_id": str(request.user.id),
        }
        assignment.delete()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.role.remove",
            target_type="partner_role_assignment",
            target_id=str(assignment_id),
            request=request,
        )
        dispatch_partner_webhooks(
            partner=partner,
            event="role.changed",
            payload=payload,
        )
        run_partner_automation_rules(
            partner=partner,
            event="role.changed",
            payload=payload,
            actor=request.user,
        )
        return Response({"detail": "Role assignment removed."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="integrations")
    def integrations(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_partner_feature(partner, "partner_integrations")
            self._require_permission(partner, request.user, "partner.integrations.manage")
            serializer = PartnerIntegrationSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(partner=partner)
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.integration.create",
                target_type="partner_integration",
                target_id=str(serializer.instance.id),
                metadata={"kind": serializer.instance.kind},
                request=request,
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        self._require_partner_feature(partner, "partner_integrations")
        self._require_permission(partner, request.user, "partner.integrations.view")
        qs = PartnerIntegration.objects.filter(partner=partner).order_by("kind", "provider")
        return Response(PartnerIntegrationSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"integrations/(?P<integration_id>[^/.]+)",
    )
    def integrations_update(self, request, pk=None, integration_id=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "partner_integrations")
        self._require_permission(partner, request.user, "partner.integrations.manage")
        integration = PartnerIntegration.objects.filter(
            id=integration_id,
            partner=partner,
        ).first()
        if not integration:
            return Response({"detail": "Integration not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PartnerIntegrationSerializer(integration, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.integration.update",
            target_type="partner_integration",
            target_id=str(integration.id),
            metadata={"fields": list((request.data or {}).keys())},
            request=request,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="webhooks")
    def webhooks(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_partner_feature(partner, "partner_webhooks")
            self._require_permission(partner, request.user, "partner.integrations.manage")
            serializer = PartnerWebhookSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(partner=partner)
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.webhook.create",
                target_type="partner_webhook",
                target_id=str(serializer.instance.id),
                request=request,
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        self._require_partner_feature(partner, "partner_webhooks")
        self._require_permission(partner, request.user, "partner.integrations.view")
        qs = PartnerWebhook.objects.filter(partner=partner).order_by("-created_at")
        return Response(PartnerWebhookSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"webhooks/(?P<webhook_id>[^/.]+)",
    )
    def webhooks_update(self, request, pk=None, webhook_id=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "partner_webhooks")
        self._require_permission(partner, request.user, "partner.integrations.manage")
        webhook = PartnerWebhook.objects.filter(id=webhook_id, partner=partner).first()
        if not webhook:
            return Response({"detail": "Webhook not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PartnerWebhookSerializer(webhook, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.webhook.update",
            target_type="partner_webhook",
            target_id=str(webhook.id),
            metadata={"fields": list((request.data or {}).keys())},
            request=request,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"webhooks/(?P<webhook_id>[^/.]+)/remove",
    )
    def webhooks_remove(self, request, pk=None, webhook_id=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "partner_webhooks")
        self._require_permission(partner, request.user, "partner.integrations.manage")
        webhook = PartnerWebhook.objects.filter(id=webhook_id, partner=partner).first()
        if not webhook:
            return Response({"detail": "Webhook not found."}, status=status.HTTP_404_NOT_FOUND)
        webhook.delete()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.webhook.delete",
            target_type="partner_webhook",
            target_id=str(webhook_id),
            request=request,
        )
        return Response({"detail": "Webhook removed."}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"webhooks/(?P<webhook_id>[^/.]+)/deliveries",
    )
    def webhook_deliveries(self, request, pk=None, webhook_id=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "partner_webhooks")
        self._require_permission(partner, request.user, "partner.integrations.view")
        webhook = PartnerWebhook.objects.filter(id=webhook_id, partner=partner).first()
        if not webhook:
            return Response({"detail": "Webhook not found."}, status=status.HTTP_404_NOT_FOUND)
        qs = PartnerWebhookDelivery.objects.filter(webhook=webhook).order_by("-created_at")[:200]
        return Response(PartnerWebhookDeliverySerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"webhooks/(?P<webhook_id>[^/.]+)/deliveries/(?P<delivery_id>[^/.]+)/retry",
    )
    def webhook_delivery_retry(self, request, pk=None, webhook_id=None, delivery_id=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "partner_webhooks")
        self._require_permission(partner, request.user, "partner.integrations.manage")
        delivery = PartnerWebhookDelivery.objects.filter(
            id=delivery_id,
            webhook__id=webhook_id,
            webhook__partner=partner,
        ).select_related("webhook").first()
        if not delivery:
            return Response({"detail": "Delivery not found."}, status=status.HTTP_404_NOT_FOUND)
        ok = retry_partner_webhook_delivery(delivery)
        return Response({"delivered": ok}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="automation-rules")
    def automation_rules(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_partner_feature(partner, "partner_automation")
            self._require_permission(partner, request.user, "partner.automation.manage")
            serializer = PartnerAutomationRuleSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(partner=partner, created_by=request.user)
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.automation.create",
                target_type="partner_automation_rule",
                target_id=str(serializer.instance.id),
                request=request,
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        self._require_partner_feature(partner, "partner_automation")
        self._require_permission(partner, request.user, "partner.automation.view")
        qs = PartnerAutomationRule.objects.filter(partner=partner).order_by("-created_at")
        return Response(PartnerAutomationRuleSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"automation-rules/(?P<rule_id>[^/.]+)",
    )
    def automation_rules_update(self, request, pk=None, rule_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.automation.manage")
        rule = PartnerAutomationRule.objects.filter(id=rule_id, partner=partner).first()
        if not rule:
            return Response({"detail": "Automation rule not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PartnerAutomationRuleSerializer(rule, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.automation.update",
            target_type="partner_automation_rule",
            target_id=str(rule.id),
            metadata={"fields": list((request.data or {}).keys())},
            request=request,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"automation-rules/(?P<rule_id>[^/.]+)/remove",
    )
    def automation_rules_remove(self, request, pk=None, rule_id=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "partner_automation")
        self._require_permission(partner, request.user, "partner.automation.manage")
        rule = PartnerAutomationRule.objects.filter(id=rule_id, partner=partner).first()
        if not rule:
            return Response({"detail": "Automation rule not found."}, status=status.HTTP_404_NOT_FOUND)
        rule.delete()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.automation.delete",
            target_type="partner_automation_rule",
            target_id=str(rule_id),
            request=request,
        )
        return Response({"detail": "Automation rule removed."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="reports/summary")
    def reports_summary(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.reports.view")
        self._require_partner_feature(partner, "partner_insight")

        # The ORG's own plan determines what level of analytics its reports
        # include, not whichever staff member happens to be viewing them.
        features = get_partner_tier_features(partner)
        can_basic = bool(features.get("analytics_basic") or features.get("analytics_advanced"))
        can_advanced = bool(features.get("analytics_advanced"))
        if not can_basic:
            raise PermissionDenied("This organization's plan does not include analytics access.")
        summary = build_partner_summary(partner)
        if not can_advanced:
            summary.pop("activity_series", None)
            activity_summary = summary.get("activity_summary") or {}
            if isinstance(activity_summary, dict):
                summary["activity_summary"] = {
                    "last_7_days": activity_summary.get("last_7_days", {}),
                }
        if str(request.query_params.get("snapshot", "")).lower() in ("1", "true", "yes"):
            snapshot = PartnerReportSnapshot.objects.create(
                partner=partner,
                kind="summary",
                data=summary,
                created_by=request.user,
            )
            return Response(PartnerReportSnapshotSerializer(snapshot).data, status=status.HTTP_201_CREATED)
        return Response(summary, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="analytics")
    def analytics(self, request, pk=None):
        """
        Lightweight real-time analytics summary for a partner.
        Requires partner_insight feature flag.
        """
        from django.db.models import Avg, Count, Q, Sum

        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.reports.view")
        self._require_partner_feature(partner, "partner_insight")

        window_days = int(request.query_params.get("days") or 30)
        window_days = min(max(window_days, 1), 365)
        since = timezone.now() - timedelta(days=window_days)

        # Members
        all_members_qs = PartnerMembership.objects.filter(partner=partner)
        total_members = all_members_qs.count()
        active_members = all_members_qs.filter(
            status=PartnerMembershipStatus.MEMBER,
        ).count()

        # Posts and engagement
        posts_qs = PartnerPost.objects.filter(partner=partner)
        total_posts = posts_qs.count()
        recent_posts = posts_qs.filter(created_at__gte=since).count()
        recent_reactions = PartnerPostReaction.objects.filter(
            post__partner=partner, created_at__gte=since
        ).count()
        recent_comments = PartnerPostComment.objects.filter(
            post__partner=partner, created_at__gte=since
        ).count()

        # Marketplace revenue: aggregate MarketplaceOrder totals for shops owned
        # by partner members.
        revenue_data: dict = {}
        try:
            from apps.commerce.models import MarketplaceOrder, MarketplaceOrderStatus

            member_user_ids = list(
                all_members_qs.values_list("user_id", flat=True)
            )
            completed_orders = MarketplaceOrder.objects.filter(
                shop__owner_id__in=member_user_ids,
                status__in=[
                    MarketplaceOrderStatus.SATISFIED,
                    MarketplaceOrderStatus.COMPLETED,
                ],
                created_at__gte=since,
            )
            agg = completed_orders.aggregate(
                total=Sum("total_amount"),
                order_count=Count("id"),
            )
            revenue_data = {
                "period_revenue": float(agg["total"] or 0),
                "period_order_count": agg["order_count"] or 0,
                "currency": "USD",
            }
        except Exception:
            revenue_data = {"period_revenue": None, "period_order_count": None}

        # Top Contributors — most active members by posts + comments authored
        # in the window. Two separate GROUP BY queries merged in Python
        # rather than one UNION query, since posts and comments are
        # different tables with no shared FK to aggregate through directly.
        post_counts = dict(
            posts_qs.filter(created_at__gte=since)
            .values_list("author_id")
            .annotate(n=Count("id"))
        )
        comment_counts = dict(
            PartnerPostComment.objects.filter(post__partner=partner, created_at__gte=since)
            .values_list("author_id")
            .annotate(n=Count("id"))
        )
        # Sorted set iteration is not deterministic across runs (hash
        # randomization) — sort the id list itself first so ties break the
        # same way every time, then break ties in the final sort by
        # (posts, user_id) rather than just "total" (caught by
        # test_owner_gets_full_analytics_payload flaking on a 1-1 tie).
        contributor_ids = sorted(set(post_counts) | set(comment_counts), key=str)
        contributor_totals = sorted(
            (
                {"user_id": uid, "posts": post_counts.get(uid, 0), "comments": comment_counts.get(uid, 0),
                 "total": post_counts.get(uid, 0) + comment_counts.get(uid, 0)}
                for uid in contributor_ids if uid
            ),
            key=lambda row: (row["total"], row["posts"], str(row["user_id"])), reverse=True,
        )[:10]
        from apps.accounts.models import User as AccountUser

        contributor_users = {
            str(u.id): (u.display_name or u.username or "")
            for u in AccountUser.objects.filter(id__in=[row["user_id"] for row in contributor_totals])
        }
        top_contributors = [
            {**row, "user_id": str(row["user_id"]), "display_name": contributor_users.get(str(row["user_id"]), "")}
            for row in contributor_totals
        ]

        # Content Performance — top posts by reactions + comments this window.
        top_posts_qs = (
            posts_qs.filter(created_at__gte=since)
            .annotate(reaction_count=Count("reactions", distinct=True), comment_count=Count("comments", distinct=True))
            .order_by("-reaction_count", "-comment_count")[:5]
        )
        content_performance = [
            {
                "id": str(p.id),
                "text_preview": p.text_preview,
                "reactions": p.reaction_count,
                "comments": p.comment_count,
                "created_at": p.created_at.isoformat(),
            }
            for p in top_posts_qs
        ]

        # Reaction Trends — emoji breakdown this window, a proxy for sentiment.
        reaction_breakdown = list(
            PartnerPostReaction.objects.filter(post__partner=partner, created_at__gte=since)
            .values("emoji").annotate(n=Count("id")).order_by("-n")[:12]
        )

        # Growth Funnel — applications by status this window, then active
        # membership as the funnel's final stage.
        application_counts = dict(
            PartnerApplication.objects.filter(partner=partner, created_at__gte=since)
            .values_list("status").annotate(n=Count("id"))
        )
        growth_funnel = {
            "applied": sum(application_counts.values()),
            "approved": application_counts.get(PartnerApplicationStatus.APPROVED, 0),
            "rejected": application_counts.get(PartnerApplicationStatus.REJECTED, 0),
            "active_members": active_members,
        }

        # Participation Depth — avg engagement per post this window.
        participation_depth = round((recent_reactions + recent_comments) / max(recent_posts, 1), 2)

        # Channel Health — task throughput per channel (created vs completed
        # this window), reusing apps.tasks rather than pulling raw chat
        # message volume from the Nest/Mongo side, which this service has no
        # direct read access to.
        channel_health = []
        try:
            from apps.tasks.models import Task, TaskStatus as TaskStatusChoices

            channel_task_counts = (
                Task.objects.filter(partner=partner, is_deleted=False, created_at__gte=since)
                .values("channel_id", "channel__name")
                .annotate(
                    created=Count("id"),
                    completed=Count("id", filter=Q(status=TaskStatusChoices.COMPLETED)),
                )
                .order_by("-created")[:10]
            )
            channel_health = [
                {
                    "channel_id": str(row["channel_id"]), "channel_name": row["channel__name"],
                    "tasks_created": row["created"], "tasks_completed": row["completed"],
                }
                for row in channel_task_counts
            ]
        except Exception:
            channel_health = []

        # Weekday Heatmap — total activity (posts+comments+reactions) by day
        # of week this window, a real substitute for an hour-level heatmap
        # (which would need timestamp-of-day granularity this window doesn't
        # warrant computing for every partner on every request).
        from django.db.models.functions import ExtractWeekDay
        weekday_totals = {i: 0 for i in range(1, 8)}
        for qs, _ in (
            (posts_qs.filter(created_at__gte=since), None),
            (PartnerPostComment.objects.filter(post__partner=partner, created_at__gte=since), None),
            (PartnerPostReaction.objects.filter(post__partner=partner, created_at__gte=since), None),
        ):
            for row in qs.annotate(wd=ExtractWeekDay("created_at")).values("wd").annotate(n=Count("id")):
                weekday_totals[row["wd"]] = weekday_totals.get(row["wd"], 0) + row["n"]
        weekday_labels = {1: "Sun", 2: "Mon", 3: "Tue", 4: "Wed", 5: "Thu", 6: "Fri", 7: "Sat"}
        community_heatmap = [{"weekday": weekday_labels[i], "total": weekday_totals[i]} for i in range(1, 8)]

        return Response(
            {
                "partner_id": str(partner.id),
                "window_days": window_days,
                "members": {
                    "total": total_members,
                    "active": active_members,
                },
                "posts": {
                    "total": total_posts,
                    "period_new": recent_posts,
                },
                "engagement": {
                    "period_reactions": recent_reactions,
                    "period_comments": recent_comments,
                },
                "revenue": revenue_data,
                "top_contributors": top_contributors,
                "content_performance": content_performance,
                "reaction_breakdown": reaction_breakdown,
                "growth_funnel": growth_funnel,
                "participation_depth": participation_depth,
                "channel_health": channel_health,
                "community_heatmap": community_heatmap,
                # Not yet backed by real data anywhere in the product —
                # honestly flagged rather than fabricated. See
                # PartnerAnalyticsPanel.tsx for how these render.
                "unavailable_metrics": [
                    "message_velocity", "campaign_tracking", "response_times",
                    "event_uptake", "resource_downloads", "retention",
                ],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "post"], url_path="exports")
    def exports(self, request, pk=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "partner_insight")
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.exports.manage")
            kind = request.data.get("kind") or "summary"
            export_format = (request.data.get("export_format") or "csv").lower()
            job = PartnerExportJob.objects.create(
                partner=partner,
                kind=kind,
                export_format=export_format,
                status=PartnerExportStatus.RUNNING,
                created_by=request.user,
            )
            try:
                rows = self._build_export_rows(partner, kind)
                file_path = self._write_export_file(job, rows, export_format)
                job.status = PartnerExportStatus.COMPLETED
                job.file_path = file_path
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "file_path", "finished_at", "updated_at"])
            except Exception as exc:
                job.status = PartnerExportStatus.FAILED
                job.error_message = str(exc)[:500]
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
                return Response({"detail": "Export failed.", "error": job.error_message}, status=400)
            return Response(PartnerExportJobSerializer(job, context={"request": request}).data, status=status.HTTP_201_CREATED)
        self._require_permission(partner, request.user, "partner.exports.view")
        qs = PartnerExportJob.objects.filter(partner=partner).order_by("-created_at")[:200]
        return Response(
            PartnerExportJobSerializer(qs, many=True, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "post"], url_path="export-schedules")
    def export_schedules(self, request, pk=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "partner_insight")
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.exports.manage")
            serializer = PartnerExportScheduleSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            schedule = serializer.save(
                partner=partner,
                created_by=request.user,
                next_run_at=next_export_run_at(serializer.validated_data.get("frequency")),
            )
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.export.schedule.create",
                target_type="partner_export_schedule",
                target_id=str(schedule.id),
                request=request,
            )
            return Response(
                PartnerExportScheduleSerializer(schedule).data,
                status=status.HTTP_201_CREATED,
            )
        self._require_permission(partner, request.user, "partner.exports.view")
        qs = PartnerExportSchedule.objects.filter(partner=partner).order_by("-created_at")
        return Response(PartnerExportScheduleSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"export-schedules/(?P<schedule_id>[^/.]+)",
    )
    def export_schedules_update(self, request, pk=None, schedule_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.exports.manage")
        schedule = PartnerExportSchedule.objects.filter(id=schedule_id, partner=partner).first()
        if not schedule:
            return Response({"detail": "Export schedule not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PartnerExportScheduleSerializer(schedule, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if "frequency" in serializer.validated_data:
            schedule.next_run_at = next_export_run_at(schedule.frequency)
            schedule.save(update_fields=["next_run_at", "updated_at"])
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.export.schedule.update",
            target_type="partner_export_schedule",
            target_id=str(schedule.id),
            metadata={"fields": list((request.data or {}).keys())},
            request=request,
        )
        return Response(PartnerExportScheduleSerializer(schedule).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"export-schedules/(?P<schedule_id>[^/.]+)/remove",
    )
    def export_schedules_remove(self, request, pk=None, schedule_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.exports.manage")
        schedule = PartnerExportSchedule.objects.filter(id=schedule_id, partner=partner).first()
        if not schedule:
            return Response({"detail": "Export schedule not found."}, status=status.HTTP_404_NOT_FOUND)
        schedule.delete()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.export.schedule.delete",
            target_type="partner_export_schedule",
            target_id=str(schedule_id),
            request=request,
        )
        return Response({"detail": "Export schedule removed."}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"export-schedules/(?P<schedule_id>[^/.]+)/run",
    )
    def export_schedules_run(self, request, pk=None, schedule_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.exports.manage")
        schedule = PartnerExportSchedule.objects.filter(id=schedule_id, partner=partner).first()
        if not schedule:
            return Response({"detail": "Export schedule not found."}, status=status.HTTP_404_NOT_FOUND)
        job = self._run_export_job(
            partner=partner,
            kind=schedule.kind,
            export_format=schedule.export_format,
            actor=request.user,
        )
        schedule.last_run_at = timezone.now()
        schedule.next_run_at = next_export_run_at(schedule.frequency)
        schedule.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
        return Response(PartnerExportJobSerializer(job, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="access-requests")
    def access_requests(self, request, pk=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "access_control")
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.access.view")
            serializer = PartnerAccessRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            access_request = serializer.save(partner=partner, requester=request.user)
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.access.request",
                target_type="partner_access_request",
                target_id=str(access_request.id),
                request=request,
            )
            dispatch_partner_webhooks(
                partner=partner,
                event="access.requested",
                payload={
                    "request_id": str(access_request.id),
                    "requester_id": str(request.user.id),
                },
            )
            run_partner_automation_rules(
                partner=partner,
                event="access.requested",
                payload={
                    "request_id": str(access_request.id),
                    "requester_id": str(request.user.id),
                },
                actor=request.user,
            )
            return Response(PartnerAccessRequestSerializer(access_request).data, status=status.HTTP_201_CREATED)
        self._require_permission(partner, request.user, "partner.access.view")
        qs = PartnerAccessRequest.objects.filter(partner=partner).order_by("-created_at")
        return Response(PartnerAccessRequestSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"access-requests/(?P<request_id>[^/.]+)/approve",
    )
    def access_requests_approve(self, request, pk=None, request_id=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "access_control")
        self._require_permission(partner, request.user, "partner.access.manage")
        access_request = PartnerAccessRequest.objects.filter(id=request_id, partner=partner).first()
        if not access_request:
            return Response({"detail": "Access request not found."}, status=status.HTTP_404_NOT_FOUND)
        if access_request.status != PartnerAccessRequestStatus.PENDING:
            return Response({"detail": "Access request already resolved."}, status=status.HTTP_400_BAD_REQUEST)
        access_request.status = PartnerAccessRequestStatus.APPROVED
        access_request.decided_by = request.user
        access_request.decided_at = timezone.now()
        access_request.save(update_fields=["status", "decided_by", "decided_at"])
        if access_request.requested_role_id and access_request.target_user_id:
            PartnerRoleAssignment.objects.update_or_create(
                partner=partner,
                user_id=access_request.target_user_id,
                role_id=access_request.requested_role_id,
                defaults={
                    "scope_type": access_request.scope_type,
                    "scope_id": access_request.scope_id,
                },
            )
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.access.approve",
            target_type="partner_access_request",
            target_id=str(access_request.id),
            request=request,
        )
        dispatch_partner_webhooks(
            partner=partner,
            event="access.approved",
            payload={
                "request_id": str(access_request.id),
                "decided_by": str(request.user.id),
            },
        )
        run_partner_automation_rules(
            partner=partner,
            event="access.approved",
            payload={
                "request_id": str(access_request.id),
                "decided_by": str(request.user.id),
            },
            actor=request.user,
        )
        return Response(PartnerAccessRequestSerializer(access_request).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"access-requests/(?P<request_id>[^/.]+)/reject",
    )
    def access_requests_reject(self, request, pk=None, request_id=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "access_control")
        self._require_permission(partner, request.user, "partner.access.manage")
        access_request = PartnerAccessRequest.objects.filter(id=request_id, partner=partner).first()
        if not access_request:
            return Response({"detail": "Access request not found."}, status=status.HTTP_404_NOT_FOUND)
        if access_request.status != PartnerAccessRequestStatus.PENDING:
            return Response({"detail": "Access request already resolved."}, status=status.HTTP_400_BAD_REQUEST)
        access_request.status = PartnerAccessRequestStatus.REJECTED
        access_request.decided_by = request.user
        access_request.decided_at = timezone.now()
        access_request.save(update_fields=["status", "decided_by", "decided_at"])
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.access.reject",
            target_type="partner_access_request",
            target_id=str(access_request.id),
            request=request,
        )
        dispatch_partner_webhooks(
            partner=partner,
            event="access.rejected",
            payload={
                "request_id": str(access_request.id),
                "decided_by": str(request.user.id),
            },
        )
        run_partner_automation_rules(
            partner=partner,
            event="access.rejected",
            payload={
                "request_id": str(access_request.id),
                "decided_by": str(request.user.id),
            },
            actor=request.user,
        )
        return Response(PartnerAccessRequestSerializer(access_request).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="access-reviews")
    def access_reviews(self, request, pk=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "access_control")
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.access.manage")
            serializer = PartnerAccessReviewSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            review = serializer.save(partner=partner, created_by=request.user)
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.access.review.create",
                target_type="partner_access_review",
                target_id=str(review.id),
                request=request,
            )
            return Response(PartnerAccessReviewSerializer(review).data, status=status.HTTP_201_CREATED)
        self._require_permission(partner, request.user, "partner.access.view")
        qs = PartnerAccessReview.objects.filter(partner=partner).order_by("-created_at")
        return Response(PartnerAccessReviewSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"access-reviews/(?P<review_id>[^/.]+)/close",
    )
    def access_reviews_close(self, request, pk=None, review_id=None):
        partner = self.get_object()
        self._require_partner_feature(partner, "access_control")
        self._require_permission(partner, request.user, "partner.access.manage")
        review = PartnerAccessReview.objects.filter(id=review_id, partner=partner).first()
        if not review:
            return Response({"detail": "Access review not found."}, status=status.HTTP_404_NOT_FOUND)
        review.status = PartnerAccessReviewStatus.CLOSED
        review.closed_at = timezone.now()
        review.findings = request.data.get("findings", review.findings)
        review.save(update_fields=["status", "closed_at", "findings", "updated_at"])
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.access.review.close",
            target_type="partner_access_review",
            target_id=str(review.id),
            request=request,
        )
        return Response(PartnerAccessReviewSerializer(review).data, status=status.HTTP_200_OK)

    def _build_export_rows(self, partner: Partner, kind: str) -> list[dict]:
        if kind == "members":
            qs = PartnerMembership.objects.filter(partner=partner).select_related("user")
            return [
                {
                    "user_id": str(item.user_id),
                    "status": item.status,
                    "role": item.role,
                    "joined_at": item.created_at.isoformat(),
                }
                for item in qs
            ]
        if kind == "audit":
            qs = PartnerAuditEvent.objects.filter(partner=partner).order_by("-created_at")[:5000]
            return [
                {
                    "action": item.action,
                    "target_type": item.target_type,
                    "target_id": item.target_id,
                    "actor_id": str(item.actor_id) if item.actor_id else None,
                    "created_at": item.created_at.isoformat(),
                }
                for item in qs
            ]
        if kind == "roles":
            qs = PartnerRoleAssignment.objects.filter(partner=partner)
            return [
                {
                    "user_id": str(item.user_id),
                    "role_id": str(item.role_id),
                    "scope_type": item.scope_type,
                    "scope_id": item.scope_id,
                }
                for item in qs
            ]
        if kind == "applications":
            qs = PartnerApplication.objects.filter(partner=partner).select_related("user")
            return [
                {
                    "user_id": str(item.user_id),
                    "status": item.status,
                    "method": item.method,
                    "job_post_id": item.job_post_id,
                    "created_at": item.created_at.isoformat(),
                }
                for item in qs
            ]
        if kind == "posts":
            qs = PartnerPost.objects.filter(partner=partner, is_deleted=False).select_related("author")
            return [
                {
                    "post_id": str(item.id),
                    "author_id": str(item.author_id),
                    "text": item.text,
                    "created_at": item.created_at.isoformat(),
                }
                for item in qs
            ]
        summary = build_partner_summary(partner)
        return [summary]

    def _run_export_job(self, *, partner: Partner, kind: str, export_format: str, actor):
        job = PartnerExportJob.objects.create(
            partner=partner,
            kind=kind,
            export_format=export_format,
            status=PartnerExportStatus.RUNNING,
            created_by=actor,
        )
        rows = self._build_export_rows(partner, kind)
        file_path = self._write_export_file(job, rows, export_format)
        job.status = PartnerExportStatus.COMPLETED
        job.file_path = file_path
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "file_path", "finished_at", "updated_at"])
        return job

    def _write_export_file(self, job: PartnerExportJob, rows: list[dict], export_format: str) -> str:
        export_root = getattr(settings, "MEDIA_ROOT", None) or "media"
        export_dir = os.path.join(export_root, "exports", str(job.partner_id))
        os.makedirs(export_dir, exist_ok=True)
        filename = f"{job.kind}-{timezone.now().strftime('%Y%m%d%H%M%S')}.{export_format}"
        full_path = os.path.join(export_dir, filename)
        if export_format == "json":
            with open(full_path, "w", encoding="utf-8") as handle:
                json.dump(rows, handle, ensure_ascii=True, indent=2)
        else:
            fieldnames = sorted({key for row in rows for key in row.keys()})
            with open(full_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
        return os.path.join("exports", str(job.partner_id), filename)

    @action(detail=True, methods=["post"], url_path="admins")
    def add_admin(self, request, pk=None):
        """
        Add or promote a partner admin by user_id.
        """
        partner = self.get_object()
        if partner.owner != request.user:
            return Response(
                {"detail": "Only the partner owner can manage admins (for now)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not partner.main_conversation_id:
            return Response(
                {"detail": "Partner has no main conversation to manage admins."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        from apps.accounts.models import User
        from apps.chat.models import ConversationMember

        user = User.objects.filter(id=user_id).first()
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        member, _ = ConversationMember.objects.get_or_create(
            conversation_id=partner.main_conversation_id,
            user=user,
            defaults={"base_role": BaseConversationRole.ADMIN},
        )
        if member.base_role not in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
            member.base_role = BaseConversationRole.ADMIN
            member.save(update_fields=["base_role"])

        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.admin.add",
            target_type="user",
            target_id=str(user.id),
            request=request,
        )
        dispatch_partner_webhooks(
            partner=partner,
            event="role.changed",
            payload={
                "action": "admin.added",
                "user_id": str(user.id),
                "role": "admin",
                "actor_id": str(request.user.id),
            },
        )
        run_partner_automation_rules(
            partner=partner,
            event="role.changed",
            payload={
                "action": "admin.added",
                "user_id": str(user.id),
                "role": "admin",
                "actor_id": str(request.user.id),
            },
            actor=request.user,
        )

        return Response({"detail": "Admin updated."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="admins/remove")
    def remove_admin(self, request, pk=None):
        """
        Demote an admin to MEMBER.
        """
        partner = self.get_object()
        if partner.owner != request.user:
            return Response(
                {"detail": "Only the partner owner can manage admins (for now)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not partner.main_conversation_id:
            return Response(
                {"detail": "Partner has no main conversation to manage admins."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        member = ConversationMember.objects.filter(
            conversation_id=partner.main_conversation_id,
            user_id=user_id,
            left_at__isnull=True,
        ).first()

        if not member:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)

        if member.base_role == BaseConversationRole.OWNER:
            return Response(
                {"detail": "Owner role cannot be removed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        member.base_role = BaseConversationRole.MEMBER
        member.save(update_fields=["base_role"])

        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.admin.remove",
            target_type="user",
            target_id=str(member.user_id),
            request=request,
        )
        dispatch_partner_webhooks(
            partner=partner,
            event="role.changed",
            payload={
                "action": "admin.removed",
                "user_id": str(member.user_id),
                "role": "member",
                "actor_id": str(request.user.id),
            },
        )
        run_partner_automation_rules(
            partner=partner,
            event="role.changed",
            payload={
                "action": "admin.removed",
                "user_id": str(member.user_id),
                "role": "member",
                "actor_id": str(request.user.id),
            },
            actor=request.user,
        )

        return Response({"detail": "Admin removed."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="settings-catalog")
    def settings_catalog(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view settings.")

        role = self._ui_role(partner, request.user)
        keys = [
            feature["key"]
            for section in PARTNER_SETTINGS_SECTIONS
            for feature in section.get("features", [])
        ]
        flags = PartnerFeatureFlag.objects.filter(
            partner=partner,
            key__in=keys,
        )
        flag_map = {flag.key: flag.is_enabled for flag in flags}

        sections = []
        for section in PARTNER_SETTINGS_SECTIONS:
            features = []
            for feature in section.get("features", []):
                enabled = flag_map.get(feature["key"], True)
                allowed = role in feature.get("access", [])
                features.append(
                    {
                        **feature,
                        "enabled": enabled,
                        "allowed": allowed,
                    }
                )
            sections.append({**section, "features": features})

        return Response(
            {"role": role, "sections": sections},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "post"], url_path="organization-apps")
    def organization_apps(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view organization apps.")

        if partner.slug in LEGACY_DEFAULT_PARTNER_SLUGS:
            ensure_default_organization_app(
                partner,
                app_type=PartnerOrganizationAppType.BIBLE,
                defaults={
                    "name": "KCAN Bible",
                    "slug": "bible",
                    "description": "Official KCAN Bible experience managed by the default partner account.",
                    "module": "partner.bible",
                    "metadata": {"internal": True, "immutable": True, "owner": "kcan"},
                    "status": PartnerOrganizationAppStatus.PUBLISHED,
                    "is_promoted_global": True,
                    "promoted_order": 20,
                    "is_active": True,
                },
            )

        if request.method == "POST":
            if not self._user_can_manage_organization_apps(partner, request.user):
                raise PermissionDenied("Not allowed to manage organization apps.")
            serializer = PartnerOrganizationAppSerializer(data=request.data)
            if not serializer.is_valid():
                logger.warning(
                    "Organization app creation failed validation for partner %s: %s",
                    partner.id,
                    serializer.errors,
                )
                return Response(
                    {"errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            app_data = dict(serializer.validated_data)
            if app_data.get("type") == PartnerOrganizationAppType.BIBLE and partner.slug != KCAN_PARTNER_SLUG:
                raise PermissionDenied("The official Bible app is reserved for KCAN.")
            app_data.pop("is_promoted_global", None)
            app_data.pop("promoted_order", None)
            if app_data.get("status") == PartnerOrganizationAppStatus.PUBLISHED and not app_data.get("published_at"):
                app_data["published_at"] = timezone.now()
            app = PartnerOrganizationApp.objects.create(partner=partner, **app_data)
            logger.info(
                "Created organization app %s for partner %s",
                app.id,
                partner.id,
            )
            return Response(
                {
                    "app": PartnerOrganizationAppSerializer(app).data,
                    "partner_id": str(partner.id),
                },
                status=status.HTTP_201_CREATED,
            )

        apps = get_partner_organization_apps_for_user(partner, request.user)
        serializer = PartnerOrganizationAppSerializer(apps, many=True)
        return Response(
            {
                "apps": serializer.data,
                "partner_id": str(partner.id),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="organization-apps/global")
    def global_organization_apps(self, request):
        apps = (
            PartnerOrganizationApp.objects.filter(
                is_active=True,
                is_promoted_global=True,
                status=PartnerOrganizationAppStatus.PUBLISHED,
            )
            .select_related("partner")
            .prefetch_related("tabs__content_blocks")
            .order_by("promoted_order", "order", "name")
        )
        serializer = PartnerOrganizationAppSerializer(apps, many=True, context={"request": request})
        return Response({"apps": serializer.data}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="organizations")
    def organizations(self, request, pk=None):
        """Shop/Health/Education/Broadcast-Channel entities connected to
        this partner's profile. Reuses apps.websites.owner_resolution's
        polymorphic owner_type/owner_id resolver — see
        PartnerOrganizationLink's model docstring for why this isn't a
        new FK column on four separate apps' models."""
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view this partner's organizations.")

        if request.method == "POST":
            if not self._user_can_manage_partner(partner, request.user):
                raise PermissionDenied("Not allowed to manage this partner's organizations.")
            owner_type = str(request.data.get("owner_type") or "")
            owner_id = str(request.data.get("owner_id") or "")
            if owner_type not in PartnerOrganizationLinkType.values:
                raise ValidationError({"owner_type": f"Must be one of {PartnerOrganizationLinkType.values}."})
            if not owner_id:
                raise ValidationError({"owner_id": "owner_id is required."})

            org = _resolve_linkable_organization(owner_type, owner_id, request.user)
            if org is None:
                raise ValidationError({"owner_id": "That organization doesn't exist, or you don't own it."})

            if PartnerOrganizationLink.objects.filter(owner_type=owner_type, owner_id=owner_id).exclude(partner=partner).exists():
                raise ValidationError({"owner_id": "That organization is already linked to a different partner."})

            link, _ = PartnerOrganizationLink.objects.get_or_create(
                owner_type=owner_type, owner_id=owner_id,
                defaults={"partner": partner, "linked_by": request.user},
            )
            return Response(_serialize_organization_link(link), status=status.HTTP_201_CREATED)

        links = PartnerOrganizationLink.objects.filter(partner=partner).order_by("-created_at")
        return Response({"organizations": [_serialize_organization_link(link) for link in links]})

    @action(detail=True, methods=["get"], url_path="organizations/linkable")
    def linkable_organizations(self, request, pk=None):
        """The requesting user's own institutions/shops/health orgs/
        broadcast channels that are not yet linked to ANY partner —
        populates the "connect an organization" picker."""
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to manage this partner's organizations.")

        linked_ids = set(PartnerOrganizationLink.objects.values_list("owner_id", flat=True))
        results = []
        for owner_type, model in _linkable_models().items():
            owner_field = "owner_user" if owner_type == PartnerOrganizationLinkType.BROADCAST_CHANNEL else "owner"
            for org in model.objects.filter(**{owner_field: request.user}):
                if org.id in linked_ids:
                    continue
                results.append({
                    "owner_type": owner_type,
                    "owner_id": str(org.id),
                    "name": _organization_display_name(owner_type, org),
                })
        return Response({"organizations": results})

    @action(detail=True, methods=["post"], url_path="organizations/unlink")
    def unlink_organization(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to manage this partner's organizations.")
        link_id = str(request.data.get("link_id") or "")
        deleted, _ = PartnerOrganizationLink.objects.filter(id=link_id, partner=partner).delete()
        if not deleted:
            raise ValidationError({"link_id": "No such linked organization."})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="server-categories")
    def server_categories(self, request, pk=None):
        partner = self.get_object()
        if request.method == "POST":
            self._require_permission(partner, request.user, "partner.categories.manage")
            serializer = PartnerServerCategorySerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            category = serializer.save(partner=partner)
            notify_nest_of_partner_event(
                partner_id=str(partner.id),
                event="partner.category_created",
                user_ids=active_partner_member_ids(partner),
                data={"categoryId": str(category.id), "name": category.name},
            )
            return Response(PartnerServerCategorySerializer(category).data, status=status.HTTP_201_CREATED)

        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view server categories.")
        categories = partner.server_categories.order_by("order", "name")
        serializer = PartnerServerCategorySerializer(categories, many=True)
        return Response({"categories": serializer.data}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"server-categories/(?P<category_id>[^/.]+)",
    )
    def server_category_detail(self, request, pk=None, category_id=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.categories.manage")
        category = PartnerServerCategory.objects.filter(id=category_id, partner=partner).first()
        if not category:
            return Response({"detail": "Category not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "DELETE":
            category.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = PartnerServerCategorySerializer(category, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(PartnerServerCategorySerializer(category).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="server-layout")
    def server_layout(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view server layout.")

        categories = list(partner.server_categories.order_by("order", "name"))
        category_serializer = PartnerServerCategorySerializer(categories, many=True)
        channels = (
            partner.channels
            .select_related("conversation", "owner", "community", "category")
            .prefetch_related("permission_overwrites__role", "permission_overwrites__user")
            .filter(is_archived=False)
            .order_by("category__order", "category__name", "order", "name")
        )
        visible_channels = filter_partner_channels_for_user(list(channels), request.user)
        channel_serializer = ChannelDetailSerializer(visible_channels, many=True, context={"request": request})
        return Response(
            {
                "partner_id": str(partner.id),
                "categories": category_serializer.data,
                "channels": channel_serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="discord-summary")
    def discord_summary(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view partner workspace summary.")
        payload = build_partner_discord_summary(partner, request.user)
        return Response(payload, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="organization-apps/(?P<app_id>[^/.]+)/access-log",
    )
    def organization_app_access_log(self, request, pk=None, app_id=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to access organization app logs.")

        try:
            app = partner.organization_apps.get(id=app_id)
        except PartnerOrganizationApp.DoesNotExist:
            raise ValidationError({"app_id": "Invalid organization app ID."})

        if request.method == "GET":
            logs = app.access_logs.select_related("user").order_by("-created_at")[:25]
            serializer = PartnerOrganizationAppAccessLogSerializer(logs, many=True)
            return Response({"logs": serializer.data}, status=status.HTTP_200_OK)

        action_key = request.data.get("action", "view")
        data_scope = request.data.get("data_scope", [])
        consent = bool(request.data.get("consent", False))

        if not request.data.get("action"):
            raise ValidationError({"action": "Action is required to log access."})

        log_entry = log_organization_app_access(
            user=request.user,
            app=app,
            action=action_key,
            data_scope=data_scope,
            consent=consent,
        )
        serializer = PartnerOrganizationAppAccessLogSerializer(log_entry)
        return Response({"log": serializer.data}, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="organization-apps/(?P<app_id>[^/.]+)/tabs",
    )
    def organization_app_tabs(self, request, pk=None, app_id=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to access organization app tabs.")
        app = partner.organization_apps.filter(id=app_id).first()
        if not app:
            raise ValidationError({"app_id": "Invalid organization app ID."})

        if request.method == "POST":
            if not self._user_can_manage_organization_apps(partner, request.user):
                raise PermissionDenied("Not allowed to manage organization app tabs.")
            serializer = PartnerOrganizationAppTabSerializer(data=request.data, context={"request": request})
            serializer.is_valid(raise_exception=True)
            tab = serializer.save(app=app)
            return Response({"tab": PartnerOrganizationAppTabSerializer(tab, context={"request": request}).data}, status=status.HTTP_201_CREATED)

        tabs = app.tabs.filter(is_active=True).order_by("order", "title")
        if not self._user_can_manage_organization_apps(partner, request.user):
            roles = get_partner_user_roles(partner, request.user)
            tabs = [tab for tab in tabs if set(tab.visible_to or []).intersection(roles)]
        serializer = PartnerOrganizationAppTabSerializer(tabs, many=True, context={"request": request})
        return Response({"tabs": serializer.data}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="organization-apps/(?P<app_id>[^/.]+)/tabs/(?P<tab_id>[^/.]+)/blocks",
    )
    def organization_app_tab_blocks(self, request, pk=None, app_id=None, tab_id=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to access organization app content.")
        tab = PartnerOrganizationAppTab.objects.filter(id=tab_id, app_id=app_id, app__partner=partner).first()
        if not tab:
            raise ValidationError({"tab_id": "Invalid organization app tab ID."})

        if request.method == "POST":
            if not self._user_can_manage_organization_apps(partner, request.user):
                raise PermissionDenied("Not allowed to manage organization app content.")
            serializer = PartnerOrganizationAppContentBlockSerializer(
                data=request.data,
                context={"request": request},
            )
            serializer.is_valid(raise_exception=True)
            block = serializer.save(tab=tab, created_by=request.user)
            if block.status == PartnerOrganizationAppStatus.PUBLISHED and not block.published_at:
                block.published_at = timezone.now()
                block.save(update_fields=["published_at", "updated_at"])
            return Response(
                {"block": PartnerOrganizationAppContentBlockSerializer(block, context={"request": request}).data},
                status=status.HTTP_201_CREATED,
            )

        blocks = tab.content_blocks.filter(is_active=True).order_by("order", "created_at")
        if not self._user_can_manage_organization_apps(partner, request.user):
            blocks = blocks.filter(status=PartnerOrganizationAppStatus.PUBLISHED)
        serializer = PartnerOrganizationAppContentBlockSerializer(blocks, many=True, context={"request": request})
        return Response({"blocks": serializer.data}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["patch"],
        url_path="organization-apps/(?P<app_id>[^/.]+)/promote",
    )
    def organization_app_promote(self, request, pk=None, app_id=None):
        partner = self.get_object()
        if not self._user_can_promote_global_apps(partner, request.user):
            raise PermissionDenied("Only KCAN/platform admins can promote apps into the main app navigation.")
        app = partner.organization_apps.filter(id=app_id).first()
        if not app:
            raise ValidationError({"app_id": "Invalid organization app ID."})
        app.is_promoted_global = bool(request.data.get("is_promoted_global", True))
        app.promoted_order = int(request.data.get("promoted_order", app.promoted_order or 0))
        app.status = request.data.get("status", app.status)
        if app.status == PartnerOrganizationAppStatus.PUBLISHED and not app.published_at:
            app.published_at = timezone.now()
        app.save(update_fields=["is_promoted_global", "promoted_order", "status", "published_at", "updated_at"])
        return Response({"app": PartnerOrganizationAppSerializer(app, context={"request": request}).data}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path="organization-apps/(?P<app_id>[^/.]+)",
    )
    def organization_app_detail(self, request, pk=None, app_id=None):
        partner = self.get_object()
        if not self._user_can_manage_organization_apps(partner, request.user):
            raise PermissionDenied("Not allowed to manage organization apps.")
        try:
            app = partner.organization_apps.get(id=app_id)
        except PartnerOrganizationApp.DoesNotExist:
            raise ValidationError({"app_id": "Invalid organization app ID."})

        if request.method == "DELETE":
            app.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = PartnerOrganizationAppSerializer(app, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        app_data = dict(serializer.validated_data)
        app_data.pop("is_promoted_global", None)
        app_data.pop("promoted_order", None)
        instance = serializer.save(**app_data)
        if instance.status == PartnerOrganizationAppStatus.PUBLISHED and not instance.published_at:
            instance.published_at = timezone.now()
            instance.save(update_fields=["published_at", "updated_at"])
        return Response({"app": serializer.data}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path="organization-apps/(?P<app_id>[^/.]+)/tabs/(?P<tab_id>[^/.]+)",
    )
    def organization_app_tab_detail(self, request, pk=None, app_id=None, tab_id=None):
        partner = self.get_object()
        if not self._user_can_manage_organization_apps(partner, request.user):
            raise PermissionDenied("Not allowed to manage organization app tabs.")
        tab = PartnerOrganizationAppTab.objects.filter(id=tab_id, app_id=app_id, app__partner=partner).first()
        if not tab:
            raise ValidationError({"tab_id": "Invalid organization app tab ID."})

        if request.method == "DELETE":
            tab.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = PartnerOrganizationAppTabSerializer(tab, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"tab": serializer.data}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path="organization-apps/(?P<app_id>[^/.]+)/tabs/(?P<tab_id>[^/.]+)/blocks/(?P<block_id>[^/.]+)",
    )
    def organization_app_block_detail(self, request, pk=None, app_id=None, tab_id=None, block_id=None):
        partner = self.get_object()
        if not self._user_can_manage_organization_apps(partner, request.user):
            raise PermissionDenied("Not allowed to manage organization app content.")
        block = PartnerOrganizationAppContentBlock.objects.filter(
            id=block_id, tab_id=tab_id, tab__app_id=app_id, tab__app__partner=partner
        ).first()
        if not block:
            raise ValidationError({"block_id": "Invalid content block ID."})

        if request.method == "DELETE":
            block.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = PartnerOrganizationAppContentBlockSerializer(block, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        if instance.status == PartnerOrganizationAppStatus.PUBLISHED and not getattr(instance, "published_at", None):
            instance.published_at = timezone.now()
            instance.save(update_fields=["published_at", "updated_at"])
        return Response({"block": serializer.data}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"], url_path="settings")
    def update_settings(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to update settings.")

        updates = request.data.get("updates", [])
        if not isinstance(updates, list):
            raise ValidationError({"updates": "Expected a list of updates."})

        allowed_keys = {
            feature["key"]
            for section in PARTNER_SETTINGS_SECTIONS
            for feature in section.get("features", [])
        }

        changed = 0
        for update in updates:
            key = update.get("key")
            enabled = update.get("enabled")
            if key not in allowed_keys or not isinstance(enabled, bool):
                continue
            PartnerFeatureFlag.objects.update_or_create(
                partner=partner,
                key=key,
                defaults={"is_enabled": enabled},
            )
            changed += 1

        return Response({"updated": changed}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="settings-config")
    def settings_config_list(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.settings.view")
        qs = PartnerSetting.objects.filter(partner=partner).order_by("key")
        return Response(PartnerSettingSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "patch"], url_path=r"settings-config/(?P<key>[^/.]+)")
    def settings_config_detail(self, request, pk=None, key=None):
        partner = self.get_object()
        allowed_keys = {
            feature["key"]
            for section in PARTNER_SETTINGS_SECTIONS
            for feature in section.get("features", [])
        }
        if key not in allowed_keys:
            raise ValidationError({"key": "Unknown settings key."})

        if request.method == "GET":
            self._require_permission(partner, request.user, "partner.settings.view")
            setting = PartnerSetting.objects.filter(partner=partner, key=key).first()
            if not setting:
                default_config = {}
                if key == LANDING_PAGE_BUILDER_KEY:
                    default_config = _normalize_landing_builder_config({})
                setting = PartnerSetting.objects.create(
                    partner=partner,
                    key=key,
                    config=default_config,
                    updated_by=request.user,
                )
            elif key == LANDING_PAGE_BUILDER_KEY:
                normalized = _normalize_landing_builder_config(setting.config or {})
                if normalized != (setting.config or {}):
                    setting.config = normalized
                    setting.updated_by = request.user
                    setting.save(update_fields=["config", "updated_by", "updated_at"])
            return Response(PartnerSettingSerializer(setting).data, status=status.HTTP_200_OK)

        self._require_permission(partner, request.user, "partner.settings.manage")
        setting, _ = PartnerSetting.objects.get_or_create(
            partner=partner,
            key=key,
            defaults={"config": {}, "updated_by": request.user},
        )
        payload = request.data.copy() if hasattr(request.data, "copy") else dict(request.data or {})
        if key == LANDING_PAGE_BUILDER_KEY:
            payload["config"] = _normalize_landing_builder_config(payload.get("config"))
        serializer = PartnerSettingSerializer(setting, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.settings.update",
            target_type="partner_setting",
            target_id=key,
            metadata={"fields": list((request.data or {}).keys())},
            request=request,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "patch"], url_path="organization-profile")
    def organization_profile(self, request, pk=None):
        partner = self.get_object()
        if request.method == "GET":
            self._require_permission(partner, request.user, "partner.settings.view")
            profile, _ = PartnerOrganizationProfile.objects.get_or_create(
                partner=partner,
                defaults={
                    "display_name": partner.name,
                    "updated_by": request.user,
                },
            )
            return Response(PartnerOrganizationProfileSerializer(profile).data, status=status.HTTP_200_OK)

        self._require_permission(partner, request.user, "partner.settings.manage")
        profile, _ = PartnerOrganizationProfile.objects.get_or_create(
            partner=partner,
            defaults={
                "display_name": partner.name,
                "updated_by": request.user,
            },
        )
        serializer = PartnerOrganizationProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.organization_profile.update",
            target_type="partner_organization_profile",
            target_id=str(partner.id),
            request=request,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="verification-status")
    def verification_status(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view partner verification status.")
        return Response(current_partner_verification_status(partner), status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="verification/start")
    def verification_start(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.settings.manage")
        serializer = PartnerVerificationStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case = start_partner_verification_case(
            partner=partner,
            actor=request.user,
            provider=serializer.validated_data.get("provider") or "",
            evidence_metadata=serializer.validated_data.get("evidence_metadata") or {},
        )
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.verification.start",
            target_type="verification_case",
            target_id=str(case.id) if case else "",
            request=request,
        )
        return Response(
            {
                "case": serialize_case_status(case),
                "status": current_partner_verification_status(partner),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="verification/cases/(?P<case_id>[^/.]+)/review")
    def verification_review(self, request, pk=None, case_id=None):
        partner = self.get_object()
        if not request.user.is_staff:
            raise PermissionDenied("Only staff reviewers can review partner verification cases.")
        case = VerificationCase.objects.select_related("subject").filter(
            id=case_id,
            subject__subject_type=VerificationSubjectType.PARTNER,
            subject__subject_id=partner.id,
        ).first()
        if not case:
            raise ValidationError({"case_id": "Invalid partner verification case."})
        serializer = PartnerVerificationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case, badges = review_partner_case(
            case=case,
            actor=request.user,
            action=serializer.validated_data["action"],
            notes=serializer.validated_data.get("notes", ""),
            badge_codes=serializer.validated_data.get("badge_codes") or None,
        )
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.verification.review",
            target_type="verification_case",
            target_id=str(case.id),
            metadata={"review_action": serializer.validated_data["action"], "badge_codes": [badge.code for badge in badges]},
            request=request,
        )
        return Response(
            {
                "case": serialize_case_status(case),
                "badges": [{"code": badge.code, "label": badge.label, "level": badge.level} for badge in badges],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="public-hub")
    def public_hub(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view the partner hub.")
        return Response(self._public_hub_payload(partner), status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="differentiator-insights")
    def differentiator_insights(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.reports.view")
        return Response(self._differentiator_insights_payload(partner), status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="team-structure")
    def team_structure(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.roles.view")
        return Response(self._team_structure_payload(partner), status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="automation-recipes")
    def automation_recipes(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.automation.view")
        return Response({"recipes": self._automation_recipe_payload(partner)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="automation-recipes/apply")
    def automation_recipe_apply(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.automation.manage")
        recipe_key = str(request.data.get("recipe_key") or "").strip()
        if not recipe_key:
            raise ValidationError({"recipe_key": "recipe_key is required."})

        recipe = next((item for item in self._automation_recipe_payload(partner) if item["key"] == recipe_key), None)
        if not recipe:
            raise ValidationError({"recipe_key": "Unknown automation recipe."})

        rule = PartnerAutomationRule.objects.create(
            partner=partner,
            name=recipe["title"],
            description=recipe["description"],
            trigger=recipe["trigger"],
            conditions=recipe["conditions"],
            actions=recipe["actions"],
            is_active=True,
            created_by=request.user,
        )
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.automation.recipe.apply",
            target_type="partner_automation_rule",
            target_id=str(rule.id),
            metadata={"recipe_key": recipe_key},
            request=request,
        )
        return Response(PartnerAutomationRuleSerializer(rule).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="experience-templates")
    def experience_templates(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view experience templates.")
        return Response({"templates": self._experience_templates_payload(partner)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"], url_path="join-config")
    def update_join_config(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to update join criteria.")

        config, _ = PartnerJoinConfig.objects.get_or_create(partner=partner)
        fields = [
            "allow_public_listing",
            "allow_apply",
            "allow_subscribe",
            "auto_approve",
            "require_profile",
            "methods",
            "criteria",
        ]
        updates = {}
        for field in fields:
            if field in request.data:
                updates[field] = request.data.get(field)
        for key, value in updates.items():
            setattr(config, key, value)
        config.save()

        return Response({"detail": "Join config updated."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "patch"], url_path="screening")
    def screening(self, request, pk=None):
        partner = self.get_object()
        config, _ = PartnerJoinConfig.objects.get_or_create(partner=partner)
        criteria = dict(config.criteria or {})

        if request.method == "GET":
            if not self._user_can_access_partner(partner, request.user):
                raise PermissionDenied("Not allowed to view screening settings.")
            return Response(
                {
                    "enabled": bool(criteria.get("screening_enabled", False)),
                    "require_rules_acceptance": bool(criteria.get("require_rules_acceptance", False)),
                    "rules_text": criteria.get("rules_text") or criteria.get("rules") or "",
                    "screening_questions": criteria.get("screening_questions") or [],
                },
                status=status.HTTP_200_OK,
            )

        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to manage screening settings.")

        if "enabled" in request.data:
            criteria["screening_enabled"] = bool(request.data.get("enabled"))
        if "require_rules_acceptance" in request.data:
            criteria["require_rules_acceptance"] = bool(request.data.get("require_rules_acceptance"))
        if "rules_text" in request.data:
            criteria["rules_text"] = str(request.data.get("rules_text") or "").strip()
        if "screening_questions" in request.data:
            questions = request.data.get("screening_questions") or []
            if not isinstance(questions, list):
                raise ValidationError({"screening_questions": "Expected a list."})
            criteria["screening_questions"] = questions

        config.criteria = criteria
        config.save(update_fields=["criteria", "updated_at"])
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.screening.update",
            target_type="partner_join_config",
            target_id=str(config.id),
            metadata={"keys": list((request.data or {}).keys())},
            request=request,
        )
        return Response(
            {
                "enabled": bool(criteria.get("screening_enabled", False)),
                "require_rules_acceptance": bool(criteria.get("require_rules_acceptance", False)),
                "rules_text": criteria.get("rules_text") or "",
                "screening_questions": criteria.get("screening_questions") or [],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "patch"], url_path="notification-preferences")
    def notification_preferences(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to manage notification preferences.")

        valid_levels = set(ConversationNotificationLevel.values)

        if request.method == "PATCH":
            server_level = request.data.get("server_notification_level")
            if server_level is not None:
                if server_level not in valid_levels:
                    raise ValidationError({"server_notification_level": "Invalid notification level."})
                if not partner.main_conversation_id:
                    raise ValidationError({"server_notification_level": "Partner has no main conversation."})
                ConversationMember.objects.update_or_create(
                    conversation_id=partner.main_conversation_id,
                    user=request.user,
                    defaults={
                        "base_role": BaseConversationRole.READONLY,
                        "left_at": None,
                        "notification_level": server_level,
                    },
                )

            channel_updates = request.data.get("channel_notifications")
            if channel_updates is not None:
                if not isinstance(channel_updates, list):
                    raise ValidationError({"channel_notifications": "Expected a list."})
                channel_map = {
                    str(channel.id): channel
                    for channel in filter_partner_channels_for_user(
                        list(
                            partner.channels.select_related("conversation", "category").filter(is_archived=False)
                        ),
                        request.user,
                    )
                }
                for update in channel_updates:
                    channel_id = str(update.get("channel_id") or "")
                    level = update.get("notification_level")
                    if not channel_id or level not in valid_levels:
                        raise ValidationError({"channel_notifications": "Invalid channel notification payload."})
                    channel = channel_map.get(channel_id)
                    if not channel or not channel.conversation_id:
                        raise ValidationError({"channel_notifications": f"Invalid channel: {channel_id}"})
                    base_role = (
                        BaseConversationRole.MEMBER
                        if channel.can_post
                        else BaseConversationRole.READONLY
                    )
                    ConversationMember.objects.update_or_create(
                        conversation_id=channel.conversation_id,
                        user=request.user,
                        defaults={
                            "base_role": base_role,
                            "left_at": None,
                            "notification_level": level,
                        },
                    )

        server_member = None
        if partner.main_conversation_id:
            server_member = ConversationMember.objects.filter(
                conversation_id=partner.main_conversation_id,
                user=request.user,
                left_at__isnull=True,
            ).first()

        visible_channels = filter_partner_channels_for_user(
            list(partner.channels.select_related("conversation", "category").filter(is_archived=False)),
            request.user,
        )
        channel_members = {
            str(member.conversation_id): member
            for member in ConversationMember.objects.filter(
                user=request.user,
                left_at__isnull=True,
                conversation_id__in=[channel.conversation_id for channel in visible_channels if channel.conversation_id],
            )
        }
        return Response(
            {
                "server_notification_level": getattr(
                    server_member,
                    "notification_level",
                    ConversationNotificationLevel.ALL,
                ),
                "channel_notifications": [
                    {
                        "channel_id": str(channel.id),
                        "channel_name": channel.name,
                        "notification_level": getattr(
                            channel_members.get(str(channel.conversation_id)),
                            "notification_level",
                            ConversationNotificationLevel.ALL,
                        ),
                    }
                    for channel in visible_channels
                ],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "post"], url_path="invites")
    def invites(self, request, pk=None):
        partner = self.get_object()
        if request.method == "GET":
            if not self._user_can_manage_partner(partner, request.user):
                raise PermissionDenied("Not allowed to view invites.")
            invites = partner.invites.select_related("created_by").order_by("-created_at")
            return Response(PartnerInviteSerializer(invites, many=True).data, status=status.HTTP_200_OK)

        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to create invites.")
        serializer = PartnerInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested_role = str(serializer.validated_data.get("membership_role") or "member").strip().lower()
        if requested_role != "subscriber":
            # The ORG's own plan, not whoever happens to own it — same fix
            # as _require_partner_feature, see PartnerSubscription's
            # docstring.
            org_features = get_partner_tier_features(partner)
            seat_limit = normalize_limit_value(org_features.get("team_seats"), default=0)
            if seat_limit is not None:
                existing_seat_count = PartnerMembership.objects.filter(
                    partner=partner, status=PartnerMembershipStatus.MEMBER
                ).count()
                if existing_seat_count >= seat_limit:
                    raise PermissionDenied(
                        f"This partner organization has reached its team seat limit ({seat_limit}). "
                        "Upgrade your plan to invite more team members."
                    )
        invite = serializer.save(partner=partner, created_by=request.user)
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.invite.create",
            target_type="partner_invite",
            target_id=str(invite.id),
            metadata={"code": invite.code, "max_uses": invite.max_uses},
            request=request,
        )
        return Response(PartnerInviteSerializer(invite).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch", "delete"], url_path=r"invites/(?P<invite_id>[^/.]+)")
    def invite_detail(self, request, pk=None, invite_id=None):
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to manage invites.")
        invite = PartnerInvite.objects.filter(id=invite_id, partner=partner).first()
        if not invite:
            return Response({"detail": "Invite not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "DELETE":
            invite.is_active = False
            invite.save(update_fields=["is_active", "updated_at"])
            log_partner_audit(
                partner=partner,
                actor=request.user,
                action="partner.invite.disable",
                target_type="partner_invite",
                target_id=str(invite.id),
                metadata={"code": invite.code},
                request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = PartnerInviteSerializer(invite, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.invite.update",
            target_type="partner_invite",
            target_id=str(invite.id),
            metadata={"fields": list((request.data or {}).keys())},
            request=request,
        )
        return Response(PartnerInviteSerializer(invite).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="redeem-invite")
    def redeem_invite(self, request):
        code = str(request.data.get("code") or "").strip().upper()
        if not code:
            raise ValidationError({"code": "Invite code is required."})

        with transaction.atomic():
            invite = PartnerInvite.objects.select_for_update().select_related("partner").filter(code=code).first()
            if not invite:
                return Response({"detail": "Invite not found."}, status=status.HTTP_404_NOT_FOUND)
            if not invite.is_redeemable():
                return Response({"detail": "Invite is no longer redeemable."}, status=status.HTTP_400_BAD_REQUEST)

            partner = invite.partner
            membership = self._get_membership(partner, request.user)
            if membership and membership.is_banned:
                raise PermissionDenied("You are banned from this partner.")

            normalized_role = (invite.membership_role or "member").strip().lower()
            status_value = (
                PartnerMembershipStatus.SUBSCRIBER
                if normalized_role == "subscriber"
                else PartnerMembershipStatus.MEMBER
            )
            if status_value == PartnerMembershipStatus.MEMBER:
                # Locks the Partner row itself (not just this invite) so
                # two people redeeming DIFFERENT invite codes for the SAME
                # partner at the same moment still serialize against each
                # other for this check — without it, both could pass
                # "count < seat_limit" before either's membership row
                # existed, overshooting the limit. select_for_update()
                # on invite above only protects concurrent redemptions of
                # this ONE code.
                Partner.objects.select_for_update().get(pk=partner.pk)
                # The ORG's own plan, not the owner's personal one — same
                # fix as _require_partner_feature, see PartnerSubscription's
                # docstring.
                org_features = get_partner_tier_features(partner)
                seat_limit = normalize_limit_value(org_features.get("team_seats"), default=0)
                if seat_limit is not None:
                    existing_seat_count = PartnerMembership.objects.filter(
                        partner=partner, status=PartnerMembershipStatus.MEMBER
                    ).exclude(user_id=request.user.id).count()
                    if existing_seat_count >= seat_limit:
                        raise PermissionDenied(
                            f"This partner organization has reached its team seat limit ({seat_limit}). "
                            "Ask the owner to upgrade their plan to add more team members."
                        )
            self._activate_partner_membership(
                partner=partner,
                user=request.user,
                status_value=status_value,
                membership_role=normalized_role,
            )
            if invite.auto_assign:
                self._auto_assign_membership(partner, request.user, invite.auto_assign)
            progress = self._upsert_onboarding_progress(
                partner=partner,
                user=request.user,
                invite=invite,
                defaults={"onboarding_snapshot": self._partner_onboarding_config(partner)},
            )
            invite.use_count += 1
            invite.save(update_fields=["use_count", "updated_at"])

        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.invite.redeem",
            target_type="partner_invite",
            target_id=str(invite.id),
            metadata={"code": invite.code},
            request=request,
        )
        dispatch_partner_webhooks(
            partner=partner,
            event="member.joined",
            payload={
                "user_id": str(request.user.id),
                "status": status_value,
                "role": normalized_role,
                "invite_id": str(invite.id),
            },
        )
        notify_nest_of_partner_event(
            partner_id=str(partner.id),
            event="partner.invite_redeemed",
            user_ids=active_partner_member_ids(partner),
            data={"userId": str(request.user.id), "role": normalized_role},
        )
        run_partner_automation_rules(
            partner=partner,
            event="member.joined",
            payload={
                "user_id": str(request.user.id),
                "status": status_value,
                "role": normalized_role,
                "invite_id": str(invite.id),
            },
            actor=request.user,
        )
        return Response(
            {
                "detail": "Invite redeemed.",
                "partner_id": str(partner.id),
                "onboarding": PartnerOnboardingProgressSerializer(progress).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="onboarding")
    def onboarding(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to access onboarding.")
        progress = self._upsert_onboarding_progress(partner=partner, user=request.user)
        return Response(
            {
                "config": self._partner_onboarding_config(partner),
                "progress": PartnerOnboardingProgressSerializer(progress).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="onboarding/complete")
    def complete_onboarding(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to complete onboarding.")

        onboarding_config = self._partner_onboarding_config(partner)
        allowed_role_ids = {item["id"] for item in onboarding_config["role_options"]}
        selected_role_ids = request.data.get("role_ids") or []
        selected_channel_ids = request.data.get("channel_ids") or onboarding_config["default_channel_ids"]
        accept_rules = bool(request.data.get("accept_rules"))

        if not isinstance(selected_role_ids, list):
            raise ValidationError({"role_ids": "Expected a list of role ids."})
        if not isinstance(selected_channel_ids, list):
            raise ValidationError({"channel_ids": "Expected a list of channel ids."})

        invalid_role_ids = [role_id for role_id in selected_role_ids if str(role_id) not in allowed_role_ids]
        if invalid_role_ids:
            raise ValidationError({"role_ids": f"Unsupported onboarding role ids: {invalid_role_ids}"})

        progress = self._upsert_onboarding_progress(
            partner=partner,
            user=request.user,
            defaults={
                "selected_role_ids": [str(role_id) for role_id in selected_role_ids],
                "selected_channel_ids": [str(channel_id) for channel_id in selected_channel_ids],
                "onboarding_snapshot": onboarding_config,
            },
        )

        if accept_rules:
            progress.rules_accepted_at = timezone.now()
        for role in PartnerRole.objects.filter(partner=partner, id__in=selected_role_ids):
            PartnerRoleAssignment.objects.get_or_create(
                partner=partner,
                role=role,
                user=request.user,
                defaults={"scope_type": PartnerRoleAssignment.SCOPE_GLOBAL, "scope_id": ""},
            )
        if selected_channel_ids:
            self._auto_assign_membership(
                partner,
                request.user,
                {"channels": selected_channel_ids},
            )
        progress.completed_at = timezone.now()
        progress.save(
            update_fields=[
                "rules_accepted_at",
                "completed_at",
                "updated_at",
            ]
        )
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.onboarding.complete",
            target_type="partner_onboarding_progress",
            target_id=str(progress.id),
            metadata={"selected_role_ids": progress.selected_role_ids, "selected_channel_ids": progress.selected_channel_ids},
            request=request,
        )
        return Response(PartnerOnboardingProgressSerializer(progress).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="apply")
    def apply(self, request, pk=None):
        partner = self.get_object()
        config = getattr(partner, "join_config", None)
        if config and not config.allow_apply:
            raise PermissionDenied("Applications are disabled for this partner.")
        membership = self._get_membership(partner, request.user)
        if membership and membership.is_banned:
            raise PermissionDenied("You are banned from this partner.")

        job_post = None
        job_post_id = request.data.get("job_post")
        if job_post_id:
            job_post = PartnerJobPost.objects.filter(
                id=job_post_id,
                partner=partner,
                is_active=True,
            ).first()

        serializer = PartnerApplicationSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        steps = job_post.steps if job_post else []
        method = serializer.validated_data.get("method") or "application"
        if config and config.methods and method not in config.methods:
            raise PermissionDenied("This join method is not available.")

        application = PartnerApplication.objects.create(
            partner=partner,
            job_post=job_post,
            user=request.user,
            method=method,
            message=serializer.validated_data.get("message", ""),
            answers=serializer.validated_data.get("answers", {}),
            profile_visible=serializer.validated_data.get("profile_visible", True),
            status=PartnerApplicationStatus.PENDING,
            stage_index=0,
            stage_state={"steps": steps, "current": 0},
        )

        PartnerMembership.objects.update_or_create(
            partner=partner,
            user=request.user,
            defaults={"status": PartnerMembershipStatus.PENDING, "role": "member", "is_banned": False, "banned_at": None},
        )

        if config and config.auto_approve:
            application.status = PartnerApplicationStatus.APPROVED
            application.save(update_fields=["status", "updated_at"])
            self._activate_partner_membership(
                partner=partner,
                user=request.user,
                status_value=PartnerMembershipStatus.MEMBER,
                membership_role="member",
            )
            if job_post:
                self._auto_assign_membership(partner, request.user, job_post.auto_assign)
            self._upsert_onboarding_progress(partner=partner, user=request.user)
            dispatch_partner_webhooks(
                partner=partner,
                event="member.joined",
                payload={
                    "user_id": str(request.user.id),
                    "status": PartnerMembershipStatus.MEMBER,
                    "role": "member",
                },
            )
            run_partner_automation_rules(
                partner=partner,
                event="member.joined",
                payload={
                    "user_id": str(request.user.id),
                    "status": PartnerMembershipStatus.MEMBER,
                    "role": "member",
                },
                actor=request.user,
            )

        return Response(PartnerApplicationSerializer(application).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="jobs")
    def jobs(self, request, pk=None):
        partner = self.get_object()
        if request.method.lower() == "get":
            qs = PartnerJobPost.objects.filter(partner=partner).order_by("-created_at")
            return Response(
                PartnerJobPostSerializer(qs, many=True).data,
                status=status.HTTP_200_OK,
            )

        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to manage job posts.")
        self._require_partner_feature(partner, "job_posting", "Job posting is available on the Partner and Partner Pro plans.")

        serializer = PartnerJobPostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = PartnerJobPost.objects.create(
            partner=partner,
            title=serializer.validated_data.get("title", ""),
            description=serializer.validated_data.get("description", ""),
            requirements=serializer.validated_data.get("requirements", ""),
            location=serializer.validated_data.get("location", ""),
            is_remote=serializer.validated_data.get("is_remote", False),
            job_type=serializer.validated_data.get("job_type", "full_time"),
            salary_min_cents=serializer.validated_data.get("salary_min_cents"),
            salary_max_cents=serializer.validated_data.get("salary_max_cents"),
            salary_currency=serializer.validated_data.get("salary_currency", "USD"),
            tags=serializer.validated_data.get("tags", []),
            steps=serializer.validated_data.get("steps", []),
            auto_assign=serializer.validated_data.get("auto_assign", {}),
            is_active=serializer.validated_data.get("is_active", True),
        )
        return Response(PartnerJobPostSerializer(job).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path="jobs/(?P<job_id>[^/.]+)")
    def update_job(self, request, pk=None, job_id=None):
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to manage job posts.")
        self._require_partner_feature(partner, "job_posting", "Job posting is available on the Partner and Partner Pro plans.")

        job = PartnerJobPost.objects.filter(partner=partner, id=job_id).first()
        if not job:
            return Response({"detail": "Job post not found."}, status=status.HTTP_404_NOT_FOUND)

        fields = [
            "title", "description", "requirements", "location", "is_remote", "job_type",
            "salary_min_cents", "salary_max_cents", "salary_currency", "tags",
            "steps", "auto_assign", "is_active",
        ]
        for field in fields:
            if field in request.data:
                setattr(job, field, request.data.get(field))
        job.save()
        return Response(PartnerJobPostSerializer(job).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="applications")
    def list_applications(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view applications.")

        qs = PartnerApplication.objects.filter(partner=partner).select_related("user", "user__profile").order_by("-created_at")
        return Response(
            PartnerApplicationDetailSerializer(qs, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="subscribe")
    def subscribe(self, request, pk=None):
        partner = self.get_object()
        config = getattr(partner, "join_config", None)
        if config and not config.allow_subscribe:
            raise PermissionDenied("Subscriptions are disabled for this partner.")
        membership = self._get_membership(partner, request.user)
        if membership and membership.is_banned:
            raise PermissionDenied("You are banned from this partner.")

        self._activate_partner_membership(
            partner=partner,
            user=request.user,
            status_value=PartnerMembershipStatus.SUBSCRIBER,
            membership_role="subscriber",
        )
        self._upsert_onboarding_progress(partner=partner, user=request.user)
        dispatch_partner_webhooks(
            partner=partner,
            event="member.joined",
            payload={
                "user_id": str(request.user.id),
                "status": PartnerMembershipStatus.SUBSCRIBER,
                "role": "subscriber",
            },
        )
        run_partner_automation_rules(
            partner=partner,
            event="member.joined",
            payload={
                "user_id": str(request.user.id),
                "status": PartnerMembershipStatus.SUBSCRIBER,
                "role": "subscriber",
            },
            actor=request.user,
        )

        return Response({"detail": "Subscribed."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="applications/(?P<app_id>[^/.]+)/approve")
    def approve_application(self, request, pk=None, app_id=None):
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to approve applications.")

        application = PartnerApplication.objects.filter(id=app_id, partner=partner).first()
        if not application:
            return Response({"detail": "Application not found."}, status=status.HTTP_404_NOT_FOUND)

        application.status = PartnerApplicationStatus.APPROVED
        application.save(update_fields=["status", "updated_at"])

        self._activate_partner_membership(
            partner=partner,
            user=application.user,
            status_value=PartnerMembershipStatus.MEMBER,
            membership_role="member",
        )
        if application.job_post_id:
            self._auto_assign_membership(partner, application.user, application.job_post.auto_assign)
        self._upsert_onboarding_progress(partner=partner, user=application.user)
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.application.approve",
            target_type="partner_application",
            target_id=str(application.id),
            metadata={"user_id": str(application.user_id)},
            request=request,
        )
        dispatch_partner_webhooks(
            partner=partner,
            event="member.joined",
            payload={
                "user_id": str(application.user_id),
                "status": PartnerMembershipStatus.MEMBER,
                "role": "member",
            },
        )
        run_partner_automation_rules(
            partner=partner,
            event="member.joined",
            payload={
                "user_id": str(application.user_id),
                "status": PartnerMembershipStatus.MEMBER,
                "role": "member",
            },
            actor=request.user,
        )

        return Response({"detail": "Application approved."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="applications/(?P<app_id>[^/.]+)/reject")
    def reject_application(self, request, pk=None, app_id=None):
        """Applications only ever had an approve path — PartnerApplicationStatus.REJECTED
        was a defined status nothing could ever set, so a manager reviewing
        applicants had no way to decline one; it just sat pending forever
        (an applicant's own withdraw_application is the only other exit)."""
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to reject applications.")

        application = PartnerApplication.objects.filter(id=app_id, partner=partner).first()
        if not application:
            return Response({"detail": "Application not found."}, status=status.HTTP_404_NOT_FOUND)
        if application.status != PartnerApplicationStatus.PENDING:
            raise ValidationError({"detail": "Only pending applications can be rejected."})

        application.status = PartnerApplicationStatus.REJECTED
        application.save(update_fields=["status", "updated_at"])
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.application.reject",
            target_type="partner_application",
            target_id=str(application.id),
            metadata={"user_id": str(application.user_id)},
            request=request,
        )
        return Response({"detail": "Application rejected."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_access_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view members.")
        queryset = self._member_directory_queryset(partner)
        page = self.paginate_queryset(queryset)
        payload = self._member_directory_entries(partner, page if page is not None else queryset[:100])
        serializer = PartnerMemberDirectoryEntrySerializer(payload, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response({"members": serializer.data}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="moderation-actions")
    def moderation_actions(self, request, pk=None):
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to view moderation actions.")
        actions = partner.moderation_actions.select_related("actor", "user", "membership").order_by("-created_at")[:100]
        return Response(PartnerModerationActionSerializer(actions, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"], url_path=r"members/(?P<member_user_id>[^/.]+)")
    def update_member(self, request, pk=None, member_user_id=None):
        """Change an existing member's core team role (member/manager/admin —
        never "owner", which is the single Partner.owner FK, not assignable
        here). This endpoint didn't exist anywhere before — the only paths
        that ever set PartnerMembership.role were invite redemption and
        application approval, so an owner/manager had no way to promote or
        demote an existing member without recreating their membership."""
        partner = self.get_object()
        if not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to manage members.")
        membership = PartnerMembership.objects.filter(partner=partner, user_id=member_user_id).first()
        if not membership:
            return Response({"detail": "Partner membership not found."}, status=status.HTTP_404_NOT_FOUND)
        if partner.owner_id == membership.user_id:
            raise PermissionDenied("The partner owner's role cannot be changed here.")

        new_role = str(request.data.get("role") or "").strip().lower()
        allowed_roles = {"member", "manager", "admin"}
        if new_role not in allowed_roles:
            raise ValidationError({"role": f"role must be one of: {', '.join(sorted(allowed_roles))}."})

        previous_role = membership.role
        membership.role = new_role
        membership.save(update_fields=["role", "updated_at"])
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action="partner.member.role_changed",
            target_type="partner_membership",
            target_id=str(membership.id),
            metadata={"user_id": str(membership.user_id), "previous_role": previous_role, "new_role": new_role},
            request=request,
        )
        entry = self._member_directory_entry_for_user(partner, membership.user_id)
        serializer = PartnerMemberDirectoryEntrySerializer(entry) if entry else None
        return Response({"member": serializer.data if serializer else None}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path=r"members/(?P<member_user_id>[^/.]+)/moderate")
    def moderate_member(self, request, pk=None, member_user_id=None):
        partner = self.get_object()
        membership = PartnerMembership.objects.filter(partner=partner, user_id=member_user_id).first()
        if not membership:
            return Response({"detail": "Partner membership not found."}, status=status.HTTP_404_NOT_FOUND)
        if partner.owner_id == membership.user_id:
            raise PermissionDenied("The partner owner cannot be moderated here.")

        action_type = str(request.data.get("action") or "").strip().lower()
        reason = str(request.data.get("reason") or "").strip()
        expires_at = request.data.get("expires_at")
        if action_type not in {
            PartnerModerationAction.ActionType.MUTE,
            PartnerModerationAction.ActionType.UNMUTE,
            PartnerModerationAction.ActionType.TIMEOUT,
            PartnerModerationAction.ActionType.KICK,
            PartnerModerationAction.ActionType.BAN,
            PartnerModerationAction.ActionType.UNBAN,
        }:
            raise ValidationError({"action": "Unsupported moderation action."})

        # kick/ban are grantable to a custom role independent of the coarse
        # owner/admin/manager check (e.g. a "Moderator" role) — mute/timeout
        # stay manager-tier-only, matching PartnerRolesPanel.tsx's catalog.
        if action_type == PartnerModerationAction.ActionType.KICK:
            self._require_permission(partner, request.user, "partner.members.kick")
        elif action_type in {PartnerModerationAction.ActionType.BAN, PartnerModerationAction.ActionType.UNBAN}:
            self._require_permission(partner, request.user, "partner.members.ban")
        elif not self._user_can_manage_partner(partner, request.user):
            raise PermissionDenied("Not allowed to moderate members.")

        if expires_at:
            expires_at = parse_datetime(str(expires_at))
            if expires_at is None:
                raise ValidationError({"expires_at": "Invalid ISO datetime."})
            if timezone.is_naive(expires_at):
                expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())
        else:
            expires_at = None

        update_fields = ["updated_at"]
        now = timezone.now()
        if action_type == PartnerModerationAction.ActionType.MUTE:
            membership.is_muted = True
            membership.muted_until = expires_at
            update_fields.extend(["is_muted", "muted_until"])
        elif action_type == PartnerModerationAction.ActionType.UNMUTE:
            membership.is_muted = False
            membership.muted_until = None
            update_fields.extend(["is_muted", "muted_until"])
        elif action_type == PartnerModerationAction.ActionType.TIMEOUT:
            membership.timed_out_until = expires_at or (now + timedelta(hours=24))
            update_fields.append("timed_out_until")
        elif action_type == PartnerModerationAction.ActionType.KICK:
            membership.status = PartnerMembershipStatus.REMOVED
            membership.removed_at = now
            update_fields.extend(["status", "removed_at"])
            ConversationMember.objects.filter(
                conversation_id=partner.main_conversation_id,
                user_id=membership.user_id,
                left_at__isnull=True,
            ).update(left_at=now)
        elif action_type == PartnerModerationAction.ActionType.BAN:
            membership.status = PartnerMembershipStatus.REMOVED
            membership.is_banned = True
            membership.banned_at = now
            membership.removed_at = now
            update_fields.extend(["status", "is_banned", "banned_at", "removed_at"])
            ConversationMember.objects.filter(
                conversation_id=partner.main_conversation_id,
                user_id=membership.user_id,
                left_at__isnull=True,
            ).update(left_at=now)
        elif action_type == PartnerModerationAction.ActionType.UNBAN:
            membership.is_banned = False
            membership.banned_at = None
            if membership.status == PartnerMembershipStatus.REMOVED:
                membership.status = PartnerMembershipStatus.SUBSCRIBER
                update_fields.append("status")
            update_fields.extend(["is_banned", "banned_at"])

        membership.save(update_fields=update_fields)
        moderation_action = PartnerModerationAction.objects.create(
            partner=partner,
            user=membership.user,
            actor=request.user,
            membership=membership,
            action_type=action_type,
            reason=reason,
            expires_at=expires_at,
            metadata={
                "action": action_type,
                "target_user_id": str(membership.user_id),
                "has_reason": bool(reason),
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
        )
        log_partner_audit(
            partner=partner,
            actor=request.user,
            action=f"partner.moderation.{action_type}",
            target_type="partner_membership",
            target_id=str(membership.id),
            metadata={"user_id": str(membership.user_id), "reason": reason},
            request=request,
        )
        if action_type in {PartnerModerationAction.ActionType.KICK, PartnerModerationAction.ActionType.BAN}:
            event = "partner.member_kicked" if action_type == PartnerModerationAction.ActionType.KICK else "partner.member_banned"
            notify_user_ids = set(active_partner_member_ids(partner))
            notify_user_ids.add(str(membership.user_id))
            notify_nest_of_partner_event(
                partner_id=str(partner.id),
                event=event,
                user_ids=list(notify_user_ids),
                data={"targetUserId": str(membership.user_id), "reason": reason},
            )
        return Response(PartnerModerationActionSerializer(moderation_action).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="links")
    def profile_links(self, request, pk=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.settings.view")
        links = self._ensure_profile_links(partner)
        serializer = PartnerProfileLinkSerializer(links, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="links/(?P<profile_key>[^/.]+)")
    def link_profile(self, request, pk=None, profile_key=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.settings.manage")
        link = self._get_profile_link(partner, profile_key)
        if not link.linked:
            link.linked = True
            link.save(update_fields=["linked", "updated_at"])
        serializer = PartnerProfileLinkSerializer(link, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["delete"], url_path="links/(?P<profile_key>[^/.]+)")
    def unlink_profile(self, request, pk=None, profile_key=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.settings.manage")
        link = self._get_profile_link(partner, profile_key)
        if link.linked:
            link.linked = False
            link.save(update_fields=["linked", "updated_at"])
        serializer = PartnerProfileLinkSerializer(link, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"], url_path="links/(?P<profile_key>[^/.]+)")
    def update_profile_link(self, request, pk=None, profile_key=None):
        partner = self.get_object()
        self._require_permission(partner, request.user, "partner.settings.manage")
        link = self._get_profile_link(partner, profile_key)
        role = request.data.get("role")
        valid_roles = {choice[0] for choice in PartnerProfileLink.ROLE_CHOICES}
        if role is None or role not in valid_roles:
            raise ValidationError({"role": "Invalid role."})
        if link.role != role:
            link.role = role
            link.save(update_fields=["role", "updated_at"])
        serializer = PartnerProfileLinkSerializer(link, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="jobs", permission_classes=[IsAuthenticated])
    def global_jobs(self, request):
        """Public job board — returns active job posts across all partners with optional filtering."""
        from django.db.models import Q
        qs = PartnerJobPost.objects.filter(is_active=True).select_related("partner").order_by("-created_at")
        search = request.query_params.get("search", "").strip()
        job_type = request.query_params.get("job_type", "").strip()
        is_remote = request.query_params.get("is_remote", "").strip()
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search) |
                Q(partner__name__icontains=search)
            )
        if job_type:
            qs = qs.filter(job_type=job_type)
        if is_remote in ("true", "1"):
            qs = qs.filter(is_remote=True)
        return Response(PartnerJobPostSerializer(qs[:200], many=True).data)

    @action(detail=False, methods=["get"], url_path="my-applications", permission_classes=[IsAuthenticated])
    def my_applications(self, request):
        """Returns the current user's partner applications with job and partner details."""
        qs = (
            PartnerApplication.objects
            .filter(user=request.user)
            .select_related("partner", "job_post")
            .order_by("-created_at")
        )
        data = [
            {
                "id": str(app.id),
                "partner_name": app.partner.name if app.partner else None,
                "partner_id": str(app.partner.id) if app.partner else None,
                "job_title": app.job_post.title if app.job_post else None,
                "job_post_id": str(app.job_post_id) if app.job_post_id else None,
                "status": app.status,
                "method": app.method,
                "created_at": app.created_at.isoformat() if app.created_at else None,
            }
            for app in qs
        ]
        return Response(data)

    @action(detail=False, methods=["post"], url_path=r"my-applications/(?P<app_id>[^/.]+)/withdraw", permission_classes=[IsAuthenticated])
    def withdraw_application(self, request, app_id=None):
        """Allows an applicant to withdraw their own pending application."""
        from rest_framework.exceptions import NotFound
        app = PartnerApplication.objects.filter(
            id=app_id,
            user=request.user,
            status=PartnerApplicationStatus.PENDING,
        ).first()
        if not app:
            raise NotFound("Application not found or cannot be withdrawn.")
        app.status = PartnerApplicationStatus.WITHDRAWN
        app.save(update_fields=["status", "updated_at"])
        return Response({"status": app.status})


class PartnerPostViewSet(viewsets.ModelViewSet):
    """
    /api/v1/partners/posts/

    - list:     GET  /api/v1/partners/posts/?partner=<partner_id>
    - create:   POST /api/v1/partners/posts/
    - retrieve: GET  /api/v1/partners/posts/{id}/
    """
    permission_classes = [IsAuthenticated]
    queryset = PartnerPost.objects.select_related("partner", "author")

    def get_serializer_class(self):
        if self.action == "create":
            return PartnerPostCreateSerializer
        return PartnerPostSerializer

    def _user_can_access_partner(self, partner: Partner, user) -> bool:
        return partner_user_can_access(partner, user)

    def _member_role(self, partner: Partner, user):
        if not partner.main_conversation_id:
            return None
        member = partner.main_conversation.memberships.filter(
            user=user,
            left_at__isnull=True,
        ).first()
        return member.base_role if member else None

    def _ensure_can_engage(self, partner: Partner, user):
        role = self._member_role(partner, user)
        if role == BaseConversationRole.READONLY:
            raise PermissionDenied("Subscribers cannot post or react.")
        # moderate_member's mute/timeout actions only ever touched
        # PartnerMembership — nothing here read those flags back, so a
        # muted or timed-out member could post/comment/react without any
        # restriction. The legacy base_role check above never covered this.
        membership = PartnerMembership.objects.filter(partner=partner, user=user).first()
        if not membership:
            return
        now = timezone.now()
        if membership.is_muted and (membership.muted_until is None or membership.muted_until > now):
            raise PermissionDenied("You are muted in this partner organization.")
        if membership.timed_out_until and membership.timed_out_until > now:
            raise PermissionDenied("You are temporarily timed out in this partner organization.")

    def _user_can_manage(self, partner: Partner, user) -> bool:
        role = self._member_role(partner, user)
        return partner.owner_id == user.id or role in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN)

    def get_queryset(self):
        user = self.request.user
        blocked_ids = UserBlock.objects.filter(blocker=user).values_list("blocked_id", flat=True)
        # Scheduled announcements never appear in the feed except to their
        # own author — the Broadcast Center's queue view (a dedicated
        # action below) is where admins review/publish/cancel them.
        visible_status = models.Q(status=PartnerPostStatus.PUBLISHED) | models.Q(
            status=PartnerPostStatus.SCHEDULED, author=user,
        )
        partner_id = self.request.query_params.get("partner")
        if partner_id:
            partner = Partner.objects.filter(id=partner_id).first()
            if not partner or not self._user_can_access_partner(partner, user):
                return PartnerPost.objects.none()
            qs = (
                PartnerPost.objects
                .select_related("partner", "author")
                .filter(partner=partner, is_deleted=False)
                .exclude(author_id__in=blocked_ids)
            )
            if not self._user_can_manage(partner, user):
                qs = qs.filter(visible_status)
            return qs.order_by("-created_at")

        accessible_partners = Partner.objects.filter(
            models.Q(owner=user)
            | models.Q(
                memberships__user=user,
                memberships__status__in=(
                    PartnerMembershipStatus.MEMBER,
                    PartnerMembershipStatus.SUBSCRIBER,
                ),
            )
            | models.Q(
                main_conversation__memberships__user=user,
                main_conversation__memberships__left_at__isnull=True,
            )
        )
        return (
            PartnerPost.objects
            .select_related("partner", "author")
            .filter(partner__in=accessible_partners, is_deleted=False)
            .filter(visible_status)
            .exclude(author_id__in=blocked_ids)
            .order_by("-created_at")
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        sample_limit = self._personalization_sample_limit(request)
        candidates = list(queryset[:sample_limit])
        metadata = self._build_partner_metadata(request.user, candidates)
        profile = get_affinity_profile(request.user)
        if profile:
            for entry in metadata.values():
                entry["profile"] = profile
        ranked = rank_feed_items(candidates, request.user, feed_type="partner", metadata_map=metadata)
        page = self.paginate_queryset(ranked)
        serializer = self.get_serializer(page if page is not None else ranked, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        log_feed_interaction(request.user, "partner", "feed_impression", weight=0.05)
        return Response(serializer.data)

    def _personalization_sample_limit(self, request):
        return resolve_personalization_sample_limit(request.query_params.get("limit"))

    def _build_partner_metadata(self, user, posts):
        if not posts:
            return {}
        partner_ids = {post.partner_id for post in posts if post.partner_id}
        memberships = PartnerMembership.objects.filter(
            partner_id__in=partner_ids,
            user=user,
            status__in=(PartnerMembershipStatus.MEMBER, PartnerMembershipStatus.SUBSCRIBER),
        ).values_list("partner_id", flat=True)
        member_ids = {str(pid) for pid in memberships}
        metadata = {}
        for post in posts:
            partner_id = str(post.partner_id) if post.partner_id else None
            metadata[str(post.id)] = {
                "source": {
                    "type": "partner",
                    "id": partner_id,
                    "is_member": partner_id in member_ids if partner_id else False,
                    "can_open": partner_id in member_ids if partner_id else False,
                }
            }
        return metadata

    def perform_create(self, serializer):
        partner_id = self.request.data.get("partner")
        if not partner_id:
            raise ValidationError({"partner": "This field is required."})
        partner = Partner.objects.filter(id=partner_id).first()
        if not partner or not self._user_can_access_partner(partner, self.request.user):
            raise PermissionDenied("Not allowed to post to this partner.")
        self._ensure_can_engage(partner, self.request.user)
        if self.request.data.get("scheduled_for") and not self._user_can_manage(partner, self.request.user):
            raise PermissionDenied("Only owners/admins can schedule an announcement.")
        dlp = evaluate_partner_dlp(partner, self.request.data.get("text", "") or "")
        if dlp["blocked"]:
            raise ValidationError({"detail": "Message blocked by DLP policy."})
        if dlp["warn"]:
            log_partner_audit(
                partner=partner,
                actor=self.request.user,
                action="partner.dlp.warn",
                target_type="partner_post",
                metadata={"matches": dlp["warn"]},
                request=self.request,
            )
        post = serializer.save()
        run_partner_automation_rules(
            partner=partner,
            event="partner.post.created",
            payload={
                "post_id": str(post.id),
                "user_id": str(self.request.user.id),
            },
            actor=self.request.user,
        )

    @action(detail=True, methods=["post"], url_path="comment")
    def comment(self, request, pk=None):
        post = self.get_object()
        if not self._user_can_access_partner(post.partner, request.user):
            raise PermissionDenied("Not allowed to comment.")
        self._ensure_can_engage(post.partner, request.user)
        dlp = evaluate_partner_dlp(post.partner, request.data.get("text", "") or "")
        if dlp["blocked"]:
            raise ValidationError({"detail": "Message blocked by DLP policy."})
        if dlp["warn"]:
            log_partner_audit(
                partner=post.partner,
                actor=request.user,
                action="partner.dlp.warn",
                target_type="partner_post_comment",
                metadata={"matches": dlp["warn"]},
                request=request,
            )
        comment = PartnerPostComment.objects.create(
            post=post,
            author=request.user,
            text=request.data.get("text", ""),
        )
        run_partner_automation_rules(
            partner=post.partner,
            event="partner.post.commented",
            payload={
                "post_id": str(post.id),
                "comment_id": str(comment.id),
                "user_id": str(request.user.id),
            },
            actor=request.user,
        )
        return Response(PartnerPostCommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="comment-room")
    def comment_room(self, request, pk=None):
        post = self.get_object()
        if not self._user_can_access_partner(post.partner, request.user):
            raise PermissionDenied("Not allowed to comment.")
        self._ensure_can_engage(post.partner, request.user)

        conversation = ensure_post_comment_conversation(
            post,
            actor=request.user,
            created_by=post.author or request.user,
            title=f"{post.partner.name} comments",
            description=f"Comments for partner post {post.id}",
        )
        ensure_conversation_member(conversation, request.user)

        return Response(
            {"conversation_id": str(conversation.id), "title": conversation.title},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="comments")
    def comments(self, request, pk=None):
        post = self.get_object()
        if not self._user_can_access_partner(post.partner, request.user):
            raise PermissionDenied("Not allowed to view comments.")
        comments = post.comments.filter(is_deleted=False).select_related("author").order_by("created_at")
        return Response(PartnerPostCommentSerializer(comments, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="react")
    def react(self, request, pk=None):
        post = self.get_object()
        if not self._user_can_access_partner(post.partner, request.user):
            raise PermissionDenied("Not allowed to react.")
        self._ensure_can_engage(post.partner, request.user)
        emoji = request.data.get("emoji") or "👍"
        action = request.data.get("action")
        existing = PartnerPostReaction.objects.filter(post=post, user=request.user).first()
        if action in ("remove", "unlike") or (action == "toggle" and existing):
            if existing:
                existing.delete()
            run_partner_automation_rules(
                partner=post.partner,
                event="partner.post.reacted",
                payload={
                    "post_id": str(post.id),
                    "user_id": str(request.user.id),
                    "action": "remove",
                },
                actor=request.user,
            )
            return Response({"detail": "Reaction removed.", "has_reacted": False}, status=status.HTTP_200_OK)

        reaction, created = PartnerPostReaction.objects.get_or_create(
            post=post,
            user=request.user,
            defaults={"emoji": emoji},
        )
        if not created and reaction.emoji != emoji:
            reaction.emoji = emoji
            reaction.save(update_fields=["emoji"])
        run_partner_automation_rules(
            partner=post.partner,
            event="partner.post.reacted",
            payload={
                "post_id": str(post.id),
                "user_id": str(request.user.id),
                "action": "add",
                "emoji": emoji,
            },
            actor=request.user,
        )
        return Response({"detail": "Reaction saved.", "has_reacted": True}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="delete")
    def delete_post(self, request, pk=None):
        post = self.get_object()
        role = self._member_role(post.partner, request.user)
        is_owner = post.author_id == request.user.id
        is_admin = role in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN)
        if not (is_owner or is_admin or post.partner.owner_id == request.user.id):
            raise PermissionDenied("Not allowed to delete this post.")
        policy = ensure_partner_policy(post.partner)
        compliance = (policy.settings or {}).get("compliance", {})
        if compliance.get("legal_hold_enabled"):
            raise PermissionDenied("Legal hold is enabled for this partner.")
        post.is_deleted = True
        post.save(update_fields=["is_deleted"])
        return Response({"detail": "Post deleted."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="broadcast")
    def broadcast(self, request, pk=None):
        post = self.get_object()
        role = self._member_role(post.partner, request.user)
        is_owner = post.author_id == request.user.id
        is_admin = role in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN)
        if not (is_owner or is_admin or post.partner.owner_id == request.user.id):
            raise PermissionDenied("Not allowed to broadcast this post.")
        post.is_broadcast = True
        post.save(update_fields=["is_broadcast"])
        try:
            from apps.broadcasts.models import BroadcastItem, BroadcastSourceType
            from datetime import timedelta

            BroadcastItem.objects.update_or_create(
                source_type=BroadcastSourceType.PARTNER_POST,
                source_id=str(post.id),
                defaults={
                    "broadcasted_by": request.user,
                    "broadcasted_at": timezone.now(),
                    "expires_at": timezone.now() + timedelta(days=10),
                    "is_deleted": False,
                },
            )
        except Exception:
            # Previously swallowed entirely — the endpoint reported success
            # even when the post never actually reached the global feed.
            logger.exception("partner post %s marked is_broadcast but BroadcastItem creation failed", post.id)
            return Response(
                {"detail": "Post saved, but publishing to the global feed failed. Try again shortly.", "is_broadcast": True, "published": False},
                status=status.HTTP_207_MULTI_STATUS,
            )
        return Response({"detail": "Post broadcasted.", "is_broadcast": True, "published": True}, status=status.HTTP_200_OK)

    # ── Broadcast Center & Announcement Scheduler ───────────────────────
    @action(detail=False, methods=["get"], url_path="queue")
    def queue(self, request):
        partner_id = request.query_params.get("partner")
        if not partner_id:
            raise ValidationError({"partner": "This field is required."})
        partner = Partner.objects.filter(id=partner_id).first()
        if not partner or not self._user_can_manage(partner, request.user):
            raise PermissionDenied("Not allowed to view the announcement queue.")
        qs = PartnerPost.objects.filter(
            partner=partner, status=PartnerPostStatus.SCHEDULED, is_deleted=False,
        ).select_related("author").order_by("scheduled_for")
        return Response(PartnerPostSerializer(qs, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="publish-now")
    def publish_now(self, request, pk=None):
        post = PartnerPost.objects.filter(id=pk, is_deleted=False).select_related("partner").first()
        if not post:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._user_can_manage(post.partner, request.user):
            raise PermissionDenied("Not allowed to publish this announcement.")
        if post.status != PartnerPostStatus.SCHEDULED:
            return Response({"detail": "Post is not scheduled."}, status=status.HTTP_400_BAD_REQUEST)
        post.status = PartnerPostStatus.PUBLISHED
        post.scheduled_for = None
        post.created_at = timezone.now()
        post.save(update_fields=["status", "scheduled_for", "created_at"])
        log_partner_audit(
            partner=post.partner, actor=request.user, action="partner.announcement.publish_now",
            target_type="partner_post", target_id=str(post.id), request=request,
        )
        return Response(PartnerPostSerializer(post, context={"request": request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancel-scheduled")
    def cancel_scheduled(self, request, pk=None):
        post = PartnerPost.objects.filter(id=pk, is_deleted=False).select_related("partner").first()
        if not post:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        is_author = post.author_id == request.user.id
        if not (is_author or self._user_can_manage(post.partner, request.user)):
            raise PermissionDenied("Not allowed to cancel this announcement.")
        if post.status != PartnerPostStatus.SCHEDULED:
            return Response({"detail": "Post is not scheduled."}, status=status.HTTP_400_BAD_REQUEST)
        log_partner_audit(
            partner=post.partner, actor=request.user, action="partner.announcement.cancel",
            target_type="partner_post", target_id=str(post.id), request=request,
        )
        post.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserAppShortcutViewSet(viewsets.ViewSet):
    """
    Home-screen shortcut management for partner org apps.
    Tracks real device shortcuts created via native Android/iOS APIs.
    """
    permission_classes = [IsAuthenticated]

    def _get_partner(self, partner_id):
        from apps.partners.models import Partner
        try:
            return Partner.objects.get(id=partner_id)
        except Partner.DoesNotExist:
            return None

    def list(self, request):
        from apps.partners.models import UserAppShortcut
        qs = UserAppShortcut.objects.filter(user=request.user).select_related("partner", "app")
        device_id = request.query_params.get("device_id")
        if device_id:
            qs = qs.filter(device_id=device_id)
        data = [
            {
                "id": str(s.id),
                "partner_id": str(s.partner_id),
                "partner_name": s.partner.name,
                "app_id": str(s.app_id) if s.app_id else None,
                "app_name": s.app.name if s.app else None,
                "device_id": s.device_id,
                "shortcut_name": s.shortcut_name,
                "icon": s.icon,
                "deep_link": s.deep_link,
                "pinned": s.pinned,
                "created_at": s.created_at.isoformat(),
                "last_opened_at": s.last_opened_at.isoformat() if s.last_opened_at else None,
            }
            for s in qs
        ]
        return Response(data)

    def create(self, request):
        from apps.partners.models import UserAppShortcut, PartnerOrganizationApp
        d = request.data
        partner_id = d.get("partner_id")
        if not partner_id:
            return Response({"detail": "partner_id required."}, status=status.HTTP_400_BAD_REQUEST)
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)

        app = None
        app_id = d.get("app_id")
        if app_id:
            try:
                app = PartnerOrganizationApp.objects.get(id=app_id, partner=partner)
            except PartnerOrganizationApp.DoesNotExist:
                pass

        device_id = d.get("device_id", "")
        deep_link = d.get("deep_link") or f"kis://org-app/{app_id or partner_id}"
        shortcut_name = d.get("shortcut_name") or (app.name if app else partner.name)
        icon = d.get("icon", "")
        pinned = bool(d.get("pinned", False))

        # Prevent duplicate shortcuts per device
        existing = UserAppShortcut.objects.filter(
            user=request.user,
            partner=partner,
            device_id=device_id,
            deep_link=deep_link,
        ).first()
        if existing:
            return Response(
                {"detail": "already_pinned", "id": str(existing.id)},
                status=status.HTTP_200_OK,
            )

        shortcut = UserAppShortcut.objects.create(
            user=request.user,
            app=app,
            partner=partner,
            device_id=device_id,
            shortcut_name=shortcut_name,
            icon=icon,
            deep_link=deep_link,
            pinned=pinned,
        )
        log_partner_audit(
            partner=partner, actor=request.user, action="shortcut.created",
            metadata={"shortcut_id": str(shortcut.id), "device_id": device_id}, request=request,
        )
        return Response({"detail": "created", "id": str(shortcut.id)}, status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        from apps.partners.models import UserAppShortcut
        try:
            shortcut = UserAppShortcut.objects.get(id=pk, user=request.user)
        except UserAppShortcut.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        partner = shortcut.partner
        log_partner_audit(
            partner=partner, actor=request.user, action="shortcut.removed",
            metadata={"shortcut_id": pk}, request=request,
        )
        shortcut.delete()
        return Response({"detail": "removed."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="opened")
    def update_last_opened(self, request, pk=None):
        from apps.partners.models import UserAppShortcut
        try:
            shortcut = UserAppShortcut.objects.get(id=pk, user=request.user)
        except UserAppShortcut.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        shortcut.last_opened_at = timezone.now()
        shortcut.save(update_fields=["last_opened_at"])
        return Response({"detail": "updated."})

    @action(detail=False, methods=["get"], url_path="analytics")
    def analytics(self, request):
        """KCAN/admin analytics for shortcut usage across the platform."""
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("Admin access required.")
        from apps.partners.models import UserAppShortcut
        from django.db.models import Count
        qs = UserAppShortcut.objects.values("partner__name", "partner__slug").annotate(
            total=Count("id"),
            pinned_count=Count("id", filter=models.Q(pinned=True)),
        ).order_by("-total")[:50]
        return Response({"results": list(qs)})
