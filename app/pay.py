from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


DELTA_TOTAL_PAY = re.compile(
    r"TOTAL PAY\s+(?P<total>\d{1,3}[:.]\d{2})TL(?P<components>[^\n\r]*)",
    re.IGNORECASE,
)
DELTA_COMPONENT = re.compile(r"(?P<value>(?:\d{1,3})?\.\d{2})(?P<label>[A-Z]{2,8})", re.IGNORECASE)
DELTA_SUPPORTED_COMPONENTS = {"EDP", "HOL", "SIT"}


def parse_clock_minutes(value: Any) -> int | None:
    """Parse airline H.MM/H:MM pay and duration values into minutes."""
    if value is None:
        return None
    text = str(value).strip()
    match = re.fullmatch(r"(?:(\d{1,3}))?([:.])(\d{2})", text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(3))
    if minutes >= 60:
        return None
    return hours * 60 + minutes


def format_pay_minutes(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    return f"{minutes // 60}:{minutes % 60:02d}"


def parse_tfp(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def format_tfp(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def tfp_ratio(tfp: Any, divisor: Any) -> str | None:
    amount = parse_tfp(tfp)
    try:
        count = Decimal(str(divisor))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if amount is None or count <= 0:
        return None
    return format_tfp(amount / count)


def tfp_per_day_away(tfp: Any, tafb: Any) -> str | None:
    amount = parse_tfp(tfp)
    minutes = parse_clock_minutes(tafb)
    if amount is None or not minutes:
        return None
    return format_tfp(amount / (Decimal(minutes) / Decimal(24 * 60)))


def southwest_pairing_pay_fields(pairing_tfp: Any, tafb: Any, duty_periods: int) -> dict[str, Any]:
    normalized = format_tfp(parse_tfp(pairing_tfp))
    return {
        "raw_trip_credit_label": "Trip Credit" if normalized is not None else None,
        "pairing_tfp": normalized,
        "tfp_per_duty_period": tfp_ratio(normalized, duty_periods),
        "tfp_per_day_away": tfp_per_day_away(normalized, tafb),
    }


def parse_delta_pay(block: str, trip_credit: Any) -> dict[str, Any]:
    """Return only Delta-supported pay fields; absent components remain absent."""
    match = DELTA_TOTAL_PAY.search(block or "")
    fields: dict[str, Any] = {
        "trip_credit": trip_credit,
        "raw_total_pay": match.group("total").replace(".", ":") if match else None,
    }
    if not match:
        return fields

    components: dict[str, str] = {}
    unknown: dict[str, str] = {}
    for token in DELTA_COMPONENT.finditer(match.group("components")):
        label = token.group("label").upper()
        formatted = format_pay_minutes(parse_clock_minutes(token.group("value")))
        if formatted is None:
            continue
        if label in DELTA_SUPPORTED_COMPONENTS:
            components[label] = formatted
        else:
            unknown[label] = formatted

    if components:
        additional_minutes = sum(parse_clock_minutes(value) or 0 for value in components.values())
        credit_minutes = parse_clock_minutes(trip_credit)
        fields["pay_components"] = components
        fields["additional_pay"] = format_pay_minutes(additional_minutes)
        fields["total_pay"] = format_pay_minutes(credit_minutes + additional_minutes) if credit_minutes is not None else None
    if unknown:
        fields["unknown_pay_components"] = unknown
    return fields


def pay_minutes_per_duty_day(value: Any, duty_days: int) -> str | None:
    minutes = parse_clock_minutes(value)
    if minutes is None or duty_days <= 0:
        return None
    return format_pay_minutes(int(Decimal(minutes / duty_days).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def pay_priority_value(result: dict[str, Any], preference: str | None) -> float | None:
    if not preference:
        return None
    value = result.get(preference)
    if preference in {"monthly_tfp", "pairing_tfp", "tfp_per_duty_period", "tfp_per_day_away"}:
        parsed = parse_tfp(value)
        return float(parsed) if parsed is not None else None
    minutes = parse_clock_minutes(value)
    return float(minutes) if minutes is not None else None
