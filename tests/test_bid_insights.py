import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.airports import coterminal_group_for_airport, expand_airports
from app.main import (
    app,
    build_bid_synopsis,
    consolidate_pairings,
    db,
    filter_pairings_for_profile,
    matching_dates,
    score_pairing,
    sort_results,
)


def pairing(pairing_id="4461", start="JFK", fleet="320", date="2026-08-11"):
    return {
        "id": pairing_id,
        "block": f"#{pairing_id}",
        "airline": "delta",
        "parser": "delta_test",
        "fleet": fleet,
        "effective": date,
        "credit": "10:00",
        "tafb": "28:00",
        "confidence": 1.0,
        "legs": [
            {"day": "A", "departure": start, "arrival": "BOS", "deadhead": False},
            {"day": "B", "departure": "BOS", "arrival": start, "deadhead": False},
        ],
        "layovers": [{"city": "BOS", "duration": "16:00"}],
    }


def test_delta_nyc_coterminal_group_expands_to_three_airports():
    assert expand_airports("delta", ["NYC"]) == ["JFK", "LGA", "EWR"]
    assert coterminal_group_for_airport("delta", "EWR") == "NYC"
    assert expand_airports("american", ["NYC"]) == ["NYC"]


def test_start_airport_preferences_apply_to_coterminal_group_and_avoid_wins():
    preferred = score_pairing(pairing(start="LGA"), {"preferred_start_airports": ["NYC"], "prefer_operate": False})
    avoided = score_pairing(pairing(start="LGA"), {
        "preferred_start_airports": ["NYC"], "avoid_start_airports": ["LGA"], "prefer_operate": False
    })
    assert preferred["score"] == 18
    assert any("preferred airport LGA" in reason for reason in preferred["reasons"])
    assert avoided["score"] == -35
    assert any("prefer to avoid" in reason for reason in avoided["reasons"])


def test_american_bid_fleet_is_a_hard_filter():
    offered = [pairing("1001", fleet="320"), pairing("1002", fleet="737")]
    assert [item["id"] for item in filter_pairings_for_profile(offered, {"bid_fleets": ["320"]})] == ["1001"]
    assert filter_pairings_for_profile(offered, {}) == offered


def test_synopsis_summarizes_redeyes_deadheads_lengths_starts_and_fleets():
    normal = pairing("1001", fleet="320")
    redeye = pairing("1002", start="LGA", fleet="737")
    redeye["block"] += " REDEYE"
    redeye["legs"][0]["deadhead"] = True
    synopsis = build_bid_synopsis([normal, redeye])
    assert synopsis["total"] == 2
    assert synopsis["redeye"] == {"count": 1, "percent": 50.0}
    assert synopsis["deadhead"] == {"count": 1, "percent": 50.0}
    assert synopsis["overnight_city_count"] == 1
    assert {row["airport"] for row in synopsis["start_airports"]} == {"JFK", "LGA"}
    assert {row["fleet"] for row in synopsis["fleets"]} == {"320", "737"}


def test_duplicate_rotation_keeps_rich_record_and_incomplete_cannot_rank_first():
    empty = {"id": "4461", "block": "#4461", "legs": [], "parser": "delta_test"}
    rich = pairing("4461")
    consolidated = consolidate_pairings([empty, rich])
    assert len(consolidated) == 1
    assert len(consolidated[0]["legs"]) == 2
    assert len(consolidated[0]["parser_candidates"]) == 2

    incomplete_result = score_pairing(empty, {})
    complete_result = score_pairing(rich, {"penalty_cities": ["BOS"], "weights": {"penalty": 5000}})
    results = [incomplete_result, complete_result]
    sort_results(results)
    assert results[0]["data_quality"] == "complete"
    assert results[1]["pairing"] == "4461"


def test_days_off_accept_month_day_without_year_and_preserve_full_year_rules():
    assert matching_dates(["2026-08-11", "2026-08-18"], {"8/11", "08-18"}) == ["2026-08-11", "2026-08-18"]
    assert matching_dates(["2026-08-11"], {"2025-08-11"}) == []
    result = score_pairing(pairing(date="2026-08-11"), {"required_days_off": ["8/11"]})
    assert result["match_level"] == "low"
    assert result["calendar_conflicts"] == ["Required off: 2026-08-11"]


def test_highest_priority_hawaii_layover_outranks_a_no_signal_sequence():
    hawaii = pairing("7396", start="LAX")
    hawaii["block"] = "#7396 REDEYE 2300 0200"
    hawaii["layovers"] = [{"city": "LIH", "duration": "22:24"}]
    hawaii["legs"][0]["arrival"] = "LIH"
    hawaii["legs"][1]["departure"] = "LIH"
    no_match = pairing("7399", start="LAX")
    no_match["layovers"] = [{"city": "JFK", "duration": "22:24"}]
    profile = {
        "elite_cities": ["HNL", "OGG", "LIH"],
        "prefer_operate": False,
        "allow_productive_redeye": False,
        "earliest_report_minutes": None,
        "latest_release_minutes": None,
    }
    results = [score_pairing(no_match, profile), score_pairing(hawaii, profile)]
    sort_results(results)
    assert results[0]["pairing"] == "7396"
    assert results[0]["cities"] == ["LIH"]
    assert results[0]["match_level"] == "excellent"
    assert any("highest-priority overnight" in reason for reason in results[0]["reasons"])


def test_diagnostic_download_is_bounded_and_includes_ranking_preferences():
    job_id = "diagnostic-bid-insights"
    selected = pairing("4461")
    selected["parser_candidates"] = [{"legs": 0, "block": "#4461"}, {"legs": 2, "block": "#4461 full"}]
    source = {"kind": "pairings", "pairings": [pairing("4460"), selected, pairing("4462")]}
    result = score_pairing(selected, {})
    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id,filename,context,status,progress,results_json,airline,profile_json,source_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, "ATL_320_AUG.pdf", "delta", "complete", 100, json.dumps([result]), "delta", json.dumps({"elite_cities": ["BOS"]}), json.dumps(source)),
            )
        response = client.post(
            f"/api/jobs/{job_id}/diagnostic.json",
            data={"pairing_id": "4461", "category": "wrong_ranking", "notes": "Missing duty details"},
        )
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    assert response.status_code == 200
    assert "crewbidiq-diagnostic-4461.json" in response.headers["content-disposition"]
    bundle = response.json()
    assert bundle["schema"] == "crewbidiq.parser-diagnostic.v1"
    assert bundle["preferences"] == {"elite_cities": ["BOS"]}
    assert set(bundle["neighboring_source_context"]) == {"previous", "selected", "next"}
    assert len(bundle["parser_candidates"]) == 2
    assert "complete bid package" in bundle["privacy"]


def test_diagnostic_ui_uses_native_browser_download():
    script = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "form.target = '_blank'" in script
    assert "form.submit()" in script
    assert "URL.createObjectURL(blob)" not in script
