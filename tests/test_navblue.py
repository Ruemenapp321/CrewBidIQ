import json

from fastapi.testclient import TestClient

from app.main import app, db
from app.navblue import build_navblue_layers


def test_navblue_plan_builds_ordered_actual_requests():
    results = [
        {"cities": ["HNL"], "duty_legs": [1, 2, 1]},
        {"cities": ["DFW"], "duty_legs": [2, 1]},
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
    results = [{"cities": ["HNL"], "duty_legs": [1, 1], "pairing": "1001"}]
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
