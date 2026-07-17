import json
from dataclasses import fields
from pathlib import Path

from app.canonical import (
    CanonicalTrip,
    DutyDay,
    Layover,
    PayBreakdown,
    TripLeg,
    attach_canonical_trip,
    canonical_trip_payload,
)
from app.main import score_pairing
from app.month_planner import build_month_plan
from app.navblue import build_navblue_layers


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "canonical_trips.json").read_text(encoding="utf-8"))


def fixture(airline: str) -> dict:
    return next(dict(item) for item in FIXTURES if item["airline"] == airline)


def test_typed_canonical_schema_contains_required_cross_airline_fields():
    assert {field.name for field in fields(CanonicalTrip)} == {
        "id", "package_id", "airline", "terminology", "base", "fleet", "seat", "bid_month",
        "source_trip_number", "trip_length_days", "calendar_span_days", "duty_period_count",
        "tafb", "pay_breakdown", "tfp", "ordered_events", "ordered_legs",
        "ordered_operating_airports", "operating_cities", "route_map_airports", "simplified_route", "duty_days",
        "layovers", "hotels", "report", "release", "operating_dates", "source_text",
        "source_page", "source_section", "raw_source_fields", "bidable_inventory_confirmed",
        "parser_confidence",
    }
    assert {field.name for field in fields(DutyDay)} == {
        "day_index", "calendar_date", "report_event", "ordered_legs", "release_event", "layover_after_duty",
    }
    assert {field.name for field in fields(TripLeg)} == {
        "sequence_index", "duty_day_index", "origin", "destination", "operating_or_deadhead",
        "flight_number", "equipment", "source_departure_time", "source_arrival_time",
        "utc_departure_time", "utc_arrival_time", "local_departure_time", "local_arrival_time",
        "origin_timezone", "destination_timezone",
    }
    assert {field.name for field in fields(Layover)} == {
        "after_duty_day", "airport", "city", "hotel", "transportation", "start_local",
        "end_local", "duration", "validated",
    }
    assert {field.name for field in fields(PayBreakdown)} == {
        "trip_credit", "edp", "hol", "sit", "additional_pay", "total_pay",
        "raw_pay_tokens", "unresolved_pay_tokens",
    }


def test_cross_airline_adapters_preserve_terminology_and_package_scoped_identity():
    expected = {"delta": "rotation", "american": "sequence", "southwest": "pairing", "generic": "pairing"}
    for airline, terminology in expected.items():
        source = fixture(airline)
        trip = canonical_trip_payload(source)
        assert trip["id"] == f'{source["package_id"]}:{source["id"]}'
        assert trip["source_trip_number"] == source["id"]
        assert trip["package_id"] == source["package_id"]
        assert trip["terminology"] == terminology
        assert trip["bidable_inventory_confirmed"] is True


def test_same_source_trip_number_in_two_packages_is_never_the_same_trip_id():
    first = fixture("generic")
    second = {**first, "package_id": "pkg-generic-replacement"}
    assert canonical_trip_payload(first)["id"] == "pkg-generic:G400"
    assert canonical_trip_payload(second)["id"] == "pkg-generic-replacement:G400"
    assert canonical_trip_payload(first)["id"] != canonical_trip_payload(second)["id"]


def test_repeated_airports_remain_ordered_and_connections_are_not_layovers():
    trip = canonical_trip_payload(fixture("delta"))
    route_events = [
        event["airport"] for event in trip["ordered_events"]
        if event["event_type"] in {"departure", "arrival"}
    ]
    assert route_events == ["ATL", "MCO", "MCO", "BOS", "BOS", "ATL"]
    assert [layover["airport"] for layover in trip["layovers"]] == ["BOS"]
    assert "MCO" not in [layover["airport"] for layover in trip["layovers"]]
    assert trip["duty_days"][0]["layover_after_duty"]["airport"] == "BOS"


def test_airline_specific_pay_and_tfp_map_without_cross_airline_rules():
    delta = canonical_trip_payload(fixture("delta"))
    american = canonical_trip_payload(fixture("american"))
    southwest = canonical_trip_payload(fixture("southwest"))
    generic = canonical_trip_payload(fixture("generic"))
    assert delta["pay_breakdown"] == {
        "trip_credit": "15:00", "edp": "0:30", "hol": "0:00", "sit": "0:15",
        "additional_pay": "0:45", "total_pay": "15:45",
        "raw_pay_tokens": ["30EDP", "15SIT"], "unresolved_pay_tokens": [],
    }
    assert american["pay_breakdown"]["trip_credit"] is None
    assert american["pay_breakdown"]["total_pay"] == "11:30"
    assert southwest["tfp"]["pairing_tfp"] == "6.50"
    assert southwest["pay_breakdown"]["total_pay"] is None
    assert generic["pay_breakdown"]["trip_credit"] == "05:00"


def test_scoring_and_api_shape_publish_one_canonical_model_with_classic_aliases():
    result = score_pairing(fixture("american"), {"prefer_operate": False})
    model = result["canonical_trip"]
    assert result["id"] == "pkg-american:A200"
    assert result["pairing"] == "A200"
    assert result["ordered_legs"] == model["ordered_legs"]
    assert result["ordered_operating_airports"] == model["ordered_operating_airports"]
    assert result["operating_cities"] == model["operating_cities"]
    assert result["route_map_airports"] == model["route_map_airports"]
    assert result["simplified_route"] == model["simplified_route"]
    assert result["ordered_events"] == model["ordered_events"]
    assert result["duty_days"] == model["duty_days"]
    assert result["layovers"] == model["layovers"]
    assert result["trip_length"] == model["trip_length_days"] == 3
    assert result["package_id"] == model["package_id"]


def test_pbs_and_month_pools_read_canonical_values_instead_of_poisoned_aliases():
    source = attach_canonical_trip(fixture("delta"))
    result = {
        **source,
        "pairing": "D100",
        "eligible": True,
        "match_class": "exact",
        "trip_length": 1,
        "cities": ["BAD"],
        "layovers": [{"city": "BAD"}],
        "total_pay": "00:01",
        "operating_dates": [],
        "duty_legs": [2, 1],
    }
    plan = build_navblue_layers(
        {"airline": "delta", "elite_cities": ["BOS"], "trip_length_priority": ["2"]},
        [result],
    )
    requests = [request for layer in plan["layers"] for request in layer["requests"]]
    assert next(request for request in requests if "Layover In BOS" in request["request"])["matching_trip_count"] == 1
    assert next(request for request in requests if "Pairing Length = 2" in request["request"])["matching_trip_count"] == 1
    month = build_month_plan({}, [result])
    assert month["pools"]["primary"]["average_trip_value"] == 15.75
    assert month["pools"]["primary"]["trip_ids"] == ["pkg-delta:D100"]


def test_classic_labs_and_exports_use_canonical_accessors():
    classic = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    labs = (ROOT / "app" / "static" / "labs.js").read_text(encoding="utf-8")
    reporting = (ROOT / "app" / "reporting.py").read_text(encoding="utf-8")
    assert "const normalized = tripModel(item)?.ordered_legs" in classic
    assert "function tripLayovers(item) { return tripModel(item)?.layovers" in classic
    assert "function tripPay(item) { return tripModel(item)?.pay_breakdown" in classic
    assert "tripLayovers(item).map" in labs
    assert "tripPayBreakdown(item)" in labs
    assert "model_from_item(item)" in reporting
