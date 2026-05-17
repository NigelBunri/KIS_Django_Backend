from __future__ import annotations

import json
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.billing.direct_payments import redact_payment_payload
from apps.billing.models import DirectPaymentAuditEvent, DirectPaymentIntent


LEGACY_DISABLED_FLAGS = [
    "KIS_LEGACY_WALLET_DEPOSIT_ENABLED",
    "KIS_LEGACY_WALLET_TRANSFER_ENABLED",
    "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED",
    "KIS_LEGACY_WALLET_UPGRADE_ENABLED",
    "KIS_LEGACY_PROMO_CASH_BONUS_ENABLED",
    "KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED",
]

MONETIZATION_DISABLED_FLAGS = [
    "KIS_PROFITABILITY_BILLING_ENABLED",
    "KIS_PROFITABILITY_ENTITLEMENTS_ENFORCED",
    "KIS_PROFITABILITY_TRIALS_ENABLED",
    "KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED",
    "KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED",
]


def _setting_bool(name: str) -> bool:
    return bool(getattr(settings, name, False))


def _setting_text(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def _redaction_self_test() -> bool:
    payload = {
        "secret": "never-print",
        "token": "never-print",
        "customer_phone": "+15555555555",
        "nested": {"authorization": "Bearer secret", "safe": "ok"},
        "items": [{"cvv": "123", "amount": 100}],
    }
    redacted = redact_payment_payload(payload)
    return (
        redacted["secret"] == "[redacted]"
        and redacted["token"] == "[redacted]"
        and redacted["customer_phone"] == "[redacted]"
        and redacted["nested"]["authorization"] == "[redacted]"
        and redacted["nested"]["safe"] == "ok"
        and redacted["items"][0]["cvv"] == "[redacted]"
    )


class Command(BaseCommand):
    help = "Verify non-secret USD-only payment launch guardrails without making live provider calls."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query direct payment intent/audit counts.")

    def handle(self, *args, **options):
        direct_links_enabled = _setting_bool("KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED")
        flw_secret_present = bool(_setting_text("FLW_SECRET_KEY"))
        flw_webhook_secret_present = bool(_setting_text("FLW_WEBHOOK_SECRET"))
        flw_redirect_url = _setting_text("FLW_REDIRECT_URL")
        parsed_redirect = urlparse(flw_redirect_url) if flw_redirect_url else None
        api_base_url = _setting_text("API_BASE_URL")
        webhook_url = urljoin(f"{api_base_url.rstrip('/')}/", "api/v1/direct-payments/webhook/flutterwave/") if api_base_url else "/api/v1/direct-payments/webhook/flutterwave/"

        checks: list[dict[str, str]] = []

        for flag in LEGACY_DISABLED_FLAGS:
            checks.append(
                {
                    "name": flag,
                    "state": "pass" if not _setting_bool(flag) else "fail",
                    "detail": "disabled" if not _setting_bool(flag) else "must be disabled for USD-only launch",
                }
            )

        for flag in MONETIZATION_DISABLED_FLAGS:
            checks.append(
                {
                    "name": flag,
                    "state": "pass" if not _setting_bool(flag) else "fail",
                    "detail": "disabled" if not _setting_bool(flag) else "must remain disabled until monetization approval",
                }
            )

        for name in [
            "KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER",
            "KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER",
            "KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER",
        ]:
            value = _setting_text(name).lower()
            checks.append(
                {
                    "name": name,
                    "state": "pass" if value == "flutterwave" else "warn",
                    "detail": value or "not configured",
                }
            )

        checks.extend(
            [
                {
                    "name": "PAYMENTS_MOCK",
                    "state": "pass" if not _setting_bool("PAYMENTS_MOCK") else "fail",
                    "detail": "disabled" if not _setting_bool("PAYMENTS_MOCK") else "mock payments must not be enabled for launch proof",
                },
                {
                    "name": "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED",
                    "state": "warn" if direct_links_enabled else "pass",
                    "detail": "provider link creation enabled; confirm staging evidence and production approval" if direct_links_enabled else "disabled by default",
                },
                {
                    "name": "FLW_SECRET_KEY",
                    "state": "pass" if not direct_links_enabled or flw_secret_present else "fail",
                    "detail": "presence checked only; value is never printed",
                },
                {
                    "name": "FLW_WEBHOOK_SECRET",
                    "state": "pass" if not direct_links_enabled or flw_webhook_secret_present else "fail",
                    "detail": "presence checked only; value is never printed",
                },
                {
                    "name": "FLW_REDIRECT_URL",
                    "state": "pass" if flw_redirect_url and (parsed_redirect.scheme == "https" or not direct_links_enabled) else "fail",
                    "detail": "configured with HTTPS for provider returns" if parsed_redirect and parsed_redirect.scheme == "https" else "must be HTTPS when provider links are enabled",
                },
                {
                    "name": "payment_payload_redaction",
                    "state": "pass" if _redaction_self_test() else "fail",
                    "detail": "sensitive provider fields are redacted before audit storage",
                },
            ]
        )

        counts = {"pending_intents": None, "paid_intents": None, "audit_events": None}
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "pending_intents": DirectPaymentIntent.objects.filter(status=DirectPaymentIntent.STATUS_PENDING).count(),
                    "paid_intents": DirectPaymentIntent.objects.filter(status=DirectPaymentIntent.STATUS_PAID).count(),
                    "audit_events": DirectPaymentAuditEvent.objects.count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append(
                    {
                        "name": "direct_payment_database_counts",
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
            "callback_url": webhook_url,
            "counts": counts,
            "count_error": count_error,
            "notes": [
                "This command does not make live payment provider calls.",
                "No secret values, raw callbacks, payment instruments, or provider payloads are printed.",
                "KIS promotional credits must remain non-cash, non-transferable, non-withdrawable, and not exchange-rated.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Payment launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            self.stdout.write(f"Flutterwave direct-payment webhook URL: {webhook_url}")
            if options["include_counts"]:
                self.stdout.write(f"Intent/audit counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Payment launch guardrails failed: {len(failures)} blocker(s).")
