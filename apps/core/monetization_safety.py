from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from django.conf import settings


UNSAFE_MONETIZATION_COPY_PATTERNS = (
    r"\bkisc\s*(?:to|=|\/)\s*usd\b",
    r"\bkis\s*coins?\s*(?:to|=|\/)\s*usd\b",
    r"\bexchange\s*rate\b",
    r"\bbuy\s+kis\s*coins?\b",
    r"\bsell\s+kis\s*coins?\b",
    r"\bwithdraw\s+(?:kis|credits?|coins?|wallet)\b",
    r"\bcash\s*out\b",
    r"\bconvert\s+(?:credits?|coins?|kis|wallet)\s+to\s+cash\b",
    r"\btransfer\s+(?:kis\s*)?(?:credits?|coins?)\b",
    r"\bwallet\s+deposit\b",
    r"\bwallet\s+top[- ]?up\b",
    r"\bcoin\s+balance\s+as\s+money\b",
)


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    ok: bool
    severity: str
    detail: str

    def as_dict(self):
        return {
            "key": self.key,
            "label": self.label,
            "status": "pass" if self.ok else "fail",
            "severity": self.severity,
            "detail": self.detail,
        }


def _setting_bool(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def _setting_text(name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or "").strip()


def _present(name: str) -> bool:
    return bool(_setting_text(name))


def scan_public_monetization_copy(texts: Iterable[str] | dict) -> dict:
    if isinstance(texts, dict):
        iterable = texts.values()
    else:
        iterable = texts

    findings = []
    for index, value in enumerate(iterable):
        text = str(value or "")
        for pattern in UNSAFE_MONETIZATION_COPY_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                findings.append(
                    {
                        "index": index,
                        "pattern": pattern,
                        "preview": text[:120],
                    }
                )
    return {
        "safe": not findings,
        "findings_count": len(findings),
        "findings": findings[:25],
    }


def _legacy_flags() -> dict:
    return {
        "wallet_deposit_enabled": _setting_bool("KIS_LEGACY_WALLET_DEPOSIT_ENABLED"),
        "wallet_transfer_enabled": _setting_bool("KIS_LEGACY_WALLET_TRANSFER_ENABLED"),
        "cash_credit_conversion_enabled": _setting_bool("KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED"),
        "wallet_upgrade_enabled": _setting_bool("KIS_LEGACY_WALLET_UPGRADE_ENABLED"),
        "promo_cash_bonus_enabled": _setting_bool("KIS_LEGACY_PROMO_CASH_BONUS_ENABLED"),
        "commerce_wallet_checkout_enabled": _setting_bool("KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED"),
        "education_wallet_checkout_enabled": _setting_bool("KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED"),
        "health_wallet_checkout_enabled": _setting_bool("KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED"),
    }


def _provider_readiness() -> dict:
    return {
        "currency": "USD",
        "primary_provider": "flutterwave",
        "payment_links_enabled": _setting_bool("KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED"),
        "flutterwave_public_key_configured": _present("FLW_PUBLIC_KEY"),
        "flutterwave_secret_key_configured": _present("FLW_SECRET_KEY"),
        "flutterwave_webhook_secret_configured": _present("FLW_WEBHOOK_SECRET"),
        "flutterwave_redirect_url_configured": _present("FLW_REDIRECT_URL"),
        "commerce_default_provider": _setting_text("KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER", "flutterwave"),
        "education_default_provider": _setting_text("KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER", "flutterwave"),
        "health_default_provider": _setting_text("KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER", "flutterwave"),
    }


def _checks(legacy_flags: dict, provider: dict) -> list[Check]:
    checks = [
        Check(
            "promo_credits_non_cash",
            "Promotional credits are non-cash",
            True,
            "critical",
            "KIS promotional credits must only subsidize eligible account/platform benefits.",
        ),
        Check(
            "promo_credits_not_transferable",
            "Promotional credits are non-transferable",
            not legacy_flags["wallet_transfer_enabled"],
            "critical",
            "Peer-to-peer credit transfers must stay disabled unless legal/product approval changes the model.",
        ),
        Check(
            "promo_credits_not_withdrawable",
            "Promotional credits are not withdrawable",
            not legacy_flags["cash_credit_conversion_enabled"],
            "critical",
            "Credit-to-cash conversion must stay disabled.",
        ),
        Check(
            "wallet_deposit_disabled",
            "Credits cannot be bought or topped up",
            not legacy_flags["wallet_deposit_enabled"],
            "critical",
            "Wallet top-up/deposit must stay disabled; paid products use USD direct provider payment.",
        ),
        Check(
            "commerce_wallet_checkout_disabled",
            "Commerce is USD/direct-provider first",
            not legacy_flags["commerce_wallet_checkout_enabled"],
            "critical",
            "Marketplace product and service checkout must not settle through promotional credits.",
        ),
        Check(
            "education_wallet_checkout_disabled",
            "Education is USD/direct-provider first",
            not legacy_flags["education_wallet_checkout_enabled"],
            "critical",
            "Education purchases/enrollments must not settle through promotional credits.",
        ),
        Check(
            "health_wallet_checkout_disabled",
            "Health is USD/direct-provider first",
            not legacy_flags["health_wallet_checkout_enabled"],
            "critical",
            "Health billing/session payments must not settle through promotional credits.",
        ),
        Check(
            "promo_cash_bonus_disabled",
            "No cash-equivalent promo bonuses",
            not legacy_flags["promo_cash_bonus_enabled"],
            "warning",
            "Rewards should be described as promotional account credits, not cash bonuses.",
        ),
        Check(
            "flutterwave_configured",
            "Flutterwave provider readiness",
            provider["flutterwave_secret_key_configured"] and provider["flutterwave_webhook_secret_configured"],
            "warning",
            "Staging/production need Flutterwave secret and webhook secret configured without exposing values.",
        ),
        Check(
            "direct_payment_links_controlled",
            "Payment links are feature-flag controlled",
            True,
            "warning",
            "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED controls live provider link creation.",
        ),
    ]
    return checks


def monetization_without_legal_risk_summary() -> dict:
    legacy_flags = _legacy_flags()
    provider = _provider_readiness()
    checks = _checks(legacy_flags, provider)
    failing = [item for item in checks if not item.ok]
    critical = [item for item in failing if item.severity == "critical"]
    warnings = [item for item in failing if item.severity != "critical"]

    return {
        "version": "phase_24_monetization_without_legal_risk",
        "principles": {
            "platform_currency": "USD",
            "direct_provider_first": True,
            "primary_payment_provider": "flutterwave",
            "promotional_credits_label": "KIS promotional credits",
            "promotional_credits_non_cash": True,
            "promotional_credits_non_transferable": True,
            "promotional_credits_non_withdrawable": True,
            "promotional_credits_not_exchange_rated": True,
            "historical_wallet_records_read_only": True,
        },
        "legacy_flags": legacy_flags,
        "provider_readiness": provider,
        "monetization_surfaces": {
            "subscriptions_and_upgrades": {
                "currency": "USD",
                "settlement": "direct_provider",
                "promo_credit_use": "subsidy_only_where_product_approved",
            },
            "marketplace": {"currency": "USD", "settlement": "direct_provider", "legacy_wallet_checkout": False},
            "education": {"currency": "USD", "settlement": "direct_provider", "legacy_wallet_checkout": False},
            "health": {"currency": "USD", "settlement": "direct_provider", "legacy_wallet_checkout": False},
            "partners": {"currency": "USD", "settlement": "direct_provider", "member_payments": "future_provider_flow"},
            "channels_creator": {"currency": "USD", "settlement": "direct_provider", "creator_payouts": "future_compliance_review_required"},
            "ads_and_sponsorships": {
                "currency": "USD",
                "settlement": "direct_provider",
                "child_targeting_allowed": False,
                "christian_safety_review_required": True,
            },
        },
        "copy_guard": {
            "unsafe_patterns": list(UNSAFE_MONETIZATION_COPY_PATTERNS),
            "required_public_wording": [
                "Use USD for purchases and subscriptions.",
                "KIS promotional credits are gifts/rewards for approved platform actions.",
                "Promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.",
                "Payments are completed through approved direct payment providers such as Flutterwave.",
            ],
            "forbidden_public_wording": [
                "KISC-to-USD exchange rate",
                "Buy KIS Coins",
                "Withdraw credits",
                "Cash out wallet balance",
                "Transfer credits to another user",
            ],
        },
        "checks": [item.as_dict() for item in checks],
        "summary": {
            "go_live_status": "blocked" if critical else ("conditional" if warnings else "go"),
            "total_checks": len(checks),
            "passed": len(checks) - len(failing),
            "critical_failures": len(critical),
            "warnings": len(warnings),
        },
        "privacy": {
            "no_secret_values": True,
            "no_payment_instrument_data": True,
            "no_raw_provider_payloads": True,
            "child_youth_safe_monetization": True,
        },
    }
