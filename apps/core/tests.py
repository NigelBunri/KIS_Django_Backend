from django.test import TestCase
from django.test import override_settings
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone
import uuid
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import User, UserContact
from apps.core import models
from apps.broadcasts.models import (
    BroadcastChannel,
    BroadcastHealthProfile,
    BroadcastHealthInstitution,
    BroadcastHealthInstitutionMember,
    ChannelContent,
)
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.moderation.models import UserBlock
from apps.core.money import (
    frontend_kisc_major_to_micro,
    frontend_kisc_major_to_usd_cents,
    parse_frontend_money_to_cents,
)
from common.media_urls import absolutize_backend_media, strip_backend_origin


class SocialRecommendationFoundationTests(APITestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(phone="+237670030001", password="TestPass123!", country="CM")
        self.contact = User.objects.create_user(phone="+237670030002", password="TestPass123!", country="CM")
        self.blocked = User.objects.create_user(phone="+237670030003", password="TestPass123!", country="CM")
        UserContact.objects.create(
            user=self.viewer,
            contact_user=self.contact,
            contact_phone=self.contact.phone,
            contact_display_name="Trusted Contact",
        )
        UserBlock.objects.create(blocker=self.viewer, blocked=self.blocked, reason="recommendation_exclusion")
        BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.contact.id,
            owner_user=self.contact,
            handle="trusted-channel",
            display_name="Trusted Channel",
            category="bible",
            is_public=True,
            is_verified=True,
            subscriber_count=120,
        )
        BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.blocked.id,
            owner_user=self.blocked,
            handle="blocked-channel",
            display_name="Blocked Channel",
            category="market",
            is_public=True,
            subscriber_count=999,
        )

    def test_recommendation_foundation_excludes_blocked_users_and_private_signals(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get(reverse("core:social-recommendation-foundation"), {"limit": 6})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data
        serialized = str(payload)
        channel_titles = [item["title"] for item in payload["sections"]["channels"]]
        people_titles = [item["title"] for item in payload["sections"]["people"]]

        self.assertIn("Trusted Channel", channel_titles)
        self.assertNotIn("Blocked Channel", serialized)
        self.assertTrue(payload["controls"]["blocked_users_excluded"])
        self.assertTrue(payload["controls"]["christian_content_safe_ranking"])
        self.assertFalse(payload["privacy"]["health_data_exposed"])
        self.assertFalse(payload["privacy"]["payment_data_exposed"])
        self.assertTrue(any("670030002" in title or title == "Trusted Contact" for title in people_titles))

    def test_child_age_mode_applies_family_safe_recommendation_controls(self):
        self.viewer.preferences = {"family_accessibility": {"age_mode": "child", "hide_sensitive_commerce": False}}
        self.viewer.save(update_fields=["preferences"])
        self.client.force_authenticate(self.viewer)

        response = self.client.get(reverse("core:social-recommendation-foundation"), {"limit": 6})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["controls"]["age_mode"], "child")
        self.assertTrue(response.data["controls"]["commerce_hidden_for_child_mode"])
        self.assertEqual(response.data["sections"]["commerce"], [])


class UnifiedPlatformDashboardSummaryTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670031001", password="TestPass123!", country="CM")
        self.client.force_authenticate(self.user)
        BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.user.id,
            owner_user=self.user,
            handle="phase20-creator",
            display_name="Phase 20 Creator",
            category="bible",
            is_public=True,
            is_verified=True,
            subscriber_count=7,
        )

    def test_unified_dashboard_summary_is_safe_and_owner_scoped(self):
        from apps.commerce.models import Shop

        Shop.objects.create(
            owner=self.user,
            name="Phase 20 Shop",
            slug="phase-20-shop",
            description="USD-only readiness surface.",
            is_verified=True,
            followers_count=3,
        )

        response = self.client.get(reverse("core:core-unified-dashboard"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data
        serialized = str(payload).lower()

        self.assertEqual(payload["version"], "phase_20_dashboard_foundation")
        self.assertGreaterEqual(payload["counts"]["channels"], 1)
        self.assertGreaterEqual(payload["counts"]["shops"], 1)
        self.assertTrue(payload["privacy"]["no_secrets"])
        self.assertTrue(payload["privacy"]["no_raw_documents"])
        self.assertTrue(payload["privacy"]["no_payment_instrument_data"])
        self.assertIn("family_accessibility", payload)
        self.assertNotIn("card_number", serialized)


class StaffSafetyCommandCenterTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            phone="+237670032001",
            password="TestPass123!",
            country="CM",
            is_staff=True,
        )
        self.user = User.objects.create_user(phone="+237670032002", password="TestPass123!", country="CM")

    def test_staff_command_center_returns_safe_operational_summary(self):
        from apps.billing.models import DirectPaymentIntent
        from apps.media.models import MediaSafetyScan
        from apps.moderation.models import Flag
        from apps.notifications.models import Notification, NotificationDelivery

        target_id = uuid.uuid4()
        MediaSafetyScan.objects.create(
            owner=self.user,
            upload_id="phase21-upload",
            context="chat",
            status="pending_review",
            quarantine=True,
            requires_review=True,
        )
        Flag.objects.create(
            source="USER",
            target_type="POST",
            target_id=target_id,
            reporter_id=self.user.id,
            reason="Unsafe content report",
            severity="HIGH",
            status="PENDING",
        )
        intent = DirectPaymentIntent.objects.create(
            user=self.user,
            target_type=DirectPaymentIntent.TARGET_MARKETPLACE_ORDER,
            target_id=target_id,
            amount_cents=2500,
            currency="USD",
            status=DirectPaymentIntent.STATUS_PENDING,
            tx_ref=f"phase21-{uuid.uuid4()}",
        )
        note = Notification.objects.create(
            user_id=self.user.id,
            type="PHASE21",
            title="Phase 21",
            body="Command center test",
        )
        NotificationDelivery.objects.create(notification=note, channel="PUSH", status="FAILED")

        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse("core:core-admin-safety-command-center"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data
        serialized = str(payload).lower()

        self.assertEqual(payload["version"], "phase_21_safety_command_center")
        self.assertTrue(payload["privacy"]["staff_only"])
        self.assertTrue(payload["privacy"]["no_secrets"])
        self.assertGreaterEqual(payload["counts"]["media_open_queue"], 1)
        self.assertGreaterEqual(payload["counts"]["moderation_pending_flags"], 1)
        self.assertGreaterEqual(payload["counts"]["payment_pending_intents"], 1)
        self.assertGreaterEqual(payload["counts"]["notification_failed_deliveries"], 1)
        self.assertNotIn("raw_callback", serialized)
        self.assertNotIn("card_number", serialized)

    def test_command_center_requires_staff(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse("core:core-admin-safety-command-center"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PerformanceOfflinePolicyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670033001", password="TestPass123!", country="CM")
        self.client.force_authenticate(self.user)

    def test_policy_exposes_safe_performance_foundation(self):
        response = self.client.get(reverse("core:core-performance-offline-policy"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data
        serialized = str(payload).lower()

        self.assertEqual(payload["version"], "phase_22_performance_offline_foundation")
        self.assertTrue(payload["mode"]["offline_first_enabled"])
        self.assertTrue(payload["mode"]["stale_while_revalidate_enabled"])
        self.assertTrue(payload["mode"]["request_deduplication_enabled"])
        self.assertTrue(payload["media_policy"]["placeholder_on_missing_thumbnail"])
        self.assertTrue(payload["pagination_policy"]["prefer_cursor"])
        self.assertTrue(payload["telemetry_policy"]["redacted"])
        self.assertTrue(payload["privacy"]["no_secrets"])
        self.assertNotIn("card_number", serialized)

    def test_child_mode_defaults_to_low_bandwidth(self):
        self.user.preferences = {"family_accessibility": {"age_mode": "child"}}
        self.user.save(update_fields=["preferences"])

        response = self.client.get(reverse("core:core-performance-offline-policy"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["mode"]["low_bandwidth_default"])


class SecurityPrivacyLaunchGateTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            phone="+237670034001",
            password="TestPass123!",
            country="CM",
            is_staff=True,
        )
        self.user = User.objects.create_user(phone="+237670034002", password="TestPass123!", country="CM")

    def test_security_launch_gate_is_staff_only_and_redacted(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse("core:core-admin-security-launch-gate"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data
        serialized = str(payload).lower()

        self.assertEqual(payload["version"], "phase_23_security_privacy_child_safety_launch_gate")
        self.assertIn(payload["summary"]["go_live_status"], {"go", "conditional", "blocked"})
        self.assertGreater(payload["summary"]["total_checks"], 10)
        self.assertTrue(payload["privacy"]["staff_only"])
        self.assertTrue(payload["privacy"]["no_secret_values"])
        self.assertNotIn("password", serialized)
        self.assertNotIn("card_number", serialized)

    def test_security_launch_gate_rejects_non_staff(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse("core:core-admin-security-launch-gate"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class MonetizationSafetySummaryTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670035001", password="TestPass123!", country="CM")
        self.client.force_authenticate(self.user)

    def test_monetization_summary_keeps_promotional_credits_non_cash(self):
        response = self.client.get(reverse("core:core-monetization-safety-summary"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data
        serialized = str(payload).lower()

        self.assertEqual(payload["version"], "phase_24_monetization_without_legal_risk")
        self.assertEqual(payload["principles"]["platform_currency"], "USD")
        self.assertTrue(payload["principles"]["promotional_credits_non_cash"])
        self.assertTrue(payload["principles"]["promotional_credits_non_transferable"])
        self.assertTrue(payload["principles"]["promotional_credits_non_withdrawable"])
        self.assertTrue(payload["principles"]["promotional_credits_not_exchange_rated"])
        self.assertFalse(payload["legacy_flags"]["wallet_deposit_enabled"])
        self.assertFalse(payload["legacy_flags"]["wallet_transfer_enabled"])
        self.assertFalse(payload["legacy_flags"]["commerce_wallet_checkout_enabled"])
        self.assertTrue(payload["privacy"]["no_secret_values"])
        self.assertTrue(payload["privacy"]["no_payment_instrument_data"])
        self.assertNotIn("flw_secret", serialized)
        self.assertNotIn("card_number", serialized)

    @override_settings(KIS_LEGACY_WALLET_TRANSFER_ENABLED=True)
    def test_legacy_transfer_flag_blocks_launch_status(self):
        response = self.client.get(reverse("core:core-monetization-safety-summary"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["legacy_flags"]["wallet_transfer_enabled"], True)
        self.assertEqual(response.data["summary"]["go_live_status"], "blocked")
        self.assertGreaterEqual(response.data["summary"]["critical_failures"], 1)


class AIAssistanceSafetyPolicyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670036001", password="TestPass123!", country="CM")
        self.client.force_authenticate(self.user)

    def test_ai_safety_policy_is_redacted_and_guarded(self):
        response = self.client.get(reverse("core:core-ai-safety-policy"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data
        serialized = str(payload).lower()

        self.assertEqual(payload["version"], "phase_25_ai_assistance_christian_safety_boundaries")
        self.assertFalse(payload["provider"]["live_calls_enabled"])
        self.assertFalse(payload["provider"]["secret_values_exposed"])
        self.assertTrue(payload["boundaries"]["christian_principles_required"])
        self.assertTrue(payload["boundaries"]["pornographic_or_sexual_content_blocked"])
        self.assertTrue(payload["boundaries"]["medical_diagnosis_blocked"])
        self.assertTrue(payload["boundaries"]["financial_advice_blocked"])
        self.assertTrue(payload["privacy_controls"]["input_redaction_required"])
        self.assertTrue(payload["privacy_controls"]["output_moderation_required"])
        self.assertFalse(payload["privacy_controls"]["store_raw_prompts"])
        self.assertFalse(payload["privacy_controls"]["store_raw_responses"])
        self.assertIn("bible_study_help", payload["assistant_surfaces"])
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("card_number", serialized)

    @override_settings(KIS_AI_LIVE_PROVIDER_CALLS_ENABLED=True, KIS_AI_PROVIDER="", KIS_AI_OUTPUT_MODERATION_REQUIRED=False)
    def test_ai_live_calls_without_guardrails_block_launch_status(self):
        response = self.client.get(reverse("core:core-ai-safety-policy"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["provider"]["live_calls_enabled"])
        self.assertEqual(response.data["summary"]["go_live_status"], "blocked")
        self.assertGreaterEqual(response.data["summary"]["critical_failures"], 1)


class CommunityPermissionHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+237670000001",
            password="StrongPass123",
            country="CM",
            email="core-tests@example.com",
        )
        self.community = models.Community.objects.create(
            slug="core-permission-tests",
            name="Core Permission Tests",
        )
        self.user_ct = ContentType.objects.get_for_model(User)
        self.community_ct = ContentType.objects.get_for_model(models.Community)
        self.permission = "community.manage"

    def _add_ace(self, *, effect: str, permissions: list[str]):
        return models.AccessControlEntry.objects.create(
            principal_content_type=self.user_ct,
            principal_object_id=str(self.user.id),
            target_content_type=self.community_ct,
            target_object_id=str(self.community.id),
            permissions=permissions,
            effect=effect,
        )

    def test_can_user_on_community_without_matching_aces_returns_false(self):
        allowed = models.CommunityPermissionHelper.can_user_on_community(
            self.user,
            self.community,
            self.permission,
        )
        self.assertFalse(allowed)

    def test_can_user_on_community_with_allow_ace_returns_true(self):
        self._add_ace(effect=models.AccessControlEntry.EFFECT_ALLOW, permissions=[self.permission])

        allowed = models.CommunityPermissionHelper.can_user_on_community(
            self.user,
            self.community,
            self.permission,
        )
        self.assertTrue(allowed)

    def test_can_user_on_community_deny_ace_overrides_allow(self):
        self._add_ace(effect=models.AccessControlEntry.EFFECT_ALLOW, permissions=[self.permission])
        self._add_ace(effect=models.AccessControlEntry.EFFECT_DENY, permissions=[self.permission])

        allowed = models.CommunityPermissionHelper.can_user_on_community(
            self.user,
            self.community,
            self.permission,
        )
        self.assertFalse(allowed)


class FrontendMoneyNormalizationTests(TestCase):
    def test_frontend_kisc_major_to_usd_cents_scales_by_ten_thousand(self):
        self.assertEqual(frontend_kisc_major_to_usd_cents("100"), 1_000_000)

    def test_frontend_kisc_major_to_micro_scales_by_one_hundred_thousand(self):
        self.assertEqual(frontend_kisc_major_to_micro("100"), 10_000_000)

    def test_parse_frontend_money_to_cents_keeps_cents_unchanged(self):
        self.assertEqual(parse_frontend_money_to_cents({"amount_cents": 1250}), 1250)

    def test_parse_frontend_money_to_cents_normalizes_major_unit_kisc(self):
        self.assertEqual(parse_frontend_money_to_cents({"amount_kisc": "100"}), 1_000_000)


class BackendMediaUrlNormalizationTests(TestCase):
    def test_strips_backend_origin_before_save(self):
        self.assertEqual(
            strip_backend_origin("http://10.112.162.99:8000/media/institutions/logo.png"),
            "/media/institutions/logo.png",
        )

    def test_keeps_external_image_url_unchanged(self):
        self.assertEqual(
            strip_backend_origin("https://cdn.example.com/media/logo.png"),
            "https://cdn.example.com/media/logo.png",
        )

    def test_absolutizes_existing_backend_url_against_current_request(self):
        request = self.client.get("/").wsgi_request
        with override_settings(API_BASE_URL="http://10.112.162.99:8000", SITE_URL="http://10.112.162.99:8000"):
            self.assertEqual(
                absolutize_backend_media("http://10.112.162.99:8000/media/institutions/logo.png", request=request),
                "http://10.112.162.99:8000/media/institutions/logo.png",
            )


class UnifiedSearchApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+237670009001",
            password="StrongPass123",
            country="CM",
            display_name="Search Owner",
            username="search_owner",
        )
        self.peer = User.objects.create_user(
            phone="+237670009002",
            password="StrongPass123",
            country="CM",
            display_name="Faithful Friend",
            username="faithful_friend",
        )
        self.client.force_authenticate(self.user)
        self.conversation = Conversation.objects.create(
            type=ConversationType.DIRECT,
            title="Faithful conversation",
            created_by=self.user,
            last_message_preview="Pray without ceasing",
        )
        ConversationMember.objects.create(
            conversation=self.conversation,
            user=self.user,
            base_role=BaseConversationRole.OWNER,
        )
        ConversationMember.objects.create(
            conversation=self.conversation,
            user=self.peer,
            base_role=BaseConversationRole.MEMBER,
        )
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.user.id,
            owner_user=self.user,
            handle="faith-channel",
            display_name="Faith Channel",
            description="Public testimony channel",
            is_public=True,
        )
        self.content = ChannelContent.objects.create(
            channel=self.channel,
            content_type="text",
            title="Faith Teaching",
            text_plain="A teaching about faith and wisdom.",
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            created_by=self.user,
        )

    def test_unified_search_returns_grouped_permission_safe_results(self):
        response = self.client.get("/api/v1/core/search/unified/", {"q": "Faith", "groups": "contacts,conversations,channels,channel_content"})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertGreaterEqual(response.data["count"], 4)
        self.assertIn("contacts", response.data["groups"])
        self.assertIn("conversations", response.data["groups"])
        self.assertIn("channels", response.data["groups"])
        self.assertIn("channel_content", response.data["groups"])
        kinds = {row["kind"] for row in response.data["results"]}
        self.assertIn("contact", kinds)
        self.assertIn("conversation", kinds)
        self.assertIn("channel", kinds)
        self.assertIn("channel_content", kinds)

    def test_unified_search_requires_authentication(self):
        self.client.force_authenticate(None)

        response = self.client.get("/api/v1/core/search/unified/", {"q": "Faith"})

        self.assertEqual(response.status_code, 401)


class PatientCanonicalHealthProfileTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+237670000002",
            password="StrongPass123",
            country="CM",
            email="health-profile-tests@example.com",
        )
        self.client.force_authenticate(self.user)
        self.other_user = User.objects.create_user(
            phone="+237670000004",
            password="StrongPass123",
            country="CM",
            email="health-caregiver@example.com",
        )
        self.other_client = APIClient()
        self.other_client.force_authenticate(self.other_user)
        self.organization = models.HealthcareOrganization.objects.create(
            name="KIS Test Clinic",
            slug="kis-test-clinic",
        )
        self.patient = models.PatientMasterRecord.objects.create(
            mrn="KIS-HP-001",
            first_name="Nigel",
            last_name="Tester",
            gender=models.PatientMasterRecord.GENDER_MALE,
            primary_contact={
                "user_id": str(self.user.id),
                "email": self.user.email,
                "phone": self.user.phone,
            },
            emergency_contact={"name": "Emergency Contact", "phone": "+237699000000"},
            metadata={"blood_type": "O+"},
            organization=self.organization,
        )
        models.AllergyRecord.objects.create(
            patient=self.patient,
            agent="Peanuts",
            severity=models.AllergyRecord.SEVERITY_SEVERE,
            status=models.AllergyRecord.STATUS_ACTIVE,
        )
        models.MedicationOrder.objects.create(
            patient=self.patient,
            drug_name="Amoxicillin",
            status=models.MedicationOrder.STATUS_ACTIVE,
        )
        models.VitalSign.objects.create(
            patient=self.patient,
            vital_type=models.VitalSign.TYPE_TEMPERATURE,
            value="37.2",
            units="C",
        )
        models.WellnessMetric.objects.create(
            patient=self.patient,
            metric_type=models.WellnessMetric.METRIC_STEPS,
            source=models.WellnessMetric.SOURCE_APPLE_HEALTH,
            measurement_window=models.WellnessMetric.WINDOW_DAILY,
            value="6400",
            units="count",
            normalized_value="6400",
            normalized_units="count",
        )
        models.WellnessMetric.objects.create(
            patient=self.patient,
            metric_type=models.WellnessMetric.METRIC_WEIGHT,
            source=models.WellnessMetric.SOURCE_MANUAL,
            measurement_window=models.WellnessMetric.WINDOW_INSTANT,
            value="78.4",
            units="kg",
            normalized_value="78.4",
            normalized_units="kg",
        )
        models.ProblemRecord.objects.create(
            patient=self.patient,
            title="Asthma",
            clinical_status=models.ProblemRecord.STATUS_ACTIVE,
            severity=models.ProblemRecord.SEVERITY_MEDIUM,
        )
        models.ImmunizationRecord.objects.create(
            patient=self.patient,
            vaccine_name="Tetanus",
            status=models.ImmunizationRecord.STATUS_COMPLETED,
        )
        models.ProcedureRecord.objects.create(
            patient=self.patient,
            procedure_name="Appendectomy",
            status=models.ProcedureRecord.STATUS_COMPLETED,
        )
        models.HealthDocument.objects.create(
            patient=self.patient,
            title="Discharge Summary",
            category=models.HealthDocument.CATEGORY_DISCHARGE,
            file_url="https://example.com/discharge-summary.pdf",
        )
        health_profile = BroadcastHealthProfile.objects.create(profile=self.user.profile, payload={})
        institution = BroadcastHealthInstitution.objects.create(
            health_profile=health_profile,
            institution_uid="inst-001",
            name="KIS Prime Hospital",
            owner_user=self.user,
        )
        BroadcastHealthInstitutionMember.objects.create(
            institution=institution,
            member_uid="member-001",
            name="Nigel Tester",
            role="owner",
            user=self.user,
        )

    def test_my_health_profile_returns_canonical_payload(self):
        response = self.client.get("/api/v1/patients/master/my-health-profile/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(self.patient.id), payload["patient_id"])
        self.assertEqual(str(self.user.id), payload["linked_user_id"])
        self.assertEqual("Nigel Tester", payload["identity"]["full_name"])
        self.assertEqual("O+", payload["emergency"]["blood_type"])
        self.assertEqual(1, payload["care_summary"]["active_allergies_count"])
        self.assertEqual(1, payload["care_summary"]["active_medications_count"])
        self.assertEqual(1, payload["affiliations"]["total_institutions"])
        self.assertEqual("KIS Prime Hospital", payload["affiliations"]["owned_institutions"][0]["name"])

    def test_my_health_profile_returns_not_found_when_user_is_not_linked(self):
        other_user = User.objects.create_user(
            phone="+237670000003",
            password="StrongPass123",
            country="CM",
            email="health-profile-missing@example.com",
        )
        self.client.force_authenticate(other_user)

        response = self.client.get("/api/v1/patients/master/my-health-profile/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual("patient_profile_not_linked", response.json()["code"])

    def test_legacy_broadcast_health_profile_write_syncs_core_patient_fields(self):
        response = self.client.post(
            "/api/v1/broadcasts/profiles/manage/",
            {
                "profile_type": "health_profile",
                "updates": {
                    "profile_name": "Nigel Health",
                    "identity": {
                        "first_name": "Nigel",
                        "last_name": "Updated",
                        "dob": "1998-06-20",
                        "gender": models.PatientMasterRecord.GENDER_MALE,
                    },
                    "emergency": {
                        "blood_type": "A+",
                        "medical_notes": "Carries inhaler",
                        "emergency_contact": {
                            "name": "Updated Emergency",
                            "phone": "+237688111222",
                        },
                    },
                    "primary_contact": {
                        "user_id": str(self.user.id),
                        "email": self.user.email,
                        "phone": self.user.phone,
                    },
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.patient.refresh_from_db()
        self.assertEqual("Updated", self.patient.last_name)
        self.assertEqual("1998-06-20", self.patient.dob.isoformat())
        self.assertEqual("A+", self.patient.metadata.get("blood_type"))
        self.assertEqual("Carries inhaler", self.patient.metadata.get("medical_notes"))
        self.assertEqual("Updated Emergency", self.patient.emergency_contact.get("name"))

        canonical = self.client.get("/api/v1/patients/master/my-health-profile/")
        self.assertEqual(canonical.status_code, 200)
        payload = canonical.json()
        self.assertEqual("Nigel Updated", payload["identity"]["full_name"])
        self.assertEqual("A+", payload["emergency"]["blood_type"])
        self.assertEqual("Carries inhaler", payload["emergency"]["medical_notes"])

    def test_health_summary_endpoint_returns_patient_facing_summary(self):
        response = self.client.get(f"/api/v1/patients/master/{self.patient.id}/health-summary/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(self.patient.id), payload["patient_id"])
        self.assertEqual("Nigel Tester", payload["identity"]["full_name"])
        self.assertEqual(1, payload["care_summary"]["active_medications_count"])
        self.assertEqual(1, len(payload["top_allergies"]))
        self.assertEqual(1, len(payload["problems"]))
        self.assertEqual(1, len(payload["immunizations"]))
        self.assertEqual(1, len(payload["procedures"]))
        self.assertEqual(1, len(payload["documents"]))
        self.assertIn("steps", payload["wellness"]["trends"])
        self.assertEqual("6400.0000", payload["wellness"]["trends"]["steps"]["latest"]["value"])

    def test_emergency_card_endpoint_returns_emergency_snapshot(self):
        response = self.client.get(f"/api/v1/patients/master/{self.patient.id}/emergency-card/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual("Nigel Tester", payload["identity"]["full_name"])
        self.assertEqual("O+", payload["emergency"]["blood_type"])
        self.assertEqual("Emergency Contact", payload["emergency"]["emergency_contact"]["name"])
        self.assertEqual(1, len(payload["severe_allergies"]))

    def test_problem_record_endpoint_creates_problem(self):
        response = self.client.post(
            "/api/v1/patients/problems/",
            {
                "patient": str(self.patient.id),
                "title": "Hypertension",
                "clinical_status": "active",
                "verification_status": "confirmed",
                "severity": "high",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.user.id, models.ProblemRecord.objects.filter(title="Hypertension").first().diagnosed_by_id)

    def test_wellness_metric_endpoint_normalizes_weight_from_pounds(self):
        response = self.client.post(
            "/api/v1/patients/wellness-metrics/",
            {
                "patient": str(self.patient.id),
                "metric_type": "weight",
                "source": "manual",
                "measurement_window": "instant",
                "value": "220",
                "units": "lb",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        metric = models.WellnessMetric.objects.get(id=response.json()["id"])
        self.assertEqual("kg", metric.normalized_units)
        self.assertGreater(float(metric.normalized_value), 99.0)

    def test_health_summary_denies_unrelated_user_without_access_grant(self):
        response = self.other_client.get(f"/api/v1/patients/master/{self.patient.id}/health-summary/")

        self.assertEqual(response.status_code, 403)

    def test_health_summary_allows_active_delegate_with_grant(self):
        models.HealthDataAccessGrant.objects.create(
            patient=self.patient,
            granted_to=self.other_user,
            granted_by=self.user,
            role=models.HealthDataAccessGrant.ROLE_CAREGIVER,
            scope=models.HealthDataAccessGrant.SCOPE_SUMMARY,
            status=models.HealthDataAccessGrant.STATUS_ACTIVE,
            allow_emergency_override=True,
        )

        response = self.other_client.get(f"/api/v1/patients/master/{self.patient.id}/health-summary/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(self.patient.id), response.json()["patient_id"])
        self.assertTrue(
            models.ComplianceAuditLog.objects.filter(
                action="patient.health_summary.read",
                target_id=str(self.patient.id),
            ).exists()
        )

    def test_export_bundle_returns_fhir_collection(self):
        response = self.client.get(f"/api/v1/patients/master/{self.patient.id}/export-bundle/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual("Bundle", payload["resourceType"])
        self.assertEqual("collection", payload["type"])
        resource_types = [entry["resource"]["resourceType"] for entry in payload["entry"]]
        self.assertIn("Patient", resource_types)
        self.assertIn("Condition", resource_types)
        self.assertIn("Immunization", resource_types)
        self.assertTrue(models.HealthRecordExchangeLog.objects.filter(patient=self.patient, direction="export").exists())

    def test_import_bundle_creates_records_and_log(self):
        response = self.client.post(
            f"/api/v1/patients/master/{self.patient.id}/import-bundle/",
            {
                "source_label": "test-provider",
                "bundle": {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Condition",
                                "code": {"text": "Diabetes"},
                                "clinicalStatus": {"text": "active"},
                                "verificationStatus": {"text": "confirmed"},
                                "severity": {"text": "high"},
                            }
                        },
                        {
                            "resource": {
                                "resourceType": "DocumentReference",
                                "description": "Imported Lab Result",
                                "type": {"text": "lab"},
                                "content": [
                                    {
                                        "attachment": {
                                            "url": "https://example.com/lab-result.pdf",
                                            "contentType": "application/pdf",
                                            "title": "Imported Lab Result",
                                        }
                                    }
                                ],
                            }
                        },
                    ],
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(models.ProblemRecord.objects.filter(patient=self.patient, title="Diabetes").exists())
        self.assertTrue(models.HealthDocument.objects.filter(patient=self.patient, title="Imported Lab Result").exists())
        log = models.HealthRecordExchangeLog.objects.filter(patient=self.patient, direction="import").latest("created_at")
        self.assertEqual("test-provider", log.source_label)
