from __future__ import annotations

import re
from typing import Any


LEVELS = ((10, "Very High"), (6, "High"), (3, "Moderate"), (0, "Low"))


def _clock_minutes(value: Any) -> int | None:
    token = re.sub(r"[^0-9]", "", str(value or ""))
    if len(token) not in {3, 4}:
        return None
    token = token.zfill(4)
    hour, minute = int(token[:2]), int(token[2:])
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def _duration_hours(value: Any) -> float | None:
    match = re.fullmatch(r"\s*(\d{1,3})[.:](\d{2})\s*", str(value or ""))
    if not match:
        return None
    return int(match.group(1)) + int(match.group(2)) / 60


def build_fatigue_index(item: dict[str, Any]) -> dict[str, Any]:
    """Assess schedule-driven fatigue risk without making a FAR 117 legality claim."""
    legs = list(item.get("legs") or [])
    duty_counts = [int(value) for value in item.get("duty_legs") or []]
    departures = [_clock_minutes(leg.get("departure_time")) for leg in legs]
    arrivals = [_clock_minutes(leg.get("arrival_time")) for leg in legs]
    known_times = sum(value is not None for value in departures + arrivals)
    wocl = [value for value in departures if value is not None and 120 <= value < 360]
    early = [value for value in departures if value is not None and 360 <= value < 480]
    late_landings = [value for value in arrivals if value is not None and value >= 1380]
    score = 0
    contributing: list[str] = []
    mitigating: list[str] = []

    if wocl:
        score += 4 + max(0, len(wocl) - 1) * 3
        contributing.append(f"{len(wocl)} departure{'s' if len(wocl) != 1 else ''} during WOCL (02:00–05:59 local)")
        if len(wocl) > 1:
            contributing.append("Repeated WOCL exposure")
    if len(early) >= 2:
        score += 2
        contributing.append(f"{len(early)} repeated early starts before 08:00")
    if late_landings:
        score += 1
        contributing.append(f"{len(late_landings)} late-duty landing{'s' if len(late_landings) != 1 else ''} at or after 23:00")
    if max(duty_counts, default=0) >= 4:
        score += 2
        contributing.append(f"High leg count: up to {max(duty_counts)} operating legs in one duty period")

    fdp_values = [
        hours
        for duty in item.get("duty_periods") or []
        if (hours := _duration_hours(duty.get("fdp"))) is not None
    ]
    if fdp_values and max(fdp_values) >= 12:
        score += 3 if max(fdp_values) >= 14 else 2
        contributing.append(f"Long scheduled FDP: up to {max(fdp_values):.1f} hours")
    if fdp_values and duty_counts and max(fdp_values) >= 11 and max(duty_counts) >= 3:
        score += 1
        contributing.append("Long duty combined with a high leg count")

    rest_hours = [
        hours for layover in item.get("layovers") or []
        if (hours := _duration_hours(layover.get("duration"))) is not None
    ]
    if rest_hours and min(rest_hours) < 10:
        score += 2
        contributing.append(f"Short recovery opportunity: {min(rest_hours):.1f} hours")
    if rest_hours and min(rest_hours) >= 14:
        score = max(0, score - 1)
        mitigating.append(f"All parsed layovers provide at least {min(rest_hours):.1f} hours")
    if not wocl:
        mitigating.append("No parsed WOCL departures")
    if duty_counts and max(duty_counts) <= 2:
        mitigating.append("No more than two operating legs in any parsed duty period")

    if not legs or known_times == 0:
        level, confidence = "Insufficient Data", "Low"
        contributing.append("Schedule times are not complete enough for a fatigue assessment")
    else:
        level = next(label for threshold, label in LEVELS if score >= threshold)
        time_coverage = known_times / max(len(legs) * 2, 1)
        if time_coverage >= 0.9 and duty_counts:
            confidence = "High" if fdp_values or rest_hours else "Moderate"
        else:
            confidence = "Low"
            contributing.append("Some schedule, duty, or rest data is missing")

    return {
        "level": level,
        "score": score,
        "contributing_factors": contributing,
        "mitigating_factors": mitigating,
        "confidence": confidence,
        "legality_assessment": "Not assessed — Fatigue Index is separate from FAR 117 legality",
        "basis": "schedule_only",
    }
