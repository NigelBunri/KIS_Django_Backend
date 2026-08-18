from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date as ddate, datetime, time as dtime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from uuid import UUID, uuid4
import hashlib
import hmac as _hmac
import time as _time

from django.core.cache import cache
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from typing import Any

from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser

from apps.accounts.tiers import get_user_tier_features, normalize_limit_value
from apps.broadcasts.models import BroadcastHealthProfile
from apps.billing.direct_payments import create_direct_payment_intent
from apps.billing.services import debit_wallet_balance, get_wallet_account
from apps.media.safety import validate_attachment_metadata_for_safe_messaging
from apps.core.money import (
    KISC_MICRO_PER_KISC,
    KISC_MICRO_PER_USD_CENT,
    frontend_kisc_major_to_micro,
)
from apps.verification.constants import VerificationSubjectType
from apps.verification.models import VerificationCase
from apps.verification.serializers import (
    HealthInstitutionVerificationReviewSerializer,
    HealthInstitutionVerificationStartSerializer,
)
from apps.verification.services import (
    current_health_institution_verification_status,
    review_health_institution_case,
    serialize_case_status,
    start_health_institution_verification_case,
)

from .models import (
    AdmissionBedSession,
    AdmissionBedStatus,
    ClinicalEngineCode,
    ClinicalEngineSession,
    ClinicalEngineSessionStatus,
    EmergencyDispatchSession,
    EmergencyDispatchStatus,
    EngineContentBlock,
    EngineRegistry,
    EngineSession,
    EngineStepDefinition,
    EngineStepProgress,
    HealthCarePlan,
    HealthInstitution,
    HealthInstitutionMembership,
    HealthInstitutionPayoutAccountStatus,
    HomeLogisticsSession,
    HomeLogisticsStatus,
    HealthService,
    InstitutionEngineManagedItem,
    NotificationReminderSession,
    NotificationReminderStatus,
    PaymentBillingSession,
    PaymentBillingStatus,
    MembershipRole,
    PharmacyFulfillmentSession,
    PharmacyFulfillmentStatus,
    SecureMessage,
    SecureMessagingSession,
    SecureMessagingStatus,
    ServiceEngineMap,
    ServiceWorkflowSession,
    HealthVitalReading,
    VideoEngineItem,
    VideoEngineItemComment,
    VideoEngineItemLike,
    VideoEngineItemProgress,
    VideoConsultationSession,
    VideoConsultationStatus,
    WellnessProgramSession,
    WellnessProgramStatus,
    WorkflowStatus,
)
from .serializers import (
    AdmissionBedEndSerializer,
    AdmissionBedPayloadSerializer,
    AdmissionBedSessionSerializer,
    AdmissionBedStartSerializer,
    AdmissionBedStepUpdateSerializer,
    ClinicalEngineEndSerializer,
    ClinicalEnginePayloadSerializer,
    ClinicalEngineSessionSerializer,
    ClinicalEngineStartSerializer,
    ClinicalEngineStepUpdateSerializer,
    EngineContentBlockSerializer,
    EngineRegistrySerializer,
    EngineSessionSerializer,
    EmergencyDispatchEndSerializer,
    EmergencyDispatchPayloadSerializer,
    EmergencyDispatchSessionSerializer,
    EmergencyDispatchStartSerializer,
    EmergencyDispatchStepUpdateSerializer,
    EmergencyDispatchTrackingSerializer,
    HealthInstitutionSerializer,
    HealthCarePlanSerializer,
    HomeLogisticsEndSerializer,
    HomeLogisticsPayloadSerializer,
    HomeLogisticsSessionSerializer,
    HomeLogisticsStartSerializer,
    HomeLogisticsStepUpdateSerializer,
    HomeLogisticsTrackingSerializer,
    HealthServiceSerializer,
    HealthVitalReadingSerializer,
    InstitutionEngineManagedItemSerializer,
    PharmacyFulfillmentEndSerializer,
    PharmacyFulfillmentPayloadSerializer,
    PharmacyFulfillmentSessionSerializer,
    PharmacyFulfillmentStartSerializer,
    PharmacyFulfillmentStepUpdateSerializer,
    PharmacyFulfillmentTrackingSerializer,
    NotificationReminderDeliverySerializer,
    NotificationReminderEndSerializer,
    NotificationReminderPayloadSerializer,
    NotificationReminderSessionSerializer,
    NotificationReminderStartSerializer,
    NotificationReminderStepUpdateSerializer,
    PaymentBillingEndSerializer,
    PaymentBillingPayloadSerializer,
    PaymentBillingSessionSerializer,
    PaymentBillingStartSerializer,
    PaymentBillingStepUpdateSerializer,
    SecureMessageCreateSerializer,
    SecureMessageSerializer,
    SecureMessagingEndSerializer,
    SecureMessagingSessionSerializer,
    SecureMessagingStartSerializer,
    SecureMessagingStepUpdateSerializer,
    ServiceEngineMapSerializer,
    ServiceWorkflowSessionSerializer,
    VideoConsultationEndSerializer,
    VideoEngineItemCommentCreateSerializer,
    VideoEngineItemCommentSerializer,
    VideoEngineItemProgressSerializer,
    VideoEngineItemSerializer,
    VideoConsultationSessionSerializer,
    VideoConsultationStartSerializer,
    VideoConsultationStepUpdateSerializer,
    WellnessProgramActivitySerializer,
    WellnessProgramEndSerializer,
    WellnessProgramPayloadSerializer,
    WellnessProgramSessionSerializer,
    WellnessProgramStartSerializer,
    WellnessProgramStepUpdateSerializer,
    WorkflowStartSerializer,
    WorkflowStepUpdateSerializer,
)
from .services import (
    build_workflow_runtime_payload,
    evaluate_video_engine_completion,
    get_engine_runtime_state,
    refresh_workflow_engine_runtime,
    resolve_engine_access,
    resolve_engine_access_window_days,
    validate_engine_step_progression,
)


def _slugify_name(name: str) -> str:
    base = "-".join((name or "").strip().lower().split())
    return f"{base[:230]}-{uuid4().hex[:10]}"


def _normalize_engine_key(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    normalized = "-".join(raw.replace("_", " ").split())
    return normalized.replace("/", "-")


RESTRICTED_SERVICE_ENGINE_CODES = {
    "lab_order",
    "imaging_order",
    "emergency_dispatch",
    "home_logistics",
    "surgery_scheduling",
    "my_test_medium",
}
RESTRICTED_MANAGED_ENGINE_KEYS = {
    "lab-order-engine",
    "imaging-order-engine",
    "emergency-dispatch-engine",
    "home-logistics-engine",
    "surgery-scheduling-engine",
    "my-test-medium",
}


def _is_restricted_service_engine_code(value: Any) -> bool:
    return str(value or "").strip().lower() in RESTRICTED_SERVICE_ENGINE_CODES


def _is_restricted_managed_engine_key(value: str) -> bool:
    return _normalize_engine_key(value) in RESTRICTED_MANAGED_ENGINE_KEYS


def _is_institution_member(user, institution: HealthInstitution) -> bool:
    if institution.owner_id == user.id:
        return True
    return HealthInstitutionMembership.objects.filter(
        institution=institution,
        user=user,
        is_active=True,
    ).exists()


def _can_manage_institution(user, institution: HealthInstitution) -> bool:
    if institution.owner_id == user.id:
        return True
    return HealthInstitutionMembership.objects.filter(
        institution=institution,
        user=user,
        is_active=True,
        role__in=[MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.MANAGER],
    ).exists()


def _get_engine_total_steps(engine_map: ServiceEngineMap) -> int:
    count = EngineStepDefinition.objects.filter(engine=engine_map.engine).count()
    if count > 0:
        return count
    return max(1, int(engine_map.engine.default_step_count or 1))


def _sync_workflow_progress(workflow_session: ServiceWorkflowSession):
    refresh_workflow_engine_runtime(workflow_session)
    engine_sessions = list(
        workflow_session.engine_sessions.select_related("engine_map__engine").order_by("engine_map__execution_order")
    )
    total_steps = 0
    completed_steps = 0
    for session in engine_sessions:
        engine_steps = _get_engine_total_steps(session.engine_map)
        total_steps += engine_steps
        completed_steps += round((engine_steps * max(0, min(100, session.progress_percent))) / 100)

    workflow_session.total_steps = total_steps
    workflow_session.completed_steps = min(completed_steps, total_steps)
    workflow_session.progress_percent = int((workflow_session.completed_steps * 100 / total_steps)) if total_steps else 0

    current = next((s for s in engine_sessions if s.is_unlocked and not s.is_completed and not s.is_expired), None)
    workflow_session.current_engine_map = current.engine_map if current else None
    workflow_session.current_step_index = current.progress_step if current else workflow_session.current_step_index

    if engine_sessions and all(s.is_completed for s in engine_sessions):
        workflow_session.status = WorkflowStatus.COMPLETED
        workflow_session.completed_at = timezone.now()
    elif workflow_session.status == WorkflowStatus.DRAFT:
        workflow_session.status = WorkflowStatus.IN_PROGRESS

    workflow_session.save(
        update_fields=[
            "total_steps",
            "completed_steps",
            "progress_percent",
            "current_engine_map",
            "current_step_index",
            "status",
            "completed_at",
            "updated_at",
        ]
    )


USD_PER_KISC = Decimal("100")
USD_CENTS_PER_USD = 100
USD_CENTS_PER_KISC = int((USD_PER_KISC * Decimal(USD_CENTS_PER_USD)))
KIS_WALLET_PROVIDER = "kis_wallet"


def _health_wallet_checkout_enabled() -> bool:
    return bool(getattr(settings, "KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED", False))


def _health_default_payment_provider() -> str:
    return str(getattr(settings, "KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER", "flutterwave") or "flutterwave").strip().lower()


def _is_legacy_health_wallet_provider(value: object) -> bool:
    return str(value or "").strip().lower() in {"kis_wallet", "wallet", "wallet_balance", "kisc", "kisc_wallet"}


def _health_provider_payment_confirmed(billing_session: PaymentBillingSession) -> bool:
    if billing_session.paid_at:
        return True
    payload = billing_session.payload if isinstance(billing_session.payload, dict) else {}
    metadata = billing_session.metadata if isinstance(billing_session.metadata, dict) else {}
    status_value = str(
        payload.get("payment_status")
        or payload.get("paymentStatus")
        or metadata.get("payment_status")
        or ""
    ).strip().lower()
    return status_value in {"paid", "success", "succeeded", "settled"}
VIDEO_CONSULTATION_STEP_ORDER = (
    "confirm_identity",
    "test_mic_camera",
    "confirm_consent",
    "join_session",
    "post_session_summary",
)
SECURE_MESSAGING_STEP_ORDER = (
    "open_thread",
    "send_message",
    "attach_files",
    "close_thread",
)
SUPPORTED_CLINICAL_ENGINE_CODES = (
    ClinicalEngineCode.EHR_RECORDS,
    ClinicalEngineCode.LAB_ORDER,
    ClinicalEngineCode.IMAGING_ORDER,
)
ADMISSION_BED_STEP_ORDER = (
    "admission_reason",
    "insurance_verification",
    "bed_assignment",
    "admission_confirmation",
)
EMERGENCY_DISPATCH_STEP_ORDER = (
    "capture_location",
    "triage_form",
    "dispatch_ambulance",
    "track_response",
)
PHARMACY_FULFILLMENT_STEP_ORDER = (
    "verify_prescription",
    "validate_inventory",
    "confirm_delivery",
    "fulfillment_tracking",
)
PAYMENT_BILLING_STEP_ORDER = (
    "review_charges",
    "select_payment_method",
    "authorize_payment",
    "issue_receipt",
)
HOME_LOGISTICS_STEP_ORDER = (
    "select_logistics_mode",
    "schedule_window",
    "assign_route",
    "track_eta",
)
WELLNESS_PROGRAM_STEP_ORDER = (
    "enroll_program",
    "set_goals",
    "track_habits",
    "review_progress",
)
NOTIFICATION_REMINDER_STEP_ORDER = (
    "select_channels",
    "configure_rules",
    "schedule_reminders",
    "confirm_delivery",
)


class WorkflowStartFailure(Exception):
    def __init__(self, detail: str, status_code: int = status.HTTP_400_BAD_REQUEST, payload: dict[str, Any] | None = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.payload = payload or {"detail": detail}


def _can_access_workflow_session(user, workflow_session: ServiceWorkflowSession) -> bool:
    if workflow_session.user_id == user.id:
        refresh_workflow_engine_runtime(workflow_session)
        return True
    allowed = _is_institution_member(user, workflow_session.institution)
    if allowed:
        refresh_workflow_engine_runtime(workflow_session)
    return allowed


def _get_video_engine_session(workflow: ServiceWorkflowSession) -> EngineSession | None:
    return (
        workflow.engine_sessions.select_related("engine_map__engine")
        .filter(engine_map__engine__code="video")
        .order_by("engine_map__execution_order")
        .first()
    )


def _get_engine_session_by_code(workflow: ServiceWorkflowSession, engine_code: str) -> EngineSession | None:
    refresh_workflow_engine_runtime(workflow)
    return (
        workflow.engine_sessions.select_related("engine_map__engine")
        .filter(engine_map__engine__code=engine_code)
        .order_by("engine_map__execution_order")
        .first()
    )


def _get_secure_messaging_engine_session(workflow: ServiceWorkflowSession) -> EngineSession | None:
    return _get_engine_session_by_code(workflow, "secure_messaging")


def _get_admission_engine_session(workflow: ServiceWorkflowSession) -> EngineSession | None:
    return _get_engine_session_by_code(workflow, "admission_bed")


def _get_emergency_engine_session(workflow: ServiceWorkflowSession) -> EngineSession | None:
    return _get_engine_session_by_code(workflow, "emergency_dispatch")


def _get_pharmacy_engine_session(workflow: ServiceWorkflowSession) -> EngineSession | None:
    return _get_engine_session_by_code(workflow, "pharmacy_fulfillment")


def _get_billing_engine_session(workflow: ServiceWorkflowSession) -> EngineSession | None:
    return _get_engine_session_by_code(workflow, "payment_billing")


def _get_home_logistics_engine_session(workflow: ServiceWorkflowSession) -> EngineSession | None:
    return _get_engine_session_by_code(workflow, "home_logistics")


def _get_wellness_engine_session(workflow: ServiceWorkflowSession) -> EngineSession | None:
    return _get_engine_session_by_code(workflow, "wellness_program")


def _get_notification_engine_session(workflow: ServiceWorkflowSession) -> EngineSession | None:
    return _get_engine_session_by_code(workflow, "notification_reminder")


def _list_engine_step_keys(engine_session: EngineSession) -> list[str]:
    step_keys = list(
        EngineStepDefinition.objects.filter(engine=engine_session.engine_map.engine)
        .order_by("step_order")
        .values_list("step_key", flat=True)
    )
    if step_keys:
        return [str(step_key) for step_key in step_keys]
    fallback_count = max(1, int(engine_session.engine_map.engine.default_step_count or 1))
    return [f"step_{idx + 1}" for idx in range(fallback_count)]


def _default_step_state(step_keys: list[str]) -> dict[str, dict[str, Any]]:
    return {
        step_key: {"is_completed": False, "completed_at": None, "payload": {}}
        for step_key in step_keys
    }


def _is_step_key_valid(engine_session: EngineSession, step_key: str) -> bool:
    return step_key in set(_list_engine_step_keys(engine_session))


def _are_all_steps_completed(step_state: dict[str, Any], step_keys: list[str]) -> bool:
    for step_key in step_keys:
        row = step_state.get(step_key) if isinstance(step_state, dict) else None
        if not isinstance(row, dict) or not bool(row.get("is_completed")):
            return False
    return True


def _append_tracking_event(
    rows: Any,
    *,
    event_type: str,
    status_value: str,
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    safe_rows = rows if isinstance(rows, list) else []
    event_payload = payload if isinstance(payload, dict) else {}
    safe_rows.append(
        {
            "type": str(event_type or "update"),
            "status": str(status_value or ""),
            "timestamp": timezone.now().isoformat(),
            "payload": event_payload,
        }
    )
    return safe_rows[-200:]


def _append_emergency_tracking_event(
    emergency_session: EmergencyDispatchSession,
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _append_tracking_event(
        emergency_session.tracking_events,
        event_type=event_type,
        status_value=emergency_session.status,
        payload=payload,
    )


def _issue_video_tokens(video_session: VideoConsultationSession, *, ttl_minutes: int = 90):
    secret = (getattr(settings, 'SECRET_KEY', '') or '').encode()
    exp_ts = int(_time.time()) + max(10, ttl_minutes) * 60
    session_id = str(video_session.id)

    def _make_token(role: str) -> str:
        payload = f"{role}:{session_id}:{exp_ts}"
        sig = _hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:24]
        return f"{payload}:{sig}"

    video_session.host_join_token = _make_token('host')
    video_session.participant_join_token = _make_token('participant')
    video_session.token_expires_at = timezone.now() + timedelta(minutes=max(10, ttl_minutes))


def _engine_access_error_response(workflow: ServiceWorkflowSession, engine_session: EngineSession) -> Response | None:
    verdict = resolve_engine_access(workflow, engine_session)
    if verdict.allowed:
        return None
    return Response(
        {"detail": verdict.detail, "engine_state": verdict.state},
        status=verdict.status_code,
    )


def _apply_engine_step_update(
    *,
    workflow: ServiceWorkflowSession,
    engine_session: EngineSession,
    step_key: str,
    is_completed: bool,
    payload: dict[str, Any] | None = None,
    content_position: float | None = None,
    content_position_provided: bool = False,
) -> tuple[ServiceWorkflowSession, EngineSession]:
    refresh_workflow_engine_runtime(workflow)
    verdict = resolve_engine_access(workflow, engine_session)
    if not verdict.allowed:
        raise PermissionDenied(verdict.detail)

    step_ok, step_detail = validate_engine_step_progression(
        engine_session,
        step_key,
        is_completed=bool(is_completed),
    )
    if not step_ok:
        raise ValidationError(step_detail)

    row, _ = EngineStepProgress.objects.get_or_create(
        engine_session=engine_session,
        step_key=step_key,
        defaults={"payload": {}, "is_completed": False},
    )
    if isinstance(payload, dict):
        row.payload = payload
    if content_position_provided:
        row.content_position = content_position
    row.is_completed = bool(is_completed)
    row.completed_at = timezone.now() if row.is_completed else None
    row.save(update_fields=["payload", "content_position", "is_completed", "completed_at", "updated_at"])

    completed_count = engine_session.step_progress.filter(is_completed=True).count()
    total_engine_steps = _get_engine_total_steps(engine_session.engine_map)
    engine_session.progress_step = min(completed_count, total_engine_steps)
    engine_session.progress_percent = int((engine_session.progress_step * 100) / total_engine_steps)
    engine_session.is_completed = engine_session.progress_step >= total_engine_steps
    if engine_session.is_completed and not engine_session.completed_at:
        engine_session.completed_at = timezone.now()
    if engine_session.is_completed and engine_session.is_expired:
        engine_session.is_expired = False
    if engine_session.is_completed and engine_session.expired_at:
        engine_session.expired_at = None
    engine_session.save(
        update_fields=[
            "progress_step",
            "progress_percent",
            "is_completed",
            "is_expired",
            "expired_at",
            "completed_at",
            "updated_at",
        ]
    )

    if engine_session.is_completed:
        next_session = (
            EngineSession.objects.filter(
                workflow_session=workflow,
                engine_map__execution_order__gt=engine_session.engine_map.execution_order,
            )
            .select_related("engine_map")
            .order_by("engine_map__execution_order")
            .first()
        )
        if next_session and not next_session.is_unlocked:
            next_session.is_unlocked = True
            if not next_session.unlocked_at:
                next_session.unlocked_at = timezone.now()
            if next_session.is_expired:
                next_session.is_expired = False
            if next_session.expired_at:
                next_session.expired_at = None
            window_days = resolve_engine_access_window_days(next_session)
            if window_days > 0:
                next_session.expires_at = next_session.unlocked_at + timedelta(days=window_days)
            else:
                next_session.expires_at = None
            next_session.save(
                update_fields=[
                    "is_unlocked",
                    "unlocked_at",
                    "expires_at",
                    "is_expired",
                    "expired_at",
                    "updated_at",
                ]
            )

    _sync_workflow_progress(workflow)
    cache.set(f"health_ops:workflow:{workflow.id}:progress", workflow.progress_percent, timeout=3600)
    workflow.refresh_from_db()
    engine_session.refresh_from_db()
    return workflow, engine_session


def _parse_date_value(value: Any) -> ddate | None:
    if isinstance(value, ddate):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return ddate.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _parse_hhmm(value: Any) -> dtime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    pieces = raw.split(":")
    if len(pieces) != 2:
        return None
    try:
        hour = int(pieces[0])
        minute = int(pieces[1])
    except (TypeError, ValueError):
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return dtime(hour=hour, minute=minute)


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = parse_datetime(value.strip())
    else:
        parsed = None
    if not parsed:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _start_workflow_session(
    *,
    user,
    institution: HealthInstitution,
    service: HealthService,
    auto_debit: bool = True,
    bypass_payment: bool = False,
    assessment_payload: dict[str, Any] | None = None,
    metadata_extra: dict[str, Any] | None = None,
) -> ServiceWorkflowSession:
    mappings = list(
        ServiceEngineMap.objects.filter(service=service)
        .exclude(engine__code__in=RESTRICTED_SERVICE_ENGINE_CODES)
        .select_related("engine")
        .order_by("execution_order")
    )
    if not mappings:
        raise WorkflowStartFailure("No engines mapped to this service.", status.HTTP_400_BAD_REQUEST)

    total_cost_micro = sum(int(m.cost_micro or 0) for m in mappings)
    total_cost_cents = _micro_to_cents(total_cost_micro)
    wallet = get_wallet_account(user)

    if not bypass_payment and auto_debit and total_cost_cents > 0 and wallet.balance_cents < total_cost_cents:
        available_micro = _cents_to_micro(int(wallet.balance_cents or 0))
        raise WorkflowStartFailure(
            "Insufficient KIS wallet balance.",
            status.HTTP_402_PAYMENT_REQUIRED,
            payload={
                "detail": "Insufficient KIS wallet balance.",
                "required_micro": int(total_cost_micro),
                "available_micro": int(available_micro),
                "required_kisc": _micro_to_kisc_text(total_cost_micro),
                "available_kisc": _micro_to_kisc_text(available_micro),
                "required_cents": total_cost_cents,
                "available_cents": int(wallet.balance_cents or 0),
            },
        )

    metadata = {"assessment_payload": assessment_payload or {}}
    if bypass_payment:
        metadata["payment_mode"] = "owner_preview"
    elif auto_debit:
        metadata["payment_mode"] = "auto_debit"
    else:
        metadata["payment_mode"] = "deferred"
    if isinstance(metadata_extra, dict):
        metadata.update(metadata_extra)

    workflow = ServiceWorkflowSession.objects.create(
        institution=institution,
        service=service,
        user=user,
        status=WorkflowStatus.IN_PROGRESS,
        is_locked_by_payment=(total_cost_micro > 0 and not auto_debit and not bypass_payment),
        requires_assessment=service.requires_assessment,
        assessment_completed=not service.requires_assessment,
        metadata=metadata,
    )

    now_value = timezone.now()
    sessions: list[EngineSession] = []
    for index, mapping in enumerate(mappings):
        unlocked = index == 0 and not workflow.is_locked_by_payment
        unlocked_at = now_value if unlocked else None
        expires_at = None
        if unlocked:
            window_days = max(0, int(getattr(mapping, "access_window_days", 0) or 0))
            if window_days > 0:
                expires_at = unlocked_at + timedelta(days=window_days)
        sessions.append(
            EngineSession(
                workflow_session=workflow,
                engine_map=mapping,
                user=user,
                is_unlocked=unlocked,
                unlocked_at=unlocked_at,
                expires_at=expires_at,
            )
        )
    EngineSession.objects.bulk_create(sessions)

    if not bypass_payment and auto_debit and total_cost_cents > 0:
        record_ledger(
            user=user,
            kind="purchase",
            amount_cents=-total_cost_cents,
            reference=f"workflow:{workflow.id}",
            meta={
                "institution_id": str(institution.id),
                "service_id": str(service.id),
                "mode": "service_chain_once",
                "charged_cents": int(total_cost_cents),
                "charged_micro": int(total_cost_micro),
                "charged_kisc": _micro_to_kisc_text(total_cost_micro),
            },
        )

    _sync_workflow_progress(workflow)
    cache.set(f"health_ops:workflow:{workflow.id}:progress", workflow.progress_percent, timeout=3600)
    workflow.refresh_from_db()
    return workflow


def _clean_service_reference(value: Any) -> str:
    return str(value or "").strip()


def _normalize_service_alias(value: Any) -> str:
    raw = _clean_service_reference(value).lower().replace("_", " ").replace("-", " ")
    return " ".join(raw.split())


def _coerce_uuid_string(value: Any) -> str | None:
    raw = _clean_service_reference(value)
    if not raw:
        return None
    try:
        return str(UUID(raw))
    except (TypeError, ValueError):
        return None


def _extract_institution_hint(request) -> str:
    query_hint = _clean_service_reference(
        request.query_params.get("institution_id") or request.query_params.get("institutionId")
    )
    if query_hint:
        return query_hint

    if not isinstance(getattr(request, "data", None), dict):
        return ""
    payload = request.data
    direct_hint = _clean_service_reference(payload.get("institution_id") or payload.get("institutionId"))
    if direct_hint:
        return direct_hint
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return _clean_service_reference(metadata.get("institution_id") or metadata.get("institutionId"))


def _extract_broadcast_institutions(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    institutions: list[dict[str, Any]] = []
    direct_rows = payload.get("institutions")
    if isinstance(direct_rows, list):
        institutions.extend([row for row in direct_rows if isinstance(row, dict)])

    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    health_profile = profiles.get("health") if isinstance(profiles.get("health"), dict) else {}
    profile_rows = health_profile.get("institutions")
    if isinstance(profile_rows, list):
        institutions.extend([row for row in profile_rows if isinstance(row, dict)])
    return institutions


def _extract_broadcast_member_user_id(member: Any) -> str:
    if not isinstance(member, dict):
        return ""
    raw = _clean_service_reference(member.get("userId") or member.get("user_id") or member.get("id"))
    if raw.lower().startswith("user-"):
        raw = raw[5:]
    return _coerce_uuid_string(raw) or raw


def _resolve_broadcast_membership_role(institution_payload: dict[str, Any], user) -> str:
    members: list[Any] = []
    for key in ("members", "employees"):
        rows = institution_payload.get(key)
        if isinstance(rows, list):
            members.extend(rows)

    target_user_id = _coerce_uuid_string(getattr(user, "id", "")) or _clean_service_reference(getattr(user, "id", ""))
    target_phone = _clean_service_reference(getattr(user, "phone", ""))
    target_email = _clean_service_reference(getattr(user, "email", "")).lower()

    owner_contact = institution_payload.get("owner_contact")
    if not isinstance(owner_contact, dict):
        owner_contact = institution_payload.get("ownerContact") if isinstance(institution_payload.get("ownerContact"), dict) else {}
    owner_contact_user_id = _extract_broadcast_member_user_id(owner_contact)
    owner_contact_phone = _clean_service_reference(owner_contact.get("phone"))
    owner_contact_email = _clean_service_reference(owner_contact.get("email")).lower()
    if target_user_id and owner_contact_user_id and owner_contact_user_id == target_user_id:
        return "owner"
    if target_phone and owner_contact_phone and owner_contact_phone == target_phone:
        return "owner"
    if target_email and owner_contact_email and owner_contact_email == target_email:
        return "owner"

    for member in members:
        if not isinstance(member, dict):
            continue
        member_user_id = _extract_broadcast_member_user_id(member)
        member_phone = _clean_service_reference(member.get("phone"))
        member_email = _clean_service_reference(member.get("email")).lower()
        if target_user_id and member_user_id and member_user_id == target_user_id:
            return _clean_service_reference(member.get("role")).lower()
        if target_phone and member_phone and member_phone == target_phone:
            return _clean_service_reference(member.get("role")).lower()
        if target_email and member_email and member_email == target_email:
            return _clean_service_reference(member.get("role")).lower()
    return ""


def _resolve_broadcast_owner_user(institution_payload: dict[str, Any], fallback_user):
    candidate_user_ids: list[str] = []
    members = institution_payload.get("members")
    if isinstance(members, list):
        for member in members:
            if not isinstance(member, dict):
                continue
            if _clean_service_reference(member.get("role")).lower() == "owner":
                owner_id = _extract_broadcast_member_user_id(member)
                if owner_id:
                    candidate_user_ids.append(owner_id)

    owner_contact = institution_payload.get("owner_contact")
    if not isinstance(owner_contact, dict):
        owner_contact = institution_payload.get("ownerContact") if isinstance(institution_payload.get("ownerContact"), dict) else {}
    owner_contact_user_id = _clean_service_reference(owner_contact.get("userId") or owner_contact.get("user_id") or owner_contact.get("id"))
    if owner_contact_user_id:
        candidate_user_ids.append(owner_contact_user_id)

    model_cls = fallback_user.__class__
    for candidate in candidate_user_ids:
        parsed_uuid = _coerce_uuid_string(candidate)
        if not parsed_uuid:
            continue
        owner_user = model_cls.objects.filter(id=parsed_uuid).first()
        if owner_user:
            return owner_user
    return fallback_user


def _collect_broadcast_service_entries(institution_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates = [
        institution_payload.get("services"),
        institution_payload.get("service_templates"),
        institution_payload.get("serviceTemplates"),
        (institution_payload.get("dashboard") or {}).get("services")
        if isinstance(institution_payload.get("dashboard"), dict)
        else None,
    ]
    for candidate in candidates:
        if not isinstance(candidate, list):
            continue
        for entry in candidate:
            if isinstance(entry, dict):
                rows.append(entry)
    return rows


def _find_broadcast_service_entry(institution_payload: dict[str, Any], service_ref: str) -> dict[str, Any] | None:
    clean_ref = _clean_service_reference(service_ref)
    if not clean_ref:
        return None
    normalized_ref = _normalize_service_alias(clean_ref)

    services = _collect_broadcast_service_entries(institution_payload)
    for service in services:
        service_id = _clean_service_reference(service.get("id") or service.get("service_id"))
        if service_id and service_id == clean_ref:
            return service

    for service in services:
        service_name = _normalize_service_alias(service.get("name") or service.get("title"))
        if service_name and service_name == normalized_ref:
            return service
    return None


def _default_service_name_from_ref(service_ref: str) -> str:
    label = _normalize_service_alias(service_ref)
    if not label:
        return "Health Service"
    return " ".join(part.capitalize() for part in label.split())


def _normalize_institution_type(value: Any) -> str:
    raw = _clean_service_reference(value).lower()
    if raw == "diagnostics_center":
        raw = "diagnostics"
    allowed = {str(code) for code, _label in HealthInstitution._meta.get_field("institution_type").choices}
    if raw in allowed:
        return raw
    return "clinic"


def _normalize_membership_role(value: Any) -> str:
    raw = _clean_service_reference(value).lower()
    if raw in {MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.MANAGER, MembershipRole.STAFF, MembershipRole.MEMBER}:
        return raw
    return MembershipRole.MEMBER


def _cents_to_micro(cents_value: Any) -> int:
    try:
        cents = int(round(float(cents_value or 0)))
    except (TypeError, ValueError):
        cents = 0
    return max(0, cents) * KISC_MICRO_PER_USD_CENT


def _micro_to_cents(micro_value: Any) -> int:
    try:
        micro = int(micro_value or 0)
    except (TypeError, ValueError):
        micro = 0
    safe_micro = max(0, micro)
    return int(
        (Decimal(safe_micro) / Decimal(KISC_MICRO_PER_USD_CENT)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _micro_to_kisc_text(micro_value: Any) -> str:
    try:
        micro = int(micro_value or 0)
    except (TypeError, ValueError):
        micro = 0
    safe_micro = max(0, micro)
    amount = (Decimal(safe_micro) / Decimal(KISC_MICRO_PER_KISC)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    text = format(amount, "f").rstrip("0").rstrip(".")
    return text or "0"


def _kisc_to_micro(value: Any, *, allow_empty: bool = False) -> int | None:
    raw = str(value).strip() if value is not None else ""
    if not raw:
        if allow_empty:
            return None
        raise ValidationError("KISC amount is required.")
    result = frontend_kisc_major_to_micro(raw, allow_none=True)
    if result is None:
        raise ValidationError("Invalid KISC amount.")
    if result < 0:
        raise ValidationError("KISC amount cannot be negative.")
    return int(result)


def _accessible_services_for_user(user, institution_hint: str = ""):
    rows = (
        HealthService.objects.select_related("institution")
        .filter(
            Q(institution__owner=user)
            | Q(institution__memberships__user=user, institution__memberships__is_active=True)
        )
        .filter(is_active=True, institution__is_active=True)
        .distinct()
    )
    clean_hint = _clean_service_reference(institution_hint)
    if not clean_hint:
        return rows
    hint_uuid = _coerce_uuid_string(clean_hint)
    filters = Q(institution__settings__legacy_institution_id=clean_hint)
    if hint_uuid:
        filters |= Q(institution_id=hint_uuid)
    return rows.filter(filters)


def _service_matches_reference(service: HealthService, service_ref: str) -> bool:
    clean_ref = _clean_service_reference(service_ref).lower()
    if not clean_ref:
        return False
    if str(service.id).lower() == clean_ref:
        return True

    schema = service.assessment_schema if isinstance(service.assessment_schema, dict) else {}
    legacy_values: list[str] = []
    legacy_single = _clean_service_reference(schema.get("legacy_service_id"))
    if legacy_single:
        legacy_values.append(legacy_single)
    legacy_many = schema.get("legacy_service_aliases")
    if isinstance(legacy_many, list):
        legacy_values.extend([_clean_service_reference(value) for value in legacy_many if _clean_service_reference(value)])

    for value in legacy_values:
        if value.lower() == clean_ref:
            return True

    normalized_ref = _normalize_service_alias(clean_ref)
    if _normalize_service_alias(service.name) == normalized_ref:
        return True
    return any(_normalize_service_alias(value) == normalized_ref for value in legacy_values)


def _find_accessible_health_service(user, service_ref: str, institution_hint: str = "") -> HealthService | None:
    clean_ref = _clean_service_reference(service_ref)
    if not clean_ref:
        return None
    rows = _accessible_services_for_user(user, institution_hint=institution_hint)

    service_uuid = _coerce_uuid_string(clean_ref)
    if service_uuid:
        hit = rows.filter(id=service_uuid).first()
        if hit:
            return hit

    hit = rows.filter(assessment_schema__legacy_service_id=clean_ref).first()
    if hit:
        return hit

    matches = [service for service in rows if _service_matches_reference(service, clean_ref)]
    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        strict_legacy = []
        for service in matches:
            schema = service.assessment_schema if isinstance(service.assessment_schema, dict) else {}
            if _clean_service_reference(schema.get("legacy_service_id")).lower() == clean_ref.lower():
                strict_legacy.append(service)
        if len(strict_legacy) == 1:
            return strict_legacy[0]
    return None


def _accessible_institutions_for_user(user):
    return (
        HealthInstitution.objects.filter(
            Q(owner=user) | Q(memberships__user=user, memberships__is_active=True)
        )
        .distinct()
    )


def _find_accessible_health_institution(user, institution_ref: str) -> HealthInstitution | None:
    clean_ref = _clean_service_reference(institution_ref)
    if not clean_ref:
        return None
    rows = _accessible_institutions_for_user(user)
    institution_uuid = _coerce_uuid_string(clean_ref)
    if institution_uuid:
        hit = rows.filter(id=institution_uuid).first()
        if hit:
            return hit
    return rows.filter(
        Q(settings__legacy_institution_id=clean_ref) | Q(slug=clean_ref)
    ).first()


@transaction.atomic
def _bootstrap_health_ops_institution_from_broadcast(
    user, institution_hint: str
) -> tuple[HealthInstitution | None, Response | None]:
    clean_hint = _clean_service_reference(institution_hint)
    if not clean_hint:
        return None, None

    selected_institution: dict[str, Any] | None = None
    selected_role = ""
    for profile in BroadcastHealthProfile.objects.select_related("profile__user").all():
        for institution_payload in _extract_broadcast_institutions(profile.payload):
            institution_ids = {
                _clean_service_reference(institution_payload.get("id")),
                _clean_service_reference(institution_payload.get("institution_id")),
                _clean_service_reference(institution_payload.get("institutionId")),
            }
            institution_ids.discard("")
            if clean_hint not in institution_ids:
                continue
            profile_owner_id = str(getattr(getattr(profile, "profile", None), "user_id", "") or "")
            selected_role = (
                "owner"
                if profile_owner_id and profile_owner_id == str(getattr(user, "id", "") or "")
                else _resolve_broadcast_membership_role(institution_payload, user)
            )
            if not selected_role:
                return None, Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
            selected_institution = institution_payload
            break
        if selected_institution:
            break

    if not selected_institution:
        return None, None

    owner_user = _resolve_broadcast_owner_user(selected_institution, fallback_user=user)
    institution_name = _clean_service_reference(selected_institution.get("name")) or "Health Institution"
    institution_type = _normalize_institution_type(selected_institution.get("type"))
    availability = (
        selected_institution.get("availability")
        if isinstance(selected_institution.get("availability"), dict)
        else {}
    )
    timezone_name = _clean_service_reference(availability.get("timezone")) or "UTC"

    institution = HealthInstitution.objects.filter(
        settings__legacy_institution_id=clean_hint
    ).first()
    if not institution:
        institution = HealthInstitution.objects.create(
            owner=owner_user,
            name=institution_name,
            slug=_slugify_name(institution_name),
            institution_type=institution_type,
            timezone=timezone_name,
            settings={
                "legacy_institution_id": clean_hint,
                "legacy_source": "broadcast_health_profile",
            },
            is_active=True,
        )
    else:
        next_settings = dict(institution.settings) if isinstance(institution.settings, dict) else {}
        if next_settings.get("legacy_institution_id") != clean_hint:
            next_settings["legacy_institution_id"] = clean_hint
        if next_settings.get("legacy_source") != "broadcast_health_profile":
            next_settings["legacy_source"] = "broadcast_health_profile"
        needs_save = False
        if institution.name != institution_name and institution_name:
            institution.name = institution_name
            needs_save = True
        if institution.institution_type != institution_type:
            institution.institution_type = institution_type
            needs_save = True
        if institution.timezone != timezone_name:
            institution.timezone = timezone_name
            needs_save = True
        if institution.settings != next_settings:
            institution.settings = next_settings
            needs_save = True
        if needs_save:
            institution.save(
                update_fields=["name", "institution_type", "timezone", "settings", "updated_at"]
            )

    HealthInstitutionMembership.objects.update_or_create(
        institution=institution,
        user=user,
        defaults={
            "role": _normalize_membership_role(selected_role),
            "is_active": True,
            "invited_by": owner_user if owner_user.id != user.id else None,
        },
    )

    return institution, None


def _resolve_institution_for_request(
    user,
    institution_id: Any,
    *,
    allow_bootstrap: bool = False,
) -> tuple[HealthInstitution | None, Response | None]:
    clean_ref = _clean_service_reference(institution_id)
    if not clean_ref:
        return None, Response({"detail": "institution_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    institution = _find_accessible_health_institution(user, clean_ref)
    if institution:
        return institution, None

    if allow_bootstrap:
        institution, bootstrap_error = _bootstrap_health_ops_institution_from_broadcast(
            user, clean_ref
        )
        if bootstrap_error:
            return None, bootstrap_error
        if institution and _is_institution_member(user, institution):
            return institution, None

    return None, Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)


@transaction.atomic
def _bootstrap_health_ops_context_from_broadcast(user, service_ref: str, institution_hint: str) -> tuple[HealthService | None, Response | None]:
    clean_ref = _clean_service_reference(service_ref)
    clean_hint = _clean_service_reference(institution_hint)
    if not clean_ref or not clean_hint:
        return None, None

    selected_institution: dict[str, Any] | None = None
    selected_role = ""
    for profile in BroadcastHealthProfile.objects.select_related("profile__user").all():
        for institution_payload in _extract_broadcast_institutions(profile.payload):
            institution_ids = {
                _clean_service_reference(institution_payload.get("id")),
                _clean_service_reference(institution_payload.get("institution_id")),
                _clean_service_reference(institution_payload.get("institutionId")),
            }
            institution_ids.discard("")
            if clean_hint not in institution_ids:
                continue
            profile_owner_id = str(getattr(getattr(profile, "profile", None), "user_id", "") or "")
            selected_role = (
                "owner"
                if profile_owner_id and profile_owner_id == str(getattr(user, "id", "") or "")
                else _resolve_broadcast_membership_role(institution_payload, user)
            )
            if not selected_role:
                return None, Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
            selected_institution = institution_payload
            break
        if selected_institution:
            break

    if not selected_institution:
        return None, None

    owner_user = _resolve_broadcast_owner_user(selected_institution, fallback_user=user)
    institution_name = _clean_service_reference(selected_institution.get("name")) or "Health Institution"
    institution_type = _normalize_institution_type(selected_institution.get("type"))
    availability = selected_institution.get("availability") if isinstance(selected_institution.get("availability"), dict) else {}
    timezone_name = _clean_service_reference(availability.get("timezone")) or "UTC"

    institution = HealthInstitution.objects.filter(settings__legacy_institution_id=clean_hint).first()
    if not institution:
        institution = HealthInstitution.objects.create(
            owner=owner_user,
            name=institution_name,
            slug=_slugify_name(institution_name),
            institution_type=institution_type,
            timezone=timezone_name,
            settings={
                "legacy_institution_id": clean_hint,
                "legacy_source": "broadcast_health_profile",
            },
            is_active=True,
        )
    else:
        next_settings = dict(institution.settings) if isinstance(institution.settings, dict) else {}
        if next_settings.get("legacy_institution_id") != clean_hint:
            next_settings["legacy_institution_id"] = clean_hint
        if next_settings.get("legacy_source") != "broadcast_health_profile":
            next_settings["legacy_source"] = "broadcast_health_profile"
        needs_save = False
        if institution.name != institution_name and institution_name:
            institution.name = institution_name
            needs_save = True
        if institution.institution_type != institution_type:
            institution.institution_type = institution_type
            needs_save = True
        if institution.timezone != timezone_name:
            institution.timezone = timezone_name
            needs_save = True
        if institution.settings != next_settings:
            institution.settings = next_settings
            needs_save = True
        if needs_save:
            institution.save(update_fields=["name", "institution_type", "timezone", "settings", "updated_at"])

    HealthInstitutionMembership.objects.update_or_create(
        institution=institution,
        user=user,
        defaults={
            "role": _normalize_membership_role(selected_role),
            "is_active": True,
        },
    )

    service_entry = _find_broadcast_service_entry(selected_institution, clean_ref)
    service_name = _clean_service_reference((service_entry or {}).get("name") or (service_entry or {}).get("title")) or _default_service_name_from_ref(clean_ref)
    service_description = _clean_service_reference((service_entry or {}).get("description") or (service_entry or {}).get("summary"))
    base_price_cents = (service_entry or {}).get("basePriceCents", (service_entry or {}).get("base_price_cents"))
    base_cost_micro = _cents_to_micro(base_price_cents)

    service = (
        HealthService.objects.filter(institution=institution, assessment_schema__legacy_service_id=clean_ref).first()
        or _find_accessible_health_service(user, clean_ref, institution_hint=clean_hint)
    )
    if service and service.institution_id != institution.id:
        service = None

    if not service:
        service = HealthService.objects.filter(institution=institution, name__iexact=service_name).first()

    if not service:
        service = HealthService.objects.create(
            institution=institution,
            name=service_name,
            description=service_description,
            is_active=True,
            requires_assessment=False,
            assessment_schema={
                "legacy_service_id": clean_ref,
                "legacy_service_aliases": sorted({clean_ref}),
            },
            base_cost_micro=base_cost_micro,
        )
    else:
        schema = service.assessment_schema if isinstance(service.assessment_schema, dict) else {}
        aliases = schema.get("legacy_service_aliases") if isinstance(schema.get("legacy_service_aliases"), list) else []
        alias_set = {_clean_service_reference(alias) for alias in aliases if _clean_service_reference(alias)}
        alias_set.add(clean_ref)
        updated_schema = dict(schema)
        if not _clean_service_reference(updated_schema.get("legacy_service_id")):
            updated_schema["legacy_service_id"] = clean_ref
        updated_schema["legacy_service_aliases"] = sorted(alias_set)

        service_needs_save = False
        if service.description != service_description and service_description:
            service.description = service_description
            service_needs_save = True
        if int(service.base_cost_micro or 0) <= 0 and base_cost_micro > 0:
            service.base_cost_micro = base_cost_micro
            service_needs_save = True
        if service.assessment_schema != updated_schema:
            service.assessment_schema = updated_schema
            service_needs_save = True
        if not service.is_active:
            service.is_active = True
            service_needs_save = True
        if service_needs_save:
            service.save(update_fields=["description", "base_cost_micro", "assessment_schema", "is_active", "updated_at"])

    return service, None


def _resolve_service_for_request(request, service_id: Any, *, allow_bootstrap: bool = False) -> tuple[HealthService | None, Response | None]:
    clean_ref = _clean_service_reference(service_id)
    if not clean_ref:
        return None, Response({"detail": "service_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    institution_hint = _extract_institution_hint(request)
    service = _find_accessible_health_service(request.user, clean_ref, institution_hint=institution_hint)
    if service:
        return service, None

    if allow_bootstrap:
        service, bootstrap_error = _bootstrap_health_ops_context_from_broadcast(
            request.user,
            clean_ref,
            institution_hint=institution_hint,
        )
        if bootstrap_error:
            return None, bootstrap_error
        if service:
            return service, None

        service = _find_accessible_health_service(request.user, clean_ref, institution_hint=institution_hint)
        if service:
            return service, None

    return None, Response(
        {"detail": "Service not found or not mapped for this account."},
        status=status.HTTP_404_NOT_FOUND,
    )


class HealthInstitutionListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = HealthInstitution.objects.filter(
            Q(owner=request.user)
            | Q(memberships__user=request.user, memberships__is_active=True)
        ).distinct().order_by("-created_at")
        return Response({"results": HealthInstitutionSerializer(qs, many=True, context={"request": request}).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request):
        features = get_user_tier_features(request.user)
        health_limit = normalize_limit_value(features.get("health_profiles"), default=0)
        if health_limit is not None and health_limit <= 0:
            raise PermissionDenied("Health institution profiles require Business Pro tier or higher.")
        if health_limit is not None:
            existing_count = HealthInstitution.objects.filter(owner=request.user).count()
            if existing_count >= health_limit:
                raise PermissionDenied(
                    f"Your current plan allows up to {health_limit} health institution profile{'s' if health_limit != 1 else ''}. Upgrade to create more."
                )
        serializer = HealthInstitutionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        institution = serializer.save(owner=request.user, slug=_slugify_name(serializer.validated_data["name"]))
        HealthInstitutionMembership.objects.get_or_create(
            institution=institution,
            user=request.user,
            defaults={"role": MembershipRole.OWNER, "is_active": True},
        )
        return Response({"institution": HealthInstitutionSerializer(institution, context={"request": request}).data}, status=status.HTTP_201_CREATED)


class HealthInstitutionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id):
        institution, error_response = _resolve_institution_for_request(
            request.user,
            institution_id,
            allow_bootstrap=True,
        )
        if error_response:
            return error_response
        return Response({"institution": HealthInstitutionSerializer(institution, context={"request": request}).data}, status=status.HTTP_200_OK)


class HealthInstitutionPayoutAccountConnectView(APIView):
    """Connects the institution's Flutterwave subaccount for direct-to-
    institution settlement splitting — mirrors
    EducationInstitutionPayoutAccountConnectView (apps/broadcasts/views.py)
    exactly, using the shared apps.billing.payout_accounts helper."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, institution_id: str):
        from apps.billing.payout_accounts import create_flutterwave_subaccount

        institution = get_object_or_404(HealthInstitution, id=institution_id)
        if not _can_manage_institution(request.user, institution):
            raise PermissionDenied("You do not have permission to manage this institution.")

        account_bank = str(request.data.get("account_bank") or "").strip()
        account_number = str(request.data.get("account_number") or "").strip()
        business_name = str(request.data.get("business_name") or institution.name).strip()
        country = str(request.data.get("country") or "NG").strip().upper()
        if not account_bank or not account_number:
            raise ValidationError({"detail": "Bank and account number are required."})

        institution.payout_account_status = HealthInstitutionPayoutAccountStatus.PENDING
        institution.save(update_fields=["payout_account_status", "updated_at"])

        try:
            subaccount_id = create_flutterwave_subaccount(
                account_bank=account_bank,
                account_number=account_number,
                business_name=business_name,
                business_email=institution.owner.email or "",
                country=country,
            )
        except ValidationError:
            institution.payout_account_status = HealthInstitutionPayoutAccountStatus.NOT_CONNECTED
            institution.save(update_fields=["payout_account_status", "updated_at"])
            raise

        institution.flutterwave_subaccount_id = subaccount_id
        institution.payout_account_status = HealthInstitutionPayoutAccountStatus.ACTIVE
        institution.payout_account_name = business_name
        institution.payout_bank_last4 = account_number[-4:] if len(account_number) >= 4 else account_number
        institution.save(
            update_fields=[
                "flutterwave_subaccount_id",
                "payout_account_status",
                "payout_account_name",
                "payout_bank_last4",
                "updated_at",
            ]
        )
        return Response(
            {
                "payout_account_status": institution.payout_account_status,
                "payout_account_name": institution.payout_account_name,
                "payout_bank_last4": institution.payout_bank_last4,
            },
            status=status.HTTP_200_OK,
        )


class HealthCareSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workflows = ServiceWorkflowSession.objects.filter(user=request.user).select_related("institution", "service")
        open_workflows = workflows.exclude(status__in=[WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED])
        care_plans = HealthCarePlan.objects.filter(user=request.user).select_related("institution", "service")
        active_care_plans = care_plans.exclude(status__in=["completed", "cancelled"])
        latest_vitals = HealthVitalReading.objects.filter(user=request.user).select_related("institution")[:10]
        reminders = NotificationReminderSession.objects.filter(user=request.user).exclude(
            status__in=[NotificationReminderStatus.COMPLETED, NotificationReminderStatus.DISABLED, NotificationReminderStatus.CANCELLED]
        )
        return Response(
            {
                "summary": {
                    "openWorkflowCount": open_workflows.count(),
                    "activeCarePlanCount": active_care_plans.count(),
                    "activeReminderCount": reminders.count(),
                    "recentVitalCount": HealthVitalReading.objects.filter(user=request.user).count(),
                    "providerMessagingReady": SecureMessagingSession.objects.filter(user=request.user).exists(),
                    "videoCareReady": VideoConsultationSession.objects.filter(user=request.user).exists(),
                    "lowBandwidthReady": bool(getattr(settings, 'HEALTH_LOW_BANDWIDTH_READY', True)),
                    "familySafeCare": bool(getattr(settings, 'HEALTH_FAMILY_SAFE_CARE', False)),
                },
                "care_plans": HealthCarePlanSerializer(active_care_plans[:20], many=True).data,
                "latest_vitals": HealthVitalReadingSerializer(latest_vitals, many=True).data,
                "workflows": ServiceWorkflowSessionSerializer(open_workflows[:20], many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class HealthInstitutionVerificationStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id):
        institution, error_response = _resolve_institution_for_request(
            request.user,
            institution_id,
            allow_bootstrap=True,
        )
        if error_response:
            return error_response
        if not _is_institution_member(request.user, institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        return Response(current_health_institution_verification_status(institution), status=status.HTTP_200_OK)


class HealthInstitutionVerificationStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, institution_id):
        institution, error_response = _resolve_institution_for_request(
            request.user,
            institution_id,
            allow_bootstrap=True,
        )
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        serializer = HealthInstitutionVerificationStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case = start_health_institution_verification_case(
            institution=institution,
            actor=request.user,
            provider=serializer.validated_data.get("provider") or "",
            evidence_metadata=serializer.validated_data.get("evidence_metadata") or {},
        )
        return Response(
            {
                "case": serialize_case_status(case),
                "status": current_health_institution_verification_status(institution),
            },
            status=status.HTTP_201_CREATED,
        )


class HealthInstitutionVerificationReviewView(APIView):
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def post(self, request, institution_id, case_id):
        institution = get_object_or_404(HealthInstitution, id=institution_id)
        case = VerificationCase.objects.select_related("subject").filter(
            id=case_id,
            subject__subject_type=VerificationSubjectType.HEALTH_INSTITUTION,
            subject__subject_id=institution.id,
        ).first()
        if not case:
            raise ValidationError({"case_id": "Invalid health institution verification case."})
        serializer = HealthInstitutionVerificationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case, badges = review_health_institution_case(
            case=case,
            actor=request.user,
            action=serializer.validated_data["action"],
            notes=serializer.validated_data.get("notes", ""),
            badge_codes=serializer.validated_data.get("badge_codes") or None,
        )
        return Response(
            {
                "case": serialize_case_status(case),
                "badges": [{"code": badge.code, "label": badge.label, "level": badge.level} for badge in badges],
            },
            status=status.HTTP_200_OK,
        )


class HealthServiceListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id):
        institution, error_response = _resolve_institution_for_request(
            request.user,
            institution_id,
            allow_bootstrap=True,
        )
        if error_response:
            return error_response
        services = list(
            HealthService.objects.filter(institution=institution)
            .prefetch_related("engine_mappings__engine")
            .order_by("name")
        )
        visible_services = []
        for service in services:
            mappings = list(service.engine_mappings.all())
            if not mappings or any(
                not _is_restricted_service_engine_code(getattr(mapping.engine, "code", ""))
                for mapping in mappings
            ):
                visible_services.append(service)
        return Response({"results": HealthServiceSerializer(visible_services, many=True).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request, institution_id):
        institution, error_response = _resolve_institution_for_request(
            request.user,
            institution_id,
            allow_bootstrap=True,
        )
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        payload = dict(request.data)
        if "base_cost_micro" not in payload and "base_cost_kisc" in payload:
            payload["base_cost_micro"] = _kisc_to_micro(payload.get("base_cost_kisc"), allow_empty=True) or 0
        payload.pop("base_cost_kisc", None)
        payload["institution"] = str(institution.id)
        serializer = HealthServiceSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        service = serializer.save()
        return Response({"service": HealthServiceSerializer(service).data}, status=status.HTTP_201_CREATED)


class HealthCarePlanListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = HealthCarePlan.objects.filter(user=request.user).select_related("institution", "service", "workflow_session")
        institution_id = request.query_params.get("institution")
        workflow_id = request.query_params.get("workflow_session")
        if institution_id:
            qs = qs.filter(institution_id=institution_id)
        if workflow_id:
            qs = qs.filter(workflow_session_id=workflow_id)
        return Response({"results": HealthCarePlanSerializer(qs[:80], many=True).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request):
        institution = get_object_or_404(HealthInstitution, id=request.data.get("institution"))
        if not _is_institution_member(request.user, institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        serializer = HealthCarePlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        care_plan = serializer.save(user=request.user)
        return Response({"care_plan": HealthCarePlanSerializer(care_plan).data}, status=status.HTTP_201_CREATED)


class HealthVitalReadingListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = HealthVitalReading.objects.filter(user=request.user).select_related("institution", "workflow_session")
        institution_id = request.query_params.get("institution")
        reading_type = request.query_params.get("reading_type")
        if institution_id:
            qs = qs.filter(institution_id=institution_id)
        if reading_type:
            qs = qs.filter(reading_type=reading_type)
        return Response({"results": HealthVitalReadingSerializer(qs[:120], many=True).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request):
        institution = get_object_or_404(HealthInstitution, id=request.data.get("institution"))
        if not _is_institution_member(request.user, institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        serializer = HealthVitalReadingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vital = serializer.save(user=request.user)
        return Response({"vital": HealthVitalReadingSerializer(vital).data}, status=status.HTTP_201_CREATED)


class ServiceEngineMappingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, service_id):
        service, error_response = _resolve_service_for_request(request, service_id, allow_bootstrap=False)
        if error_response:
            return error_response
        if not _is_institution_member(request.user, service.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        rows = (
            ServiceEngineMap.objects.filter(service=service)
            .exclude(engine__code__in=RESTRICTED_SERVICE_ENGINE_CODES)
            .select_related("engine")
            .order_by("execution_order")
        )
        return Response({"results": ServiceEngineMapSerializer(rows, many=True).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request, service_id):
        service, error_response = _resolve_service_for_request(request, service_id, allow_bootstrap=False)
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, service.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        payload = dict(request.data)
        if "cost_micro" not in payload and "cost_kisc" in payload:
            payload["cost_micro"] = _kisc_to_micro(payload.get("cost_kisc"), allow_empty=True) or 0
        payload.pop("cost_kisc", None)
        payload["service"] = str(service.id)
        serializer = ServiceEngineMapSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        engine = get_object_or_404(EngineRegistry, id=serializer.validated_data["engine_id"])
        if _is_restricted_service_engine_code(getattr(engine, "code", "")):
            return Response(
                {"detail": "This engine is coming up and cannot be attached to services yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        mapping = serializer.save()
        return Response({"mapping": ServiceEngineMapSerializer(mapping).data}, status=status.HTTP_201_CREATED)


def _resequence_service_engine_mappings(service: HealthService, ordered_rows: list[ServiceEngineMap]):
    updates: list[ServiceEngineMap] = []
    for index, row in enumerate(ordered_rows, start=1):
        if int(row.execution_order or 0) != index:
            row.execution_order = index
            updates.append(row)
    if updates:
        ServiceEngineMap.objects.bulk_update(updates, ["execution_order", "updated_at"])


class ServiceEngineMappingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, service_id, mapping_id):
        service, error_response = _resolve_service_for_request(request, service_id, allow_bootstrap=False)
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, service.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        mapping = get_object_or_404(
            ServiceEngineMap.objects.select_related("service", "engine"),
            id=mapping_id,
            service=service,
        )

        payload = request.data if isinstance(request.data, dict) else {}
        updates: list[str] = []

        if "execution_order" in payload:
            new_order = max(1, int(payload.get("execution_order") or 1))
            all_rows = list(ServiceEngineMap.objects.filter(service=service).order_by("execution_order", "created_at"))
            all_rows = [row for row in all_rows if row.id != mapping.id]
            insert_index = min(len(all_rows), new_order - 1)
            all_rows.insert(insert_index, mapping)
            _resequence_service_engine_mappings(service, all_rows)
            mapping.refresh_from_db()

        if "cost_micro" in payload:
            mapping.cost_micro = max(0, int(payload.get("cost_micro") or 0))
            updates.append("cost_micro")
        elif "cost_kisc" in payload:
            mapping.cost_micro = max(0, int(_kisc_to_micro(payload.get("cost_kisc"), allow_empty=True) or 0))
            updates.append("cost_micro")
        if "is_required" in payload:
            mapping.is_required = bool(payload.get("is_required"))
            updates.append("is_required")
        if "access_window_days" in payload:
            mapping.access_window_days = max(0, int(payload.get("access_window_days") or 0))
            updates.append("access_window_days")
        if "completion_mode" in payload:
            completion_mode = str(payload.get("completion_mode") or "").strip()
            allowed_modes = {choice for choice, _ in ServiceEngineMap._meta.get_field("completion_mode").choices}
            if completion_mode not in allowed_modes:
                return Response({"detail": "Invalid completion_mode."}, status=status.HTTP_400_BAD_REQUEST)
            mapping.completion_mode = completion_mode
            updates.append("completion_mode")
        if "config" in payload:
            config_value = payload.get("config")
            mapping.config = config_value if isinstance(config_value, dict) else {}
            updates.append("config")

        if updates:
            mapping.save(update_fields=[*updates, "updated_at"])

        return Response({"mapping": ServiceEngineMapSerializer(mapping).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def delete(self, request, service_id, mapping_id):
        service, error_response = _resolve_service_for_request(request, service_id, allow_bootstrap=False)
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, service.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        mapping = get_object_or_404(ServiceEngineMap, id=mapping_id, service=service)
        mapping.delete()
        remaining = list(ServiceEngineMap.objects.filter(service=service).order_by("execution_order", "created_at"))
        _resequence_service_engine_mappings(service, remaining)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ServiceEngineMappingReorderView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, service_id):
        service, error_response = _resolve_service_for_request(request, service_id, allow_bootstrap=False)
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, service.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data if isinstance(request.data, dict) else {}
        mapping_ids = payload.get("mapping_ids") if isinstance(payload.get("mapping_ids"), list) else []
        cleaned_ids = [str(item).strip() for item in mapping_ids if str(item).strip()]
        if not cleaned_ids:
            return Response({"detail": "mapping_ids is required."}, status=status.HTTP_400_BAD_REQUEST)

        rows = list(ServiceEngineMap.objects.filter(service=service))
        by_id = {str(row.id): row for row in rows}
        if set(cleaned_ids) != set(by_id.keys()):
            return Response(
                {"detail": "mapping_ids must include all service mappings exactly once."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ordered_rows = [by_id[row_id] for row_id in cleaned_ids]
        _resequence_service_engine_mappings(service, ordered_rows)
        refreshed = ServiceEngineMap.objects.filter(service=service).select_related("engine").order_by("execution_order")
        return Response({"results": ServiceEngineMapSerializer(refreshed, many=True).data}, status=status.HTTP_200_OK)


class EngineRegistryListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        engines = EngineRegistry.objects.filter(is_active=True).exclude(code__in=RESTRICTED_SERVICE_ENGINE_CODES).order_by("name")
        return Response({"results": EngineRegistrySerializer(engines, many=True).data}, status=status.HTTP_200_OK)


class WorkflowSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = WorkflowStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        institution = get_object_or_404(HealthInstitution, id=serializer.validated_data["institution_id"])
        if not _is_institution_member(request.user, institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        service = get_object_or_404(HealthService, id=serializer.validated_data["service_id"], institution=institution)
        owner_preview = bool(serializer.validated_data.get("owner_preview", False))
        if owner_preview and institution.owner_id != request.user.id:
            return Response({"detail": "Owner preview is only available to the institution owner."}, status=status.HTTP_403_FORBIDDEN)
        try:
            requested_auto_debit = bool(serializer.validated_data.get("auto_debit", False))
            legacy_wallet_disabled = requested_auto_debit and not _health_wallet_checkout_enabled()
            workflow = _start_workflow_session(
                user=request.user,
                institution=institution,
                service=service,
                auto_debit=(False if owner_preview or legacy_wallet_disabled else requested_auto_debit),
                bypass_payment=owner_preview,
                assessment_payload=serializer.validated_data.get("assessment_payload", {}),
                metadata_extra={
                    **({"owner_preview": True} if owner_preview else {}),
                    **({"legacy_health_wallet_checkout_disabled": True, "payment_mode": "provider_pending"} if legacy_wallet_disabled else {}),
                } or None,
            )
        except WorkflowStartFailure as exc:
            return Response(exc.payload, status=exc.status_code)

        return Response({"session": ServiceWorkflowSessionSerializer(workflow).data}, status=status.HTTP_201_CREATED)


class WorkflowSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, workflow_session_id):
        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("user").prefetch_related("engine_sessions__engine_map__engine"),
            id=workflow_session_id,
            user=request.user,
        )
        serializer = WorkflowStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        engine_session = get_object_or_404(
            EngineSession,
            id=serializer.validated_data["engine_session_id"],
            workflow_session=workflow,
            user=request.user,
        )
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        step_key = serializer.validated_data["step_key"]
        workflow, _ = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=serializer.validated_data["is_completed"],
            payload=serializer.validated_data.get("payload", {}),
            content_position=serializer.validated_data.get("content_position"),
            content_position_provided="content_position" in serializer.validated_data,
        )
        return Response({"session": ServiceWorkflowSessionSerializer(workflow).data}, status=status.HTTP_200_OK)


class WorkflowSessionResumeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, workflow_session_id):
        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.prefetch_related("engine_sessions__engine_map__engine"),
            id=workflow_session_id,
            user=request.user,
        )
        refresh_workflow_engine_runtime(workflow)
        _sync_workflow_progress(workflow)
        return Response({"session": ServiceWorkflowSessionSerializer(workflow).data}, status=status.HTTP_200_OK)


class VideoConsultationSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = VideoConsultationStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_session = _get_video_engine_session(workflow)
        if not engine_session:
            return Response({"detail": "Video engine is not mapped to this workflow."}, status=status.HTTP_404_NOT_FOUND)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        metadata = serializer.validated_data.get("metadata", {})
        recording_enabled = bool(serializer.validated_data.get("recording_enabled", False))
        waiting_room_enabled = bool(serializer.validated_data.get("waiting_room_enabled", True))

        created = False
        video_session = (
            VideoConsultationSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session", "institution", "service", "user")
            .filter(workflow_session=workflow)
            .first()
        )
        if not video_session:
            created = True
            video_session = VideoConsultationSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                room_code=f"kis-{uuid4().hex[:12]}",
                status=VideoConsultationStatus.WAITING_ROOM,
                recording_enabled=recording_enabled,
                waiting_room_enabled=waiting_room_enabled,
                step_state={
                    step_key: {"is_completed": False, "completed_at": None, "payload": {}}
                    for step_key in VIDEO_CONSULTATION_STEP_ORDER
                },
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            _issue_video_tokens(video_session)
            video_session.save()
        else:
            if video_session.status in {VideoConsultationStatus.COMPLETED, VideoConsultationStatus.CANCELLED}:
                return Response(
                    {"detail": "This video session has already ended."},
                    status=status.HTTP_409_CONFLICT,
                )
            if timezone.now() >= video_session.token_expires_at:
                _issue_video_tokens(video_session)
            video_session.recording_enabled = recording_enabled
            video_session.waiting_room_enabled = waiting_room_enabled
            if video_session.status == VideoConsultationStatus.SCHEDULED:
                video_session.status = VideoConsultationStatus.WAITING_ROOM
            if isinstance(metadata, dict) and metadata:
                next_meta = video_session.metadata if isinstance(video_session.metadata, dict) else {}
                next_meta.update(metadata)
                video_session.metadata = next_meta
            video_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "video_session_id": str(video_session.id),
                "video_status": video_session.status,
                "room_code": video_session.room_code,
                "token_expires_at": video_session.token_expires_at.isoformat(),
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "video_session": VideoConsultationSessionSerializer(video_session, context={"request": request}).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class VideoConsultationSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, video_session_id):
        video_session = get_object_or_404(
            VideoConsultationSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=video_session_id,
        )
        if not _can_access_workflow_session(request.user, video_session.workflow_session):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        refresh_tokens = timezone.now() >= (video_session.token_expires_at - timedelta(minutes=3))
        if refresh_tokens and video_session.status not in {VideoConsultationStatus.COMPLETED, VideoConsultationStatus.CANCELLED}:
            _issue_video_tokens(video_session)
            video_session.save(update_fields=["host_join_token", "participant_join_token", "token_expires_at", "updated_at"])

        payload = {
            "video_session": VideoConsultationSessionSerializer(video_session, context={"request": request}).data,
            "engine_session": EngineSessionSerializer(video_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(video_session.workflow_session).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class VideoConsultationSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, video_session_id):
        video_session = get_object_or_404(
            VideoConsultationSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=video_session_id,
        )
        workflow = video_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if video_session.status in {VideoConsultationStatus.COMPLETED, VideoConsultationStatus.CANCELLED}:
            return Response({"detail": "This video session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = video_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = VideoConsultationStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step_key = str(serializer.validated_data["step_key"])
        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload = serializer.validated_data.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        now_value = timezone.now()
        next_step_state = video_session.step_state if isinstance(video_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload
        next_step_state[step_key] = current_row
        video_session.step_state = next_step_state

        next_meta = video_session.metadata if isinstance(video_session.metadata, dict) else {}
        step_payloads = next_meta.get("step_payloads")
        if not isinstance(step_payloads, dict):
            step_payloads = {}
        step_payloads[step_key] = payload
        next_meta["step_payloads"] = step_payloads
        video_session.metadata = next_meta

        if step_key == "join_session" and is_completed:
            video_session.status = VideoConsultationStatus.IN_SESSION
            if not video_session.started_at:
                video_session.started_at = now_value
        if step_key == "post_session_summary" and is_completed:
            video_session.status = VideoConsultationStatus.COMPLETED
            if not video_session.started_at:
                video_session.started_at = now_value
            video_session.ended_at = now_value

        video_session.save(update_fields=["step_state", "metadata", "status", "started_at", "ended_at", "updated_at"])

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"video_step": payload},
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "video_session_id": str(video_session.id),
                "video_status": video_session.status,
                "last_video_step": step_key,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "video_session": VideoConsultationSessionSerializer(video_session, context={"request": request}).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class VideoConsultationSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, video_session_id):
        video_session = get_object_or_404(
            VideoConsultationSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=video_session_id,
        )
        workflow = video_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if video_session.status in {VideoConsultationStatus.COMPLETED, VideoConsultationStatus.CANCELLED}:
            return Response({"detail": "This video session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = VideoConsultationEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or VideoConsultationStatus.COMPLETED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})

        now_value = timezone.now()
        next_meta = video_session.metadata if isinstance(video_session.metadata, dict) else {}
        if summary:
            next_meta["post_session_summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        video_session.metadata = next_meta
        video_session.ended_at = now_value

        engine_session = video_session.engine_session
        if status_value == VideoConsultationStatus.CANCELLED:
            video_session.status = VideoConsultationStatus.CANCELLED
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            video_session.status = VideoConsultationStatus.COMPLETED
            if not video_session.started_at:
                video_session.started_at = now_value
            next_steps = video_session.step_state if isinstance(video_session.step_state, dict) else {}
            next_steps["post_session_summary"] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            video_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key="post_session_summary",
                is_completed=True,
                payload={"summary": summary},
            )

        video_session.save(update_fields=["metadata", "step_state", "status", "started_at", "ended_at", "updated_at"])

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "video_session_id": str(video_session.id),
                "video_status": video_session.status,
                "ended_at": video_session.ended_at.isoformat() if video_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "video_session": VideoConsultationSessionSerializer(video_session, context={"request": request}).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class ServiceVideoEngineItemListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, service_id, mapping_id):
        service, error_response = _resolve_service_for_request(request, service_id, allow_bootstrap=False)
        if error_response:
            return error_response
        mapping = get_object_or_404(
            ServiceEngineMap.objects.select_related("service__institution", "engine"),
            id=mapping_id,
            service=service,
        )
        if str(mapping.engine.code) != "video":
            return Response({"detail": "Video items can only be managed on video engines."}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_institution_member(request.user, service.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        rows = VideoEngineItem.objects.filter(engine_map=mapping).order_by("sort_order", "created_at")
        if not _can_manage_institution(request.user, service.institution):
            rows = rows.filter(is_active=True)
        return Response(
            {"results": VideoEngineItemSerializer(rows, many=True, context={"request": request}).data},
            status=status.HTTP_200_OK,
        )

    @transaction.atomic
    def post(self, request, service_id, mapping_id):
        service, error_response = _resolve_service_for_request(request, service_id, allow_bootstrap=False)
        if error_response:
            return error_response
        mapping = get_object_or_404(
            ServiceEngineMap.objects.select_related("service__institution", "engine"),
            id=mapping_id,
            service=service,
        )
        if str(mapping.engine.code) != "video":
            return Response({"detail": "Video items can only be managed on video engines."}, status=status.HTTP_400_BAD_REQUEST)
        if not _can_manage_institution(request.user, service.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        payload = dict(request.data)
        payload["engine_map"] = str(mapping.id)
        serializer = VideoEngineItemSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        item = serializer.save(created_by=request.user, updated_by=request.user)
        return Response(
            {"item": VideoEngineItemSerializer(item, context={"request": request}).data},
            status=status.HTTP_201_CREATED,
        )


class ServiceVideoEngineItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, service_id, mapping_id, item_id):
        service, error_response = _resolve_service_for_request(request, service_id, allow_bootstrap=False)
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, service.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        item = get_object_or_404(
            VideoEngineItem.objects.select_related("engine_map__service__institution", "engine_map__engine"),
            id=item_id,
            engine_map_id=mapping_id,
            engine_map__service=service,
        )
        if str(item.engine_map.engine.code) != "video":
            return Response({"detail": "Video items can only be managed on video engines."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = VideoEngineItemSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save(updated_by=request.user)
        return Response(
            {"item": VideoEngineItemSerializer(updated, context={"request": request}).data},
            status=status.HTTP_200_OK,
        )

    @transaction.atomic
    def delete(self, request, service_id, mapping_id, item_id):
        service, error_response = _resolve_service_for_request(request, service_id, allow_bootstrap=False)
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, service.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        item = get_object_or_404(
            VideoEngineItem.objects.select_related("engine_map__engine"),
            id=item_id,
            engine_map_id=mapping_id,
            engine_map__service=service,
        )
        if str(item.engine_map.engine.code) != "video":
            return Response({"detail": "Video items can only be managed on video engines."}, status=status.HTTP_400_BAD_REQUEST)
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EngineSessionVideoItemListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, engine_session_id):
        engine_session = get_object_or_404(
            EngineSession.objects.select_related("workflow_session", "engine_map__service__institution", "engine_map__engine"),
            id=engine_session_id,
        )
        workflow = engine_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error
        if str(engine_session.engine_map.engine.code) != "video":
            return Response({"detail": "This engine session does not support video items."}, status=status.HTTP_400_BAD_REQUEST)

        rows = VideoEngineItem.objects.filter(engine_map=engine_session.engine_map, is_active=True).order_by("sort_order", "created_at")
        return Response(
            {
                "results": VideoEngineItemSerializer(
                    rows,
                    many=True,
                    context={"request": request, "engine_session_id": str(engine_session.id)},
                ).data,
            },
            status=status.HTTP_200_OK,
        )


class EngineSessionVideoItemProgressView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, engine_session_id, item_id):
        engine_session = get_object_or_404(
            EngineSession.objects.select_related("workflow_session", "engine_map__engine"),
            id=engine_session_id,
        )
        workflow = engine_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error
        if str(engine_session.engine_map.engine.code) != "video":
            return Response({"detail": "This engine session does not support video items."}, status=status.HTTP_400_BAD_REQUEST)

        item = get_object_or_404(VideoEngineItem, id=item_id, engine_map=engine_session.engine_map, is_active=True)
        raw_data = request.data if isinstance(request.data, dict) else {}
        watched_seconds = max(0, int(raw_data.get("watched_seconds") or 0))
        explicit_complete = bool(raw_data.get("is_completed"))
        completion_payload = raw_data.get("payload") if isinstance(raw_data.get("payload"), dict) else {}

        duration_seconds = max(0, int(item.duration_seconds or 0))
        auto_complete = duration_seconds > 0 and watched_seconds >= int(duration_seconds * 0.9)
        should_complete = explicit_complete or auto_complete

        now_value = timezone.now()
        progress_row, _ = VideoEngineItemProgress.objects.get_or_create(
            item=item,
            engine_session=engine_session,
            user=request.user,
            defaults={
                "watched_seconds": watched_seconds,
                "is_completed": should_complete,
                "started_at": now_value,
                "last_watched_at": now_value,
            },
        )
        if watched_seconds > int(progress_row.watched_seconds or 0):
            progress_row.watched_seconds = watched_seconds
        progress_row.last_watched_at = now_value
        if should_complete:
            progress_row.is_completed = True
            if not progress_row.completed_at:
                progress_row.completed_at = now_value
        progress_row.save(update_fields=["watched_seconds", "is_completed", "completed_at", "last_watched_at", "updated_at"])

        completed_items, total_items, is_done = evaluate_video_engine_completion(engine_session)
        if is_done:
            step_keys = _list_engine_step_keys(engine_session)
            for step_key in step_keys:
                already_completed = EngineStepProgress.objects.filter(
                    engine_session=engine_session,
                    step_key=step_key,
                    is_completed=True,
                ).exists()
                if already_completed:
                    continue
                workflow, engine_session = _apply_engine_step_update(
                    workflow=workflow,
                    engine_session=engine_session,
                    step_key=step_key,
                    is_completed=True,
                    payload={
                        "source": "video_item_progress",
                        "item_id": str(item.id),
                        "payload": completion_payload,
                    },
                )

        return Response(
            {
                "progress": VideoEngineItemProgressSerializer(progress_row).data,
                "completed_items": completed_items,
                "total_items": total_items,
                "engine_completed": bool(engine_session.is_completed),
                "engine_session": EngineSessionSerializer(engine_session).data,
                "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            },
            status=status.HTTP_200_OK,
        )


class EngineSessionVideoItemLikeView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, engine_session_id, item_id):
        engine_session = get_object_or_404(
            EngineSession.objects.select_related("workflow_session", "engine_map__engine"),
            id=engine_session_id,
        )
        workflow = engine_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error
        if str(engine_session.engine_map.engine.code) != "video":
            return Response({"detail": "This engine session does not support video items."}, status=status.HTTP_400_BAD_REQUEST)

        item = get_object_or_404(VideoEngineItem, id=item_id, engine_map=engine_session.engine_map, is_active=True)
        VideoEngineItemLike.objects.get_or_create(item=item, engine_session=engine_session, user=request.user)
        return Response({"likes_count": int(item.likes.count()), "viewer_liked": True}, status=status.HTTP_200_OK)

    @transaction.atomic
    def delete(self, request, engine_session_id, item_id):
        engine_session = get_object_or_404(
            EngineSession.objects.select_related("workflow_session", "engine_map__engine"),
            id=engine_session_id,
        )
        workflow = engine_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error
        if str(engine_session.engine_map.engine.code) != "video":
            return Response({"detail": "This engine session does not support video items."}, status=status.HTTP_400_BAD_REQUEST)

        item = get_object_or_404(VideoEngineItem, id=item_id, engine_map=engine_session.engine_map, is_active=True)
        VideoEngineItemLike.objects.filter(item=item, engine_session=engine_session, user=request.user).delete()
        return Response({"likes_count": int(item.likes.count()), "viewer_liked": False}, status=status.HTTP_200_OK)


class EngineSessionVideoItemCommentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, engine_session_id, item_id):
        engine_session = get_object_or_404(
            EngineSession.objects.select_related("workflow_session", "engine_map__engine"),
            id=engine_session_id,
        )
        workflow = engine_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        item = get_object_or_404(VideoEngineItem, id=item_id, engine_map=engine_session.engine_map, is_active=True)
        rows = item.comments.filter(engine_session=engine_session, is_deleted=False).order_by("created_at")
        return Response(
            {"results": VideoEngineItemCommentSerializer(rows, many=True).data},
            status=status.HTTP_200_OK,
        )

    @transaction.atomic
    def post(self, request, engine_session_id, item_id):
        engine_session = get_object_or_404(
            EngineSession.objects.select_related("workflow_session", "engine_map__engine"),
            id=engine_session_id,
        )
        workflow = engine_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        item = get_object_or_404(VideoEngineItem, id=item_id, engine_map=engine_session.engine_map, is_active=True)
        serializer = VideoEngineItemCommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = VideoEngineItemComment.objects.create(
            item=item,
            engine_session=engine_session,
            user=request.user,
            body=str(serializer.validated_data.get("body") or "").strip(),
        )
        return Response(
            {"comment": VideoEngineItemCommentSerializer(row).data},
            status=status.HTTP_201_CREATED,
        )


class SecureMessagingSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = SecureMessagingStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_session = _get_secure_messaging_engine_session(workflow)
        if not engine_session:
            return Response({"detail": "Secure messaging engine is not mapped to this workflow."}, status=status.HTTP_404_NOT_FOUND)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        metadata = serializer.validated_data.get("metadata", {})
        created = False
        messaging_session = (
            SecureMessagingSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session", "institution", "service", "user")
            .filter(workflow_session=workflow)
            .first()
        )
        step_keys = _list_engine_step_keys(engine_session) or list(SECURE_MESSAGING_STEP_ORDER)

        if not messaging_session:
            created = True
            messaging_session = SecureMessagingSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                thread_code=f"kis-chat-{uuid4().hex[:12]}",
                status=SecureMessagingStatus.WAITING,
                step_state=_default_step_state(step_keys),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            messaging_session.save()
        else:
            if messaging_session.status in {SecureMessagingStatus.COMPLETED, SecureMessagingStatus.CLOSED}:
                return Response(
                    {"detail": "This secure messaging session has already ended."},
                    status=status.HTTP_409_CONFLICT,
                )
            if not isinstance(messaging_session.step_state, dict) or not messaging_session.step_state:
                messaging_session.step_state = _default_step_state(step_keys)
            if isinstance(metadata, dict) and metadata:
                next_meta = messaging_session.metadata if isinstance(messaging_session.metadata, dict) else {}
                next_meta.update(metadata)
                messaging_session.metadata = next_meta
            messaging_session.save(update_fields=["step_state", "metadata", "updated_at"])

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "secure_messaging_session_id": str(messaging_session.id),
                "secure_messaging_status": messaging_session.status,
                "thread_code": messaging_session.thread_code,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "messaging_session": SecureMessagingSessionSerializer(messaging_session).data,
            "messages": [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class SecureMessagingSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, messaging_session_id):
        messaging_session = get_object_or_404(
            SecureMessagingSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=messaging_session_id,
        )
        if not _can_access_workflow_session(request.user, messaging_session.workflow_session):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(200, limit))

        mark_read = str(request.query_params.get("mark_read", "1")).strip().lower() not in {"0", "false", "no"}
        unread_qs = messaging_session.messages.filter(is_read=False).exclude(sender=request.user)
        if mark_read:
            now_value = timezone.now()
            unread_qs.update(is_read=True, read_at=now_value, updated_at=now_value)

        message_rows_desc = list(
            messaging_session.messages.select_related("sender")
            .order_by("-created_at")[:limit]
        )
        message_rows = list(reversed(message_rows_desc))
        unread_count = messaging_session.messages.filter(is_read=False).exclude(sender=request.user).count()

        payload = {
            "messaging_session": SecureMessagingSessionSerializer(messaging_session).data,
            "messages": SecureMessageSerializer(message_rows, many=True).data,
            "unread_count": unread_count,
            "engine_session": EngineSessionSerializer(messaging_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(messaging_session.workflow_session).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_200_OK)


class SecureMessagingSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, messaging_session_id):
        messaging_session = get_object_or_404(
            SecureMessagingSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=messaging_session_id,
        )
        workflow = messaging_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if messaging_session.status in {SecureMessagingStatus.COMPLETED, SecureMessagingStatus.CLOSED}:
            return Response({"detail": "This secure messaging session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = messaging_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = SecureMessagingStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step_key = str(serializer.validated_data["step_key"])
        if not _is_step_key_valid(engine_session, step_key):
            return Response({"detail": "Invalid step_key for secure messaging engine."}, status=status.HTTP_400_BAD_REQUEST)

        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload = serializer.validated_data.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        now_value = timezone.now()
        next_step_state = messaging_session.step_state if isinstance(messaging_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload
        next_step_state[step_key] = current_row
        messaging_session.step_state = next_step_state

        if step_key in {"open_thread", "send_message"} and is_completed:
            if not messaging_session.started_at:
                messaging_session.started_at = now_value
            messaging_session.status = SecureMessagingStatus.ACTIVE

        if step_key == "close_thread" and is_completed:
            messaging_session.status = SecureMessagingStatus.COMPLETED
            messaging_session.ended_at = now_value

        messaging_session.save(update_fields=["step_state", "status", "started_at", "ended_at", "updated_at"])

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"messaging_step": payload},
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "secure_messaging_session_id": str(messaging_session.id),
                "secure_messaging_status": messaging_session.status,
                "last_secure_messaging_step": step_key,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "messaging_session": SecureMessagingSessionSerializer(messaging_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_200_OK)


class SecureMessagingMessageCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, messaging_session_id):
        messaging_session = get_object_or_404(
            SecureMessagingSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=messaging_session_id,
        )
        workflow = messaging_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if messaging_session.status in {SecureMessagingStatus.COMPLETED, SecureMessagingStatus.CLOSED}:
            return Response({"detail": "This secure messaging session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = SecureMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        metadata = serializer.validated_data.get("metadata", {})
        if isinstance(metadata, dict):
            validate_attachment_metadata_for_safe_messaging(metadata.get("attachments") or [])
            validate_attachment_metadata_for_safe_messaging([metadata.get("attachment")] if metadata.get("attachment") else [])

        message = SecureMessage.objects.create(
            session=messaging_session,
            sender=request.user,
            message_type=serializer.validated_data["message_type"],
            body=serializer.validated_data.get("body", ""),
            attachment_url=serializer.validated_data.get("attachment_url"),
            metadata={
                **(metadata if isinstance(metadata, dict) else {}),
                "media_safety": {
                    "status": "allowed",
                    "quarantine_enabled": True,
                    "provider_live_calls_enabled": False,
                },
            },
        )

        now_value = timezone.now()
        messaging_session.last_message_at = now_value
        if not messaging_session.started_at:
            messaging_session.started_at = now_value
        messaging_session.status = SecureMessagingStatus.ACTIVE

        next_steps = messaging_session.step_state if isinstance(messaging_session.step_state, dict) else {}
        for auto_step_key in ("open_thread", "send_message"):
            row = next_steps.get(auto_step_key)
            if not isinstance(row, dict):
                row = {}
            row["is_completed"] = True
            row["completed_at"] = now_value.isoformat()
            row["payload"] = {"source": "message_create", "message_id": str(message.id)}
            next_steps[auto_step_key] = row
        messaging_session.step_state = next_steps
        messaging_session.save(update_fields=["last_message_at", "started_at", "status", "step_state", "updated_at"])

        engine_session = messaging_session.engine_session
        if _is_step_key_valid(engine_session, "open_thread"):
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key="open_thread",
                is_completed=True,
                payload={"source": "message_create", "message_id": str(message.id)},
            )
        if _is_step_key_valid(engine_session, "send_message"):
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key="send_message",
                is_completed=True,
                payload={"message_id": str(message.id), "message_type": message.message_type},
            )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "secure_messaging_session_id": str(messaging_session.id),
                "secure_messaging_status": messaging_session.status,
                "last_message_id": str(message.id),
                "last_message_at": now_value.isoformat(),
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "message": SecureMessageSerializer(message).data,
            "messaging_session": SecureMessagingSessionSerializer(messaging_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_201_CREATED)


class SecureMessagingSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, messaging_session_id):
        messaging_session = get_object_or_404(
            SecureMessagingSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=messaging_session_id,
        )
        workflow = messaging_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if messaging_session.status in {SecureMessagingStatus.COMPLETED, SecureMessagingStatus.CLOSED}:
            return Response({"detail": "This secure messaging session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = SecureMessagingEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or SecureMessagingStatus.COMPLETED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})

        now_value = timezone.now()
        next_meta = messaging_session.metadata if isinstance(messaging_session.metadata, dict) else {}
        if summary:
            next_meta["thread_summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        messaging_session.metadata = next_meta
        messaging_session.ended_at = now_value

        engine_session = messaging_session.engine_session
        if status_value == SecureMessagingStatus.CLOSED:
            messaging_session.status = SecureMessagingStatus.CLOSED
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            messaging_session.status = SecureMessagingStatus.COMPLETED
            if not messaging_session.started_at:
                messaging_session.started_at = now_value
            next_steps = messaging_session.step_state if isinstance(messaging_session.step_state, dict) else {}
            next_steps["close_thread"] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            messaging_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key="close_thread",
                is_completed=True,
                payload={"summary": summary},
            )

        messaging_session.save(update_fields=["metadata", "step_state", "status", "started_at", "ended_at", "updated_at"])

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "secure_messaging_session_id": str(messaging_session.id),
                "secure_messaging_status": messaging_session.status,
                "ended_at": messaging_session.ended_at.isoformat() if messaging_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "messaging_session": SecureMessagingSessionSerializer(messaging_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_200_OK)


class ClinicalEngineSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = ClinicalEngineStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_code = str(serializer.validated_data["engine_code"])
        if engine_code not in SUPPORTED_CLINICAL_ENGINE_CODES:
            return Response({"detail": "Unsupported clinical engine code."}, status=status.HTTP_400_BAD_REQUEST)

        engine_session = _get_engine_session_by_code(workflow, engine_code)
        if not engine_session:
            return Response({"detail": "Clinical engine is not mapped to this workflow."}, status=status.HTTP_404_NOT_FOUND)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        created = False
        clinical_session = (
            ClinicalEngineSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session", "institution", "service", "user")
            .filter(workflow_session=workflow, engine_code=engine_code)
            .first()
        )
        step_keys = _list_engine_step_keys(engine_session)
        now_value = timezone.now()

        if not clinical_session:
            created = True
            clinical_session = ClinicalEngineSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                engine_code=engine_code,
                status=ClinicalEngineSessionStatus.IN_PROGRESS if payload_data else ClinicalEngineSessionStatus.WAITING,
                step_state=_default_step_state(step_keys),
                payload=payload_data,
                metadata=metadata,
                started_at=now_value if payload_data else None,
            )
            clinical_session.save()
        else:
            if clinical_session.status in {ClinicalEngineSessionStatus.COMPLETED, ClinicalEngineSessionStatus.CANCELLED}:
                return Response({"detail": "This clinical engine session has already ended."}, status=status.HTTP_409_CONFLICT)
            if not isinstance(clinical_session.step_state, dict) or not clinical_session.step_state:
                clinical_session.step_state = _default_step_state(step_keys)
            if payload_data:
                next_payload = clinical_session.payload if isinstance(clinical_session.payload, dict) else {}
                next_payload.update(payload_data)
                clinical_session.payload = next_payload
            if metadata:
                next_meta = clinical_session.metadata if isinstance(clinical_session.metadata, dict) else {}
                next_meta.update(metadata)
                clinical_session.metadata = next_meta
            if clinical_session.status == ClinicalEngineSessionStatus.WAITING and (payload_data or metadata):
                clinical_session.status = ClinicalEngineSessionStatus.IN_PROGRESS
            if clinical_session.status == ClinicalEngineSessionStatus.IN_PROGRESS and not clinical_session.started_at:
                clinical_session.started_at = now_value
            clinical_session.save(
                update_fields=[
                    "step_state",
                    "payload",
                    "metadata",
                    "status",
                    "started_at",
                    "updated_at",
                ]
            )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "clinical_session_id": str(clinical_session.id),
                "clinical_engine_code": engine_code,
                "clinical_status": clinical_session.status,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "clinical_session": ClinicalEngineSessionSerializer(clinical_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class ClinicalEngineSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, clinical_session_id):
        clinical_session = get_object_or_404(
            ClinicalEngineSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=clinical_session_id,
        )
        if not _can_access_workflow_session(request.user, clinical_session.workflow_session):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        payload = {
            "clinical_session": ClinicalEngineSessionSerializer(clinical_session).data,
            "engine_session": EngineSessionSerializer(clinical_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(clinical_session.workflow_session).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class ClinicalEngineSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, clinical_session_id):
        clinical_session = get_object_or_404(
            ClinicalEngineSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=clinical_session_id,
        )
        workflow = clinical_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if clinical_session.status in {ClinicalEngineSessionStatus.COMPLETED, ClinicalEngineSessionStatus.CANCELLED}:
            return Response({"detail": "This clinical engine session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = clinical_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = ClinicalEngineStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step_key = str(serializer.validated_data["step_key"])
        if not _is_step_key_valid(engine_session, step_key):
            return Response({"detail": "Invalid step_key for this clinical engine."}, status=status.HTTP_400_BAD_REQUEST)

        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload_data = serializer.validated_data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}

        now_value = timezone.now()
        next_step_state = clinical_session.step_state if isinstance(clinical_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload_data
        next_step_state[step_key] = current_row
        clinical_session.step_state = next_step_state

        if clinical_session.status == ClinicalEngineSessionStatus.WAITING:
            clinical_session.status = ClinicalEngineSessionStatus.IN_PROGRESS
        if clinical_session.status == ClinicalEngineSessionStatus.IN_PROGRESS and not clinical_session.started_at:
            clinical_session.started_at = now_value

        step_keys = _list_engine_step_keys(engine_session)
        if _are_all_steps_completed(next_step_state, step_keys):
            clinical_session.status = ClinicalEngineSessionStatus.COMPLETED
            clinical_session.ended_at = now_value

        clinical_session.save(update_fields=["step_state", "status", "started_at", "ended_at", "updated_at"])

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"engine_code": clinical_session.engine_code, "clinical_step": payload_data},
            content_position=serializer.validated_data.get("content_position"),
            content_position_provided="content_position" in serializer.validated_data,
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "clinical_session_id": str(clinical_session.id),
                "clinical_engine_code": clinical_session.engine_code,
                "clinical_status": clinical_session.status,
                "last_clinical_step": step_key,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "clinical_session": ClinicalEngineSessionSerializer(clinical_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class ClinicalEngineSessionPayloadView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, clinical_session_id):
        clinical_session = get_object_or_404(
            ClinicalEngineSession.objects.select_related("engine_session", "workflow_session", "institution"),
            id=clinical_session_id,
        )
        workflow = clinical_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if clinical_session.status in {ClinicalEngineSessionStatus.COMPLETED, ClinicalEngineSessionStatus.CANCELLED}:
            return Response({"detail": "This clinical engine session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = ClinicalEnginePayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merge = bool(serializer.validated_data.get("merge", True))

        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        now_value = timezone.now()
        if merge:
            next_payload = clinical_session.payload if isinstance(clinical_session.payload, dict) else {}
            next_payload.update(payload_data)
            clinical_session.payload = next_payload
            next_meta = clinical_session.metadata if isinstance(clinical_session.metadata, dict) else {}
            next_meta.update(metadata)
            clinical_session.metadata = next_meta
        else:
            clinical_session.payload = payload_data
            clinical_session.metadata = metadata

        if clinical_session.status == ClinicalEngineSessionStatus.WAITING and (payload_data or metadata):
            clinical_session.status = ClinicalEngineSessionStatus.IN_PROGRESS
            clinical_session.started_at = clinical_session.started_at or now_value

        clinical_session.save(update_fields=["payload", "metadata", "status", "started_at", "updated_at"])

        payload = {
            "clinical_session": ClinicalEngineSessionSerializer(clinical_session).data,
            "engine_session": EngineSessionSerializer(clinical_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class ClinicalEngineSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, clinical_session_id):
        clinical_session = get_object_or_404(
            ClinicalEngineSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=clinical_session_id,
        )
        workflow = clinical_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if clinical_session.status in {ClinicalEngineSessionStatus.COMPLETED, ClinicalEngineSessionStatus.CANCELLED}:
            return Response({"detail": "This clinical engine session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = ClinicalEngineEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or ClinicalEngineSessionStatus.COMPLETED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})

        now_value = timezone.now()
        next_meta = clinical_session.metadata if isinstance(clinical_session.metadata, dict) else {}
        if summary:
            next_meta["summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        clinical_session.metadata = next_meta
        clinical_session.ended_at = now_value

        engine_session = clinical_session.engine_session
        if status_value == ClinicalEngineSessionStatus.CANCELLED:
            clinical_session.status = ClinicalEngineSessionStatus.CANCELLED
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            clinical_session.status = ClinicalEngineSessionStatus.COMPLETED
            if not clinical_session.started_at:
                clinical_session.started_at = now_value
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "step_1"
            next_steps = clinical_session.step_state if isinstance(clinical_session.step_state, dict) else {}
            next_steps[final_step_key] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            clinical_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"summary": summary, "engine_code": clinical_session.engine_code},
            )

        clinical_session.save(update_fields=["metadata", "step_state", "status", "started_at", "ended_at", "updated_at"])

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "clinical_session_id": str(clinical_session.id),
                "clinical_engine_code": clinical_session.engine_code,
                "clinical_status": clinical_session.status,
                "ended_at": clinical_session.ended_at.isoformat() if clinical_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "clinical_session": ClinicalEngineSessionSerializer(clinical_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class AdmissionBedSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = AdmissionBedStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_session = _get_admission_engine_session(workflow)
        if not engine_session:
            return Response({"detail": "Admission engine is not mapped to this workflow."}, status=status.HTTP_404_NOT_FOUND)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        created = False
        admission_session = (
            AdmissionBedSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session", "institution", "service", "user")
            .filter(workflow_session=workflow)
            .first()
        )
        step_keys = _list_engine_step_keys(engine_session) or list(ADMISSION_BED_STEP_ORDER)
        now_value = timezone.now()

        if not admission_session:
            created = True
            admission_session = AdmissionBedSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                status=AdmissionBedStatus.INTAKE if payload_data else AdmissionBedStatus.WAITING,
                ward_name=str(payload_data.get("ward_name") or "").strip(),
                bed_code=str(payload_data.get("bed_code") or "").strip(),
                triage_level=str(payload_data.get("triage_level") or "").strip(),
                requires_isolation=bool(payload_data.get("requires_isolation", False)),
                requires_icu=bool(payload_data.get("requires_icu", False)),
                step_state=_default_step_state(step_keys),
                payload=payload_data,
                metadata=metadata,
                started_at=now_value if payload_data else None,
            )
            admission_session.save()
        else:
            if admission_session.status in {AdmissionBedStatus.COMPLETED, AdmissionBedStatus.CANCELLED}:
                return Response({"detail": "This admission session has already ended."}, status=status.HTTP_409_CONFLICT)
            if not isinstance(admission_session.step_state, dict) or not admission_session.step_state:
                admission_session.step_state = _default_step_state(step_keys)

            next_payload = admission_session.payload if isinstance(admission_session.payload, dict) else {}
            if payload_data:
                next_payload.update(payload_data)
            admission_session.payload = next_payload

            next_meta = admission_session.metadata if isinstance(admission_session.metadata, dict) else {}
            if metadata:
                next_meta.update(metadata)
            admission_session.metadata = next_meta

            if "ward_name" in payload_data:
                admission_session.ward_name = str(payload_data.get("ward_name") or "").strip()
            if "bed_code" in payload_data:
                admission_session.bed_code = str(payload_data.get("bed_code") or "").strip()
            if "triage_level" in payload_data:
                admission_session.triage_level = str(payload_data.get("triage_level") or "").strip()
            if "requires_isolation" in payload_data:
                admission_session.requires_isolation = bool(payload_data.get("requires_isolation"))
            if "requires_icu" in payload_data:
                admission_session.requires_icu = bool(payload_data.get("requires_icu"))

            if admission_session.status == AdmissionBedStatus.WAITING and (payload_data or metadata):
                admission_session.status = AdmissionBedStatus.INTAKE
            if admission_session.status == AdmissionBedStatus.INTAKE and not admission_session.started_at:
                admission_session.started_at = now_value
            admission_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "admission_session_id": str(admission_session.id),
                "admission_status": admission_session.status,
                "ward_name": admission_session.ward_name,
                "bed_code": admission_session.bed_code,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "admission_session": AdmissionBedSessionSerializer(admission_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class AdmissionBedSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, admission_session_id):
        admission_session = get_object_or_404(
            AdmissionBedSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=admission_session_id,
        )
        if not _can_access_workflow_session(request.user, admission_session.workflow_session):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        payload = {
            "admission_session": AdmissionBedSessionSerializer(admission_session).data,
            "engine_session": EngineSessionSerializer(admission_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(admission_session.workflow_session).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class AdmissionBedSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, admission_session_id):
        admission_session = get_object_or_404(
            AdmissionBedSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=admission_session_id,
        )
        workflow = admission_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if admission_session.status in {AdmissionBedStatus.COMPLETED, AdmissionBedStatus.CANCELLED}:
            return Response({"detail": "This admission session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = admission_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = AdmissionBedStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step_key = str(serializer.validated_data["step_key"])
        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload_data = serializer.validated_data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}

        now_value = timezone.now()
        next_step_state = admission_session.step_state if isinstance(admission_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload_data
        next_step_state[step_key] = current_row
        admission_session.step_state = next_step_state

        next_payload = admission_session.payload if isinstance(admission_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        admission_session.payload = next_payload

        if step_key == "admission_reason" and is_completed:
            admission_session.status = AdmissionBedStatus.INTAKE
            admission_session.started_at = admission_session.started_at or now_value
        if step_key == "bed_assignment" and is_completed:
            admission_session.status = AdmissionBedStatus.BED_ASSIGNED
            admission_session.assigned_at = now_value
            if "ward_name" in payload_data:
                admission_session.ward_name = str(payload_data.get("ward_name") or "").strip()
            if "bed_code" in payload_data:
                admission_session.bed_code = str(payload_data.get("bed_code") or "").strip()
        if step_key == "admission_confirmation" and is_completed:
            admission_session.status = AdmissionBedStatus.ADMITTED

        if "triage_level" in payload_data:
            admission_session.triage_level = str(payload_data.get("triage_level") or "").strip()
        if "requires_isolation" in payload_data:
            admission_session.requires_isolation = bool(payload_data.get("requires_isolation"))
        if "requires_icu" in payload_data:
            admission_session.requires_icu = bool(payload_data.get("requires_icu"))

        step_keys = _list_engine_step_keys(engine_session)
        if _are_all_steps_completed(next_step_state, step_keys):
            admission_session.status = AdmissionBedStatus.ADMITTED

        admission_session.save()

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"admission_step": payload_data},
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "admission_session_id": str(admission_session.id),
                "admission_status": admission_session.status,
                "ward_name": admission_session.ward_name,
                "bed_code": admission_session.bed_code,
                "last_admission_step": step_key,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "admission_session": AdmissionBedSessionSerializer(admission_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class AdmissionBedSessionPayloadView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, admission_session_id):
        admission_session = get_object_or_404(
            AdmissionBedSession.objects.select_related("engine_session", "workflow_session", "institution"),
            id=admission_session_id,
        )
        workflow = admission_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if admission_session.status in {AdmissionBedStatus.COMPLETED, AdmissionBedStatus.CANCELLED}:
            return Response({"detail": "This admission session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = AdmissionBedPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merge = bool(serializer.validated_data.get("merge", True))
        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        now_value = timezone.now()
        if merge:
            next_payload = admission_session.payload if isinstance(admission_session.payload, dict) else {}
            next_payload.update(payload_data)
            admission_session.payload = next_payload
            next_meta = admission_session.metadata if isinstance(admission_session.metadata, dict) else {}
            next_meta.update(metadata)
            admission_session.metadata = next_meta
        else:
            admission_session.payload = payload_data
            admission_session.metadata = metadata

        if "ward_name" in payload_data:
            admission_session.ward_name = str(payload_data.get("ward_name") or "").strip()
        if "bed_code" in payload_data:
            admission_session.bed_code = str(payload_data.get("bed_code") or "").strip()
        if "triage_level" in payload_data:
            admission_session.triage_level = str(payload_data.get("triage_level") or "").strip()
        if "requires_isolation" in payload_data:
            admission_session.requires_isolation = bool(payload_data.get("requires_isolation"))
        if "requires_icu" in payload_data:
            admission_session.requires_icu = bool(payload_data.get("requires_icu"))

        if admission_session.status == AdmissionBedStatus.WAITING and (payload_data or metadata):
            admission_session.status = AdmissionBedStatus.INTAKE
            admission_session.started_at = admission_session.started_at or now_value

        admission_session.save()

        payload = {
            "admission_session": AdmissionBedSessionSerializer(admission_session).data,
            "engine_session": EngineSessionSerializer(admission_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class AdmissionBedSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, admission_session_id):
        admission_session = get_object_or_404(
            AdmissionBedSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=admission_session_id,
        )
        workflow = admission_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if admission_session.status in {AdmissionBedStatus.COMPLETED, AdmissionBedStatus.CANCELLED}:
            return Response({"detail": "This admission session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = AdmissionBedEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or AdmissionBedStatus.COMPLETED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})

        now_value = timezone.now()
        next_meta = admission_session.metadata if isinstance(admission_session.metadata, dict) else {}
        if summary:
            next_meta["discharge_summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        admission_session.metadata = next_meta
        admission_session.ended_at = now_value

        engine_session = admission_session.engine_session
        if status_value == AdmissionBedStatus.CANCELLED:
            admission_session.status = AdmissionBedStatus.CANCELLED
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            admission_session.status = AdmissionBedStatus.COMPLETED
            admission_session.started_at = admission_session.started_at or now_value
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "admission_confirmation"
            next_steps = admission_session.step_state if isinstance(admission_session.step_state, dict) else {}
            next_steps[final_step_key] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            admission_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"summary": summary},
            )

        admission_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "admission_session_id": str(admission_session.id),
                "admission_status": admission_session.status,
                "ended_at": admission_session.ended_at.isoformat() if admission_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "admission_session": AdmissionBedSessionSerializer(admission_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 10},
        }
        return Response(payload, status=status.HTTP_200_OK)


class EmergencyDispatchSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = EmergencyDispatchStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_session = _get_emergency_engine_session(workflow)
        if not engine_session:
            return Response({"detail": "Emergency engine is not mapped to this workflow."}, status=status.HTTP_404_NOT_FOUND)
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}
        dispatch_code = str(serializer.validated_data.get("dispatch_code") or "").strip() or f"kis-emg-{uuid4().hex[:10]}"

        created = False
        emergency_session = (
            EmergencyDispatchSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session", "institution", "service", "user")
            .filter(workflow_session=workflow)
            .first()
        )
        step_keys = _list_engine_step_keys(engine_session) or list(EMERGENCY_DISPATCH_STEP_ORDER)
        now_value = timezone.now()

        if not emergency_session:
            created = True
            emergency_session = EmergencyDispatchSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                dispatch_code=dispatch_code,
                status=EmergencyDispatchStatus.TRIAGING if payload_data else EmergencyDispatchStatus.WAITING,
                triage_level=str(payload_data.get("triage_level") or "").strip(),
                location_latitude=payload_data.get("latitude"),
                location_longitude=payload_data.get("longitude"),
                ambulance_reference=str(payload_data.get("ambulance_reference") or "").strip(),
                current_eta_minutes=payload_data.get("eta_minutes"),
                step_state=_default_step_state(step_keys),
                payload=payload_data,
                tracking_events=[],
                metadata=metadata,
                started_at=now_value if payload_data else None,
            )
            emergency_session.tracking_events = _append_emergency_tracking_event(
                emergency_session,
                event_type="session_started",
                payload={"dispatch_code": dispatch_code},
            )
            emergency_session.save()
        else:
            if emergency_session.status in {EmergencyDispatchStatus.RESOLVED, EmergencyDispatchStatus.CANCELLED}:
                return Response({"detail": "This emergency dispatch session has already ended."}, status=status.HTTP_409_CONFLICT)
            if not isinstance(emergency_session.step_state, dict) or not emergency_session.step_state:
                emergency_session.step_state = _default_step_state(step_keys)

            next_payload = emergency_session.payload if isinstance(emergency_session.payload, dict) else {}
            if payload_data:
                next_payload.update(payload_data)
            emergency_session.payload = next_payload

            next_meta = emergency_session.metadata if isinstance(emergency_session.metadata, dict) else {}
            if metadata:
                next_meta.update(metadata)
            emergency_session.metadata = next_meta

            if "latitude" in payload_data:
                emergency_session.location_latitude = payload_data.get("latitude")
            if "longitude" in payload_data:
                emergency_session.location_longitude = payload_data.get("longitude")
            if "eta_minutes" in payload_data:
                emergency_session.current_eta_minutes = payload_data.get("eta_minutes")
            if "triage_level" in payload_data:
                emergency_session.triage_level = str(payload_data.get("triage_level") or "").strip()
            if "ambulance_reference" in payload_data:
                emergency_session.ambulance_reference = str(payload_data.get("ambulance_reference") or "").strip()

            if emergency_session.status == EmergencyDispatchStatus.WAITING and (payload_data or metadata):
                emergency_session.status = EmergencyDispatchStatus.TRIAGING
            if emergency_session.status in {EmergencyDispatchStatus.WAITING, EmergencyDispatchStatus.TRIAGING}:
                emergency_session.started_at = emergency_session.started_at or now_value

            emergency_session.tracking_events = _append_emergency_tracking_event(
                emergency_session,
                event_type="session_refresh",
                payload={"updated": bool(payload_data or metadata)},
            )
            emergency_session.last_tracking_at = now_value
            emergency_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "emergency_session_id": str(emergency_session.id),
                "emergency_status": emergency_session.status,
                "dispatch_code": emergency_session.dispatch_code,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "emergency_session": EmergencyDispatchSessionSerializer(emergency_session).data,
            "tracking_events": emergency_session.tracking_events[-50:] if isinstance(emergency_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class EmergencyDispatchSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, emergency_session_id):
        emergency_session = get_object_or_404(
            EmergencyDispatchSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=emergency_session_id,
        )
        if not _can_access_workflow_session(request.user, emergency_session.workflow_session):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(200, limit))
        tracking_rows = emergency_session.tracking_events if isinstance(emergency_session.tracking_events, list) else []

        payload = {
            "emergency_session": EmergencyDispatchSessionSerializer(emergency_session).data,
            "tracking_events": tracking_rows[-limit:],
            "engine_session": EngineSessionSerializer(emergency_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(emergency_session.workflow_session).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_200_OK)


class EmergencyDispatchSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, emergency_session_id):
        emergency_session = get_object_or_404(
            EmergencyDispatchSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=emergency_session_id,
        )
        workflow = emergency_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if emergency_session.status in {EmergencyDispatchStatus.RESOLVED, EmergencyDispatchStatus.CANCELLED}:
            return Response({"detail": "This emergency session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = emergency_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = EmergencyDispatchStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step_key = str(serializer.validated_data["step_key"])
        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload_data = serializer.validated_data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}

        now_value = timezone.now()
        next_step_state = emergency_session.step_state if isinstance(emergency_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload_data
        next_step_state[step_key] = current_row
        emergency_session.step_state = next_step_state

        next_payload = emergency_session.payload if isinstance(emergency_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        emergency_session.payload = next_payload

        if "latitude" in payload_data:
            emergency_session.location_latitude = payload_data.get("latitude")
        if "longitude" in payload_data:
            emergency_session.location_longitude = payload_data.get("longitude")
        if "eta_minutes" in payload_data:
            emergency_session.current_eta_minutes = payload_data.get("eta_minutes")
        if "triage_level" in payload_data:
            emergency_session.triage_level = str(payload_data.get("triage_level") or "").strip()
        if "ambulance_reference" in payload_data:
            emergency_session.ambulance_reference = str(payload_data.get("ambulance_reference") or "").strip()

        if step_key == "capture_location" and is_completed:
            emergency_session.status = EmergencyDispatchStatus.TRIAGING
            emergency_session.started_at = emergency_session.started_at or now_value
        elif step_key == "triage_form" and is_completed:
            emergency_session.status = EmergencyDispatchStatus.TRIAGING
            emergency_session.started_at = emergency_session.started_at or now_value
        elif step_key == "dispatch_ambulance" and is_completed:
            emergency_session.status = EmergencyDispatchStatus.DISPATCHED
            emergency_session.dispatched_at = emergency_session.dispatched_at or now_value
        elif step_key == "track_response" and is_completed:
            if bool(payload_data.get("arrived", False)):
                emergency_session.status = EmergencyDispatchStatus.ARRIVED
                emergency_session.arrived_at = emergency_session.arrived_at or now_value
            else:
                emergency_session.status = EmergencyDispatchStatus.IN_TRANSIT

        step_keys = _list_engine_step_keys(engine_session)
        if _are_all_steps_completed(next_step_state, step_keys) and emergency_session.status not in {
            EmergencyDispatchStatus.ARRIVED,
            EmergencyDispatchStatus.RESOLVED,
            EmergencyDispatchStatus.CANCELLED,
        }:
            emergency_session.status = EmergencyDispatchStatus.ARRIVED

        emergency_session.last_tracking_at = now_value
        emergency_session.tracking_events = _append_emergency_tracking_event(
            emergency_session,
            event_type="step_update",
            payload={"step_key": step_key, "is_completed": is_completed, "payload": payload_data},
        )
        emergency_session.save()

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"emergency_step": payload_data},
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "emergency_session_id": str(emergency_session.id),
                "emergency_status": emergency_session.status,
                "dispatch_code": emergency_session.dispatch_code,
                "last_emergency_step": step_key,
                "last_tracking_at": emergency_session.last_tracking_at.isoformat() if emergency_session.last_tracking_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "emergency_session": EmergencyDispatchSessionSerializer(emergency_session).data,
            "tracking_events": emergency_session.tracking_events[-50:] if isinstance(emergency_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_200_OK)


class EmergencyDispatchSessionPayloadView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, emergency_session_id):
        emergency_session = get_object_or_404(
            EmergencyDispatchSession.objects.select_related("engine_session", "workflow_session", "institution"),
            id=emergency_session_id,
        )
        workflow = emergency_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if emergency_session.status in {EmergencyDispatchStatus.RESOLVED, EmergencyDispatchStatus.CANCELLED}:
            return Response({"detail": "This emergency session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = EmergencyDispatchPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merge = bool(serializer.validated_data.get("merge", True))
        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        now_value = timezone.now()
        if merge:
            next_payload = emergency_session.payload if isinstance(emergency_session.payload, dict) else {}
            next_payload.update(payload_data)
            emergency_session.payload = next_payload
            next_meta = emergency_session.metadata if isinstance(emergency_session.metadata, dict) else {}
            next_meta.update(metadata)
            emergency_session.metadata = next_meta
        else:
            emergency_session.payload = payload_data
            emergency_session.metadata = metadata

        if "latitude" in payload_data:
            emergency_session.location_latitude = payload_data.get("latitude")
        if "longitude" in payload_data:
            emergency_session.location_longitude = payload_data.get("longitude")
        if "eta_minutes" in payload_data:
            emergency_session.current_eta_minutes = payload_data.get("eta_minutes")
        if "triage_level" in payload_data:
            emergency_session.triage_level = str(payload_data.get("triage_level") or "").strip()
        if "ambulance_reference" in payload_data:
            emergency_session.ambulance_reference = str(payload_data.get("ambulance_reference") or "").strip()

        if emergency_session.status == EmergencyDispatchStatus.WAITING and (payload_data or metadata):
            emergency_session.status = EmergencyDispatchStatus.TRIAGING
            emergency_session.started_at = emergency_session.started_at or now_value

        emergency_session.last_tracking_at = now_value
        emergency_session.tracking_events = _append_emergency_tracking_event(
            emergency_session,
            event_type="payload_update",
            payload={"payload": payload_data, "metadata": metadata},
        )
        emergency_session.save()

        payload = {
            "emergency_session": EmergencyDispatchSessionSerializer(emergency_session).data,
            "tracking_events": emergency_session.tracking_events[-50:] if isinstance(emergency_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(emergency_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_200_OK)


class EmergencyDispatchTrackingView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, emergency_session_id):
        emergency_session = get_object_or_404(
            EmergencyDispatchSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=emergency_session_id,
        )
        workflow = emergency_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if emergency_session.status in {EmergencyDispatchStatus.RESOLVED, EmergencyDispatchStatus.CANCELLED}:
            return Response({"detail": "This emergency session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = EmergencyDispatchTrackingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now_value = timezone.now()
        if "latitude" in data:
            emergency_session.location_latitude = data.get("latitude")
        if "longitude" in data:
            emergency_session.location_longitude = data.get("longitude")
        if "eta_minutes" in data:
            emergency_session.current_eta_minutes = data.get("eta_minutes")
        if "ambulance_reference" in data:
            emergency_session.ambulance_reference = str(data.get("ambulance_reference") or "").strip()

        status_value = str(data.get("status") or "").strip()
        if status_value:
            emergency_session.status = status_value
            if status_value == EmergencyDispatchStatus.DISPATCHED:
                emergency_session.dispatched_at = emergency_session.dispatched_at or now_value
            if status_value == EmergencyDispatchStatus.IN_TRANSIT:
                emergency_session.dispatched_at = emergency_session.dispatched_at or now_value
            if status_value == EmergencyDispatchStatus.ARRIVED:
                emergency_session.arrived_at = emergency_session.arrived_at or now_value
            if status_value == EmergencyDispatchStatus.RESOLVED:
                emergency_session.resolved_at = emergency_session.resolved_at or now_value
                emergency_session.ended_at = emergency_session.ended_at or now_value

        payload_data = data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        next_payload = emergency_session.payload if isinstance(emergency_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        emergency_session.payload = next_payload

        emergency_session.last_tracking_at = now_value
        emergency_session.tracking_events = _append_emergency_tracking_event(
            emergency_session,
            event_type="tracking_ping",
            payload={
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "eta_minutes": data.get("eta_minutes"),
                "status": status_value,
                "note": str(data.get("note") or "").strip(),
                "payload": payload_data,
            },
        )
        emergency_session.save()

        engine_session = emergency_session.engine_session
        if status_value == EmergencyDispatchStatus.RESOLVED:
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "track_response"
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"source": "tracking_ping", "status": "resolved"},
            )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "emergency_session_id": str(emergency_session.id),
                "emergency_status": emergency_session.status,
                "dispatch_code": emergency_session.dispatch_code,
                "last_tracking_at": emergency_session.last_tracking_at.isoformat() if emergency_session.last_tracking_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "emergency_session": EmergencyDispatchSessionSerializer(emergency_session).data,
            "tracking_events": emergency_session.tracking_events[-50:] if isinstance(emergency_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_200_OK)


class EmergencyDispatchSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, emergency_session_id):
        emergency_session = get_object_or_404(
            EmergencyDispatchSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=emergency_session_id,
        )
        workflow = emergency_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if emergency_session.status in {EmergencyDispatchStatus.RESOLVED, EmergencyDispatchStatus.CANCELLED}:
            return Response({"detail": "This emergency session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = EmergencyDispatchEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or EmergencyDispatchStatus.RESOLVED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})

        now_value = timezone.now()
        next_meta = emergency_session.metadata if isinstance(emergency_session.metadata, dict) else {}
        if summary:
            next_meta["resolution_summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        emergency_session.metadata = next_meta
        emergency_session.ended_at = now_value

        engine_session = emergency_session.engine_session
        if status_value == EmergencyDispatchStatus.CANCELLED:
            emergency_session.status = EmergencyDispatchStatus.CANCELLED
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            emergency_session.status = EmergencyDispatchStatus.RESOLVED
            emergency_session.resolved_at = emergency_session.resolved_at or now_value
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "track_response"
            next_steps = emergency_session.step_state if isinstance(emergency_session.step_state, dict) else {}
            next_steps[final_step_key] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            emergency_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"summary": summary},
            )

        emergency_session.last_tracking_at = now_value
        emergency_session.tracking_events = _append_emergency_tracking_event(
            emergency_session,
            event_type="session_end",
            payload={"status": emergency_session.status, "summary": summary},
        )
        emergency_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "emergency_session_id": str(emergency_session.id),
                "emergency_status": emergency_session.status,
                "dispatch_code": emergency_session.dispatch_code,
                "ended_at": emergency_session.ended_at.isoformat() if emergency_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "emergency_session": EmergencyDispatchSessionSerializer(emergency_session).data,
            "tracking_events": emergency_session.tracking_events[-50:] if isinstance(emergency_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 5},
        }
        return Response(payload, status=status.HTTP_200_OK)


class PharmacyFulfillmentSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = PharmacyFulfillmentStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_session = _get_pharmacy_engine_session(workflow)
        if not engine_session:
            return Response(
                {"detail": "Pharmacy fulfillment engine is not mapped to this workflow."},
                status=status.HTTP_404_NOT_FOUND,
            )
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}
        cart_items = serializer.validated_data.get("cart_items", [])
        if not isinstance(cart_items, list):
            cart_items = []
        cart_items_provided = "cart_items" in request.data

        created = False
        pharmacy_session = (
            PharmacyFulfillmentSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session", "institution", "service", "user")
            .filter(workflow_session=workflow)
            .first()
        )
        step_keys = _list_engine_step_keys(engine_session) or list(PHARMACY_FULFILLMENT_STEP_ORDER)
        now_value = timezone.now()

        if not pharmacy_session:
            created = True
            pharmacy_session = PharmacyFulfillmentSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                status=PharmacyFulfillmentStatus.VERIFYING if (payload_data or cart_items) else PharmacyFulfillmentStatus.WAITING,
                cart_items=cart_items,
                delivery_mode=str(payload_data.get("delivery_mode") or "").strip(),
                payment_reference=str(payload_data.get("payment_reference") or "").strip(),
                fulfillment_reference=str(payload_data.get("fulfillment_reference") or "").strip(),
                current_eta_minutes=payload_data.get("eta_minutes"),
                step_state=_default_step_state(step_keys),
                payload=payload_data,
                metadata=metadata,
                tracking_events=[],
                started_at=now_value if (payload_data or cart_items) else None,
            )
            pharmacy_session.tracking_events = _append_tracking_event(
                pharmacy_session.tracking_events,
                event_type="session_started",
                status_value=pharmacy_session.status,
                payload={"cart_items_count": len(cart_items)},
            )
            pharmacy_session.last_tracking_at = now_value
            pharmacy_session.save()
        else:
            if pharmacy_session.status in {PharmacyFulfillmentStatus.COMPLETED, PharmacyFulfillmentStatus.CANCELLED}:
                return Response({"detail": "This pharmacy session has already ended."}, status=status.HTTP_409_CONFLICT)
            if not isinstance(pharmacy_session.step_state, dict) or not pharmacy_session.step_state:
                pharmacy_session.step_state = _default_step_state(step_keys)

            next_payload = pharmacy_session.payload if isinstance(pharmacy_session.payload, dict) else {}
            if payload_data:
                next_payload.update(payload_data)
            pharmacy_session.payload = next_payload

            next_meta = pharmacy_session.metadata if isinstance(pharmacy_session.metadata, dict) else {}
            if metadata:
                next_meta.update(metadata)
            pharmacy_session.metadata = next_meta

            if cart_items_provided:
                pharmacy_session.cart_items = cart_items
            if "delivery_mode" in payload_data:
                pharmacy_session.delivery_mode = str(payload_data.get("delivery_mode") or "").strip()
            if "payment_reference" in payload_data:
                pharmacy_session.payment_reference = str(payload_data.get("payment_reference") or "").strip()
            if "fulfillment_reference" in payload_data:
                pharmacy_session.fulfillment_reference = str(payload_data.get("fulfillment_reference") or "").strip()
            if "eta_minutes" in payload_data:
                pharmacy_session.current_eta_minutes = payload_data.get("eta_minutes")

            if pharmacy_session.status == PharmacyFulfillmentStatus.WAITING and (payload_data or metadata or cart_items_provided):
                pharmacy_session.status = PharmacyFulfillmentStatus.VERIFYING
            if pharmacy_session.status == PharmacyFulfillmentStatus.WAITING and cart_items:
                pharmacy_session.status = PharmacyFulfillmentStatus.VERIFYING
            if pharmacy_session.status == PharmacyFulfillmentStatus.VERIFYING:
                pharmacy_session.started_at = pharmacy_session.started_at or now_value

            pharmacy_session.tracking_events = _append_tracking_event(
                pharmacy_session.tracking_events,
                event_type="session_refresh",
                status_value=pharmacy_session.status,
                payload={"updated": bool(payload_data or metadata or cart_items_provided)},
            )
            pharmacy_session.last_tracking_at = now_value
            pharmacy_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "pharmacy_session_id": str(pharmacy_session.id),
                "pharmacy_status": pharmacy_session.status,
                "delivery_mode": pharmacy_session.delivery_mode,
                "payment_reference": pharmacy_session.payment_reference,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "pharmacy_session": PharmacyFulfillmentSessionSerializer(pharmacy_session).data,
            "tracking_events": pharmacy_session.tracking_events[-50:] if isinstance(pharmacy_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 8},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PharmacyFulfillmentSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, pharmacy_session_id):
        pharmacy_session = get_object_or_404(
            PharmacyFulfillmentSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=pharmacy_session_id,
        )
        if not _can_access_workflow_session(request.user, pharmacy_session.workflow_session):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(200, limit))
        tracking_rows = pharmacy_session.tracking_events if isinstance(pharmacy_session.tracking_events, list) else []

        payload = {
            "pharmacy_session": PharmacyFulfillmentSessionSerializer(pharmacy_session).data,
            "tracking_events": tracking_rows[-limit:],
            "engine_session": EngineSessionSerializer(pharmacy_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(pharmacy_session.workflow_session).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 8},
        }
        return Response(payload, status=status.HTTP_200_OK)


class PharmacyFulfillmentSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, pharmacy_session_id):
        pharmacy_session = get_object_or_404(
            PharmacyFulfillmentSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=pharmacy_session_id,
        )
        workflow = pharmacy_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if pharmacy_session.status in {PharmacyFulfillmentStatus.COMPLETED, PharmacyFulfillmentStatus.CANCELLED}:
            return Response({"detail": "This pharmacy session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = pharmacy_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = PharmacyFulfillmentStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step_key = str(serializer.validated_data["step_key"])
        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload_data = serializer.validated_data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}

        now_value = timezone.now()
        next_step_state = pharmacy_session.step_state if isinstance(pharmacy_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload_data
        next_step_state[step_key] = current_row
        pharmacy_session.step_state = next_step_state

        next_payload = pharmacy_session.payload if isinstance(pharmacy_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        pharmacy_session.payload = next_payload

        cart_items_value = payload_data.get("cart_items")
        if isinstance(cart_items_value, list):
            pharmacy_session.cart_items = cart_items_value
        if "delivery_mode" in payload_data:
            pharmacy_session.delivery_mode = str(payload_data.get("delivery_mode") or "").strip()
        if "payment_reference" in payload_data:
            pharmacy_session.payment_reference = str(payload_data.get("payment_reference") or "").strip()
        if "fulfillment_reference" in payload_data:
            pharmacy_session.fulfillment_reference = str(payload_data.get("fulfillment_reference") or "").strip()
        if "eta_minutes" in payload_data:
            pharmacy_session.current_eta_minutes = payload_data.get("eta_minutes")

        if step_key == "verify_prescription" and is_completed:
            pharmacy_session.status = PharmacyFulfillmentStatus.VERIFYING
            pharmacy_session.started_at = pharmacy_session.started_at or now_value
        elif step_key == "validate_inventory" and is_completed:
            pharmacy_session.status = PharmacyFulfillmentStatus.INVENTORY_CONFIRMED
        elif step_key == "confirm_delivery" and is_completed:
            delivery_mode = str(payload_data.get("delivery_mode") or pharmacy_session.delivery_mode or "").strip().lower()
            if delivery_mode in {"pickup", "collection"}:
                pharmacy_session.status = PharmacyFulfillmentStatus.READY_FOR_COLLECTION
                pharmacy_session.ready_at = pharmacy_session.ready_at or now_value
            else:
                pharmacy_session.status = PharmacyFulfillmentStatus.FULFILLMENT_IN_PROGRESS
        elif step_key == "fulfillment_tracking" and is_completed:
            delivered_flag = bool(payload_data.get("delivered"))
            if delivered_flag:
                pharmacy_session.status = PharmacyFulfillmentStatus.DELIVERED
                pharmacy_session.delivered_at = pharmacy_session.delivered_at or now_value
            else:
                pharmacy_session.status = PharmacyFulfillmentStatus.FULFILLMENT_IN_PROGRESS

        step_keys = _list_engine_step_keys(engine_session)
        if _are_all_steps_completed(next_step_state, step_keys) and pharmacy_session.status not in {
            PharmacyFulfillmentStatus.DELIVERED,
            PharmacyFulfillmentStatus.COMPLETED,
            PharmacyFulfillmentStatus.CANCELLED,
        }:
            pharmacy_session.status = PharmacyFulfillmentStatus.READY_FOR_COLLECTION
            pharmacy_session.ready_at = pharmacy_session.ready_at or now_value

        pharmacy_session.last_tracking_at = now_value
        pharmacy_session.tracking_events = _append_tracking_event(
            pharmacy_session.tracking_events,
            event_type="step_update",
            status_value=pharmacy_session.status,
            payload={"step_key": step_key, "is_completed": is_completed, "payload": payload_data},
        )
        pharmacy_session.save()

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"pharmacy_step": payload_data},
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "pharmacy_session_id": str(pharmacy_session.id),
                "pharmacy_status": pharmacy_session.status,
                "delivery_mode": pharmacy_session.delivery_mode,
                "payment_reference": pharmacy_session.payment_reference,
                "last_pharmacy_step": step_key,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "pharmacy_session": PharmacyFulfillmentSessionSerializer(pharmacy_session).data,
            "tracking_events": pharmacy_session.tracking_events[-50:] if isinstance(pharmacy_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 8},
        }
        return Response(payload, status=status.HTTP_200_OK)


class PharmacyFulfillmentSessionPayloadView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, pharmacy_session_id):
        pharmacy_session = get_object_or_404(
            PharmacyFulfillmentSession.objects.select_related("engine_session", "workflow_session", "institution"),
            id=pharmacy_session_id,
        )
        workflow = pharmacy_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if pharmacy_session.status in {PharmacyFulfillmentStatus.COMPLETED, PharmacyFulfillmentStatus.CANCELLED}:
            return Response({"detail": "This pharmacy session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = PharmacyFulfillmentPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merge = bool(serializer.validated_data.get("merge", True))
        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        now_value = timezone.now()
        if merge:
            next_payload = pharmacy_session.payload if isinstance(pharmacy_session.payload, dict) else {}
            next_payload.update(payload_data)
            pharmacy_session.payload = next_payload
            next_meta = pharmacy_session.metadata if isinstance(pharmacy_session.metadata, dict) else {}
            next_meta.update(metadata)
            pharmacy_session.metadata = next_meta
        else:
            pharmacy_session.payload = payload_data
            pharmacy_session.metadata = metadata

        cart_items_value = payload_data.get("cart_items")
        if isinstance(cart_items_value, list):
            pharmacy_session.cart_items = cart_items_value
        if "delivery_mode" in payload_data:
            pharmacy_session.delivery_mode = str(payload_data.get("delivery_mode") or "").strip()
        if "payment_reference" in payload_data:
            pharmacy_session.payment_reference = str(payload_data.get("payment_reference") or "").strip()
        if "fulfillment_reference" in payload_data:
            pharmacy_session.fulfillment_reference = str(payload_data.get("fulfillment_reference") or "").strip()
        if "eta_minutes" in payload_data:
            pharmacy_session.current_eta_minutes = payload_data.get("eta_minutes")

        if pharmacy_session.status == PharmacyFulfillmentStatus.WAITING and (payload_data or metadata):
            pharmacy_session.status = PharmacyFulfillmentStatus.VERIFYING
            pharmacy_session.started_at = pharmacy_session.started_at or now_value

        pharmacy_session.last_tracking_at = now_value
        pharmacy_session.tracking_events = _append_tracking_event(
            pharmacy_session.tracking_events,
            event_type="payload_update",
            status_value=pharmacy_session.status,
            payload={"payload": payload_data, "metadata": metadata},
        )
        pharmacy_session.save()

        payload = {
            "pharmacy_session": PharmacyFulfillmentSessionSerializer(pharmacy_session).data,
            "tracking_events": pharmacy_session.tracking_events[-50:] if isinstance(pharmacy_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(pharmacy_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 8},
        }
        return Response(payload, status=status.HTTP_200_OK)


class PharmacyFulfillmentTrackingView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, pharmacy_session_id):
        pharmacy_session = get_object_or_404(
            PharmacyFulfillmentSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=pharmacy_session_id,
        )
        workflow = pharmacy_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if pharmacy_session.status in {PharmacyFulfillmentStatus.COMPLETED, PharmacyFulfillmentStatus.CANCELLED}:
            return Response({"detail": "This pharmacy session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = PharmacyFulfillmentTrackingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now_value = timezone.now()
        if "eta_minutes" in data:
            pharmacy_session.current_eta_minutes = data.get("eta_minutes")
        if "delivery_mode" in data:
            pharmacy_session.delivery_mode = str(data.get("delivery_mode") or "").strip()
        if "payment_reference" in data:
            pharmacy_session.payment_reference = str(data.get("payment_reference") or "").strip()
        if "fulfillment_reference" in data:
            pharmacy_session.fulfillment_reference = str(data.get("fulfillment_reference") or "").strip()

        status_value = str(data.get("status") or "").strip()
        if status_value:
            pharmacy_session.status = status_value
            if status_value == PharmacyFulfillmentStatus.READY_FOR_COLLECTION:
                pharmacy_session.ready_at = pharmacy_session.ready_at or now_value
            if status_value == PharmacyFulfillmentStatus.DELIVERED:
                pharmacy_session.delivered_at = pharmacy_session.delivered_at or now_value
            if status_value == PharmacyFulfillmentStatus.COMPLETED:
                pharmacy_session.ended_at = pharmacy_session.ended_at or now_value

        payload_data = data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        next_payload = pharmacy_session.payload if isinstance(pharmacy_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        pharmacy_session.payload = next_payload

        pharmacy_session.last_tracking_at = now_value
        pharmacy_session.tracking_events = _append_tracking_event(
            pharmacy_session.tracking_events,
            event_type="tracking_ping",
            status_value=pharmacy_session.status,
            payload={
                "eta_minutes": data.get("eta_minutes"),
                "status": status_value,
                "note": str(data.get("note") or "").strip(),
                "payload": payload_data,
            },
        )
        pharmacy_session.save()

        engine_session = pharmacy_session.engine_session
        if status_value == PharmacyFulfillmentStatus.COMPLETED:
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "fulfillment_tracking"
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"source": "tracking_ping", "status": "completed"},
            )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "pharmacy_session_id": str(pharmacy_session.id),
                "pharmacy_status": pharmacy_session.status,
                "delivery_mode": pharmacy_session.delivery_mode,
                "payment_reference": pharmacy_session.payment_reference,
                "last_tracking_at": pharmacy_session.last_tracking_at.isoformat() if pharmacy_session.last_tracking_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "pharmacy_session": PharmacyFulfillmentSessionSerializer(pharmacy_session).data,
            "tracking_events": pharmacy_session.tracking_events[-50:] if isinstance(pharmacy_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 8},
        }
        return Response(payload, status=status.HTTP_200_OK)


class PharmacyFulfillmentSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pharmacy_session_id):
        pharmacy_session = get_object_or_404(
            PharmacyFulfillmentSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=pharmacy_session_id,
        )
        workflow = pharmacy_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if pharmacy_session.status in {PharmacyFulfillmentStatus.COMPLETED, PharmacyFulfillmentStatus.CANCELLED}:
            return Response({"detail": "This pharmacy session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = PharmacyFulfillmentEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or PharmacyFulfillmentStatus.COMPLETED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})

        now_value = timezone.now()
        next_meta = pharmacy_session.metadata if isinstance(pharmacy_session.metadata, dict) else {}
        if summary:
            next_meta["closure_summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        pharmacy_session.metadata = next_meta
        pharmacy_session.ended_at = now_value

        engine_session = pharmacy_session.engine_session
        if status_value == PharmacyFulfillmentStatus.CANCELLED:
            pharmacy_session.status = PharmacyFulfillmentStatus.CANCELLED
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            pharmacy_session.status = PharmacyFulfillmentStatus.COMPLETED
            pharmacy_session.delivered_at = pharmacy_session.delivered_at or now_value
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "fulfillment_tracking"
            next_steps = pharmacy_session.step_state if isinstance(pharmacy_session.step_state, dict) else {}
            next_steps[final_step_key] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            pharmacy_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"summary": summary},
            )

        pharmacy_session.last_tracking_at = now_value
        pharmacy_session.tracking_events = _append_tracking_event(
            pharmacy_session.tracking_events,
            event_type="session_end",
            status_value=pharmacy_session.status,
            payload={"status": pharmacy_session.status, "summary": summary},
        )
        pharmacy_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "pharmacy_session_id": str(pharmacy_session.id),
                "pharmacy_status": pharmacy_session.status,
                "delivery_mode": pharmacy_session.delivery_mode,
                "payment_reference": pharmacy_session.payment_reference,
                "ended_at": pharmacy_session.ended_at.isoformat() if pharmacy_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "pharmacy_session": PharmacyFulfillmentSessionSerializer(pharmacy_session).data,
            "tracking_events": pharmacy_session.tracking_events[-50:] if isinstance(pharmacy_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 8},
        }
        return Response(payload, status=status.HTTP_200_OK)


class PaymentBillingSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = PaymentBillingStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_session = _get_billing_engine_session(workflow)
        if not engine_session:
            return Response(
                {"detail": "Payment billing engine is not mapped to this workflow."},
                status=status.HTTP_404_NOT_FOUND,
            )
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        total_amount_micro_value = serializer.validated_data.get("total_amount_micro")
        total_amount_kisc_value = serializer.validated_data.get("total_amount_kisc")
        if total_amount_kisc_value not in (None, ""):
            total_amount_micro_value = _kisc_to_micro(total_amount_kisc_value, allow_empty=True)
        if total_amount_micro_value is None:
            total_amount_micro_value = int(engine_session.engine_map.cost_micro or 0)
        total_amount_micro = max(0, int(total_amount_micro_value or 0))

        insurance_coverage_micro_value = serializer.validated_data.get("insurance_coverage_micro")
        insurance_coverage_kisc_value = serializer.validated_data.get("insurance_coverage_kisc")
        if insurance_coverage_kisc_value not in (None, ""):
            insurance_coverage_micro_value = _kisc_to_micro(insurance_coverage_kisc_value, allow_empty=True)
        insurance_coverage_micro = max(0, int(insurance_coverage_micro_value or 0))

        payable_amount_micro = serializer.validated_data.get("payable_amount_micro")
        payable_amount_kisc = serializer.validated_data.get("payable_amount_kisc")
        if payable_amount_kisc not in (None, ""):
            payable_amount_micro = _kisc_to_micro(payable_amount_kisc, allow_empty=True)
        if payable_amount_micro is None:
            payable_amount_micro = max(0, total_amount_micro - insurance_coverage_micro)
        else:
            payable_amount_micro = max(0, int(payable_amount_micro))
        requested_provider = str(serializer.validated_data.get("payment_provider") or _health_default_payment_provider()).strip().lower()
        if _is_legacy_health_wallet_provider(requested_provider) and not _health_wallet_checkout_enabled():
            return Response(
                {
                    "detail": "Health wallet/KIS Coin checkout is disabled. Use USD checkout with Flutterwave or another configured payment provider.",
                    "code": "legacy_health_wallet_checkout_disabled",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        payment_provider = KIS_WALLET_PROVIDER if _is_legacy_health_wallet_provider(requested_provider) else (requested_provider or _health_default_payment_provider())
        metadata["currency"] = "KISC" if payment_provider == KIS_WALLET_PROVIDER else "USD"
        metadata["payment_status"] = "not_required" if payable_amount_micro <= 0 else "pending"
        metadata["payment_required"] = bool(payable_amount_micro > 0)
        if payment_provider != KIS_WALLET_PROVIDER:
            metadata["payment_provider"] = payment_provider

        billing_session = (
            PaymentBillingSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session")
            .filter(workflow_session=workflow)
            .first()
        )
        created = False
        now_value = timezone.now()
        if not billing_session:
            created = True
            billing_session = PaymentBillingSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                status=PaymentBillingStatus.WAITING,
                total_amount_micro=total_amount_micro,
                insurance_coverage_micro=insurance_coverage_micro,
                payable_amount_micro=payable_amount_micro,
                amount_paid_micro=0,
                payment_provider=payment_provider,
                step_state=_default_step_state(list(PAYMENT_BILLING_STEP_ORDER)),
                payload=payload_data,
                metadata=metadata,
                started_at=now_value,
            )
            billing_session.save()
        else:
            if billing_session.status in {
                PaymentBillingStatus.COMPLETED,
                PaymentBillingStatus.CANCELLED,
                PaymentBillingStatus.FAILED,
            }:
                return Response({"detail": "This billing session has already ended."}, status=status.HTTP_409_CONFLICT)
            if total_amount_micro > 0:
                billing_session.total_amount_micro = total_amount_micro
            billing_session.insurance_coverage_micro = insurance_coverage_micro
            billing_session.payable_amount_micro = payable_amount_micro
            billing_session.payment_provider = payment_provider
            next_payload = billing_session.payload if isinstance(billing_session.payload, dict) else {}
            next_payload.update(payload_data)
            billing_session.payload = next_payload
            next_meta = billing_session.metadata if isinstance(billing_session.metadata, dict) else {}
            next_meta.update(metadata)
            billing_session.metadata = next_meta
            if not billing_session.started_at:
                billing_session.started_at = now_value
            billing_session.save()

        if payment_provider != KIS_WALLET_PROVIDER and int(billing_session.payable_amount_micro or 0) > 0:
            intent = create_direct_payment_intent(
                user=request.user,
                target_type="health_billing_session",
                target_id=billing_session.id,
                provider=payment_provider,
                metadata={
                    "source": "health_billing_session",
                    "workflow_session_id": str(workflow.id),
                    "engine_session_id": str(engine_session.id),
                    "institution_id": str(workflow.institution_id),
                    "service_id": str(workflow.service_id),
                },
            )
            billing_session.payment_reference = intent.tx_ref
            billing_session.metadata = {
                **(billing_session.metadata if isinstance(billing_session.metadata, dict) else {}),
                "payment_reference": intent.tx_ref,
                "direct_payment_intent_id": str(intent.id),
                "payment_url": intent.payment_url,
            }
            billing_session.save(update_fields=["payment_reference", "metadata", "updated_at"])

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "billing_session_id": str(billing_session.id),
                "billing_status": billing_session.status,
                "payable_amount_micro": int(billing_session.payable_amount_micro or 0),
                "payable_amount_kisc": _micro_to_kisc_text(billing_session.payable_amount_micro or 0),
                "payable_amount_usd_label": f"${(Decimal(_micro_to_cents(billing_session.payable_amount_micro or 0)) / Decimal('100')).quantize(Decimal('0.01'))}",
                "payment_provider": billing_session.payment_provider or payment_provider,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "billing_session": PaymentBillingSessionSerializer(billing_session).data,
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 8},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PaymentBillingSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, billing_session_id):
        billing_session = get_object_or_404(
            PaymentBillingSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=billing_session_id,
        )
        workflow = billing_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        access_error = _engine_access_error_response(workflow, billing_session.engine_session)
        if access_error:
            return access_error

        return Response(
            {
                "billing_session": PaymentBillingSessionSerializer(billing_session).data,
                "engine_session": EngineSessionSerializer(billing_session.engine_session).data,
                "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
                "transport": "polling",
                "polling": {"recommended_interval_seconds": 8},
            },
            status=status.HTTP_200_OK,
        )


class PaymentBillingSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, billing_session_id):
        billing_session = get_object_or_404(
            PaymentBillingSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=billing_session_id,
        )
        workflow = billing_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if billing_session.status in {
            PaymentBillingStatus.COMPLETED,
            PaymentBillingStatus.CANCELLED,
            PaymentBillingStatus.FAILED,
        }:
            return Response({"detail": "This billing session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = billing_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = PaymentBillingStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        step_key = str(serializer.validated_data["step_key"]).strip()
        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload_data = serializer.validated_data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        now_value = timezone.now()

        next_step_state = billing_session.step_state if isinstance(billing_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload_data
        next_step_state[step_key] = current_row
        billing_session.step_state = next_step_state

        if step_key == "review_charges" and is_completed:
            billing_session.status = PaymentBillingStatus.QUOTE_READY
            billing_session.started_at = billing_session.started_at or now_value
        elif step_key == "select_payment_method" and is_completed:
            provider = str(payload_data.get("payment_provider") or payload_data.get("paymentProvider") or billing_session.payment_provider or _health_default_payment_provider()).strip().lower()
            if _is_legacy_health_wallet_provider(provider) and not _health_wallet_checkout_enabled():
                return Response(
                    {
                        "detail": "Health wallet/KIS Coin checkout is disabled. Use USD checkout with Flutterwave or another configured payment provider.",
                        "code": "legacy_health_wallet_checkout_disabled",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            billing_session.payment_provider = KIS_WALLET_PROVIDER if _is_legacy_health_wallet_provider(provider) else (provider or _health_default_payment_provider())
            payload_data["payment_provider"] = billing_session.payment_provider
            billing_session.status = PaymentBillingStatus.PAYMENT_PENDING
        elif step_key == "authorize_payment" and is_completed:
            if request.user.id != workflow.user_id:
                return Response(
                    {"detail": "Only the workflow owner can authorize payment."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            provider = str(payload_data.get("payment_provider") or payload_data.get("paymentProvider") or billing_session.payment_provider or _health_default_payment_provider()).strip().lower()
            legacy_wallet_payment = _is_legacy_health_wallet_provider(provider)
            if legacy_wallet_payment and not _health_wallet_checkout_enabled():
                return Response(
                    {
                        "detail": "Health wallet/KIS Coin checkout is disabled. Use USD checkout with Flutterwave or another configured payment provider.",
                        "code": "legacy_health_wallet_checkout_disabled",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            amount_paid_micro = payload_data.get("amount_paid_micro")
            amount_paid_kisc = payload_data.get("amount_paid_kisc")
            if amount_paid_kisc in (None, ""):
                amount_paid_kisc = payload_data.get("amountPaidKisc")
            if amount_paid_kisc not in (None, ""):
                amount_paid_micro = _kisc_to_micro(amount_paid_kisc, allow_empty=True)
            if amount_paid_micro is not None:
                billing_session.amount_paid_micro = max(0, int(amount_paid_micro))
            else:
                billing_session.amount_paid_micro = max(
                    int(billing_session.amount_paid_micro or 0),
                    int(billing_session.payable_amount_micro or 0),
                )

            payable_required = max(0, int(billing_session.payable_amount_micro or 0))
            if payable_required > 0 and int(billing_session.amount_paid_micro or 0) < payable_required:
                return Response(
                    {
                        "detail": "Amount paid is below required payable amount.",
                        "required_micro": int(payable_required),
                        "required_kisc": _micro_to_kisc_text(payable_required),
                        "amount_paid_micro": int(billing_session.amount_paid_micro or 0),
                        "amount_paid_kisc": _micro_to_kisc_text(billing_session.amount_paid_micro or 0),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Legacy wallet debit is retained only behind an explicit migration flag.
            if legacy_wallet_payment and not billing_session.paid_at and int(billing_session.amount_paid_micro or 0) > 0:
                charge_micro = int(billing_session.amount_paid_micro or 0)
                charge_cents = _micro_to_cents(charge_micro)
                wallet = get_wallet_account(workflow.user)
                if wallet.balance_cents < charge_cents:
                    available_micro = _cents_to_micro(int(wallet.balance_cents or 0))
                    return Response(
                        {
                            "detail": "Insufficient legacy wallet balance.",
                            "required_micro": int(charge_micro),
                            "available_micro": int(available_micro),
                            "required_kisc": _micro_to_kisc_text(charge_micro),
                            "available_kisc": _micro_to_kisc_text(available_micro),
                            "required_cents": int(charge_cents),
                            "available_cents": int(wallet.balance_cents or 0),
                        },
                        status=status.HTTP_402_PAYMENT_REQUIRED,
                    )
                debit_wallet_balance(
                    user=workflow.user,
                    amount_cents=charge_cents,
                    reference=f"health_ops_billing:{billing_session.id}",
                    kind="purchase",
                    meta={
                        "workflow_session_id": str(workflow.id),
                        "engine_session_id": str(engine_session.id),
                        "billing_session_id": str(billing_session.id),
                        "payment_mode": "kis_wallet",
                        "charged_micro": int(charge_micro),
                        "charged_kisc": _micro_to_kisc_text(charge_micro),
                    },
                )

            payload_data["amount_paid_micro"] = int(billing_session.amount_paid_micro or 0)
            payload_data["amount_paid_kisc"] = _micro_to_kisc_text(billing_session.amount_paid_micro or 0)
            billing_session.payment_reference = str(payload_data.get("payment_reference") or billing_session.payment_reference or "").strip()
            if not billing_session.payment_reference:
                prefix = "kis-wallet" if legacy_wallet_payment else str(provider or _health_default_payment_provider()).replace("_", "-")
                billing_session.payment_reference = f"{prefix}-{str(billing_session.id).replace('-', '')[:12]}"
            billing_session.payment_provider = KIS_WALLET_PROVIDER if legacy_wallet_payment else (provider or _health_default_payment_provider())
            if legacy_wallet_payment or _health_provider_payment_confirmed(billing_session) or str(payload_data.get("payment_status") or payload_data.get("paymentStatus") or "").strip().lower() in {"paid", "success", "succeeded", "settled"}:
                billing_session.status = PaymentBillingStatus.PAID
                billing_session.paid_at = billing_session.paid_at or now_value
                payload_data["payment_status"] = "paid"
            else:
                billing_session.status = PaymentBillingStatus.PAYMENT_PENDING
                payload_data["payment_status"] = "pending"
        elif step_key == "issue_receipt" and is_completed:
            billing_session.invoice_number = str(payload_data.get("invoice_number") or billing_session.invoice_number or "").strip()
            billing_session.status = PaymentBillingStatus.COMPLETED
            billing_session.ended_at = billing_session.ended_at or now_value

        next_payload = billing_session.payload if isinstance(billing_session.payload, dict) else {}
        next_payload.update(payload_data)
        billing_session.payload = next_payload
        billing_session.save()

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"billing_step": payload_data},
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "billing_session_id": str(billing_session.id),
                "billing_status": billing_session.status,
                "last_billing_step": step_key,
                "payable_amount_kisc": _micro_to_kisc_text(billing_session.payable_amount_micro or 0),
                "amount_paid_kisc": _micro_to_kisc_text(billing_session.amount_paid_micro or 0),
                "payment_provider": billing_session.payment_provider,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        return Response(
            {
                "billing_session": PaymentBillingSessionSerializer(billing_session).data,
                "engine_session": EngineSessionSerializer(engine_session).data,
                "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
                "transport": "polling",
                "polling": {"recommended_interval_seconds": 8},
            },
            status=status.HTTP_200_OK,
        )


class PaymentBillingSessionPayloadView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, billing_session_id):
        billing_session = get_object_or_404(
            PaymentBillingSession.objects.select_related("engine_session", "workflow_session", "institution"),
            id=billing_session_id,
        )
        workflow = billing_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if billing_session.status in {
            PaymentBillingStatus.COMPLETED,
            PaymentBillingStatus.CANCELLED,
            PaymentBillingStatus.FAILED,
        }:
            return Response({"detail": "This billing session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = billing_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = PaymentBillingPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merge = bool(serializer.validated_data.get("merge", True))
        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        if merge:
            next_payload = billing_session.payload if isinstance(billing_session.payload, dict) else {}
            next_payload.update(payload_data)
            billing_session.payload = next_payload
            next_meta = billing_session.metadata if isinstance(billing_session.metadata, dict) else {}
            next_meta.update(metadata)
            billing_session.metadata = next_meta
        else:
            billing_session.payload = payload_data
            billing_session.metadata = metadata

        requested_provider = str(
            payload_data.get("payment_provider")
            or payload_data.get("paymentProvider")
            or billing_session.payment_provider
            or _health_default_payment_provider()
        ).strip().lower()
        if _is_legacy_health_wallet_provider(requested_provider) and not _health_wallet_checkout_enabled():
            return Response(
                {
                    "detail": "Health wallet/KIS Coin checkout is disabled. Use USD checkout with Flutterwave or another configured payment provider.",
                    "code": "legacy_health_wallet_checkout_disabled",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        billing_session.payment_provider = KIS_WALLET_PROVIDER if _is_legacy_health_wallet_provider(requested_provider) else (requested_provider or _health_default_payment_provider())
        payload_data["payment_provider"] = billing_session.payment_provider
        if "payment_reference" in payload_data:
            billing_session.payment_reference = str(payload_data.get("payment_reference") or "").strip()
        if "invoice_number" in payload_data:
            billing_session.invoice_number = str(payload_data.get("invoice_number") or "").strip()
        if "amount_paid_kisc" in payload_data:
            billing_session.amount_paid_micro = max(0, int(_kisc_to_micro(payload_data.get("amount_paid_kisc"), allow_empty=True) or 0))
            payload_data["amount_paid_micro"] = int(billing_session.amount_paid_micro or 0)
        if "amount_paid_micro" in payload_data:
            billing_session.amount_paid_micro = max(0, int(payload_data.get("amount_paid_micro") or 0))
        payload_data["amount_paid_kisc"] = _micro_to_kisc_text(billing_session.amount_paid_micro or 0)
        billing_session.save()

        return Response(
            {
                "billing_session": PaymentBillingSessionSerializer(billing_session).data,
                "engine_session": EngineSessionSerializer(engine_session).data,
                "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
                "transport": "polling",
                "polling": {"recommended_interval_seconds": 8},
            },
            status=status.HTTP_200_OK,
        )


class PaymentBillingSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, billing_session_id):
        billing_session = get_object_or_404(
            PaymentBillingSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=billing_session_id,
        )
        workflow = billing_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if billing_session.status in {
            PaymentBillingStatus.COMPLETED,
            PaymentBillingStatus.CANCELLED,
            PaymentBillingStatus.FAILED,
        }:
            return Response({"detail": "This billing session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = billing_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = PaymentBillingEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or PaymentBillingStatus.COMPLETED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})
        now_value = timezone.now()

        next_meta = billing_session.metadata if isinstance(billing_session.metadata, dict) else {}
        if summary:
            next_meta["closure_summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        billing_session.metadata = next_meta

        if status_value in {PaymentBillingStatus.CANCELLED, PaymentBillingStatus.FAILED}:
            billing_session.status = status_value
            billing_session.ended_at = now_value
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            if int(billing_session.payable_amount_micro or 0) > 0 and not billing_session.paid_at:
                return Response(
                    {
                        "detail": "Provider payment must be confirmed before completing billing.",
                        "required_micro": int(billing_session.payable_amount_micro or 0),
                        "required_kisc": _micro_to_kisc_text(billing_session.payable_amount_micro or 0),
                        "payment_provider": billing_session.payment_provider or _health_default_payment_provider(),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            billing_session.status = PaymentBillingStatus.COMPLETED
            billing_session.ended_at = now_value
            if int(billing_session.amount_paid_micro or 0) <= 0:
                billing_session.amount_paid_micro = max(0, int(billing_session.payable_amount_micro or 0))
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "issue_receipt"
            next_steps = billing_session.step_state if isinstance(billing_session.step_state, dict) else {}
            next_steps[final_step_key] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            billing_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"summary": summary},
            )

        billing_session.save()
        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "billing_session_id": str(billing_session.id),
                "billing_status": billing_session.status,
                "ended_at": billing_session.ended_at.isoformat() if billing_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        return Response(
            {
                "billing_session": PaymentBillingSessionSerializer(billing_session).data,
                "engine_session": EngineSessionSerializer(engine_session).data,
                "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
                "transport": "polling",
                "polling": {"recommended_interval_seconds": 8},
            },
            status=status.HTTP_200_OK,
        )


class HomeLogisticsSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = HomeLogisticsStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_session = _get_home_logistics_engine_session(workflow)
        if not engine_session:
            return Response(
                {"detail": "Home logistics engine is not mapped to this workflow."},
                status=status.HTTP_404_NOT_FOUND,
            )
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}
        requested_logistics_code = str(serializer.validated_data.get("logistics_code") or "").strip()
        requested_task_type = str(serializer.validated_data.get("task_type") or "").strip()
        logistics_code = requested_logistics_code or f"kis-log-{uuid4().hex[:10]}"

        created = False
        home_session = (
            HomeLogisticsSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session", "institution", "service", "user")
            .filter(workflow_session=workflow)
            .first()
        )
        step_keys = _list_engine_step_keys(engine_session) or list(HOME_LOGISTICS_STEP_ORDER)
        now_value = timezone.now()
        scheduled_start = _parse_optional_datetime(payload_data.get("scheduled_window_start"))
        scheduled_end = _parse_optional_datetime(payload_data.get("scheduled_window_end"))

        if not home_session:
            created = True
            home_session = HomeLogisticsSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                logistics_code=logistics_code,
                status=HomeLogisticsStatus.SCHEDULING if (payload_data or requested_task_type) else HomeLogisticsStatus.WAITING,
                task_type=requested_task_type or str(payload_data.get("task_type") or "").strip(),
                route_reference=str(payload_data.get("route_reference") or "").strip(),
                assignee_name=str(payload_data.get("assignee_name") or "").strip(),
                current_eta_minutes=payload_data.get("eta_minutes"),
                location_latitude=payload_data.get("latitude"),
                location_longitude=payload_data.get("longitude"),
                scheduled_window_start=scheduled_start,
                scheduled_window_end=scheduled_end,
                step_state=_default_step_state(step_keys),
                payload=payload_data,
                tracking_events=[],
                metadata=metadata,
                started_at=now_value if (payload_data or requested_task_type) else None,
            )
            home_session.tracking_events = _append_tracking_event(
                home_session.tracking_events,
                event_type="session_started",
                status_value=home_session.status,
                payload={"logistics_code": logistics_code},
            )
            home_session.last_tracking_at = now_value
            home_session.save()
        else:
            if home_session.status in {HomeLogisticsStatus.COMPLETED, HomeLogisticsStatus.CANCELLED}:
                return Response({"detail": "This home logistics session has already ended."}, status=status.HTTP_409_CONFLICT)
            if not isinstance(home_session.step_state, dict) or not home_session.step_state:
                home_session.step_state = _default_step_state(step_keys)

            if requested_logistics_code:
                home_session.logistics_code = requested_logistics_code
            if requested_task_type:
                home_session.task_type = requested_task_type

            next_payload = home_session.payload if isinstance(home_session.payload, dict) else {}
            if payload_data:
                next_payload.update(payload_data)
            home_session.payload = next_payload

            next_meta = home_session.metadata if isinstance(home_session.metadata, dict) else {}
            if metadata:
                next_meta.update(metadata)
            home_session.metadata = next_meta

            if "task_type" in payload_data:
                home_session.task_type = str(payload_data.get("task_type") or "").strip()
            if "route_reference" in payload_data:
                home_session.route_reference = str(payload_data.get("route_reference") or "").strip()
            if "assignee_name" in payload_data:
                home_session.assignee_name = str(payload_data.get("assignee_name") or "").strip()
            if "eta_minutes" in payload_data:
                home_session.current_eta_minutes = payload_data.get("eta_minutes")
            if "latitude" in payload_data:
                home_session.location_latitude = payload_data.get("latitude")
            if "longitude" in payload_data:
                home_session.location_longitude = payload_data.get("longitude")
            if scheduled_start:
                home_session.scheduled_window_start = scheduled_start
            if scheduled_end:
                home_session.scheduled_window_end = scheduled_end

            if home_session.status == HomeLogisticsStatus.WAITING and (payload_data or metadata or requested_task_type):
                home_session.status = HomeLogisticsStatus.SCHEDULING
                home_session.started_at = home_session.started_at or now_value

            home_session.tracking_events = _append_tracking_event(
                home_session.tracking_events,
                event_type="session_refresh",
                status_value=home_session.status,
                payload={"updated": bool(payload_data or metadata or requested_task_type)},
            )
            home_session.last_tracking_at = now_value
            home_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "home_logistics_session_id": str(home_session.id),
                "home_logistics_status": home_session.status,
                "logistics_code": home_session.logistics_code,
                "route_reference": home_session.route_reference,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "home_logistics_session": HomeLogisticsSessionSerializer(home_session).data,
            "tracking_events": home_session.tracking_events[-50:] if isinstance(home_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 6},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class HomeLogisticsSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, home_logistics_session_id):
        home_session = get_object_or_404(
            HomeLogisticsSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=home_logistics_session_id,
        )
        if not _can_access_workflow_session(request.user, home_session.workflow_session):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(200, limit))
        tracking_rows = home_session.tracking_events if isinstance(home_session.tracking_events, list) else []

        payload = {
            "home_logistics_session": HomeLogisticsSessionSerializer(home_session).data,
            "tracking_events": tracking_rows[-limit:],
            "engine_session": EngineSessionSerializer(home_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(home_session.workflow_session).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 6},
        }
        return Response(payload, status=status.HTTP_200_OK)


class HomeLogisticsSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, home_logistics_session_id):
        home_session = get_object_or_404(
            HomeLogisticsSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=home_logistics_session_id,
        )
        workflow = home_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if home_session.status in {HomeLogisticsStatus.COMPLETED, HomeLogisticsStatus.CANCELLED}:
            return Response({"detail": "This home logistics session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = home_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = HomeLogisticsStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step_key = str(serializer.validated_data["step_key"])
        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload_data = serializer.validated_data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}

        now_value = timezone.now()
        next_step_state = home_session.step_state if isinstance(home_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload_data
        next_step_state[step_key] = current_row
        home_session.step_state = next_step_state

        next_payload = home_session.payload if isinstance(home_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        home_session.payload = next_payload

        if "task_type" in payload_data:
            home_session.task_type = str(payload_data.get("task_type") or "").strip()
        if "route_reference" in payload_data:
            home_session.route_reference = str(payload_data.get("route_reference") or "").strip()
        if "assignee_name" in payload_data:
            home_session.assignee_name = str(payload_data.get("assignee_name") or "").strip()
        if "eta_minutes" in payload_data:
            home_session.current_eta_minutes = payload_data.get("eta_minutes")
        if "latitude" in payload_data:
            home_session.location_latitude = payload_data.get("latitude")
        if "longitude" in payload_data:
            home_session.location_longitude = payload_data.get("longitude")
        scheduled_start = _parse_optional_datetime(payload_data.get("scheduled_window_start"))
        scheduled_end = _parse_optional_datetime(payload_data.get("scheduled_window_end"))
        if scheduled_start:
            home_session.scheduled_window_start = scheduled_start
        if scheduled_end:
            home_session.scheduled_window_end = scheduled_end

        if step_key == "select_logistics_mode" and is_completed:
            home_session.status = HomeLogisticsStatus.SCHEDULING
            home_session.started_at = home_session.started_at or now_value
        elif step_key == "schedule_window" and is_completed:
            home_session.status = HomeLogisticsStatus.SCHEDULING
        elif step_key == "assign_route" and is_completed:
            home_session.status = HomeLogisticsStatus.ROUTE_ASSIGNED
            home_session.dispatched_at = home_session.dispatched_at or now_value
        elif step_key == "track_eta" and is_completed:
            if bool(payload_data.get("arrived", False)):
                home_session.status = HomeLogisticsStatus.ARRIVED
                home_session.arrived_at = home_session.arrived_at or now_value
            else:
                home_session.status = HomeLogisticsStatus.IN_TRANSIT

        step_keys = _list_engine_step_keys(engine_session)
        if _are_all_steps_completed(next_step_state, step_keys) and home_session.status not in {
            HomeLogisticsStatus.ARRIVED,
            HomeLogisticsStatus.COMPLETED,
            HomeLogisticsStatus.CANCELLED,
        }:
            home_session.status = HomeLogisticsStatus.ARRIVED
            home_session.arrived_at = home_session.arrived_at or now_value

        home_session.last_tracking_at = now_value
        home_session.tracking_events = _append_tracking_event(
            home_session.tracking_events,
            event_type="step_update",
            status_value=home_session.status,
            payload={"step_key": step_key, "is_completed": is_completed, "payload": payload_data},
        )
        home_session.save()

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"home_logistics_step": payload_data},
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "home_logistics_session_id": str(home_session.id),
                "home_logistics_status": home_session.status,
                "logistics_code": home_session.logistics_code,
                "route_reference": home_session.route_reference,
                "last_home_logistics_step": step_key,
                "last_tracking_at": home_session.last_tracking_at.isoformat() if home_session.last_tracking_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "home_logistics_session": HomeLogisticsSessionSerializer(home_session).data,
            "tracking_events": home_session.tracking_events[-50:] if isinstance(home_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 6},
        }
        return Response(payload, status=status.HTTP_200_OK)


class HomeLogisticsSessionPayloadView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, home_logistics_session_id):
        home_session = get_object_or_404(
            HomeLogisticsSession.objects.select_related("engine_session", "workflow_session", "institution"),
            id=home_logistics_session_id,
        )
        workflow = home_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if home_session.status in {HomeLogisticsStatus.COMPLETED, HomeLogisticsStatus.CANCELLED}:
            return Response({"detail": "This home logistics session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = HomeLogisticsPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merge = bool(serializer.validated_data.get("merge", True))
        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        now_value = timezone.now()
        if merge:
            next_payload = home_session.payload if isinstance(home_session.payload, dict) else {}
            next_payload.update(payload_data)
            home_session.payload = next_payload
            next_meta = home_session.metadata if isinstance(home_session.metadata, dict) else {}
            next_meta.update(metadata)
            home_session.metadata = next_meta
        else:
            home_session.payload = payload_data
            home_session.metadata = metadata

        if "task_type" in payload_data:
            home_session.task_type = str(payload_data.get("task_type") or "").strip()
        if "route_reference" in payload_data:
            home_session.route_reference = str(payload_data.get("route_reference") or "").strip()
        if "assignee_name" in payload_data:
            home_session.assignee_name = str(payload_data.get("assignee_name") or "").strip()
        if "eta_minutes" in payload_data:
            home_session.current_eta_minutes = payload_data.get("eta_minutes")
        if "latitude" in payload_data:
            home_session.location_latitude = payload_data.get("latitude")
        if "longitude" in payload_data:
            home_session.location_longitude = payload_data.get("longitude")
        scheduled_start = _parse_optional_datetime(payload_data.get("scheduled_window_start"))
        scheduled_end = _parse_optional_datetime(payload_data.get("scheduled_window_end"))
        if scheduled_start:
            home_session.scheduled_window_start = scheduled_start
        if scheduled_end:
            home_session.scheduled_window_end = scheduled_end

        if home_session.status == HomeLogisticsStatus.WAITING and (payload_data or metadata):
            home_session.status = HomeLogisticsStatus.SCHEDULING
            home_session.started_at = home_session.started_at or now_value

        home_session.last_tracking_at = now_value
        home_session.tracking_events = _append_tracking_event(
            home_session.tracking_events,
            event_type="payload_update",
            status_value=home_session.status,
            payload={"payload": payload_data, "metadata": metadata},
        )
        home_session.save()

        payload = {
            "home_logistics_session": HomeLogisticsSessionSerializer(home_session).data,
            "tracking_events": home_session.tracking_events[-50:] if isinstance(home_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(home_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 6},
        }
        return Response(payload, status=status.HTTP_200_OK)


class HomeLogisticsTrackingView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, home_logistics_session_id):
        home_session = get_object_or_404(
            HomeLogisticsSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=home_logistics_session_id,
        )
        workflow = home_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if home_session.status in {HomeLogisticsStatus.COMPLETED, HomeLogisticsStatus.CANCELLED}:
            return Response({"detail": "This home logistics session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = HomeLogisticsTrackingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now_value = timezone.now()
        if "latitude" in data:
            home_session.location_latitude = data.get("latitude")
        if "longitude" in data:
            home_session.location_longitude = data.get("longitude")
        if "eta_minutes" in data:
            home_session.current_eta_minutes = data.get("eta_minutes")
        if "route_reference" in data:
            home_session.route_reference = str(data.get("route_reference") or "").strip()
        if "assignee_name" in data:
            home_session.assignee_name = str(data.get("assignee_name") or "").strip()

        status_value = str(data.get("status") or "").strip()
        if status_value:
            home_session.status = status_value
            if status_value == HomeLogisticsStatus.ROUTE_ASSIGNED:
                home_session.dispatched_at = home_session.dispatched_at or now_value
            if status_value == HomeLogisticsStatus.IN_TRANSIT:
                home_session.dispatched_at = home_session.dispatched_at or now_value
            if status_value == HomeLogisticsStatus.ARRIVED:
                home_session.arrived_at = home_session.arrived_at or now_value
            if status_value in {HomeLogisticsStatus.COMPLETED, HomeLogisticsStatus.CANCELLED}:
                home_session.ended_at = home_session.ended_at or now_value

        payload_data = data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        next_payload = home_session.payload if isinstance(home_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        home_session.payload = next_payload

        home_session.last_tracking_at = now_value
        home_session.tracking_events = _append_tracking_event(
            home_session.tracking_events,
            event_type="tracking_ping",
            status_value=home_session.status,
            payload={
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "eta_minutes": data.get("eta_minutes"),
                "status": status_value,
                "note": str(data.get("note") or "").strip(),
                "payload": payload_data,
            },
        )
        home_session.save()

        engine_session = home_session.engine_session
        if status_value == HomeLogisticsStatus.COMPLETED:
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "track_eta"
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"source": "tracking_ping", "status": "completed"},
            )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "home_logistics_session_id": str(home_session.id),
                "home_logistics_status": home_session.status,
                "logistics_code": home_session.logistics_code,
                "route_reference": home_session.route_reference,
                "last_tracking_at": home_session.last_tracking_at.isoformat() if home_session.last_tracking_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "home_logistics_session": HomeLogisticsSessionSerializer(home_session).data,
            "tracking_events": home_session.tracking_events[-50:] if isinstance(home_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 6},
        }
        return Response(payload, status=status.HTTP_200_OK)


class HomeLogisticsSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, home_logistics_session_id):
        home_session = get_object_or_404(
            HomeLogisticsSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=home_logistics_session_id,
        )
        workflow = home_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if home_session.status in {HomeLogisticsStatus.COMPLETED, HomeLogisticsStatus.CANCELLED}:
            return Response({"detail": "This home logistics session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = HomeLogisticsEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or HomeLogisticsStatus.COMPLETED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})

        now_value = timezone.now()
        next_meta = home_session.metadata if isinstance(home_session.metadata, dict) else {}
        if summary:
            next_meta["closure_summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        home_session.metadata = next_meta
        home_session.ended_at = now_value

        engine_session = home_session.engine_session
        if status_value == HomeLogisticsStatus.CANCELLED:
            home_session.status = HomeLogisticsStatus.CANCELLED
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            home_session.status = HomeLogisticsStatus.COMPLETED
            home_session.arrived_at = home_session.arrived_at or now_value
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "track_eta"
            next_steps = home_session.step_state if isinstance(home_session.step_state, dict) else {}
            next_steps[final_step_key] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            home_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"summary": summary},
            )

        home_session.last_tracking_at = now_value
        home_session.tracking_events = _append_tracking_event(
            home_session.tracking_events,
            event_type="session_end",
            status_value=home_session.status,
            payload={"status": home_session.status, "summary": summary},
        )
        home_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "home_logistics_session_id": str(home_session.id),
                "home_logistics_status": home_session.status,
                "logistics_code": home_session.logistics_code,
                "route_reference": home_session.route_reference,
                "ended_at": home_session.ended_at.isoformat() if home_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "home_logistics_session": HomeLogisticsSessionSerializer(home_session).data,
            "tracking_events": home_session.tracking_events[-50:] if isinstance(home_session.tracking_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 6},
        }
        return Response(payload, status=status.HTTP_200_OK)


class WellnessProgramSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = WellnessProgramStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_session = _get_wellness_engine_session(workflow)
        if not engine_session:
            return Response(
                {"detail": "Wellness program engine is not mapped to this workflow."},
                status=status.HTTP_404_NOT_FOUND,
            )
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}
        requested_program_name = str(serializer.validated_data.get("program_name") or "").strip()

        created = False
        wellness_session = (
            WellnessProgramSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session", "institution", "service", "user")
            .filter(workflow_session=workflow)
            .first()
        )
        step_keys = _list_engine_step_keys(engine_session) or list(WELLNESS_PROGRAM_STEP_ORDER)
        now_value = timezone.now()

        if not wellness_session:
            created = True
            completion_percent_value = int(payload_data.get("completion_percent") or 0)
            completion_percent_value = max(0, min(100, completion_percent_value))
            wellness_session = WellnessProgramSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                status=WellnessProgramStatus.ENROLLED if (payload_data or requested_program_name) else WellnessProgramStatus.WAITING,
                program_name=requested_program_name or str(payload_data.get("program_name") or "").strip(),
                goal_payload=payload_data.get("goal_payload") if isinstance(payload_data.get("goal_payload"), dict) else {},
                habit_payload=payload_data.get("habit_payload") if isinstance(payload_data.get("habit_payload"), dict) else {},
                current_streak=max(0, int(payload_data.get("current_streak") or 0)),
                completion_percent=completion_percent_value,
                step_state=_default_step_state(step_keys),
                payload=payload_data,
                activity_events=[],
                metadata=metadata,
                started_at=now_value if (payload_data or requested_program_name) else None,
            )
            wellness_session.activity_events = _append_tracking_event(
                wellness_session.activity_events,
                event_type="session_started",
                status_value=wellness_session.status,
                payload={"program_name": wellness_session.program_name},
            )
            wellness_session.last_activity_at = now_value
            wellness_session.save()
        else:
            if wellness_session.status in {WellnessProgramStatus.COMPLETED, WellnessProgramStatus.CANCELLED}:
                return Response({"detail": "This wellness session has already ended."}, status=status.HTTP_409_CONFLICT)
            if not isinstance(wellness_session.step_state, dict) or not wellness_session.step_state:
                wellness_session.step_state = _default_step_state(step_keys)

            if requested_program_name:
                wellness_session.program_name = requested_program_name

            next_payload = wellness_session.payload if isinstance(wellness_session.payload, dict) else {}
            if payload_data:
                next_payload.update(payload_data)
            wellness_session.payload = next_payload

            next_meta = wellness_session.metadata if isinstance(wellness_session.metadata, dict) else {}
            if metadata:
                next_meta.update(metadata)
            wellness_session.metadata = next_meta

            if "program_name" in payload_data:
                wellness_session.program_name = str(payload_data.get("program_name") or "").strip()
            if "goal_payload" in payload_data and isinstance(payload_data.get("goal_payload"), dict):
                wellness_session.goal_payload = payload_data.get("goal_payload")
            if "habit_payload" in payload_data and isinstance(payload_data.get("habit_payload"), dict):
                wellness_session.habit_payload = payload_data.get("habit_payload")
            if "current_streak" in payload_data:
                wellness_session.current_streak = max(0, int(payload_data.get("current_streak") or 0))
            if "completion_percent" in payload_data:
                wellness_session.completion_percent = max(0, min(100, int(payload_data.get("completion_percent") or 0)))

            if wellness_session.status == WellnessProgramStatus.WAITING and (payload_data or metadata or requested_program_name):
                wellness_session.status = WellnessProgramStatus.ENROLLED
                wellness_session.started_at = wellness_session.started_at or now_value
            if wellness_session.status == WellnessProgramStatus.ENROLLED and bool(payload_data.get("activate", False)):
                wellness_session.status = WellnessProgramStatus.IN_PROGRESS

            wellness_session.activity_events = _append_tracking_event(
                wellness_session.activity_events,
                event_type="session_refresh",
                status_value=wellness_session.status,
                payload={"updated": bool(payload_data or metadata or requested_program_name)},
            )
            wellness_session.last_activity_at = now_value
            wellness_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "wellness_session_id": str(wellness_session.id),
                "wellness_status": wellness_session.status,
                "wellness_program_name": wellness_session.program_name,
                "wellness_completion_percent": int(wellness_session.completion_percent or 0),
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "wellness_session": WellnessProgramSessionSerializer(wellness_session).data,
            "activity_events": wellness_session.activity_events[-50:] if isinstance(wellness_session.activity_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 12},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class WellnessProgramSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, wellness_session_id):
        wellness_session = get_object_or_404(
            WellnessProgramSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=wellness_session_id,
        )
        if not _can_access_workflow_session(request.user, wellness_session.workflow_session):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(200, limit))
        activity_rows = wellness_session.activity_events if isinstance(wellness_session.activity_events, list) else []

        payload = {
            "wellness_session": WellnessProgramSessionSerializer(wellness_session).data,
            "activity_events": activity_rows[-limit:],
            "engine_session": EngineSessionSerializer(wellness_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(wellness_session.workflow_session).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 12},
        }
        return Response(payload, status=status.HTTP_200_OK)


class WellnessProgramSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, wellness_session_id):
        wellness_session = get_object_or_404(
            WellnessProgramSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=wellness_session_id,
        )
        workflow = wellness_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if wellness_session.status in {WellnessProgramStatus.COMPLETED, WellnessProgramStatus.CANCELLED}:
            return Response({"detail": "This wellness session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = wellness_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = WellnessProgramStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step_key = str(serializer.validated_data["step_key"])
        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload_data = serializer.validated_data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}

        now_value = timezone.now()
        next_step_state = wellness_session.step_state if isinstance(wellness_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload_data
        next_step_state[step_key] = current_row
        wellness_session.step_state = next_step_state

        next_payload = wellness_session.payload if isinstance(wellness_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        wellness_session.payload = next_payload

        if "program_name" in payload_data:
            wellness_session.program_name = str(payload_data.get("program_name") or "").strip()
        if "goal_payload" in payload_data and isinstance(payload_data.get("goal_payload"), dict):
            wellness_session.goal_payload = payload_data.get("goal_payload")
        if "habit_payload" in payload_data and isinstance(payload_data.get("habit_payload"), dict):
            wellness_session.habit_payload = payload_data.get("habit_payload")
        if "current_streak" in payload_data:
            wellness_session.current_streak = max(0, int(payload_data.get("current_streak") or 0))
        if "completion_percent" in payload_data:
            wellness_session.completion_percent = max(0, min(100, int(payload_data.get("completion_percent") or 0)))

        if step_key == "enroll_program" and is_completed:
            wellness_session.status = WellnessProgramStatus.ENROLLED
            wellness_session.started_at = wellness_session.started_at or now_value
        elif step_key == "set_goals" and is_completed:
            wellness_session.status = WellnessProgramStatus.IN_PROGRESS
        elif step_key == "track_habits" and is_completed:
            wellness_session.status = WellnessProgramStatus.IN_PROGRESS
        elif step_key == "review_progress" and is_completed:
            wellness_session.status = WellnessProgramStatus.IN_PROGRESS

        step_keys = _list_engine_step_keys(engine_session)
        completed_steps_count = sum(
            1
            for row in next_step_state.values()
            if isinstance(row, dict) and bool(row.get("is_completed"))
        )
        if step_keys:
            wellness_session.completion_percent = max(
                int(wellness_session.completion_percent or 0),
                int((completed_steps_count * 100) / max(1, len(step_keys))),
            )

        wellness_session.last_activity_at = now_value
        wellness_session.activity_events = _append_tracking_event(
            wellness_session.activity_events,
            event_type="step_update",
            status_value=wellness_session.status,
            payload={"step_key": step_key, "is_completed": is_completed, "payload": payload_data},
        )
        wellness_session.save()

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"wellness_step": payload_data},
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "wellness_session_id": str(wellness_session.id),
                "wellness_status": wellness_session.status,
                "wellness_program_name": wellness_session.program_name,
                "wellness_completion_percent": int(wellness_session.completion_percent or 0),
                "last_wellness_step": step_key,
                "last_activity_at": wellness_session.last_activity_at.isoformat() if wellness_session.last_activity_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "wellness_session": WellnessProgramSessionSerializer(wellness_session).data,
            "activity_events": wellness_session.activity_events[-50:] if isinstance(wellness_session.activity_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 12},
        }
        return Response(payload, status=status.HTTP_200_OK)


class WellnessProgramSessionPayloadView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, wellness_session_id):
        wellness_session = get_object_or_404(
            WellnessProgramSession.objects.select_related("engine_session", "workflow_session", "institution"),
            id=wellness_session_id,
        )
        workflow = wellness_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if wellness_session.status in {WellnessProgramStatus.COMPLETED, WellnessProgramStatus.CANCELLED}:
            return Response({"detail": "This wellness session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = WellnessProgramPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merge = bool(serializer.validated_data.get("merge", True))
        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        now_value = timezone.now()
        if merge:
            next_payload = wellness_session.payload if isinstance(wellness_session.payload, dict) else {}
            next_payload.update(payload_data)
            wellness_session.payload = next_payload
            next_meta = wellness_session.metadata if isinstance(wellness_session.metadata, dict) else {}
            next_meta.update(metadata)
            wellness_session.metadata = next_meta
        else:
            wellness_session.payload = payload_data
            wellness_session.metadata = metadata

        if "program_name" in payload_data:
            wellness_session.program_name = str(payload_data.get("program_name") or "").strip()
        if "goal_payload" in payload_data and isinstance(payload_data.get("goal_payload"), dict):
            wellness_session.goal_payload = payload_data.get("goal_payload")
        if "habit_payload" in payload_data and isinstance(payload_data.get("habit_payload"), dict):
            wellness_session.habit_payload = payload_data.get("habit_payload")
        if "current_streak" in payload_data:
            wellness_session.current_streak = max(0, int(payload_data.get("current_streak") or 0))
        if "completion_percent" in payload_data:
            wellness_session.completion_percent = max(0, min(100, int(payload_data.get("completion_percent") or 0)))

        if wellness_session.status == WellnessProgramStatus.WAITING and (payload_data or metadata):
            wellness_session.status = WellnessProgramStatus.ENROLLED
            wellness_session.started_at = wellness_session.started_at or now_value

        wellness_session.last_activity_at = now_value
        wellness_session.activity_events = _append_tracking_event(
            wellness_session.activity_events,
            event_type="payload_update",
            status_value=wellness_session.status,
            payload={"payload": payload_data, "metadata": metadata},
        )
        wellness_session.save()

        payload = {
            "wellness_session": WellnessProgramSessionSerializer(wellness_session).data,
            "activity_events": wellness_session.activity_events[-50:] if isinstance(wellness_session.activity_events, list) else [],
            "engine_session": EngineSessionSerializer(wellness_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 12},
        }
        return Response(payload, status=status.HTTP_200_OK)


class WellnessProgramActivityView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, wellness_session_id):
        wellness_session = get_object_or_404(
            WellnessProgramSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=wellness_session_id,
        )
        workflow = wellness_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if wellness_session.status in {WellnessProgramStatus.COMPLETED, WellnessProgramStatus.CANCELLED}:
            return Response({"detail": "This wellness session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = WellnessProgramActivitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        now_value = timezone.now()

        status_value = str(data.get("status") or "").strip()
        if status_value:
            wellness_session.status = status_value
            if status_value == WellnessProgramStatus.IN_PROGRESS:
                wellness_session.started_at = wellness_session.started_at or now_value
                wellness_session.paused_at = None
            if status_value == WellnessProgramStatus.PAUSED:
                wellness_session.paused_at = now_value
            if status_value == WellnessProgramStatus.COMPLETED:
                wellness_session.ended_at = wellness_session.ended_at or now_value
                wellness_session.completion_percent = 100

        if "streak_delta" in data:
            wellness_session.current_streak = max(0, int(wellness_session.current_streak or 0) + int(data.get("streak_delta") or 0))
        if "completion_percent" in data:
            wellness_session.completion_percent = max(0, min(100, int(data.get("completion_percent") or 0)))

        payload_data = data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        next_payload = wellness_session.payload if isinstance(wellness_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        wellness_session.payload = next_payload

        wellness_session.last_activity_at = now_value
        wellness_session.activity_events = _append_tracking_event(
            wellness_session.activity_events,
            event_type=str(data.get("event_type") or "progress"),
            status_value=wellness_session.status,
            payload={
                "note": str(data.get("note") or "").strip(),
                "streak_delta": data.get("streak_delta"),
                "completion_percent": data.get("completion_percent"),
                "payload": payload_data,
            },
        )
        wellness_session.save()

        engine_session = wellness_session.engine_session
        if wellness_session.status == WellnessProgramStatus.COMPLETED:
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "review_progress"
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"source": "activity_ping", "status": "completed"},
            )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "wellness_session_id": str(wellness_session.id),
                "wellness_status": wellness_session.status,
                "wellness_program_name": wellness_session.program_name,
                "wellness_completion_percent": int(wellness_session.completion_percent or 0),
                "current_streak": int(wellness_session.current_streak or 0),
                "last_activity_at": wellness_session.last_activity_at.isoformat() if wellness_session.last_activity_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "wellness_session": WellnessProgramSessionSerializer(wellness_session).data,
            "activity_events": wellness_session.activity_events[-50:] if isinstance(wellness_session.activity_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 12},
        }
        return Response(payload, status=status.HTTP_200_OK)


class WellnessProgramSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, wellness_session_id):
        wellness_session = get_object_or_404(
            WellnessProgramSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=wellness_session_id,
        )
        workflow = wellness_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if wellness_session.status in {WellnessProgramStatus.COMPLETED, WellnessProgramStatus.CANCELLED}:
            return Response({"detail": "This wellness session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = WellnessProgramEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or WellnessProgramStatus.COMPLETED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})

        now_value = timezone.now()
        next_meta = wellness_session.metadata if isinstance(wellness_session.metadata, dict) else {}
        if summary:
            next_meta["closure_summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        wellness_session.metadata = next_meta
        wellness_session.ended_at = now_value

        engine_session = wellness_session.engine_session
        if status_value == WellnessProgramStatus.CANCELLED:
            wellness_session.status = WellnessProgramStatus.CANCELLED
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            wellness_session.status = WellnessProgramStatus.COMPLETED
            wellness_session.completion_percent = 100
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "review_progress"
            next_steps = wellness_session.step_state if isinstance(wellness_session.step_state, dict) else {}
            next_steps[final_step_key] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            wellness_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"summary": summary},
            )

        wellness_session.last_activity_at = now_value
        wellness_session.activity_events = _append_tracking_event(
            wellness_session.activity_events,
            event_type="session_end",
            status_value=wellness_session.status,
            payload={"status": wellness_session.status, "summary": summary},
        )
        wellness_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "wellness_session_id": str(wellness_session.id),
                "wellness_status": wellness_session.status,
                "wellness_program_name": wellness_session.program_name,
                "wellness_completion_percent": int(wellness_session.completion_percent or 0),
                "ended_at": wellness_session.ended_at.isoformat() if wellness_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "wellness_session": WellnessProgramSessionSerializer(wellness_session).data,
            "activity_events": wellness_session.activity_events[-50:] if isinstance(wellness_session.activity_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 12},
        }
        return Response(payload, status=status.HTTP_200_OK)


class NotificationReminderSessionStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = NotificationReminderStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        workflow = get_object_or_404(
            ServiceWorkflowSession.objects.select_related("institution", "service", "user").prefetch_related(
                "engine_sessions__engine_map__engine"
            ),
            id=serializer.validated_data["workflow_session_id"],
        )
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        engine_session = _get_notification_engine_session(workflow)
        if not engine_session:
            return Response(
                {"detail": "Notification reminder engine is not mapped to this workflow."},
                status=status.HTTP_404_NOT_FOUND,
            )
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}
        reminder_timezone = str(serializer.validated_data.get("reminder_timezone") or "").strip()

        created = False
        notification_session = (
            NotificationReminderSession.objects.select_for_update()
            .select_related("engine_session", "workflow_session", "institution", "service", "user")
            .filter(workflow_session=workflow)
            .first()
        )
        step_keys = _list_engine_step_keys(engine_session) or list(NOTIFICATION_REMINDER_STEP_ORDER)
        now_value = timezone.now()
        next_run_at = _parse_optional_datetime(payload_data.get("next_run_at"))

        if not notification_session:
            created = True
            notification_session = NotificationReminderSession(
                workflow_session=workflow,
                engine_session=engine_session,
                institution=workflow.institution,
                service=workflow.service,
                user=workflow.user,
                status=NotificationReminderStatus.CONFIGURING if (payload_data or reminder_timezone) else NotificationReminderStatus.WAITING,
                channel_config=payload_data.get("channel_config") if isinstance(payload_data.get("channel_config"), dict) else {},
                rule_config=payload_data.get("rule_config") if isinstance(payload_data.get("rule_config"), dict) else {},
                reminder_timezone=reminder_timezone or str(payload_data.get("reminder_timezone") or "UTC"),
                next_run_at=next_run_at,
                step_state=_default_step_state(step_keys),
                payload=payload_data,
                delivery_events=[],
                metadata=metadata,
                started_at=now_value if (payload_data or reminder_timezone) else None,
            )
            notification_session.delivery_events = _append_tracking_event(
                notification_session.delivery_events,
                event_type="session_started",
                status_value=notification_session.status,
                payload={"reminder_timezone": notification_session.reminder_timezone},
            )
            notification_session.last_delivery_at = now_value
            notification_session.save()
        else:
            if notification_session.status in {
                NotificationReminderStatus.COMPLETED,
                NotificationReminderStatus.DISABLED,
                NotificationReminderStatus.CANCELLED,
            }:
                return Response({"detail": "This notification session has already ended."}, status=status.HTTP_409_CONFLICT)
            if not isinstance(notification_session.step_state, dict) or not notification_session.step_state:
                notification_session.step_state = _default_step_state(step_keys)

            next_payload = notification_session.payload if isinstance(notification_session.payload, dict) else {}
            if payload_data:
                next_payload.update(payload_data)
            notification_session.payload = next_payload

            next_meta = notification_session.metadata if isinstance(notification_session.metadata, dict) else {}
            if metadata:
                next_meta.update(metadata)
            notification_session.metadata = next_meta

            if reminder_timezone:
                notification_session.reminder_timezone = reminder_timezone
            if "channel_config" in payload_data and isinstance(payload_data.get("channel_config"), dict):
                notification_session.channel_config = payload_data.get("channel_config")
            if "rule_config" in payload_data and isinstance(payload_data.get("rule_config"), dict):
                notification_session.rule_config = payload_data.get("rule_config")
            if "reminder_timezone" in payload_data:
                notification_session.reminder_timezone = str(payload_data.get("reminder_timezone") or "UTC")
            if next_run_at:
                notification_session.next_run_at = next_run_at

            if notification_session.status == NotificationReminderStatus.WAITING and (payload_data or metadata or reminder_timezone):
                notification_session.status = NotificationReminderStatus.CONFIGURING
                notification_session.started_at = notification_session.started_at or now_value

            notification_session.delivery_events = _append_tracking_event(
                notification_session.delivery_events,
                event_type="session_refresh",
                status_value=notification_session.status,
                payload={"updated": bool(payload_data or metadata or reminder_timezone)},
            )
            notification_session.last_delivery_at = now_value
            notification_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "notification_session_id": str(notification_session.id),
                "notification_status": notification_session.status,
                "notification_next_run_at": notification_session.next_run_at.isoformat() if notification_session.next_run_at else "",
                "notification_sent_count": int(notification_session.sent_count or 0),
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "notification_session": NotificationReminderSessionSerializer(notification_session).data,
            "delivery_events": notification_session.delivery_events[-50:] if isinstance(notification_session.delivery_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 15},
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class NotificationReminderSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, notification_session_id):
        notification_session = get_object_or_404(
            NotificationReminderSession.objects.select_related(
                "engine_session",
                "workflow_session",
                "institution",
                "service",
                "user",
            ),
            id=notification_session_id,
        )
        if not _can_access_workflow_session(request.user, notification_session.workflow_session):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(200, limit))
        delivery_rows = notification_session.delivery_events if isinstance(notification_session.delivery_events, list) else []

        payload = {
            "notification_session": NotificationReminderSessionSerializer(notification_session).data,
            "delivery_events": delivery_rows[-limit:],
            "engine_session": EngineSessionSerializer(notification_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(notification_session.workflow_session).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 15},
        }
        return Response(payload, status=status.HTTP_200_OK)


class NotificationReminderSessionStepUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, notification_session_id):
        notification_session = get_object_or_404(
            NotificationReminderSession.objects.select_related(
                "engine_session__engine_map__engine",
                "workflow_session",
                "institution",
            ),
            id=notification_session_id,
        )
        workflow = notification_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if notification_session.status in {
            NotificationReminderStatus.COMPLETED,
            NotificationReminderStatus.DISABLED,
            NotificationReminderStatus.CANCELLED,
        }:
            return Response({"detail": "This notification session has already ended."}, status=status.HTTP_409_CONFLICT)

        engine_session = notification_session.engine_session
        access_error = _engine_access_error_response(workflow, engine_session)
        if access_error:
            return access_error

        serializer = NotificationReminderStepUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step_key = str(serializer.validated_data["step_key"])
        is_completed = bool(serializer.validated_data.get("is_completed", True))
        payload_data = serializer.validated_data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}

        now_value = timezone.now()
        next_step_state = notification_session.step_state if isinstance(notification_session.step_state, dict) else {}
        current_row = next_step_state.get(step_key)
        if not isinstance(current_row, dict):
            current_row = {}
        current_row["is_completed"] = is_completed
        current_row["completed_at"] = now_value.isoformat() if is_completed else None
        current_row["payload"] = payload_data
        next_step_state[step_key] = current_row
        notification_session.step_state = next_step_state

        next_payload = notification_session.payload if isinstance(notification_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        notification_session.payload = next_payload

        if "channel_config" in payload_data and isinstance(payload_data.get("channel_config"), dict):
            notification_session.channel_config = payload_data.get("channel_config")
        if "rule_config" in payload_data and isinstance(payload_data.get("rule_config"), dict):
            notification_session.rule_config = payload_data.get("rule_config")
        if "reminder_timezone" in payload_data:
            notification_session.reminder_timezone = str(payload_data.get("reminder_timezone") or "UTC")
        next_run_at = _parse_optional_datetime(payload_data.get("next_run_at"))
        if next_run_at:
            notification_session.next_run_at = next_run_at

        if step_key == "select_channels" and is_completed:
            notification_session.status = NotificationReminderStatus.CONFIGURING
            notification_session.started_at = notification_session.started_at or now_value
        elif step_key == "configure_rules" and is_completed:
            notification_session.status = NotificationReminderStatus.CONFIGURING
        elif step_key == "schedule_reminders" and is_completed:
            notification_session.status = NotificationReminderStatus.ACTIVE
        elif step_key == "confirm_delivery" and is_completed:
            notification_session.status = NotificationReminderStatus.ACTIVE

        step_keys = _list_engine_step_keys(engine_session)
        if _are_all_steps_completed(next_step_state, step_keys) and notification_session.status not in {
            NotificationReminderStatus.COMPLETED,
            NotificationReminderStatus.DISABLED,
            NotificationReminderStatus.CANCELLED,
        }:
            notification_session.status = NotificationReminderStatus.ACTIVE

        notification_session.last_delivery_at = now_value
        notification_session.delivery_events = _append_tracking_event(
            notification_session.delivery_events,
            event_type="step_update",
            status_value=notification_session.status,
            payload={"step_key": step_key, "is_completed": is_completed, "payload": payload_data},
        )
        notification_session.save()

        workflow, engine_session = _apply_engine_step_update(
            workflow=workflow,
            engine_session=engine_session,
            step_key=step_key,
            is_completed=is_completed,
            payload={"notification_step": payload_data},
        )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "notification_session_id": str(notification_session.id),
                "notification_status": notification_session.status,
                "notification_next_run_at": notification_session.next_run_at.isoformat() if notification_session.next_run_at else "",
                "notification_sent_count": int(notification_session.sent_count or 0),
                "last_notification_step": step_key,
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "notification_session": NotificationReminderSessionSerializer(notification_session).data,
            "delivery_events": notification_session.delivery_events[-50:] if isinstance(notification_session.delivery_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 15},
        }
        return Response(payload, status=status.HTTP_200_OK)


class NotificationReminderSessionPayloadView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, notification_session_id):
        notification_session = get_object_or_404(
            NotificationReminderSession.objects.select_related("engine_session", "workflow_session", "institution"),
            id=notification_session_id,
        )
        workflow = notification_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if notification_session.status in {
            NotificationReminderStatus.COMPLETED,
            NotificationReminderStatus.DISABLED,
            NotificationReminderStatus.CANCELLED,
        }:
            return Response({"detail": "This notification session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = NotificationReminderPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merge = bool(serializer.validated_data.get("merge", True))
        payload_data = serializer.validated_data.get("payload", {})
        metadata = serializer.validated_data.get("metadata", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        if not isinstance(metadata, dict):
            metadata = {}

        now_value = timezone.now()
        if merge:
            next_payload = notification_session.payload if isinstance(notification_session.payload, dict) else {}
            next_payload.update(payload_data)
            notification_session.payload = next_payload
            next_meta = notification_session.metadata if isinstance(notification_session.metadata, dict) else {}
            next_meta.update(metadata)
            notification_session.metadata = next_meta
        else:
            notification_session.payload = payload_data
            notification_session.metadata = metadata

        if "channel_config" in payload_data and isinstance(payload_data.get("channel_config"), dict):
            notification_session.channel_config = payload_data.get("channel_config")
        if "rule_config" in payload_data and isinstance(payload_data.get("rule_config"), dict):
            notification_session.rule_config = payload_data.get("rule_config")
        if "reminder_timezone" in payload_data:
            notification_session.reminder_timezone = str(payload_data.get("reminder_timezone") or "UTC")
        next_run_at = _parse_optional_datetime(payload_data.get("next_run_at"))
        if next_run_at:
            notification_session.next_run_at = next_run_at

        if notification_session.status == NotificationReminderStatus.WAITING and (payload_data or metadata):
            notification_session.status = NotificationReminderStatus.CONFIGURING
            notification_session.started_at = notification_session.started_at or now_value

        notification_session.last_delivery_at = now_value
        notification_session.delivery_events = _append_tracking_event(
            notification_session.delivery_events,
            event_type="payload_update",
            status_value=notification_session.status,
            payload={"payload": payload_data, "metadata": metadata},
        )
        notification_session.save()

        payload = {
            "notification_session": NotificationReminderSessionSerializer(notification_session).data,
            "delivery_events": notification_session.delivery_events[-50:] if isinstance(notification_session.delivery_events, list) else [],
            "engine_session": EngineSessionSerializer(notification_session.engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 15},
        }
        return Response(payload, status=status.HTTP_200_OK)


class NotificationReminderDeliveryView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, notification_session_id):
        notification_session = get_object_or_404(
            NotificationReminderSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=notification_session_id,
        )
        workflow = notification_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if notification_session.status in {
            NotificationReminderStatus.COMPLETED,
            NotificationReminderStatus.DISABLED,
            NotificationReminderStatus.CANCELLED,
        }:
            return Response({"detail": "This notification session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = NotificationReminderDeliverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        now_value = timezone.now()

        status_value = str(data.get("status") or "").strip()
        if status_value:
            notification_session.status = status_value
            if status_value == NotificationReminderStatus.ACTIVE:
                notification_session.started_at = notification_session.started_at or now_value
            if status_value in {
                NotificationReminderStatus.COMPLETED,
                NotificationReminderStatus.DISABLED,
                NotificationReminderStatus.CANCELLED,
            }:
                notification_session.ended_at = notification_session.ended_at or now_value

        next_run_at = data.get("next_run_at")
        if next_run_at is not None:
            notification_session.next_run_at = next_run_at

        if bool(data.get("sent", False)):
            notification_session.sent_count = int(notification_session.sent_count or 0) + 1
            notification_session.last_sent_at = now_value
            if notification_session.status == NotificationReminderStatus.CONFIGURING:
                notification_session.status = NotificationReminderStatus.ACTIVE

        if bool(data.get("failed", False)):
            notification_session.failed_count = int(notification_session.failed_count or 0) + 1

        payload_data = data.get("payload", {})
        if not isinstance(payload_data, dict):
            payload_data = {}
        next_payload = notification_session.payload if isinstance(notification_session.payload, dict) else {}
        if payload_data:
            next_payload.update(payload_data)
        notification_session.payload = next_payload

        notification_session.last_delivery_at = now_value
        notification_session.delivery_events = _append_tracking_event(
            notification_session.delivery_events,
            event_type="delivery_ping",
            status_value=notification_session.status,
            payload={
                "channel": str(data.get("channel") or "").strip(),
                "note": str(data.get("note") or "").strip(),
                "sent": bool(data.get("sent", False)),
                "failed": bool(data.get("failed", False)),
                "next_run_at": notification_session.next_run_at.isoformat() if notification_session.next_run_at else None,
                "payload": payload_data,
            },
        )
        notification_session.save()

        engine_session = notification_session.engine_session
        if notification_session.status == NotificationReminderStatus.COMPLETED:
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "confirm_delivery"
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"source": "delivery_ping", "status": "completed"},
            )

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "notification_session_id": str(notification_session.id),
                "notification_status": notification_session.status,
                "notification_next_run_at": notification_session.next_run_at.isoformat() if notification_session.next_run_at else "",
                "notification_sent_count": int(notification_session.sent_count or 0),
                "notification_failed_count": int(notification_session.failed_count or 0),
                "last_delivery_at": notification_session.last_delivery_at.isoformat() if notification_session.last_delivery_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "notification_session": NotificationReminderSessionSerializer(notification_session).data,
            "delivery_events": notification_session.delivery_events[-50:] if isinstance(notification_session.delivery_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 15},
        }
        return Response(payload, status=status.HTTP_200_OK)


class NotificationReminderSessionEndView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, notification_session_id):
        notification_session = get_object_or_404(
            NotificationReminderSession.objects.select_related("engine_session__engine_map__engine", "workflow_session", "institution"),
            id=notification_session_id,
        )
        workflow = notification_session.workflow_session
        if not _can_access_workflow_session(request.user, workflow):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if notification_session.status in {
            NotificationReminderStatus.COMPLETED,
            NotificationReminderStatus.DISABLED,
            NotificationReminderStatus.CANCELLED,
        }:
            return Response({"detail": "This notification session has already ended."}, status=status.HTTP_409_CONFLICT)

        serializer = NotificationReminderEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_value = str(serializer.validated_data.get("status") or NotificationReminderStatus.COMPLETED)
        summary = str(serializer.validated_data.get("summary") or "").strip()
        metadata_payload = serializer.validated_data.get("metadata", {})

        now_value = timezone.now()
        next_meta = notification_session.metadata if isinstance(notification_session.metadata, dict) else {}
        if summary:
            next_meta["closure_summary"] = summary
        if isinstance(metadata_payload, dict) and metadata_payload:
            next_meta.update(metadata_payload)
        notification_session.metadata = next_meta
        notification_session.ended_at = now_value

        engine_session = notification_session.engine_session
        if status_value in {NotificationReminderStatus.CANCELLED, NotificationReminderStatus.DISABLED}:
            notification_session.status = status_value
            engine_session.is_paused = True
            engine_session.save(update_fields=["is_paused", "updated_at"])
            if workflow.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
                workflow.status = WorkflowStatus.PAUSED
                workflow.save(update_fields=["status", "updated_at"])
        else:
            notification_session.status = NotificationReminderStatus.COMPLETED
            step_keys = _list_engine_step_keys(engine_session)
            final_step_key = step_keys[-1] if step_keys else "confirm_delivery"
            next_steps = notification_session.step_state if isinstance(notification_session.step_state, dict) else {}
            next_steps[final_step_key] = {
                "is_completed": True,
                "completed_at": now_value.isoformat(),
                "payload": {"summary": summary},
            }
            notification_session.step_state = next_steps
            workflow, engine_session = _apply_engine_step_update(
                workflow=workflow,
                engine_session=engine_session,
                step_key=final_step_key,
                is_completed=True,
                payload={"summary": summary},
            )

        notification_session.last_delivery_at = now_value
        notification_session.delivery_events = _append_tracking_event(
            notification_session.delivery_events,
            event_type="session_end",
            status_value=notification_session.status,
            payload={"status": notification_session.status, "summary": summary},
        )
        notification_session.save()

        next_state = engine_session.state_blob if isinstance(engine_session.state_blob, dict) else {}
        next_state.update(
            {
                "notification_session_id": str(notification_session.id),
                "notification_status": notification_session.status,
                "notification_next_run_at": notification_session.next_run_at.isoformat() if notification_session.next_run_at else "",
                "notification_sent_count": int(notification_session.sent_count or 0),
                "notification_failed_count": int(notification_session.failed_count or 0),
                "ended_at": notification_session.ended_at.isoformat() if notification_session.ended_at else "",
            }
        )
        engine_session.state_blob = next_state
        engine_session.save(update_fields=["state_blob", "updated_at"])

        payload = {
            "notification_session": NotificationReminderSessionSerializer(notification_session).data,
            "delivery_events": notification_session.delivery_events[-50:] if isinstance(notification_session.delivery_events, list) else [],
            "engine_session": EngineSessionSerializer(engine_session).data,
            "workflow_session": ServiceWorkflowSessionSerializer(workflow).data,
            "transport": "polling",
            "polling": {"recommended_interval_seconds": 15},
        }
        return Response(payload, status=status.HTTP_200_OK)


class EngineContentBlockListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, engine_id):
        engine = get_object_or_404(EngineRegistry, id=engine_id)
        rows = EngineContentBlock.objects.filter(engine=engine).order_by("order", "created_at")
        return Response({"results": EngineContentBlockSerializer(rows, many=True).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request, engine_id):
        engine = get_object_or_404(EngineRegistry, id=engine_id)
        payload = dict(request.data)
        payload["engine"] = str(engine.id)
        serializer = EngineContentBlockSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        block = serializer.save(created_by=request.user)
        return Response({"content_block": EngineContentBlockSerializer(block).data}, status=status.HTTP_201_CREATED)


class EngineContentBlockDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, engine_id, content_block_id):
        block = get_object_or_404(EngineContentBlock, id=content_block_id, engine_id=engine_id)
        if block.created_by_id != request.user.id:
            return Response({"detail": "Only creator can edit this block."}, status=status.HTTP_403_FORBIDDEN)
        serializer = EngineContentBlockSerializer(block, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save(version=block.version + 1)
        return Response({"content_block": EngineContentBlockSerializer(updated).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def delete(self, request, engine_id, content_block_id):
        block = get_object_or_404(EngineContentBlock, id=content_block_id, engine_id=engine_id)
        if block.created_by_id != request.user.id:
            return Response({"detail": "Only creator can delete this block."}, status=status.HTTP_403_FORBIDDEN)
        block.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _normalize_managed_item_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw_payload or {})
    alias_map = {
        "item_kind": "itemKind",
        "amount_micro": "amountMicro",
        "amount_kisc_input": "amountKisc",
        "value_int": "valueInt",
        "value_date": "valueDate",
        "image_url": "imageUrl",
        "sort_order": "sortOrder",
        "is_active": "isActive",
        "parent": "parentId",
        "engine_name": "engineName",
    }
    for canonical, alias in alias_map.items():
        if canonical not in payload and alias in payload:
            payload[canonical] = payload.get(alias)
    return payload


class InstitutionEngineManagedItemListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, institution_id, engine_key):
        institution, error_response = _resolve_institution_for_request(
            request.user,
            institution_id,
            allow_bootstrap=True,
        )
        if error_response:
            return error_response

        normalized_engine_key = _normalize_engine_key(engine_key)
        if not normalized_engine_key:
            return Response({"detail": "engine_key is required."}, status=status.HTTP_400_BAD_REQUEST)
        if _is_restricted_managed_engine_key(normalized_engine_key):
            return Response(
                {"detail": "This engine is coming up and cannot be managed yet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        rows = InstitutionEngineManagedItem.objects.filter(
            institution=institution,
            engine_key=normalized_engine_key,
        )
        item_kind = str(request.query_params.get("item_kind") or "").strip().lower()
        if item_kind:
            rows = rows.filter(item_kind=item_kind)
        parent_id = str(request.query_params.get("parent_id") or "").strip()
        if parent_id:
            rows = rows.filter(parent_id=parent_id)
        root_only = str(request.query_params.get("root_only") or "").strip().lower() in {"1", "true", "yes"}
        if root_only:
            rows = rows.filter(parent__isnull=True)
        include_inactive = str(request.query_params.get("include_inactive") or "").strip().lower() in {"1", "true", "yes"}
        if not include_inactive:
            rows = rows.filter(is_active=True)

        rows = rows.select_related("created_by", "updated_by", "parent").order_by("sort_order", "created_at")
        return Response({"results": InstitutionEngineManagedItemSerializer(rows, many=True).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request, institution_id, engine_key):
        institution, error_response = _resolve_institution_for_request(
            request.user,
            institution_id,
            allow_bootstrap=True,
        )
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        normalized_engine_key = _normalize_engine_key(engine_key)
        if not normalized_engine_key:
            return Response({"detail": "engine_key is required."}, status=status.HTTP_400_BAD_REQUEST)
        if _is_restricted_managed_engine_key(normalized_engine_key):
            return Response(
                {"detail": "This engine is coming up and cannot be managed yet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        payload = _normalize_managed_item_payload(request.data if isinstance(request.data, dict) else {})
        payload["institution"] = str(institution.id)
        payload["engine_key"] = normalized_engine_key
        payload["engine_name"] = str(payload.get("engine_name") or normalized_engine_key).strip()
        payload["item_kind"] = str(payload.get("item_kind") or "").strip().lower()

        if not payload["item_kind"]:
            return Response({"detail": "item_kind is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not payload.get("sort_order"):
            existing_last = (
                InstitutionEngineManagedItem.objects.filter(
                    institution=institution,
                    engine_key=normalized_engine_key,
                    item_kind=payload["item_kind"],
                    parent_id=payload.get("parent"),
                )
                .order_by("-sort_order")
                .first()
            )
            payload["sort_order"] = int(getattr(existing_last, "sort_order", 0) or 0) + 1

        serializer = InstitutionEngineManagedItemSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        parent = serializer.validated_data.get("parent")
        if parent and (
            parent.institution_id != institution.id or _normalize_engine_key(parent.engine_key) != normalized_engine_key
        ):
            return Response(
                {"detail": "Parent item must belong to the same institution and engine."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        item = serializer.save(created_by=request.user, updated_by=request.user)
        return Response({"item": InstitutionEngineManagedItemSerializer(item).data}, status=status.HTTP_201_CREATED)


class InstitutionEngineManagedItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, institution_id, engine_key, item_id):
        institution, error_response = _resolve_institution_for_request(
            request.user,
            institution_id,
            allow_bootstrap=True,
        )
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        normalized_engine_key = _normalize_engine_key(engine_key)
        if _is_restricted_managed_engine_key(normalized_engine_key):
            return Response(
                {"detail": "This engine is coming up and cannot be managed yet."},
                status=status.HTTP_403_FORBIDDEN,
            )
        item = get_object_or_404(
            InstitutionEngineManagedItem.objects.select_related("institution", "parent"),
            id=item_id,
            institution=institution,
            engine_key=normalized_engine_key,
        )
        payload = _normalize_managed_item_payload(request.data if isinstance(request.data, dict) else {})
        serializer = InstitutionEngineManagedItemSerializer(item, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        parent = serializer.validated_data.get("parent")
        if parent and (
            parent.institution_id != institution.id or _normalize_engine_key(parent.engine_key) != normalized_engine_key
        ):
            return Response(
                {"detail": "Parent item must belong to the same institution and engine."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        updated_item = serializer.save(updated_by=request.user)
        return Response({"item": InstitutionEngineManagedItemSerializer(updated_item).data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def delete(self, request, institution_id, engine_key, item_id):
        institution, error_response = _resolve_institution_for_request(
            request.user,
            institution_id,
            allow_bootstrap=True,
        )
        if error_response:
            return error_response
        if not _can_manage_institution(request.user, institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        normalized_engine_key = _normalize_engine_key(engine_key)
        if _is_restricted_managed_engine_key(normalized_engine_key):
            return Response(
                {"detail": "This engine is coming up and cannot be managed yet."},
                status=status.HTTP_403_FORBIDDEN,
            )
        item = get_object_or_404(
            InstitutionEngineManagedItem,
            id=item_id,
            institution=institution,
            engine_key=normalized_engine_key,
        )
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
