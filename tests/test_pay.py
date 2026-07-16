from app.main import score_pairing, sort_results
from app.pay import parse_delta_pay, tfp_per_day_away, tfp_ratio
from app.parsers import delta


DELTA_SAMPLE = """#4492
A 1234 ATL 0900 MCO 1030 1.30 320
TOTAL CREDIT 21.24TL   TAFB 48.00
TOTAL PAY 22:32TL .11SIT .57EDP .00HOL .00CARVE
"""


def test_delta_total_pay_is_credit_plus_supported_components():
    pay = parse_delta_pay(DELTA_SAMPLE, "21.24")
    assert pay["trip_credit"] == "21.24"
    assert pay["pay_components"] == {"SIT": "0:11", "EDP": "0:57", "HOL": "0:00"}
    assert pay["additional_pay"] == "1:08"
    assert pay["total_pay"] == "22:32"
    assert pay["raw_total_pay"] == "22:32"
    assert pay["unknown_pay_components"] == {"CARVE": "0:00"}


def test_unknown_delta_component_is_preserved_and_not_added():
    pay = parse_delta_pay("TOTAL PAY 10:45TL .15EDP .30XYZ", "10.00")
    assert pay["total_pay"] == "10:15"
    assert pay["unknown_pay_components"] == {"XYZ": "0:30"}


def test_missing_delta_components_are_not_silently_zero():
    pay = parse_delta_pay("TOTAL CREDIT 10.00TL", "10.00")
    assert "additional_pay" not in pay
    assert "total_pay" not in pay
    assert "pay_components" not in pay


def test_delta_parser_exposes_total_pay_and_breakdown():
    row = delta.parse(DELTA_SAMPLE)[0]
    assert row["airline"] == "delta"
    assert row["total_pay"] == "22:32"
    assert row["additional_pay"] == "1:08"


def test_tfp_efficiency_calculations():
    assert tfp_ratio("90.18", 13) == "6.94"
    assert tfp_per_day_away("90.18", "252:10") == "8.58"


def test_delta_scoring_exposes_total_pay_and_additional_breakdown():
    pairing = delta.parse(DELTA_SAMPLE)[0]
    result = score_pairing(pairing, {"pay_priority": "total_pay", "prefer_operate": False})
    assert result["trip_credit"] == "21.24"
    assert result["total_pay"] == "22:32"
    assert result["additional_pay"] == "1:08"
    assert result["pay_components"]["EDP"] == "0:57"
    assert result["pay_explanation"] == "Total Pay: 22:32"


def test_non_delta_airline_never_receives_delta_pay_components():
    pairing = {
        "id": "TEST", "block": "TOTAL PAY 10:30TL .30EDP", "airline": "american",
        "credit": "10.00", "legs": [], "layovers": [],
    }
    result = score_pairing(pairing, {})
    assert "pay_components" not in result
    assert "total_pay" not in result
    assert "additional_pay" not in result


def test_hard_day_off_conflict_stays_below_pay_optimized_result():
    high = delta.parse(DELTA_SAMPLE)[0]
    high["effective"] = "2026-08-11"
    low = delta.parse(DELTA_SAMPLE.replace("#4492", "#4493").replace("22:32", "21:32").replace(".57EDP", ".00EDP"))[0]
    low["effective"] = "2026-08-12"
    profile = {"pay_priority": "total_pay", "required_days_off": ["8/11"], "prefer_operate": False}
    results = [score_pairing(high, profile), score_pairing(low, profile)]
    sort_results(results)
    assert results[0]["pairing"] == "4493"
