from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.models import (
    EmergencyDispatchSession,
    EmergencyDispatchStatus,
    EngineCompletionMode,
    EngineRegistry,
    EngineSession,
    EngineStepDefinition,
    HealthInstitution,
    HealthInstitutionMembership,
    HealthService,
    MembershipRole,
    ServiceEngineMap,
    ServiceWorkflowSession,
    WorkflowStatus,
)

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


def _seed_engine(code: str, name: str, steps: list[str]) -> EngineRegistry:
    engine, _ = EngineRegistry.objects.get_or_create(
        code=code,
        defaults={
            "name": name, "category": "workflow", "is_fixed": True, "is_active": True,
            "schema_version": 1, "default_step_count": max(1, len(steps)),
        },
    )
    for index, step in enumerate(steps, start=1):
        EngineStepDefinition.objects.get_or_create(
            engine=engine, step_key=step,
            defaults={
                "title": step.replace("_", " ").title(), "description": "",
                "step_order": index, "validation_schema": {}, "completion_rule": {}, "is_required": True,
            },
        )
    return engine


@override_settings(SECURE_SSL_REDIRECT=False)
class EmergencyDispatchSelfCertificationRegressionTests(APITestCase):
    """Regression coverage for a real safety-relevant vulnerability: a
    patient could self-certify real-world emergency-response facts
    (ambulance dispatched, paramedics arrived, emergency resolved) with no
    institution confirmation at all, across three separate endpoints. Fixed
    to require institution emergency-response staff for every transition
    except the patient's own legitimate self-cancellation."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237699000001", "emg_owner")
        self.patient = _create_user("+237699000002", "emg_patient")
        self.staff = _create_user("+237699000003", "emg_staff")

        self.institution = HealthInstitution.objects.create(owner=self.owner, name="Emergency Integrity Clinic")
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.staff, role=MembershipRole.STAFF, is_active=True,
        )
        self.service = HealthService.objects.create(institution=self.institution, name="Emergency")

        emergency_engine = _seed_engine(
            "emergency_dispatch", "Emergency Integrity",
            ["capture_location", "triage_form", "dispatch_ambulance", "track_response"],
        )
        emergency_map = ServiceEngineMap.objects.create(
            service=self.service, engine=emergency_engine, execution_order=1,
            is_required=True, access_window_days=1, completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        self.workflow = ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient,
            status=WorkflowStatus.IN_PROGRESS, assessment_completed=True,
        )
        EngineSession.objects.create(
            workflow_session=self.workflow, engine_map=emergency_map, user=self.patient, is_unlocked=True,
        )

        self.client.force_authenticate(self.patient)
        start_resp = self.client.post(
            reverse("health-ops-emergency-session-start"),
            {"workflow_session_id": str(self.workflow.id)},
            format="json",
        )
        assert start_resp.status_code == status.HTTP_201_CREATED, start_resp.data
        self.session_id = str(start_resp.data["emergency_session"]["id"])
        self.step_url = reverse("health-ops-emergency-session-step", kwargs={"emergency_session_id": self.session_id})
        self.tracking_url = reverse("health-ops-emergency-session-tracking", kwargs={"emergency_session_id": self.session_id})
        self.end_url = reverse("health-ops-emergency-session-end", kwargs={"emergency_session_id": self.session_id})

    def test_patient_can_still_report_own_location_and_triage(self):
        resp = self.client.patch(
            self.step_url,
            {"step_key": "capture_location", "is_completed": True, "payload": {"latitude": 1.1, "longitude": 2.2}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        resp = self.client.patch(
            self.step_url,
            {"step_key": "triage_form", "is_completed": True, "payload": {"triage_level": "high"}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)

    def test_patient_cannot_self_certify_dispatch_via_step_update(self):
        resp = self.client.patch(
            self.step_url,
            {"step_key": "dispatch_ambulance", "is_completed": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        session = EmergencyDispatchSession.objects.get(id=self.session_id)
        self.assertNotEqual(session.status, EmergencyDispatchStatus.DISPATCHED)

    def test_patient_cannot_self_certify_arrival_via_step_update(self):
        resp = self.client.patch(
            self.step_url,
            {"step_key": "track_response", "is_completed": True, "payload": {"arrived": True}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        session = EmergencyDispatchSession.objects.get(id=self.session_id)
        self.assertNotEqual(session.status, EmergencyDispatchStatus.ARRIVED)

    def test_institution_staff_can_confirm_dispatch_and_arrival(self):
        self.client.patch(
            self.step_url, {"step_key": "capture_location", "is_completed": True}, format="json",
        )
        self.client.patch(
            self.step_url, {"step_key": "triage_form", "is_completed": True}, format="json",
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            self.step_url,
            {"step_key": "dispatch_ambulance", "is_completed": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.data["emergency_session"]["status"], EmergencyDispatchStatus.DISPATCHED)

        resp = self.client.patch(
            self.step_url,
            {"step_key": "track_response", "is_completed": True, "payload": {"arrived": True}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.data["emergency_session"]["status"], EmergencyDispatchStatus.ARRIVED)

    def test_patient_cannot_self_certify_status_via_tracking_endpoint(self):
        resp = self.client.patch(self.tracking_url, {"status": EmergencyDispatchStatus.RESOLVED}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        session = EmergencyDispatchSession.objects.get(id=self.session_id)
        self.assertNotEqual(session.status, EmergencyDispatchStatus.RESOLVED)

    def test_patient_can_cancel_own_emergency_via_tracking_endpoint(self):
        resp = self.client.patch(self.tracking_url, {"status": EmergencyDispatchStatus.CANCELLED}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        session = EmergencyDispatchSession.objects.get(id=self.session_id)
        self.assertEqual(session.status, EmergencyDispatchStatus.CANCELLED)

    def test_institution_staff_can_set_status_via_tracking_endpoint(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(self.tracking_url, {"status": EmergencyDispatchStatus.DISPATCHED}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        session = EmergencyDispatchSession.objects.get(id=self.session_id)
        self.assertEqual(session.status, EmergencyDispatchStatus.DISPATCHED)

    def test_patient_cannot_self_resolve_via_end_endpoint(self):
        resp = self.client.post(self.end_url, {"status": EmergencyDispatchStatus.RESOLVED}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        session = EmergencyDispatchSession.objects.get(id=self.session_id)
        self.assertNotEqual(session.status, EmergencyDispatchStatus.RESOLVED)

    def test_patient_can_cancel_via_end_endpoint(self):
        resp = self.client.post(self.end_url, {"status": EmergencyDispatchStatus.CANCELLED}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        session = EmergencyDispatchSession.objects.get(id=self.session_id)
        self.assertEqual(session.status, EmergencyDispatchStatus.CANCELLED)

    def test_institution_staff_can_resolve_via_end_endpoint(self):
        self.client.patch(
            self.step_url, {"step_key": "capture_location", "is_completed": True}, format="json",
        )
        self.client.patch(
            self.step_url, {"step_key": "triage_form", "is_completed": True}, format="json",
        )
        self.client.force_authenticate(self.staff)
        self.client.patch(
            self.step_url, {"step_key": "dispatch_ambulance", "is_completed": True}, format="json",
        )
        resp = self.client.post(self.end_url, {"status": EmergencyDispatchStatus.RESOLVED, "summary": "Resolved on scene"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        session = EmergencyDispatchSession.objects.get(id=self.session_id)
        self.assertEqual(session.status, EmergencyDispatchStatus.RESOLVED)

    def test_stranger_cannot_access_session_at_all(self):
        stranger = _create_user("+237699000004", "emg_stranger")
        self.client.force_authenticate(stranger)
        resp = self.client.patch(self.tracking_url, {"status": EmergencyDispatchStatus.CANCELLED}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
