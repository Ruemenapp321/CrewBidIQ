from __future__ import annotations

import re
from typing import Any, Iterable

from app.destinations import destination_matches


MATCH_LABELS = {
    "exact": "Exact Match",
    "strong": "Strong Match",
    "partial": "Partial Match",
    "near": "Near Match",
}


def values_list(value: Any) -> list[str]:
    source = value if isinstance(value, list) else re.split(r"[,\n]", str(value or ""))
    return [str(item).strip().upper() for item in source if str(item).strip()]


def length_rule_matches(length: int, rule: str) -> bool:
    token = str(rule or "").strip().upper().replace(" ", "")
    if re.fullmatch(r"\d+", token):
        return length == int(token)
    plus = re.fullmatch(r"(\d+)\+", token)
    if plus:
        return length >= int(plus.group(1))
    span = re.fullmatch(r"(\d+)[-–](\d+)", token)
    return bool(span and int(span.group(1)) <= length <= int(span.group(2)))


def matching_length_rank(length: int, ordered_rules: Iterable[str]) -> int | None:
    for index, rule in enumerate(ordered_rules):
        if length_rule_matches(length, str(rule)):
            return index
    return None


def length_priority(profile: dict[str, Any]) -> list[str]:
    return values_list(profile.get("trip_length_priority") or profile.get("preferred_trip_lengths"))


def length_score_contribution(length: int, profile: dict[str, Any]) -> tuple[float, int | None, str | None]:
    priority = length_priority(profile)
    if not priority:
        return 0.0, None, None
    rank = matching_length_rank(length, priority)
    if rank is None:
        return -8.0, None, f"Does not match your ranked trip lengths ({', '.join(priority)})"
    contribution = max(6.0, 24.0 - rank * 4.0)
    return contribution, rank, f"Matches your #{rank + 1} trip-length choice: {priority[rank]} days"


def classify_preferences(profile: dict[str, Any]) -> dict[str, list[str]]:
    must_have: list[str] = []
    prefer: list[str] = []
    avoid: list[str] = []
    must_avoid: list[str] = []
    if values_list(profile.get("required_trip_lengths")):
        must_have.append("required_trip_lengths")
    if values_list(profile.get("required_destination_groups")):
        must_have.append("required_destination_groups")
    if values_list(profile.get("required_days_off")):
        must_avoid.append("required_days_off_conflicts")
    if profile.get("must_avoid_redeye"):
        must_avoid.append("redeye")
    if values_list(profile.get("must_avoid_destinations")):
        must_avoid.append("must_avoid_destinations")
    for key in ("hard_max_legs_per_day", "hard_max_deadheads", "hard_min_layover_hours"):
        if profile.get(key) not in (None, ""):
            must_have.append(key)
    for key in ("trip_length_priority", "preferred_trip_lengths", "elite_cities", "secondary_cities", "preferred_start_airports", "pay_priority"):
        if profile.get(key):
            prefer.append(key)
    for key in ("penalty_cities", "avoid_start_airports", "max_legs_per_day", "max_deadheads", "min_layover_hours"):
        if profile.get(key) not in (None, "", [], {}):
            avoid.append(key)
    if profile.get("allow_productive_redeye") is False:
        avoid.append("redeye")
    return {"must_have": must_have, "prefer": prefer, "avoid": avoid, "must_avoid": must_avoid}


def evaluate_recommendation(result: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    matched: list[str] = []
    compromises: list[str] = []
    violations: list[str] = []
    neutral: list[str] = []
    trip_length = int(result.get("trip_length") or 0)
    cities = [str(city).upper() for city in result.get("cities", [])]
    duty_legs = [int(value) for value in result.get("duty_legs", [])]

    if result.get("data_quality") == "incomplete":
        violations.append("Required parser data is incomplete")
    required_lengths = values_list(profile.get("required_trip_lengths"))
    if required_lengths:
        if any(length_rule_matches(trip_length, rule) for rule in required_lengths):
            matched.append(f"Trip length is {trip_length} days, within required {', '.join(required_lengths)}")
        else:
            violations.append(f"Requires {', '.join(required_lengths)} days; this trip is {trip_length} days")

    required_destinations = values_list(profile.get("required_destination_groups"))
    for destination in required_destinations:
        if destination_matches(cities, destination):
            matched.append(f"Includes required destination group {destination}")
        else:
            violations.append(f"Missing required destination group {destination}")

    for conflict in result.get("calendar_conflicts", []):
        if str(conflict).startswith("Required off:"):
            violations.append(str(conflict))

    if profile.get("must_avoid_redeye") and result.get("redeye") != "none":
        violations.append("Contains a WOCL departure, which you marked Must avoid")
    for destination in values_list(profile.get("must_avoid_destinations")):
        if destination_matches(cities, destination):
            violations.append(f"Includes {destination}, which you marked Must avoid")

    hard_max_legs = profile.get("hard_max_legs_per_day")
    if hard_max_legs not in (None, "") and max(duty_legs, default=0) > int(hard_max_legs):
        violations.append(f"Has {max(duty_legs)} legs in a duty day; hard maximum is {hard_max_legs}")
    hard_max_deadheads = profile.get("hard_max_deadheads")
    if hard_max_deadheads not in (None, "") and int(result.get("deadheads") or 0) > int(hard_max_deadheads):
        violations.append(f"Has {result.get('deadheads')} deadheads; hard maximum is {hard_max_deadheads}")
    hard_max_total = profile.get("hard_max_total_legs")
    if hard_max_total not in (None, "") and sum(duty_legs) > int(hard_max_total):
        violations.append(f"Has {sum(duty_legs)} operating legs; hard trip maximum is {hard_max_total}")

    if profile.get("transcontinental"):
        (matched if result.get("transcontinental") else compromises).append(
            "Includes transcontinental flying" if result.get("transcontinental") else "Does not include a transcontinental leg"
        )
    if profile.get("long_haul"):
        (matched if result.get("long_haul") else compromises).append(
            "Includes long-haul flying" if result.get("long_haul") else "Does not include a parsed long-haul leg"
        )

    for entry in profile.get("destination_preferences") or []:
        value = str(entry.get("value") or "").upper()
        strength = str(entry.get("strength") or "neutral").lower().replace(" ", "_")
        hit = destination_matches(cities, value)
        if strength in {"favorite", "preferred"}:
            (matched if hit else compromises).append(f"Includes {value}" if hit else f"Does not include preferred {value}")
        elif strength == "avoid" and hit:
            compromises.append(f"Includes avoided destination {value}")
        elif strength == "strongly_avoid" and hit:
            violations.append(f"Includes strongly avoided destination {value}")
        elif strength == "neutral" and hit:
            neutral.append(f"Includes neutral destination {value}")

    priority = length_priority(profile)
    rank = matching_length_rank(trip_length, priority)
    if priority:
        if rank is None:
            compromises.append(f"Trip length {trip_length} is outside ranked choices {', '.join(priority)}")
        else:
            matched.append(f"Trip length is your #{rank + 1} choice ({priority[rank]})")

    elite = values_list(profile.get("elite_cities"))
    secondary = values_list(profile.get("secondary_cities"))
    if elite:
        hits = [value for value in elite if destination_matches(cities, value)]
        if hits:
            matched.append(f"Highest-priority overnight: {', '.join(hits)}")
        else:
            compromises.append("Does not include a highest-priority overnight")
    elif secondary:
        hits = [value for value in secondary if destination_matches(cities, value)]
        if hits:
            matched.append(f"Preferred overnight: {', '.join(hits)}")

    avoided_hits = [value for value in values_list(profile.get("penalty_cities")) if destination_matches(cities, value)]
    if avoided_hits:
        compromises.append(f"Includes an avoided overnight: {', '.join(avoided_hits)}")
    if profile.get("allow_productive_redeye") is False and result.get("redeye") != "none":
        compromises.append("Contains a WOCL departure you prefer to avoid")

    neutral.append(f"{trip_length}-day trip with {len(duty_legs)} duty period{'s' if len(duty_legs) != 1 else ''}")
    neutral.append(f"{sum(duty_legs)} operating legs total")
    if result.get("deadheads") == 0:
        neutral.append("No deadheads")

    eligible = not violations
    if not eligible:
        match_class = "near"
    elif matched and not compromises:
        match_class = "exact"
    elif matched and len(compromises) <= 1:
        match_class = "strong"
    else:
        match_class = "partial"
    return {
        "eligible": eligible,
        "eligibility_result": "eligible" if eligible else "near_match_only",
        "preference_classes": classify_preferences(profile),
        "matched_preferences": matched,
        "compromises": compromises,
        "eligibility_violations": violations,
        "neutral_attributes": neutral,
        "match_class": match_class,
        "match_label": MATCH_LABELS[match_class],
        "relaxations_required": [f"Relax: {violation}" for violation in violations],
    }
