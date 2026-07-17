from app.southwest_planning import optimize_schedule_conflicts, rank_southwest_line


def member(match_class="exact", raw_score=9999):
    return {"match_class": match_class, "score": raw_score, "duty_legs": [1, 2], "layovers": [{"city": "HNL"}], "eligibility_violations": []}


def test_line_ranker_uses_normalized_components_not_raw_pairing_score():
    line = {"monthly_tfp": "90.00", "tfp_per_duty_period": "7.50", "duty_period_count": 12}
    high_raw = rank_southwest_line(line, [member(raw_score=999999)], {})
    low_raw = rank_southwest_line(line, [member(raw_score=-999999)], {})
    assert high_raw["score"] == low_raw["score"]
    assert set(high_raw["line_score_components"]) == {"calendar_fit", "pairing_quality", "monthly_tfp", "tfp_efficiency", "nights_at_home"}


def test_required_day_conflict_makes_line_near_match_only():
    line = {"monthly_tfp": "90.00", "tfp_per_duty_period": "7.50", "work_dates": ["2026-08-11"]}
    ranked = rank_southwest_line(line, [member()], {"required_days_off": ["2026-08-11"]})
    assert ranked["eligible"] is False
    assert ranked["match_label"] == "Near Match"


def test_conflict_optimization_keeps_conflict_value_separate_and_disclaimed():
    line = {"work_dates": ["2026-08-11", "2026-08-12"]}
    events = [{"type": "vacation", "dates": ["2026-08-11"]}]
    result = optimize_schedule_conflicts(line, events, "maximize_conflicts")
    assert result["conflict_value"] == 10
    assert result["general_line_quality_unchanged"] is True
    assert "not guaranteed pay" in result["display_label"]
