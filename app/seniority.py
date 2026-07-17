from __future__ import annotations

from typing import Any


HOLD_LEVELS = ("Very High", "High", "Moderate", "Low", "Very Low", "Insufficient Data")
DESIRABILITY_LEVELS = ("Very High", "High", "Moderate", "Low", "Very Low", "Insufficient Data")


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


def _occurrences(item: dict[str, Any]) -> int:
    raw = item.get("operations")
    if raw in (None, ""):
        raw = len(item.get("operating_dates") or item.get("dates") or [])
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _desirability(item: dict[str, Any]) -> tuple[str, list[str]]:
    match_class = str(item.get("match_class") or item.get("match_level") or "").strip().lower()
    hard_failures = item.get("hard_failures") or item.get("near_match_reasons") or []
    matches = item.get("matched_preferences") or []
    compromises = item.get("compromises") or []
    factors: list[str] = []
    if hard_failures or match_class.startswith("near"):
        level = "Low"
        factors.append("The trip does not satisfy one or more stated hard preferences")
    elif match_class.startswith("exact"):
        level = "Very High"
        factors.append("The trip satisfies all stated hard preferences")
    elif match_class.startswith("strong"):
        level = "High"
        factors.append("The trip is a strong match for the stated preferences")
    elif match_class.startswith("partial"):
        level = "Moderate"
        factors.append("The trip is a partial match for the stated preferences")
    elif matches or compromises:
        level = "High" if matches and not compromises else "Moderate"
        factors.append("Desirability reflects the stated preference matches and compromises")
    else:
        level = "Insufficient Data"
        factors.append("No preference-based match class is available")

    fatigue = item.get("fatigue_index") or {}
    try:
        max_legs = max([int(value) for value in item.get("duty_legs") or []] or [0])
    except (TypeError, ValueError):
        max_legs = 0
    if fatigue.get("level") in {"High", "Very High"} or max_legs >= 4:
        if level in {"Very High", "High", "Moderate", "Insufficient Data"}:
            level = "Low"
        factors.append("High fatigue exposure or workload reduces assessed desirability")
    return level, factors


def estimate_hold_outlook(
    item: dict[str, Any],
    seniority: dict[str, Any] | None = None,
    *,
    monthly_inventory: int | None = None,
    prior_award_data: bool = False,
) -> dict[str, Any]:
    """Estimate holdability without inventing an award probability."""
    occurrences = _occurrences(item)
    desirability, desirability_factors = _desirability(item)
    factors: list[str] = []
    missing: list[str] = []
    score = 0

    if occurrences:
        factors.append(f"{occurrences} published occurrence{'s' if occurrences != 1 else ''} in the active package")
        if occurrences >= 8:
            score += 2
        elif occurrences >= 4:
            score += 1
        elif occurrences == 1:
            score -= 2
        else:
            score -= 1
    else:
        missing.append("published occurrences for this trip")

    if seniority:
        senior_to = float(seniority["percent_senior_to"])
        factors.append(seniority["wording"][0])
        if senior_to >= 75:
            score += 2
        elif senior_to >= 50:
            score += 1
        elif senior_to < 20:
            score -= 2
        elif senior_to < 40:
            score -= 1
    else:
        missing.append("category seniority")

    if desirability in {"Very Low", "Low"}:
        score += 1 if desirability == "Low" else 2
        factors.append("Lower assessed desirability may reduce demand")
    elif desirability in {"High", "Very High"}:
        score -= 1 if desirability == "High" else 2
        factors.append("Higher assessed desirability may increase demand")

    if monthly_inventory:
        factors.append(f"The active package contains {monthly_inventory} trips")
    else:
        missing.append("active-package inventory count")

    if not prior_award_data:
        missing.append("validated historical award data")

    if not occurrences:
        likelihood = "Insufficient Data"
    elif score >= 4:
        likelihood = "Very High"
    elif score >= 2:
        likelihood = "High"
    elif score >= 0:
        likelihood = "Moderate"
    elif score >= -2:
        likelihood = "Low"
    else:
        likelihood = "Very Low"

    confidence = "High" if prior_award_data and seniority and occurrences else (
        "Moderate" if seniority and occurrences else "Low"
    )
    warning = f"Missing or unavailable data: {', '.join(dict.fromkeys(missing))}." if missing else None
    basis = "Historical award data" if prior_award_data else "Inventory-based estimate only"
    return {
        "likelihood": likelihood,
        "outlook": likelihood,
        "desirability": desirability,
        "desirability_factors": desirability_factors,
        "confidence": confidence,
        "factors": factors,
        "evidence": factors,
        "estimate_basis": basis,
        "missing_data_warning": warning,
        "probability": None,
    }
