from __future__ import annotations

from datetime import datetime
from typing import Any


QUALITY = {"exact": 100.0, "strong": 80.0, "partial": 55.0, "near": 0.0}


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dates(values: Any) -> list[str]:
    if not values:
        return []
    source = values if isinstance(values, list) else str(values).replace("\n", ",").split(",")
    return [str(value).strip() for value in source if str(value).strip()]


def _longest_consecutive(dates: list[str]) -> int:
    parsed = []
    for value in dates:
        try:
            parsed.append(datetime.fromisoformat(value).date())
        except ValueError:
            continue
    parsed = sorted(set(parsed))
    longest = current = 0
    previous = None
    for value in parsed:
        current = current + 1 if previous and (value - previous).days == 1 else 1
        longest = max(longest, current)
        previous = value
    return longest


def rank_southwest_line(line: dict[str, Any], members: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    work_dates = _dates(line.get("work_dates")) or list(dict.fromkeys(
        date for member in members for date in _dates(member.get("operating_dates") or member.get("dates"))
    ))
    off_dates = _dates(line.get("off_dates"))
    required_off = set(_dates(profile.get("required_days_off")))
    preferred_off = set(_dates(profile.get("preferred_days_off")))
    hard_conflicts = sorted(required_off.intersection(work_dates))
    soft_conflicts = sorted(preferred_off.intersection(work_dates))
    calendar_fit = max(0.0, 100.0 - len(hard_conflicts) * 100.0 - len(soft_conflicts) * 12.0)
    pairing_quality = sum(QUALITY.get(str(member.get("match_class") or "partial"), 55.0) for member in members) / max(len(members), 1)
    monthly_tfp = _float(line.get("monthly_tfp"))
    tfp_per_dp = _float(line.get("tfp_per_duty_period"))
    tfp_component = min(100.0, (monthly_tfp or 0.0) / 100.0 * 100.0)
    efficiency_component = min(100.0, (tfp_per_dp or 0.0) / 8.0 * 100.0)
    duty_periods = int(line.get("duty_period_count") or sum(len(member.get("duty_legs") or []) for member in members))
    nights_away = int(line.get("total_nights_away") or sum(member.get("overnight_count") or len(member.get("layovers") or []) for member in members))
    nights_home = line.get("nights_at_home")
    home_component = 50.0 if nights_home is None else min(100.0, float(nights_home) / 20.0 * 100.0)
    components = {
        "calendar_fit": round(calendar_fit, 1),
        "pairing_quality": round(pairing_quality, 1),
        "monthly_tfp": round(tfp_component, 1),
        "tfp_efficiency": round(efficiency_component, 1),
        "nights_at_home": round(home_component, 1),
    }
    score = round(calendar_fit * .35 + pairing_quality * .25 + tfp_component * .20 + efficiency_component * .15 + home_component * .05, 1)
    violations = [f"Works required day off {value}" for value in hard_conflicts]
    for member in members:
        violations.extend(member.get("eligibility_violations") or [])
    violations = list(dict.fromkeys(violations))
    classes = [str(member.get("match_class") or "partial") for member in members]
    if violations:
        match_class = "near"
    elif classes and all(value == "exact" for value in classes):
        match_class = "exact"
    elif classes and all(value in {"exact", "strong"} for value in classes):
        match_class = "strong"
    else:
        match_class = "partial"
    labels = {"exact": "Exact Match", "strong": "Strong Match", "partial": "Partial Match", "near": "Near Match"}
    reasons = [
        f"Calendar fit component: {components['calendar_fit']}/100",
        f"Pairing quality component: {components['pairing_quality']}/100",
        f"Line TFP: {line.get('monthly_tfp') or 'not available'}",
        f"TFP per duty period: {line.get('tfp_per_duty_period') or 'not available'}",
    ]
    return {
        "score": score,
        "line_score_components": components,
        "work_dates": work_dates,
        "off_dates": off_dates,
        "weekends_off": line.get("weekends_off"),
        "consecutive_days_off": line.get("consecutive_days_off"),
        "longest_block_off": line.get("longest_block_off") or _longest_consecutive(off_dates),
        "duty_period_count": duty_periods,
        "total_nights_away": nights_away,
        "nights_at_home": nights_home,
        "eligible": not violations,
        "eligibility_result": "eligible" if not violations else "near_match_only",
        "eligibility_violations": violations,
        "relaxations_required": [f"Relax: {value}" for value in violations],
        "matched_preferences": reasons,
        "compromises": [f"Works preferred day off {value}" for value in soft_conflicts],
        "neutral_attributes": [f"Contains {len(members)} pairing types", f"Contains {duty_periods} duty periods"],
        "match_class": match_class,
        "match_label": labels[match_class],
    }


def optimize_schedule_conflicts(line: dict[str, Any], events: list[dict[str, Any]], mode: str = "protect") -> dict[str, Any]:
    work_dates = set(_dates(line.get("work_dates")))
    overlaps: list[dict[str, Any]] = []
    for event in events:
        event_dates = set(_dates(event.get("dates") or event.get("date")))
        shared = sorted(work_dates.intersection(event_dates))
        if shared:
            overlaps.append({
                "event_type": event.get("type") or "fixed_event",
                "dates": shared,
                "overlap_type": "full pairing overlap" if set(shared) == work_dates else "calendar overlap",
            })
    conflict_count = sum(len(item["dates"]) for item in overlaps)
    maximize = mode in {"maximize_conflicts", "maximize_vacation_extension"}
    value = conflict_count * (10 if maximize else -10)
    return {
        "mode": mode,
        "overlaps": overlaps,
        "conflict_value": value,
        "display_label": "Potential conflict value — not guaranteed pay",
        "general_line_quality_unchanged": True,
    }
