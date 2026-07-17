from __future__ import annotations

import re
from typing import Any


def _minutes(hour: str, minute: str | None = None) -> int:
    return int(hour) * 60 + int(minute or 0)


def interpret_trip_intent(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    lower = raw.lower()
    intent: dict[str, Any] = {
        "raw_text": raw,
        "required_trip_lengths": [],
        "trip_length_priority": [],
        "destination_groups": [],
        "matched_phrases": [],
        "assumptions": [],
        "needs_review": True,
    }

    length_range = re.search(r"\b(\d)\s*(?:-|–|to)\s*(\d)\s*-?\s*day", lower)
    single_length = re.search(r"\b(\d)\s*-?\s*day", lower)
    if length_range:
        rule = f"{length_range.group(1)}-{length_range.group(2)}"
        intent["required_trip_lengths"] = [rule]
        intent["trip_length_priority"] = [rule]
        intent["matched_phrases"].append(f"trip length {rule} days")
    elif single_length:
        rule = single_length.group(1)
        intent["required_trip_lengths"] = [rule]
        intent["trip_length_priority"] = [rule]
        intent["matched_phrases"].append(f"trip length {rule} days")

    if re.search(r"\bone[- ]and[- ]done\b|\bone leg (?:a|per) day\b|\bone leg per duty", lower):
        intent["max_legs_per_day"] = 1
        intent["one_and_done"] = True
        intent["matched_phrases"].append("one operating leg per duty day")
    first = re.search(r"(?:easy first day|first day[^.]*?(?:max(?:imum)?\s*)?(\d)\s*legs?)", lower)
    if first:
        intent["max_first_day_legs"] = int(first.group(1) or 1)
        intent["matched_phrases"].append(f"maximum {intent['max_first_day_legs']} first-day legs")
    if "one leg home" in lower or "one leg on the last day" in lower:
        intent["max_last_day_legs"] = 1
        intent["matched_phrases"].append("one leg on the last duty day")
    max_legs = re.search(r"(?:two|2) legs max", lower)
    if max_legs:
        intent["hard_max_legs_per_day"] = 2
        intent["matched_phrases"].append("hard maximum two legs per duty day")
    total_legs = re.search(r"(?:maximum|max|no more than)\s+(\d+)\s+(?:operating\s+)?legs?\s+(?:total|for the trip)", lower)
    if total_legs:
        intent["hard_max_total_legs"] = int(total_legs.group(1))
        intent["matched_phrases"].append(f"hard maximum {total_legs.group(1)} operating legs for the trip")

    if "transcon" in lower or "transcontinental" in lower:
        intent["transcontinental"] = True
        intent["destination_groups"].append({"value": "TRANSCON", "strength": "preferred"})
        intent["matched_phrases"].append("transcontinental flying")
    if "long-haul" in lower or "long haul" in lower:
        intent["long_haul"] = True
        intent["matched_phrases"].append("long-haul flying")
    for phrase, group in (("hawaii", "HAWAII"), ("japan", "JAPAN"), ("asia", "ASIA"), ("europe", "EUROPE"), ("caribbean", "CARIBBEAN")):
        if phrase in lower:
            intent["destination_groups"].append({"value": group, "strength": "preferred"})
            intent["matched_phrases"].append(f"preferred destination group {group}")

    after = re.search(r"report(?:s|ing)?\s+after\s+(\d{1,2})(?::?(\d{2}))?", lower)
    before = re.search(r"release(?:s|d)?\s+before\s+(\d{1,2})(?::?(\d{2}))?", lower)
    if after:
        intent["earliest_report_minutes"] = _minutes(after.group(1), after.group(2))
        intent["matched_phrases"].append(f"report after {intent['earliest_report_minutes'] // 60:02d}:{intent['earliest_report_minutes'] % 60:02d}")
    if before:
        intent["latest_release_minutes"] = _minutes(before.group(1), before.group(2))
        intent["matched_phrases"].append(f"release before {intent['latest_release_minutes'] // 60:02d}:{intent['latest_release_minutes'] % 60:02d}")

    if re.search(r"\bno redeyes?\b|\bavoid redeyes?\b", lower):
        intent["must_avoid_redeye"] = True
        intent["redeye_preference"] = "must_avoid"
        intent["matched_phrases"].append("no WOCL departures")
    elif "redeye" in lower:
        intent["redeye_preference"] = "review"
        intent["assumptions"].append("Redeye was mentioned without saying whether to prefer or avoid it")

    layover = re.search(r"(?:layovers?|rests?)\s+(?:of\s+)?(?:at least\s+)?(\d{1,2})\s*(?:hours?|hrs?)", lower)
    if layover:
        intent["min_layover_hours"] = int(layover.group(1))
        intent["matched_phrases"].append(f"layovers at least {layover.group(1)} hours")
    elif "long layover" in lower:
        intent["min_layover_hours"] = 16
        intent["assumptions"].append("Interpreted 'long layovers' as at least 16 hours; review this value")

    if "no deadhead" in lower:
        intent["hard_max_deadheads"] = 0
        intent["deadhead_preference"] = "must_avoid"
        intent["matched_phrases"].append("no deadheads")
    if "commute" in lower:
        intent["commute_preference"] = "commute_friendly"
        intent["matched_phrases"].append("commute-friendly report and release")
    if "tfp" in lower:
        intent["pay_priority"] = "tfp_per_day_away" if "efficien" in lower else "monthly_tfp"
    elif "total pay" in lower or "high pay" in lower:
        intent["pay_priority"] = "total_pay"
    elif "credit" in lower:
        intent["pay_priority"] = "trip_credit"

    if not intent["matched_phrases"]:
        intent["assumptions"].append("No supported trip criteria were detected; enter preferences manually")
    intent["interpreted_summary"] = intent["matched_phrases"] or ["No criteria interpreted"]
    return intent


def trip_intent_profile(intent: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "required_trip_lengths", "trip_length_priority", "max_legs_per_day", "hard_max_legs_per_day",
        "max_first_day_legs", "max_last_day_legs", "earliest_report_minutes", "latest_release_minutes",
        "must_avoid_redeye", "min_layover_hours", "hard_max_deadheads", "hard_max_total_legs", "pay_priority",
        "transcontinental", "long_haul", "one_and_done", "commute_preference",
    }
    profile = {key: value for key, value in intent.items() if key in allowed and value not in (None, "", [], {})}
    destination_groups = [item["value"] for item in intent.get("destination_groups", []) if item.get("strength") == "must_have"]
    preferred_groups = [item["value"] for item in intent.get("destination_groups", []) if item.get("strength") in {"favorite", "preferred"} and item.get("value") != "TRANSCON"]
    if destination_groups:
        profile["required_destination_groups"] = destination_groups
    if preferred_groups:
        profile["secondary_cities"] = preferred_groups
    return profile
