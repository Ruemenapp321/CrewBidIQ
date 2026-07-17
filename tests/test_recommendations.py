from app.recommendations import evaluate_recommendation, length_rule_matches, matching_length_rank
from app.main import sort_results


def result(length=4, city="HNL", conflicts=None, redeye="none"):
    return {
        "trip_length": length, "cities": [city], "calendar_conflicts": conflicts or [],
        "redeye": redeye, "duty_legs": [1] * length, "deadheads": 0, "data_quality": "complete",
    }


def test_length_rules_support_exact_ranges_and_six_plus():
    assert length_rule_matches(4, "4")
    assert length_rule_matches(4, "3-5")
    assert length_rule_matches(7, "6+")
    assert not length_rule_matches(5, "6+")
    assert matching_length_rank(7, ["6+", "5", "4", "3", "2", "1"]) == 0


def test_hard_requirement_violation_is_near_match_only():
    evaluated = evaluate_recommendation(result(length=2), {"required_trip_lengths": ["4"]})
    assert evaluated["eligible"] is False
    assert evaluated["match_label"] == "Near Match"
    assert evaluated["relaxations_required"] == ["Relax: Requires 4 days; this trip is 2 days"]


def test_exact_match_uses_actual_preferences_and_data():
    evaluated = evaluate_recommendation(result(), {
        "required_trip_lengths": ["4"], "trip_length_priority": ["4", "3"], "elite_cities": ["HAWAII"]
    })
    assert evaluated["eligible"] is True
    assert evaluated["match_label"] == "Exact Match"
    assert any("Hawaii" in value or "HAWAII" in value for value in evaluated["matched_preferences"])


def test_ineligible_result_cannot_be_classified_above_eligible_result():
    eligible = evaluate_recommendation(result(length=4), {"required_trip_lengths": ["4"]})
    ineligible = evaluate_recommendation(result(length=2), {"required_trip_lengths": ["4"]})
    assert eligible["eligible"] and not ineligible["eligible"]


def test_sort_never_places_high_pay_ineligible_trip_above_eligible_trip():
    results = [
        {"pairing": "NEAR", "eligible": False, "match_class": "near", "data_quality": "complete", "pay_priority": "total_pay", "pay_priority_value": 9999, "score": 9999},
        {"pairing": "EXACT", "eligible": True, "match_class": "exact", "data_quality": "complete", "pay_priority": "total_pay", "pay_priority_value": 1, "score": 1},
    ]
    sort_results(results)
    assert [item["pairing"] for item in results] == ["EXACT", "NEAR"]


def test_ordered_trip_length_priority_controls_eligible_sorting():
    results = [
        {"pairing": "THREE", "eligible": True, "match_class": "exact", "data_quality": "complete", "trip_length_preference_active": True, "length_priority_rank": 2, "score": 100},
        {"pairing": "FIVE", "eligible": True, "match_class": "strong", "data_quality": "complete", "trip_length_preference_active": True, "length_priority_rank": 0, "score": 1},
    ]
    sort_results(results)
    assert [item["pairing"] for item in results] == ["FIVE", "THREE"]


def test_strongly_avoided_destination_is_a_hard_violation():
    evaluated = evaluate_recommendation(result(city="HNL"), {
        "destination_preferences": [{"value": "HAWAII", "strength": "Strongly avoid"}]
    })
    assert evaluated["eligible"] is False
    assert evaluated["eligibility_violations"] == ["Includes strongly avoided destination HAWAII"]
