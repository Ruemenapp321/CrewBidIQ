from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from app.canonical import canonical_value


LEVELS = ((11, "Very High"), (6, "High"), (3, "Moderate"), (0, "Low"))


def _clock_minutes(value: Any) -> int | None:
    text = str(value or "").strip()
    iso_match = re.search(r"T(\d{2}):(\d{2})", text)
    if iso_match:
        hour, minute = int(iso_match.group(1)), int(iso_match.group(2))
    else:
        clock_match = re.fullmatch(r"(\d{1,2}):?(\d{2})", text)
        if not clock_match:
            return None
        hour, minute = int(clock_match.group(1)), int(clock_match.group(2))
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def _duration_hours(value: Any) -> float | None:
    match = re.fullmatch(r"\s*(\d{1,3})[.:](\d{2})\s*", str(value or ""))
    if not match:
        return None
    return int(match.group(1)) + int(match.group(2)) / 60


def _timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or "T" not in text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_hours(start: Any, end: Any) -> float | None:
    start_time, end_time = _timestamp(start), _timestamp(end)
    if start_time is None or end_time is None:
        return None
    if start_time.tzinfo is not None and end_time.tzinfo is not None:
        elapsed = (end_time.astimezone() - start_time.astimezone()).total_seconds() / 3600
    else:
        elapsed = (end_time - start_time).total_seconds() / 3600
    return elapsed if 0 < elapsed <= 30 else None


def _leg_value(leg: dict[str, Any], canonical_key: str, legacy_key: str) -> Any:
    return leg.get(canonical_key) if canonical_key in leg else leg.get(legacy_key)


def _duty_counts(item: dict[str, Any], legs: list[dict[str, Any]]) -> list[int]:
    duty_days = canonical_value(item, "duty_days", []) or []
    counts = [len(day.get("ordered_legs") or []) for day in duty_days]
    if any(counts):
        return counts
    source = item.get("duty_legs") or []
    if source:
        try:
            return [int(value) for value in source]
        except (TypeError, ValueError):
            pass
    grouped: dict[int, int] = {}
    for leg in legs:
        try:
            day = int(leg.get("duty_day_index") or leg.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        grouped[day] = grouped.get(day, 0) + 1
    return [grouped[day] for day in sorted(grouped)]


def _duty_boundaries(item: dict[str, Any], legs: list[dict[str, Any]]) -> list[tuple[Any, Any]]:
    duty_days = canonical_value(item, "duty_days", []) or []
    boundaries: list[tuple[Any, Any]] = []
    for day in duty_days:
        day_legs = day.get("ordered_legs") or []
        report = (day.get("report_event") or {}).get("local_time")
        release = (day.get("release_event") or {}).get("local_time")
        if not report and day_legs:
            report = day_legs[0].get("local_departure_time")
        if not release and day_legs:
            release = day_legs[-1].get("local_arrival_time")
        boundaries.append((report, release))
    if boundaries:
        return boundaries

    grouped: dict[int, list[dict[str, Any]]] = {}
    for leg in legs:
        try:
            day = int(leg.get("duty_day_index") or leg.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        grouped.setdefault(day, []).append(leg)
    return [
        (
            _leg_value(group[0], "local_departure_time", "departure_time"),
            _leg_value(group[-1], "local_arrival_time", "arrival_time"),
        )
        for _, group in sorted(grouped.items())
    ]


def _timezone_shift_hours(leg: dict[str, Any]) -> float | None:
    departure = _timestamp(_leg_value(leg, "local_departure_time", "departure_time"))
    arrival = _timestamp(_leg_value(leg, "local_arrival_time", "arrival_time"))
    if departure is None or arrival is None or departure.utcoffset() is None or arrival.utcoffset() is None:
        return None
    return abs((arrival.utcoffset() - departure.utcoffset()).total_seconds()) / 3600


def build_fatigue_index(item: dict[str, Any]) -> dict[str, Any]:
    """Assess schedule-driven fatigue risk without making a FAR legality claim."""
    canonical_legs = canonical_value(item, "ordered_legs", []) or []
    legs = list(canonical_legs or item.get("legs") or [])
    duty_counts = _duty_counts(item, legs)
    duty_boundaries = _duty_boundaries(item, legs)
    departures = [_clock_minutes(_leg_value(leg, "local_departure_time", "departure_time")) for leg in legs]
    arrivals = [_clock_minutes(_leg_value(leg, "local_arrival_time", "arrival_time")) for leg in legs]
    known_times = sum(value is not None for value in departures + arrivals)
    endpoint_count = max(len(legs) * 2, 1)
    time_coverage = known_times / endpoint_count
    score = 0
    contributing: list[str] = []
    mitigating: list[str] = []
    missing_fields: list[str] = []

    wocl_events = sum(1 for value in departures + arrivals if value is not None and 120 <= value < 360)
    if wocl_events:
        score += 3 + min(4, max(0, wocl_events - 1) * 2)
        contributing.append(f"{wocl_events} event{'s' if wocl_events != 1 else ''} during WOCL (02:00-05:59 local)")
        if wocl_events > 1:
            score += 2
            contributing.append("Repeated WOCL exposure")
    else:
        mitigating.append("No parsed WOCL events")

    report_clocks = [_clock_minutes(report) for report, _ in duty_boundaries]
    early_starts = sum(1 for value in report_clocks if value is not None and value < 420)
    if early_starts >= 2:
        score += 2 + (1 if early_starts >= 3 else 0)
        contributing.append(f"{early_starts} repeated duty starts before 07:00 local")

    release_clocks = [_clock_minutes(release) for _, release in duty_boundaries]
    late_releases = sum(1 for value in release_clocks if value is not None and (value >= 1380 or value < 240))
    if late_releases:
        score += 1 + (1 if late_releases >= 2 else 0)
        contributing.append(f"{late_releases} late or overnight duty release{'s' if late_releases != 1 else ''}")

    redeye_days: list[int] = []
    for index, (departure, arrival) in enumerate(zip(departures, arrivals), 1):
        if departure is None or arrival is None:
            continue
        if departure >= 1260 and arrival < 540:
            try:
                redeye_days.append(int(legs[index - 1].get("duty_day_index") or legs[index - 1].get("day") or 1))
            except (TypeError, ValueError):
                redeye_days.append(1)
    if redeye_days:
        score += 3 + min(2, len(redeye_days) - 1)
        contributing.append(f"{len(redeye_days)} redeye {'duties' if len(redeye_days) != 1 else 'duty'}")
        if any(day > min(redeye_days) for day in redeye_days) or min(redeye_days) > 1:
            score += 1
            contributing.append("Mid-rotation redeye exposure")

    max_legs = max(duty_counts, default=0)
    high_workload_days = sum(1 for value in duty_counts if value >= 4)
    if max_legs >= 4:
        score += 2
        contributing.append(f"High workload: up to {max_legs} legs in one duty day")
    if high_workload_days >= 2:
        score += 2
        contributing.append(f"Repeated high-workload duty days ({high_workload_days})")
    if duty_counts and max_legs <= 2:
        mitigating.append("No more than two legs in any parsed duty day")

    fdp_values = [
        hours
        for duty in item.get("duty_periods") or []
        if (hours := _duration_hours(duty.get("fdp"))) is not None
    ]
    if not fdp_values:
        fdp_values = [
            hours
            for report, release in duty_boundaries
            if (hours := _elapsed_hours(report, release)) is not None
        ]
    long_duties = [hours for hours in fdp_values if hours >= 12]
    if long_duties:
        score += 2 + (1 if max(long_duties) >= 14 else 0)
        contributing.append(f"Long scheduled duty: up to {max(long_duties):.1f} hours")
    if long_duties and max_legs >= 3:
        score += 1
        contributing.append("Long duty combined with a high leg count")

    timezone_shifts = [shift for leg in legs if (shift := _timezone_shift_hours(leg)) is not None]
    major_shifts = [shift for shift in timezone_shifts if shift >= 3]
    if major_shifts:
        score += 1 + (1 if len(major_shifts) >= 2 else 0)
        contributing.append(f"Time-zone transition of up to {max(major_shifts):g} hours")

    rest_hours = [
        hours
        for layover in (canonical_value(item, "layovers", []) or [])
        if (hours := _duration_hours(layover.get("duration"))) is not None
    ]
    if rest_hours and min(rest_hours) < 10:
        score += 2
        contributing.append(f"Short recovery opportunity: {min(rest_hours):.1f} hours")
    if rest_hours and min(rest_hours) >= 14:
        score = max(0, score - 1)
        mitigating.append(f"All parsed layovers provide at least {min(rest_hours):.1f} hours")
    if redeye_days:
        for day in redeye_days:
            next_index = day
            if next_index < len(duty_counts) and duty_counts[next_index] > 0:
                rest = rest_hours[day - 1] if 0 <= day - 1 < len(rest_hours) else None
                if rest is None or rest < 14:
                    score += 1
                    contributing.append("Flying continues after a redeye without a confirmed extended recovery period")
                    break
                mitigating.append("An extended recovery period follows the parsed redeye")
                break

    if not legs:
        missing_fields.append("ordered legs")
    if time_coverage < 0.75:
        missing_fields.append("local departure or arrival times")
    if not duty_counts:
        missing_fields.append("duty-day structure")
    if len(duty_counts) > 1 and not rest_hours:
        missing_fields.append("recovery durations")

    insufficient = not legs or time_coverage < 0.25
    if insufficient:
        level, confidence = "Insufficient Data", "Low"
        contributing = ["Schedule times are not complete enough for a fatigue assessment"]
    else:
        level = next(label for threshold, label in LEVELS if score >= threshold)
        if time_coverage >= 0.9 and duty_counts and (fdp_values or rest_hours):
            confidence = "High"
        elif time_coverage >= 0.6 and duty_counts:
            confidence = "Moderate"
        else:
            confidence = "Low"

    warning = f"Missing or incomplete data: {', '.join(missing_fields)}." if missing_fields else None
    return {
        "level": level,
        "score": score,
        "contributing_factors": contributing,
        "mitigating_factors": mitigating,
        "confidence": confidence,
        "missing_data_warning": warning,
        "missing_fields": missing_fields,
        "legality_assessment": "Not assessed - Fatigue Index is separate from FAR legality",
        "basis": "schedule_only",
    }
