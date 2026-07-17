from datetime import date
from pathlib import Path

from app.main import score_pairing, score_southwest_line, parse_southwest_lines
from app.parsers import southwest
from app.reporting import build_bid_report
from app.southwest_time import normalize_herb_event
from tests.test_southwest_lines import LINE_SAMPLE
from tests.test_southwest_parser import SAMPLE


def test_july_lax_report_converts_cdt_to_pdt():
    event = normalize_herb_event("0705", date(2026, 7, 6), "LAX")
    assert event["normalized_utc_timestamp"] == "2026-07-06T12:05:00+00:00"
    assert event["local_event_timestamp"] == "2026-07-06T05:05:00-07:00"
    assert event["local_event_timezone"] == "America/Los_Angeles"


def test_winter_conversion_uses_cst_and_pst():
    event = normalize_herb_event("0705", date(2026, 1, 5), "LAX")
    assert event["normalized_utc_timestamp"] == "2026-01-05T13:05:00+00:00"
    assert event["local_event_timestamp"] == "2026-01-05T05:05:00-08:00"


def test_dst_transition_dates_use_actual_event_date():
    before = normalize_herb_event("0705", date(2026, 3, 7), "LAX")
    after = normalize_herb_event("0705", date(2026, 3, 9), "LAX")
    assert before["normalized_utc_timestamp"] == "2026-03-07T13:05:00+00:00"
    assert after["normalized_utc_timestamp"] == "2026-03-09T12:05:00+00:00"


def test_departure_and_arrival_are_localized_for_each_airport():
    row = southwest.parse(SAMPLE)[0]
    lax_to_oak = row["legs"][0]
    sat_to_den = row["legs"][3]
    assert (lax_to_oak["departure_time"], lax_to_oak["arrival_time"]) == ("0605", "0725")
    assert sat_to_den["departure_time"] == "0700"
    assert sat_to_den["arrival_time"] == "0810"
    assert sat_to_den["departure_local_event_timezone"] == "America/Chicago"
    assert sat_to_den["arrival_local_event_timezone"] == "America/Denver"


def test_source_herb_values_are_preserved_only_in_internal_parse_record():
    row = southwest.parse(SAMPLE)[0]
    leg = row["legs"][0]
    provenance = leg["departure_time_provenance"]
    assert provenance["source_time_herb"] == "0805"
    assert provenance["source_timezone"] == "America/Chicago"
    assert provenance["normalized_utc_timestamp"]
    assert provenance["local_event_timestamp"]
    scored = score_pairing(row, {})
    assert "source_time_herb" not in str(scored).lower()
    assert "source_departure_time_herb" not in str(scored).lower()
    assert scored["legs"][0]["departure_time"] == "0605"


def test_southwest_line_details_and_export_are_local_only():
    pairing = southwest.parse(SAMPLE)[0]
    scored = score_pairing(pairing, {})
    line = parse_southwest_lines(LINE_SAMPLE, {"XA28"})[0]
    result = score_southwest_line(line, {"XA28": scored}, {})
    assert "Herb" not in result["original_display"]
    assert "LAX 0805" not in result["original_display"]
    assert "LAX 0605" in result["original_display"]
    pdf = build_bid_report([result], {}, "southwest", "LAX AUG 2026.zip")
    assert b"Herb Time" not in pdf


def test_missing_event_year_never_exposes_source_clock_as_local():
    no_year = SAMPLE.replace("SCHEDULE PERIOD: AUG 1, 2026 - AUG 31, 2026 POSITION: FO\n", "")
    row = southwest.parse(no_year)[0]
    assert row["time_normalization_status"] == "unavailable"
    assert row["legs"][0]["departure_time"] is None
    assert score_pairing(row, {})["legs"][0]["departure_time"] is None


def test_standard_user_surfaces_do_not_offer_herb_time():
    for filename in ("app/static/app.js", "app/static/labs.js", "app/static/flight-deck.js", "app/reporting.py"):
        text = Path(filename).read_text(encoding="utf-8")
        assert "Herb Time" not in text
        assert "View in Herb" not in text


def test_flight_deck_trip_flow_receives_southwest_local_times_only():
    row = southwest.parse(SAMPLE)[0]
    scored = score_pairing(row, {})
    model = scored["canonical_trip"]
    first_day = model["duty_days"][0]
    second_day = model["duty_days"][1]
    first_leg = first_day["ordered_legs"][0]

    assert first_day["calendar_date"] == "2026-08-10"
    assert first_day["report_event"]["local_time"] == "2026-08-10T05:05:00-07:00"
    assert first_day["report_event"]["airport"] == "LAX"
    assert first_day["release_event"]["local_time"] == "1455"
    assert first_day["release_event"]["airport"] == "SAT"
    assert second_day["calendar_date"] == "2026-08-11"
    assert second_day["report_event"]["local_time"] == "0630"
    assert second_day["report_event"]["airport"] == "SAT"
    assert second_day["release_event"]["local_time"] == "1235"
    assert second_day["release_event"]["airport"] == "LAX"
    assert [layover["airport"] for layover in model["layovers"]] == ["SAT"]
    assert first_day["layover_after_duty"]["airport"] == "SAT"
    assert second_day["layover_after_duty"] is None
    assert first_leg["local_departure_time"] == "0605"
    assert first_leg["local_arrival_time"] == "0725"
    assert first_leg["connection_after"] == "00:40"
    assert first_leg["source_departure_time"] is None
    assert first_leg["source_arrival_time"] is None
    assert first_day["report_event"]["source_time"] is None
    assert all(event["source_time"] is None for event in model["ordered_events"])
