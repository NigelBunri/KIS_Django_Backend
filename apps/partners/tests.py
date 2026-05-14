from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.partners.models import (
    Partner,
    PartnerApplication,
    PartnerAutomationRule,
    PartnerInvite,
    PartnerJoinConfig,
    PartnerMembership,
    PartnerMembershipStatus,
    PartnerOnboardingProgress,
    PartnerModerationAction,
    PartnerOrganizationApp,
    PartnerOrganizationAppType,
    PartnerOrganizationProfile,
    PartnerPost,
    PartnerRole,
)
from apps.partners.serializers import PartnerDetailSerializer, PartnerOrganizationProfileSerializer, PartnerPostSerializer
from apps.verification.constants import VerificationBadgeCode, VerificationSubjectType
from apps.verification.models import VerificationBadge
from apps.verification.services import current_partner_verification_status, start_partner_verification_case


class PartnerApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = self._create_user("owner", "+237670000001")
        self.member = self._create_user("member", "+237670000002")
        self.manager = self._create_user("manager", "+237670000003")

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
        self.assertTrue(
            PartnerModerationAction.objects.filter(
                partner=partner,
                user=self.member,
                action_type="ban",
            ).exists()
        )

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
