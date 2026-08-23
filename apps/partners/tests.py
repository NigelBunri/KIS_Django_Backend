from unittest.mock import patch
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
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
    PartnerIntegration,
    PartnerInvite,
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
