import json

from fastapi.testclient import TestClient

from app.main import app, db
from app.reporting import build_bid_report


def test_professional_report_contains_pdf_and_multiple_pages():
    result = {
        "pairing": "2478", "display_label": "Rotation", "match_level": "excellent",
        "credit": "21:35", "tafb": "72:10", "layovers": [{"city": "SAN", "duration": "16:00"}],
        "duty_legs": [2, 3, 1], "redeye": "none", "reasons": ["SAN is a highest-priority overnight"],
        "original_display": "#2478\nATL 0830 SAN 1035",
    }
    payload = build_bid_report([result], {"elite_cities": ["SAN"]}, "delta", "August.pdf")
    assert payload.startswith(b"%PDF-")
    assert payload.count(b"/Type /Page") >= 3


def test_legacy_csv_url_returns_pdf_for_cached_clients():
    job_id = "legacy-report-route-test"
    result = {"pairing": "2478", "display_label": "Rotation", "match_level": "good", "reasons": []}
    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id,filename,context,status,progress,results_json,airline,profile_json) VALUES(?,?,?,?,?,?,?,?)",
                (job_id, "August.pdf", "delta", "complete", 100, json.dumps([result]), "delta", "{}"),
            )
        response = client.get(f"/api/jobs/{job_id}/csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
