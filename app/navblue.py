from __future__ import annotations

import calendar
import re
from datetime import datetime
from typing import Any

from app.canonical import canonical_value
from app.geography import resolve_layover_preference
from app.recommendations import length_rule_matches


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else re.split(r"[,\n]", str(value))
    return [str(item).strip().upper() for item in values if str(item).strip()]


def _bid_year(filename: str) -> int:
    match = re.search(r"\b(20\d{2})\b", filename or "")
    return int(match.group(1)) if match else datetime.now().year


def _navblue_date(value: str, filename: str) -> str:
    parts = [part for part in re.split(r"[-/]", value) if part]
    if len(parts) == 3 and len(parts[0]) == 4:
        year, month, day = map(int, parts)
    elif len(parts) >= 2:
        month, day = map(int, parts[-2:])
        year = _bid_year(filename)
    else:
        return value
    if not 1 <= month <= 12:
        return value
    return f"{calendar.month_name[month]} {day}, {year}"


def _time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        minutes = int(value)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else None


def _result_layover_codes(result: dict[str, Any]) -> set[str]:
    canonical_layovers = canonical_value(result, "layovers", []) or []
    structured = {
        str(value.get("arrival_airport") or value.get("airport") or value.get("city") or "").upper()
        for value in canonical_layovers
        if str(value.get("arrival_airport") or value.get("airport") or value.get("city") or "").strip()
    }
    if structured:
        return structured
    return {str(value or "").strip().upper() for value in (result.get("cities") or []) if str(value or "").strip()}


def _matching_layovers(results: list[dict[str, Any]], cities: str | list[str]) -> int:
    wanted = {cities} if isinstance(cities, str) else set(cities)
    return sum(bool(wanted.intersection(_result_layover_codes(result))) for result in results)


def _available_layover_airports(results: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(
        code
        for result in results
        for code in sorted(_result_layover_codes(result))
    ))


def _layover_request(
    verb: str,
    value: str,
    results: list[dict[str, Any]],
    available_airports: list[str],
    warnings: list[str],
    reason: str,
) -> dict[str, Any] | None:
    resolved = resolve_layover_preference(value, available_airports)
    airports = list(resolved["airports"])
    if not airports:
        warnings.append(
            f"No {resolved['label']} layovers are present in this bid package, so CrewBidIQ did not emit an active {verb.lower()} request."
        )
        return None
    joined = " OR ".join(airports)
    return _request(
        f"{verb} Pairings If Layover In {joined}",
        f"{reason} Resolved {resolved['label']} to {joined} from this package's published layovers.",
        _matching_layovers(results, airports),
        preference_type=f"{verb} Pairings",
        values=airports,
    )


def _result_length(result: dict[str, Any]) -> int:
    return int(canonical_value(result, "trip_length_days", result.get("trip_length") or len(result.get("duty_legs", []))) or 0)


def _request(
    text: str,
    reason: str,
    matches: int | None = None,
    *,
    interface_category: str = "Pairings",
    preference_type: str | None = None,
    values: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request": text,
        "reason": reason,
        "interface_category": interface_category,
        "preference_type": preference_type or text.split(" If ", 1)[0],
        "values": values or [],
    }
    if matches is not None:
        payload["matching_trip_count"] = matches
    return payload


def build_navblue_layers(
    profile: dict[str, Any],
    results: list[dict[str, Any]],
    filename: str = "",
) -> dict[str, Any]:
    """Build a pilot-reviewable NavBlue PBS request order from CrewBidIQ preferences."""
    results = [item for item in results if item.get("eligible") is True]
    airline = str(profile.get("airline") or "").lower()
    warnings: list[str] = []
    available_layovers = _available_layover_airports(results)
    layers: list[dict[str, Any]] = []
    layers.append({"number": 1, "title": "Start Pairing Group", "requests": []})
    hard_requests: list[dict[str, Any]] = []
    for value in _list(profile.get("required_days_off")):
        day = _navblue_date(value, filename)
        hard_requests.append(_request(f"Prefer Off Date {day}", f"Protect required day off {day}.", interface_category="Days Off", preference_type="Prefer Off", values=[day]))
    if hard_requests:
        layers.append({"number": len(layers) + 1, "title": "Protect non-negotiables", "requests": hard_requests})

    avoid_requests: list[dict[str, Any]] = []
    for city in _list(profile.get("penalty_cities")):
        if request := _layover_request("Avoid", city, results, available_layovers, warnings, "Avoid this overnight preference."):
            avoid_requests.append(request)
    earliest = _time(profile.get("earliest_report") or profile.get("earliest_report_minutes"))
    latest = _time(profile.get("latest_release") or profile.get("latest_release_minutes"))
    if earliest:
        avoid_requests.append(_request(
            f"Avoid Pairings If Pairing Check-In Time Before < {earliest}",
            f"Avoid reports earlier than {earliest}.",
        ))
    if latest:
        avoid_requests.append(_request(
            f"Avoid Pairings If Pairing Check-Out Time After > {latest}",
            f"Avoid releases later than {latest}.",
        ))
    if airline == "delta" and (profile.get("must_avoid_redeye") or profile.get("allow_productive_redeye") is False):
        avoid_requests.append(_request(
            "Avoid Pairings If Redeye",
            "Avoid pairings with a WOCL departure.",
            sum(result.get("redeye") != "none" for result in results),
            preference_type="Avoid Pairings",
            values=["Redeye"],
        ))
    if airline == "delta" and profile.get("hard_max_legs_per_day") not in (None, ""):
        limit = int(profile["hard_max_legs_per_day"])
        avoid_requests.append(_request(
            f"Avoid Pairings If Legs Per Duty Period > {limit}",
            f"Protect the hard maximum of {limit} operating legs per duty period.",
            sum(max(result.get("duty_legs") or [0]) > limit for result in results),
            preference_type="Avoid Pairings",
            values=[f"Legs per Duty Period greater than {limit}"],
        ))
    if avoid_requests:
        layers.append({"number": len(layers) + 1, "title": "Remove poor fits", "requests": avoid_requests})

    priority_requests: list[dict[str, Any]] = []
    for city in _list(profile.get("elite_cities")):
        if request := _layover_request("Award", city, results, available_layovers, warnings, "Place these highest-priority overnights first."):
            priority_requests.append(request)
    if priority_requests:
        layers.append({"number": len(layers) + 1, "title": "Award highest priorities", "requests": priority_requests})

    shape_requests: list[dict[str, Any]] = []
    ordered_lengths = _list(profile.get("trip_length_priority"))
    lengths = sorted({int(value) for value in _list(profile.get("preferred_trip_lengths")) if value.isdigit()})
    result_length = _result_length
    if ordered_lengths:
        for rule in ordered_lengths:
            matches = sum(length_rule_matches(result_length(result), rule) for result in results)
            if rule.endswith("+"):
                request_text = f"Award Pairings If Pairing Length >= {rule[:-1]} Days"
            elif "-" in rule or "–" in rule:
                low, high = re.split(r"[-–]", rule, maxsplit=1)
                request_text = f"Award Pairings If Pairing Length Between {low} Days And {high} Days"
            else:
                request_text = f"Award Pairings If Pairing Length = {rule} Days"
            shape_requests.append(_request(
                request_text,
                f"Use ranked trip-length choice {rule} in this order.",
                matches,
                preference_type="Award Pairings",
                values=[rule],
            ))
    elif len(lengths) == 1:
        length = lengths[0]
        matches = sum(result_length(result) == length for result in results)
        shape_requests.append(_request(
            f"Award Pairings If Pairing Length = {length} Days",
            f"Favor the preferred {length}-day pairing length.",
            matches,
        ))
    elif lengths:
        low, high = min(lengths), max(lengths)
        matches = sum(low <= result_length(result) <= high for result in results)
        shape_requests.append(_request(
            f"Award Pairings If Pairing Length Between {low} Days And {high} Days",
            f"Favor preferred pairing lengths from {low} through {high} days.",
            matches,
        ))
    for city in _list(profile.get("secondary_cities")):
        if request := _layover_request("Award", city, results, available_layovers, warnings, "Favor these overnights after the highest priorities."):
            shape_requests.append(request)
    if shape_requests:
        layers.append({"number": len(layers) + 1, "title": "Shape the remaining awards", "requests": shape_requests})

    layers.append({
        "number": len(layers) + 1,
        "title": "Keep a broad fallback",
        "requests": [_request("Award Pairings", "Allow the remaining legal pairing pool after the preferences above.", len(results))],
    })

    warnings.extend([
        "Confirm each request is available in your airline's NavBlue configuration before submitting.",
        "CrewBidIQ does not submit these requests to NavBlue; this is a pilot-reviewed draft.",
    ])
    if profile.get("max_legs_per_day") not in (None, "") and airline != "delta":
        warnings.append("Maximum legs per duty day needs airline-specific NavBlue keyword confirmation and was not emitted automatically.")
    if (profile.get("allow_productive_redeye") is False or profile.get("avoid_final_redeye")) and airline != "delta":
        warnings.append("Redeye handling needs airline-specific NavBlue keyword confirmation and was not emitted automatically.")
    ordering = 0
    for index, layer in enumerate(layers):
        relaxed = None if index <= 1 else f"Broadens beyond: {layers[index - 1]['title']}"
        for request in layer["requests"]:
            ordering += 1
            request["ordering"] = ordering
            request["explanation"] = request["reason"]
            request["relaxed_from_previous"] = relaxed
        if layer["requests"]:
            layer["next_action"] = "Else Start Next Bid Group" if index < len(layers) - 1 else "Review and submit manually in NAVBLUE/PBS"
    return {
        "layers": layers,
        "warnings": warnings,
        "request_count": ordering,
        "submission_mode": "pilot_review_only",
        "airline_scope": airline or "generic_navblue",
        "available_layovers": available_layovers,
    }
