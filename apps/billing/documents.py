from __future__ import annotations

import os
import math
import random
from datetime import datetime
from typing import Tuple, Optional

from django.utils import timezone

from apps.commerce.models import ServiceBooking, ServiceBookingReceipt

from django.conf import settings

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import html as html_module


# -----------------------------
# Storage helpers (unchanged)
# -----------------------------
def _receipt_storage_root() -> str:
    media_root = getattr(settings, "MEDIA_ROOT", "media")
    path = os.path.join(media_root, "billing", "receipts")
    os.makedirs(path, exist_ok=True)
    return path


def _relative_path(filename: str) -> str:
    return os.path.join("billing", "receipts", filename)


def _booking_receipt_storage_root() -> str:
    media_root = getattr(settings, "MEDIA_ROOT", "media")
    path = os.path.join(media_root, "billing", "booking-receipts")
    os.makedirs(path, exist_ok=True)
    return path


def _booking_relative_path(filename: str) -> str:
    return os.path.join("billing", "booking-receipts", filename)


def _build_media_url(request, relative_path: str) -> str:
    media_url = getattr(settings, "MEDIA_URL", "/media/")
    media_url = media_url if media_url.endswith("/") else f"{media_url}/"
    relative = relative_path.lstrip("/")
    return request.build_absolute_uri(f"{media_url}{relative}")


def _public_currency_label(value: object | None) -> str:
    code = str(value or "").strip().upper()
    if code == "USD":
        return "USD"
    if code in {"KISC", "KIS"}:
        return "Historical promotional credits"
    return code or "USD"


# -----------------------------
# Font setup (optional)
# -----------------------------
def _try_register_font(ttf_path: str, family_name: str) -> bool:
    """
    Optional: if you have premium fonts (e.g. PlayfairDisplay, Inter),
    register them here and use family_name in setFont calls.

    Safe fallback: built-in Helvetica/Times.
    """
    try:
        if os.path.exists(ttf_path):
            pdfmetrics.registerFont(TTFont(family_name, ttf_path))
            return True
    except Exception:
        return False
    return False


# -----------------------------
# Design primitives
# -----------------------------
def _rounded_rect(c: canvas.Canvas, x: float, y: float, w: float, h: float, r: float,
                  fill_color=None, stroke_color=None, stroke_width: float = 1,
                  fill: int = 1, stroke: int = 0):
    if fill_color is not None:
        c.setFillColor(fill_color)
    if stroke_color is not None:
        c.setStrokeColor(stroke_color)
    c.setLineWidth(stroke_width)
    c.roundRect(x, y, w, h, r, fill=fill, stroke=stroke)


def _soft_shadow(c: canvas.Canvas, x: float, y: float, w: float, h: float, r: float,
                 layers: int = 10, dx: float = 0, dy: float = -2):
    """
    Fake a soft shadow by drawing multiple slightly larger translucent rounds.
    """
    # ReportLab alpha works via Color(alpha=...) in newer versions; if not, it still looks OK.
    for i in range(layers):
        grow = i * 1.2
        alpha = max(0.02, 0.12 - i * 0.01)
        shadow = colors.Color(0, 0, 0, alpha=alpha)
        _rounded_rect(
            c,
            x - grow + dx,
            y - grow + dy,
            w + 2 * grow,
            h + 2 * grow,
            r + grow * 0.6,
            fill_color=shadow,
            stroke_color=None,
            stroke_width=0,
            fill=1,
            stroke=0,
        )


def _draw_wave_gold(c: canvas.Canvas, page_w: float, page_h: float):
    """
    Gold wave accent like your receipt mock.
    """
    gold1 = colors.Color(0.93, 0.79, 0.47)
    gold2 = colors.Color(0.78, 0.61, 0.30)

    c.saveState()
    c.setLineWidth(4)
    c.setStrokeColor(gold1)
    y = page_h * 0.80
    path = c.beginPath()
    path.moveTo(page_w * 0.05, y)
    path.curveTo(
        page_w * 0.35, y + 30,
        page_w * 0.65, y - 25,
        page_w * 0.95, y + 10,
    )
    c.drawPath(path, stroke=1, fill=0)

    c.setLineWidth(2)
    c.setStrokeColor(gold2)
    y2 = page_h * 0.77
    path = c.beginPath()
    path.moveTo(page_w * 0.05, y2)
    path.curveTo(
        page_w * 0.35, y2 + 22,
        page_w * 0.65, y2 - 18,
        page_w * 0.95, y2 + 8,
    )
    c.drawPath(path, stroke=1, fill=0)
    c.restoreState()


def _draw_bokeh(c: canvas.Canvas, page_w: float, page_h: float, seed: int = 7):
    """
    Subtle circular light bokeh on the receipt background.
    """
    rnd = random.Random(seed)
    c.saveState()
    for _ in range(42):
        r = rnd.uniform(8, 38)
        x = rnd.uniform(page_w * 0.55, page_w * 0.98)
        y = rnd.uniform(page_h * 0.05, page_h * 0.78)
        alpha = rnd.uniform(0.03, 0.10)
        col = colors.Color(1, 0.85, 0.55, alpha=alpha)
        c.setFillColor(col)
        c.circle(x, y, r, fill=1, stroke=0)
    c.restoreState()


def _draw_watercolor(c: canvas.Canvas, page_w: float, page_h: float, seed: int = 12):
    """
    Pastel watercolor blobs for the invoice background.
    """
    rnd = random.Random(seed)
    palette = [
        colors.Color(0.98, 0.78, 0.66, alpha=0.18),  # peach
        colors.Color(0.70, 0.90, 0.92, alpha=0.18),  # aqua
        colors.Color(0.78, 0.73, 0.92, alpha=0.16),  # lavender
        colors.Color(0.92, 0.92, 0.92, alpha=0.10),  # soft gray
    ]
    c.saveState()
    for i in range(10):
        col = palette[i % len(palette)]
        c.setFillColor(col)
        cx = rnd.uniform(page_w * 0.05, page_w * 0.95)
        cy = rnd.uniform(page_h * 0.15, page_h * 0.95)
        base = rnd.uniform(110, 220)
        # draw an irregular blob by plotting a "wobbly" circle
        path = c.beginPath()
        steps = 22
        for s in range(steps + 1):
            ang = (2 * math.pi) * (s / steps)
            wobble = rnd.uniform(-0.18, 0.18)
            rr = base * (1 + wobble)
            x = cx + rr * math.cos(ang)
            y = cy + rr * math.sin(ang)
            if s == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
    c.restoreState()


def _kv_rows(c: canvas.Canvas, x: float, y_top: float, w: float,
             rows: list[tuple[str, str]],
             label_font: str, value_font: str,
             label_size: int = 11, value_size: int = 11,
             line_gap: float = 20,
             divider_alpha: float = 0.12):
    """
    Clean key/value rows with faint dividers.
    """
    c.saveState()
    label_color = colors.Color(0.15, 0.18, 0.22)
    value_color = colors.Color(0.10, 0.12, 0.15)
    divider = colors.Color(0, 0, 0, alpha=divider_alpha)

    label_w = w * 0.34
    value_x = x + label_w + 10

    y = y_top
    for (k, v) in rows:
        c.setFillColor(label_color)
        c.setFont(label_font, label_size)
        c.drawString(x, y, f"{k}:")

        c.setFillColor(value_color)
        c.setFont(value_font, value_size)
        c.drawString(value_x, y, v)

        # divider line
        c.setStrokeColor(divider)
        c.setLineWidth(1)
        c.line(x, y - 10, x + w, y - 10)

        y -= line_gap

    c.restoreState()
    return y


def _draw_line_item_table(
    c: canvas.Canvas,
    x: float,
    y_top: float,
    w: float,
    rows: list[tuple[str, str]],
    *,
    row_height: float = 28,
) -> None:
    """
    Draws a subtle table for invoice line items.
    """
    if not rows:
        return

    total_height = len(rows) * row_height + 16
    header_y = y_top - 12
    box_y = header_y - total_height - 4

    c.saveState()
    # pale panel behind the table
    c.setFillColor(colors.Color(1, 1, 1, alpha=0.95))
    c.roundRect(x - 8, box_y, w + 16, total_height + 20, 12, fill=1, stroke=0)

    # header
    c.setFillColor(colors.Color(0.10, 0.13, 0.20))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, header_y, "Charges & adjustments")

    # divider below header
    c.setStrokeColor(colors.Color(0, 0, 0, alpha=0.07))
    c.setLineWidth(1)
    c.line(x, header_y - 6, x + w, header_y - 6)

    current_y = header_y - row_height
    for label, value in rows:
        c.setFillColor(colors.Color(0.15, 0.18, 0.22))
        c.setFont("Helvetica", 11)
        c.drawString(x, current_y, label)

        c.setFillColor(colors.Color(0.04, 0.07, 0.14))
        c.setFont("Helvetica-Bold", 12)
        c.drawRightString(x + w, current_y, value)

        # finer divider
        c.setStrokeColor(colors.Color(0, 0, 0, alpha=0.08))
        c.setLineWidth(0.6)
        c.line(x, current_y - 12, x + w, current_y - 12)
        current_y -= row_height
    c.restoreState()


def _draw_footer_center(c: canvas.Canvas, page_w: float, text: str, y: float,
                        font: str = "Helvetica-Oblique", size: int = 11,
                        color=colors.Color(1, 1, 1, alpha=0.85)):
    c.saveState()
    c.setFillColor(color)
    c.setFont(font, size)
    tw = pdfmetrics.stringWidth(text, font, size)
    c.drawString((page_w - tw) / 2, y, text)
    c.restoreState()


# -----------------------------
# Beautiful PDF renderers
# -----------------------------
def render_receipt_pdf(tx, path: str) -> None:
    """
    Premium navy + gold receipt (matches your mock style).
    """
    c = canvas.Canvas(path, pagesize=letter)
    page_w, page_h = letter

    navy = colors.Color(0.06, 0.11, 0.18)
    navy2 = colors.Color(0.08, 0.14, 0.24)
    gold = colors.Color(0.93, 0.79, 0.47)

    # Background
    c.setFillColor(navy)
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    # Subtle top gradient-ish overlay (simple: big translucent rect)
    c.setFillColor(colors.Color(navy2.red, navy2.green, navy2.blue, alpha=0.65))
    c.rect(0, page_h * 0.55, page_w, page_h * 0.45, fill=1, stroke=0)

    _draw_wave_gold(c, page_w, page_h)
    _draw_bokeh(c, page_w, page_h, seed=hash(getattr(tx, "tx_ref", "tx")) % 1000)

    # Header: "KIS" + "Receipt"
    c.saveState()
    c.setFillColor(gold)
    c.setFont("Times-Bold", 46)
    c.drawCentredString(page_w / 2, page_h * 0.87, "KIS")

    c.setFillColor(colors.Color(1, 1, 1, alpha=0.92))
    c.setFont("Helvetica-Oblique", 26)
    c.drawCentredString(page_w / 2, page_h * 0.835, "Receipt")
    c.restoreState()

    # Card
    card_w = page_w * 0.78
    card_h = page_h * 0.50
    card_x = (page_w - card_w) / 2
    card_y = page_h * 0.24

    _soft_shadow(c, card_x, card_y, card_w, card_h, r=14, layers=12, dy=-4)
    _rounded_rect(c, card_x, card_y, card_w, card_h, r=14,
                  fill_color=colors.Color(0.96, 0.97, 0.98),
                  stroke_color=colors.Color(0, 0, 0, alpha=0.08),
                  stroke_width=1, fill=1, stroke=1)

    # Rows
    created_at = tx.created_at.strftime("%Y-%m-%d %H:%M:%S") if getattr(tx, "created_at", None) else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    intent = (getattr(tx, "meta", None) or {}).get("intent", "payment")

    rows = [
        ("Reference", str(getattr(tx, "tx_ref", "N/A"))),
        ("Status", str(getattr(tx, "status", "N/A")).capitalize()),
        ("Amount", f"${getattr(tx, 'amount_cents', 0) / 100:.2f} {getattr(tx, 'currency', 'USD')}"),
        ("Intent", str(intent).capitalize()),
        ("Provider", str(getattr(tx, "provider", "N/A"))),
        ("Method", str(getattr(tx, "method", None) or "n/a")),
        ("Date", created_at),
        ("Metadata", str(getattr(tx, "meta", None) or {})),
    ]

    # Inner padding
    pad = 22
    inner_x = card_x + pad
    inner_w = card_w - 2 * pad
    y_top = card_y + card_h - 44

    c.saveState()
    c.setFillColor(colors.Color(0.12, 0.14, 0.18))
    c.setFont("Helvetica-Bold", 13)
    c.drawString(inner_x, y_top + 18, "Transaction Details")
    c.restoreState()

    _kv_rows(
        c,
        x=inner_x,
        y_top=y_top - 10,
        w=inner_w,
        rows=rows,
        label_font="Helvetica-Bold",
        value_font="Helvetica",
        label_size=11,
        value_size=11,
        line_gap=22,
        divider_alpha=0.10,
    )

    # Footer
    _draw_footer_center(c, page_w, "Thank you for your business!", y=page_h * 0.13,
                        font="Helvetica-Oblique", size=13,
                        color=colors.Color(1, 1, 1, alpha=0.90))
    _draw_footer_center(c, page_w, "www.kis-billing.com", y=page_h * 0.095,
                        font="Helvetica", size=10,
                        color=colors.Color(1, 1, 1, alpha=0.65))

    c.showPage()
    c.save()


def render_booking_receipt_pdf(booking: ServiceBooking, receipt: Optional[ServiceBookingReceipt], path: str) -> None:
    """
    Styled PDF tailored for a commerce service booking.
    """
    c = canvas.Canvas(path, pagesize=letter)
    page_w, page_h = letter

    navy = colors.Color(0.06, 0.11, 0.18)
    navy2 = colors.Color(0.08, 0.14, 0.24)
    gold = colors.Color(0.93, 0.79, 0.47)

    c.setFillColor(navy)
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)
    c.setFillColor(colors.Color(navy2.red, navy2.green, navy2.blue, alpha=0.65))
    c.rect(0, page_h * 0.55, page_w, page_h * 0.45, fill=1, stroke=0)
    _draw_wave_gold(c, page_w, page_h)
    _draw_bokeh(c, page_w, page_h, seed=hash(str(booking.id)) % 1000)

    phase_label = _receipt_phase_label(receipt)

    c.saveState()
    c.setFillColor(gold)
    c.setFont("Times-Bold", 46)
    c.drawCentredString(page_w / 2, page_h * 0.87, "KIS")
    c.setFillColor(colors.Color(1, 1, 1, alpha=0.92))
    c.setFont("Helvetica-Oblique", 26)
    c.drawCentredString(page_w / 2, page_h * 0.835, phase_label)
    c.restoreState()

    card_w = page_w * 0.78
    card_h = page_h * 0.50
    card_x = (page_w - card_w) / 2
    card_y = page_h * 0.24

    _soft_shadow(c, card_x, card_y, card_w, card_h, r=14, layers=12, dy=-4)
    _rounded_rect(
        c,
        card_x,
        card_y,
        card_w,
        card_h,
        r=14,
        fill_color=colors.Color(0.96, 0.97, 0.98),
        stroke_color=colors.Color(0, 0, 0, alpha=0.08),
        stroke_width=1,
        fill=1,
        stroke=1,
    )

    def _display_name(user):
        if not user:
            return "Customer"
        return (
            getattr(user, "display_name", "") or getattr(user, "username", "")
            or getattr(user, "phone", "") or "Customer"
        )

    service_name = getattr(booking.service, "name", "Service")
    shop = booking.shop
    provider_user = getattr(shop, "owner", None) if shop else None
    provider_name = _display_name(provider_user)
    payer_name = _display_name(booking.user)
    instructions = booking.instructions or "—"
    scheduled = booking.scheduled_at
    scheduled_str = (
        timezone.localtime(scheduled).strftime("%B %d, %Y %I:%M %p %Z")
        if scheduled else "TBD"
    )
    status_label = str(booking.status or "pending").replace("_", " ").capitalize()
    payment = getattr(booking, "payment", None)
    amount_cents = (
        (receipt.amount_cents if receipt and receipt.amount_cents else 0)
        or booking.price_cents
        or getattr(payment, "amount_cents", 0)
        or 0
    )
    currency = (
        (receipt.currency if receipt and receipt.currency else None)
        or (getattr(payment, "currency", "USD") or "USD")
    )
    currency_label = _public_currency_label(currency)
    receipt_ref = getattr(receipt, "transaction_reference", "") if receipt else ""
    payment_ref = (
        (receipt_ref.strip() if receipt_ref and receipt_ref.strip() else None)
        or getattr(payment, "transaction_reference", "")
        or booking.payment_tx_ref
        or "—"
    )
    phase_label = _receipt_phase_label(receipt)

    rows = [
        ("Booking reference", str(booking.id)),
        ("Service", service_name),
        ("Provider", provider_name),
        ("Scheduled for", scheduled_str),
        ("Status", status_label),
        ("Amount", f"{currency_label} {amount_cents / 100:.2f}"),
        ("Payment reference", payment_ref),
        ("Customer", payer_name),
        ("Instructions", instructions),
    ]

    pad = 22
    inner_x = card_x + pad
    inner_w = card_w - 2 * pad
    y_top = card_y + card_h - 44

    c.saveState()
    c.setFillColor(colors.Color(0.12, 0.14, 0.18))
    c.setFont("Helvetica-Bold", 13)
    c.drawString(inner_x, y_top + 18, "Booking details")
    c.restoreState()

    _kv_rows(
        c,
        inner_x,
        y_top,
        inner_w,
        rows,
        label_font="Helvetica",
        value_font="Helvetica-Bold",
        label_size=12,
        value_size=12,
        line_gap=24,
    )

    support_y = card_y + 24
    c.saveState()
    c.setFillColor(colors.Color(0.12, 0.16, 0.22, alpha=0.75))
    c.setFont("Helvetica", 10.5)
    c.drawCentredString(page_w / 2, support_y, "Questions? Email support@kis-billing.com with your booking reference.")
    c.restoreState()

    c.showPage()
    c.save()

def render_invoice_pdf(sub, path: str) -> None:
    """
    Pastel watercolor invoice (matches your mock style).
    """
    c = canvas.Canvas(path, pagesize=letter)
    page_w, page_h = letter

    paper = colors.Color(0.98, 0.97, 0.95)
    navy_text = colors.Color(0.08, 0.12, 0.22)
    accent = colors.Color(0.16, 0.28, 0.45)

    c.setFillColor(paper)
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)
    _draw_watercolor(c, page_w, page_h, seed=hash(str(getattr(sub, 'id', 'sub'))) % 1000)
    _draw_wave_gold(c, page_w, page_h)

    c.setFillColor(navy_text)
    c.setFont("Times-Bold", 44)
    c.drawCentredString(page_w / 2, page_h * 0.88, "KIS")
    c.setFont("Helvetica-Oblique", 28)
    c.drawCentredString(page_w / 2, page_h * 0.83, "Invoice")

    card_w = page_w * 0.78
    card_h = page_h * 0.56
    card_x = (page_w - card_w) / 2
    card_y = page_h * 0.22

    _soft_shadow(c, card_x, card_y, card_w, card_h, r=16, layers=14, dy=-4)
    _rounded_rect(
        c,
        card_x,
        card_y,
        card_w,
        card_h,
        r=18,
        fill_color=colors.Color(1, 1, 1, alpha=0.92),
        stroke_color=colors.Color(0, 0, 0, alpha=0.10),
        stroke_width=1.2,
        fill=1,
        stroke=1,
    )

    pad = 26
    inner_x = card_x + pad
    inner_w = card_w - 2 * pad
    ribbon_height = 28
    ribbon_y = card_y + card_h - ribbon_height - 10

    ribbon_w = inner_w * 0.85
    ribbon_x = inner_x + (inner_w - ribbon_w) / 2
    c.saveState()
    c.setFillColor(accent)
    c.roundRect(ribbon_x, ribbon_y, ribbon_w, ribbon_height, 10, fill=1, stroke=0)
    c.restoreState()

    c.saveState()
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(ribbon_x + ribbon_w / 2, ribbon_y + 8, "BILLING SUMMARY")
    c.restoreState()

    tier_name = sub.tier.name if getattr(sub, "tier", None) else "N/A"
    status = str(getattr(sub, "status", "n/a")).capitalize()
    period_start = sub.started_at.strftime("%Y-%m-%d") if getattr(sub, "started_at", None) else "N/A"
    period_end = sub.ends_at.strftime("%Y-%m-%d") if getattr(sub, "ends_at", None) else "Open"

    summary_rows = [
        ("Invoice", f"INV-{getattr(sub, 'id', 'N/A')}"),
        ("Tier", tier_name),
        ("Status", status),
        ("Period", f"{period_start} — {period_end}"),
    ]

    y_anchor = ribbon_y - 32

    last_y = _kv_rows(
        c,
        x=inner_x,
        y_top=y_anchor,
        w=inner_w,
        rows=summary_rows,
        label_font="Helvetica-Bold",
        value_font="Helvetica",
        label_size=12,
        value_size=12,
        line_gap=26,
        divider_alpha=0.12,
    )

    billed_to = getattr(sub.user, "display_name", None) or getattr(sub.user, "email", "KIS Member")
    c.saveState()
    c.setFillColor(accent)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(inner_x, last_y - 8, f"Invoiced to {billed_to}")
    c.restoreState()

    meta = getattr(sub, "billing_meta", {}) or {}
    line_items: list[tuple[str, str]] = []
    tier_price = getattr(sub.tier, "price_cents", 0) or 0
    currency = str(meta.get("currency") or "USD").upper()

    def format_money(cents: int) -> str:
        return f"{currency} {cents / 100:.2f}"

    line_items.append(("Tier price", format_money(tier_price)))
    downgrade_to = meta.get("downgrade_to")
    if downgrade_to:
        line_items.append(("Downgrade target", str(downgrade_to)))

    proration = int(meta.get("proration_credit_cents") or 0)
    if proration:
        line_items.append(("Proration credit", f"-{format_money(abs(proration))}"))

    total_cents = max(tier_price - proration, 0)
    line_items.append(("Total due", format_money(total_cents)))

    table_top = last_y - 40
    _draw_line_item_table(c, inner_x, table_top, inner_w, line_items)

    support_y = card_y + 24
    c.saveState()
    c.setFillColor(colors.Color(0.12, 0.16, 0.22, alpha=0.75))
    c.setFont("Helvetica", 10.5)
    c.drawCentredString(page_w / 2, support_y, "Questions? Email support@kis-billing.com with your invoice reference.")
    c.restoreState()

    c.showPage()
    c.save()


def _render_receipt_html(tx) -> str:
    """
    Returns a styled HTML receipt mirroring the premium PDF vibe.
    """
    def _safe(value):
        return html_module.escape(str(value)) if value is not None else "—"

    created = getattr(tx, "created_at", None)
    created_str = created.strftime("%B %d, %Y %I:%M %p") if created else "N/A"
    amount = getattr(tx, "amount_cents", 0) or 0
    currency = (getattr(tx, "currency", "USD") or "USD").upper()
    meta = getattr(tx, "meta", None) or {}
    intent = str(meta.get("intent", "payment")).capitalize()
    provider = _safe(getattr(tx, "provider", "KIS"))
    method = _safe(getattr(tx, "method", "N/A"))

    rows = [
        ("Reference", _safe(getattr(tx, "tx_ref", "N/A"))),
        ("Status", _safe(getattr(tx, "status", "pending")).capitalize()),
        ("Amount", f"{currency} {amount / 100:.2f}"),
        ("Intent", intent),
        ("Provider", provider),
        ("Method", method),
        ("Date", created_str),
        ("Metadata", _safe(meta)),
    ]

    meta_lines = "<br>".join(
        f"<strong>{html_module.escape(str(k))}:</strong> {html_module.escape(str(v))}"
        for k, v in meta.items()
    ) or "—"

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>KIS Receipt</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
        background: radial-gradient(circle at top, #08132a, #040710 55%);
        color: #0c1320;
      }}
      .page {{
        max-width: 960px;
        margin: 0 auto;
        padding: 48px 24px 64px;
      }}
      .hero {{
        text-align: center;
        color: #f8f8f8;
      }}
      .hero h1 {{
        margin: 0;
        font-size: 48px;
        letter-spacing: 0.2em;
      }}
      .hero .pill {{
        display: inline-block;
        margin-top: 12px;
        padding: 6px 18px;
        border-radius: 999px;
        background: linear-gradient(135deg, #f2c94c, #f2994a);
        color: #0c1222;
        font-weight: 600;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.3em;
      }}
      .card {{
        margin-top: 32px;
        background: #f5f6fa;
        border-radius: 24px;
        padding: 32px;
        box-shadow: 0 25px 45px rgba(2, 7, 18, 0.45);
        border: 1px solid rgba(255, 255, 255, 0.4);
      }}
      .section-title {{
        font-size: 20px;
        font-weight: 600;
        margin-bottom: 16px;
        color: #101828;
      }}
      .table {{
        width: 100%;
        border-collapse: collapse;
      }}
      .table th,
      .table td {{
        text-align: left;
        padding: 12px 8px;
        font-size: 15px;
      }}
      .table th {{
        font-weight: 600;
        color: #475467;
      }}
      .divider {{
        border-top: 1px dashed rgba(15, 23, 42, 0.2);
        margin: 24px 0;
      }}
      .footer {{
        margin-top: 32px;
        font-size: 13px;
        color: #475467;
        text-align: center;
      }}
      .cta {{
        margin-top: 20px;
        text-align: center;
      }}
      .cta a {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 12px 26px;
        border-radius: 999px;
        background: linear-gradient(135deg, #0ea5e9, #9333ea);
        color: #fff;
        text-decoration: none;
        font-weight: 600;
        font-size: 14px;
        box-shadow: 0 12px 24px rgba(14, 165, 233, 0.35);
      }}
      .cta a:hover {{
        box-shadow: 0 14px 30px rgba(14, 165, 233, 0.45);
      }}
      .meta-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 16px;
      }}
      .meta-card {{
        background: #fff;
        border-radius: 16px;
        padding: 14px 18px;
        border: 1px solid rgba(15, 23, 42, 0.08);
        font-size: 14px;
        color: #0f172a;
      }}
      .meta-card strong {{
        display: block;
        font-size: 12px;
        text-transform: uppercase;
        color: #475467;
        letter-spacing: 0.3em;
        margin-bottom: 6px;
      }}
      @media (max-width: 640px) {{
        .card {{
          padding: 24px;
        }}
        .hero h1 {{
          font-size: 36px;
        }}
      }}
    </style>
  </head>
  <body>
    <div class="page">
      <div class="hero">
        <div class="pill">Receipt</div>
        <h1>KIS</h1>
        <p style="margin-top: 8px; opacity: 0.7;">
          Premium receipt generated securely for your transaction.
        </p>
      </div>
      <div class="card">
        <div class="section-title">Transaction summary</div>
        <table class="table">
          {"".join(f"<tr><th>{html_module.escape(key)}</th><td>{html_module.escape(value)}</td></tr>" for key, value in rows)}
        </table>
        <div class="divider"></div>
        <div class="section-title">Metadata</div>
        <div class="meta-grid">
          <div class="meta-card">
            <strong>Raw metadata</strong>
            <div style="font-family: 'Courier New', monospace; line-height: 1.4; font-size: 13px;">
              {meta_lines}
            </div>
          </div>
          <div class="meta-card">
            <strong>Intent</strong>
            <div>{intent}</div>
            <strong style="margin-top: 14px;">Provider</strong>
            <div>{provider}</div>
          </div>
        </div>
        <div class="cta">
          <a href="{_safe(f'{getattr(tx, "tx_ref", "receipt")}.pdf')}" target="_blank">Download beautiful PDF</a>
        </div>
      </div>
      <div class="footer">
        Need help? Reach out to <a href="mailto:support@kis-billing.com">support@kis-billing.com</a> with your receipt reference.
      </div>
    </div>
  </body>
</html>"""
    return html


def _receipt_phase_label(receipt: Optional[ServiceBookingReceipt]) -> str:
    if not receipt:
        return "Booking receipt"
    labels = {
        ServiceBookingReceipt.PHASE_DEPOSIT: "Deposit receipt",
        ServiceBookingReceipt.PHASE_REMAINING: "Final payment receipt",
    }
    return labels.get(receipt.phase, receipt.phase.replace("_", " ").title())


def _render_booking_receipt_html(booking, receipt: Optional[ServiceBookingReceipt]) -> str:
    def _safe(value):
        return html_module.escape(str(value)) if value is not None else "—"

    def _display_name(user):
        if not user:
            return "Customer"
        return (
            getattr(user, "display_name", "") or getattr(user, "username", "")
            or getattr(user, "phone", "") or "Customer"
        )

    created = getattr(booking, "created_at", None)
    created_str = (
        timezone.localtime(created).strftime("%B %d, %Y %I:%M %p")
        if created else "N/A"
    )
    scheduled = getattr(booking, "scheduled_at", None)
    scheduled_str = (
        timezone.localtime(scheduled).strftime("%B %d, %Y %I:%M %p %Z")
        if scheduled else "TBD"
    )
    service_name = _safe(getattr(booking.service, "name", "Service"))
    shop = getattr(booking, "shop", None)
    provider_user = getattr(shop, "owner", None) if shop else None
    provider_name = _safe(_display_name(provider_user))
    payer_name = _safe(_display_name(booking.user))
    instructions = _safe(booking.instructions or "—")
    payment = getattr(booking, "payment", None)
    amount = (
        (receipt.amount_cents if receipt and receipt.amount_cents else 0)
        or booking.price_cents
        or getattr(payment, "amount_cents", 0)
        or 0
    )
    currency = (
        (receipt.currency if receipt and receipt.currency else None)
        or (getattr(payment, "currency", "USD") or "USD")
    )
    currency_label = _public_currency_label(currency)
    receipt_ref = str(getattr(receipt, "transaction_reference", "") or "").strip() if receipt else ""
    payment_ref = (
        receipt_ref
        or getattr(payment, "transaction_reference", "")
        or booking.payment_tx_ref
        or "—"
    )
    phase_label = _receipt_phase_label(receipt)
    rows = [
        ("Booking reference", _safe(booking.id)),
        ("Service", service_name),
        ("Provider", provider_name),
        ("Scheduled for", _safe(scheduled_str)),
        ("Status", _safe(str(booking.status or "pending").replace("_", " ").capitalize())),
        ("Amount", f"{currency_label} {amount / 100:.2f}"),
        ("Payment reference", _safe(payment_ref)),
        ("Customer", payer_name),
        ("Instructions", instructions),
    ]

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>KIS Booking receipt</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
        background: linear-gradient(180deg, #031028, #050b1e 45%, #050a14 85%);
        color: #f7f7f7;
      }}
      .page {{
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 28px;
        gap: 18px;
      }}
      .card {{
        width: min(720px, 100%);
        border-radius: 18px;
        padding: 24px;
        background: rgba(255, 255, 255, 0.96);
        color: #111;
        box-shadow: 0 18px 45px rgba(4, 9, 23, 0.45);
      }}
      .header {{
        text-align: center;
        margin-bottom: 12px;
      }}
      .header h1 {{
        margin: 0;
        font-size: 32px;
        letter-spacing: 2px;
        color: #d89f34;
      }}
      .header p {{
        margin: 4px 0 0;
        font-size: 14px;
        color: #555;
      }}
      .phase-label {{
        margin-top: 4px;
        font-size: 12px;
        letter-spacing: 0.6px;
        color: #d89f34;
        text-transform: uppercase;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 14px;
        margin-top: 18px;
      }}
      .row {{
        border-bottom: 1px solid rgba(17, 25, 39, 0.12);
        padding-bottom: 6px;
        display: flex;
        flex-direction: column;
      }}
      .row span {{
        font-size: 12px;
        color: #6c6c6c;
      }}
      .row strong {{
        font-size: 15px;
        color: #111;
      }}
    </style>
  </head>
  <body>
    <div class="page">
      <div class="card">
        <div class="header">
          <h1>KIS Booking receipt</h1>
          <p class="phase-label">{phase_label}</p>
          <p>Generated on {created_str}</p>
        </div>
        <div class="grid">
          {''.join(f'''
            <div class="row">
              <span>{html_module.escape(key)}</span>
              <strong>{value}</strong>
            </div>
          ''' for key, value in rows)}
        </div>
        <p style="margin-top: 18px; font-size: 12px; color: #888;">
          Need help? Email support@kis-billing.com with your booking reference.
        </p>
      </div>
    </div>
  </body>
</html>
"""
    return html


# -----------------------------
# Plug into your existing API
# -----------------------------
def ensure_receipt_documents(tx) -> Tuple[str, str]:
    storage_root = _receipt_storage_root()
    html_name = f"{tx.tx_ref}.html"
    pdf_name = f"{tx.tx_ref}.pdf"
    html_path = os.path.join(storage_root, html_name)
    pdf_path = os.path.join(storage_root, pdf_name)

    # keep your HTML if you want (optional)
    if not os.path.exists(html_path):
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(_render_receipt_html(tx))

    if not os.path.exists(pdf_path):
        render_receipt_pdf(tx, pdf_path)

    return _relative_path(html_name), _relative_path(pdf_name)


def ensure_booking_receipt_documents(
    booking: ServiceBooking,
    receipt: Optional[ServiceBookingReceipt] = None,
    force: bool = False,
) -> Tuple[str, str]:
    storage_root = _booking_receipt_storage_root()
    suffix = f"-{receipt.phase}-{receipt.id}" if receipt else ""
    base_name = f"booking-{booking.id}{suffix}"
    html_name = f"{base_name}.html"
    pdf_name = f"{base_name}.pdf"
    html_path = os.path.join(storage_root, html_name)
    pdf_path = os.path.join(storage_root, pdf_name)

    if force:
        for target in (html_path, pdf_path):
            if os.path.exists(target):
                os.remove(target)

    if not os.path.exists(html_path):
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(_render_booking_receipt_html(booking, receipt))
    if not os.path.exists(pdf_path):
        render_booking_receipt_pdf(booking, receipt, pdf_path)

    return _booking_relative_path(html_name), _booking_relative_path(pdf_name)


def ensure_invoice_documents(sub) -> Tuple[str, str]:
    storage_root = _receipt_storage_root()
    base_name = f"invoice_{sub.id}"
    html_name = f"{base_name}.html"
    pdf_name = f"{base_name}.pdf"
    html_path = os.path.join(storage_root, html_name)
    pdf_path = os.path.join(storage_root, pdf_name)

    if not os.path.exists(html_path):
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(f"<html><body><h2>KIS Invoice</h2><p>{sub.id}</p></body></html>")

    if not os.path.exists(pdf_path):
        render_invoice_pdf(sub, pdf_path)

    return _relative_path(html_name), _relative_path(pdf_name)


def build_receipt_urls(request, tx) -> Tuple[str, str]:
    html_rel, pdf_rel = ensure_receipt_documents(tx)
    return _build_media_url(request, html_rel), _build_media_url(request, pdf_rel)


def build_invoice_urls(request, sub) -> Tuple[str, str]:
    html_rel, pdf_rel = ensure_invoice_documents(sub)
    return _build_media_url(request, html_rel), _build_media_url(request, pdf_rel)


def build_booking_receipt_urls(
    request,
    booking: ServiceBooking,
    receipt: Optional[ServiceBookingReceipt] = None,
    force: bool = False,
) -> Tuple[str, str]:
    html_rel, pdf_rel = ensure_booking_receipt_documents(booking, receipt, force=force)
    return _build_media_url(request, html_rel), _build_media_url(request, pdf_rel)
