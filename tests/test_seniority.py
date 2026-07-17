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
    outlook = estimate_hold_outlook({"operations": 10, "matched_preferences": ["Hawaii"]}, context)
    assert outlook["outlook"] == "More likely"
    assert outlook["probability"] is None
    assert outlook["estimate_basis"] == "Inventory-based estimate only"


def test_hold_outlook_is_insufficient_without_inventory_or_seniority():
    assert estimate_hold_outlook({}, None)["outlook"] == "Insufficient data"
