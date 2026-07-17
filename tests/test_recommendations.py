import pytest

from app.recommendations import (
    evaluate_eligibility,
    evaluate_recommendation,
    length_rule_matches,
    matching_length_rank,
    rank_eligible_recommendations,
    recommendation_pipeline,
)
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


def test_stage_one_collects_every_hard_failure_and_stage_two_is_skipped():
    candidate = result(length=2, city="HNL", redeye="WOCL departure")
    candidate["deadheads"] = 2
    profile = {
        "required_trip_lengths": ["4"],
        "required_destination_groups": ["EUROPE"],
        "must_avoid_redeye": True,
        "hard_max_deadheads": 0,
    }
    evaluated = evaluate_recommendation(candidate, profile)
    assert evaluated["eligibility_stage"] == "failed"
    assert evaluated["ranking_stage"] == "not_ranked_hard_failure"
    assert evaluated["ranking_score"] is None
    assert evaluated["match_label"] == "Near Match"
    assert evaluated["eligibility_violations"] == [
        "Requires 4 days; this trip is 2 days",
        "Missing required destination group EUROPE",
        "Contains a WOCL departure, which you marked Must avoid",
        "Has 2 deadheads; hard maximum is 0",
    ]


def test_exact_match_has_passed_every_hard_requirement():
    profile = {
        "required_trip_lengths": ["4"],
        "required_destination_groups": ["HAWAII"],
        "must_avoid_redeye": True,
        "hard_max_deadheads": 0,
    }
    evaluated = evaluate_recommendation(result(), profile)
    assert evaluated["match_label"] == "Exact Match"
    assert evaluated["eligible"] is True
    assert evaluated["eligibility_violations"] == []
    assert len(evaluated["hard_requirement_matches"]) == 4
    assert evaluated["ranking_stage"] == "ranked_eligible"


def test_stage_two_rejects_ineligible_input_even_with_a_large_score():
    with pytest.raises(ValueError, match="eligible trips only"):
        rank_eligible_recommendations([
            {"pairing": "NEAR", "eligible": False, "score": 99999, "eligibility_violations": ["hard failure"]}
        ])


def test_no_exact_match_returns_closest_near_matches_by_complete_failure_distance():
    profile = {"required_trip_lengths": ["4"], "required_destination_groups": ["EUROPE"]}
    one_failure = {"pairing": "ONE", "package_id": "pkg", **result(length=4, city="HNL")}
    two_failures = {"pairing": "TWO", "package_id": "pkg", **result(length=2, city="HNL")}
    evaluated = [evaluate_recommendation(two_failures, profile), evaluate_recommendation(one_failure, profile)]
    for source, output in zip((two_failures, one_failure), evaluated):
        output.update({"pairing": source["pairing"], "package_id": source["package_id"]})
    ordered = recommendation_pipeline(evaluated, "pkg")
    assert not any(item["eligible"] for item in ordered)
    assert [item["pairing"] for item in ordered] == ["ONE", "TWO"]
    assert [len(item["eligibility_violations"]) for item in ordered] == [1, 2]


def test_explicit_preference_rules_support_all_four_levels():
    candidate = result(length=4, city="HNL")
    profile = {
        "preference_rules": [
            {"criterion": "trip_length", "value": "4", "level": "Must have"},
            {"criterion": "destination", "value": "HAWAII", "level": "Prefer"},
            {"criterion": "redeye", "level": "Avoid"},
            {"criterion": "deadheads", "level": "Must avoid"},
        ]
    }
    evaluated = evaluate_recommendation(candidate, profile)
    assert evaluated["eligible"] is True
    assert evaluated["preference_classes"] == {
        "must_have": ["trip_length"],
        "prefer": ["destination"],
        "avoid": ["redeye"],
        "must_avoid": ["deadheads"],
    }
    assert evaluated["match_label"] == "Exact Match"


def test_recommendation_pipeline_rejects_mixed_packages():
    with pytest.raises(ValueError, match="another bid package"):
        recommendation_pipeline([
            {"pairing": "A", "package_id": "package-a", "eligible": True, "match_class": "exact"}
        ], "package-b")


def test_instructional_example_is_a_hard_integrity_failure():
    candidate = result()
    candidate.update({"page_classification": "EXAMPLE", "bidable_inventory_confirmed": False})
    evaluated = evaluate_recommendation(candidate, {})
    assert evaluated["eligible"] is False
    assert evaluated["ranking_stage"] == "not_ranked_hard_failure"
    assert evaluated["eligibility_violations"] == [
        "Source record is not confirmed bidable inventory",
        "Source record is instructional or example material, not bidable inventory",
    ]


def test_explanations_do_not_use_unsupported_generic_language():
    evaluated = evaluate_recommendation(result(), {"required_trip_lengths": ["4"], "elite_cities": ["HAWAII"]})
    explanation = " ".join(
        value
        for section in ("qualification_reasons", "matched_preferences", "compromises", "neutral_attributes", "eligibility_violations")
        for value in evaluated[section]
    )
    for phrase in ("Good quality of life", "Competitive", "Great connection coverage", "Strong trip", "Heavy recovery"):
        assert phrase not in explanation
