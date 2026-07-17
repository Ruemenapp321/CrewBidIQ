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
