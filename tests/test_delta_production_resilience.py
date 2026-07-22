from __future__ import annotations

import json
from pathlib import Path

from app import main
from app.canonical import canonical_trip_payload
from app.geography import available_layover_options, resolve_layover_preference
from app.navblue import build_navblue_layers
from app.parsers import delta


FIXTURE = Path(__file__).parent / "fixtures" / "delta_august_sanitized.txt"


def parsed_fixture() -> list[dict]:
    return delta.parse(FIXTURE.read_text(encoding="utf-8"))


def test_delta_accepts_exact_four_character_rotation_ids_and_derives_local_release():
    rows = parsed_fixture()
    by_id = {row["id"]: row for row in rows}

    assert set(by_id) == {"4426", "L599", "B123", "U123"}
    assert by_id["4426"]["first_report"] == "04:35"
    assert by_id["4426"]["tafb_minutes"] == 38 * 60 + 18
    assert by_id["4426"]["final_release"] == "18:53"
    assert by_id["4426"]["release_day_offset"] == 1
    assert by_id["L599"]["source_region_code"] == "L"
    assert by_id["L599"]["source_region"] == "LATIN"
    assert by_id["B123"]["source_region"] == "ATLANTIC"
    assert by_id["U123"]["source_region"] == "SOUTH_AMERICA"
    assert by_id["L599"]["operating_dates"] == ["2026-08-04", "2026-08-11"]


def test_delta_canonical_report_release_and_layover_provenance_are_explicit():
    row = next(item for item in parsed_fixture() if item["id"] == "4426")
    canonical = canonical_trip_payload(row)

    assert canonical["report"]["local_time"] == "04:35"
    assert canonical["report"]["provenance"] == "printed_check_in"
    assert canonical["release"]["local_time"] == "18:53"
    assert canonical["release"]["day_offset"] == 1
    assert canonical["release"]["provenance"] == "derived_from_report_plus_tafb"
    assert canonical["layovers"][0]["arrival_airport"] == "CDG"
    assert canonical["layovers"][0]["layover_market"] == "CDG"
    assert canonical["layovers"][0]["city"] == "Paris"
    assert canonical["layovers"][0]["country_code"] == "FR"
    assert canonical["layovers"][0]["theater"] == "EUROPE"
    assert canonical["layovers"][0]["source"] == "printed_layover_line"


def test_package_layover_options_and_semantic_resolution_use_only_available_airports():
    rows = parsed_fixture()
    options = available_layover_options(rows)
    available = [item["airport"] for item in options["airports"]]
    cdg = next(item for item in options["airports"] if item["airport"] == "CDG")

    assert resolve_layover_preference("EUROPE", available)["airports"] == ["CDG", "LHR"]
    assert resolve_layover_preference("RIO", available)["airports"] == ["GIG"]
    assert cdg["layover_market"] == "CDG"
    assert cdg["country_name"] == "France"
    assert "PARIS" in cdg["aliases"]
    assert {group["code"] for group in options["theaters"]} >= {"EUROPE", "SOUTH_AMERICA"}


def test_navblue_expands_europe_to_available_airports_and_never_emits_literal_region():
    results = [
        {"eligible": True, "canonical_trip": {"layovers": [{"airport": "CDG"}]}},
        {"eligible": True, "canonical_trip": {"layovers": [{"airport": "LHR"}]}},
        {"eligible": True, "canonical_trip": {"layovers": [{"airport": "GIG"}]}},
    ]
    payload = build_navblue_layers({"airline": "delta", "secondary_cities": ["EUROPE"]}, results)
    requests = [request for layer in payload["layers"] for request in layer["requests"]]
    region_request = next(request for request in requests if request["values"] == ["CDG", "LHR"])

    assert "EUROPE" not in region_request["request"]
    assert region_request["request"].endswith("CDG OR LHR")
    assert region_request["matching_trip_count"] == 2
    assert "Resolved Europe to CDG OR LHR" in region_request["explanation"]


def test_navblue_skips_zero_trip_semantic_groups_with_a_warning():
    results = [{"eligible": True, "canonical_trip": {"layovers": [{"airport": "ATL"}]}}]
    payload = build_navblue_layers({"airline": "delta", "secondary_cities": ["EUROPE"]}, results)
    requests = [request for layer in payload["layers"] for request in layer["requests"]]

    assert all("EUROPE" not in request["request"] for request in requests)
    assert any("No Europe layovers" in warning for warning in payload["warnings"])


def test_storage_compaction_keeps_one_canonical_source_and_removes_duplicate_views():
    result = {
        "id": "package:4426",
        "package_id": "package",
        "original_display": "duplicate",
        "legs": [{"departure": "ATL"}],
        "ordered_legs": [{"origin": "ATL"}],
        "canonical_trip": {
            "source_text": "authoritative source",
            "ordered_legs": [{"origin": "ATL"}],
            "raw_source_fields": {"source_region_code": "L", "airport_event_provenance": ["large"]},
        },
    }
    compact = main.compact_result_for_storage(result)

    assert "original_display" not in compact
    assert "legs" not in compact
    assert "ordered_legs" not in compact
    assert compact["canonical_trip"]["source_text"] == "authoritative source"
    assert compact["canonical_trip"]["raw_source_fields"] == {"source_region_code": "L"}


def test_delta_batch_checkpoint_round_trips_pairings_warnings_and_resume_state():
    cache_key = "test:delta-batch-checkpoint"
    main.init_db()
    with main.db() as connection:
        connection.execute("DELETE FROM parse_batch_cache WHERE cache_key=?", (cache_key,))
    try:
        main._store_delta_batch(
            cache_key, 3, 21, 30,
            [{"id": "L599", "bidable_inventory_confirmed": True}],
            [{"code": "DELTA_ROTATION_QUARANTINED", "rotation": "L598"}],
            True,
        )
        row = main._cached_delta_batches(cache_key)[3]
        pairings, warnings = main._decode_delta_batch(row)

        assert (row["page_start"], row["page_end"]) == (21, 30)
        assert row["last_pairing_id"] == "L599"
        assert row["inventory_open_after"] == 1
        assert pairings == [{"id": "L599", "bidable_inventory_confirmed": True}]
        assert warnings[0]["rotation"] == "L598"
    finally:
        with main.db() as connection:
            connection.execute("DELETE FROM parse_batch_cache WHERE cache_key=?", (cache_key,))


def test_delta_batch_failure_retries_pages_and_quarantines_only_the_bad_page(tmp_path, monkeypatch):
    source = FIXTURE.read_text(encoding="utf-8")
    cover = source.split("<<<CREWBIDIQ_PAGE:2>>>", 1)[0]
    inventory = source.split("<<<CREWBIDIQ_PAGE:2>>>", 1)[1]
    first_pairing, later_pairings = inventory.split("#L599", 1)
    page_texts = [cover, first_pairing, f"MASTER PAIRINGS\n#L599{later_pairings}"]
    pdf_path = tmp_path / "delta.pdf"
    document = main.fitz.open()
    for page_text in page_texts:
        page = document.new_page()
        page.insert_textbox(page.rect + (36, 36, -36, -36), page_text, fontsize=7)
    document.save(pdf_path)
    document.close()

    cache_key = "test:delta-page-retry"
    job_id = "test-delta-page-retry"
    main.init_db()
    with main.db() as connection:
        connection.execute("DELETE FROM parse_batch_cache WHERE cache_key=?", (cache_key,))
        connection.execute(
            """INSERT OR REPLACE INTO jobs
               (id,filename,status,progress,package_id,state,current_stage,airline)
               VALUES(?,?,?,?,?,?,?,?)""",
            (job_id, "delta.pdf", "processing", 0, "test-package", "parsing", "extracting_pages", "delta"),
        )
    original = delta.parse_page_batch

    def fail_batch_then_one_page(page_batch, **kwargs):
        if len(page_batch) > 1:
            raise RuntimeError("batch ordering failure")
        if page_batch[0][0] == 2:
            raise RuntimeError("malformed page")
        return original(page_batch, **kwargs)

    monkeypatch.setattr(delta, "parse_page_batch", fail_batch_then_one_page)
    try:
        rows, _, warnings = main.parse_delta_pdf_bounded(pdf_path, job_id, cache_key, batch_size=10)
        assert {row["id"] for row in rows} == {"L599", "B123", "U123"}
        assert any(warning["code"] == "DELTA_BATCH_RETRIED_BY_PAGE" for warning in warnings)
        assert any(warning["code"] == "DELTA_PAGE_PARSE_QUARANTINED" and warning["source_page"] == 2 for warning in warnings)
        job = main.get_job(job_id)
        assert job["warning_count"] == len(warnings)
        assert job["state"] == "parsing"
        assert job["records_processed"] == len(rows)
    finally:
        with main.db() as connection:
            connection.execute("DELETE FROM parse_batch_cache WHERE cache_key=?", (cache_key,))
            connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def test_labs_map_declares_all_five_dark_and_light_density_bins():
    source = (Path(main.BASE_DIR) / "app" / "static" / "labs.js").read_text(encoding="utf-8")
    for color in ("#20344A", "#27516A", "#2F6E86", "#3B8AA0", "#62AFC0", "#D9E7EC", "#B7D5DE", "#8ABDCB", "#569BAE", "#256D7D"):
        assert color in source
    assert "published trip records" in source
