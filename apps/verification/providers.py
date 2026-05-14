from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.conf import settings

from .constants import VerificationSubjectType

logger = logging.getLogger(__name__)

SENSITIVE_PROVIDER_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "app_id",
    "appid",
    "app_token",
    "applicant_id",
    "base64",
    "bvn",
    "document",
    "document_base64",
    "document_data",
    "email",
    "face",
    "file",
    "id_number",
    "image",
    "image_base64",
    "nin",
    "passport",
    "password",
    "phone",
    "partner_id",
    "raw",
    "raw_document",
    "secret",
    "secret_key",
    "selfie",
    "signature",
    "token",
    "x-partner-id",
}


def redact_provider_payload(value, *, depth: int = 0):
    if depth > 8:
        return "[redacted:depth]"
    if isinstance(value, dict):
        output = {}
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in SENSITIVE_PROVIDER_KEYS or any(
                term in normalized
                for term in ("secret", "token", "password", "base64", "document", "passport", "selfie", "image")
            ):
                output[str(key)] = "[redacted]"
            else:
                output[str(key)] = redact_provider_payload(child, depth=depth + 1)
        return output
    if isinstance(value, list):
        return [redact_provider_payload(item, depth=depth + 1) for item in value[:25]]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("data:") or len(stripped) > 160:
            return "[redacted]"
    return value


def _safe_reference_count(evidence_metadata) -> int:
    if not isinstance(evidence_metadata, dict):
        return 0
    count = 0
    for key in ("private_media_refs", "private_media", "documents"):
        refs = evidence_metadata.get(key)
        if isinstance(refs, list):
            count += len(refs)
    return count


def _webhook_url(provider_name: str) -> str:
    base_url = str(getattr(settings, "VERIFICATION_WEBHOOK_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        return ""
    return f"{base_url}/api/v1/verification/webhooks/{provider_name}/"


@dataclass(frozen=True)
class VerificationProviderConfig:
    name: str
    base_url: str
    configured: bool


class ProviderAdapter:
    name = "base"
    sandbox_endpoint = ""

    def live_calls_enabled(self, subject_type: str = "") -> bool:
        if not getattr(settings, "VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED", False):
            return False
        if str(getattr(settings, "ENV", "") or "").lower() == "production":
            return False
        allowed_envs = {str(item).strip().lower() for item in (getattr(settings, "VERIFICATION_PROVIDER_LIVE_ALLOWED_ENVS", []) or [])}
        if allowed_envs and str(getattr(settings, "ENV", "") or "").lower() not in allowed_envs:
            return False
        allowed_subjects = set(getattr(settings, "VERIFICATION_LIVE_PROVIDER_SUBJECTS", []) or [])
        return not allowed_subjects or subject_type in allowed_subjects

    def config(self) -> VerificationProviderConfig:
        raise NotImplementedError

    def sandbox_network_enabled(self, subject_type: str = "") -> bool:
        return bool(
            self.live_calls_enabled(subject_type)
            and getattr(settings, "VERIFICATION_PROVIDER_SANDBOX_ENABLED", True)
            and getattr(settings, "VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED", False)
        )

    def sandbox_case_request(self, case) -> dict:
        return {
            "endpoint": self.sandbox_endpoint,
            "method": "POST",
            "headers": {},
            "body": {
                "external_id": str(case.id),
                "level": case.level,
                "subject_type": case.subject.subject_type,
                "private_reference_count": _safe_reference_count(case.evidence_metadata),
                "callback_url": _webhook_url(self.name),
                "sandbox": True,
            },
        }

    def execute_sandbox_case_request(self, case, request_payload: dict) -> dict:
        if not self.sandbox_network_enabled(getattr(case.subject, "subject_type", "")):
            return {
                "status": "not_sent",
                "reason": "sandbox_network_disabled",
                "reference": f"sandbox:{self.name}:{case.id}",
            }
        endpoint = str(request_payload.get("endpoint") or "").strip()
        if not endpoint:
            return {
                "status": "not_sent",
                "reason": "missing_sandbox_endpoint",
                "reference": f"sandbox:{self.name}:{case.id}",
            }
        body = json.dumps(request_payload.get("body") or {}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            **(request_payload.get("headers") or {}),
        }
        timeout = max(1, min(int(getattr(settings, "VERIFICATION_PROVIDER_TIMEOUT_SECONDS", 10) or 10), 30))
        try:
            req = urllib.request.Request(endpoint, data=body, headers=headers, method=str(request_payload.get("method") or "POST"))
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read(4096)
                try:
                    parsed = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    parsed = {"response_bytes": len(raw)}
                safe_response = redact_provider_payload(parsed)
                reference = (
                    safe_response.get("id")
                    or safe_response.get("reference")
                    or safe_response.get("applicant_id")
                    or safe_response.get("applicantId")
                    or f"sandbox:{self.name}:{case.id}"
                )
                return {
                    "status": "sent",
                    "http_status": response.status,
                    "reference": str(reference),
                    "response": safe_response,
                }
        except urllib.error.HTTPError as exc:
            logger.warning(
                "verification.provider_sandbox_http_error provider=%s status=%s body=%s",
                self.name,
                exc.code,
                redact_provider_payload(exc.read(1024).decode("utf-8", errors="ignore")),
            )
            return {"status": "failed", "http_status": exc.code, "reason": "provider_http_error", "reference": f"sandbox:{self.name}:{case.id}"}
        except Exception as exc:
            logger.warning("verification.provider_sandbox_error provider=%s error=%s", self.name, exc.__class__.__name__)
            return {"status": "failed", "reason": exc.__class__.__name__, "reference": f"sandbox:{self.name}:{case.id}"}

    def start_case(self, case) -> dict:
        cfg = self.config()
        live_enabled = self.live_calls_enabled(getattr(case.subject, "subject_type", ""))
        sandbox_enabled = bool(getattr(settings, "VERIFICATION_PROVIDER_SANDBOX_ENABLED", True))
        if live_enabled and cfg.configured and sandbox_enabled:
            reference = f"sandbox:{cfg.name}:{case.id}"
            safe_request = {
                "provider": cfg.name,
                "case_id": str(case.id),
                "subject_type": case.subject.subject_type,
                "level": case.level,
                "private_reference_count": _safe_reference_count(case.evidence_metadata),
                "sandbox": True,
            }
            sandbox_request = self.sandbox_case_request(case)
            sandbox_response = self.execute_sandbox_case_request(case, sandbox_request)
            response_reference = sandbox_response.get("reference") or reference
            return {
                "provider": cfg.name,
                "configured": True,
                "reference": str(response_reference),
                "next_action": "provider_sandbox_pending",
                "live_calls_enabled": True,
                "sandbox_enabled": True,
                "sandbox_handoff_ready": True,
                "sandbox_network_enabled": self.sandbox_network_enabled(case.subject.subject_type),
                "live_call_made": sandbox_response.get("status") == "sent",
                "provider_request": redact_provider_payload({**safe_request, **sandbox_request}),
                "provider_response": redact_provider_payload(sandbox_response),
            }
        return {
            "provider": cfg.name,
            "configured": cfg.configured,
            "reference": f"local:{case.id}",
            "next_action": "manual_review" if not (cfg.configured and live_enabled) else "provider_live_flag_ready",
            "live_calls_enabled": live_enabled,
            "live_call_made": False,
        }

    def start_user_case(self, case) -> dict:
        return self.start_case(case)


class DojahAdapter(ProviderAdapter):
    name = "dojah"

    def config(self) -> VerificationProviderConfig:
        return VerificationProviderConfig(
            name=self.name,
            base_url=getattr(settings, "DOJAH_BASE_URL", ""),
            configured=bool(getattr(settings, "DOJAH_APP_ID", "") and getattr(settings, "DOJAH_SECRET_KEY", "")),
        )

    @property
    def sandbox_endpoint(self) -> str:
        return f"{self.config().base_url.rstrip('/')}/api/v1/kyc/id"

    def sandbox_case_request(self, case) -> dict:
        cfg = self.config()
        return {
            "endpoint": self.sandbox_endpoint,
            "method": "POST",
            "headers": {
                "AppId": getattr(settings, "DOJAH_APP_ID", ""),
                "Authorization": getattr(settings, "DOJAH_SECRET_KEY", ""),
            },
            "body": {
                "reference_id": str(case.id),
                "verification_type": "business" if case.subject.subject_type != VerificationSubjectType.USER else "document",
                "callback_url": _webhook_url(self.name),
                "metadata": {
                    "level": case.level,
                    "private_reference_count": _safe_reference_count(case.evidence_metadata),
                    "sandbox": True,
                },
            },
        }


class SumsubAdapter(ProviderAdapter):
    name = "sumsub"

    def config(self) -> VerificationProviderConfig:
        return VerificationProviderConfig(
            name=self.name,
            base_url=getattr(settings, "SUMSUB_BASE_URL", ""),
            configured=bool(getattr(settings, "SUMSUB_APP_TOKEN", "") and getattr(settings, "SUMSUB_SECRET_KEY", "")),
        )

    @property
    def sandbox_endpoint(self) -> str:
        return f"{self.config().base_url.rstrip('/')}/resources/applicants?levelName=basic-kyc-level"

    def sandbox_case_request(self, case) -> dict:
        return {
            "endpoint": self.sandbox_endpoint,
            "method": "POST",
            "headers": {
                "X-App-Token": getattr(settings, "SUMSUB_APP_TOKEN", ""),
            },
            "body": {
                "externalUserId": str(case.id),
                "fixedInfo": {},
                "metadata": {
                    "level": case.level,
                    "private_reference_count": _safe_reference_count(case.evidence_metadata),
                    "callback_url": _webhook_url(self.name),
                    "sandbox": True,
                },
            },
        }


class SmileIdAdapter(ProviderAdapter):
    name = "smile_id"

    def config(self) -> VerificationProviderConfig:
        return VerificationProviderConfig(
            name=self.name,
            base_url=getattr(settings, "SMILE_ID_BASE_URL", ""),
            configured=bool(getattr(settings, "SMILE_ID_PARTNER_ID", "") and getattr(settings, "SMILE_ID_API_KEY", "")),
        )

    @property
    def sandbox_endpoint(self) -> str:
        return f"{self.config().base_url.rstrip('/')}/v1/id_verification"

    def sandbox_case_request(self, case) -> dict:
        return {
            "endpoint": self.sandbox_endpoint,
            "method": "POST",
            "headers": {
                "X-Partner-ID": getattr(settings, "SMILE_ID_PARTNER_ID", ""),
                "Authorization": getattr(settings, "SMILE_ID_API_KEY", ""),
            },
            "body": {
                "partner_params": {
                    "job_id": str(case.id),
                    "user_id": str(case.subject.subject_id),
                },
                "callback_url": _webhook_url(self.name),
                "metadata": {
                    "level": case.level,
                    "private_reference_count": _safe_reference_count(case.evidence_metadata),
                    "sandbox": True,
                },
            },
        }


ADAPTERS = {
    DojahAdapter.name: DojahAdapter(),
    SumsubAdapter.name: SumsubAdapter(),
    SmileIdAdapter.name: SmileIdAdapter(),
    "smile": SmileIdAdapter(),
}


def get_provider_adapter(provider: str | None = None) -> ProviderAdapter:
    preferred = (provider or getattr(settings, "VERIFICATION_PROVIDER_PRIMARY", "dojah") or "dojah").strip().lower()
    return ADAPTERS.get(preferred) or ADAPTERS["dojah"]


def provider_public_status(provider: str | None = None, *, subject_type: str = "") -> dict:
    adapter = get_provider_adapter(provider)
    cfg = adapter.config()
    return {
        "name": cfg.name,
        "configured": cfg.configured,
        "live_calls_enabled": adapter.live_calls_enabled(subject_type),
        "sandbox_enabled": bool(getattr(settings, "VERIFICATION_PROVIDER_SANDBOX_ENABLED", True)),
        "sandbox_network_enabled": adapter.sandbox_network_enabled(subject_type),
        "live_call_made": False,
    }


def verify_webhook_signature(*, body: bytes, signature: str | None) -> tuple[bool, str]:
    secret = getattr(settings, "VERIFICATION_WEBHOOK_SECRET", "")
    if not secret:
        return False, "missing_webhook_secret"
    if not signature:
        return False, "missing_signature"

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    normalized = signature.strip()
    if normalized.startswith("sha256="):
        normalized = normalized.split("=", 1)[1]
    if not hmac.compare_digest(expected, normalized):
        return False, "signature_mismatch"
    return True, "ok"
