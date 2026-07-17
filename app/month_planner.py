from __future__ import annotations

import re
from statistics import mean
from typing import Any


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.fullmatch(r"\s*(\d{1,3})(?:[.:](\d{2}))?\s*", str(value))
    if not match:
        return None
    return int(match.group(1)) + int(match.group(2) or 0) / 60


def _occurrences(item: dict[str, Any]) -> int:
    explicit = item.get("operations")
    if explicit not in (None, ""):
        return max(int(explicit), 0)
    dates = item.get("operating_dates") or item.get("dates") or []
    return len(dates) if dates else 1


def _trip_value(item: dict[str, Any]) -> float | None:
    for key in ("total_pay", "monthly_tfp", "pairing_tfp", "trip_credit", "credit"):
        if (parsed := _number(item.get(key))) is not None:
            return parsed
    return None


def _pool(name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    values = [value for item in items if (value := _trip_value(item)) is not None]
    return {
        "name": name,
        "unique_trip_count": len(items),
        "occurrence_count": sum(_occurrences(item) for item in items),
        "average_trip_value": round(mean(values), 2) if values else None,
        "trip_ids": [str(item.get("pairing")) for item in items],
    }


def build_month_plan(intent: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [item for item in results if item.get("eligible", True)]
    primary_items = [item for item in eligible if item.get("match_class") == "exact"]
    secondary_items = [item for item in eligible if item.get("match_class") == "strong"]
    fallback_items = [item for item in eligible if item.get("match_class") not in {"exact", "strong"}]
    pools = {
        "primary": _pool("Primary Pool", primary_items),
        "secondary": _pool("Secondary Pool", secondary_items),
        "fallback": _pool("Fallback Pool", fallback_items),
    }

    low = _number(intent.get("target_credit_min"))
    high = _number(intent.get("target_credit_max"))
    target = mean([value for value in (low, high) if value is not None]) if low is not None or high is not None else None
    available_values = [value for item in eligible if (value := _trip_value(item)) is not None]
    average_value = mean(available_values) if available_values else None
    trips_needed = max(1, round(target / average_value)) if target and average_value else None
    primary_occurrences = pools["primary"]["occurrence_count"]
    all_occurrences = sum(pool["occurrence_count"] for pool in pools.values())
    warnings: list[str] = []
    if trips_needed and primary_occurrences < trips_needed:
        warnings.append(f"Primary Pool has {primary_occurrences} published occurrences; approximately {trips_needed} trips may be needed for the target.")
    if trips_needed and all_occurrences < trips_needed:
        warnings.append("The full eligible pool appears too small to reach the monthly target without relaxing preferences.")
    if not primary_items:
        warnings.append("No Exact Matches are available; build the month from Secondary and Fallback pools only after reviewing compromises.")
    hard_dates = list(intent.get("hard_dates_off") or intent.get("required_days_off") or [])
    preferred_dates = list(intent.get("preferred_dates_off") or intent.get("preferred_days_off") or [])
    fixed_events = sum(len(intent.get(key) or []) for key in ("vacation", "training", "carry_in", "carry_out"))
    return {
        "month_intent": {
            "target_credit_range": [low, high],
            "target_workdays": intent.get("target_workdays"),
            "minimum_days_off": intent.get("minimum_days_off"),
            "hard_dates_off": hard_dates,
            "preferred_dates_off": preferred_dates,
            "preferred_work_blocks": intent.get("preferred_work_blocks") or [],
            "preferred_days_off_blocks": intent.get("preferred_days_off_blocks") or [],
            "vacation": intent.get("vacation") or [],
            "training": intent.get("training") or [],
            "carry_in": intent.get("carry_in") or [],
            "carry_out": intent.get("carry_out") or [],
            "seniority_context": intent.get("seniority_context"),
            "risk_tolerance": intent.get("risk_tolerance") or "balanced",
        },
        "pools": pools,
        "estimated_trips_needed": trips_needed,
        "eligible_occurrence_count": all_occurrences,
        "fixed_event_count": fixed_events,
        "monthly_credit_feasibility": "Review required" if not target or not average_value else ("Feasible from published inventory" if all_occurrences >= (trips_needed or 0) else "Pool too small"),
        "warnings": warnings,
        "limitations": [
            "Occurrence counts describe published inventory, not award probability.",
            "Legal combinations, exact spacing, carry-in/out, vacation, and training must be verified before submission.",
        ],
    }
