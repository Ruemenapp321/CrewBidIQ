from __future__ import annotations

import re
from typing import Any


def local_clock_minutes(value: Any) -> int | None:
    """Parse a local wall clock; dotted Delta source values are not decimals."""
    token = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2})[.:]?(\d{2})", token)
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    return hours * 60 + minutes if hours < 24 and minutes < 60 else None


def duration_minutes(value: Any) -> int | None:
    """Parse an airline HH.MM/HH:MM duration without applying clock limits."""
    token = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,3})[.:](\d{2})", token)
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    return hours * 60 + minutes if minutes < 60 else None


def format_clock(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    value = minutes % (24 * 60)
    return f"{value // 60:02d}:{value % 60:02d}"


def derive_release(report: Any, tafb: Any) -> dict[str, Any] | None:
    report_minutes = local_clock_minutes(report)
    elapsed = duration_minutes(tafb)
    if report_minutes is None or elapsed is None:
        return None
    total = report_minutes + elapsed
    return {"local_time": format_clock(total), "day_offset": total // (24 * 60), "elapsed_minutes": elapsed}
