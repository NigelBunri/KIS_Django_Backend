"""
Validation and spam heuristics for `form` section submissions. No
Turnstile/CAPTCHA here (this codebase has no Turnstile integration on the
Django side at all — the website repo's own contact form verifies
Turnstile entirely in Next.js, forwarding straight to a spreadsheet, never
touching Django) — this is a honeypot + timing + link-density heuristic
instead, scored rather than hard-blocked so an owner can still see (and
judge) borderline submissions in their Responses list.
"""
from rest_framework.exceptions import ValidationError

from apps.websites.models import FORM_FIELD_TYPES

HONEYPOT_KEY = "_hp"
ELAPSED_MS_KEY = "_elapsed_ms"

# Below this, a submission is near-certainly scripted (a human can't read
# the fields and type an answer this fast) — not proof, just a strong
# signal, so it adds to spam_score rather than being rejected outright.
SUSPICIOUSLY_FAST_MS = 1500


def _field_schema(section_data: dict) -> list[dict]:
    fields = section_data.get("fields")
    return fields if isinstance(fields, list) else []


def validate_submission_data(section_data: dict, submitted: dict) -> dict:
    """Validates `submitted` against the section's declared `fields`
    schema and returns the cleaned {key: value} data to store — dropping
    any key not declared on the section, so a submission can never smuggle
    arbitrary extra fields into storage."""
    if not isinstance(submitted, dict):
        raise ValidationError({"data": "data must be an object."})

    cleaned: dict = {}
    for field in _field_schema(section_data):
        key = field.get("key")
        if not key:
            continue
        value = submitted.get(key, "")
        value = "" if value is None else str(value).strip()
        if field.get("required") and not value:
            raise ValidationError({key: f"{field.get('label') or key} is required."})
        max_len = 5000 if field.get("type") == "textarea" else 500
        cleaned[key] = value[:max_len]
    return cleaned


def score_submission(submitted: dict) -> float:
    score = 0.0

    honeypot = submitted.get(HONEYPOT_KEY)
    if honeypot:
        score += 1.0

    elapsed_ms = submitted.get(ELAPSED_MS_KEY)
    if isinstance(elapsed_ms, (int, float)) and 0 <= elapsed_ms < SUSPICIOUSLY_FAST_MS:
        score += 0.4

    text = " ".join(str(v) for v in submitted.values() if isinstance(v, str))
    link_count = text.count("http://") + text.count("https://")
    if link_count >= 2:
        score += 0.4
    elif link_count == 1:
        score += 0.15

    return min(score, 1.0)


def validate_field_schema(fields) -> None:
    """Used when an owner edits a `form` section's own field list (via
    the normal WebsitePageDetailView.patch sections payload — no
    dedicated endpoint, since fields live inside sections like every
    other section's data)."""
    if not isinstance(fields, list):
        raise ValidationError({"fields": "fields must be a list."})
    seen_keys = set()
    for field in fields:
        if not isinstance(field, dict) or not field.get("key") or not field.get("label"):
            raise ValidationError({"fields": "Each field needs a key and a label."})
        if field["key"] in seen_keys:
            raise ValidationError({"fields": f"Duplicate field key: {field['key']!r}."})
        seen_keys.add(field["key"])
        field_type = field.get("type", "text")
        if field_type not in FORM_FIELD_TYPES:
            raise ValidationError({"fields": f"Unknown field type: {field_type!r}."})
