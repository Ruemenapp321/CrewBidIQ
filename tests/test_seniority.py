from app.seniority import build_seniority_context, estimate_hold_outlook


def test_seniority_wording_shows_both_clear_percentages():
    context = build_seniority_context({"category_seniority": 620, "category_population": 1000})
    assert context["percent_senior_to"] == 38
    assert context["percent_from_top"] == 62
    assert context["wording"] == [
        "You are senior to approximately 38% of pilots in this category.",
        "You are 62% from the top of the category.",
    ]


def test_hold_outlook_never_invents_probability_and_discloses_inventory_basis():
    context = build_seniority_context({"category_seniority": 200, "category_population": 1000})
    outlook = estimate_hold_outlook(
        {"operations": 10, "match_class": "strong", "matched_preferences": ["Hawaii"]},
        context,
        monthly_inventory=100,
    )
    assert outlook["likelihood"] in {"Very High", "High", "Moderate", "Low", "Very Low"}
    assert outlook["outlook"] == outlook["likelihood"]
    assert outlook["desirability"] == "High"
    assert outlook["probability"] is None
    assert outlook["estimate_basis"] == "Inventory-based estimate only"
    assert outlook["factors"]
    assert "validated historical award data" in outlook["missing_data_warning"]


def test_hold_outlook_is_insufficient_without_inventory_or_seniority():
    result = estimate_hold_outlook({}, None)
    assert result["outlook"] == "Insufficient Data"
    assert result["confidence"] == "Low"
    assert result["missing_data_warning"]


def test_low_desirability_can_have_very_high_likelihood_of_holding():
    context = build_seniority_context({"category_seniority": 100, "category_population": 1000})
    result = estimate_hold_outlook({
        "operations": 12,
        "match_class": "near",
        "hard_failures": ["Required trip length not met"],
        "fatigue_index": {"level": "Very High"},
        "duty_legs": [5, 5, 4],
    }, context, monthly_inventory=80)
    assert result["desirability"] == "Low"
    assert result["likelihood"] == "Very High"
    assert result["probability"] is None


def test_historical_data_flag_changes_basis_without_creating_probability():
    context = build_seniority_context({"category_seniority": 500, "category_population": 1000})
    result = estimate_hold_outlook(
        {"operations": 4, "match_class": "partial"},
        context,
        monthly_inventory=50,
        prior_award_data=True,
    )
    assert result["estimate_basis"] == "Historical award data"
    assert result["probability"] is None
