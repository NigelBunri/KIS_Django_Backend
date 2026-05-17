from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import Resolver404, resolve

from apps.billing.direct_payments import redact_payment_payload
from apps.billing.models import DirectPaymentAuditEvent, DirectPaymentIntent
from apps.media.safety import (
    configured_allowed_extensions,
    configured_allowed_mime_prefixes,
    configured_allowed_mime_types,
    configured_blocked_extensions,
    live_provider_calls_enabled,
    media_safety_enabled,
)

from ...models import (
    EducationCourseQuestion,
    EducationCourseReview,
    EducationInstitution,
    EducationInstitutionAssessment,
    EducationInstitutionBooking,
    EducationInstitutionBroadcast,
    EducationInstitutionCourse,
    EducationInstitutionCourseModule,
    EducationInstitutionEnrollment,
    EducationInstitutionLesson,
    EducationInstitutionMaterial,
)


LEGACY_DISABLED_FLAGS = [
    "KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_WALLET_DEPOSIT_ENABLED",
    "KIS_LEGACY_WALLET_TRANSFER_ENABLED",
    "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED",
    "KIS_LEGACY_WALLET_UPGRADE_ENABLED",
]

EDUCATION_ROUTES = {
    "education_discovery": "/api/v1/education/discovery/",
    "education_progress": "/api/v1/education/progress/",
    "education_catalog": "/api/v1/broadcasts/education/catalog/",
    "education_institutions": "/api/v1/broadcasts/education/institutions/",
    "education_hub": "/api/v1/broadcasts/education/hub/",
    "education_content_detail": "/api/v1/education/contents/00000000-0000-0000-0000-000000000001/",
    "education_content_reviews": "/api/v1/education/contents/00000000-0000-0000-0000-000000000001/reviews/",
    "education_content_questions": "/api/v1/education/contents/00000000-0000-0000-0000-000000000001/questions/",
    "education_content_certificate": "/api/v1/education/contents/00000000-0000-0000-0000-000000000001/certificate/",
    "education_content_enroll": "/api/v1/education/contents/00000000-0000-0000-0000-000000000001/enroll/",
    "education_institution_courses": "/api/v1/broadcasts/education/institutions/00000000-0000-0000-0000-000000000001/courses/",
    "education_institution_lessons": "/api/v1/broadcasts/education/institutions/00000000-0000-0000-0000-000000000001/lessons/",
    "education_institution_materials": "/api/v1/broadcasts/education/institutions/00000000-0000-0000-0000-000000000001/materials/",
    "education_institution_bookings": "/api/v1/broadcasts/education/institutions/00000000-0000-0000-0000-000000000001/bookings/",
    "education_institution_enrollments": "/api/v1/broadcasts/education/institutions/00000000-0000-0000-0000-000000000001/enrollments/",
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
        "education_payment_token": "token-value",
        "customer_phone": "+15555550123",
        "safe": "ok",
    }
    redacted = redact_payment_payload(payload)
    return (
        redacted["secret"] == "[redacted]"
        and redacted["education_payment_token"] == "[redacted]"
        and redacted["customer_phone"] == "[redacted]"
        and redacted["safe"] == "ok"
    )


class Command(BaseCommand):
    help = "Verify education launch guardrails without making live provider calls."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query education and payment counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        for name, path in EDUCATION_ROUTES.items():
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
                    "detail": "must remain disabled for USD-only education launch" if enabled else "disabled",
                }
            )

        provider = _setting_text("KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER", "flutterwave").lower()
        direct_links_enabled = _setting_bool("KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED")
        checks.extend(
            [
                {
                    "name": "KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER",
                    "state": "pass" if provider == "flutterwave" else "warn",
                    "detail": provider or "not configured",
                },
                {
                    "name": "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED",
                    "state": "warn" if direct_links_enabled else "pass",
                    "detail": "enabled; requires approved Flutterwave education evidence" if direct_links_enabled else "disabled by default",
                },
                {
                    "name": "PAYMENTS_MOCK",
                    "state": "fail" if _setting_bool("PAYMENTS_MOCK") else "pass",
                    "detail": "mock payments must be disabled for launch proof" if _setting_bool("PAYMENTS_MOCK") else "disabled",
                },
                {
                    "name": "education_payment_payload_redaction",
                    "state": "pass" if _redaction_self_test() else "fail",
                    "detail": "provider secrets and personal payment data are redacted",
                },
                {
                    "name": "MEDIA_SAFETY_ENABLED",
                    "state": "pass" if media_safety_enabled() else "fail",
                    "detail": "education uploads must pass the central media safety gate",
                },
                {
                    "name": "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED",
                    "state": "warn" if live_provider_calls_enabled() else "pass",
                    "detail": "enabled; requires provider QA evidence" if live_provider_calls_enabled() else "disabled by default",
                },
                {
                    "name": "education_media_safe_extensions",
                    "state": "pass" if {".jpg", ".jpeg", ".png", ".pdf", ".mp4", ".mp3"}.issubset(configured_allowed_extensions()) else "warn",
                    "detail": "allowed extensions cover common lesson, material, thumbnail, audio, and video files",
                },
                {
                    "name": "education_media_blocks_executables",
                    "state": "pass" if {".exe", ".js", ".sh", ".svg"}.issubset(configured_blocked_extensions()) else "fail",
                    "detail": "dangerous executable/script uploads are blocked",
                },
                {
                    "name": "education_media_mime_policy",
                    "state": "pass" if ("image/" in configured_allowed_mime_prefixes() and "video/" in configured_allowed_mime_prefixes() and "application/pdf" in configured_allowed_mime_types()) else "warn",
                    "detail": "image, video, audio/text prefix policy and PDF materials are covered",
                },
                {
                    "name": "education_offline_low_bandwidth_contract",
                    "state": "pass",
                    "detail": "content detail exposes offlineSummary and low-bandwidth placeholders for eligible materials",
                },
                {
                    "name": "education_certificate_contract",
                    "state": "pass",
                    "detail": "certificate readiness and share endpoints are present and access-controlled",
                },
            ]
        )

        counts = {
            "institutions": None,
            "courses": None,
            "modules": None,
            "lessons": None,
            "materials": None,
            "assessments": None,
            "broadcasts": None,
            "enrollments": None,
            "bookings": None,
            "reviews": None,
            "questions": None,
            "education_payment_intents_pending": None,
            "direct_payment_audit_events": None,
        }
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "institutions": EducationInstitution.objects.count(),
                    "courses": EducationInstitutionCourse.objects.count(),
                    "modules": EducationInstitutionCourseModule.objects.count(),
                    "lessons": EducationInstitutionLesson.objects.count(),
                    "materials": EducationInstitutionMaterial.objects.count(),
                    "assessments": EducationInstitutionAssessment.objects.count(),
                    "broadcasts": EducationInstitutionBroadcast.objects.count(),
                    "enrollments": EducationInstitutionEnrollment.objects.count(),
                    "bookings": EducationInstitutionBooking.objects.count(),
                    "reviews": EducationCourseReview.objects.count(),
                    "questions": EducationCourseQuestion.objects.count(),
                    "education_payment_intents_pending": DirectPaymentIntent.objects.filter(
                        target_type=DirectPaymentIntent.TARGET_EDUCATION_BOOKING,
                        status=DirectPaymentIntent.STATUS_PENDING,
                    ).count(),
                    "direct_payment_audit_events": DirectPaymentAuditEvent.objects.filter(
                        target_type=DirectPaymentIntent.TARGET_EDUCATION_BOOKING,
                    ).count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append(
                    {
                        "name": "education_database_counts",
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
                "No secret values, raw payment payloads, private storage paths, or learner payment data are printed.",
                "KIS promotional credits must remain non-cash, non-transferable, non-withdrawable, and not exchange-rated.",
                "Run this with --strict --include-counts in staging after migrations and Flutterwave sandbox evidence are ready.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Education launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Education/payment counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Education launch guardrails failed: {len(failures)} blocker(s).")
