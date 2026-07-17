import json

from fastapi.testclient import TestClient

from app.main import app, db
from app.navblue import build_navblue_layers


def test_navblue_plan_builds_ordered_actual_requests():
    results = [
        {"cities": ["HNL"], "duty_legs": [1, 2, 1], "eligible": True},
        {"cities": ["DFW"], "duty_legs": [2, 1], "eligible": True},
    ]
    plan = build_navblue_layers({
        "required_days_off": ["8/11"],
        "penalty_cities": ["DFW"],
        "elite_cities": ["HNL"],
        "preferred_trip_lengths": ["2", "3"],
        "earliest_report_minutes": 480,
        "latest_release_minutes": 1080,
    }, results, "LAX AUG 2026.pdf")
    requests = [request["request"] for layer in plan["layers"] for request in layer["requests"]]
    assert requests == [
        "Prefer Off Date August 11, 2026",
        "Avoid Pairings If Layover In DFW",
        "Avoid Pairings If Pairing Check-In Time Before < 08:00",
        "Avoid Pairings If Pairing Check-Out Time After > 18:00",
        "Award Pairings If Layover In HNL",
        "Award Pairings If Pairing Length Between 2 Days And 3 Days",
        "Award Pairings",
    ]
    assert plan["layers"][0] == {"number": 1, "title": "Start Pairing Group", "requests": []}
    assert plan["layers"][1]["title"] == "Protect non-negotiables"
    assert plan["request_count"] == 7


def test_navblue_endpoint_uses_same_completed_classic_job(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    job_id = "navblue-shared-job"
    results = [{"cities": ["HNL"], "duty_legs": [1, 1], "pairing": "1001", "eligible": True}]
    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id,filename,status,progress,results_json,airline,profile_json) VALUES(?,?,?,?,?,?,?)",
                (job_id, "ATL AUG 2026.pdf", "complete", 100, json.dumps(results), "delta", "{}"),
            )
        response = client.post(f"/api/jobs/{job_id}/navblue-plan", json={"elite_cities": ["HNL"]})
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    assert response.status_code == 200
    assert response.json()["layers"][0] == {"number": 1, "title": "Start Pairing Group", "requests": []}
    assert response.json()["layers"][1]["requests"][0]["request"] == "Award Pairings If Layover In HNL"


def test_navblue_length_counts_elapsed_trip_days_not_duty_periods():
    plan = build_navblue_layers(
        {"preferred_trip_lengths": ["4"]},
        [{"trip_length": 4, "duty_legs": [3, 2, 3], "eligible": True}],
        "ATL320 AUG.pdf",
    )
    length_request = next(
        request
        for layer in plan["layers"]
        for request in layer["requests"]
        if "Pairing Length" in request["request"]
    )
    assert length_request["matching_trip_count"] == 1


def test_navblue_checklist_includes_entry_fields_order_and_manual_submission_warning():
    plan = build_navblue_layers(
        {"airline": "delta", "trip_length_priority": ["4", "3"], "must_avoid_redeye": True},
        [{"trip_length": 4, "redeye": "none", "cities": [], "eligible": True}],
        "ATL AUG 2026.pdf",
    )
    requests = [request for layer in plan["layers"] for request in layer["requests"]]
    assert requests[0]["interface_category"] == "Pairings"
    assert requests[0]["preference_type"] == "Avoid Pairings"
    assert [request["ordering"] for request in requests] == list(range(1, len(requests) + 1))
    assert plan["submission_mode"] == "pilot_review_only"
    assert any("does not submit" in warning for warning in plan["warnings"])


def test_navblue_ordered_trip_lengths_remain_in_user_order():
    plan = build_navblue_layers(
        {"trip_length_priority": ["6+", "5", "4", "3", "2", "1"]},
        [{"trip_length": 6, "cities": [], "eligible": True}, {"trip_length": 5, "cities": [], "eligible": True}],
    )
    length_requests = [
        request["values"][0]
        for layer in plan["layers"]
        for request in layer["requests"]
        if "Pairing Length" in request["request"]
    ]
    assert length_requests == ["6+", "5", "4", "3", "2", "1"]


def test_navblue_counts_only_stage_one_eligible_results():
    plan = build_navblue_layers(
        {"elite_cities": ["HNL"]},
        [
            {"cities": ["HNL"], "layovers": [{"city": "HNL"}], "eligible": True},
            {"cities": ["HNL"], "layovers": [{"city": "HNL"}], "eligible": False, "match_class": "near", "eligibility_violations": ["hard failure"]},
        ],
    )
    request = next(
        request
        for layer in plan["layers"]
        for request in layer["requests"]
        if "Layover In HNL" in request["request"]
    )
    assert request["matching_trip_count"] == 1
