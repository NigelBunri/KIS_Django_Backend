"""
Global theming for a Website — palette, typography, button style. Colors
are freeform hex (validated, not constrained to a palette) since a color
picker doesn't need anything build-time-known. Typography is a small,
closed set of presets rather than an arbitrary font name: the website
repo loads fonts via next/font/google (self-hosted at build time, no
runtime request to fonts.googleapis.com — required by its CSP), which
needs the font imports to be static/known ahead of time. A user-supplied
arbitrary font name has nothing to resolve to there, so this is
validated here rather than left to the frontend to silently ignore.
"""
import re

from rest_framework.exceptions import ValidationError

TYPOGRAPHY_PRESETS = {"system", "sans", "serif"}
BUTTON_SHAPES = {"rounded", "pill", "square"}
BUTTON_FILLS = {"solid", "outline"}

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _validate_hex_color(value, field_name):
    if value is None:
        return
    if not isinstance(value, str) or not _HEX_COLOR_RE.match(value):
        raise ValidationError({field_name: f"{field_name} must be a hex color like #1a1a2e."})


def validate_branding(payload: dict) -> None:
    """Raises ValidationError on anything malformed; silently accepts a
    partial/empty payload (branding is optional, callers merge onto the
    existing value — see WebsiteDetailView.patch)."""
    if not isinstance(payload, dict):
        raise ValidationError({"branding": "branding must be an object."})

    palette = payload.get("palette")
    if palette is not None:
        if not isinstance(palette, dict):
            raise ValidationError({"branding": "branding.palette must be an object."})
        for key in ("primary", "secondary", "background", "text"):
            _validate_hex_color(palette.get(key), f"branding.palette.{key}")

    typography = payload.get("typography")
    if typography is not None:
        if not isinstance(typography, dict):
            raise ValidationError({"branding": "branding.typography must be an object."})
        preset = typography.get("preset")
        if preset is not None and preset not in TYPOGRAPHY_PRESETS:
            raise ValidationError({"branding": f"branding.typography.preset must be one of {sorted(TYPOGRAPHY_PRESETS)}."})

    buttons = payload.get("buttons")
    if buttons is not None:
        if not isinstance(buttons, dict):
            raise ValidationError({"branding": "branding.buttons must be an object."})
        shape = buttons.get("shape")
        if shape is not None and shape not in BUTTON_SHAPES:
            raise ValidationError({"branding": f"branding.buttons.shape must be one of {sorted(BUTTON_SHAPES)}."})
        fill = buttons.get("fill")
        if fill is not None and fill not in BUTTON_FILLS:
            raise ValidationError({"branding": f"branding.buttons.fill must be one of {sorted(BUTTON_FILLS)}."})
