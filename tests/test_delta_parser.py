from pathlib import Path
import fitz
from app.airports import is_valid_airport_code
from app.parsers import delta
from app.main import build_bid_synopsis, consolidate_pairings, detect_airports, filter_pairings_for_profile, score_pairing, sort_pdf_text_for_airline
from app.recommendations import evaluate_eligibility


ROTATION_5354 = """#5354  TU              EFFECTIVE AUG25 ONLY                  CHECK-IN AT 14.15
 DAY   FLIGHT T  DEPARTS   ARRIVES C BLK.  TURN BLK/MAX FDP/MAX PWA FDP/MAX
  A      2047    ATL 1515  ROC 1720  2.05   .55 320 M                         2
                 ROC 1815  ATL 2035  2.20  2.25                               2
         2372    ATL 2300  ROA 0027  1.27       319   10.12/12.00 10.12/11.30 2
          ROA 28.03/HAMPTON INN DTWN              5.52/ 9.00  .00CRD  5.52TL
  C      2818    ROA 0600  ATL 0725  1.25   .58                               2
         2632    ATL 0823  OMA 0942* 2.19           M  5.42/12.00  5.42/11.30 2
          OMA 18.18/HILTON OMAHA                  3.44/ 9.00  .00CRD  3.44TL
  D      1136    OMA 0530  ATL 0845  2.15  1.00                               2
         1444    ATL 0945  IAD 1133  1.48   .57     M                         2
                 IAD 1230  ATL 1420  1.50              8.50/12.00  8.50/11.30 2
                                                  5.53/ 9.00  .00CRD  5.53TL
                                             2.30MCD 2.45TRP  .00DPA  .16ADG
  TOTAL CREDIT 21.00TL  15.29BL    5.31CR   24.44FDP                TAFB  72.35
  TOTAL PAY    21:55TL    .13SIT    .42EDP    .00HOL    .00CARVE
"""

ROTATION_4497 = """#4497  TH              EFFECTIVE AUG27 ONLY                  CHECK-IN AT  6.25
 DAY   FLIGHT T  DEPARTS   ARRIVES C BLK.  TURN BLK/MAX FDP/MAX PWA FDP/MAX
  A      1380    ATL 0725  PIT 0905  1.40   .55 320 M                         2
                 PIT 1000  ATL 1147  1.47  1.23                               2
          360    ATL 1310  DCA 1453* 1.43       319    8.28/12.00  8.28/11.30 2
          DCA 13.42/GAYLORD NATIONAL               5.10/ 9.00  .00CRD  5.10TL
  B      1193    DCA 0605  MSP 0740  2.35  1.25 320 M                         2
         2482    MSP 0905  YVR 1050  3.45          8.45/12.00  8.45/11.30 2
          YVR 17.40/PINNACLE HOTEL                6.20/ 9.00  .00CRD  6.20TL
  C      2875    YVR 0600  MSP 1121  3.21  1.39                               2
          402    MSP 1300  RSW 1723* 3.23       321    9.23/14.00  9.23/13.00 2
          RSW 11.53/LUMINARY HOTEL RSW            6.44/ 9.00  .00CRD  6.44TL
  D      1361    RSW 0646  ATL 0835  1.49            M  2.49/12.00  2.49/11.30 2
                                                  1.49/ 9.00  .11DPM  2.00TL
  TOTAL CREDIT 21.20TL  20.03BL    1.17CR   29.25FDP                TAFB  74.40
  TOTAL PAY    21:20TL    .00SIT    .00EDP    .00HOL    .00CARVE
"""

R523 = """#R523  MO              EFFECTIVE AUG01 ONLY                  CHECK-IN AT 08.00
 DAY   FLIGHT T  DEPARTS   ARRIVES C BLK.  TURN BLK/MAX FDP/MAX PWA FDP/MAX
  A      200    ATL 1000  JNB 2200  9.00   .55 350 M                         2
  B      201    JNB 2300  ATL 1100  9.00       350   10.00/12.00 10.00/11.30 2
  TOTAL CREDIT 18.00TL  18.00BL    .00CR   20.00FDP                TAFB  28.00
"""

A023 = """ATL A330 SEPTEMBER 2026 BID PACKAGE
MASTER PAIRINGS
#A023  MO              EFFECTIVE SEP08 ONLY                  CHECK-IN AT 14.00
 DAY   FLIGHT T POS DEPARTS   ARRIVES C BLK.  TURN BLK/MAX FDP/MAX PWA FDP/MAX
  A       82    ATL 1600  ATH 0830  9.30       330 M                         2
          ATH 24.00/ATHENS HOTEL                    5.00/ 9.00  .00CRD  5.00TL
  B       83    ATH 1000  ATL 1500 10.00       330  11.00/12.00 10.30/11.30 2
  TOTAL CREDIT 19.30TL  19.30BL    .00CR   21.30FDP                TAFB  48.00
  TOTAL PAY    20:00TL    .30SIT    .00EDP    .00HOL
"""


def test_delta_a023_operating_airports_come_only_from_validated_leg_rows(monkeypatch):
    monkeypatch.setenv("PARSER_DEBUG_ENABLED", "true")
    pairing = delta.parse(A023)[0]
    result = score_pairing(pairing, {"prefer_operate": False})
    model = result["canonical_trip"]

    assert [
        (leg["departure"], leg["arrival"])
        for leg in pairing["legs"]
    ] == [("ATL", "ATH"), ("ATH", "ATL")]
    assert result["ordered_operating_airports"] == model["ordered_operating_airports"] == ["ATL", "ATH", "ATL"]
    assert result["operating_cities"] == model["operating_cities"] == ["ATL", "ATH"]
    assert result["touched_cities"] == ["ATL", "ATH"]
    assert result["route_map_airports"] == model["route_map_airports"] == ["ATL", "ATH"]
    assert result["simplified_route"] == model["simplified_route"] == "ATL–ATH–ATL"
    assert result["international"] is True
    assert [(leg["origin"], leg["destination"]) for leg in model["ordered_legs"]] == [("ATL", "ATH"), ("ATH", "ATL")]
    assert [layover["airport"] for layover in model["layovers"]] == ["ATH"]
    assert not ({"POS", "BLK", "PWA", "PAY"} & set(result["operating_cities"]))
    assert not ({"POS", "BLK", "PWA", "PAY"} & {layover["airport"] for layover in model["layovers"]})

    # Metadata membership cannot override the structural source-row context.
    assert all(is_valid_airport_code(token) for token in ("POS", "BLK", "PWA", "PAY"))
    diagnostics = [entry for entry in delta.get_diagnostics() if entry.get("token")]
    for token in ("POS", "BLK", "PWA", "PAY"):
        rejected = next(entry for entry in diagnostics if entry["token"] == token and entry["result"] == "REJECTED")
        assert rejected["reason"] == "not_in_validated_flight_leg_origin_destination_position"
    assert next(entry for entry in diagnostics if entry["token"] == "POS")["context"] == "header field"
    assert next(entry for entry in diagnostics if entry["token"] == "BLK")["context"] == "column heading"
    assert next(entry for entry in diagnostics if entry["token"] == "PWA")["context"] == "duty-limit field"
    assert next(entry for entry in diagnostics if entry["token"] == "PAY")["context"] == "pay heading"
    assert any(entry["token"] == "ATL" and entry["result"] == "ACCEPTED" for entry in diagnostics)
    assert any(entry["token"] == "ATH" and entry["result"] == "ACCEPTED" for entry in diagnostics)

    provenance = pairing["airport_event_provenance"]
    assert model["raw_source_fields"]["airport_event_provenance"] == provenance
    assert [(event["token"], event["role"]) for event in provenance] == [
        ("ATL", "origin"), ("ATH", "destination"), ("ATH", "origin"), ("ATL", "destination")
    ]
    assert all(event["source_page"] == 1 and event["source_line"] for event in provenance)
    assert all(event["leg_index"] in {1, 2} and event["duty_day_index"] in {1, 2} for event in provenance)


def test_destination_matching_and_frontends_cannot_reintroduce_delta_header_tokens():
    result = score_pairing(delta.parse(A023)[0], {"prefer_operate": False})
    decision = evaluate_eligibility(result, {"must_avoid_destinations": ["POS"]})
    assert decision["eligible"] is True
    assert detect_airports("POS BLK PWA ATL ATH PAY", {"airline": "delta", "legs": []}) == []

    root = Path(__file__).resolve().parents[1]
    classic = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    labs = (root / "app" / "static" / "labs.js").read_text(encoding="utf-8")
    flight_deck = (root / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")
    assert "tripModel(item)?.ordered_legs" in classic
    assert "tripModel(item)?.operating_cities" in classic
    assert "tripModel(item)?.operating_cities" in labs
    assert "tripModel(item).simplified_route" in flight_deck
    for script in (classic, labs, flight_deck):
        assert "source_text.match" not in script
        assert "original_display.match" not in script


def test_legitimate_delta_flight_to_pos_remains_valid():
    source = A023.replace("ATL 1600  ATH 0830", "ATL 1600  POS 2030").replace(
        "ATH 24.00/ATHENS HOTEL", "POS 24.00/PORT OF SPAIN HOTEL"
    ).replace("ATH 1000  ATL 1500", "POS 1000  ATL 1500")
    result = score_pairing(delta.parse(source)[0], {"prefer_operate": False})
    assert result["ordered_operating_airports"] == ["ATL", "POS", "ATL"]
    assert result["operating_cities"] == ["ATL", "POS"]
    assert result["simplified_route"] == "ATL–POS–ATL"
    assert [layover["airport"] for layover in result["canonical_trip"]["layovers"]] == ["POS"]


def test_instructional_r523_is_rejected_but_atl_a350_inventory_occurrence_is_accepted(monkeypatch):
    monkeypatch.setenv("PARSER_DEBUG_ENABLED", "true")
    instructional_package = f"""<<<CREWBIDIQ_PAGE:1>>>
DTW A320 BID PACKAGE
350 FOUR PILOT OPERATIONS & FRMS
Below is an example published in the 350 bid package for reference.
{R523}
"""
    assert delta.parse(instructional_package) == []
    rejected_diagnostic = delta.get_diagnostics()[0]

    atl_package = f"""<<<CREWBIDIQ_PAGE:1>>>
ATL A350 BID PACKAGE
MASTER PAIRINGS
{R523}
"""
    parsed = delta.parse(atl_package)
    accepted = parsed[0]
    assert accepted["package_base"] == "ATL"
    assert accepted["package_fleet"] == "A350"
    assert accepted["bidable_inventory_confirmed"] is True
    assert accepted["page_classification"] == "BIDABLE_INVENTORY"
    assert accepted["source_page"] == 1
    assert accepted["inventory_key"] == f'{accepted["package_id"]}:R523'

    assert rejected_diagnostic == {
        "candidate_rotation": "R523", "source_page": 1,
        "source_heading": "DTW A320 BID PACKAGE", "page_classification": "EXAMPLE",
        "result": "REJECTED", "rejection_reason": "instructional_example_outside_bidable_inventory",
        "confidence": rejected_diagnostic["confidence"],
    }
    assert delta.get_diagnostics()[0]["result"] == "ACCEPTED"


def test_rotation_shaped_example_outside_inventory_is_rejected():
    assert delta.parse(f"ATL A350 TRAINING EXAMPLE\nnot for bidding\n{R523}") == []


def test_package_scoped_rotation_ids_do_not_suppress_another_package():
    first = {"id": "R523", "package_id": "dtw-a320", "bidable_inventory_confirmed": False, "legs": []}
    second = {"id": "R523", "package_id": "atl-a350", "bidable_inventory_confirmed": True, "legs": [{"departure": "ATL"}]}
    consolidated = consolidate_pairings([first, second])
    assert len(consolidated) == 2
    assert filter_pairings_for_profile(consolidated, {}) == [consolidated[1]]


def test_all_shared_inventory_consumers_only_receive_confirmed_records():
    valid = delta.parse(ROTATION_5354)[0]
    rejected = {**valid, "id": "R523", "rotation_number": "R523", "inventory_key": f'{valid["package_id"]}:R523', "bidable_inventory_confirmed": False}
    accepted = filter_pairings_for_profile([valid, rejected], {})
    assert [row["id"] for row in accepted] == ["5354"]
    # Classic, Labs, Flight Deck/PBS and exports are all generated from this scored result list.
    results = [score_pairing(row, {}) for row in accepted]
    assert all(result["bidable_inventory_confirmed"] for result in results)
    assert all("JNB" not in result["cities"] for result in results)
    assert build_bid_synopsis([valid, rejected])["total"] == 1


def test_delta_rotation_5354_preserves_its_own_column_and_pay_data():
    pairing = delta.parse(ROTATION_5354)[0]
    assert pairing["credit"] == "21.00"
    assert pairing["tafb"] == "72.35"
    assert pairing["total_pay"] == "21:55"
    assert pairing["additional_pay"] == "0:55"
    assert pairing["pay_components"] == {"SIT": "0:13", "EDP": "0:42", "HOL": "0:00"}
    assert [layover["city"] for layover in pairing["layovers"]] == ["ROA", "OMA"]

    result = score_pairing(pairing, {
        "elite_cities": ["OMA"],
        "preferred_trip_lengths": ["4"],
        "prefer_operate": False,
    })
    assert result["start_airport"] == "ATL"
    assert result["trip_length"] == 4
    assert result["duty_legs"] == [3, 2, 3]
    assert result["equipment_codes"] == ["320", "319"]
    assert result["cities"] == ["ROA", "OMA"]
    assert "Matches your preferred 4-day trip length" in result["reasons"]
    assert all("5-day" not in reason for reason in result["reasons"])


def test_delta_operating_date_validator_rejects_pay_tokens_and_wrong_month():
    context = (8, 2026)
    assert delta.is_valid_operating_date_token("AUG25", context)
    assert delta.is_valid_operating_date_token("25AUG2026", context)
    assert delta.is_valid_operating_date_token("2026-08-25", context)
    for token in ("CRD", "DHD", "MCD", "TRP", "DPA", "ADG", "EDP", "SIT", "HOL", "43EDP", "02SIT", "00HOL", "23DHD", "52TRP", "00CRD", "SEP25", "AUG32"):
        assert not delta.is_valid_operating_date_token(token, context)


def test_valid_delta_operating_dates_are_normalized_with_bid_month_context():
    pairing = delta.parse("AUGUST 2026\n" + ROTATION_5354)[0]
    assert pairing["operating_dates"] == ["2026-08-25"]
    assert pairing["effective"] == "2026-08-25"
    assert pairing["operating_dates_status"] == "validated"
    result = score_pairing(pairing, {})
    assert result["operating_dates"] == ["2026-08-25"]


def test_pay_tokens_after_effective_column_never_become_operating_dates():
    source = ("AUGUST 2026\n" + ROTATION_5354).replace(
        "EFFECTIVE AUG25 ONLY                  CHECK-IN AT 14.15",
        "EFFECTIVE\n43EDP 02SIT 00HOL 23DHD 52TRP 00CRD\nCHECK-IN AT 14.15",
    )
    pairing = delta.parse(source)[0]
    result = score_pairing(pairing, {})
    assert pairing["operating_dates"] == []
    assert pairing["operating_dates_status"] == "unavailable"
    assert result["operating_dates"] == []
    assert result["operating_dates_status"] == "unavailable"
    assert not ({"43EDP", "02SIT", "00HOL", "23DHD", "52TRP", "00CRD"} & set(result["operating_dates"]))


def test_classic_labs_and_flight_deck_share_delta_normalized_pay_and_dates():
    pairing = delta.parse("AUGUST 2026\n" + ROTATION_5354)[0]
    result = score_pairing(pairing, {})
    for field in ("operating_dates", "trip_credit", "edp", "hol", "sit", "additional_pay", "total_pay", "raw_pay_tokens", "unresolved_pay_tokens"):
        assert field in result
    root = Path(__file__).resolve().parents[1]
    classic = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    labs = (root / "app" / "static" / "labs.js").read_text(encoding="utf-8")
    assert "item.unresolved_pay_tokens" in classic
    assert "if (!(item.operating_dates || item.dates || []).length)" in classic
    assert "item.total_pay" in labs


def test_delta_and_auto_pdf_extraction_preserve_native_column_order():
    assert sort_pdf_text_for_airline("delta") is False
    assert sort_pdf_text_for_airline("auto") is False
    assert sort_pdf_text_for_airline("generic") is True


def test_delta_rotation_4497_uses_its_own_start_length_layovers_and_pay():
    pairing = delta.parse(ROTATION_4497)[0]
    result = score_pairing(pairing, {"preferred_trip_lengths": ["4"], "prefer_operate": False})
    assert result["start_airport"] == "ATL"
    assert result["trip_length"] == 4
    assert result["duty_legs"] == [3, 2, 2, 1]
    assert result["cities"] == ["DCA", "YVR", "RSW"]
    assert result["equipment_codes"] == ["320", "319", "321"]
    assert result["tafb"] == "74.40"
    assert result["trip_credit"] == "21.20"
    assert result["additional_pay"] == "0:00"
    assert result["total_pay"] == "21:20"
    assert "Matches your preferred 4-day trip length" in result["reasons"]


def test_delta_august_package():
    path = Path("/mnt/data/DTW320 AUG 2026.pdf")
    if not path.exists():
        return
    doc = fitz.open(path)
    text = "\n".join(page.get_text("text", sort=True) for page in doc)
    parsed = delta.parse(text)
    ids = {p["id"] for p in parsed}
    assert "4913" in ids
    assert len(parsed) > 200
    assert sum(bool(p["legs"]) for p in parsed) > 150
