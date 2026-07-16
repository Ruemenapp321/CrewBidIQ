import json

from fastapi.testclient import TestClient

from app.main import app, db


def test_labs_feature_flag_leaves_classic_unchanged_when_disabled(monkeypatch):
    monkeypatch.delenv("LABS_ENABLED", raising=False)

    with TestClient(app) as client:
        classic = client.get("/")
        labs = client.get("/labs")

    assert classic.status_code == 200
    assert 'href="/labs"' not in classic.text
    assert "Analyze</a>" not in classic.text
    assert labs.status_code == 404


def test_enabled_labs_is_one_click_from_classic_and_results(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")

    with TestClient(app) as client:
        classic = client.get("/")
        results = client.get("/results")

    for response in (classic, results):
        assert response.status_code == 200
        assert '<a href="/labs">Labs <small>Beta</small></a>' in response.text
        assert '<a href="/labs"><span>L</span>Labs</a>' in response.text
        assert "Continue in Labs" in response.text
    assert 'data-classic-page="home"' in classic.text
    assert 'data-classic-page="results"' in results.text
    assert 'href="/" class="active"><span>A</span>Analyze' in classic.text
    assert 'href="/results" class="active"><span>R</span>Results' in results.text


def test_all_labs_routes_share_one_feature_gated_shell(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    routes = {
        "/labs": "landing",
        "/labs/build": "build",
        "/labs/recommendations": "recommendations",
        "/labs/preview": "preview",
        "/labs/plan": "plan",
    }

    with TestClient(app) as client:
        responses = {route: client.get(route) for route in routes}

    for route, page in routes.items():
        response = responses[route]
        assert response.status_code == 200
        assert f'data-labs-page="{page}"' in response.text
        assert 'href="/" class="active">Classic' not in response.text
        assert '<a href="/"' in response.text
        assert "Return to Classic" in response.text
        assert 'href="/labs" class="active">Labs <small>Beta</small></a>' in response.text
        assert 'class="bottom-nav three labs-bottom-nav"' in response.text
        assert 'type="file"' not in response.text


def test_labs_uses_the_classic_job_key_and_has_no_second_upload_flow():
    with TestClient(app) as client:
        script = client.get("/static/labs.js")

    assert script.status_code == 200
    assert "crewbidiqLatestJob" in script.text
    assert "crewbidiqActiveJob" in script.text
    assert "No bid package loaded" in script.text
    assert "Upload in Classic" in script.text
    assert "new FormData" not in script.text


def test_job_status_exposes_package_identity_for_labs(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    job_id = "labs-shared-session-test"
    source = {"kind": "pairings", "pairings": [], "synopsis": {"total": 0}}

    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id,filename,status,progress,message,results_json,airline,profile_json,source_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, "ATL320 AUG.pdf", "complete", 100, "Complete", "[]", "delta", "{}", json.dumps(source)),
            )
        response = client.get(f"/api/jobs/{job_id}")
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    assert response.status_code == 200
    assert response.json()["filename"] == "ATL320 AUG.pdf"
    assert response.json()["airline"] == "delta"
    assert response.json()["synopsis"] == {"total": 0}
