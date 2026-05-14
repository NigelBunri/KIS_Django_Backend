from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

PROMOTIONAL_CREDIT_LABEL = "promotional credits"
PROMOTIONAL_CREDIT_POLICY = (
    "Promotional credits are gift/reward credits for eligible KIS benefits. "
    "They are not cash, cannot be bought, transferred, withdrawn, sold, or converted to cash."
)


def cents_to_promotional_credit_units(amount_cents: int | None) -> Decimal:
    cents = Decimal(int(amount_cents or 0))
    return (cents / Decimal("10000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def promotional_credit_label_from_cents(amount_cents: int | None) -> str:
    units = cents_to_promotional_credit_units(amount_cents)
    return f"{format(units, 'f')} {PROMOTIONAL_CREDIT_LABEL}"


def credit_delta_label(credits_delta: int | None) -> str:
    value = int(credits_delta or 0)
    sign = "+" if value > 0 else ""
    return f"{sign}{value} {PROMOTIONAL_CREDIT_LABEL}"
