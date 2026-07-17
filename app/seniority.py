from __future__ import annotations

from typing import Any


OUTLOOKS = ("More likely", "Competitive", "Less likely", "Aspirational", "Insufficient data")


def build_seniority_context(values: dict[str, Any] | None) -> dict[str, Any] | None:
    source = dict(values or {})
    try:
        position = int(source.get("category_seniority") or 0)
        population = int(source.get("category_population") or 0)
    except (TypeError, ValueError):
        position = population = 0
    if position <= 0 or population <= 0 or position > population:
        return None
    from_top = round(position / population * 100, 1)
    senior_to = round((population - position) / population * 100, 1)
    return {
        "global_seniority": source.get("global_seniority"),
        "category_seniority": position,
        "category_population": population,
        "base": source.get("base"),
        "fleet": source.get("fleet"),
        "seat": source.get("seat"),
        "bid_month": source.get("bid_month"),
        "percent_from_top": from_top,
        "percent_senior_to": senior_to,
        "wording": [
            f"You are senior to approximately {senior_to:g}% of pilots in this category.",
            f"You are {from_top:g}% from the top of the category.",
        ],
    }


def estimate_hold_outlook(
    item: dict[str, Any],
    seniority: dict[str, Any] | None = None,
    *,
    monthly_inventory: int | None = None,
    prior_award_data: bool = False,
) -> dict[str, Any]:
    occurrences = int(item.get("operations") or len(item.get("operating_dates") or item.get("dates") or []))
    desirable = bool(item.get("matched_preferences"))
    evidence = [f"{occurrences} published occurrence{'s' if occurrences != 1 else ''}"] if occurrences else []
    if seniority:
        senior_to = float(seniority["percent_senior_to"])
        evidence.append(seniority["wording"][0])
        if occurrences >= 8 and senior_to >= 60:
            outlook = "More likely"
        elif occurrences >= 4 and senior_to >= 35:
            outlook = "Competitive"
        elif occurrences <= 1 and senior_to < 35:
            outlook = "Aspirational"
        else:
            outlook = "Less likely"
    elif occurrences:
        outlook = "Competitive" if occurrences >= 8 else ("Less likely" if occurrences >= 3 else "Aspirational")
    else:
        outlook = "Insufficient data"
    if desirable:
        evidence.append("Matches desirable trip attributes, which may increase demand")
    if monthly_inventory:
        evidence.append(f"Compared with {monthly_inventory} trips in the monthly inventory")
    confidence = "High" if prior_award_data and seniority else ("Moderate" if seniority and occurrences else "Low")
    return {
        "outlook": outlook,
        "confidence": confidence,
        "evidence": evidence,
        "estimate_basis": "Historical award data" if prior_award_data else "Inventory-based estimate only",
        "probability": None,
    }
