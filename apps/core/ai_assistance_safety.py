from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


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


def _provider_key_present(provider: str) -> bool:
    provider = provider.lower()
    if provider == "gemini":
        return bool(_setting_text("GEMINI_API_KEY"))
    if provider == "groq":
        return bool(_setting_text("GROQ_API_KEY"))
    if provider == "openai":
        return bool(_setting_text("OPENAI_API_KEY"))
    return False


def ai_assistance_safety_policy(user=None) -> dict:
    provider = _setting_text("KIS_AI_PROVIDER")
    live_calls = _setting_bool("KIS_AI_LIVE_PROVIDER_CALLS_ENABLED")
    output_moderation = _setting_bool("KIS_AI_OUTPUT_MODERATION_REQUIRED", True)
    input_redaction = _setting_bool("KIS_AI_INPUT_REDACTION_REQUIRED", True)
    child_safe = _setting_bool("KIS_AI_CHILD_SAFE_MODE_REQUIRED", True)
    stores_prompts = _setting_bool("KIS_AI_STORE_PROMPTS_ENABLED")
    stores_responses = _setting_bool("KIS_AI_STORE_RESPONSES_ENABLED")
    medical_diagnosis = _setting_bool("KIS_AI_MEDICAL_DIAGNOSIS_ENABLED")
    financial_advice = _setting_bool("KIS_AI_FINANCIAL_ADVICE_ENABLED")
    provider_key_ready = _provider_key_present(provider) if provider else False

    checks = [
        Check(
            "live_provider_calls_disabled_by_default",
            "Live AI provider calls are gated",
            not live_calls or (bool(provider) and provider_key_ready and output_moderation and input_redaction),
            "critical",
            "Provider network calls must remain disabled unless provider config, redaction, and moderation are approved.",
        ),
        Check(
            "output_moderation_required",
            "AI outputs require moderation",
            output_moderation,
            "critical",
            "Outputs must be checked before user-facing display, especially for children and youth.",
        ),
        Check(
            "input_redaction_required",
            "AI inputs require redaction",
            input_redaction,
            "critical",
            "Private health, payment, verification, credential, and child data must not be sent raw to providers.",
        ),
        Check(
            "child_safe_mode_required",
            "Child/youth safe mode is required",
            child_safe,
            "critical",
            "Child and youth experiences must use stricter guidance, retrieval, and refusal rules.",
        ),
        Check(
            "no_prompt_response_storage",
            "Raw prompts and responses are not stored",
            not stores_prompts and not stores_responses,
            "critical",
            "Store only redacted audit metadata until retention, consent, and privacy review are complete.",
        ),
        Check(
            "medical_diagnosis_disabled",
            "AI medical diagnosis is disabled",
            not medical_diagnosis,
            "critical",
            "AI may support admin summaries and triage wording, but must not diagnose or replace licensed care.",
        ),
        Check(
            "financial_advice_disabled",
            "AI financial advice is disabled",
            not financial_advice,
            "critical",
            "AI must not provide investment, legal, tax, credit, or cash-equivalent financial advice.",
        ),
    ]
    failing = [item for item in checks if not item.ok]
    critical = [item for item in failing if item.severity == "critical"]
    warnings = [item for item in failing if item.severity != "critical"]

    return {
        "version": "phase_25_ai_assistance_christian_safety_boundaries",
        "enabled": _setting_bool("KIS_AI_ASSISTANCE_ENABLED", True),
        "provider": {
            "selected": provider or "not_configured",
            "live_calls_enabled": live_calls,
            "provider_key_configured": provider_key_ready,
            "secret_values_exposed": False,
        },
        "boundaries": {
            "christian_principles_required": True,
            "pornographic_or_sexual_content_blocked": True,
            "manipulative_or_predatory_content_blocked": True,
            "medical_diagnosis_blocked": not medical_diagnosis,
            "financial_advice_blocked": not financial_advice,
            "legal_advice_blocked": True,
            "self_harm_escalation_required": True,
            "child_youth_safe_mode_required": child_safe,
            "human_review_for_high_risk_outputs": True,
        },
        "privacy_controls": {
            "input_redaction_required": input_redaction,
            "output_moderation_required": output_moderation,
            "store_raw_prompts": stores_prompts,
            "store_raw_responses": stores_responses,
            "no_private_health_payloads": True,
            "no_payment_instrument_payloads": True,
            "no_raw_verification_documents": True,
            "no_secret_values": True,
        },
        "assistant_surfaces": {
            "bible_study_help": {"status": "placeholder_ready", "risk": "low", "guardrail": "scripture-grounded and pastoral-not-authoritative"},
            "learning_tutoring": {"status": "placeholder_ready", "risk": "medium", "guardrail": "age-safe tutoring and no cheating automation"},
            "health_admin_support": {"status": "placeholder_ready", "risk": "high", "guardrail": "summaries only, no diagnosis or treatment decisions"},
            "commerce_product_help": {"status": "placeholder_ready", "risk": "medium", "guardrail": "no manipulative sales or unsafe claims"},
            "moderation_triage": {"status": "placeholder_ready", "risk": "high", "guardrail": "staff decision support only"},
            "creator_channel_drafting": {"status": "placeholder_ready", "risk": "medium", "guardrail": "media safety and Christian content policy enforced"},
            "messaging_suggestions": {"status": "placeholder_ready", "risk": "medium", "guardrail": "opt-in, no private data leakage, no manipulation"},
            "admin_insights": {"status": "placeholder_ready", "risk": "high", "guardrail": "aggregate/redacted only"},
        },
        "checks": [item.as_dict() for item in checks],
        "summary": {
            "go_live_status": "blocked" if critical else ("conditional" if warnings else "go"),
            "total_checks": len(checks),
            "passed": len(checks) - len(failing),
            "critical_failures": len(critical),
            "warnings": len(warnings),
        },
    }
