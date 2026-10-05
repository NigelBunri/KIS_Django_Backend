from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.channels.models import Channel
from apps.communities.models import Community, CommunityMembership, CommunityMembershipStatus, CommunityRole
from apps.health_ops.communication_sync import (
    ensure_institution_community,
    ensure_service_channel,
    on_institution_partner_connected,
    sync_membership_communication,
    sync_patient_service_communication,
)
from apps.health_ops.models import (
    HealthInstitution,
    HealthInstitutionMembership,
    HealthService,
    MembershipRole,
    ServiceWorkflowSession,
    WorkflowStatus,
)
from apps.partners.models import Partner


User = get_user_model()


def _create_user(phone: str, username: str):
    return User.objects.create_user(
        phone=phone,
        country="CM",
        password="pass1234",
        username=username,
        display_name=username.title(),
        phone_country_code="+237",
        phone_number=phone.replace("+237", ""),
    )


class HealthCommunicationSyncUnitTests(TestCase):
    """Unit coverage for the ensure_*/sync_* functions directly — the
    partner-connect/view-level flow is covered separately below."""

    def setUp(self):
        self.owner = _create_user("+237690820001", "hcs_owner")
        self.staff = _create_user("+237690820002", "hcs_staff")
        self.patient = _create_user("+237690820003", "hcs_patient")
        self.institution = HealthInstitution.objects.create(owner=self.owner, name="Hope Clinic")
        self.service = HealthService.objects.create(institution=self.institution, name="General Consult")

    def test_ensure_institution_community_is_noop_without_partner(self):
        self.assertIsNone(ensure_institution_community(self.institution))
        self.institution.refresh_from_db()
        self.assertIsNone(self.institution.community_id)

    def test_ensure_institution_community_creates_once_and_is_idempotent(self):
        self.institution.partner = Partner.objects.create(owner=self.owner, name="Hope Group", slug="hope-group")
        self.institution.save(update_fields=["partner"])

        community = ensure_institution_community(self.institution)
        self.assertIsNotNone(community)
        self.institution.refresh_from_db()
        self.assertEqual(self.institution.community_id, community.id)

        again = ensure_institution_community(self.institution)
        self.assertEqual(again.id, community.id)
        self.assertEqual(Community.objects.filter(partner=self.institution.partner).count(), 1)

    def test_ensure_institution_community_joins_and_admins_owner(self):
        self.institution.partner = Partner.objects.create(owner=self.owner, name="Hope Group 2", slug="hope-group-2")
        self.institution.save(update_fields=["partner"])
        community = ensure_institution_community(self.institution)
        membership = CommunityMembership.objects.get(community=community, user=self.owner)
        self.assertEqual(membership.status, CommunityMembershipStatus.ACTIVE)
        self.assertEqual(membership.role, CommunityRole.ADMIN)

    def test_ensure_service_channel_nests_under_institution_community(self):
        self.institution.partner = Partner.objects.create(owner=self.owner, name="Hope Group 3", slug="hope-group-3")
        self.institution.save(update_fields=["partner"])
        channel = ensure_service_channel(self.service)
        self.assertIsNotNone(channel)
        self.assertEqual(channel.community_id, self.institution.community_id)

    def test_sync_membership_communication_grants_admin_for_manager_role(self):
        self.institution.partner = Partner.objects.create(owner=self.owner, name="Hope Group 4", slug="hope-group-4")
        self.institution.save(update_fields=["partner"])
        membership = HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.staff, role=MembershipRole.MANAGER, is_active=True,
        )
        sync_membership_communication(membership)
        community = self.institution.community
        self.assertEqual(
            CommunityMembership.objects.get(community=community, user=self.staff).role, CommunityRole.ADMIN,
        )

    def test_sync_membership_communication_plain_member_not_admin(self):
        self.institution.partner = Partner.objects.create(owner=self.owner, name="Hope Group 5", slug="hope-group-5")
        self.institution.save(update_fields=["partner"])
        membership = HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.staff, role=MembershipRole.STAFF, is_active=True,
        )
        sync_membership_communication(membership)
        community = self.institution.community
        self.assertEqual(
            CommunityMembership.objects.get(community=community, user=self.staff).role, CommunityRole.MEMBER,
        )

    def test_sync_membership_communication_noop_for_inactive_membership(self):
        self.institution.partner = Partner.objects.create(owner=self.owner, name="Hope Group 6", slug="hope-group-6")
        self.institution.save(update_fields=["partner"])
        membership = HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.staff, role=MembershipRole.STAFF, is_active=False,
        )
        sync_membership_communication(membership)
        # No community should even be forced into existence by an inactive membership alone.
        self.institution.refresh_from_db()
        if self.institution.community_id:
            self.assertFalse(
                CommunityMembership.objects.filter(community_id=self.institution.community_id, user=self.staff).exists()
            )

    def test_sync_patient_service_communication_joins_channel(self):
        self.institution.partner = Partner.objects.create(owner=self.owner, name="Hope Group 7", slug="hope-group-7")
        self.institution.save(update_fields=["partner"])
        workflow = ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )
        sync_patient_service_communication(workflow)
        self.service.refresh_from_db()
        self.assertIsNotNone(self.service.channel_id)
        from apps.chat.models import ConversationMember

        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=self.service.channel.conversation, user=self.patient, left_at__isnull=True,
            ).exists()
        )

    def test_late_partner_connect_backfills_existing_members_and_patients(self):
        """The exact bug class Education's acceptance test found: an
        institution that already has staff and active patients *before*
        ever connecting a Partner Account must not leave them stranded."""
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.owner, role=MembershipRole.OWNER, is_active=True,
        )
        staff_membership = HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.staff, role=MembershipRole.MANAGER, is_active=True,
        )
        workflow = ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )

        # No partner yet — nothing should exist.
        self.assertIsNone(self.institution.community_id)

        self.institution.partner = Partner.objects.create(owner=self.owner, name="Hope Group 8", slug="hope-group-8")
        self.institution.save(update_fields=["partner"])
        on_institution_partner_connected(self.institution)

        self.institution.refresh_from_db()
        self.assertIsNotNone(self.institution.community_id)
        self.assertEqual(
            CommunityMembership.objects.get(community=self.institution.community, user=self.staff).role,
            CommunityRole.ADMIN,
        )
        self.service.refresh_from_db()
        self.assertIsNotNone(self.service.channel_id)
        from apps.chat.models import ConversationMember

        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=self.service.channel.conversation, user=self.patient, left_at__isnull=True,
            ).exists()
        )

    def test_completed_workflow_sessions_are_not_backfilled(self):
        ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.COMPLETED,
        )
        self.institution.partner = Partner.objects.create(owner=self.owner, name="Hope Group 9", slug="hope-group-9")
        self.institution.save(update_fields=["partner"])
        on_institution_partner_connected(self.institution)
        self.service.refresh_from_db()
        self.assertIsNone(self.service.channel_id)


@override_settings(SECURE_SSL_REDIRECT=False)
class HealthCommunicationSyncViewIntegrationTests(APITestCase):
    """Confirms the real HTTP partner-connect/disconnect endpoints trigger
    (and disconnect never un-triggers) the sync."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237690830001", "hcsv_owner")
        self.institution = HealthInstitution.objects.create(owner=self.owner, name="Unity Hospital")
        self.partner = Partner.objects.create(owner=self.owner, name="Unity Group", slug="unity-group")

    def _partner_connect_url(self):
        return reverse("health-ops-institution-partner-connect", kwargs={"institution_id": str(self.institution.id)})

    def test_connecting_partner_via_api_creates_community(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(self._partner_connect_url(), {"partner_id": str(self.partner.id)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertIsNotNone(self.institution.community_id)
        self.assertTrue(
            CommunityMembership.objects.filter(community=self.institution.community, user=self.owner).exists()
        )

    def test_disconnecting_partner_via_api_does_not_delete_community(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._partner_connect_url(), {"partner_id": str(self.partner.id)}, format="json")
        self.institution.refresh_from_db()
        community_id = self.institution.community_id
        self.assertIsNotNone(community_id)

        response = self.client.delete(self._partner_connect_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertIsNone(self.institution.partner_id)
        # Unlink only — the community row itself must survive.
        self.assertTrue(Community.objects.filter(id=community_id).exists())
        self.assertEqual(self.institution.community_id, community_id)
