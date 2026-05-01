import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_KEYS = {
    "access",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "code",
    "fcm_server_key",
    "firebase_credentials_json",
    "jwt",
    "jwt_secret",
    "new_password",
    "otp",
    "otp_code",
    "password",
    "password1",
    "password2",
    "push_token",
    "refresh",
    "refresh_token",
    "secret",
    "secret_key",
    "signature",
    "token",
}

SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(password|secret|token|otp|code|authorization)\b\s*[:=]\s*['\"]?[^,'\"\s&}]+"),
]


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized.endswith("_token") or normalized.endswith("_secret")


def redact_mapping(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_sensitive_key(str(key)) else redact_mapping(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(redact_mapping(item) for item in value)
    return redact_text(str(value)) if isinstance(value, str) else value


def redact_text(value: str) -> str:
    redacted = str(value)
    for pattern in SENSITIVE_VALUE_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]" if match.lastindex else "[REDACTED]", redacted)
    return redacted


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(str(url))
        if not parts.query:
            return urlunsplit(parts)
        query = [
            (key, "[REDACTED]" if _is_sensitive_key(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return redact_text(str(url))


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_mapping(record.msg)
        if isinstance(record.args, dict):
            record.args = redact_mapping(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact_mapping(arg) for arg in record.args)
        return True
