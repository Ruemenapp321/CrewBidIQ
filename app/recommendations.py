from __future__ import annotations

import re
from typing import Any, Iterable

from app.canonical import canonical_value
from app.destinations import destination_matches


MATCH_LABELS = {
    "exact": "Exact Match",
    "strong": "Strong Match",
    "partial": "Partial Match",
    "near": "Near Match",
}

PREFERENCE_LEVELS = ("must_have", "prefer", "avoid", "must_avoid")
LEVEL_ALIASES = {
    "must_have": "must_have",
    "musthave": "must_have",
    "required": "must_have",
    "favorite": "prefer",
    "preferred": "prefer",
    "prefer": "prefer",
    "avoid": "avoid",
    "strongly_avoid": "must_avoid",
    "must_avoid": "must_avoid",
    "mustavoid": "must_avoid",
}


def values_list(value: Any) -> list[str]:
    source = value if isinstance(value, list) else re.split(r"[,\n]", str(value or ""))
    return [str(item).strip().upper() for item in source if str(item).strip()]


def normalize_preference_level(value: Any) -> str:
    token = re.sub(r"[^a-z]+", "_", str(value or "prefer").strip().lower()).strip("_")
    return LEVEL_ALIASES.get(token, "prefer")


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


def _trip_context(result: dict[str, Any]) -> dict[str, Any]:
    trip_length = int(canonical_value(result, "trip_length_days", result.get("trip_length") or 0) or 0)
    has_canonical = isinstance(result.get("canonical_trip"), dict)
    layovers = canonical_value(result, "layovers", []) or []
    cities = [
        str(layover.get("airport") or layover.get("city") or "").upper()
        for layover in layovers
        if layover.get("airport") or layover.get("city")
    ]
    if not cities and not has_canonical:
        cities = [str(city).upper() for city in result.get("cities", [])]
    duty_legs = [int(value) for value in result.get("duty_legs", [])]
    return {
        "trip_length": trip_length,
        "layovers": layovers,
        "cities": cities,
        "duty_legs": duty_legs,
        "total_legs": sum(duty_legs),
        "deadheads": int(result.get("deadheads") or 0),
        "has_redeye": result.get("redeye") not in (None, "", "none"),
    }


def _clock_minutes(value: Any) -> int | None:
    token = str(value or "").strip().replace(":", "")
    if not re.fullmatch(r"\d{3,4}", token):
        return None
    token = token.zfill(4)
    hours, minutes = int(token[:2]), int(token[2:])
    return hours * 60 + minutes if hours < 24 and minutes < 60 else None


def _duration_hours(value: Any) -> float | None:
    token = str(value or "").strip().replace(".", ":")
    match = re.fullmatch(r"(\d{1,3}):(\d{2})", token)
    if not match or int(match.group(2)) > 59:
        return None
    return int(match.group(1)) + int(match.group(2)) / 60


def _minimum_layover_hours(layovers: list[dict[str, Any]]) -> float | None:
    durations = [_duration_hours(layover.get("duration")) for layover in layovers]
    known = [value for value in durations if value is not None]
    return min(known) if known else None


def _explicit_rule_outcome(result: dict[str, Any], rule: dict[str, Any]) -> tuple[bool, str, str]:
    """Return whether a normalized preference condition is present or satisfied.

    The same outcome can be assigned any of the four preference levels. Must
    have/Prefer reward a true outcome; Avoid/Must avoid reward a false outcome.
    """
    context = _trip_context(result)
    criterion = re.sub(r"[^a-z0-9]+", "_", str(rule.get("criterion") or rule.get("key") or "").lower()).strip("_")
    raw_value = rule.get("value", rule.get("values"))
    values = values_list(raw_value)

    if criterion in {"trip_length", "trip_length_days", "sequence_days"}:
        matched = bool(values and any(length_rule_matches(context["trip_length"], value) for value in values))
        expected = ", ".join(values) or "a configured length"
        return matched, f"Trip length {context['trip_length']} days matches {expected}", f"Trip length {context['trip_length']} days does not match {expected}"

    if criterion in {"destination", "destinations", "layover", "layovers", "overnight"}:
        matched_values = [value for value in values if destination_matches(context["cities"], value)]
        expected = ", ".join(values) or "the configured destination"
        return bool(matched_values), f"Includes {', '.join(matched_values) or expected}", f"Does not include {expected}"

    if criterion in {"redeye", "redeyes", "wocl"}:
        return context["has_redeye"], "Contains a WOCL departure", "Does not contain a WOCL departure"

    numeric: float | None = None
    label = criterion.replace("_", " ") or "value"
    if criterion in {"deadhead", "deadheads"}:
        numeric, label = float(context["deadheads"]), "deadheads"
    elif criterion in {"legs_per_duty_day", "max_legs_per_day"}:
        numeric, label = float(max(context["duty_legs"], default=0)), "maximum legs in a duty day"
    elif criterion in {"total_legs", "legs"}:
        numeric, label = float(context["total_legs"]), "total operating legs"
    elif criterion in {"layover_hours", "minimum_layover", "min_layover_hours"}:
        numeric, label = _minimum_layover_hours(context["layovers"]), "shortest layover hours"
    elif criterion in {"report_time", "first_report"}:
        numeric, label = _clock_minutes(result.get("first_report") or result.get("checkin")), "first report"
    elif criterion in {"release_time", "final_release"}:
        numeric, label = _clock_minutes(result.get("final_release") or result.get("release")), "final release"
    elif criterion == "tafb":
        numeric, label = _duration_hours(result.get("tafb")), "TAFB hours"
    elif criterion in {"pay", "total_pay", "tfp", "pairing_tfp", "monthly_tfp"}:
        source_key = str(rule.get("source") or criterion)
        raw_numeric = result.get(source_key)
        try:
            numeric = float(raw_numeric)
        except (TypeError, ValueError):
            numeric = _duration_hours(raw_numeric)
        label = source_key.replace("_", " ")

    if numeric is not None or criterion in {
        "deadhead", "deadheads", "legs_per_duty_day", "max_legs_per_day", "total_legs", "legs",
        "layover_hours", "minimum_layover", "min_layover_hours", "report_time", "first_report",
        "release_time", "final_release", "tafb", "pay", "total_pay", "tfp", "pairing_tfp", "monthly_tfp",
    }:
        if numeric is None:
            return False, f"{label.title()} is available", f"{label.title()} is unavailable"
        operator = str(rule.get("operator") or ("min" if criterion in {"layover_hours", "minimum_layover", "min_layover_hours"} else "max")).lower()
        threshold_source = rule.get("min") if operator in {"min", ">=", "at_least"} else rule.get("max")
        if threshold_source in (None, ""):
            threshold_source = raw_value
        if threshold_source in (None, "", []):
            present = numeric > 0
            return present, f"Has {numeric:g} {label}", f"Has no {label}"
        try:
            threshold = float(threshold_source)
        except (TypeError, ValueError):
            threshold = _duration_hours(threshold_source)
        if threshold is None:
            return False, f"{label.title()} has a valid threshold", f"Configured threshold for {label} is invalid"
        if operator in {"min", ">=", "at_least"}:
            matched = numeric >= threshold
            comparison = "at least"
        elif operator in {"equal", "equals", "=="}:
            matched = numeric == threshold
            comparison = "exactly"
        else:
            matched = numeric <= threshold
            comparison = "no more than"
        return matched, f"{label.title()} is {numeric:g}, {comparison} {threshold:g}", f"{label.title()} is {numeric:g}; requires {comparison} {threshold:g}"

    return False, f"{criterion or 'Preference'} is satisfied", f"Unsupported preference criterion: {criterion or 'unnamed'}"


def explicit_preference_rules(profile: dict[str, Any]) -> list[dict[str, Any]]:
    source = profile.get("preference_rules") or profile.get("preferences") or []
    if not isinstance(source, list):
        return []
    return [rule for rule in source if isinstance(rule, dict)]


def classify_preferences(profile: dict[str, Any]) -> dict[str, list[str]]:
    classes = {level: [] for level in PREFERENCE_LEVELS}
    if values_list(profile.get("required_trip_lengths")):
        classes["must_have"].append("required_trip_lengths")
    if values_list(profile.get("required_destination_groups")):
        classes["must_have"].append("required_destination_groups")
    if values_list(profile.get("required_days_off")):
        classes["must_avoid"].append("required_days_off_conflicts")
    if profile.get("must_avoid_redeye"):
        classes["must_avoid"].append("redeye")
    if values_list(profile.get("must_avoid_destinations")):
        classes["must_avoid"].append("must_avoid_destinations")
    for key in (
        "hard_max_legs_per_day", "hard_max_deadheads", "hard_max_total_legs", "hard_min_layover_hours",
        "hard_earliest_report_minutes", "hard_latest_release_minutes",
    ):
        if profile.get(key) not in (None, ""):
            classes["must_have"].append(key)
    for key in (
        "trip_length_priority", "preferred_trip_lengths", "elite_cities", "secondary_cities",
        "preferred_start_airports", "preferred_aircraft", "pay_priority", "earliest_report_minutes",
        "latest_release_minutes",
    ):
        if profile.get(key) not in (None, "", [], {}):
            classes["prefer"].append(key)
    for key in (
        "penalty_cities", "avoid_start_airports", "max_legs_per_day", "max_deadheads",
        "min_layover_hours", "avoid_holidays",
    ):
        if profile.get(key) not in (None, "", [], {}, False):
            classes["avoid"].append(key)
    if profile.get("allow_productive_redeye") is False:
        classes["avoid"].append("redeye")
    for index, rule in enumerate(explicit_preference_rules(profile)):
        level = normalize_preference_level(rule.get("level", rule.get("strength")))
        classes[level].append(str(rule.get("criterion") or rule.get("key") or f"preference_rule_{index + 1}"))
    return classes


def evaluate_eligibility(result: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Stage 1: evaluate only hard requirements and integrity gates."""
    context = _trip_context(result)
    matched: list[str] = []
    violations: list[str] = []

    if result.get("data_quality") == "incomplete":
        violations.append("Required parser data is incomplete")
    if result.get("bidable_inventory_confirmed") is False:
        violations.append("Source record is not confirmed bidable inventory")
    if str(result.get("page_classification") or "").upper() in {"INSTRUCTIONS", "REFERENCE", "EXAMPLE"}:
        violations.append("Source record is instructional or example material, not bidable inventory")

    required_lengths = values_list(profile.get("required_trip_lengths"))
    if required_lengths:
        if any(length_rule_matches(context["trip_length"], rule) for rule in required_lengths):
            matched.append(f"Trip length is {context['trip_length']} days, within required {', '.join(required_lengths)}")
        else:
            violations.append(f"Requires {', '.join(required_lengths)} days; this trip is {context['trip_length']} days")

    required_destinations = values_list(profile.get("required_destination_groups"))
    for destination in required_destinations:
        if destination_matches(context["cities"], destination):
            matched.append(f"Includes required destination group {destination}")
        else:
            violations.append(f"Missing required destination group {destination}")

    for conflict in result.get("calendar_conflicts", []):
        if str(conflict).startswith("Required off:"):
            violations.append(str(conflict))

    if profile.get("must_avoid_redeye"):
        if context["has_redeye"]:
            violations.append("Contains a WOCL departure, which you marked Must avoid")
        else:
            matched.append("Avoids WOCL departures as required")
    for destination in values_list(profile.get("must_avoid_destinations")):
        if destination_matches(context["cities"], destination):
            violations.append(f"Includes {destination}, which you marked Must avoid")
        else:
            matched.append(f"Avoids {destination} as required")

    hard_max_legs = profile.get("hard_max_legs_per_day")
    if hard_max_legs not in (None, ""):
        actual = max(context["duty_legs"], default=0)
        if actual > int(hard_max_legs):
            violations.append(f"Has {actual} legs in a duty day; hard maximum is {hard_max_legs}")
        else:
            matched.append(f"Maximum {actual} legs in a duty day is within hard maximum {hard_max_legs}")
    hard_max_deadheads = profile.get("hard_max_deadheads")
    if hard_max_deadheads not in (None, ""):
        if context["deadheads"] > int(hard_max_deadheads):
            violations.append(f"Has {context['deadheads']} deadheads; hard maximum is {hard_max_deadheads}")
        else:
            matched.append(f"Has {context['deadheads']} deadheads, within hard maximum {hard_max_deadheads}")
    hard_max_total = profile.get("hard_max_total_legs")
    if hard_max_total not in (None, ""):
        if context["total_legs"] > int(hard_max_total):
            violations.append(f"Has {context['total_legs']} operating legs; hard trip maximum is {hard_max_total}")
        else:
            matched.append(f"Has {context['total_legs']} operating legs, within hard trip maximum {hard_max_total}")
    hard_min_layover = profile.get("hard_min_layover_hours")
    if hard_min_layover not in (None, ""):
        minimum = _minimum_layover_hours(context["layovers"])
        if minimum is None:
            violations.append("Layover duration is unavailable for the required minimum")
        elif minimum < float(hard_min_layover):
            violations.append(f"Shortest layover is {minimum:g} hours; hard minimum is {hard_min_layover}")
        else:
            matched.append(f"Shortest layover is {minimum:g} hours, meeting hard minimum {hard_min_layover}")

    for key, source_key, comparison, label in (
        ("hard_earliest_report_minutes", "first_report", "min", "First report"),
        ("hard_latest_release_minutes", "final_release", "max", "Final release"),
    ):
        threshold = profile.get(key)
        if threshold in (None, ""):
            continue
        actual = _clock_minutes(result.get(source_key) or result.get("checkin" if source_key == "first_report" else "release"))
        if actual is None:
            violations.append(f"{label} is unavailable for the hard time requirement")
        elif (comparison == "min" and actual < int(threshold)) or (comparison == "max" and actual > int(threshold)):
            violations.append(f"{label} {actual // 60:02d}:{actual % 60:02d} violates hard limit {int(threshold) // 60:02d}:{int(threshold) % 60:02d}")
        else:
            matched.append(f"{label} {actual // 60:02d}:{actual % 60:02d} meets the hard time requirement")

    for entry in profile.get("destination_preferences") or []:
        if not isinstance(entry, dict):
            continue
        value = str(entry.get("value") or "").upper()
        level = normalize_preference_level(entry.get("level", entry.get("strength")))
        if not value or level not in {"must_have", "must_avoid"}:
            continue
        hit = destination_matches(context["cities"], value)
        if level == "must_have":
            (matched if hit else violations).append(f"Includes required destination {value}" if hit else f"Missing required destination {value}")
        else:
            raw_level = str(entry.get("level", entry.get("strength")) or "").strip().lower()
            failure = (
                f"Includes strongly avoided destination {value}"
                if raw_level.startswith("strongly")
                else f"Includes {value}, which you marked Must avoid"
            )
            (violations if hit else matched).append(failure if hit else f"Avoids {value} as required")

    for rule in explicit_preference_rules(profile):
        level = normalize_preference_level(rule.get("level", rule.get("strength")))
        if level not in {"must_have", "must_avoid"}:
            continue
        condition, positive, negative = _explicit_rule_outcome(result, rule)
        if level == "must_have":
            (matched if condition else violations).append(positive if condition else negative)
        else:
            (violations if condition else matched).append(
                f"Must avoid: {positive}" if condition else f"Avoids as required: {negative}"
            )

    matched = list(dict.fromkeys(matched))
    violations = list(dict.fromkeys(violations))
    eligible = not violations
    return {
        "eligible": eligible,
        "eligibility_stage": "passed" if eligible else "failed",
        "eligibility_result": "eligible" if eligible else "near_match_only",
        "hard_requirement_matches": matched,
        "eligibility_violations": violations,
        "relaxations_required": [f"Relax: {violation}" for violation in violations],
        "preference_classes": classify_preferences(profile),
    }


def _evaluate_soft_preferences(result: dict[str, Any], profile: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    context = _trip_context(result)
    matched: list[str] = []
    compromises: list[str] = []
    neutral: list[str] = []

    if profile.get("transcontinental"):
        (matched if result.get("transcontinental") else compromises).append(
            "Includes transcontinental flying" if result.get("transcontinental") else "Does not include a transcontinental leg"
        )
    if profile.get("long_haul"):
        (matched if result.get("long_haul") else compromises).append(
            "Includes long-haul flying" if result.get("long_haul") else "Does not include a parsed long-haul leg"
        )

    for entry in profile.get("destination_preferences") or []:
        if not isinstance(entry, dict):
            continue
        value = str(entry.get("value") or "").upper()
        level = normalize_preference_level(entry.get("level", entry.get("strength")))
        hit = destination_matches(context["cities"], value)
        if level == "prefer":
            (matched if hit else compromises).append(f"Includes {value}" if hit else f"Does not include preferred {value}")
        elif level == "avoid":
            (compromises if hit else matched).append(f"Includes avoided destination {value}" if hit else f"Avoids {value}")
        elif level not in {"must_have", "must_avoid"} and hit:
            neutral.append(f"Includes neutral destination {value}")

    priority = length_priority(profile)
    rank = matching_length_rank(context["trip_length"], priority)
    if priority:
        if rank is None:
            compromises.append(f"Trip length {context['trip_length']} is outside ranked choices {', '.join(priority)}")
        else:
            matched.append(f"Trip length is your #{rank + 1} choice ({priority[rank]})")

    elite = values_list(profile.get("elite_cities"))
    secondary = values_list(profile.get("secondary_cities"))
    if elite:
        hits = [value for value in elite if destination_matches(context["cities"], value)]
        if hits:
            matched.append(f"Highest-priority overnight: {', '.join(hits)}")
        else:
            compromises.append("Does not include a highest-priority overnight")
    elif secondary:
        hits = [value for value in secondary if destination_matches(context["cities"], value)]
        if hits:
            matched.append(f"Preferred overnight: {', '.join(hits)}")
        else:
            compromises.append("Does not include a preferred overnight")

    avoided_hits = [value for value in values_list(profile.get("penalty_cities")) if destination_matches(context["cities"], value)]
    if avoided_hits:
        compromises.append(f"Includes an avoided overnight: {', '.join(avoided_hits)}")
    elif values_list(profile.get("penalty_cities")):
        matched.append("Avoids the configured overnight penalty list")

    if profile.get("allow_productive_redeye") is False:
        if context["has_redeye"]:
            compromises.append("Contains a WOCL departure you prefer to avoid")
        else:
            matched.append("Avoids WOCL departures")

    for rule in explicit_preference_rules(profile):
        level = normalize_preference_level(rule.get("level", rule.get("strength")))
        if level not in {"prefer", "avoid"}:
            continue
        condition, positive, negative = _explicit_rule_outcome(result, rule)
        if level == "prefer":
            (matched if condition else compromises).append(positive if condition else negative)
        else:
            (compromises if condition else matched).append(f"Avoid preference not met: {positive}" if condition else f"Avoids: {negative}")

    neutral.append(f"{context['trip_length']}-day trip with {len(context['duty_legs'])} duty period{'s' if len(context['duty_legs']) != 1 else ''}")
    neutral.append(f"{context['total_legs']} operating legs total")
    if context["deadheads"] == 0:
        neutral.append("No deadheads")
    return list(dict.fromkeys(matched)), list(dict.fromkeys(compromises)), list(dict.fromkeys(neutral))


def build_ranking_components(result: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    context = _trip_context(result)
    minimum_layover = _minimum_layover_hours(context["layovers"])
    pay_priority = str(profile.get("pay_priority") or "") or None
    return {
        "trip_length": {
            "days": context["trip_length"],
            "priority_rank": result.get("length_priority_rank"),
            "preference": length_priority(profile),
        },
        "report_time": {
            "value": result.get("first_report") or result.get("checkin"),
            "preferred_after_minutes": profile.get("earliest_report_minutes"),
        },
        "release_time": {
            "value": result.get("final_release") or result.get("release"),
            "preferred_before_minutes": profile.get("latest_release_minutes"),
        },
        "layovers": {"count": len(context["layovers"]), "shortest_hours": minimum_layover},
        "destination_preference": {"layover_cities": context["cities"]},
        "legs_per_duty_day": context["duty_legs"],
        "total_legs": context["total_legs"],
        "redeyes": len(result.get("redeye_legs") or ([] if not context["has_redeye"] else [True])),
        "deadheads": context["deadheads"],
        "tafb": result.get("tafb"),
        "pay_or_tfp": {"priority": pay_priority, "value": result.get("pay_priority_value")},
        "fatigue": result.get("fatigue_index"),
        "commute": result.get("commute_assessment"),
        "seniority_or_holding": result.get("hold_outlook"),
        "user_priority_order": profile.get("priority_order") or length_priority(profile),
    }


def rank_eligible_trip(
    result: dict[str, Any],
    profile: dict[str, Any],
    eligibility: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage 2: explain and classify one trip that already passed Stage 1."""
    decision = eligibility or evaluate_eligibility(result, profile)
    if not decision.get("eligible"):
        raise ValueError("Stage 2 ranking accepts eligible trips only")
    soft_matches, compromises, neutral = _evaluate_soft_preferences(result, profile)
    matched = list(dict.fromkeys((decision.get("hard_requirement_matches") or []) + soft_matches))
    match_class = "exact" if matched and not compromises else ("strong" if matched and len(compromises) <= 1 else "partial")
    return {
        **decision,
        "ranking_stage": "ranked_eligible",
        "ranking_score": result.get("score", 0),
        "ranking_components": build_ranking_components(result, profile),
        "qualification_reasons": decision.get("hard_requirement_matches") or ["No hard requirement was violated"],
        "matched_preferences": matched,
        "compromises": compromises,
        "neutral_attributes": neutral,
        "match_class": match_class,
        "match_label": MATCH_LABELS[match_class],
    }


def evaluate_recommendation(result: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Run Stage 1, then Stage 2 only if the trip is eligible."""
    eligibility = evaluate_eligibility(result, profile)
    if eligibility["eligible"]:
        return rank_eligible_trip(result, profile, eligibility)

    soft_matches, compromises, neutral = _evaluate_soft_preferences(result, profile)
    return {
        **eligibility,
        "ranking_stage": "not_ranked_hard_failure",
        "ranking_score": None,
        "ranking_components": {},
        "qualification_reasons": ["Shown only as a Near Match; it failed one or more hard requirements"],
        "matched_preferences": list(dict.fromkeys((eligibility.get("hard_requirement_matches") or []) + soft_matches)),
        "compromises": compromises,
        "neutral_attributes": neutral,
        "match_class": "near",
        "match_label": MATCH_LABELS["near"],
        "near_match_distance": len(eligibility["eligibility_violations"]),
    }


def rank_eligible_recommendations(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order only eligible results; fail closed if Stage 2 receives a Near Match."""
    eligible = list(results)
    if any(item.get("eligible") is not True for item in eligible):
        raise ValueError("Stage 2 ranking accepts eligible trips only")
    match_order = {"exact": 3, "strong": 2, "partial": 1}

    def key(item: dict[str, Any]) -> tuple[Any, ...]:
        complete = item.get("data_quality") != "incomplete"
        length_preference = bool(item.get("trip_length_preference_active"))
        length_rank = item.get("length_priority_rank")
        length_order = 0 if not length_preference else -(int(length_rank) if length_rank is not None else 10_000)
        pay_preference = bool(item.get("pay_priority"))
        pay_value = item.get("pay_priority_value")
        return (
            complete,
            length_order,
            match_order.get(str(item.get("match_class")), 0),
            pay_preference and pay_value is not None,
            pay_value if pay_preference and pay_value is not None else float("-inf"),
            item.get("ranking_score", item.get("score", 0)),
        )

    return sorted(eligible, key=key, reverse=True)


def order_near_matches(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order Near Matches by hard-failure distance, never by Stage 2 rank."""
    near = list(results)
    return sorted(
        near,
        key=lambda item: (
            len(item.get("eligibility_violations") or []),
            -len(item.get("hard_requirement_matches") or []),
            len(item.get("compromises") or []),
            str(item.get("pairing") or item.get("id") or ""),
        ),
    )


def recommendation_pipeline(
    results: Iterable[dict[str, Any]],
    active_package_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return ranked eligible recommendations followed by isolated Near Matches."""
    candidates = list(results)
    if active_package_id:
        mismatched = [item for item in candidates if str(item.get("package_id") or "") != str(active_package_id)]
        if mismatched:
            raise ValueError("Package isolation check rejected recommendation results from another bid package")
    eligible = [item for item in candidates if item.get("eligible") is True]
    near = [item for item in candidates if item.get("eligible") is not True]
    return rank_eligible_recommendations(eligible) + order_near_matches(near)
