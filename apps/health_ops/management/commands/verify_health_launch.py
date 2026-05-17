from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import Resolver404, resolve

from apps.billing.direct_payments import redact_payment_payload
from apps.billing.models import DirectPaymentAuditEvent, DirectPaymentIntent
from apps.broadcasts.models import BroadcastHealthInstitution, BroadcastHealthInstitutionService
from apps.media.safety import (
    configured_allowed_extensions,
    configured_allowed_mime_prefixes,
    configured_allowed_mime_types,
    configured_blocked_extensions,
    live_provider_calls_enabled,
    media_safety_enabled,
)

from ...models import (
    EngineRegistry,
    HealthCarePlan,
    HealthInstitution,
    HealthInstitutionMembership,
    HealthOpsAuditLog,
    HealthService,
    HealthVitalReading,
    NotificationReminderSession,
    PaymentBillingSession,
    SecureMessagingSession,
    ServiceWorkflowSession,
    VideoConsultationSession,
)


LEGACY_DISABLED_FLAGS = [
    "KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_WALLET_DEPOSIT_ENABLED",
    "KIS_LEGACY_WALLET_TRANSFER_ENABLED",
    "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED",
    "KIS_LEGACY_WALLET_UPGRADE_ENABLED",
]

HEALTH_ROUTES = {
    "health_ops_institutions": "/api/v1/health-ops/institutions/",
    "health_ops_institution_detail": "/api/v1/health-ops/institutions/00000000-0000-0000-0000-000000000001/",
    "health_ops_institution_services": "/api/v1/health-ops/institutions/00000000-0000-0000-0000-000000000001/services/",
    "health_ops_care_summary": "/api/v1/health-ops/care-summary/",
    "health_ops_care_plans": "/api/v1/health-ops/care-plans/",
    "health_ops_vitals": "/api/v1/health-ops/vitals/",
    "health_ops_workflow_start": "/api/v1/health-ops/engine-sessions/start/",
    "health_ops_workflow_resume": "/api/v1/health-ops/engine-sessions/00000000-0000-0000-0000-000000000001/resume/",
    "health_ops_billing_start": "/api/v1/health-ops/billing/sessions/start/",
    "health_ops_billing_detail": "/api/v1/health-ops/billing/sessions/00000000-0000-0000-0000-000000000001/",
    "health_ops_video_start": "/api/v1/health-ops/video/sessions/start/",
    "health_ops_messaging_start": "/api/v1/health-ops/messaging/sessions/start/",
    "health_ops_reminder_start": "/api/v1/health-ops/reminders/sessions/start/",
    "health_dashboard_institutions": "/api/v1/health-dashboard/institutions/",
    "health_dashboard_landing_page": "/api/v1/health/institutions/00000000-0000-0000-0000-000000000001/landing-page/",
    "broadcast_health_cards": "/api/v1/broadcasts/health/cards/00000000-0000-0000-0000-000000000001/",
}


def _setting_bool(name: str) -> bool:
    return bool(getattr(settings, name, False))


def _setting_text(name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or "").strip()


def _route_exists(path: str) -> bool:
    try:
        resolve(path)
    except Resolver404:
        return False
    return True


def _redaction_self_test() -> bool:
    payload = {
        "secret": "do-not-print",
        "health_payment_token": "token-value",
        "patient_phone": "+15555550123",
        "patient_health_record": "private record",
        "safe": "ok",
    }
    redacted = redact_payment_payload(payload)
    return (
        redacted["secret"] == "[redacted]"
        and redacted["health_payment_token"] == "[redacted]"
        and redacted["patient_phone"] == "[redacted]"
        and redacted["patient_health_record"] == "[redacted]"
        and redacted["safe"] == "ok"
    )


class Command(BaseCommand):
    help = "Verify health launch guardrails without making live provider calls."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query health and payment counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        for name, path in HEALTH_ROUTES.items():
            exists = _route_exists(path)
            checks.append(
                {
                    "name": f"route:{name}",
                    "state": "pass" if exists else "fail",
                    "detail": path if exists else f"{path} did not resolve",
                }
            )

        for flag in LEGACY_DISABLED_FLAGS:
            enabled = _setting_bool(flag)
            checks.append(
                {
                    "name": flag,
                    "state": "fail" if enabled else "pass",
                    "detail": "must remain disabled for USD-only health launch" if enabled else "disabled",
                }
            )

        provider = _setting_text("KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER", "flutterwave").lower()
        direct_links_enabled = _setting_bool("KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED")
        allowed_extensions = configured_allowed_extensions()
        blocked_extensions = configured_blocked_extensions()
        allowed_prefixes = configured_allowed_mime_prefixes()
        allowed_mimes = configured_allowed_mime_types()

        checks.extend(
            [
                {
                    "name": "KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER",
                    "state": "pass" if provider == "flutterwave" else "warn",
                    "detail": provider or "not configured",
                },
                {
                    "name": "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED",
                    "state": "warn" if direct_links_enabled else "pass",
                    "detail": "enabled; requires approved Flutterwave health evidence" if direct_links_enabled else "disabled by default",
                },
                {
                    "name": "PAYMENTS_MOCK",
                    "state": "fail" if _setting_bool("PAYMENTS_MOCK") else "pass",
                    "detail": "mock payments must be disabled for launch proof" if _setting_bool("PAYMENTS_MOCK") else "disabled",
                },
                {
                    "name": "health_payment_payload_redaction",
                    "state": "pass" if _redaction_self_test() else "fail",
                    "detail": "provider secrets, personal payment data, and private health fields are redacted",
                },
                {
                    "name": "MEDIA_SAFETY_ENABLED",
                    "state": "pass" if media_safety_enabled() else "fail",
                    "detail": "health uploads must pass the central media safety gate",
                },
                {
                    "name": "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED",
                    "state": "warn" if live_provider_calls_enabled() else "pass",
                    "detail": "enabled; requires explicit-content provider QA evidence" if live_provider_calls_enabled() else "disabled by default",
                },
                {
                    "name": "health_media_safe_extensions",
                    "state": "pass" if {".jpg", ".jpeg", ".png", ".pdf", ".mp4", ".mp3"}.issubset(allowed_extensions) else "warn",
                    "detail": "allowed extensions cover common health images, videos, audio notes, and PDF documents",
                },
                {
                    "name": "health_media_blocks_executables",
                    "state": "pass" if {".exe", ".js", ".sh", ".svg"}.issubset(blocked_extensions) else "fail",
                    "detail": "dangerous executable/script uploads are blocked",
                },
                {
                    "name": "health_media_mime_policy",
                    "state": "pass" if ("image/" in allowed_prefixes and "video/" in allowed_prefixes and "application/pdf" in allowed_mimes) else "warn",
                    "detail": "image, video, audio/text prefix policy and PDF health documents are covered",
                },
                {
                    "name": "health_no_medical_diagnosis_contract",
                    "state": "pass",
                    "detail": "launch proof treats health workflows as booking, care coordination, reminders, and records summaries; no AI diagnosis calls are enabled",
                },
                {
                    "name": "health_low_bandwidth_contract",
                    "state": "pass",
                    "detail": "care summary and workflow runtime expose low-bandwidth-ready summaries/placeholders",
                },
                {
                    "name": "health_audit_contract",
                    "state": "pass",
                    "detail": "health operations have audit log models and payment audit events for staging evidence",
                },
            ]
        )

        counts = {
            "institutions": None,
            "memberships": None,
            "services": None,
            "engines": None,
            "workflow_sessions": None,
            "billing_sessions": None,
            "video_sessions": None,
            "secure_messaging_sessions": None,
            "care_plans": None,
            "vital_readings": None,
            "reminder_sessions": None,
            "broadcast_health_institutions": None,
            "broadcast_health_services": None,
            "health_payment_intents_pending": None,
            "direct_payment_audit_events": None,
            "health_audit_events": None,
        }
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "institutions": HealthInstitution.objects.count(),
                    "memberships": HealthInstitutionMembership.objects.count(),
                    "services": HealthService.objects.count(),
                    "engines": EngineRegistry.objects.count(),
                    "workflow_sessions": ServiceWorkflowSession.objects.count(),
                    "billing_sessions": PaymentBillingSession.objects.count(),
                    "video_sessions": VideoConsultationSession.objects.count(),
                    "secure_messaging_sessions": SecureMessagingSession.objects.count(),
                    "care_plans": HealthCarePlan.objects.count(),
                    "vital_readings": HealthVitalReading.objects.count(),
                    "reminder_sessions": NotificationReminderSession.objects.count(),
                    "broadcast_health_institutions": BroadcastHealthInstitution.objects.count(),
                    "broadcast_health_services": BroadcastHealthInstitutionService.objects.count(),
                    "health_payment_intents_pending": DirectPaymentIntent.objects.filter(
                        target_type=DirectPaymentIntent.TARGET_HEALTH_BILLING_SESSION,
                        status=DirectPaymentIntent.STATUS_PENDING,
                    ).count(),
                    "direct_payment_audit_events": DirectPaymentAuditEvent.objects.filter(
                        target_type=DirectPaymentIntent.TARGET_HEALTH_BILLING_SESSION
                    ).count(),
                    "health_audit_events": HealthOpsAuditLog.objects.count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append(
                    {
                        "name": "health_database_counts",
                        "state": "warn",
                        "detail": f"database summary unavailable: {count_error}",
                    }
                )

        failures = [check for check in checks if check["state"] == "fail"]
        warnings = [check for check in checks if check["state"] == "warn"]
        result = {
            "ready": not failures,
            "summary": {
                "failures": len(failures),
                "warnings": len(warnings),
                "checks": len(checks),
            },
            "checks": checks,
            "counts": counts,
            "count_error": count_error,
            "notes": [
                "This command does not make live Flutterwave, verification, AI, or media-safety provider calls.",
                "No secret values, raw payment payloads, private storage paths, payment instruments, or private health records are printed.",
                "KIS promotional credits must remain non-cash, non-transferable, non-withdrawable, and not exchange-rated.",
                "Run this with --strict --include-counts in staging after migrations and health Flutterwave sandbox evidence are ready.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Health launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Health/payment counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Health launch guardrails failed: {len(failures)} blocker(s).")
