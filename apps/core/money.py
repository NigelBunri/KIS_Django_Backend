from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

KISC_TO_USD_RATE = Decimal("100")
USD_CENTS_PER_DOLLAR = Decimal("100")
USD_CENTS_PER_KISC = KISC_TO_USD_RATE * USD_CENTS_PER_DOLLAR
KISC_MICRO_PER_USD_CENT = Decimal("10")
KISC_MICRO_PER_KISC = USD_CENTS_PER_KISC * KISC_MICRO_PER_USD_CENT


def parse_decimal_amount(value) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


def frontend_kisc_major_to_usd_cents(value, *, allow_none: bool = False) -> int | None:
    parsed = parse_decimal_amount(value)
    if parsed is None:
        return None if allow_none else 0
    cents = (parsed * USD_CENTS_PER_KISC).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def frontend_kisc_major_to_micro(value, *, allow_none: bool = False) -> int | None:
    parsed = parse_decimal_amount(value)
    if parsed is None:
        return None if allow_none else 0
    micro = (parsed * KISC_MICRO_PER_KISC).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(micro)


def parse_frontend_money_to_cents(
    data: Any,
    *,
    cents_key: str = "amount_cents",
    kisc_key: str = "amount_kisc",
    usd_key: str = "amount_usd",
) -> int:
    if not hasattr(data, "get"):
        return 0

    amount_cents = data.get(cents_key)
    if amount_cents not in (None, ""):
        try:
            parsed_cents = int(amount_cents)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid {cents_key}")
        if parsed_cents > 0:
            return parsed_cents

    amount_kisc = data.get(kisc_key)
    if amount_kisc not in (None, ""):
        converted = frontend_kisc_major_to_usd_cents(amount_kisc, allow_none=True)
        if converted is None:
            raise ValueError(f"Invalid {kisc_key}")
        return int(converted)

    amount_usd = data.get(usd_key)
    if amount_usd not in (None, ""):
        parsed_usd = parse_decimal_amount(amount_usd)
        if parsed_usd is None:
            raise ValueError(f"Invalid {usd_key}")
        cents = (parsed_usd * USD_CENTS_PER_DOLLAR).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(cents)

    return 0
