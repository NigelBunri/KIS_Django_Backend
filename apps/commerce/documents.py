from __future__ import annotations

import math
import random
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _draw_wave_gold(c, page_w, page_h):
    gold1 = colors.Color(0.93, 0.79, 0.47)
    gold2 = colors.Color(0.78, 0.61, 0.30)
    c.saveState()
    c.setLineWidth(4)
    c.setStrokeColor(gold1)
    y = page_h * 0.78
    path = c.beginPath()
    path.moveTo(page_w * 0.05, y)
    path.curveTo(page_w * 0.35, y + 28, page_w * 0.65, y - 20, page_w * 0.95, y + 12)
    c.drawPath(path, stroke=1, fill=0)
    c.setLineWidth(2)
    c.setStrokeColor(gold2)
    y2 = page_h * 0.75
    path = c.beginPath()
    path.moveTo(page_w * 0.05, y2)
    path.curveTo(page_w * 0.35, y2 + 22, page_w * 0.65, y2 - 18, page_w * 0.95, y2 + 10)
    c.drawPath(path, stroke=1, fill=0)
    c.restoreState()


def _draw_watercolor(c, page_w, page_h, seed=12):
    rnd = random.Random(seed)
    palette = [
        colors.Color(0.98, 0.78, 0.66, alpha=0.18),
        colors.Color(0.70, 0.90, 0.92, alpha=0.16),
        colors.Color(0.78, 0.73, 0.92, alpha=0.14),
        colors.Color(0.92, 0.92, 0.92, alpha=0.10),
    ]
    c.saveState()
    for i in range(8):
        col = palette[i % len(palette)]
        c.setFillColor(col)
        cx = rnd.uniform(page_w * 0.05, page_w * 0.95)
        cy = rnd.uniform(page_h * 0.2, page_h * 0.8)
        base = rnd.uniform(80, 160)
        path = c.beginPath()
        steps = 20
        for s in range(steps + 1):
            ang = (2 * math.pi) * (s / steps)
            wobble = rnd.uniform(-0.2, 0.2)
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


def _draw_bokeh(c, page_w, page_h, seed=7):
    rnd = random.Random(seed)
    c.saveState()
    for _ in range(32):
        r = rnd.uniform(6, 28)
        x = rnd.uniform(page_w * 0.55, page_w * 0.98)
        y = rnd.uniform(page_h * 0.05, page_h * 0.75)
        alpha = rnd.uniform(0.02, 0.12)
        col = colors.Color(1, 0.85, 0.55, alpha=alpha)
        c.setFillColor(col)
        c.circle(x, y, r, fill=1, stroke=0)
    c.restoreState()


def render_marketplace_receipt_pdf(order):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    page_w, page_h = letter

    _draw_watercolor(c, page_w, page_h)
    _draw_wave_gold(c, page_w, page_h)
    _draw_bokeh(c, page_w, page_h)

    c.setFillColor(colors.HexColor('#1E1E1E'))
    c.setFont('Helvetica-Bold', 20)
    c.drawString(60, page_h - 90, 'KISC Marketplace Receipt')
    c.setFont('Helvetica', 11)
    buyer_label = getattr(order.buyer, 'get_full_name', None)
    if callable(buyer_label):
        buyer_label = buyer_label()
    else:
        buyer_label = getattr(order.buyer, 'username', 'Unknown')
    c.drawString(60, page_h - 115, f'Order ID: {order.id}')
    c.drawString(60, page_h - 131, f'Buyer: {buyer_label}')
    c.drawString(60, page_h - 147, f'Provider: {order.shop.name}')
    c.drawString(60, page_h - 163, f'Status: {order.get_status_display()}')
    c.drawString(60, page_h - 179, f'Total: {order.total_amount:.2f} {order.currency or "KISC"}')

    c.setStrokeColor(colors.HexColor('#D8D8D8'))
    c.line(60, page_h - 190, page_w - 60, page_h - 190)

    c.setFont('Helvetica-Bold', 14)
    c.drawString(60, page_h - 210, 'Items')
    y = page_h - 234
    c.setFont('Helvetica', 12)
    for item in order.items.select_related('product').all():
        price = Decimal(item.unit_price_cents) / 100
        c.drawString(60, y, item.product.name)
        c.drawRightString(page_w - 60, y, f'{price:.2f} x{item.quantity}')
        y -= 16
        attrs = item.selected_attributes or {}
        for key, val in attrs.items():
            value = val if isinstance(val, str) else ', '.join(map(str, val))
            c.setFont('Helvetica-Oblique', 10)
            c.drawString(70, y, f'• {key}: {value}')
            c.setFont('Helvetica', 12)
            y -= 12
        if y < 90:
            c.showPage()
            y = page_h - 60
            c.setFont('Helvetica', 12)
    y -= 8
    c.setStrokeColor(colors.HexColor('#B0B0B0'))
    c.setLineWidth(0.5)
    c.line(60, y, page_w - 60, y)
    y -= 18

    subtotal = Decimal(order.total_amount or 0)
    c.setFont('Helvetica-Bold', 13)
    c.drawString(60, y, 'Subtotal')
    c.drawRightString(page_w - 60, y, f'{subtotal:.2f} {order.currency or "KISC"}')
    y -= 18

    tax = Decimal('0.00')
    c.drawString(60, y, 'Tax (0%)')
    c.drawRightString(page_w - 60, y, f'{tax:.2f}')
    y -= 20

    c.setFont('Helvetica-Bold', 14)
    c.drawString(60, y, 'Grand Total')
    c.drawRightString(page_w - 60, y, f'{(subtotal + tax):.2f} {order.currency or "KISC"}')

    c.setFont('Helvetica', 10)
    timestamp = order.created_at.strftime('%Y-%m-%d %H:%M:%S') if order.created_at else 'N/A'
    c.drawString(60, 50, f'Generated: {timestamp}')

    c.save()
    buffer.seek(0)
    return buffer
