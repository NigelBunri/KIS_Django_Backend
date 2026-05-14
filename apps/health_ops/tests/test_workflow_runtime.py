from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.billing.direct_payments import reconcile_direct_payment_callback
from apps.billing.models import DirectPaymentIntent, WalletLedgerEntry
from apps.billing.services import get_wallet_account
from apps.health_ops.models import (
    EngineCompletionMode,
    EngineRegistry,
    EngineSession,
    EngineStepDefinition,
    HealthInstitution,
    HealthService,
    PaymentBillingSession,
    ServiceEngineMap,
    ServiceWorkflowSession,
    WorkflowStatus,
)
from apps.health_ops.serializers import PaymentBillingStartSerializer


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
            "name": name,
            "category": "workflow",
            "is_fixed": True,
            "is_active": True,
            "schema_version": 1,
            "default_step_count": max(1, len(steps)),
        },
    )
    existing_by_key = {
        str(row.step_key): row
        for row in EngineStepDefinition.objects.filter(engine=engine).only("step_key", "step_order")
    }
    used_orders = {
        int(row.step_order)
        for row in existing_by_key.values()
        if row.step_order is not None
    }
    for index, step in enumerate(steps, start=1):
        if step in existing_by_key:
            continue
        step_order = index
        if step_order in used_orders:
            step_order = (max(used_orders) + 1) if used_orders else 1
        EngineStepDefinition.objects.create(
            engine=engine,
            step_key=step,
            title=step.replace("_", " ").title(),
            description="",
            step_order=step_order,
            validation_schema={},
            completion_rule={},
            is_required=True,
        )
        used_orders.add(step_order)
    return engine


@override_settings(SECURE_SSL_REDIRECT=False)
class HealthOpsWorkflowRuntimeTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237690700101", "ops_owner")
        self.user = _create_user("+237690700102", "ops_user")

        self.institution = HealthInstitution.objects.create(
            owner=self.owner,
            name="Workflow Hospital",
            slug="workflow-hospital",
            institution_type="hospital",
            timezone="UTC",
            settings={},
            is_active=True,
        )
        self.service = HealthService.objects.create(
            institution=self.institution,
            name="Cardiology Lesson",
            description="Structured workflow lesson",
            is_active=True,
            requires_assessment=False,
            assessment_schema={},
            base_cost_micro=0,
        )
        self.client.force_authenticate(self.user)

    def test_payment_billing_serializer_keeps_micro_amount_unchanged(self):
        serializer = PaymentBillingStartSerializer(
            data={
                "workflow_session_id": str(uuid4()),
                "total_amount_micro": 123456,
                "payable_amount_micro": 654321,
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["total_amount_micro"], 123456)
        self.assertEqual(serializer.validated_data["payable_amount_micro"], 654321)

    def _create_workflow(self, mappings: list[ServiceEngineMap]) -> ServiceWorkflowSession:
        workflow = ServiceWorkflowSession.objects.create(
            institution=self.institution,
            service=self.service,
            user=self.user,
            status=WorkflowStatus.IN_PROGRESS,
            is_locked_by_payment=False,
            requires_assessment=False,
            assessment_completed=True,
            metadata={},
        )
        now_value = timezone.now()
        for index, mapping in enumerate(mappings):
            unlocked = index == 0
            EngineSession.objects.create(
                workflow_session=workflow,
                engine_map=mapping,
                user=self.user,
                is_unlocked=unlocked,
                unlocked_at=now_value if unlocked else None,
                expires_at=(now_value + timedelta(days=mapping.access_window_days)) if unlocked and mapping.access_window_days > 0 else None,
            )
        return workflow

    def test_resume_marks_expired_required_engine_and_blocks_next(self):
        payment_engine = _seed_engine("payment_runtime_gate", "Payment Runtime Gate", ["review_charges"])
        video_engine = _seed_engine("video_runtime_gate", "Video Runtime Gate", ["watch_video"])

        payment_map = ServiceEngineMap.objects.create(
            service=self.service,
            engine=payment_engine,
            execution_order=1,
            config={},
            cost_micro=0,
            is_required=True,
            access_window_days=1,
            completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        video_map = ServiceEngineMap.objects.create(
            service=self.service,
            engine=video_engine,
            execution_order=2,
            config={},
            cost_micro=0,
            is_required=True,
            access_window_days=9,
            completion_mode=EngineCompletionMode.VIDEO_ITEMS,
        )
        workflow = self._create_workflow([payment_map, video_map])
        first_session = workflow.engine_sessions.select_related("engine_map").order_by("engine_map__execution_order").first()
        assert first_session is not None
        first_session.unlocked_at = timezone.now() - timedelta(days=5)
        first_session.expires_at = timezone.now() - timedelta(days=1)
        first_session.is_unlocked = True
        first_session.save(update_fields=["unlocked_at", "expires_at", "is_unlocked", "updated_at"])

        url = reverse("health-ops-session-resume", kwargs={"workflow_session_id": workflow.id})
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        runtime = response.data["session"]["runtime"]
        self.assertEqual(runtime["blocked_reason"], "required_engine_expired")
        engines = runtime["engines"]
        self.assertEqual(engines[0]["state"], "expired")
        self.assertEqual(engines[1]["state"], "locked")

    def test_step_updates_enforce_order_and_unlock_next_engine(self):
        billing_engine = _seed_engine(
            "payment_runtime_steps",
            "Payment Runtime Steps",
            ["review_charges", "authorize_payment"],
        )
        video_engine = _seed_engine("video_runtime_steps", "Video Runtime Steps", ["watch_video"])

        billing_map = ServiceEngineMap.objects.create(
            service=self.service,
            engine=billing_engine,
            execution_order=1,
            config={},
            cost_micro=0,
            is_required=True,
            access_window_days=5,
            completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        video_map = ServiceEngineMap.objects.create(
            service=self.service,
            engine=video_engine,
            execution_order=2,
            config={},
            cost_micro=0,
            is_required=True,
            access_window_days=9,
            completion_mode=EngineCompletionMode.VIDEO_ITEMS,
        )
        workflow = self._create_workflow([billing_map, video_map])
        sessions = list(workflow.engine_sessions.select_related("engine_map").order_by("engine_map__execution_order"))
        billing_session = sessions[0]
        video_session = sessions[1]

        step_url = reverse("health-ops-session-step-update", kwargs={"workflow_session_id": workflow.id})
        invalid = self.client.patch(
            step_url,
            {
                "engine_session_id": str(billing_session.id),
                "step_key": "authorize_payment",
                "is_completed": True,
            },
            format="json",
            secure=True,
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

        first_ok = self.client.patch(
            step_url,
            {
                "engine_session_id": str(billing_session.id),
                "step_key": "review_charges",
                "is_completed": True,
            },
            format="json",
            secure=True,
        )
        self.assertEqual(first_ok.status_code, status.HTTP_200_OK)

        second_ok = self.client.patch(
            step_url,
            {
                "engine_session_id": str(billing_session.id),
                "step_key": "authorize_payment",
                "is_completed": True,
            },
            format="json",
            secure=True,
        )
        self.assertEqual(second_ok.status_code, status.HTTP_200_OK)

        video_session.refresh_from_db()
        self.assertTrue(video_session.is_unlocked)
        self.assertIsNotNone(video_session.unlocked_at)
        self.assertIsNotNone(video_session.expires_at)

    def test_billing_start_blocks_when_engine_is_expired(self):
        billing_engine = _seed_engine("payment_billing", "Payment", ["review_charges"])
        billing_map = ServiceEngineMap.objects.create(
            service=self.service,
            engine=billing_engine,
            execution_order=1,
            config={},
            cost_micro=0,
            is_required=True,
            access_window_days=1,
            completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        workflow = self._create_workflow([billing_map])
        billing_engine_session = workflow.engine_sessions.first()
        assert billing_engine_session is not None
        billing_engine_session.unlocked_at = timezone.now() - timedelta(days=3)
        billing_engine_session.expires_at = timezone.now() - timedelta(days=1)
        billing_engine_session.is_unlocked = True
        billing_engine_session.save(update_fields=["unlocked_at", "expires_at", "is_unlocked", "updated_at"])

        start_url = reverse("health-ops-billing-session-start")
        response = self.client.post(
            start_url,
            {"workflow_session_id": str(workflow.id)},
            format="json",
            secure=True,
        )
        self.assertEqual(response.status_code, status.HTTP_410_GONE)

    @override_settings(KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED=True)
    def test_authorize_payment_debits_kis_wallet_once(self):
        billing_engine = _seed_engine(
            "payment_billing",
            "Payment",
            ["review_charges", "select_payment_method", "authorize_payment"],
        )
        billing_map = ServiceEngineMap.objects.create(
            service=self.service,
            engine=billing_engine,
            execution_order=1,
            config={},
            cost_micro=200000,  # 2 KISC
            is_required=True,
            access_window_days=2,
            completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        workflow = self._create_workflow([billing_map])

        wallet = get_wallet_account(self.user)
        wallet.balance_cents = 50000
        wallet.save(update_fields=["balance_cents", "updated_at"])

        start_url = reverse("health-ops-billing-session-start")
        start_response = self.client.post(
            start_url,
            {
                "workflow_session_id": str(workflow.id),
                "total_amount_kisc": "2",
                "payable_amount_kisc": "2",
            },
            format="json",
            secure=True,
        )
        self.assertEqual(start_response.status_code, status.HTTP_201_CREATED)
        billing_session_id = str(start_response.data["billing_session"]["id"])

        step_url = reverse("health-ops-billing-session-step", kwargs={"billing_session_id": billing_session_id})
        review = self.client.patch(
            step_url,
            {"step_key": "review_charges", "is_completed": True},
            format="json",
            secure=True,
        )
        self.assertEqual(review.status_code, status.HTTP_200_OK)

        select = self.client.patch(
            step_url,
            {"step_key": "select_payment_method", "is_completed": True, "payload": {"payment_provider": "kis_wallet"}},
            format="json",
            secure=True,
        )
        self.assertEqual(select.status_code, status.HTTP_200_OK)

        authorize = self.client.patch(
            step_url,
            {"step_key": "authorize_payment", "is_completed": True},
            format="json",
            secure=True,
        )
        self.assertEqual(authorize.status_code, status.HTTP_200_OK)

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance_cents, 30000)
        ledger = WalletLedgerEntry.objects.filter(
            user=self.user,
            reference=f"health_ops_billing:{billing_session_id}",
            kind="purchase",
        ).order_by("created_at")
        self.assertEqual(ledger.count(), 1)
        self.assertEqual(ledger.first().amount_cents, -20000)

        billing_session = PaymentBillingSession.objects.get(id=billing_session_id)
        self.assertEqual(billing_session.status, "paid")
        self.assertIsNotNone(billing_session.paid_at)
        self.assertEqual(str(billing_session.payment_provider or "").strip(), "kis_wallet")

        authorize_again = self.client.patch(
            step_url,
            {"step_key": "authorize_payment", "is_completed": True},
            format="json",
            secure=True,
        )
        self.assertEqual(authorize_again.status_code, status.HTTP_200_OK)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance_cents, 30000)

    def test_health_billing_defaults_to_usd_provider_pending_without_wallet_debit(self):
        billing_engine = _seed_engine(
            "payment_billing",
            "Payment USD",
            ["review_charges", "select_payment_method", "authorize_payment"],
        )
        billing_map = ServiceEngineMap.objects.create(
            service=self.service,
            engine=billing_engine,
            execution_order=1,
            config={},
            cost_micro=200000,
            is_required=True,
            access_window_days=2,
            completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        workflow = self._create_workflow([billing_map])
        wallet = get_wallet_account(self.user)
        wallet.balance_cents = 50000
        wallet.save(update_fields=["balance_cents", "updated_at"])

        start_response = self.client.post(
            reverse("health-ops-billing-session-start"),
            {
                "workflow_session_id": str(workflow.id),
                "total_amount_micro": 200000,
                "payable_amount_micro": 200000,
            },
            format="json",
            secure=True,
        )
        self.assertEqual(start_response.status_code, status.HTTP_201_CREATED, start_response.data)
        billing_session = start_response.data["billing_session"]
        self.assertEqual(billing_session["payment_provider"], "flutterwave")
        self.assertEqual(billing_session["currency_label"], "USD")
        billing_session_id = str(billing_session["id"])
        step_url = reverse("health-ops-billing-session-step", kwargs={"billing_session_id": billing_session_id})

        review = self.client.patch(
            step_url,
            {"step_key": "review_charges", "is_completed": True},
            format="json",
            secure=True,
        )
        self.assertEqual(review.status_code, status.HTTP_200_OK, review.data)

        select = self.client.patch(
            step_url,
            {"step_key": "select_payment_method", "is_completed": True, "payload": {"payment_provider": "flutterwave"}},
            format="json",
            secure=True,
        )
        self.assertEqual(select.status_code, status.HTTP_200_OK, select.data)

        authorize = self.client.patch(
            step_url,
            {"step_key": "authorize_payment", "is_completed": True, "payload": {"payment_provider": "flutterwave"}},
            format="json",
            secure=True,
        )
        self.assertEqual(authorize.status_code, status.HTTP_200_OK, authorize.data)
        session = PaymentBillingSession.objects.get(id=billing_session_id)
        self.assertEqual(session.status, "payment_pending")
        self.assertIsNone(session.paid_at)
        self.assertEqual(session.payment_provider, "flutterwave")
        intent = DirectPaymentIntent.objects.get(id=session.metadata["direct_payment_intent_id"])
        self.assertEqual(intent.target_type, DirectPaymentIntent.TARGET_HEALTH_BILLING_SESSION)
        self.assertEqual(intent.amount_cents, 20000)
        self.assertEqual(session.payment_reference, intent.tx_ref)

        ok, result, _intent = reconcile_direct_payment_callback(
            payload={"data": {"tx_ref": intent.tx_ref, "status": "successful", "id": "flw-health-001"}},
            signature="",
        )
        self.assertTrue(ok)
        self.assertEqual(result, "paid")
        session.refresh_from_db()
        self.assertEqual(session.status, "paid")
        self.assertIsNotNone(session.paid_at)
        self.assertEqual(session.payload["payment_status"], "paid")
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance_cents, 50000)

    def test_health_wallet_checkout_is_disabled_by_default(self):
        billing_engine = _seed_engine(
            "payment_billing",
            "Payment Wallet Block",
            ["review_charges", "select_payment_method"],
        )
        billing_map = ServiceEngineMap.objects.create(
            service=self.service,
            engine=billing_engine,
            execution_order=1,
            config={},
            cost_micro=200000,
            is_required=True,
            access_window_days=2,
            completion_mode=EngineCompletionMode.STEP_PROGRESS,
        )
        workflow = self._create_workflow([billing_map])

        start_response = self.client.post(
            reverse("health-ops-billing-session-start"),
            {
                "workflow_session_id": str(workflow.id),
                "payment_provider": "kis_wallet",
                "payable_amount_micro": 200000,
            },
            format="json",
            secure=True,
        )

        self.assertEqual(start_response.status_code, status.HTTP_403_FORBIDDEN, start_response.data)
        self.assertEqual(start_response.data["code"], "legacy_health_wallet_checkout_disabled")
