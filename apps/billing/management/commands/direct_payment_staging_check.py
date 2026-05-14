from __future__ import annotations

import json
from urllib.parse import urljoin

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.billing.models import DirectPaymentIntent


def _present(value: object) -> bool:
    return bool(str(value or "").strip())


class Command(BaseCommand):
    help = "Print a non-secret staging readiness summary for direct USD payment intents."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON.",
        )
        parser.add_argument(
            "--include-counts",
            action="store_true",
            help="Query DirectPaymentIntent counts. This requires database access.",
        )

    def handle(self, *args, **options):
        django_env = str(getattr(settings, "DJANGO_ENV", "") or "").strip().lower()
        api_base_url = str(getattr(settings, "API_BASE_URL", "") or "").strip().rstrip("/")
        webhook_path = "/api/v1/direct-payments/webhook/flutterwave/"
        direct_payment_webhook_url = urljoin(f"{api_base_url}/", webhook_path.lstrip("/")) if api_base_url else webhook_path

        checks = {
            "django_env_is_staging": django_env == "staging",
            "direct_provider_links_enabled": bool(getattr(settings, "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED", False)),
            "flutterwave_secret_key_present": _present(getattr(settings, "FLW_SECRET_KEY", "")),
            "flutterwave_webhook_secret_present": _present(getattr(settings, "FLW_WEBHOOK_SECRET", "")),
            "flutterwave_redirect_url_present": _present(getattr(settings, "FLW_REDIRECT_URL", "")),
            "api_base_url_present": _present(api_base_url),
            "legacy_wallet_deposit_disabled": not bool(getattr(settings, "KIS_LEGACY_WALLET_DEPOSIT_ENABLED", False)),
            "legacy_wallet_transfer_disabled": not bool(getattr(settings, "KIS_LEGACY_WALLET_TRANSFER_ENABLED", False)),
            "legacy_commerce_wallet_checkout_disabled": not bool(getattr(settings, "KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED", False)),
            "legacy_education_wallet_checkout_disabled": not bool(getattr(settings, "KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED", False)),
            "legacy_health_wallet_checkout_disabled": not bool(getattr(settings, "KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED", False)),
        }
        ready = all(checks.values())
        counts = {"pending": None, "paid": None}
        count_error = ""
        if options.get("include_counts"):
            try:
                counts = {
                    "pending": DirectPaymentIntent.objects.filter(status=DirectPaymentIntent.STATUS_PENDING).count(),
                    "paid": DirectPaymentIntent.objects.filter(status=DirectPaymentIntent.STATUS_PAID).count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__

        result = {
            "ready_for_staging_provider_link_qa": ready,
            "checks": checks,
            "urls": {
                "direct_payment_flutterwave_webhook": direct_payment_webhook_url,
                "flutterwave_redirect_url_configured": bool(checks["flutterwave_redirect_url_present"]),
            },
            "direct_payment_intent_counts": counts,
            "direct_payment_intent_count_error": count_error,
            "notes": [
                "No secret values are printed by this command.",
                "Use only in staging with approved Flutterwave sandbox credentials before enabling production payment links.",
                "Provider dashboard callback URL should match direct_payment_flutterwave_webhook.",
            ],
        }

        if options.get("json"):
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            return

        self.stdout.write(f"Ready for staging provider-link QA: {ready}")
        for key, value in checks.items():
            marker = "PASS" if value else "BLOCKED"
            self.stdout.write(f"- {marker}: {key}")
        self.stdout.write(f"Direct payment Flutterwave webhook URL: {direct_payment_webhook_url}")
        self.stdout.write(f"Pending intents: {counts['pending']}")
        self.stdout.write(f"Paid intents: {counts['paid']}")
        if count_error:
            self.stdout.write(f"Intent count query skipped/failed: {count_error}")
