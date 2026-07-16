import json

from fastapi.testclient import TestClient

from app.main import app, db


def pairing(pairing_id: str, city: str) -> dict:
    return {
        "id": pairing_id,
        "block": f"#{pairing_id}\nATL 0800 {city} 1000",
        "legs": [
            {"day": "A", "deadhead": False, "departure": "ATL", "departure_time": "0800", "arrival": city, "arrival_time": "1000", "aircraft": "321"},
            {"day": "B", "deadhead": False, "departure": city, "departure_time": "0800", "arrival": "ATL", "arrival_time": "1000", "aircraft": "321"},
        ],
        "layovers": [{"city": city, "duration": "16:00", "hotel": None}],
        "credit": "10:00", "tafb": "26:00", "parser": "delta_test", "confidence": 1.0,
    }


def test_preferences_rerank_stored_pairings_without_reupload():
    job_id = "rescore-test"
    source = {"kind": "pairings", "pairings": [pairing("1001", "BOS"), pairing("1002", "SAN")]}
    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id,filename,context,status,progress,results_json,airline,profile_json,source_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, "August.pdf", "delta", "complete", 100, "[]", "delta", "{}", json.dumps(source)),
            )
        bos = client.post(f"/api/jobs/{job_id}/rescore", data={"profile_json": json.dumps({"elite_cities": ["BOS"], "weights": {"elite": 50}})})
        san = client.post(f"/api/jobs/{job_id}/rescore", data={"profile_json": json.dumps({"elite_cities": ["SAN"], "weights": {"elite": 50}})})
    assert bos.status_code == 200
    assert san.status_code == 200
    assert bos.json()["results"][0]["pairing"] == "1001"
    assert san.json()["results"][0]["pairing"] == "1002"
    assert "without parsing" in san.json()["message"]


def test_old_analysis_explains_that_one_reupload_is_required():
    job_id = "pre-rescore-test"
    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id,filename,context,status,progress,results_json,airline,profile_json,source_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, "Old.pdf", "delta", "complete", 100, "[]", "delta", "{}", None),
            )
        response = client.post(f"/api/jobs/{job_id}/rescore", data={"profile_json": "{}"})
    assert response.status_code == 409
    assert "Upload the bid package one more time" in response.json()["detail"]


def test_five_day_only_preference_never_explains_a_three_day_match():
    job_id = "five-day-rescore-test"
    three_day = pairing("3003", "OMA")
    three_day["legs"].append({"day": "C", "deadhead": False, "departure": "ATL", "departure_time": "0900", "arrival": "OMA", "arrival_time": "1100", "aircraft": "321"})
    five_day = pairing("5005", "BOS")
    for day in ("C", "D", "E"):
        five_day["legs"].append({"day": day, "deadhead": False, "departure": "ATL", "departure_time": "0900", "arrival": "BOS", "arrival_time": "1100", "aircraft": "321"})
    source = {"kind": "pairings", "pairings": [three_day, five_day]}
    profile = {"preferred_trip_lengths": ["5"], "elite_cities": ["OMA", "DSM"], "prefer_operate": False}
    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id,filename,context,status,progress,results_json,airline,profile_json,source_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, "ATL320 AUG.pdf", "delta", "complete", 100, "[]", "delta", "{}", json.dumps(source)),
            )
        response = client.post(f"/api/jobs/{job_id}/rescore", data={"profile_json": json.dumps(profile)})
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    assert response.status_code == 200
    results = {result["pairing"]: result for result in response.json()["results"]}
    assert response.json()["results"][0]["pairing"] == "5005"
    assert "Matches your preferred 5-day trip length" in results["5005"]["reasons"]
    assert results["5005"]["trip_length_match"] is True
    assert results["3003"]["trip_length_match"] is False
    assert all("preferred 3-day" not in reason for result in results.values() for reason in result["reasons"])
