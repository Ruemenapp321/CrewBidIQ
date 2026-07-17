import json
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from app.main import app, db


ROOT = Path(__file__).resolve().parents[1]


def pdf_bytes(rotation: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (40, 50),
        f"MASTER PAIRINGS\n#{rotation}\nTOTAL CREDIT 10.00TL\nTAFB 24.00\nCHECK-IN AT 08.00",
    )
    payload = document.tobytes()
    document.close()
    return payload


def upload(client: TestClient, rotation: str, filename: str = "ATL320 AUG 2026.pdf") -> dict:
    response = client.post(
        "/api/jobs",
        data={"airline": "delta", "context": "classic", "profile_json": "{}"},
        files={"file": (filename, pdf_bytes(rotation), "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    status = client.get(f'/api/jobs/{body["job_id"]}')
    assert status.status_code == 200
    assert status.json()["status"] == "complete"
    return status.json()


def cleanup(*job_ids: str) -> None:
    with db() as conn:
        conn.executemany("DELETE FROM jobs WHERE id=?", [(job_id,) for job_id in job_ids])


def test_replacement_and_refresh_never_return_package_a_records(monkeypatch):
    monkeypatch.setenv("PACKAGE_DEBUG_ENABLED", "true")
    with TestClient(app) as client:
        package_a = upload(client, "1001")
        package_b = upload(client, "2002", "DTW320 SEP 2026.pdf")
        try:
            assert package_a["package_id"] != package_b["package_id"]
            assert {result["pairing"] for result in package_a["results"]} == {"1001"}
            assert {result["pairing"] for result in package_b["results"]} == {"2002"}
            assert {result["package_id"] for result in package_b["results"]} == {package_b["package_id"]}

            # Browser refresh reloads one job/package and cannot merge the prior response.
            refreshed = client.get(f'/api/jobs/{package_b["job_id"]}').json()
            assert refreshed["package_id"] == package_b["package_id"]
            assert {result["pairing"] for result in refreshed["results"]} == {"2002"}
            diagnostics = refreshed["package_diagnostics"]
            assert diagnostics["active_package_id"] == package_b["package_id"]
            assert diagnostics["parsed_candidate_count"] == 1
            assert diagnostics["accepted_inventory_count"] == 1
            assert diagnostics["recommendation_input_count"] == 1
            assert diagnostics["recommendation_output_count"] == 1
            assert diagnostics["result_package_ids"] == [package_b["package_id"]]
        finally:
            cleanup(package_a["job_id"], package_b["job_id"])


def test_repeated_identical_upload_uses_distinct_package_namespaces():
    with TestClient(app) as client:
        first = upload(client, "3003")
        second = upload(client, "3003")
        try:
            assert first["package_id"] != second["package_id"]
            assert first["results"][0]["package_id"] == first["package_id"]
            assert second["results"][0]["package_id"] == second["package_id"]
            assert first["results"][0]["inventory_key"] != second["results"][0]["inventory_key"]
        finally:
            cleanup(first["job_id"], second["job_id"])


def test_all_recommendation_and_flight_deck_requests_require_active_package(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    with TestClient(app) as client:
        package = upload(client, "4004")
        job_id, package_id = package["job_id"], package["package_id"]
        try:
            assert client.post(f"/api/jobs/{job_id}/rescore", data={"profile_json": "{}"}).status_code == 400
            assert client.post(f"/api/jobs/{job_id}/rescore", data={"profile_json": "{}", "package_id": "old-package"}).status_code == 409
            reranked = client.post(f"/api/jobs/{job_id}/rescore", data={"profile_json": "{}", "package_id": package_id})
            assert reranked.status_code == 200
            assert {row["package_id"] for row in reranked.json()["results"]} == {package_id}

            pbs = client.post(f"/api/jobs/{job_id}/navblue-plan", json={"package_id": package_id})
            pools = client.post(f"/api/jobs/{job_id}/month-plan", json={"package_id": package_id})
            assert pbs.status_code == pools.status_code == 200
            assert pbs.json()["package_id"] == pools.json()["package_id"] == package_id
            assert client.post(f"/api/jobs/{job_id}/navblue-plan", json={"package_id": "old-package"}).status_code == 409

            export = client.get(f"/api/jobs/{job_id}/report.pdf", params={"package_id": package_id})
            assert export.status_code == 200
            assert client.get(f"/api/jobs/{job_id}/report.pdf", params={"package_id": "old-package"}).status_code == 409
        finally:
            cleanup(job_id)


def test_server_rejects_contaminated_results_for_classic_labs_and_exports():
    with TestClient(app) as client:
        package = upload(client, "5005")
        contaminated = [{**package["results"][0], "package_id": "package-a"}]
        try:
            with db() as conn:
                conn.execute("UPDATE jobs SET results_json=? WHERE id=?", (json.dumps(contaminated), package["job_id"]))
            assert client.get(f'/api/jobs/{package["job_id"]}').status_code == 409
            assert client.get(
                f'/api/jobs/{package["job_id"]}/report.pdf', params={"package_id": package["package_id"]}
            ).status_code == 409
        finally:
            cleanup(package["job_id"])


def test_frontends_share_package_guard_and_clear_every_dependent_surface():
    classic = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    labs = (ROOT / "app" / "static" / "labs.js").read_text(encoding="utf-8")
    for script in (classic, labs):
        assert "crewbidiqActivePackage" in script
        assert "crewbidiqShortlist" in script
        assert "crewbidiqComparison" in script
        assert "crewbidiqPbsPool" in script
        assert "crewbidiqCommuteAssessments" in script
        assert "crewbidiqExports" in script
        assert "Mixed-package results were rejected" in script
    assert "data.append('package_id', activePackageId || '')" in classic
    assert "package_id: activePackageId()" in labs
    assert "refinedRecommendationsSignature = ''" in labs


def test_demo_fixtures_are_explicitly_namespaced_and_never_persisted_as_active_package():
    classic = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "activePackageId = 'demo:explicit'" in classic
    assert "demo_mode: true" in classic
    assert "localStorage.setItem('crewbidiqActivePackage', 'demo:explicit')" not in classic
