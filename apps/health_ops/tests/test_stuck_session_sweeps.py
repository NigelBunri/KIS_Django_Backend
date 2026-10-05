from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.health_ops.models import (
    EmergencyDispatchSession,
    EmergencyDispatchStatus,
    EngineCompletionMode,
    EngineRegistry,
    EngineSession,
    HealthInstitution,
    HealthService,
    PaymentBillingSession,
    PaymentBillingStatus,
    ServiceEngineMap,
    ServiceWorkflowSession,
    WorkflowStatus,
)
from apps.health_ops.tasks import sweep_stuck_billing_sessions, sweep_stuck_emergency_dispatch_sessions

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


class StuckSessionSweepTests(TestCase):
    def setUp(self):
        self.owner = _create_user("+237700000001", "sweep_owner")
        self.patient = _create_user("+237700000002", "sweep_patient")
        self.institution = HealthInstitution.objects.create(owner=self.owner, name="Sweep Test Clinic")
        self.service = HealthService.objects.create(institution=self.institution, name="Emergency")

        engine = EngineRegistry.objects.create(code="emergency_dispatch_sweep_test", name="Emergency Sweep", category="workflow")
        engine_map = ServiceEngineMap.objects.create(
            service=self.service, engine=engine, execution_order=1,
            is_required=True, completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        self.workflow = ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient, status=WorkflowStatus.IN_PROGRESS,
        )
        self.engine_session = EngineSession.objects.create(
            workflow_session=self.workflow, engine_map=engine_map, user=self.patient, is_unlocked=True,
        )

    def _make_emergency_session(self, *, status, last_tracking_at=None, created_at=None):
        session = EmergencyDispatchSession.objects.create(
            workflow_session=self.workflow, engine_session=self.engine_session,
            institution=self.institution, service=self.service, user=self.patient,
            dispatch_code="kis-emg-test", status=status,
        )
        if created_at is not None:
            EmergencyDispatchSession.objects.filter(id=session.id).update(created_at=created_at)
        if last_tracking_at is not None:
            EmergencyDispatchSession.objects.filter(id=session.id).update(last_tracking_at=last_tracking_at)
        return EmergencyDispatchSession.objects.get(id=session.id)

    def test_detects_session_stuck_via_stale_last_tracking_at(self):
        self._make_emergency_session(
            status=EmergencyDispatchStatus.DISPATCHED,
            last_tracking_at=timezone.now() - timedelta(minutes=45),
        )
        result = sweep_stuck_emergency_dispatch_sessions()
        self.assertEqual(result["stuck_count"], 1)

    def test_detects_session_stuck_with_null_last_tracking_at_via_created_at_fallback(self):
        """A session created and then immediately abandoned (dropped
        connection right after /start) never gets a last_tracking_at at
        all — must still be caught via created_at, not silently missed."""
        self._make_emergency_session(
            status=EmergencyDispatchStatus.WAITING,
            created_at=timezone.now() - timedelta(minutes=45),
        )
        result = sweep_stuck_emergency_dispatch_sessions()
        self.assertEqual(result["stuck_count"], 1)

    def test_recently_updated_session_not_flagged(self):
        self._make_emergency_session(
            status=EmergencyDispatchStatus.TRIAGING,
            last_tracking_at=timezone.now() - timedelta(minutes=5),
        )
        result = sweep_stuck_emergency_dispatch_sessions()
        self.assertEqual(result["stuck_count"], 0)

    def test_resolved_session_never_flagged_regardless_of_age(self):
        self._make_emergency_session(
            status=EmergencyDispatchStatus.RESOLVED,
            last_tracking_at=timezone.now() - timedelta(days=5),
        )
        result = sweep_stuck_emergency_dispatch_sessions()
        self.assertEqual(result["stuck_count"], 0)

    def test_cancelled_session_never_flagged(self):
        self._make_emergency_session(
            status=EmergencyDispatchStatus.CANCELLED,
            last_tracking_at=timezone.now() - timedelta(days=5),
        )
        result = sweep_stuck_emergency_dispatch_sessions()
        self.assertEqual(result["stuck_count"], 0)

    def test_detects_stuck_payment_pending_billing_session(self):
        billing_engine = EngineRegistry.objects.create(code="payment_billing_sweep_test", name="Billing Sweep", category="workflow")
        billing_map = ServiceEngineMap.objects.create(
            service=self.service, engine=billing_engine, execution_order=2,
            completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        billing_engine_session = EngineSession.objects.create(
            workflow_session=self.workflow, engine_map=billing_map, user=self.patient, is_unlocked=True,
        )
        billing_session = PaymentBillingSession.objects.create(
            workflow_session=self.workflow, engine_session=billing_engine_session,
            institution=self.institution, service=self.service, user=self.patient,
            status=PaymentBillingStatus.PAYMENT_PENDING, total_amount_micro=100000, payable_amount_micro=100000,
        )
        PaymentBillingSession.objects.filter(id=billing_session.id).update(
            updated_at=timezone.now() - timedelta(hours=2),
        )
        result = sweep_stuck_billing_sessions()
        self.assertEqual(result["stuck_count"], 1)

    def test_recently_updated_billing_session_not_flagged(self):
        billing_engine = EngineRegistry.objects.create(code="payment_billing_sweep_test_2", name="Billing Sweep 2", category="workflow")
        billing_map = ServiceEngineMap.objects.create(
            service=self.service, engine=billing_engine, execution_order=2,
            completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        billing_engine_session = EngineSession.objects.create(
            workflow_session=self.workflow, engine_map=billing_map, user=self.patient, is_unlocked=True,
        )
        PaymentBillingSession.objects.create(
            workflow_session=self.workflow, engine_session=billing_engine_session,
            institution=self.institution, service=self.service, user=self.patient,
            status=PaymentBillingStatus.PAYMENT_PENDING, total_amount_micro=100000, payable_amount_micro=100000,
        )
        result = sweep_stuck_billing_sessions()
        self.assertEqual(result["stuck_count"], 0)
