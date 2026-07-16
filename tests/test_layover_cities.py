from app.main import detect_layover_cities, match_level, score_pairing


def sample_pairing():
    return {
        "id": "2478",
        "block": "#2478",
        "legs": [
            {"day": "A", "deadhead": False, "departure": "ATL", "arrival": "MCO"},
            {"day": "A", "deadhead": False, "departure": "MCO", "arrival": "BOS"},
            {"day": "B", "deadhead": False, "departure": "BOS", "arrival": "JFK"},
            {"day": "B", "deadhead": False, "departure": "JFK", "arrival": "ATL"},
        ],
        "layovers": [{"city": "BOS", "duration": "15:30", "hotel": None}],
        "credit": "12.00",
        "tafb": "30.00",
        "parser": "test",
        "confidence": 1.0,
    }


def profile():
    return {
        "elite_cities": ["BOS", "MCO"],
        "secondary_cities": [], "small_cities": [], "penalty_cities": [],
        "preferred_aircraft": [], "required_days_off": [], "preferred_days_off": [],
        "holiday_dates": [], "weights": {"elite": 10}, "max_deadheads": 99,
        "max_transfers": 99, "prefer_operate": False, "allow_productive_redeye": True,
        "avoid_holidays": False, "avoid_reserve": False,
        "earliest_report_minutes": 0, "latest_release_minutes": 1439,
    }


def test_explicit_layovers_exclude_connection_and_base():
    pairing = sample_pairing()
    assert detect_layover_cities(pairing) == ["BOS"]
    result = score_pairing(pairing, profile())
    assert result["cities"] == ["BOS"]
    assert result["touched_cities"] == ["ATL", "MCO", "BOS", "JFK"]
    assert any("BOS" in reason for reason in result["reasons"])
    assert not any("MCO" in reason for reason in result["reasons"])


def test_layover_inference_uses_end_of_nonfinal_duty_only():
    pairing = sample_pairing()
    pairing["layovers"] = []
    assert detect_layover_cities(pairing) == ["BOS"]


def test_match_levels_are_absolute_and_required_conflicts_are_low():
    assert match_level(65, []) == "excellent"
    assert match_level(35, []) == "strong"
    assert match_level(12, []) == "good"
    assert match_level(2, []) == "fair"
    assert match_level(-1, []) == "low"
    assert match_level(100, ["Required off: 2026-08-10"]) == "low"
