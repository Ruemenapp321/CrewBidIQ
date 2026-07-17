from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from app.parsers import american
from app.main import app, db, detect_airports, detect_layover_cities, score_pairing


SAMPLE = """<<<CREWBIDIQ_PAGE:8>>>
LAX - LOS ANGELES
AUGUST 2026
DAY  --DEPARTURE--  ---ARRIVAL---  GRND/ REST/
DP D/A EQ FLT# STA DLCL/DHBT ML STA ALCL/AHBT BLOCK SYNTH TPAY DUTY TAFB FDP CALENDAR 08/01-08/30
LAX 777
SEQ 520  2 OPS POSN CA FO MO TU WE TH FR SA SU
RPT 0927/0927
1 1/1 92 1595D LAX 0957/0957 PHX 1129/1129 AA 1.32 1.41X -- -- 5 -- -- -- --
1 1/1 83 404 PHX 1310/1310 L MIA 2043/1743 4.33 -- -- -- -- -- -- --
RLS 2113/1813 4.33 1.32 6.05 8.46 8.16 -- -- -- -- -- -- --
MIA MIAMI AIRPORT MARRIOTT 305-649-5000 11.42 -- -- -- -- -- -- --
SHUTTLE SUPER SHUTTLE 305-555-0100
RPT 0855/0555 -- 12 -- -- -- -- --
2 2/2 83 3172 MIA 0955/0655 L PHX 1141/1141 4.46 2.30X
2 2/2 26 3119D PHX 1411/1411 LAX 1534/1534 AA 1.23
RLS 1604/1604 4.46 1.23 6.09 10.09 5.46
TTL 9.19 2.55 12.14 30.37
LAX 777
SEQ 591•‧‧ 1 OPS POSN FB JAPANESE OPERATION MO TU WE TH FR SA SU
RPT 2155/2155
1 1/3 •‧‧82 73 LAX 2255/2255 D SYD 0705/1405 15.10 -- -- -- -- 15 -- --
RLS 0735/1435 15.10 0.00 15.10 16.40 16.10
SYD PULLMAN SYDNEY HYDE PARK 6 129-361-8400 24.35
RPT 0810/1510
2 4/4 82 72 SYD 0910/1610 L LAX 0635/0635 13.55
RLS 0705/0705 13.55 0.00 13.55 15.25 14.55
TTL 29.05 0.00 29.05 56.40
"""


FLEET_SAMPLE = """<<<CREWBIDIQ_PAGE:25>>>
AUGUST 2026
DAY --DEPARTURE-- ---ARRIVAL--- GRND/ REST/
DP D/A EQ FLT# STA DLCL/DHBT ML STA ALCL/AHBT BLOCK SYNTH TPAY DUTY TAFB FDP CALENDAR 08/01-08/30
LAX 787
SEQ 660 1 OPS POSN FO MO TU WE TH FR SA SU
RPT 2200/2200
1 1/2 78 136 LAX 2300/2300 D LHR 1715/0915 10.15 -- -- -- -- -- -- 9 -- -- -- --
RLS 1745/0945 10.15 0.00 10.15 11.45 11.15
TTL 10.15 0.00 10.15 11.45
"""


MULTI_DAY_SAMPLE = """<<<CREWBIDIQ_PAGE:30>>>
AMERICAN AIRLINES
AUGUST 2026
DAY --DEPARTURE-- ---ARRIVAL--- GRND/ REST/
DP D/A EQ FLT# STA DLCL/DHBT ML STA ALCL/AHBT BLOCK SYNTH TPAY DUTY TAFB FDP CALENDAR 08/01-08/30
LAX 787
SEQ 703 1 OPS POSN FO MO TU WE TH FR SA SU
RPT 0800/0800
1 1/1 78 100 LAX 0900/0900 D JFK 1700/1400 5.00
RLS 1730/1430 5.00 0.00 5.00 9.30 9.00
JFK HOTEL 15.00
RPT 0830/0530
2 2/3 78 101 JFK 0930/0630 D LAX 1200/1200 5.30
RLS 1230/1230 5.30 0.00 5.30 8.00 7.30
TTL 10.30 0.00 10.30 52.30
LAX 787
SEQ 705 1 OPS POSN FO MO TU WE TH FR SA SU
RPT 2200/2200
1 1/3 78 200 LAX 2300/2300 D SYD 0700/1400 15.00
RLS 0730/1430 15.00 0.00 15.00 16.30 16.00
SYD HOTEL 24.00
RPT 2100/0400
2 4/5 78 201 SYD 2200/0500 D LAX 1800/1800 14.00
RLS 1830/1830 14.00 0.00 14.00 15.30 15.00
TTL 29.00 0.00 29.00 96.30
LAX 787
SEQ 706 1 OPS POSN FO MO TU WE TH FR SA SU
RPT 2300/2300
1 1/2 78 300 LAX 2350/2350 D JFK 0020/2120 5.30
RLS 0050/2150 5.30 0.00 5.30 1.50 1.20
TTL 5.30 0.00 5.30 1.50
"""


def sequence_fixture(days: int, sequence_id: str, *, intermediate_ttl: bool = False) -> str:
    lines = [
        "AMERICAN AIRLINES", "AUGUST 2026",
        "DP D/A EQ FLT# STA DLCL/DHBT ML STA ALCL/AHBT BLOCK SYNTH TPAY DUTY TAFB FDP CALENDAR 08/01-08/30",
        "LAX 787", f"SEQ {sequence_id} 1 OPS POSN FO MO TU WE TH FR SA SU",
    ]
    departure, arrival = "LAX", "JFK"
    for day in range(1, days + 1):
        lines.extend([
            "RPT 0800/0800",
            f"{day} {day}/{day} 78 {100 + day} {departure} 0900/0900 D {arrival} 1700/1400 5.00",
            "RLS 1730/1430 5.00 0.00 5.00 9.30 9.00",
        ])
        if day < days:
            lines.append(f"{arrival} HOTEL {12 + day}.00")
        if intermediate_ttl and day == 2:
            lines.append("TTL 10.00 0.00 10.00 33.30")
        departure, arrival = arrival, departure
    lines.append(f"TTL {days * 5}.00 0.00 {days * 5}.00 {days * 24}.00")
    return "\n".join(lines)


def test_detects_aa_cockpit_sequence_package():
    assert american.detect(SAMPLE) >= 0.9


def test_parses_duties_deadheads_layovers_totals_and_dates():
    rows = american.parse(SAMPLE)
    assert len(rows) == 2
    row = rows[0]
    assert row["id"] == "520"
    assert row["operations"] == 2
    assert row["positions"] == ["CA", "FO"]
    assert row["fleet"] == "777"
    assert row["start_dates"] == ["2026-08-05", "2026-08-12"]
    assert len(row["legs"]) == 4
    assert row["legs"][0]["deadhead"] is True
    assert row["legs"][0]["raw_flight"] == "1595D"
    assert row["legs"][0]["departure_home_time"] == "0957"
    assert row["layovers"] == [{"city": "MIA", "duration": "11.42", "hotel": "MIAMI AIRPORT MARRIOTT", "hotel_phone": "305-649-5000", "transportation_provider": "SHUTTLE SUPER SHUTTLE", "transportation_phone": "305-555-0100"}]
    assert row["credit"] == "12.14"
    assert row["raw_total_pay"] == "12.14"
    assert row["total_pay"] == "12:14"
    assert row["source_total_pay_label"] == "TPAY"
    assert row["tafb"] == "30.37"
    assert row["equipment_codes"] == ["92", "83", "26"]
    assert row["equipment_mapping_status"] == "raw_unmapped"
    assert row["source_pdf_page"] == 8
    assert row["source_terminology"] == "sequence"
    assert row["fleet_section"] == "LAX 777"
    assert row["total_flight_segments"] == 4
    assert [duty["leg_count"] for duty in row["duty_periods"]] == [2, 2]
    assert row["duty_periods"][0]["report_local"] == "0927"
    assert row["duty_periods"][0]["report_home_base"] == "0927"
    assert row["duty_periods"][0]["release_local"] == "2113"
    assert row["duty_periods"][0]["release_home_base"] == "1813"
    assert row["duty_periods"][0]["trip_pay"] == "6.05"
    assert row["duty_periods"][0]["raw_tpay"] == "6.05"
    assert row["duty_periods"][0]["total_pay"] == "6:05"
    assert "SEQ 520  2 OPS" in row["block"]


def test_parses_relief_position_qualifier_and_date_line_days():
    row = american.parse(SAMPLE)[1]
    assert row["id"] == "591"
    assert row["positions"] == ["FB"]
    assert row["operation_qualifiers"] == ["JAPANESE OPERATION"]
    assert row["legs"][0]["departure_day"] == 1
    assert row["legs"][0]["arrival_day"] == 3
    assert row["start_dates"] == ["2026-08-15"]
    assert row["sequence_days"] == 4
    assert row["calendar_span_days"] == 4
    assert row["duty_period_count"] == 2
    assert row["overnight_count"] == 1
    assert row["first_report"] == "2155"
    assert row["final_release"] == "0705"


def test_layovers_are_distinct_from_all_operating_cities():
    row = american.parse(SAMPLE)[0]
    assert detect_layover_cities(row) == ["MIA"]
    assert detect_airports(row["block"], row) == ["LAX", "PHX", "MIA"]


def test_tracks_fleet_changes_and_long_haul_source_page():
    row = american.parse(FLEET_SAMPLE)[0]
    assert row["id"] == "660"
    assert row["base"] == "LAX"
    assert row["fleet"] == "787"
    assert row["fleet_section"] == "LAX 787"
    assert row["source_pdf_page"] == 25
    assert row["legs"][0]["departure_day"] == 1
    assert row["legs"][0]["arrival_day"] == 2


def test_intro_pages_do_not_create_false_sequences():
    intro = """AMERICAN AIRLINES\nAUGUST 2026\nSEQUENCE CONSTRUCTION NOTES\nPOSN CA FO\nSYNTH TPAY TAFB\n"""
    assert american.parse(intro) == []


def test_american_tpay_total_is_exposed_and_rankable_as_total_pay():
    pairing = american.parse(SAMPLE)[0]
    result = score_pairing(pairing, {"pay_priority": "total_pay", "prefer_operate": False})
    assert result["total_pay"] == "12:14"
    assert result["raw_total_pay"] == "12.14"
    assert result["total_pay_per_duty_day"] == "6:07"
    assert result["pay_explanation"] == "Total Pay: 12:14"
    assert "pay_components" not in result


def test_american_three_and_five_day_sequences_use_calendar_span_not_duty_count():
    rows = {row["id"]: row for row in american.parse(MULTI_DAY_SAMPLE)}
    assert rows["703"]["sequence_days"] == 3
    assert rows["703"]["duty_period_count"] == 2
    assert rows["705"]["sequence_days"] == 5
    assert rows["705"]["duty_period_count"] == 2
    assert rows["705"]["total_flight_segments"] == 2
    assert rows["705"]["normalization_diagnostics"]["length_basis"] == "report_to_release_calendar_span"


def test_american_after_midnight_release_uses_arrival_day_without_double_counting():
    row = next(row for row in american.parse(MULTI_DAY_SAMPLE) if row["id"] == "706")
    assert row["sequence_days"] == 2
    assert row["first_report_day"] == 1
    assert row["final_release_day"] == 2


def test_american_scoring_uses_sequence_days_and_keeps_all_lengths_without_default_limit():
    parsed = american.parse(SAMPLE + "\n" + MULTI_DAY_SAMPLE)
    results = [score_pairing(row, {"prefer_operate": False}) for row in parsed]
    assert {result["trip_length"] for result in results} >= {2, 3, 4, 5}
    five_day = next(row for row in parsed if row["id"] == "705")
    exact = score_pairing(five_day, {"preferred_trip_lengths": ["5"], "prefer_operate": False})
    assert exact["trip_length"] == 5
    assert exact["trip_length_match"] is True
    assert "Matches your preferred 5-day trip length" in exact["reasons"]


def test_valid_one_through_five_day_sequences_all_survive_without_length_restriction():
    parsed = [american.parse(sequence_fixture(days, f"8{days:02d}"))[0] for days in range(1, 6)]
    results = [score_pairing(row, {"prefer_operate": False}) for row in parsed]
    assert [row["sequence_days"] for row in parsed] == [1, 2, 3, 4, 5]
    assert [row["calendar_span_days"] for row in parsed] == [1, 2, 3, 4, 5]
    assert [row["duty_period_count"] for row in parsed] == [1, 2, 3, 4, 5]
    assert all(result["eligible"] for result in results)
    assert {result["trip_length"] for result in results} == {1, 2, 3, 4, 5}


def test_ttl_page_subtotal_does_not_truncate_a_four_day_sequence():
    row = american.parse(sequence_fixture(4, "840", intermediate_ttl=True))[0]
    assert row["sequence_days"] == 4
    assert row["duty_period_count"] == 4
    assert len(row["legs"]) == 4
    assert row["final_release"] == "1730"


def test_four_day_request_classifies_four_day_exact_and_shorter_as_near():
    rows = [american.parse(sequence_fixture(days, f"9{days:02d}"))[0] for days in (2, 3, 4)]
    profile = {"required_trip_lengths": ["4"], "trip_length_priority": ["4"], "prefer_operate": False}
    results = {result["trip_length"]: result for result in (score_pairing(row, profile) for row in rows)}
    assert results[4]["eligible"] is True
    assert results[4]["match_class"] == "exact"
    for days in (2, 3):
        assert results[days]["eligible"] is False
        assert results[days]["match_class"] == "near"
        assert results[days]["eligibility_violations"] == [f"Requires 4 days; this trip is {days} days"]


def test_four_day_sequence_survives_pdf_to_api_and_frontend_display(monkeypatch):
    monkeypatch.setenv("RECOMMENDATION_DEBUG_ENABLED", "true")
    document = fitz.open()
    page = document.new_page(width=900, height=1200)
    page.insert_textbox(fitz.Rect(30, 30, 870, 1170), sequence_fixture(4, "944"), fontname="courier", fontsize=8)
    payload = document.tobytes()
    document.close()

    with TestClient(app) as client:
        upload = client.post(
            "/api/jobs",
            data={"airline": "american", "context": "classic", "profile_json": '{"required_trip_lengths":["4"],"trip_length_priority":["4"]}'},
            files={"file": ("LAX787 AUG 2026.pdf", payload, "application/pdf")},
        )
        assert upload.status_code == 200
        job_id = upload.json()["job_id"]
        try:
            response = client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "complete"
            result = body["results"][0]
            assert result["pairing"] == "944"
            assert result["sequence_days"] == result["trip_length"] == 4
            assert result["calendar_span_days"] == 4
            assert result["duty_period_count"] == 4
            assert result["overnight_count"] == 3
            assert result["first_report"] == "0800"
            assert result["final_release"] == "1730"
            assert result["match_class"] == "exact"
            diagnostic = result["recommendation_debug"]["sequence"]
            assert diagnostic == {
                "sequence_id": "944", "parsed_sequence_days": 4, "calendar_span_days": 4,
                "duty_period_count": 4, "overnight_count": 3, "first_report": "0800",
                "final_release": "1730", "eligibility": "eligible", "rejection_reason": None,
            }
            frontend = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
            assert "item.trip_length ? `${item.trip_length} days`" in frontend
            assert "allResults.filter(item => item.eligible !== false)" in frontend
            assert "trip_length <= 2" not in frontend
        finally:
            with db() as conn:
                conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
