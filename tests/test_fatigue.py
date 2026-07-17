from app.fatigue import build_fatigue_index


def trip(departures, *, tafb="120:00", length=6, layover="14:00"):
    return {
        "legs": [{"departure_time": time, "arrival_time": "1200"} for time in departures],
        "duty_legs": [1] * length,
        "trip_length": length,
        "tafb": tafb,
        "layovers": [{"duration": layover}],
    }


def test_trip_length_and_tafb_alone_do_not_create_high_fatigue():
    result = build_fatigue_index(trip(["1000"] * 6, tafb="200:00", length=10))
    assert result["level"] == "Low"


def test_wocl_and_repeated_wocl_raise_fatigue():
    one = build_fatigue_index(trip(["0530", "1000"]))
    repeated = build_fatigue_index(trip(["0530", "0430", "1000"]))
    assert one["score"] < repeated["score"]
    assert repeated["level"] in {"High", "Very High"}


def test_recovery_mitigates_but_does_not_erase_wocl():
    short = build_fatigue_index(trip(["0530", "0430"], layover="9:00"))
    recovered = build_fatigue_index(trip(["0530", "0430"], layover="16:00"))
    assert recovered["score"] < short["score"]
    assert any("WOCL" in factor for factor in recovered["contributing_factors"])


def test_missing_times_reduce_confidence():
    result = build_fatigue_index({"legs": [{"departure_time": None}], "duty_legs": [1]})
    assert result["level"] == "Insufficient Data"
    assert result["confidence"] == "Low"
    assert result["missing_data_warning"]


def test_repeated_early_starts_long_duties_and_workload_are_explained():
    result = build_fatigue_index({
        "legs": [
            {"day": day, "departure_time": "0615", "arrival_time": "0900"}
            for day in range(1, 4) for _ in range(4)
        ],
        "duty_legs": [4, 4, 4],
        "duty_periods": [{"fdp": "12:30"}, {"fdp": "13:00"}, {"fdp": "12:15"}],
        "layovers": [{"duration": "11:00"}, {"duration": "11:30"}],
    })
    factors = " ".join(result["contributing_factors"])
    assert result["level"] in {"High", "Very High"}
    assert "repeated duty starts" in factors
    assert "Repeated high-workload" in factors
    assert "Long scheduled duty" in factors


def test_mid_rotation_redeye_timezone_shift_and_following_work_are_explained():
    result = build_fatigue_index({
        "ordered_legs": [
            {
                "duty_day_index": 1,
                "local_departure_time": "2026-07-01T10:00:00-04:00",
                "local_arrival_time": "2026-07-01T12:00:00-04:00",
            },
            {
                "duty_day_index": 2,
                "local_departure_time": "2026-07-02T22:30:00-04:00",
                "local_arrival_time": "2026-07-03T05:00:00-07:00",
            },
            {
                "duty_day_index": 3,
                "local_departure_time": "2026-07-03T11:00:00-07:00",
                "local_arrival_time": "2026-07-03T13:00:00-07:00",
            },
        ],
        "duty_legs": [1, 1, 1],
        "layovers": [{"duration": "12:00"}, {"duration": "10:00"}],
    })
    factors = " ".join(result["contributing_factors"])
    assert "Mid-rotation redeye" in factors
    assert "Time-zone transition" in factors
    assert "Flying continues after a redeye" in factors


def test_far_legality_is_always_separate_from_fatigue():
    result = build_fatigue_index(trip(["0530", "0430"]))
    assert "separate from FAR legality" in result["legality_assessment"]
    assert result["basis"] == "schedule_only"
