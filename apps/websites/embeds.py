"""
Validation for an `embed` section's data.{provider,url}. Restricted to a
curated provider allowlist (apps.websites.models.EMBED_PROVIDERS) rather
than an arbitrary URL/script embed — see that constant's docstring for
why. Each provider's pattern only matches that provider's own embeddable
player/widget URL shape, not just "any URL on this domain", so a
generic youtube.com link (not an /embed/ one) is still rejected — the
page it would load isn't iframe-embeddable and just shows a blank frame.
"""
import re

from rest_framework.exceptions import ValidationError

_PROVIDER_URL_PATTERNS = {
    "youtube": re.compile(r"^https://www\.youtube(-nocookie)?\.com/embed/[\w-]+"),
    "vimeo": re.compile(r"^https://player\.vimeo\.com/video/\d+"),
    "calendly": re.compile(r"^https://calendly\.com/[\w./-]+"),
    "google_maps": re.compile(r"^https://www\.google\.com/maps/embed"),
    "google_calendar": re.compile(r"^https://calendar\.google\.com/calendar/embed"),
    "spotify": re.compile(r"^https://open\.spotify\.com/embed/"),
    "loom": re.compile(r"^https://www\.loom\.com/embed/[\w-]+"),
}


def validate_embed(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValidationError({"embed": "embed section data must be an object."})

    provider = data.get("provider")
    pattern = _PROVIDER_URL_PATTERNS.get(provider)
    if pattern is None:
        raise ValidationError({"provider": f"provider must be one of {sorted(_PROVIDER_URL_PATTERNS)}."})

    url = str(data.get("url") or "")
    if not pattern.match(url):
        raise ValidationError({"url": f"url doesn't look like a valid {provider} embed URL."})
