from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.models import (
    EngineCompletionMode,
    EngineRegistry,
    EngineSession,
    EngineStepDefinition,
    HealthInstitution,
    HealthInstitutionMembership,
    HealthInstitutionPayoutAccountStatus,
    HealthService,
    MembershipRole,
    PaymentBillingSession,
    PaymentBillingStatus,
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
class BillingPaymentForgeryRegressionTests(APITestCase):
    """Regression coverage for a real vulnerability found in this audit:
    PaymentBillingSessionStepUpdateView's authorize_payment step and
    PaymentBillingSessionPayloadView both previously trusted a bare
    client-supplied payment_status="paid" (or amount_paid_micro) as proof
    of payment — a patient could flip their own bill to PAID, or poison the
    stored payload read by _health_provider_payment_confirmed, with zero
    real money moving. Fixed to require either a real synchronous wallet
    debit or the provider's own webhook-confirmed payload."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237698000001", "bill_owner")
        self.patient = _create_user("+237698000002", "bill_patient")
        self.staff = _create_user("+237698000003", "bill_staff")

        self.institution = HealthInstitution.objects.create(
            owner=self.owner, name="Billing Integrity Clinic",
            payout_account_status=HealthInstitutionPayoutAccountStatus.ACTIVE,
            flutterwave_subaccount_id="RS_TEST_BILLING",
        )
        HealthInstitutionMembership.objects.create(
            institution=self.institution, user=self.staff, role=MembershipRole.STAFF, is_active=True,
        )
        self.service = HealthService.objects.create(institution=self.institution, name="Consult", base_cost_micro=200000)

        billing_engine = _seed_engine(
            "payment_billing",
            "Payment Integrity",
            ["review_charges", "select_payment_method", "authorize_payment"],
        )
        billing_map = ServiceEngineMap.objects.create(
            service=self.service, engine=billing_engine, execution_order=1,
            cost_micro=200000, is_required=True, access_window_days=2,
            completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        self.workflow = ServiceWorkflowSession.objects.create(
            institution=self.institution, service=self.service, user=self.patient,
            status=WorkflowStatus.IN_PROGRESS, assessment_completed=True,
        )
        now_value = timezone.now()
        EngineSession.objects.create(
            workflow_session=self.workflow, engine_map=billing_map, user=self.patient,
            is_unlocked=True, unlocked_at=now_value, expires_at=now_value + timedelta(days=2),
        )

        self.client.force_authenticate(self.patient)
        start_response = self.client.post(
            reverse("health-ops-billing-session-start"),
            {
                "workflow_session_id": str(self.workflow.id),
                "total_amount_micro": 200000,
                "payable_amount_micro": 200000,
            },
            format="json",
        )
        assert start_response.status_code == status.HTTP_201_CREATED, start_response.data
        self.billing_session_id = str(start_response.data["billing_session"]["id"])
        self.step_url = reverse("health-ops-billing-session-step", kwargs={"billing_session_id": self.billing_session_id})
        self.payload_url = reverse("health-ops-billing-session-payload", kwargs={"billing_session_id": self.billing_session_id})

        self.client.patch(self.step_url, {"step_key": "review_charges", "is_completed": True}, format="json")
        self.client.patch(
            self.step_url,
            {"step_key": "select_payment_method", "is_completed": True, "payload": {"payment_provider": "flutterwave"}},
            format="json",
        )

    def test_patient_cannot_self_certify_payment_via_authorize_payment_step(self):
        resp = self.client.patch(
            self.step_url,
            {
                "step_key": "authorize_payment",
                "is_completed": True,
                "payload": {"payment_provider": "flutterwave", "payment_status": "paid", "amount_paid_micro": 200000},
            },
            format="json",
        )
        # Either a 400 (amount_paid_micro from a plain patient is ignored,
        # so the "amount paid >= payable" check correctly fails) or a 200
        # that still does NOT reach PAID is an acceptable secure outcome —
        # what must never happen is the bill actually being marked PAID.
        self.assertIn(resp.status_code, {status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST})
        session = PaymentBillingSession.objects.get(id=self.billing_session_id)
        self.assertNotEqual(session.status, PaymentBillingStatus.PAID)
        self.assertIsNone(session.paid_at)

    def test_patient_cannot_poison_payload_via_payload_endpoint_then_authorize(self):
        poison_resp = self.client.patch(
            self.payload_url,
            {"payload": {"payment_status": "paid"}},
            format="json",
        )
        self.assertEqual(poison_resp.status_code, status.HTTP_200_OK, poison_resp.content)
        session = PaymentBillingSession.objects.get(id=self.billing_session_id)
        self.assertNotIn(session.payload.get("payment_status"), {"paid"})

        resp = self.client.patch(
            self.step_url,
            {"step_key": "authorize_payment", "is_completed": True, "payload": {"payment_provider": "flutterwave"}},
            format="json",
        )
        self.assertIn(resp.status_code, {status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST})
        session.refresh_from_db()
        self.assertNotEqual(session.status, PaymentBillingStatus.PAID)

    def test_patient_cannot_set_amount_paid_via_payload_endpoint(self):
        resp = self.client.patch(
            self.payload_url,
            {"payload": {"amount_paid_micro": 200000}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        session = PaymentBillingSession.objects.get(id=self.billing_session_id)
        self.assertEqual(int(session.amount_paid_micro or 0), 0)

    def test_institution_staff_can_record_out_of_band_payment_via_payload_endpoint(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            self.payload_url,
            {"payload": {"amount_paid_micro": 200000, "invoice_number": "INV-001"}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        session = PaymentBillingSession.objects.get(id=self.billing_session_id)
        self.assertEqual(int(session.amount_paid_micro or 0), 200000)
        self.assertEqual(session.invoice_number, "INV-001")

    def test_legitimate_webhook_confirmed_payment_still_works(self):
        """Confirms the fix doesn't break the real path: a genuinely
        webhook-confirmed payload (as reconcile_direct_payment_callback
        would write) is still accepted at the authorize_payment step."""
        session = PaymentBillingSession.objects.get(id=self.billing_session_id)
        session.payload = {**session.payload, "payment_status": "paid"}
        session.amount_paid_micro = int(session.payable_amount_micro or 0)
        session.paid_at = timezone.now()
        session.save(update_fields=["payload", "amount_paid_micro", "paid_at", "updated_at"])

        resp = self.client.patch(
            self.step_url,
            {"step_key": "authorize_payment", "is_completed": True, "payload": {"payment_provider": "flutterwave"}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        session.refresh_from_db()
        self.assertEqual(session.status, PaymentBillingStatus.PAID)
