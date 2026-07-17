from app.trip_intent import interpret_trip_intent, trip_intent_profile


def test_interprets_four_day_one_and_done_transcon_request_for_review():
    intent = interpret_trip_intent("I want a 4-day with one-and-done transcons.")
    assert intent["required_trip_lengths"] == ["4"]
    assert intent["max_legs_per_day"] == 1
    assert intent["transcontinental"] is True
    assert intent["needs_review"] is True
    profile = trip_intent_profile(intent)
    assert profile["required_trip_lengths"] == ["4"]


def test_interprets_no_redeyes_leg_limit_and_report_time():
    intent = interpret_trip_intent("No redeyes, two legs max, report after 0900.")
    assert intent["must_avoid_redeye"] is True
    assert intent["hard_max_legs_per_day"] == 2
    assert intent["earliest_report_minutes"] == 540


def test_long_layover_assumption_is_explicit_not_silent():
    intent = interpret_trip_intent("I want a 3-day Hawaii trip with long layovers.")
    assert intent["min_layover_hours"] == 16
    assert intent["assumptions"]
    assert intent["destination_groups"][0]["value"] == "HAWAII"


def test_total_leg_limit_and_commute_context_survive_profile_conversion():
    intent = interpret_trip_intent("Commute friendly, no more than 6 legs total, long haul.")
    profile = trip_intent_profile(intent)
    assert profile["hard_max_total_legs"] == 6
    assert profile["long_haul"] is True
    assert profile["commute_preference"] == "commute_friendly"
