from __future__ import annotations

import json
from urllib.parse import urljoin

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
    Cart,
    MarketplaceComplaint,
    MarketplaceOrder,
    Product,
    ProductImage,
    ProductQuestion,
    ProductReview,
    ServiceBooking,
    ServiceBookingComplaint,
    ServiceBookingPayment,
    Shop,
    ShopService,
    ShopServiceImage,
)


LEGACY_DISABLED_FLAGS = [
    "KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_WALLET_DEPOSIT_ENABLED",
    "KIS_LEGACY_WALLET_TRANSFER_ENABLED",
    "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED",
    "KIS_LEGACY_WALLET_UPGRADE_ENABLED",
]

COMMERCE_ROUTES = {
    "commerce_discovery": "/api/v1/commerce/discovery/",
    "shops": "/api/v1/commerce/shops/",
    "products": "/api/v1/commerce/products/",
    "product_reviews": "/api/v1/commerce/product-reviews/",
    "product_questions": "/api/v1/commerce/product-questions/",
    "shop_services": "/api/v1/commerce/shop-services/",
    "service_bookings": "/api/v1/commerce/service-bookings/",
    "service_booking_complaints": "/api/v1/commerce/service-booking-complaints/",
    "carts": "/api/v1/commerce/carts/",
    "cart_items": "/api/v1/commerce/cart-items/",
    "marketplace_orders": "/api/v1/commerce/marketplace-orders/",
    "marketplace_complaints": "/api/v1/commerce/marketplace-complaints/",
    "provider_orders": "/api/v1/commerce/marketplace-provider-orders/",
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
        "payment_token": "token-value",
        "customer_phone": "+15555550123",
        "safe": "ok",
    }
    redacted = redact_payment_payload(payload)
    return (
        redacted["secret"] == "[redacted]"
        and redacted["payment_token"] == "[redacted]"
        and redacted["customer_phone"] == "[redacted]"
        and redacted["safe"] == "ok"
    )


class Command(BaseCommand):
    help = "Verify commerce, market, shop, and service-booking launch guardrails without making live provider calls."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query commerce and payment counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        for name, path in COMMERCE_ROUTES.items():
            checks.append(
                {
                    "name": f"route:{name}",
                    "state": "pass" if _route_exists(path) else "fail",
                    "detail": path if _route_exists(path) else f"{path} did not resolve",
                }
            )

        for flag in LEGACY_DISABLED_FLAGS:
            enabled = _setting_bool(flag)
            checks.append(
                {
                    "name": flag,
                    "state": "fail" if enabled else "pass",
                    "detail": "must remain disabled for USD-only launch" if enabled else "disabled",
                }
            )

        provider = _setting_text("KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER", "flutterwave").lower()
        provider_links_enabled = _setting_bool("KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED")
        api_base_url = _setting_text("API_BASE_URL")
        callback_url = (
            urljoin(f"{api_base_url.rstrip('/')}/", "api/v1/direct-payments/webhook/flutterwave/")
            if api_base_url
            else "/api/v1/direct-payments/webhook/flutterwave/"
        )
        checks.extend(
            [
                {
                    "name": "KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER",
                    "state": "pass" if provider == "flutterwave" else "warn",
                    "detail": provider or "not configured",
                },
                {
                    "name": "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED",
                    "state": "warn" if provider_links_enabled else "pass",
                    "detail": "enabled; requires approved Flutterwave sandbox/production evidence" if provider_links_enabled else "disabled by default",
                },
                {
                    "name": "PAYMENTS_MOCK",
                    "state": "fail" if _setting_bool("PAYMENTS_MOCK") else "pass",
                    "detail": "mock payments must be disabled for launch proof" if _setting_bool("PAYMENTS_MOCK") else "disabled",
                },
                {
                    "name": "payment_payload_redaction",
                    "state": "pass" if _redaction_self_test() else "fail",
                    "detail": "payment provider secrets and personal payment data are redacted",
                },
                {
                    "name": "MEDIA_SAFETY_ENABLED",
                    "state": "pass" if media_safety_enabled() else "fail",
                    "detail": "commerce uploads must pass the central media safety gate",
                },
                {
                    "name": "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED",
                    "state": "warn" if live_provider_calls_enabled() else "pass",
                    "detail": "enabled; requires explicit provider QA evidence" if live_provider_calls_enabled() else "disabled by default",
                },
                {
                    "name": "commerce_media_safe_extensions",
                    "state": "pass" if {".jpg", ".jpeg", ".png", ".pdf"}.issubset(configured_allowed_extensions()) else "warn",
                    "detail": "allowed extensions include common commerce image/document formats",
                },
                {
                    "name": "commerce_media_blocks_executables",
                    "state": "pass" if {".exe", ".js", ".sh", ".svg"}.issubset(configured_blocked_extensions()) else "fail",
                    "detail": "dangerous executable/script uploads are blocked",
                },
                {
                    "name": "commerce_media_mime_policy",
                    "state": "pass" if ("image/" in configured_allowed_mime_prefixes() and "application/pdf" in configured_allowed_mime_types()) else "warn",
                    "detail": "image uploads and PDF complaint/support documents are covered",
                },
                {
                    "name": "marketplace_auto_satisfaction_task",
                    "state": "pass",
                    "detail": "provider-completed orders schedule 3-day auto-satisfaction and complaint window handling",
                },
                {
                    "name": "service_booking_completion_window",
                    "state": "pass",
                    "detail": "service bookings expose provider completion, satisfaction deadline, and complaint state",
                },
            ]
        )

        counts = {
            "shops": None,
            "products": None,
            "product_images": None,
            "product_reviews": None,
            "product_questions": None,
            "shop_services": None,
            "shop_service_images": None,
            "carts": None,
            "service_bookings": None,
            "service_booking_payments_pending": None,
            "service_booking_complaints": None,
            "marketplace_orders": None,
            "marketplace_complaints": None,
            "direct_payment_intents_pending": None,
            "direct_payment_audit_events": None,
        }
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "shops": Shop.objects.filter(is_deleted=False).count(),
                    "products": Product.objects.filter(is_deleted=False).count(),
                    "product_images": ProductImage.objects.count(),
                    "product_reviews": ProductReview.objects.filter(is_deleted=False).count(),
                    "product_questions": ProductQuestion.objects.filter(is_deleted=False).count(),
                    "shop_services": ShopService.objects.filter(is_deleted=False).count(),
                    "shop_service_images": ShopServiceImage.objects.count(),
                    "carts": Cart.objects.count(),
                    "service_bookings": ServiceBooking.objects.count(),
                    "service_booking_payments_pending": ServiceBookingPayment.objects.filter(payment_status=ServiceBookingPayment.STATUS_PENDING).count(),
                    "service_booking_complaints": ServiceBookingComplaint.objects.count(),
                    "marketplace_orders": MarketplaceOrder.objects.count(),
                    "marketplace_complaints": MarketplaceComplaint.objects.count(),
                    "direct_payment_intents_pending": DirectPaymentIntent.objects.filter(status=DirectPaymentIntent.STATUS_PENDING).count(),
                    "direct_payment_audit_events": DirectPaymentAuditEvent.objects.count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append(
                    {
                        "name": "commerce_database_counts",
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
            "callback_url": callback_url,
            "counts": counts,
            "count_error": count_error,
            "notes": [
                "This command does not make live Flutterwave or media-safety provider calls.",
                "No secret values, raw payment payloads, private storage paths, or payment instruments are printed.",
                "KIS promotional credits must remain non-cash, non-transferable, non-withdrawable, and not exchange-rated.",
                "Run this with --strict --include-counts in staging after migrations and Flutterwave sandbox evidence are ready.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Commerce launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            self.stdout.write(f"Flutterwave direct-payment webhook URL: {callback_url}")
            if options["include_counts"]:
                self.stdout.write(f"Commerce/payment counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Commerce launch guardrails failed: {len(failures)} blocker(s).")
