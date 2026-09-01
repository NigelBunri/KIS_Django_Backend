from datetime import timedelta
from unittest.mock import patch
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription, User
from apps.accounts.tiers import ensure_default_account_tiers
from apps.channels.models import Channel
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.partners.models import (
    Partner,
    PartnerApplication,
    PartnerAuditEvent,
    PartnerAutomationRule,
    PartnerDepartment,
    PartnerDepartmentMembership,
    PartnerLocation,
    PartnerIntegration,
    PartnerInvite,
    PartnerJobPost,
    PartnerJoinConfig,
    PartnerMembership,
    PartnerMembershipStatus,
    PartnerOnboardingProgress,
    PartnerModerationAction,
    PartnerOrganizationApp,
    PartnerOrganizationAppType,
    PartnerOrganizationLink,
    PartnerOrganizationProfile,
    PartnerPost,
    PartnerRole,
    PartnerRoleAssignment,
    PartnerSubscription,
    PartnerWebhook,
    PartnerWebhookDelivery,
)
from apps.partners.serializers import (
    PartnerAuditEventSerializer,
    PartnerDetailSerializer,
    PartnerIntegrationSerializer,
    PartnerOrganizationProfileSerializer,
    PartnerPostSerializer,
    PartnerWebhookDeliverySerializer,
    PartnerWebhookSerializer,
)
from apps.verification.constants import VerificationBadgeCode, VerificationSubjectType
from apps.verification.models import VerificationBadge
from apps.verification.services import current_partner_verification_status, start_partner_verification_case


class PartnerApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = self._create_user("owner", "+237670000001")
        self.member = self._create_user("member", "+237670000002")
        self.manager = self._create_user("manager", "+237670000003")
        # Real partner org owners are always on the Partner/Partner Pro tier
        # (PartnerViewSet.create gates partner_accounts to those tiers) —
        # give the test owner a matching active subscription so team-seat
        # enforcement in redeem_invite behaves like production, instead of
        # the untiered-user default.
        ensure_default_account_tiers()
        partner_tier = AccountTier.objects.filter(name__iexact="Partner").first()
        if partner_tier:
            Subscription.objects.create(user=self.owner, tier=partner_tier, status="active")

    def _create_user(self, username: str, phone: str) -> User:
        suffix = phone[-4:]
        return User.objects.create_user(
            phone=phone,
            country="CM",
            password="pass1234",
            email=f"{username}-{suffix}@example.com",
            username=f"{username}-{suffix}",
            display_name=username.title(),
            phone_country_code="+237",
            phone_number=phone[-9:],
        )

    def _create_partner(self, owner: User | None = None, name: str = "Partner One", slug: str = "partner-one") -> Partner:
        owner = owner or self.owner
        conversation = Conversation.objects.create(
            type=ConversationType.POST,
            title=name,
            description=f"Post space for {name}",
            created_by=owner,
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=owner,
            base_role=BaseConversationRole.OWNER,
        )
        partner = Partner.objects.create(
            owner=owner,
            name=name,
            slug=slug,
            main_conversation=conversation,
        )
        PartnerJoinConfig.objects.get_or_create(partner=partner)
        PartnerOrganizationProfile.objects.get_or_create(
            partner=partner,
            defaults={"display_name": name, "updated_by": owner},
        )
        PartnerRole.objects.get_or_create(
            partner=partner,
            name="Member",
            defaults={"permissions": [], "is_default": True},
        )
        # Mirrors production's ensure_partner_subscription (called from the
        # real create-partner paths, which this raw-ORM test helper
        # bypasses) — otherwise the org has no workspace-level plan at all,
        # and every partner-tier-gated feature this test suite exercises
        # (team seats, job_posting, webhooks, etc.) would 403 regardless of
        # what tier the owner's personal Subscription is set to.
        from apps.partners.services import ensure_partner_subscription

        ensure_partner_subscription(partner)
        return partner

    def _detail_url(self, partner: Partner, suffix: str = "") -> str:
        base = f"/api/v1/partners/{partner.id}/"
        return f"{base}{suffix}"

    def test_partner_create_creates_post_conversation_and_defaults(self):
        self.client.force_authenticate(self.owner)

        with patch("apps.partners.views.require_feature", return_value=None):
            response = self.client.post(
                "/api/v1/partners/",
                {
                    "name": "Kingdom Builders",
                    "slug": "kingdom-builders",
                    "description": "Partner server",
                    "create_main_conversation": True,
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        partner = Partner.objects.get(slug="kingdom-builders")
        self.assertIsNotNone(partner.main_conversation_id)
        self.assertEqual(partner.main_conversation.type, ConversationType.POST)
        self.assertTrue(PartnerJoinConfig.objects.filter(partner=partner).exists())
        self.assertTrue(PartnerOrganizationProfile.objects.filter(partner=partner).exists())
        self.assertTrue(PartnerRole.objects.filter(partner=partner, name="Owner").exists())

    def test_verify_partners_launch_command_passes_safe_local_defaults(self):
        output = StringIO()

        call_command("verify_partners_launch", stdout=output)

        rendered = output.getvalue()
        self.assertIn("Partners launch guardrails ready: True", rendered)
        self.assertIn("PASS: route:partner_list", rendered)
        self.assertIn("PASS: partner_secret_redaction", rendered)
        self.assertIn("PASS: MEDIA_SAFETY_ENABLED", rendered)

    def test_partner_secret_serializers_redact_read_payloads(self):
        partner = self._create_partner()
        webhook = PartnerWebhook.objects.create(
            partner=partner,
            name="Ops hook",
            url="https://example.com/hook",
            events=["member.joined"],
            secret="super-secret",
        )
        delivery = PartnerWebhookDelivery.objects.create(
            webhook=webhook,
            event="member.joined",
            payload={"token": "private-token", "safe": "ok"},
        )
        integration = PartnerIntegration.objects.create(
            partner=partner,
            kind=PartnerIntegration.KIND_WEBHOOK,
            provider="example",
            config={"api_key": "private-key", "safe": "ok"},
        )
        audit = PartnerAuditEvent.objects.create(
            partner=partner,
            actor=self.owner,
            action="integration.updated",
            metadata={"webhook_secret": "private-secret", "safe": "ok"},
        )

        webhook_payload = PartnerWebhookSerializer(webhook).data
        delivery_payload = PartnerWebhookDeliverySerializer(delivery).data
        integration_payload = PartnerIntegrationSerializer(integration).data
        audit_payload = PartnerAuditEventSerializer(audit).data

        self.assertNotIn("secret", webhook_payload)
        self.assertEqual(delivery_payload["payload"]["token"], "[redacted]")
        self.assertEqual(delivery_payload["payload"]["safe"], "ok")
        self.assertEqual(integration_payload["config"]["api_key"], "[redacted]")
        self.assertEqual(integration_payload["config"]["safe"], "ok")
        self.assertEqual(audit_payload["metadata"]["webhook_secret"], "[redacted]")
        self.assertEqual(audit_payload["metadata"]["safe"], "ok")

    def test_partner_verification_start_creates_central_case_with_safe_metadata(self):
        partner = self._create_partner()
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            self._detail_url(partner, "verification/start/"),
            {
                "provider": "sumsub",
                "evidence_metadata": {
                    "company_registration": [
                        {
                            "type": "certificate",
                            "private_media_id": "private-company-doc",
                            "url": "https://example.com/public-company-doc.pdf",
                        }
                    ],
                    "representative_authorization": [{"type": "board_resolution", "private_media_id": "private-auth-doc"}],
                    "beneficial_owners": [{"name": "Owner", "percentage": 100, "private_media_id": "private-ubo-doc"}],
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["case"]["provider"], "sumsub")
        case_id = response.data["case"]["id"]
        from apps.verification.models import VerificationCase

        case = VerificationCase.objects.get(id=case_id)
        self.assertEqual(case.subject.subject_type, VerificationSubjectType.PARTNER)
        self.assertEqual(case.evidence_metadata["company_registration"][0]["private_media_id"], "private-company-doc")
        self.assertNotIn("url", case.evidence_metadata["company_registration"][0])

    def test_staff_can_approve_partner_case_and_issue_public_badges(self):
        partner = self._create_partner()
        case = start_partner_verification_case(
            partner=partner,
            actor=self.owner,
            evidence_metadata={"company_registration": [{"private_media_id": "private-company-doc"}]},
        )
        self.staff = self._create_user("staff", "+237670000099")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])
        self.client.force_authenticate(self.staff)

        response = self.client.post(
            self._detail_url(partner, f"verification/cases/{case.id}/review/"),
            {"action": "approve", "badge_codes": ["verified_partner", "verified_organization", "official_partner"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        badge_codes = set(
            VerificationBadge.objects.filter(
                subject__subject_type=VerificationSubjectType.PARTNER,
                subject__subject_id=partner.id,
            ).values_list("code", flat=True)
        )
        self.assertIn(VerificationBadgeCode.VERIFIED_PARTNER, badge_codes)
        self.assertIn(VerificationBadgeCode.VERIFIED_ORGANIZATION, badge_codes)
        self.assertIn(VerificationBadgeCode.OFFICIAL_PARTNER, badge_codes)
        self.assertTrue(current_partner_verification_status(partner)["verified"])

    def test_partner_serializers_expose_verification_summary(self):
        partner = self._create_partner()
        case = start_partner_verification_case(partner=partner, actor=self.owner, evidence_metadata={})
        self.staff = self._create_user("staff_summary", "+237670000098")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])
        from apps.verification.services import review_partner_case

        review_partner_case(case=case, actor=self.staff, action="approve")
        profile = PartnerOrganizationProfile.objects.get(partner=partner)

        detail = PartnerDetailSerializer(partner).data
        profile_data = PartnerOrganizationProfileSerializer(profile).data

        self.assertTrue(detail["verification_summary"]["verified"])
        self.assertTrue(profile_data["verification_summary"]["verified"])

    def test_partner_verification_start_rejects_raw_document_payload(self):
        partner = self._create_partner()
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            self._detail_url(partner, "verification/start/"),
            {"evidence_metadata": {"company_registration": [{"document_base64": "data:image/png;base64,abc123"}]}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_manager_member_cannot_update_partner_settings(self):
        partner = self._create_partner()
        PartnerMembership.objects.create(
            partner=partner,
            user=self.member,
            status=PartnerMembershipStatus.MEMBER,
            role="member",
        )
        ConversationMember.objects.create(
            conversation=partner.main_conversation,
            user=self.member,
            base_role=BaseConversationRole.MEMBER,
        )

        self.client.force_authenticate(self.member)
        response = self.client.patch(
            self._detail_url(partner, "settings/"),
            {"updates": [{"key": "org_profile", "enabled": False}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_subscribe_creates_partner_membership_and_readonly_conversation_membership(self):
        partner = self._create_partner()

        self.client.force_authenticate(self.member)
        response = self.client.post(self._detail_url(partner, "subscribe/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = PartnerMembership.objects.get(partner=partner, user=self.member)
        self.assertEqual(membership.status, PartnerMembershipStatus.SUBSCRIBER)
        conv_member = ConversationMember.objects.get(conversation=partner.main_conversation, user=self.member)
        self.assertEqual(conv_member.base_role, BaseConversationRole.READONLY)

    def test_apply_creates_pending_application_and_membership(self):
        partner = self._create_partner()

        self.client.force_authenticate(self.member)
        response = self.client.post(
            self._detail_url(partner, "apply/"),
            {"method": "application", "message": "Please add me"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        application = PartnerApplication.objects.get(partner=partner, user=self.member)
        membership = PartnerMembership.objects.get(partner=partner, user=self.member)
        self.assertEqual(application.status, "pending")
        self.assertEqual(membership.status, PartnerMembershipStatus.PENDING)

    def test_manager_can_see_manager_only_organization_app(self):
        partner = self._create_partner()
        PartnerMembership.objects.create(
            partner=partner,
            user=self.manager,
            status=PartnerMembershipStatus.MEMBER,
            role="manager",
        )
        PartnerMembership.objects.create(
            partner=partner,
            user=self.member,
            status=PartnerMembershipStatus.MEMBER,
            role="member",
        )

        manager_app = PartnerOrganizationApp.objects.create(
            partner=partner,
            name="Ops Console",
            slug="ops-console",
            type=PartnerOrganizationAppType.KIS,
            visible_to=["manager"],
        )

        self.client.force_authenticate(self.manager)
        manager_response = self.client.get(self._detail_url(partner, "organization-apps/"))
        self.assertEqual(manager_response.status_code, status.HTTP_200_OK)
        manager_app_ids = {item["id"] for item in manager_response.data["apps"]}
        self.assertIn(str(manager_app.id), manager_app_ids)

        self.client.force_authenticate(self.member)
        member_response = self.client.get(self._detail_url(partner, "organization-apps/"))
        self.assertEqual(member_response.status_code, status.HTTP_200_OK)
        self.assertEqual(member_response.data["apps"], [])

    def test_owner_can_deactivate_and_reactivate_partner(self):
        partner = self._create_partner()

        self.client.force_authenticate(self.owner)
        deactivate_response = self.client.post(self._detail_url(partner, "deactivate/"), {}, format="json")
        self.assertEqual(deactivate_response.status_code, status.HTTP_200_OK)

        partner.refresh_from_db()
        self.assertFalse(partner.is_active)
        self.assertEqual(partner.deactivation_source, Partner.DeactivationSource.USER)

        reactivate_response = self.client.post(self._detail_url(partner, "reactivate/"), {}, format="json")
        self.assertEqual(reactivate_response.status_code, status.HTTP_200_OK)

        partner.refresh_from_db()
        self.assertTrue(partner.is_active)
        self.assertIsNone(partner.deactivation_source)

    def test_redeem_invite_creates_membership_and_onboarding_progress(self):
        partner = self._create_partner()
        invite = PartnerInvite.objects.create(
            partner=partner,
            created_by=self.owner,
            label="Core team",
            membership_role="member",
        )

        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/partners/redeem-invite/",
            {"code": invite.code},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = PartnerMembership.objects.get(partner=partner, user=self.member)
        self.assertEqual(membership.status, PartnerMembershipStatus.MEMBER)
        onboarding = PartnerOnboardingProgress.objects.get(partner=partner, user=self.member)
        self.assertEqual(onboarding.invite_id, invite.id)
        invite.refresh_from_db()
        self.assertEqual(invite.use_count, 1)

    def test_manager_can_moderate_member_with_ban(self):
        partner = self._create_partner()
        PartnerMembership.objects.create(
            partner=partner,
            user=self.manager,
            status=PartnerMembershipStatus.MEMBER,
            role="manager",
        )
        target_membership = PartnerMembership.objects.create(
            partner=partner,
            user=self.member,
            status=PartnerMembershipStatus.MEMBER,
            role="member",
        )
        ConversationMember.objects.create(
            conversation=partner.main_conversation,
            user=self.member,
            base_role=BaseConversationRole.MEMBER,
        )

        self.client.force_authenticate(self.manager)
        response = self.client.post(
            self._detail_url(partner, f"members/{self.member.id}/moderate/"),
            {"action": "ban", "reason": "spam"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target_membership.refresh_from_db()
        self.assertTrue(target_membership.is_banned)
        self.assertEqual(target_membership.status, PartnerMembershipStatus.REMOVED)
        action = PartnerModerationAction.objects.get(
            partner=partner,
            user=self.member,
            action_type="ban",
        )
        self.assertNotIn("request", action.metadata)
        self.assertEqual(action.metadata["action"], "ban")
        self.assertEqual(action.metadata["target_user_id"], str(self.member.id))
        self.assertTrue(action.metadata["has_reason"])

    def test_banned_member_cannot_subscribe_or_apply_again(self):
        partner = self._create_partner()
        PartnerMembership.objects.create(
            partner=partner,
            user=self.member,
            status=PartnerMembershipStatus.REMOVED,
            role="member",
            is_banned=True,
        )

        self.client.force_authenticate(self.member)
        subscribe_response = self.client.post(self._detail_url(partner, "subscribe/"), {}, format="json")
        apply_response = self.client.post(
            self._detail_url(partner, "apply/"),
            {"method": "application", "message": "Let me back in"},
            format="json",
        )

        self.assertEqual(subscribe_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(apply_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_partner_public_hub_returns_profile_and_metrics(self):
        partner = self._create_partner()
        PartnerMembership.objects.create(
            partner=partner,
            user=self.member,
            status=PartnerMembershipStatus.MEMBER,
            role="member",
        )

        self.client.force_authenticate(self.member)
        response = self.client.get(self._detail_url(partner, "public-hub/"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["partner_id"], str(partner.id))
        self.assertIn("public_metrics", response.data)
        self.assertIn("profile", response.data)

    def test_partner_discord_summary_exposes_workspace_readiness_and_unread(self):
        partner = self._create_partner()
        PartnerMembership.objects.create(
            partner=partner,
            user=self.member,
            status=PartnerMembershipStatus.MEMBER,
            role="member",
        )
        channel_conversation = Conversation.objects.create(
            type=ConversationType.CHANNEL,
            title="Prayer room",
            description="Partner channel",
            created_by=self.owner,
            last_message_seq=8,
        )
        Channel.objects.create(
            partner=partner,
            owner=self.owner,
            conversation=channel_conversation,
            name="Prayer room",
            slug="prayer-room",
            channel_type="text",
        )
        ConversationMember.objects.create(
            conversation=channel_conversation,
            user=self.member,
            base_role=BaseConversationRole.MEMBER,
            last_read_seq=5,
        )

        self.client.force_authenticate(self.member)
        response = self.client.get(self._detail_url(partner, "discord-summary/"))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["partner_id"], str(partner.id))
        self.assertEqual(response.data["counts"]["visible_channels"], 1)
        self.assertEqual(response.data["counts"]["unread_messages"], 3)
        self.assertTrue(response.data["readiness"]["moderation_ready"])
        self.assertTrue(response.data["readiness"]["family_safe_media"])
        self.assertEqual(response.data["safety"]["media_gate"], "enabled")

    def test_manager_can_apply_automation_recipe(self):
        partner = self._create_partner()
        PartnerMembership.objects.create(
            partner=partner,
            user=self.manager,
            status=PartnerMembershipStatus.MEMBER,
            role="manager",
        )

        self.client.force_authenticate(self.manager)
        with patch("apps.partners.views.require_feature", return_value=None):
            response = self.client.post(
                self._detail_url(partner, "automation-recipes/apply/"),
                {"recipe_key": "onboarding-followup"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            PartnerAutomationRule.objects.filter(
                partner=partner,
                name="Onboarding Follow-up",
            ).exists()
        )

    def test_partner_post_comment_room_is_reused_and_membership_is_created(self):
        partner = self._create_partner()
        PartnerMembership.objects.create(
            partner=partner,
            user=self.member,
            status=PartnerMembershipStatus.MEMBER,
            role="member",
        )
        ConversationMember.objects.create(
            conversation=partner.main_conversation,
            user=self.member,
            base_role=BaseConversationRole.MEMBER,
        )
        post = PartnerPost.objects.create(
            partner=partner,
            author=self.owner,
            text_plain="Comment here",
            text_preview="Comment here",
        )

        self.client.force_authenticate(self.member)
        first = self.client.post(f"/api/v1/partners/posts/{post.id}/comment-room/", {}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        conversation_id = first.data.get("conversation_id")
        self.assertTrue(conversation_id)

        second = self.client.post(f"/api/v1/partners/posts/{post.id}/comment-room/", {}, format="json")
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual(second.data.get("conversation_id"), conversation_id)

        self.assertTrue(
            ConversationMember.objects.filter(
                conversation_id=conversation_id,
                user=self.member,
                left_at__isnull=True,
            ).exists()
        )

    def test_partner_post_serializer_prefers_comment_conversation_sequence_for_count(self):
        partner = self._create_partner()
        discussion = Conversation.objects.create(
            type=ConversationType.POST,
            title="Partner comments",
            description="Canonical discussion",
            created_by=self.owner,
            last_message_seq=6,
        )
        post = PartnerPost.objects.create(
            partner=partner,
            author=self.owner,
            text_plain="Comment source",
            text_preview="Comment source",
            comment_conversation=discussion,
        )

        payload = PartnerPostSerializer(post).data

        self.assertEqual(payload["comments_count"], 6)


class PartnerPostModerationEnforcementApiTests(TestCase):
    """moderate_member's mute/timeout actions set PartnerMembership flags,
    but nothing in PartnerPostViewSet ever read them back — a muted or
    timed-out member could post/comment/react without any restriction."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670005001", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670005002", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Moderation Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        ConversationMember.objects.create(conversation=conversation, user=self.member, base_role=BaseConversationRole.MEMBER)
        self.partner = Partner.objects.create(owner=self.owner, name="Moderation Partner", slug="moderation-partner", main_conversation=conversation)
        self.membership = PartnerMembership.objects.create(
            partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER,
        )
        self.post = PartnerPost.objects.create(partner=self.partner, author=self.owner, text_plain="Hello", text_preview="Hello")

    def test_muted_member_cannot_post(self):
        self.membership.is_muted = True
        self.membership.save(update_fields=["is_muted"])
        self.client.force_authenticate(self.member)

        response = self.client.post("/api/v1/partners/posts/", {"partner": str(self.partner.id)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_muted_member_cannot_comment_or_react(self):
        self.membership.is_muted = True
        self.membership.save(update_fields=["is_muted"])
        self.client.force_authenticate(self.member)

        comment_response = self.client.post(f"/api/v1/partners/posts/{self.post.id}/comment/", {"text": "hi"}, format="json")
        react_response = self.client.post(f"/api/v1/partners/posts/{self.post.id}/react/", {"emoji": "👍"}, format="json")

        self.assertEqual(comment_response.status_code, status.HTTP_403_FORBIDDEN, comment_response.data)
        self.assertEqual(react_response.status_code, status.HTTP_403_FORBIDDEN, react_response.data)

    def test_timed_out_member_cannot_post(self):
        self.membership.timed_out_until = timezone.now() + timedelta(hours=1)
        self.membership.save(update_fields=["timed_out_until"])
        self.client.force_authenticate(self.member)

        response = self.client.post("/api/v1/partners/posts/", {"partner": str(self.partner.id)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_expired_timeout_no_longer_blocks(self):
        self.membership.timed_out_until = timezone.now() - timedelta(hours=1)
        self.membership.save(update_fields=["timed_out_until"])
        self.client.force_authenticate(self.member)

        response = self.client.post("/api/v1/partners/posts/", {"partner": str(self.partner.id)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_unmuted_member_can_post(self):
        self.client.force_authenticate(self.member)

        response = self.client.post("/api/v1/partners/posts/", {"partner": str(self.partner.id)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)


class PartnerJobPostingTierGateApiTests(TestCase):
    """jobs/update_job only ever checked _user_can_manage_partner — any
    manager/admin/owner could post jobs regardless of ANY tier at all.
    Also covers the field-drop bug where job creation silently ignored
    location/is_remote/job_type/salary/tags.

    job_posting is gated by the PARTNER's own workspace-level
    PartnerSubscription (apps/partners/tiers.py), not by whichever staff
    member happens to be making the request — see PartnerSubscription's
    docstring for why request.user's personal tier was the wrong thing to
    check here. business_pro_manager deliberately has a lower PERSONAL
    tier than the org's own plan, specifically to prove the org's plan is
    what's being checked, not theirs.
    """

    def setUp(self):
        self.client = APIClient()
        ensure_default_account_tiers()
        self.owner = User.objects.create_user(phone="+237670006001", country="CM", password="pass1234")
        self.business_pro_manager = User.objects.create_user(phone="+237670006002", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Jobs Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Jobs Partner", slug="jobs-partner", main_conversation=conversation)
        PartnerMembership.objects.create(
            partner=self.partner, user=self.business_pro_manager, role="manager", status=PartnerMembershipStatus.MEMBER,
        )

        self.partner_tier = AccountTier.objects.filter(name__iexact="Partner").first()
        self.business_pro_tier = AccountTier.objects.filter(name__iexact="Business Pro").first()
        # The ORG's plan — this is what job_posting actually checks now.
        PartnerSubscription.objects.create(partner=self.partner, tier=self.partner_tier, status="active")
        # Personal tiers, deliberately mismatched from the org's plan to
        # prove personal tier is irrelevant to this gate.
        Subscription.objects.create(user=self.owner, tier=self.business_pro_tier, status="active")
        Subscription.objects.create(user=self.business_pro_manager, tier=self.business_pro_tier, status="active")

    def _url(self, suffix=""):
        return f"/api/v1/partners/{self.partner.id}/jobs/{suffix}"

    def test_partner_tier_owner_can_create_job_with_full_field_set(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            self._url(),
            {
                "title": "Youth Pastor", "description": "Lead youth ministry", "requirements": "5 years experience",
                "location": "Douala", "is_remote": False, "job_type": "part_time",
                "salary_min_cents": 50000, "salary_max_cents": 90000, "salary_currency": "XAF",
                "tags": ["ministry", "youth"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        job = PartnerJobPost.objects.get(partner=self.partner)
        self.assertEqual(job.location, "Douala")
        self.assertEqual(job.job_type, "part_time")
        self.assertEqual(job.salary_min_cents, 50000)
        self.assertEqual(job.salary_max_cents, 90000)
        self.assertEqual(job.salary_currency, "XAF")
        self.assertEqual(job.tags, ["ministry", "youth"])

    def test_manager_with_lower_personal_tier_can_still_create_job_when_org_has_partner_tier(self):
        # The manager's own personal Subscription is Business Pro (below
        # Partner) — must not matter, since the gate checks the ORG's plan.
        self.client.force_authenticate(self.business_pro_manager)

        response = self.client.post(self._url(), {"title": "Youth Pastor"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(PartnerJobPost.objects.filter(partner=self.partner, title="Youth Pastor").exists())

    def test_manager_with_lower_personal_tier_can_still_update_job_when_org_has_partner_tier(self):
        job = PartnerJobPost.objects.create(partner=self.partner, title="Existing role")
        self.client.force_authenticate(self.business_pro_manager)

        response = self.client.patch(self._url(f"{job.id}/"), {"title": "Renamed"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        job.refresh_from_db()
        self.assertEqual(job.title, "Renamed")

    def test_owner_cannot_create_job_when_the_org_itself_lacks_the_required_tier(self):
        # Flip it around: give the OWNER a personal Partner-tier
        # subscription, but downgrade the ORG's own plan below job_posting.
        # Must still be blocked — personal tier grants nothing here.
        self.partner.subscription.tier = self.business_pro_tier
        self.partner.subscription.save(update_fields=["tier"])
        Subscription.objects.filter(user=self.owner).update(tier=self.partner_tier)
        self.client.force_authenticate(self.owner)

        response = self.client.post(self._url(), {"title": "Should be blocked"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.assertFalse(PartnerJobPost.objects.filter(partner=self.partner, title="Should be blocked").exists())

    def test_update_job_persists_full_field_set(self):
        job = PartnerJobPost.objects.create(partner=self.partner, title="Existing role")
        self.client.force_authenticate(self.owner)

        response = self.client.patch(
            self._url(f"{job.id}/"),
            {"location": "Remote", "is_remote": True, "job_type": "contract", "salary_min_cents": 10000, "tags": ["remote"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        job.refresh_from_db()
        self.assertEqual(job.location, "Remote")
        self.assertTrue(job.is_remote)
        self.assertEqual(job.job_type, "contract")
        self.assertEqual(job.salary_min_cents, 10000)
        self.assertEqual(job.tags, ["remote"])


class PartnerMemberRoleUpdateApiTests(TestCase):
    """Covers the new PATCH /partners/{id}/members/{user_id}/ endpoint —
    previously there was no way anywhere (not either client, not Django
    admin) to change an existing member's PartnerMembership.role."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670003001", country="CM", password="pass1234")
        self.manager = User.objects.create_user(phone="+237670003002", country="CM", password="pass1234")
        self.target = User.objects.create_user(phone="+237670003003", country="CM", password="pass1234")
        self.stranger = User.objects.create_user(phone="+237670003004", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Role Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Role Partner", slug="role-partner", main_conversation=conversation)
        self.manager_membership = PartnerMembership.objects.create(
            partner=self.partner, user=self.manager, role="manager", status=PartnerMembershipStatus.MEMBER,
        )
        self.target_membership = PartnerMembership.objects.create(
            partner=self.partner, user=self.target, role="member", status=PartnerMembershipStatus.MEMBER,
        )

    def _url(self, user_id):
        return f"/api/v1/partners/{self.partner.id}/members/{user_id}/"

    def test_manager_can_promote_a_member(self):
        self.client.force_authenticate(self.manager)

        response = self.client.patch(self._url(self.target.id), {"role": "manager"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.target_membership.refresh_from_db()
        self.assertEqual(self.target_membership.role, "manager")
        self.assertEqual(response.data["member"]["membership_role"], "manager")

    def test_owner_can_demote_a_manager(self):
        self.client.force_authenticate(self.owner)

        response = self.client.patch(self._url(self.manager.id), {"role": "member"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.manager_membership.refresh_from_db()
        self.assertEqual(self.manager_membership.role, "member")

    def test_plain_member_cannot_change_roles(self):
        self.client.force_authenticate(self.target)

        response = self.client.patch(self._url(self.manager.id), {"role": "member"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_stranger_cannot_change_roles(self):
        # A total stranger isn't even in the partner's visible queryset —
        # get_object() 404s before the permission check runs, same as any
        # other partner-scoped endpoint (not a 403, since that would leak
        # that a partner with this id exists at all).
        self.client.force_authenticate(self.stranger)

        response = self.client.patch(self._url(self.target.id), {"role": "manager"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)

    def test_owner_role_cannot_be_changed_here(self):
        self.client.force_authenticate(self.owner)
        PartnerMembership.objects.create(partner=self.partner, user=self.owner, role="owner", status=PartnerMembershipStatus.MEMBER)

        response = self.client.patch(self._url(self.owner.id), {"role": "manager"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_invalid_role_is_rejected(self):
        self.client.force_authenticate(self.owner)

        response = self.client.patch(self._url(self.target.id), {"role": "owner"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.target_membership.refresh_from_db()
        self.assertEqual(self.target_membership.role, "member")

    def test_role_change_is_audit_logged(self):
        self.client.force_authenticate(self.owner)

        self.client.patch(self._url(self.target.id), {"role": "admin"}, format="json")

        self.assertTrue(
            PartnerAuditEvent.objects.filter(partner=self.partner, action="partner.member.role_changed").exists()
        )


class PartnerMemberDirectoryPaginationApiTests(TestCase):
    """GET /partners/{id}/members/ used to load EVERY membership and EVERY
    PartnerRoleAssignment for the whole org into memory on every request —
    no LIMIT, no pagination — and PATCH /members/{user_id}/ rebuilt that
    same full, unbounded directory just to return the one row it changed.
    Both would only get slower, not fail outright, on a small test
    org — the bug only shows up at real scale, which is exactly why it
    needs an explicit regression test rather than "existing tests still
    pass"."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670004001", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Big Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Big Partner", slug="big-partner", main_conversation=conversation)
        self.members = []
        for i in range(30):
            user = User.objects.create_user(phone=f"+23767000{5000 + i}", country="CM", password="pass1234")
            PartnerMembership.objects.create(partner=self.partner, user=user, role="member", status=PartnerMembershipStatus.MEMBER)
            self.members.append(user)

    def test_members_endpoint_returns_a_paginated_envelope_not_the_full_list(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/members/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        # A paginated response — meta.count reflects the true total (30
        # members + the owner's own membership, if any) while "results"
        # holds only one bounded page, never all of them in one payload.
        self.assertIn("meta", response.data)
        self.assertIn("results", response.data)
        self.assertLess(len(response.data["results"]), 31)
        self.assertGreaterEqual(response.data["meta"]["count"], 30)

    def test_second_page_returns_different_members_than_the_first(self):
        self.client.force_authenticate(self.owner)

        page1 = self.client.get(f"/api/v1/partners/{self.partner.id}/members/?page=1&page_size=10")
        page2 = self.client.get(f"/api/v1/partners/{self.partner.id}/members/?page=2&page_size=10")

        ids_page1 = {row["user_id"] for row in page1.data["results"]}
        ids_page2 = {row["user_id"] for row in page2.data["results"]}
        self.assertEqual(len(ids_page1), 10)
        self.assertEqual(len(ids_page2), 10)
        self.assertTrue(ids_page1.isdisjoint(ids_page2))


class PartnerApplicationReviewApiTests(TestCase):
    """PartnerApplicationStatus.REJECTED was a defined status nothing could
    ever set — approve_application existed but there was no reject
    counterpart anywhere, so a manager reviewing an applicant could only
    approve or leave it pending forever."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670004001", country="CM", password="pass1234")
        self.manager = User.objects.create_user(phone="+237670004002", country="CM", password="pass1234")
        self.applicant = User.objects.create_user(phone="+237670004003", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Application Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Application Partner", slug="application-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.manager, role="manager", status=PartnerMembershipStatus.MEMBER)
        self.application = PartnerApplication.objects.create(partner=self.partner, user=self.applicant, method="application", message="Let me in")

    def _url(self, suffix):
        return f"/api/v1/partners/{self.partner.id}/{suffix}"

    def test_manager_can_reject_a_pending_application(self):
        self.client.force_authenticate(self.manager)

        response = self.client.post(self._url(f"applications/{self.application.id}/reject/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "rejected")
        self.assertFalse(PartnerMembership.objects.filter(partner=self.partner, user=self.applicant).exists())

    def test_rejecting_an_already_decided_application_is_rejected(self):
        self.client.force_authenticate(self.manager)
        self.application.status = "approved"
        self.application.save(update_fields=["status"])

        response = self.client.post(self._url(f"applications/{self.application.id}/reject/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

    def test_applicant_cannot_reject_their_own_application(self):
        # The applicant has no membership on this partner, so they're outside
        # get_object()'s visible queryset — 404, not 403, same as every other
        # partner-scoped endpoint (doesn't leak that the partner exists).
        self.client.force_authenticate(self.applicant)

        response = self.client.post(self._url(f"applications/{self.application.id}/reject/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)

    def test_reject_is_audit_logged(self):
        self.client.force_authenticate(self.owner)

        self.client.post(self._url(f"applications/{self.application.id}/reject/"), {}, format="json")

        self.assertTrue(
            PartnerAuditEvent.objects.filter(partner=self.partner, action="partner.application.reject").exists()
        )

    def test_approve_is_audit_logged(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(self._url(f"applications/{self.application.id}/approve/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(
            PartnerAuditEvent.objects.filter(partner=self.partner, action="partner.application.approve").exists()
        )


class PartnerApplicationCvApiTests(TestCase):
    """A user's existing KIS profile (headline/bio/industry/experience/
    education/skills/projects) already existed but was never surfaced to a
    partner reviewing an application — PartnerApplicationDetailSerializer
    only ever returned display_name/phone/avatar_url, and there was no
    resume field on PartnerApplication to begin with."""

    def setUp(self):
        from datetime import date

        from apps.accounts.models import Education, Experience

        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670007001", country="CM", password="pass1234")
        self.applicant = User.objects.create_user(phone="+237670007002", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="CV Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="CV Partner", slug="cv-partner", main_conversation=conversation)

        self.applicant.profile.headline = "Backend Engineer"
        self.applicant.profile.bio = "I build things."
        self.applicant.profile.open_to_work = True
        self.applicant.profile.save(update_fields=["headline", "bio", "open_to_work", "updated_at"])
        Experience.objects.create(user=self.applicant, title="Engineer", description="Built stuff", start_date=date(2020, 1, 1), currently_working=True)
        Education.objects.create(user=self.applicant, school="KIS University", description="CS degree", start_date=date(2016, 1, 1), end_date=date(2020, 1, 1))

    def _apply(self, profile_visible=True):
        self.client.force_authenticate(self.applicant)
        return self.client.post(
            f"/api/v1/partners/{self.partner.id}/apply/",
            {"method": "application", "message": "hire me", "profile_visible": profile_visible},
            format="json",
        )

    def test_reviewer_sees_full_cv_when_profile_visible(self):
        self._apply(profile_visible=True)
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/applications/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        cv = response.data[0]["user"]["cv"]
        self.assertEqual(cv["headline"], "Backend Engineer")
        self.assertTrue(cv["open_to_work"])
        self.assertEqual(len(cv["experiences"]), 1)
        self.assertEqual(cv["experiences"][0]["title"], "Engineer")
        self.assertEqual(len(cv["educations"]), 1)
        self.assertEqual(cv["educations"][0]["school"], "KIS University")

    def test_no_cv_leaks_when_profile_not_visible(self):
        self._apply(profile_visible=False)
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/applications/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data[0]["user"], {"id": str(self.applicant.id)})


class UserAppShortcutApiTests(TestCase):
    """log_partner_audit is keyword-only (services.py:1316) but
    UserAppShortcutViewSet.create/destroy called it with four positional
    args — a TypeError on every real create/delete call, never caught by
    any existing test since none exercised this viewset at all."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670002001", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Shortcut Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Shortcut Partner", slug="shortcut-partner", main_conversation=conversation)
        self.client.force_authenticate(self.owner)

    def test_create_shortcut_does_not_crash_on_audit_logging(self):
        response = self.client.post(
            "/api/v1/partners/app-shortcuts/",
            {"partner_id": str(self.partner.id), "device_id": "device-1", "shortcut_name": "My Shortcut"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(PartnerAuditEvent.objects.filter(partner=self.partner, action="shortcut.created").exists())

    def test_destroy_shortcut_does_not_crash_on_audit_logging(self):
        create_response = self.client.post(
            "/api/v1/partners/app-shortcuts/",
            {"partner_id": str(self.partner.id), "device_id": "device-1", "shortcut_name": "My Shortcut"},
            format="json",
        )
        shortcut_id = create_response.data["id"]

        response = self.client.delete(f"/api/v1/partners/app-shortcuts/{shortcut_id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(PartnerAuditEvent.objects.filter(partner=self.partner, action="shortcut.removed").exists())


class PartnerOrganizationLinkApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670001001", country="CM", password="pass1234")
        self.stranger = User.objects.create_user(phone="+237670001002", country="CM", password="pass1234")
        self.partner = Partner.objects.create(owner=self.owner, name="Org Link Partner", slug="org-link-partner")

        from apps.commerce.models import Shop

        self.shop = Shop.objects.create(owner=self.owner, name="My Shop", slug="org-link-shop")
        self.strangers_shop = Shop.objects.create(owner=self.stranger, name="Not Mine", slug="org-link-not-mine")

    def test_owner_can_link_their_own_shop(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/organizations/",
            {"owner_type": "shop", "owner_id": str(self.shop.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["owner_id"], str(self.shop.id))

        list_response = self.client.get(f"/api/v1/partners/{self.partner.id}/organizations/")
        self.assertEqual(len(list_response.data["organizations"]), 1)

    def test_cannot_link_someone_elses_shop(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/organizations/",
            {"owner_type": "shop", "owner_id": str(self.strangers_shop.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_double_link_an_already_linked_organization(self):
        self.client.force_authenticate(self.owner)
        other_partner = Partner.objects.create(owner=self.owner, name="Other Partner", slug="org-link-other")
        self.client.post(
            f"/api/v1/partners/{self.partner.id}/organizations/",
            {"owner_type": "shop", "owner_id": str(self.shop.id)},
            format="json",
        )
        response = self.client.post(
            f"/api/v1/partners/{other_partner.id}/organizations/",
            {"owner_type": "shop", "owner_id": str(self.shop.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_linkable_organizations_excludes_already_linked(self):
        self.client.force_authenticate(self.owner)
        before = self.client.get(f"/api/v1/partners/{self.partner.id}/organizations/linkable/")
        self.assertEqual(len(before.data["organizations"]), 1)

        self.client.post(
            f"/api/v1/partners/{self.partner.id}/organizations/",
            {"owner_type": "shop", "owner_id": str(self.shop.id)},
            format="json",
        )
        after = self.client.get(f"/api/v1/partners/{self.partner.id}/organizations/linkable/")
        self.assertEqual(len(after.data["organizations"]), 0)

    def test_owner_can_unlink(self):
        self.client.force_authenticate(self.owner)
        link_response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/organizations/",
            {"owner_type": "shop", "owner_id": str(self.shop.id)},
            format="json",
        )
        link_id = link_response.data["id"]
        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/organizations/unlink/",
            {"link_id": link_id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(PartnerOrganizationLink.objects.filter(partner=self.partner).count(), 0)

    def test_broadcast_channel_link_shows_display_name_not_blank(self):
        # BroadcastChannel has no `name` field (it's `display_name`) —
        # _serialize_organization_link previously used a blind
        # getattr(org, "name", "") that silently returned "" for channels.
        from apps.broadcasts.models import BroadcastChannel

        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle="org-link-test-channel",
            display_name="My Test Channel",
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/organizations/",
            {"owner_type": "broadcast_channel", "owner_id": str(channel.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["name"], "My Test Channel")

        linkable_response = self.client.get(f"/api/v1/partners/{self.partner.id}/organizations/linkable/")
        # Already linked, so it shouldn't appear in the linkable list, but
        # any OTHER unlinked channel the owner has should show its
        # display_name correctly too.
        other_channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle="org-link-test-channel-2",
            display_name="My Second Channel",
        )
        linkable_response = self.client.get(f"/api/v1/partners/{self.partner.id}/organizations/linkable/")
        names = {org["name"] for org in linkable_response.data["organizations"]}
        self.assertIn("My Second Channel", names)

    def test_stranger_cannot_link_organizations_to_someone_elses_partner(self):
        # A total stranger (no ownership, no membership) never resolves the
        # partner at all — PartnerViewSet.get_queryset excludes it entirely,
        # so this 404s rather than 403s, same as every other stranger-access
        # case against this ViewSet.
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/organizations/",
            {"owner_type": "shop", "owner_id": str(self.strangers_shop.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class PartnerRoleDetailApiTests(TestCase):
    """roles() only ever had create/list — no way to rename a role, change
    its permissions, or delete it once created without going through
    Django admin directly."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670007001", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670007002", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Roles Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Roles Partner", slug="roles-partner", main_conversation=conversation)
        self.role = PartnerRole.objects.create(partner=self.partner, name="Greeter", permissions=["partner.reports.view"])

    def _url(self, role_id):
        return f"/api/v1/partners/{self.partner.id}/roles/{role_id}/"

    def test_owner_can_rename_role_and_change_permissions(self):
        self.client.force_authenticate(self.owner)

        response = self.client.patch(
            self._url(self.role.id),
            {"name": "Front Desk", "permissions": ["partner.reports.view", "partner.audit.view"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.role.refresh_from_db()
        self.assertEqual(self.role.name, "Front Desk")
        self.assertEqual(self.role.permissions, ["partner.reports.view", "partner.audit.view"])

    def test_owner_can_delete_an_unassigned_role(self):
        self.client.force_authenticate(self.owner)

        response = self.client.delete(self._url(self.role.id))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerRole.objects.filter(id=self.role.id).exists())

    def test_cannot_delete_a_role_that_is_still_assigned(self):
        PartnerRoleAssignment.objects.create(partner=self.partner, role=self.role, user=self.member, scope_type="global")
        self.client.force_authenticate(self.owner)

        response = self.client.delete(self._url(self.role.id))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(PartnerRole.objects.filter(id=self.role.id).exists())

    def test_plain_member_cannot_edit_roles(self):
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)
        self.client.force_authenticate(self.member)

        response = self.client.patch(self._url(self.role.id), {"name": "Hacked"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PartnerFineGrainedPermissionTests(TestCase):
    """kick/ban/category-management used to only ever check the coarse
    owner/admin/manager role, so a custom "Moderator" role granted via
    PartnerRolesPanel.tsx's permission catalog (partner.members.kick,
    partner.members.ban, partner.categories.manage) had zero effect —
    a plain member given that custom role still got 403."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670008001", country="CM", password="pass1234")
        self.moderator = User.objects.create_user(phone="+237670008002", country="CM", password="pass1234")
        self.target = User.objects.create_user(phone="+237670008003", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Fine-grained Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(
            owner=self.owner, name="Fine-grained Partner", slug="fine-grained-partner", main_conversation=conversation,
        )
        PartnerMembership.objects.create(partner=self.partner, user=self.moderator, role="member", status=PartnerMembershipStatus.MEMBER)
        PartnerMembership.objects.create(partner=self.partner, user=self.target, role="member", status=PartnerMembershipStatus.MEMBER)

    def _grant(self, user, *codenames):
        role = PartnerRole.objects.create(partner=self.partner, name="Custom", permissions=list(codenames))
        PartnerRoleAssignment.objects.create(partner=self.partner, role=role, user=user, scope_type="global")

    def _moderate_url(self):
        return f"/api/v1/partners/{self.partner.id}/members/{self.target.id}/moderate/"

    def test_plain_member_cannot_kick(self):
        self.client.force_authenticate(self.moderator)
        response = self.client.post(self._moderate_url(), {"action": "kick"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_with_kick_permission_can_kick(self):
        self._grant(self.moderator, "partner.members.kick")
        self.client.force_authenticate(self.moderator)
        response = self.client.post(self._moderate_url(), {"action": "kick"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_member_with_kick_permission_cannot_ban(self):
        self._grant(self.moderator, "partner.members.kick")
        self.client.force_authenticate(self.moderator)
        response = self.client.post(self._moderate_url(), {"action": "ban"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_with_ban_permission_can_ban(self):
        self._grant(self.moderator, "partner.members.ban")
        self.client.force_authenticate(self.moderator)
        response = self.client.post(self._moderate_url(), {"action": "ban"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_plain_member_cannot_create_category(self):
        self.client.force_authenticate(self.moderator)
        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/server-categories/", {"name": "General"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_with_categories_permission_can_create_category(self):
        self._grant(self.moderator, "partner.categories.manage")
        self.client.force_authenticate(self.moderator)
        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/server-categories/",
            {"name": "General", "slug": "general"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)


class PartnerRealtimeEventNotificationTests(TestCase):
    """Kick/ban/role-change/invite-redemption/category-creation used to be
    silent to any already-open client — the only way to see the change was
    a manual refresh. These assert the Nest push fires with the right
    event name and audience, without hitting a real network call."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670009001", country="CM", password="pass1234")
        self.target = User.objects.create_user(phone="+237670009002", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Realtime Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(
            owner=self.owner, name="Realtime Partner", slug="realtime-partner", main_conversation=conversation,
        )
        PartnerMembership.objects.create(partner=self.partner, user=self.target, role="member", status=PartnerMembershipStatus.MEMBER)

    @patch("apps.partners.views.notify_nest_of_partner_event")
    def test_kick_notifies_target_and_members(self, mock_notify):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/members/{self.target.id}/moderate/",
            {"action": "kick"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        self.assertEqual(kwargs["event"], "partner.member_kicked")
        self.assertIn(str(self.target.id), kwargs["user_ids"])
        self.assertEqual(kwargs["data"]["targetUserId"], str(self.target.id))

    @patch("apps.partners.views.notify_nest_of_partner_event")
    def test_mute_does_not_trigger_a_realtime_event(self, mock_notify):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/members/{self.target.id}/moderate/",
            {"action": "mute"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        mock_notify.assert_not_called()

    @patch("apps.partners.views.notify_nest_of_partner_event")
    def test_category_created_notifies_members(self, mock_notify):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/server-categories/",
            {"name": "General", "slug": "general"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        mock_notify.assert_called_once()
        self.assertEqual(mock_notify.call_args.kwargs["event"], "partner.category_created")


class PartnerListSerializerCanManageTests(TestCase):
    """Regression coverage for PartnerListSerializer.can_manage, added so
    the "connect this shop/institution to a partner" picker (which filters
    client-side on this field) can tell a low-privilege member apart from
    someone who can actually manage the partner - the pre-existing
    member_role field only reflected the legacy conversation base_role, not
    PartnerMembership.role, and would under-report a "manager" membership."""

    def setUp(self):
        self.owner = User.objects.create_user(phone="+237670002001", country="CM", password="pass1234")
        self.manager_member = User.objects.create_user(phone="+237670002002", country="CM", password="pass1234")
        self.plain_member = User.objects.create_user(phone="+237670002003", country="CM", password="pass1234")
        self.stranger = User.objects.create_user(phone="+237670002004", country="CM", password="pass1234")
        self.partner = Partner.objects.create(owner=self.owner, name="Serializer Partner", slug="serializer-partner")
        PartnerMembership.objects.create(
            partner=self.partner, user=self.manager_member,
            status=PartnerMembershipStatus.MEMBER, role="manager",
        )
        PartnerMembership.objects.create(
            partner=self.partner, user=self.plain_member,
            status=PartnerMembershipStatus.MEMBER, role="member",
        )

    def _can_manage_for(self, user) -> bool:
        from unittest.mock import MagicMock

        from apps.partners.serializers import PartnerListSerializer

        request = MagicMock(user=user)
        data = PartnerListSerializer(self.partner, context={"request": request}).data
        return data["can_manage"]

    def test_owner_can_manage(self):
        self.assertTrue(self._can_manage_for(self.owner))

    def test_manager_role_member_can_manage(self):
        self.assertTrue(self._can_manage_for(self.manager_member))

    def test_plain_role_member_cannot_manage(self):
        self.assertFalse(self._can_manage_for(self.plain_member))

    def test_stranger_cannot_manage(self):
        self.assertFalse(self._can_manage_for(self.stranger))


class PartnerDepartmentApiTests(TestCase):
    """Org Setup > Departments & Units."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670006001", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670006002", country="CM", password="pass1234")
        self.other_member = User.objects.create_user(phone="+237670006003", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Dept Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Dept Partner", slug="dept-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)

    def test_owner_can_create_department_with_members(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/departments/",
            {"name": "Finance", "description": "Money stuff", "member_ids": [str(self.member.id)]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["name"], "Finance")
        self.assertEqual(response.data["member_count"], 1)

    def test_plain_member_cannot_create_department(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/departments/", {"name": "Finance"}, format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_list_departments(self):
        PartnerDepartment.objects.create(partner=self.partner, name="Finance")
        self.client.force_authenticate(self.member)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/departments/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_update_replaces_member_roster(self):
        department = PartnerDepartment.objects.create(partner=self.partner, name="Finance")
        PartnerDepartmentMembership.objects.create(department=department, user=self.member)
        self.client.force_authenticate(self.owner)

        response = self.client.patch(
            f"/api/v1/partners/{self.partner.id}/departments/{department.id}/",
            {"member_ids": [str(self.other_member.id)]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        member_ids = set(department.memberships.values_list("user_id", flat=True))
        self.assertEqual(member_ids, {self.other_member.id})

    def test_department_members_endpoint(self):
        department = PartnerDepartment.objects.create(partner=self.partner, name="Finance")
        PartnerDepartmentMembership.objects.create(department=department, user=self.member)
        self.client.force_authenticate(self.member)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/departments/{department.id}/members/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["user_id"], str(self.member.id))

    def test_owner_can_delete_department(self):
        department = PartnerDepartment.objects.create(partner=self.partner, name="Finance")
        self.client.force_authenticate(self.owner)

        response = self.client.delete(f"/api/v1/partners/{self.partner.id}/departments/{department.id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerDepartment.objects.filter(id=department.id).exists())


class PartnerLocationApiTests(TestCase):
    """Org Setup > Locations & Branches."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670006101", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670006102", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Loc Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Loc Partner", slug="loc-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)

    def test_owner_can_create_location(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/locations/",
            {"name": "HQ", "city": "Douala", "country": "Cameroon", "is_primary": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["is_primary"])

    def test_plain_member_cannot_create_location(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/locations/", {"name": "HQ"}, format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_only_one_primary_location_at_a_time(self):
        first = PartnerLocation.objects.create(partner=self.partner, name="HQ", is_primary=True)
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/locations/",
            {"name": "Branch", "is_primary": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        first.refresh_from_db()
        self.assertFalse(first.is_primary)

    def test_member_can_list_locations(self):
        PartnerLocation.objects.create(partner=self.partner, name="HQ")
        self.client.force_authenticate(self.member)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/locations/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_owner_can_delete_location(self):
        location = PartnerLocation.objects.create(partner=self.partner, name="HQ")
        self.client.force_authenticate(self.owner)

        response = self.client.delete(f"/api/v1/partners/{self.partner.id}/locations/{location.id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerLocation.objects.filter(id=location.id).exists())


class PartnerAnalyticsApiTests(TestCase):
    """Analytics & Insights — the /analytics/ action already existed with a
    basic members/posts/engagement/revenue summary but no frontend ever
    called it; extended here with top_contributors/content_performance/
    growth_funnel/participation_depth/channel_health/community_heatmap and
    given a real panel."""

    def setUp(self):
        self.client = APIClient()
        from apps.accounts.tiers import ensure_default_account_tiers
        ensure_default_account_tiers()

        self.owner = User.objects.create_user(phone="+237670007001", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670007002", country="CM", password="pass1234")
        self.other_member = User.objects.create_user(phone="+237670007003", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Analytics Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Analytics Partner", slug="analytics-partner", main_conversation=conversation)

        from apps.accounts.models import AccountTier, Subscription
        partner_tier = AccountTier.objects.filter(name__iexact="Partner").first()
        PartnerSubscription.objects.create(partner=self.partner, tier=partner_tier, status="active")
        Subscription.objects.filter(user=self.owner).delete()

        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)

        from apps.partners.models import PartnerPostComment, PartnerPostReaction
        post = PartnerPost.objects.create(partner=self.partner, author=self.member, text_plain="Hello", text_preview="Hello")
        PartnerPostComment.objects.create(post=post, author=self.owner, text="Nice!")
        PartnerPostReaction.objects.create(post=post, user=self.owner, emoji="👍")

    def test_owner_gets_full_analytics_payload(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/analytics/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["top_contributors"][0]["user_id"], str(self.member.id))
        self.assertEqual(response.data["content_performance"][0]["reactions"], 1)
        self.assertEqual(response.data["content_performance"][0]["comments"], 1)
        self.assertEqual(response.data["growth_funnel"]["active_members"], 1)
        self.assertEqual(len(response.data["community_heatmap"]), 7)
        self.assertIn("message_velocity", response.data["unavailable_metrics"])

    def test_plain_member_without_reports_permission_forbidden(self):
        self.client.force_authenticate(self.member)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/analytics/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_forbidden(self):
        outsider = User.objects.create_user(phone="+237670007099", country="CM", password="pass1234")
        self.client.force_authenticate(outsider)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/analytics/")

        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))


class PartnerLeadershipApiTests(TestCase):
    """Leadership & Org Tree — org_tree_view, leadership_directory,
    reporting_lines, span_of_control, role_alignment, leadership_scorecards,
    plus department notes (org_tree_notes/succession_plan/leadership_goals/
    onboarding_paths)."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670008001", country="CM", password="pass1234")
        self.lead = User.objects.create_user(phone="+237670008002", country="CM", password="pass1234")
        self.report = User.objects.create_user(phone="+237670008003", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Leadership Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Leadership Partner", slug="leadership-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.lead, role="member", status=PartnerMembershipStatus.MEMBER)
        PartnerMembership.objects.create(partner=self.partner, user=self.report, role="member", status=PartnerMembershipStatus.MEMBER)

        self.led_department = PartnerDepartment.objects.create(partner=self.partner, name="Youth Ministry", lead=self.lead)
        PartnerDepartmentMembership.objects.create(department=self.led_department, user=self.report)
        PartnerDepartment.objects.create(partner=self.partner, name="Unled Department")

    def test_leadership_payload_reflects_departments(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/leadership/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data["org_tree"]), 2)
        self.assertEqual(response.data["leadership_directory"][0]["user_id"], str(self.lead.id))
        self.assertEqual(response.data["leadership_directory"][0]["direct_reports"], 1)
        self.assertEqual(response.data["reporting_lines"][0]["user_id"], str(self.report.id))
        self.assertEqual(response.data["reporting_lines"][0]["reports_to_id"], str(self.lead.id))
        self.assertEqual(response.data["role_alignment"]["unaligned_departments"][0]["department_name"], "Unled Department")
        self.assertEqual(response.data["leadership_scorecards"][0]["user_id"], str(self.lead.id))
        self.assertIn("team_health", response.data["unavailable_metrics"])

    def test_member_can_view_leadership(self):
        self.client.force_authenticate(self.report)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/leadership/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_owner_can_create_and_delete_department_note(self):
        self.client.force_authenticate(self.owner)

        create_response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/department-notes/",
            {"department": self.led_department.id, "category": "succession", "title": "Backup lead", "body": "Consider Jane."},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        note_id = create_response.data["id"]

        list_response = self.client.get(
            f"/api/v1/partners/{self.partner.id}/department-notes/",
            {"department_id": self.led_department.id},
        )
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]["category"], "succession")

        delete_response = self.client.delete(f"/api/v1/partners/{self.partner.id}/department-notes/{note_id}/")
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)

    def test_plain_member_cannot_create_department_note(self):
        self.client.force_authenticate(self.report)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/department-notes/",
            {"department": self.led_department.id, "body": "hi"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PartnerResourceApiTests(TestCase):
    """Resource Library & Knowledge Base."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670009001", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670009002", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Resource Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Resource Partner", slug="resource-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)

    def test_owner_can_create_article_resource(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/resources/",
            {"kind": "article", "title": "Welcome Guide", "category": "onboarding", "body": "Welcome to the team!"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["title"], "Welcome Guide")

    def test_file_resource_requires_asset(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/resources/",
            {"kind": "file", "title": "Playbook"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_plain_member_cannot_create_resource(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/resources/",
            {"kind": "article", "title": "X", "body": "Y"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_list_and_filter_resources(self):
        from apps.partners.models import PartnerResource

        PartnerResource.objects.create(partner=self.partner, kind="article", title="A", category="onboarding")
        PartnerResource.objects.create(partner=self.partner, kind="article", title="B", category="policy")
        self.client.force_authenticate(self.member)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/resources/", {"category": "onboarding"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "A")

    def test_owner_can_delete_resource(self):
        from apps.partners.models import PartnerResource

        resource = PartnerResource.objects.create(partner=self.partner, kind="article", title="A")
        self.client.force_authenticate(self.owner)

        response = self.client.delete(f"/api/v1/partners/{self.partner.id}/resources/{resource.id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerResource.objects.filter(id=resource.id).exists())


class PartnerCalendarEventApiTests(TestCase):
    """Events Calendar."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670009101", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670009102", country="CM", password="pass1234")
        self.other_member = User.objects.create_user(phone="+237670009103", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Calendar Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Calendar Partner", slug="calendar-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)
        PartnerMembership.objects.create(partner=self.partner, user=self.other_member, role="member", status=PartnerMembershipStatus.MEMBER)

    def test_owner_can_create_event(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/calendar-events/",
            {
                "title": "Annual Retreat",
                "description": "Yearly gathering",
                "location": "Main Hall",
                "start_at": "2026-10-01T09:00:00Z",
                "end_at": "2026-10-01T17:00:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["title"], "Annual Retreat")
        self.assertEqual(response.data["rsvp_counts"], {"going": 0, "maybe": 0, "declined": 0})

    def test_plain_member_cannot_create_event(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/calendar-events/",
            {"title": "X", "start_at": "2026-10-01T09:00:00Z", "end_at": "2026-10-01T10:00:00Z"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admins_only_event_hidden_from_plain_members(self):
        from apps.partners.models import PartnerCalendarEvent

        PartnerCalendarEvent.objects.create(
            partner=self.partner, title="Admin sync", visibility="admins_only",
            start_at="2026-10-01T09:00:00Z", end_at="2026-10-01T10:00:00Z",
        )
        self.client.force_authenticate(self.member)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/calendar-events/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

        self.client.force_authenticate(self.owner)
        owner_response = self.client.get(f"/api/v1/partners/{self.partner.id}/calendar-events/")
        self.assertEqual(len(owner_response.data), 1)

    def test_member_can_rsvp_and_change_status(self):
        from apps.partners.models import PartnerCalendarEvent, PartnerCalendarRsvp

        event = PartnerCalendarEvent.objects.create(
            partner=self.partner, title="Potluck", start_at="2026-10-01T09:00:00Z", end_at="2026-10-01T10:00:00Z",
        )
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/calendar-events/{event.id}/rsvp/",
            {"status": "going"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(PartnerCalendarRsvp.objects.get(event=event, user=self.member).status, "going")

        response2 = self.client.post(
            f"/api/v1/partners/{self.partner.id}/calendar-events/{event.id}/rsvp/",
            {"status": "declined"},
            format="json",
        )
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(PartnerCalendarRsvp.objects.filter(event=event, user=self.member).count(), 1)
        self.assertEqual(PartnerCalendarRsvp.objects.get(event=event, user=self.member).status, "declined")

    def test_owner_can_view_attendees_but_member_cannot(self):
        from apps.partners.models import PartnerCalendarEvent, PartnerCalendarRsvp

        event = PartnerCalendarEvent.objects.create(
            partner=self.partner, title="Board Meeting", start_at="2026-10-01T09:00:00Z", end_at="2026-10-01T10:00:00Z",
        )
        PartnerCalendarRsvp.objects.create(event=event, user=self.member, status="going")

        self.client.force_authenticate(self.member)
        denied = self.client.get(f"/api/v1/partners/{self.partner.id}/calendar-events/{event.id}/attendees/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        allowed = self.client.get(f"/api/v1/partners/{self.partner.id}/calendar-events/{event.id}/attendees/")
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(len(allowed.data), 1)
        self.assertEqual(allowed.data[0]["status"], "going")

    def test_owner_can_delete_event(self):
        from apps.partners.models import PartnerCalendarEvent

        event = PartnerCalendarEvent.objects.create(
            partner=self.partner, title="Cancelled Event", start_at="2026-10-01T09:00:00Z", end_at="2026-10-01T10:00:00Z",
        )
        self.client.force_authenticate(self.owner)

        response = self.client.delete(f"/api/v1/partners/{self.partner.id}/calendar-events/{event.id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerCalendarEvent.objects.filter(id=event.id).exists())


class PartnerAnnouncementSchedulingApiTests(TestCase):
    """Broadcast Center & Announcement Scheduler."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670009201", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670009202", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Broadcast Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        ConversationMember.objects.create(conversation=conversation, user=self.member, base_role=BaseConversationRole.MEMBER)
        self.partner = Partner.objects.create(owner=self.owner, name="Broadcast Partner", slug="broadcast-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)

    def test_owner_can_schedule_an_announcement(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            "/api/v1/partners/posts/",
            {
                "partner": str(self.partner.id),
                "text_plain": "We're closed next week",
                "scheduled_for": "2026-12-01T09:00:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        post = PartnerPost.objects.get(id=response.data["id"])
        self.assertEqual(post.status, "scheduled")
        self.assertIsNotNone(post.scheduled_for)

    def test_plain_member_cannot_schedule_an_announcement(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            "/api/v1/partners/posts/",
            {"partner": str(self.partner.id), "text_plain": "X", "scheduled_for": "2026-12-01T09:00:00Z"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_scheduling_in_the_past_is_rejected(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            "/api/v1/partners/posts/",
            {"partner": str(self.partner.id), "text_plain": "X", "scheduled_for": "2020-01-01T09:00:00Z"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scheduled_post_hidden_from_feed_and_other_members(self):
        post = PartnerPost.objects.create(
            partner=self.partner, author=self.owner, text_plain="Hidden", text_preview="Hidden",
            status="scheduled", scheduled_for="2026-12-01T09:00:00Z",
        )
        self.client.force_authenticate(self.member)

        response = self.client.get(f"/api/v1/partners/posts/?partner={self.partner.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        post_ids = [row["id"] for row in response.data.get("results", response.data)]
        self.assertNotIn(str(post.id), post_ids)

    def test_owner_can_see_queue_but_member_cannot(self):
        PartnerPost.objects.create(
            partner=self.partner, author=self.owner, text_plain="Queued", text_preview="Queued",
            status="scheduled", scheduled_for="2026-12-01T09:00:00Z",
        )
        self.client.force_authenticate(self.member)
        denied = self.client.get(f"/api/v1/partners/posts/queue/?partner={self.partner.id}")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        allowed = self.client.get(f"/api/v1/partners/posts/queue/?partner={self.partner.id}")
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(len(allowed.data), 1)

    def test_owner_can_publish_now(self):
        post = PartnerPost.objects.create(
            partner=self.partner, author=self.owner, text_plain="Queued", text_preview="Queued",
            status="scheduled", scheduled_for="2026-12-01T09:00:00Z",
        )
        self.client.force_authenticate(self.owner)

        response = self.client.post(f"/api/v1/partners/posts/{post.id}/publish-now/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        post.refresh_from_db()
        self.assertEqual(post.status, "published")
        self.assertIsNone(post.scheduled_for)

    def test_author_can_cancel_own_scheduled_post(self):
        post = PartnerPost.objects.create(
            partner=self.partner, author=self.owner, text_plain="Queued", text_preview="Queued",
            status="scheduled", scheduled_for="2026-12-01T09:00:00Z",
        )
        self.client.force_authenticate(self.owner)

        response = self.client.post(f"/api/v1/partners/posts/{post.id}/cancel-scheduled/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerPost.objects.filter(id=post.id).exists())

    def test_sweep_task_publishes_due_posts(self):
        from apps.partners.tasks import publish_due_scheduled_posts

        due_post = PartnerPost.objects.create(
            partner=self.partner, author=self.owner, text_plain="Due", text_preview="Due",
            status="scheduled", scheduled_for="2020-01-01T09:00:00Z",
        )
        future_post = PartnerPost.objects.create(
            partner=self.partner, author=self.owner, text_plain="Future", text_preview="Future",
            status="scheduled", scheduled_for="2026-12-01T09:00:00Z",
        )

        result = publish_due_scheduled_posts()

        self.assertEqual(result["published"], 1)
        due_post.refresh_from_db()
        future_post.refresh_from_db()
        self.assertEqual(due_post.status, "published")
        self.assertEqual(future_post.status, "scheduled")


class PartnerSupportTicketApiTests(TestCase):
    """Support Inbox / Helpdesk."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670009301", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670009302", country="CM", password="pass1234")
        self.other_member = User.objects.create_user(phone="+237670009303", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Helpdesk Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        ConversationMember.objects.create(conversation=conversation, user=self.member, base_role=BaseConversationRole.MEMBER)
        self.partner = Partner.objects.create(owner=self.owner, name="Helpdesk Partner", slug="helpdesk-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)
        PartnerMembership.objects.create(partner=self.partner, user=self.other_member, role="member", status=PartnerMembershipStatus.MEMBER)

    def test_member_can_submit_ticket(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/support-tickets/",
            {"subject": "Can't access group chat", "description": "Getting an error", "priority": "high"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["status"], "open")
        self.assertEqual(response.data["priority"], "high")

    def test_member_only_sees_own_tickets_admin_sees_all(self):
        from apps.partners.models import SupportTicket

        SupportTicket.objects.create(partner=self.partner, requester=self.member, subject="Mine")
        SupportTicket.objects.create(partner=self.partner, requester=self.other_member, subject="Not mine")

        self.client.force_authenticate(self.member)
        member_response = self.client.get(f"/api/v1/partners/{self.partner.id}/support-tickets/")
        self.assertEqual(len(member_response.data), 1)
        self.assertEqual(member_response.data[0]["subject"], "Mine")

        self.client.force_authenticate(self.owner)
        owner_response = self.client.get(f"/api/v1/partners/{self.partner.id}/support-tickets/")
        self.assertEqual(len(owner_response.data), 2)

    def test_member_cannot_view_others_ticket_detail(self):
        from apps.partners.models import SupportTicket

        ticket = SupportTicket.objects.create(partner=self.partner, requester=self.other_member, subject="Private")
        self.client.force_authenticate(self.member)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/support-tickets/{ticket.id}/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_cannot_change_ticket_status(self):
        from apps.partners.models import SupportTicket

        ticket = SupportTicket.objects.create(partner=self.partner, requester=self.member, subject="Mine")
        self.client.force_authenticate(self.member)

        response = self.client.patch(
            f"/api/v1/partners/{self.partner.id}/support-tickets/{ticket.id}/",
            {"status": "resolved"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_assign_and_resolve_ticket_and_resolved_at_is_stamped(self):
        from apps.partners.models import SupportTicket

        ticket = SupportTicket.objects.create(partner=self.partner, requester=self.member, subject="Mine")
        self.client.force_authenticate(self.owner)

        response = self.client.patch(
            f"/api/v1/partners/{self.partner.id}/support-tickets/{ticket.id}/",
            {"status": "resolved", "assignee": str(self.owner.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "resolved")
        self.assertEqual(ticket.assignee_id, self.owner.id)
        self.assertIsNotNone(ticket.resolved_at)

    def test_requester_and_admin_can_reply_but_internal_note_hidden_from_requester(self):
        from apps.partners.models import SupportTicket

        ticket = SupportTicket.objects.create(partner=self.partner, requester=self.member, subject="Mine")

        self.client.force_authenticate(self.member)
        reply_response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/support-tickets/{ticket.id}/replies/",
            {"body": "Any update?"},
            format="json",
        )
        self.assertEqual(reply_response.status_code, status.HTTP_201_CREATED, reply_response.data)

        self.client.force_authenticate(self.owner)
        note_response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/support-tickets/{ticket.id}/replies/",
            {"body": "Escalating internally", "is_internal_note": True},
            format="json",
        )
        self.assertEqual(note_response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(note_response.data["is_internal_note"])

        self.client.force_authenticate(self.member)
        member_replies = self.client.get(f"/api/v1/partners/{self.partner.id}/support-tickets/{ticket.id}/replies/")
        self.assertEqual(len(member_replies.data), 1)

        self.client.force_authenticate(self.owner)
        owner_replies = self.client.get(f"/api/v1/partners/{self.partner.id}/support-tickets/{ticket.id}/replies/")
        self.assertEqual(len(owner_replies.data), 2)

    def test_member_cannot_force_internal_note(self):
        from apps.partners.models import SupportTicket, SupportTicketReply

        ticket = SupportTicket.objects.create(partner=self.partner, requester=self.member, subject="Mine")
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/support-tickets/{ticket.id}/replies/",
            {"body": "Trying to sneak a note", "is_internal_note": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        reply = SupportTicketReply.objects.get(id=response.data["id"])
        self.assertFalse(reply.is_internal_note)

    def test_owner_can_view_summary_but_member_cannot(self):
        from apps.partners.models import SupportTicket

        SupportTicket.objects.create(partner=self.partner, requester=self.member, subject="A")
        SupportTicket.objects.create(partner=self.partner, requester=self.member, subject="B", status="resolved")

        self.client.force_authenticate(self.member)
        denied = self.client.get(f"/api/v1/partners/{self.partner.id}/support-inbox-summary/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        allowed = self.client.get(f"/api/v1/partners/{self.partner.id}/support-inbox-summary/")
        self.assertEqual(allowed.status_code, status.HTTP_200_OK, allowed.data)
        self.assertEqual(allowed.data["total"], 2)
        self.assertEqual(allowed.data["counts"]["open"], 1)
        self.assertEqual(allowed.data["counts"]["resolved"], 1)


class PartnerPostTemplateApiTests(TestCase):
    """Post Templates."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670009401", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670009402", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Templates Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Templates Partner", slug="templates-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)

    def test_owner_can_create_template(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/post-templates/",
            {"title": "Office closed", "body": "Our office will be closed on {date} for {reason}."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["title"], "Office closed")

    def test_plain_member_cannot_manage_templates(self):
        self.client.force_authenticate(self.member)

        create = self.client.post(
            f"/api/v1/partners/{self.partner.id}/post-templates/",
            {"title": "X", "body": "Y"},
            format="json",
        )
        self.assertEqual(create.status_code, status.HTTP_403_FORBIDDEN)

        listing = self.client.get(f"/api/v1/partners/{self.partner.id}/post-templates/")
        self.assertEqual(listing.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_list_update_and_delete_template(self):
        from apps.partners.models import PartnerPostTemplate

        template = PartnerPostTemplate.objects.create(partner=self.partner, title="Welcome", body="Hi there!")
        self.client.force_authenticate(self.owner)

        listing = self.client.get(f"/api/v1/partners/{self.partner.id}/post-templates/")
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(len(listing.data), 1)

        update = self.client.patch(
            f"/api/v1/partners/{self.partner.id}/post-templates/{template.id}/",
            {"body": "Hi there, welcome aboard!"},
            format="json",
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK, update.data)
        self.assertEqual(update.data["body"], "Hi there, welcome aboard!")

        delete = self.client.delete(f"/api/v1/partners/{self.partner.id}/post-templates/{template.id}/")
        self.assertEqual(delete.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerPostTemplate.objects.filter(id=template.id).exists())


class PartnerSurveyApiTests(TestCase):
    """Feedback Hub & Surveys."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670009501", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670009502", country="CM", password="pass1234")
        self.other_member = User.objects.create_user(phone="+237670009503", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Survey Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Survey Partner", slug="survey-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)
        PartnerMembership.objects.create(partner=self.partner, user=self.other_member, role="member", status=PartnerMembershipStatus.MEMBER)

    def _create_open_survey_with_questions(self):
        from apps.partners.models import PartnerSurvey, PartnerSurveyQuestion

        survey = PartnerSurvey.objects.create(partner=self.partner, title="Ministry feedback", status="open")
        choice_q = PartnerSurveyQuestion.objects.create(
            survey=survey, text="How did you hear about us?", question_type="single_choice",
            options=[{"id": "friend", "label": "Friend"}, {"id": "social", "label": "Social media"}],
            order=1,
        )
        rating_q = PartnerSurveyQuestion.objects.create(
            survey=survey, text="Rate your experience", question_type="rating", order=2,
        )
        text_q = PartnerSurveyQuestion.objects.create(
            survey=survey, text="Any comments?", question_type="text", required=False, order=3,
        )
        return survey, choice_q, rating_q, text_q

    def test_owner_can_create_survey_with_nested_questions(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/surveys/",
            {
                "title": "New Member Survey",
                "description": "Help us improve",
                "status": "open",
                "questions": [
                    {"text": "How satisfied are you?", "question_type": "rating", "order": 1},
                    {"text": "Comments", "question_type": "text", "required": False, "order": 2},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["question_count"], 2)

    def test_plain_member_cannot_create_survey(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/surveys/",
            {"title": "X"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_only_sees_open_surveys_admin_sees_all(self):
        from apps.partners.models import PartnerSurvey

        PartnerSurvey.objects.create(partner=self.partner, title="Draft survey", status="draft")
        PartnerSurvey.objects.create(partner=self.partner, title="Open survey", status="open")

        self.client.force_authenticate(self.member)
        member_response = self.client.get(f"/api/v1/partners/{self.partner.id}/surveys/")
        self.assertEqual(len(member_response.data), 1)
        self.assertEqual(member_response.data[0]["title"], "Open survey")

        self.client.force_authenticate(self.owner)
        owner_response = self.client.get(f"/api/v1/partners/{self.partner.id}/surveys/")
        self.assertEqual(len(owner_response.data), 2)

    def test_member_can_respond_once_and_second_attempt_is_rejected(self):
        survey, choice_q, rating_q, text_q = self._create_open_survey_with_questions()
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/surveys/{survey.id}/respond/",
            {
                "answers": [
                    {"question": choice_q.id, "value": {"choice_id": "friend"}},
                    {"question": rating_q.id, "value": {"value": 5}},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(len(response.data["answers"]), 2)

        again = self.client.post(
            f"/api/v1/partners/{self.partner.id}/surveys/{survey.id}/respond/",
            {"answers": [{"question": rating_q.id, "value": {"value": 3}}]},
            format="json",
        )
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_required_question_must_be_answered(self):
        survey, choice_q, rating_q, text_q = self._create_open_survey_with_questions()
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/surveys/{survey.id}/respond/",
            {"answers": [{"question": text_q.id, "value": {"text": "Great!"}}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_view_aggregated_results(self):
        survey, choice_q, rating_q, text_q = self._create_open_survey_with_questions()
        self.client.force_authenticate(self.member)
        self.client.post(
            f"/api/v1/partners/{self.partner.id}/surveys/{survey.id}/respond/",
            {
                "answers": [
                    {"question": choice_q.id, "value": {"choice_id": "friend"}},
                    {"question": rating_q.id, "value": {"value": 4}},
                    {"question": text_q.id, "value": {"text": "Loved it"}},
                ],
            },
            format="json",
        )
        self.client.force_authenticate(self.other_member)
        self.client.post(
            f"/api/v1/partners/{self.partner.id}/surveys/{survey.id}/respond/",
            {
                "answers": [
                    {"question": choice_q.id, "value": {"choice_id": "friend"}},
                    {"question": rating_q.id, "value": {"value": 2}},
                ],
            },
            format="json",
        )

        self.client.force_authenticate(self.member)
        denied = self.client.get(f"/api/v1/partners/{self.partner.id}/surveys/{survey.id}/results/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        allowed = self.client.get(f"/api/v1/partners/{self.partner.id}/surveys/{survey.id}/results/")
        self.assertEqual(allowed.status_code, status.HTTP_200_OK, allowed.data)
        self.assertEqual(allowed.data["total_responses"], 2)
        by_id = {q["question_id"]: q for q in allowed.data["questions"]}
        self.assertEqual(by_id[choice_q.id]["choice_counts"], {"friend": 2})
        self.assertEqual(by_id[rating_q.id]["average_rating"], 3.0)
        self.assertEqual(by_id[text_q.id]["text_answers"], ["Loved it"])

    def test_survey_closed_to_responses_when_not_open(self):
        from apps.partners.models import PartnerSurvey

        survey = PartnerSurvey.objects.create(partner=self.partner, title="Closed survey", status="closed")
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/surveys/{survey.id}/respond/",
            {"answers": []},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class PartnerBudgetApiTests(TestCase):
    """Budget Tracking."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670009601", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670009602", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Budget Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Budget Partner", slug="budget-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)

    def test_owner_can_create_budget(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/budgets/",
            {"name": "Youth Ministry 2026", "allocated_amount": "5000.00", "currency": "USD"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["spent_amount"], 0)
        self.assertEqual(response.data["percent_used"], 0)

    def test_plain_member_cannot_manage_budgets(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/budgets/",
            {"name": "X", "allocated_amount": "100.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        listing = self.client.get(f"/api/v1/partners/{self.partner.id}/budgets/")
        self.assertEqual(listing.status_code, status.HTTP_403_FORBIDDEN)

    def test_expenses_reduce_remaining_and_update_percent_used(self):
        from apps.partners.models import PartnerBudget

        budget = PartnerBudget.objects.create(partner=self.partner, name="Missions Fund", allocated_amount="1000.00")
        self.client.force_authenticate(self.owner)

        expense_response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/budgets/{budget.id}/expenses/",
            {"description": "Airfare", "amount": "250.00"},
            format="json",
        )
        self.assertEqual(expense_response.status_code, status.HTTP_201_CREATED, expense_response.data)

        budget_response = self.client.get(f"/api/v1/partners/{self.partner.id}/budgets/")
        self.assertEqual(budget_response.status_code, status.HTTP_200_OK)
        entry = budget_response.data[0]
        self.assertEqual(str(entry["spent_amount"]), "250.00")
        self.assertEqual(str(entry["remaining_amount"]), "750.00")
        self.assertEqual(entry["percent_used"], 25.0)

    def test_owner_can_delete_expense_and_budget(self):
        from apps.partners.models import PartnerBudget, PartnerBudgetExpense

        budget = PartnerBudget.objects.create(partner=self.partner, name="Outreach", allocated_amount="500.00")
        expense = PartnerBudgetExpense.objects.create(budget=budget, description="Flyers", amount="50.00")
        self.client.force_authenticate(self.owner)

        delete_expense = self.client.delete(
            f"/api/v1/partners/{self.partner.id}/budgets/{budget.id}/expenses/{expense.id}/",
        )
        self.assertEqual(delete_expense.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerBudgetExpense.objects.filter(id=expense.id).exists())

        delete_budget = self.client.delete(f"/api/v1/partners/{self.partner.id}/budgets/{budget.id}/")
        self.assertEqual(delete_budget.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PartnerBudget.objects.filter(id=budget.id).exists())

    def test_budgets_filterable_by_department(self):
        from apps.partners.models import PartnerBudget, PartnerDepartment

        dept = PartnerDepartment.objects.create(partner=self.partner, name="Youth")
        PartnerBudget.objects.create(partner=self.partner, department=dept, name="Youth budget", allocated_amount="100.00")
        PartnerBudget.objects.create(partner=self.partner, name="General budget", allocated_amount="200.00")
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/budgets/", {"department": dept.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "Youth budget")


class PartnerVolunteerRosterApiTests(TestCase):
    """Volunteer Roster."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+237670009701", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237670009702", country="CM", password="pass1234")
        self.other_member = User.objects.create_user(phone="+237670009703", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Volunteer Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Volunteer Partner", slug="volunteer-partner", main_conversation=conversation)
        PartnerMembership.objects.create(partner=self.partner, user=self.member, role="member", status=PartnerMembershipStatus.MEMBER)
        PartnerMembership.objects.create(partner=self.partner, user=self.other_member, role="member", status=PartnerMembershipStatus.MEMBER)

    def test_owner_can_create_shift(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/volunteer-shifts/",
            {
                "title": "Sunday setup crew",
                "location": "Main Hall",
                "starts_at": "2026-10-01T07:00:00Z",
                "ends_at": "2026-10-01T09:00:00Z",
                "slots_total": 2,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["slots_remaining"], 2)

    def test_plain_member_cannot_create_shift(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/volunteer-shifts/",
            {"title": "X", "starts_at": "2026-10-01T07:00:00Z", "ends_at": "2026-10-01T09:00:00Z"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_sign_up_and_cancel(self):
        from apps.partners.models import PartnerVolunteerShift

        shift = PartnerVolunteerShift.objects.create(
            partner=self.partner, title="Cleanup", starts_at="2026-10-01T07:00:00Z", ends_at="2026-10-01T09:00:00Z", slots_total=2,
        )
        self.client.force_authenticate(self.member)

        signup = self.client.post(f"/api/v1/partners/{self.partner.id}/volunteer-shifts/{shift.id}/signup/", {}, format="json")
        self.assertEqual(signup.status_code, status.HTTP_201_CREATED, signup.data)
        self.assertEqual(signup.data["status"], "signed_up")

        again = self.client.post(f"/api/v1/partners/{self.partner.id}/volunteer-shifts/{shift.id}/signup/", {}, format="json")
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

        cancel = self.client.post(
            f"/api/v1/partners/{self.partner.id}/volunteer-shifts/{shift.id}/signup/", {"action": "cancel"}, format="json",
        )
        self.assertEqual(cancel.status_code, status.HTTP_200_OK)

        resignup = self.client.post(f"/api/v1/partners/{self.partner.id}/volunteer-shifts/{shift.id}/signup/", {}, format="json")
        self.assertEqual(resignup.status_code, status.HTTP_201_CREATED)

    def test_shift_rejects_signup_when_full(self):
        from apps.partners.models import PartnerVolunteerShift

        shift = PartnerVolunteerShift.objects.create(
            partner=self.partner, title="Small crew", starts_at="2026-10-01T07:00:00Z", ends_at="2026-10-01T09:00:00Z", slots_total=1,
        )
        self.client.force_authenticate(self.member)
        self.client.post(f"/api/v1/partners/{self.partner.id}/volunteer-shifts/{shift.id}/signup/", {}, format="json")

        self.client.force_authenticate(self.other_member)
        response = self.client.post(f"/api/v1/partners/{self.partner.id}/volunteer-shifts/{shift.id}/signup/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_view_roster_but_member_cannot(self):
        from apps.partners.models import PartnerVolunteerShift

        shift = PartnerVolunteerShift.objects.create(
            partner=self.partner, title="Ushers", starts_at="2026-10-01T07:00:00Z", ends_at="2026-10-01T09:00:00Z", slots_total=5,
        )
        self.client.force_authenticate(self.member)
        self.client.post(f"/api/v1/partners/{self.partner.id}/volunteer-shifts/{shift.id}/signup/", {}, format="json")

        denied = self.client.get(f"/api/v1/partners/{self.partner.id}/volunteer-shifts/{shift.id}/roster/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        allowed = self.client.get(f"/api/v1/partners/{self.partner.id}/volunteer-shifts/{shift.id}/roster/")
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(len(allowed.data), 1)
        self.assertEqual(allowed.data[0]["volunteer"], self.member.id)
