from __future__ import annotations

"""
KIS certificate PDF generator (ReportLab)

Goals:
- Proper certificate "shape": outer frame + inner frame + rounded paper panel.
- Strong, elegant typography hierarchy.
- KIS watermark centered and subtle (VERY IMPORTANT).
- Optional assets (logo/seal/corner) used when present; clean vector fallback otherwise.
"""

import os
from dataclasses import dataclass
from datetime import date as date_type
from io import BytesIO
from typing import Optional, Tuple

from django.conf import settings
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas


# ---------------- helpers

def _parse_hex_color(color: Optional[str]) -> Optional[Tuple[float, float, float]]:
    if not color:
        return None
    value = color.strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        r = int(value[0:2], 16) / 255.0
        g = int(value[2:4], 16) / 255.0
        b = int(value[4:6], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return None


def _safe_asset_path(*parts: str) -> str:
    base = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "static", "branding"))
    return os.path.normpath(os.path.join(base, *parts))


def _set_alpha(c: canvas.Canvas, alpha: float) -> None:
    try:
        c.setFillAlpha(alpha)
    except Exception:
        pass
    try:
        c.setStrokeAlpha(alpha)
    except Exception:
        pass


def _fit_text(c: canvas.Canvas, text: str, max_width: float, font_name: str, start_size: int, min_size: int = 10) -> int:
    size = start_size
    while size > min_size and c.stringWidth(text, font_name, size) > max_width:
        size -= 1
    return size


def _draw_vertical_gradient(
    c: canvas.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    top: Color,
    bottom: Color,
    steps: int = 80,
) -> None:
    for i in range(steps):
        t = i / max(steps - 1, 1)
        r = top.red + (bottom.red - top.red) * t
        g = top.green + (bottom.green - top.green) * t
        b = top.blue + (bottom.blue - top.blue) * t
        c.setFillColor(Color(r, g, b, alpha=1))
        c.rect(x, y + (h * i / steps), w, h / steps + 0.7, stroke=0, fill=1)


def _rounded_rect_path(c: canvas.Canvas, x: float, y: float, w: float, h: float, r: float) -> None:
    # ReportLab Canvas has roundRect, but no path clip helper; we use roundRect directly when needed.
    c.roundRect(x, y, w, h, r, stroke=0, fill=1)


def _draw_corner_fallback(c: canvas.Canvas, x: float, y: float, size: float, stroke: Color) -> None:
    # Elegant corner "bracket" fallback
    c.saveState()
    c.setStrokeColor(stroke)
    c.setLineWidth(1.0)
    k = size * 0.62
    c.line(x, y + size, x + k, y + size)
    c.line(x, y + size, x, y + size - k)
    c.restoreState()


@dataclass(frozen=True)
class CertificateTheme:
    bg_top: Color
    bg_bottom: Color
    panel: Color
    ink: Color
    ink_muted: Color
    accent: Color
    gold_1: Color
    gold_2: Color
    watermark_alpha: float


def theme_light(brand_hex: Optional[str] = None) -> CertificateTheme:
    brand_rgb = _parse_hex_color(brand_hex)
    accent = Color(*brand_rgb, alpha=1) if brand_rgb else Color(0.09, 0.19, 0.33, alpha=1)
    return CertificateTheme(
        bg_top=Color(0.99, 0.98, 0.96, alpha=1),
        bg_bottom=Color(0.95, 0.97, 0.99, alpha=1),
        panel=Color(0.995, 0.995, 0.992, alpha=1),
        ink=Color(0.07, 0.10, 0.14, alpha=1),
        ink_muted=Color(0.30, 0.33, 0.37, alpha=1),
        accent=accent,
        gold_1=Color(0.86, 0.72, 0.37, alpha=1),
        gold_2=Color(0.62, 0.48, 0.20, alpha=1),
        watermark_alpha=0.035,
    )


def theme_dark(brand_hex: Optional[str] = None) -> CertificateTheme:
    brand_rgb = _parse_hex_color(brand_hex)
    accent = Color(*brand_rgb, alpha=1) if brand_rgb else Color(0.93, 0.82, 0.55, alpha=1)
    return CertificateTheme(
        bg_top=Color(0.07, 0.08, 0.10, alpha=1),
        bg_bottom=Color(0.03, 0.03, 0.04, alpha=1),
        panel=Color(0.10, 0.11, 0.13, alpha=1),
        ink=Color(0.94, 0.92, 0.88, alpha=1),
        ink_muted=Color(0.74, 0.72, 0.68, alpha=1),
        accent=accent,
        gold_1=Color(0.90, 0.77, 0.45, alpha=1),
        gold_2=Color(0.60, 0.45, 0.18, alpha=1),
        watermark_alpha=0.03,
    )


# ---------------- drawing primitives

def _draw_outer_frame(c: canvas.Canvas, th: CertificateTheme, page_w: float, page_h: float) -> Tuple[float, float, float, float]:
    """
    Returns inner frame rect: (x, y, w, h)
    """
    outer = 0.52 * inch
    inner = 0.78 * inch

    # Outer frame
    c.saveState()
    c.setStrokeColor(th.gold_1)
    c.setLineWidth(3.0)
    c.rect(outer, outer, page_w - 2 * outer, page_h - 2 * outer, stroke=1, fill=0)

    # Inner frame
    c.setStrokeColor(th.gold_2)
    c.setLineWidth(1.5)
    c.rect(inner, inner, page_w - 2 * inner, page_h - 2 * inner, stroke=1, fill=0)
    c.restoreState()

    return (inner, inner, page_w - 2 * inner, page_h - 2 * inner)


def _draw_corner_ornaments(c: canvas.Canvas, th: CertificateTheme, x: float, y: float, w: float, h: float) -> None:
    ornament_path = _safe_asset_path("kis-corner.png")
    size = 0.80 * inch
    pad = 0.18 * inch

    if os.path.exists(ornament_path):
        c.drawImage(ornament_path, x + pad, y + h - pad - size, size, size, mask="auto")
        c.drawImage(ornament_path, x + w - pad - size, y + h - pad - size, size, size, mask="auto")
        c.drawImage(ornament_path, x + pad, y + pad, size, size, mask="auto")
        c.drawImage(ornament_path, x + w - pad - size, y + pad, size, size, mask="auto")
        return

    # Vector fallback
    c.saveState()
    s = th.gold_2
    _draw_corner_fallback(c, x + pad, y + h - pad - size, size, s)
    c.saveState()
    c.translate(x + w - pad - size, y + h - pad - size)
    c.scale(-1, 1)
    _draw_corner_fallback(c, 0, 0, size, s)
    c.restoreState()
    _draw_corner_fallback(c, x + pad, y + pad, size, s)
    c.saveState()
    c.translate(x + w - pad - size, y + pad)
    c.scale(-1, 1)
    _draw_corner_fallback(c, 0, 0, size, s)
    c.restoreState()
    c.restoreState()


def _draw_inner_panel(c: canvas.Canvas, th: CertificateTheme, frame: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    fx, fy, fw, fh = frame
    panel_pad = 0.28 * inch
    px = fx + panel_pad
    py = fy + panel_pad
    pw = fw - 2 * panel_pad
    ph = fh - 2 * panel_pad

    # subtle panel shadow (fake)
    c.saveState()
    _set_alpha(c, 0.14 if th.panel.alpha == 1 else 0.10)
    c.setFillColor(Color(0, 0, 0, alpha=1))
    c.roundRect(px + 6, py - 6, pw, ph, 16, stroke=0, fill=1)
    c.restoreState()

    # actual panel
    c.saveState()
    c.setFillColor(th.panel)
    c.roundRect(px, py, pw, ph, 16, stroke=0, fill=1)
    c.restoreState()

    # thin panel border
    c.saveState()
    c.setStrokeColor(th.gold_2)
    c.setLineWidth(1.0)
    c.roundRect(px, py, pw, ph, 16, stroke=1, fill=0)
    c.restoreState()

    return (px, py, pw, ph)


def _draw_watermark_inside_panel(c: canvas.Canvas, th: CertificateTheme, panel: Tuple[float, float, float, float]) -> None:
    px, py, pw, ph = panel
    logo_path = _safe_asset_path("kis-logo.png")

    c.saveState()
    _set_alpha(c, th.watermark_alpha)
    if os.path.exists(logo_path):
        size = min(pw, ph) * 0.42
        c.drawImage(
            logo_path,
            px + (pw - size) / 2,
            py + (ph - size) / 2 + 0.10 * inch,
            size,
            size,
            mask="auto",
        )
    else:
        c.setFillColor(th.ink_muted)
        c.setFont("Helvetica-Bold", 96)
        c.drawCentredString(px + pw / 2, py + ph / 2, "KIS")
    c.restoreState()


def _draw_gold_seal(c: canvas.Canvas, th: CertificateTheme, x: float, y: float, r: float) -> None:
    seal_path = _safe_asset_path("kis-seal.png")
    if os.path.exists(seal_path):
        c.drawImage(seal_path, x - r, y - r, 2 * r, 2 * r, mask="auto")
        return

    # vector fallback
    c.saveState()
    c.setFillColor(th.gold_1)
    c.circle(x, y, r, stroke=0, fill=1)
    c.setFillColor(th.gold_2)
    c.circle(x, y, r * 0.86, stroke=0, fill=1)
    c.setFillColor(th.panel)
    c.circle(x, y, r * 0.70, stroke=0, fill=1)
    c.setFillColor(th.ink)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(x, y + 2, "CERTIFIED")
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(x, y - 10, "KIS • COMPLETION")
    c.restoreState()


def _draw_signature_block(
    c: canvas.Canvas,
    th: CertificateTheme,
    x: float,
    y: float,
    w: float,
    title: str,
    value: str,
) -> None:
    c.saveState()
    c.setStrokeColor(th.ink_muted)
    c.setLineWidth(0.85)
    c.line(x, y, x + w, y)

    c.setFillColor(th.ink)
    c.setFont("Helvetica-Oblique", 12)
    c.drawString(x, y + 6, value)

    c.setFillColor(th.ink_muted)
    c.setFont("Helvetica", 9)
    c.drawString(x, y - 12, title)
    c.restoreState()


# ---------------- public API

def build_certificate_pdf(
    *,
    user_name: str,
    course_title: str,
    partner_name: str = "KCAN, Kingdom Citizens & Ambassadors Network",
    brand_color: Optional[str] = None,
    logo_url: Optional[str] = None,  # kept for API compatibility; not used in PDF (images are local assets)
    issued_on: Optional[date_type] = None,
    instructor_name: str = "David Williams",
    theme: str = "light",
    credential_id: Optional[str] = None,
    wordmark: Optional[str] = None,
) -> bytes:
    """
    Returns PDF bytes.
    """
    issued_on = issued_on or date_type.today()
    th = theme_dark(brand_color) if str(theme).lower().strip() == "dark" else theme_light(brand_color)

    buf = BytesIO()
    page_w, page_h = letter
    c = canvas.Canvas(buf, pagesize=letter)

    # Background gradient (outside)
    _draw_vertical_gradient(c, 0, 0, page_w, page_h, th.bg_top, th.bg_bottom)

    # Outer frame
    frame = _draw_outer_frame(c, th, page_w, page_h)

    # Corner ornaments in frame
    _draw_corner_ornaments(c, th, *frame)

    # Inner rounded paper panel
    panel = _draw_inner_panel(c, th, frame)

    # Watermark inside panel
    _draw_watermark_inside_panel(c, th, panel)

    # ---- content layout inside panel
    px, py, pw, ph = panel
    cx = px + pw / 2

    # Title block positions (relative to panel)
    top = py + ph
    bottom = py

    # A clean vertical system tucked further down inside the panel
    y_brand = top - 0.65 * inch
    y_main = y_brand - 0.85 * inch
    y_sub = y_main - 0.45 * inch
    y_present = y_sub - 0.55 * inch
    y_name = y_present - 0.65 * inch
    y_div = y_name - 0.45 * inch
    y_course_label = y_div - 0.45 * inch
    y_course = y_course_label - 0.45 * inch
    y_partner = y_course - 0.60 * inch

    # Brand line: dynamic wordmark (defaults to KIS if nothing supplied)
    c.setFillColor(th.accent)
    c.setFont("Helvetica-Bold", 18)
    brand_line = (wordmark or "KIS").strip() or "KIS"
    c.drawCentredString(cx, y_brand, brand_line)

    # Gold divider
    c.setStrokeColor(th.gold_2)
    c.setLineWidth(1.0)
    c.line(cx - 2.2 * inch, y_brand - 0.20 * inch, cx + 2.2 * inch, y_brand - 0.20 * inch)

    # Main title: CERTIFICATE OF COMPLETION
    c.setFillColor(th.ink)
    c.setFont("Times-Bold", 46)
    c.drawCentredString(cx, y_main, "Certificate")

    c.setFillColor(th.ink_muted)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(cx, y_sub, "OF")

    c.setFillColor(th.ink)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(cx, y_sub - 0.32 * inch, "COMPLETION")

    # Another subtle divider under title
    c.setStrokeColor(th.gold_1)
    c.setLineWidth(1.0)
    c.line(cx - 2.35 * inch, y_sub - 0.55 * inch, cx + 2.35 * inch, y_sub - 0.55 * inch)

    # Presented to line
    c.setFillColor(th.ink_muted)
    c.setFont("Helvetica", 10.5)
    c.drawCentredString(cx, y_present, "THIS CERTIFICATE IS PROUDLY PRESENTED TO")

    # Name (big + elegant)
    name_max = pw * 0.84
    name_size = _fit_text(c, user_name, name_max, "Times-BoldItalic", 34, min_size=22)
    c.setFillColor(th.ink)
    c.setFont("Times-BoldItalic", name_size)
    c.drawCentredString(cx, y_name, user_name)

    # Divider under name
    c.setStrokeColor(th.gold_2)
    c.setLineWidth(0.9)
    c.line(cx - 2.15 * inch, y_div, cx + 2.15 * inch, y_div)

    # Course text
    c.setFillColor(th.ink_muted)
    c.setFont("Helvetica", 10.5)
    c.drawCentredString(cx, y_course_label, "For successfully completing the course")

    course_max = pw * 0.80
    course_size = _fit_text(c, course_title, course_max, "Helvetica-Bold", 22, min_size=13)
    c.setFillColor(th.ink)
    c.setFont("Helvetica-Bold", course_size)
    c.drawCentredString(cx, y_course, course_title)

    # Partner
    c.setFillColor(th.ink_muted)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(cx, y_partner, partner_name.upper())

    # Footer: signatures + seal
    footer_y = bottom + 1.20 * inch
    sig_w = 2.55 * inch
    left_x = px + 0.85 * inch
    right_x = px + pw - 0.85 * inch - sig_w

    _draw_signature_block(c, th, left_x, footer_y, sig_w, "Instructor", instructor_name)
    _draw_signature_block(c, th, right_x, footer_y, sig_w, "Date", issued_on.strftime("%B %d, %Y"))

    # Seal centered below partner but above signatures (classic look)
    seal_r = 0.56 * inch
    seal_x = px + pw - 1.10 * inch
    seal_y = footer_y + 0.80 * inch
    _draw_gold_seal(c, th, seal_x, seal_y, seal_r)

    # Credential ID (small, bottom-left inside panel)
    if credential_id:
        c.setFillColor(th.ink_muted)
        c.setFont("Helvetica", 8)
        c.drawString(px + 0.40 * inch, bottom + 0.42 * inch, f"Credential ID: {credential_id}")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


# ------------- optional storage helpers

def _certificate_storage_root() -> str:
    media_root = getattr(settings, "MEDIA_ROOT", "media")
    path = os.path.join(media_root, "bible", "certificates")
    os.makedirs(path, exist_ok=True)
    return path


def _relative_certificate_path(filename: str) -> str:
    return os.path.join("bible", "certificates", filename)


def _normalise_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in value)
    return safe[:64]


def ensure_certificate_file(enrollment_id: int, credential_token: Optional[str], pdf_bytes: bytes) -> str:
    components = [str(enrollment_id)]
    if credential_token:
        components.append(_normalise_filename(credential_token))
    filename = f"certificate_{'_'.join(components)}.pdf"
    dest = os.path.join(_certificate_storage_root(), filename)
    if not os.path.exists(dest):
        with open(dest, "wb") as handle:
            handle.write(pdf_bytes)
    return _relative_certificate_path(filename)


def build_certificate_url(request, relative_path: str) -> str:
    media_url = getattr(settings, "MEDIA_URL", "/media/")
    media_url = media_url if media_url.endswith("/") else f"{media_url}/"
    relative = relative_path.lstrip("/")
    return request.build_absolute_uri(f"{media_url}{relative}")
