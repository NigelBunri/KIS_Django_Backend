from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import NoReverseMatch, reverse

from apps.verification.constants import VerificationSubjectType
from apps.verification.models import VerificationAuditEvent, VerificationBadge, VerificationCase
from apps.verification.providers import provider_public_status, redact_provider_payload


REQUIRED_SUBJECT_TYPES = {
    VerificationSubjectType.USER,
    VerificationSubjectType.SHOP,
    VerificationSubjectType.PARTNER,
    VerificationSubjectType.HEALTH_INSTITUTION,
    VerificationSubjectType.EDUCATION_INSTITUTION,
}

POST_LAUNCH_SUBJECT_TYPES = {
    "channel_creator": "Channel/creator verification is not a first-launch subject type yet.",
    "publisher": "Bible/KCAN publisher verification should map to partner or a future publisher subject type.",
}

STAFF_URL_NAMES = [
    "verification:staff-cases",
    "verification:staff-badge-issue",
    "verification:staff-audit-events",
    "verification:staff-provider-callbacks",
    "verification:staff-suspicious-signals",
    "verification:staff-expiry-reminders",
]

PUBLIC_URL_NAMES = [
    "verification:user-status",
    "verification:user-start",
    "verification:trust-overview",
]


def _setting_bool(name: str) -> bool:
    return bool(getattr(settings, name, False))


def _setting_text(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def _redaction_self_test() -> bool:
    payload = {
        "secret": "provider-secret",
        "token": "provider-token",
        "document_base64": "data:image/png;base64,secret",
        "provider": {"applicant_id": "private-applicant", "safe": "ok"},
        "items": [{"passport": "raw-passport", "status": "approved"}],
    }
    redacted = redact_provider_payload(payload)
    serialized = json.dumps(redacted)
    return (
        "[redacted]" in serialized
        and "provider-secret" not in serialized
        and "provider-token" not in serialized
        and "private-applicant" not in serialized
        and "raw-passport" not in serialized
        and redacted["provider"]["safe"] == "ok"
    )


def _reverse_exists(name: str) -> bool:
    try:
        reverse(name)
    except NoReverseMatch:
        return False
    return True


class Command(BaseCommand):
    help = "Verify non-secret verification/trust-badge launch guardrails without making provider calls."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query verification case/badge/audit counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        checks.append(
            {
                "name": "VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED",
                "state": "fail" if _setting_bool("VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED") else "pass",
                "detail": "disabled by default" if not _setting_bool("VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED") else "must remain disabled for launch proof",
            }
        )
        checks.append(
            {
                "name": "VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED",
                "state": "warn" if _setting_bool("VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED") else "pass",
                "detail": "disabled; no sandbox network calls are made" if not _setting_bool("VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED") else "sandbox network enabled; use only with approved staging credentials",
            }
        )
        checks.append(
            {
                "name": "VERIFICATION_WEBHOOK_SECRET",
                "state": "pass" if bool(_setting_text("VERIFICATION_WEBHOOK_SECRET")) else "warn",
                "detail": "presence checked only; value is never printed" if _setting_text("VERIFICATION_WEBHOOK_SECRET") else "configure for staging/production signed callback proof",
            }
        )
        checks.append(
            {
                "name": "provider_payload_redaction",
                "state": "pass" if _redaction_self_test() else "fail",
                "detail": "provider secrets/raw document fields are redacted before staff serialization/logging",
            }
        )

        configured_providers = []
        for provider in ("dojah", "sumsub", "smile_id"):
            status = provider_public_status(provider)
            configured_providers.append(provider if status.get("configured") else "")
            checks.append(
                {
                    "name": f"provider.{provider}.live_calls",
                    "state": "fail" if status.get("live_calls_enabled") else "pass",
                    "detail": "disabled" if not status.get("live_calls_enabled") else "live calls enabled; block launch unless explicitly approved",
                }
            )

        missing_url_names = [name for name in [*STAFF_URL_NAMES, *PUBLIC_URL_NAMES] if not _reverse_exists(name)]
        checks.append(
            {
                "name": "verification_urls_present",
                "state": "pass" if not missing_url_names else "fail",
                "detail": "required verification URLs resolve" if not missing_url_names else f"missing: {', '.join(missing_url_names)}",
            }
        )

        model_subject_types = {choice[0] for choice in VerificationSubjectType.CHOICES}
        missing_subject_types = sorted(REQUIRED_SUBJECT_TYPES - model_subject_types)
        checks.append(
            {
                "name": "required_subject_types",
                "state": "pass" if not missing_subject_types else "fail",
                "detail": "user/shop/partner/health/education subject types are present" if not missing_subject_types else f"missing: {', '.join(missing_subject_types)}",
            }
        )
        for name, detail in POST_LAUNCH_SUBJECT_TYPES.items():
            checks.append(
                {
                    "name": f"post_launch_subject_type.{name}",
                    "state": "warn",
                    "detail": detail,
                }
            )

        counts = {"cases": None, "active_badges": None, "revoked_badges": None, "audit_events": None}
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "cases": VerificationCase.objects.count(),
                    "active_badges": VerificationBadge.objects.filter(status="active").count(),
                    "revoked_badges": VerificationBadge.objects.filter(status="revoked").count(),
                    "audit_events": VerificationAuditEvent.objects.count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append(
                    {
                        "name": "verification_database_counts",
                        "state": "warn",
                        "detail": f"database summary unavailable: {count_error}",
                    }
                )

        failures = [check for check in checks if check["state"] == "fail"]
        warnings = [check for check in checks if check["state"] == "warn"]
        result = {
            "ready": not failures,
            "summary": {
                "checks": len(checks),
                "failures": len(failures),
                "warnings": len(warnings),
            },
            "checks": checks,
            "counts": counts,
            "count_error": count_error,
            "notes": [
                "This command does not make live verification provider calls.",
                "No provider secrets, raw documents, private media ids, or raw provider payloads are printed.",
                "Channel/creator and publisher trust should use existing partner/user trust summaries until dedicated subject types are approved.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Verification launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Verification counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Verification launch guardrails failed: {len(failures)} blocker(s).")
