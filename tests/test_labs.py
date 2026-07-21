import io
import json
import zipfile
from pathlib import Path

import fitz

from fastapi.testclient import TestClient

from app import main
from app.main import app, db


def _pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def _remove_job(job_id: str) -> None:
    with db() as conn:
        row = conn.execute("SELECT source_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row and row["source_json"]:
            cache_key = json.loads(row["source_json"]).get("cache_key")
            if cache_key:
                conn.execute("DELETE FROM parse_cache WHERE cache_key=?", (cache_key,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))


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
        "/labs": "analysis_dashboard",
        "/labs/build": "build",
        "/labs/recommendations": "recommendations",
        "/labs/preview": "preview",
        "/labs/plan": "plan",
        "/labs/southwest": "southwest",
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


def test_package_scoped_labs_route_is_the_dense_dashboard_shell(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    package_id = "package-route-123"

    with TestClient(app) as client:
        response = client.get(f"/bid-packages/{package_id}/labs")

    assert response.status_code == 200
    assert 'data-labs-page="analysis_dashboard"' in response.text
    assert f'window.CREWBIDIQ_BID_PACKAGE_ID="{package_id}"' in response.text
    assert f'href="/bid-packages/{package_id}/labs"' in response.text
    assert "leaflet.css" in response.text
    assert "airport-coordinates.js" in response.text
    assert "Package Overview" in response.text
    assert "Risk Signals" in response.text
    assert 'type="file"' not in response.text


def test_package_status_recovers_latest_job_by_authoritative_id_and_session():
    package_id = "dashboard-api-package"
    old_job = "dashboard-api-old"
    current_job = "dashboard-api-current"
    session_id = "dashboard-browser-session"
    source = {
        "kind": "pairings",
        "synopsis": {"total": 2, "complete": 2, "incomplete": 0},
        "package_diagnostics": {"recommendation_output_count": 2},
    }
    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO packages
                   (id,filename,airline,persisted,recoverable,session_id_hash,created_at,updated_at)
                   VALUES(?,?,?,0,0,?,?,?)""",
                (package_id, "ATL AUG 2026.pdf", "delta", main.session_hash(session_id), "2026-07-20T10:00:00", "2026-07-20T10:05:00"),
            )
            for job_id, updated_at in ((old_job, "2026-07-20T10:01:00"), (current_job, "2026-07-20T10:05:00")):
                conn.execute(
                    """INSERT OR REPLACE INTO jobs
                       (id,filename,status,state,current_stage,progress,results_json,source_json,airline,package_id,recoverable,session_id_hash,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, "ATL AUG 2026.pdf", "complete", "completed", "ready", 100, "[]", json.dumps(source), "delta", package_id, 1, main.session_hash(session_id), "2026-07-20T10:00:00", updated_at),
                )
        recovered = client.get(f"/api/packages/{package_id}", headers={"X-CrewBidIQ-Session": session_id})
        rejected = client.get(f"/api/packages/{package_id}", headers={"X-CrewBidIQ-Session": "another-session"})
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE id IN (?,?)", (old_job, current_job))
            conn.execute("DELETE FROM packages WHERE id=?", (package_id,))

    assert recovered.status_code == 200
    assert recovered.json()["package_id"] == package_id
    assert recovered.json()["job_id"] == current_job
    assert recovered.json()["package"]["parsed_count"] == 2
    assert rejected.status_code == 403
    assert rejected.json()["detail"]["error_code"] == "SESSION_EXPIRED"


def test_dashboard_uses_real_package_data_and_releases_interactive_resources():
    script = Path("app/static/labs.js").read_text(encoding="utf-8")
    css = Path("app/static/app.css").read_text(encoding="utf-8")

    for label in (
        "CREWBIDIQ BRIEFING",
        "RISK & COMPLIANCE SIGNALS",
        "WIN OUTLOOK",
        "TEAMING & STRATEGY",
        "SOURCE NAVIGATION",
        "No prime, partner, or subcontractor records were detected",
    ):
        assert label in script
    assert "window.CREWBIDIQ_BID_PACKAGE_ID" in script
    assert "fetch(`/api/packages/${encodeURIComponent(packageId)}`" in script
    assert "include_source=true" in script
    assert "include_results=false" in script
    assert "dashboardMap.remove()" in script
    assert "dashboardRecommendationsController?.abort()" in script
    assert "dashboardSourceController?.abort()" in script
    assert "if (pageLifecycleSuspended) return" in script
    assert "routeBidPackageId && !routePackageRejected" in script
    assert "window.location.pathname.startsWith('/bid-packages/')" in script
    assert ".command-grid{display:grid" in css
    assert "@media(max-width:620px)" in css


def test_source_page_metadata_is_opt_in_to_keep_recommendation_pages_lightweight():
    job_id = "dashboard-source-index-job"
    package_id = "dashboard-source-index-package"
    result = {
        "id": "R1001",
        "package_id": package_id,
        "pairing": "R1001",
        "rank": 1,
        "match_class": "strong",
        "source_page": 17,
        "source_section": "MASTER PAIRINGS",
    }
    summary = main.recommendation_summary(result)
    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO jobs
                   (id,filename,status,state,current_stage,progress,results_json,summaries_json,source_json,airline,package_id,recoverable)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""",
                (job_id, "ATL AUG.pdf", "complete", "completed", "ready", 100, json.dumps([result]), json.dumps([summary]), json.dumps({"synopsis": {"total": 1}}), "delta", package_id),
            )
        compact = client.get(f"/api/jobs/{job_id}/recommendations", params={"package_id": package_id})
        indexed = client.get(f"/api/jobs/{job_id}/recommendations", params={"package_id": package_id, "include_source": "true"})
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    assert compact.status_code == indexed.status_code == 200
    assert "source_page" not in compact.json()["results"][0]
    assert indexed.json()["results"][0]["source_page"] == 17
    assert indexed.json()["results"][0]["source_section"] == "MASTER PAIRINGS"


def test_southwest_line_ranker_has_independent_feature_flag(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    monkeypatch.setenv("SOUTHWEST_LINE_RANKER_ENABLED", "false")
    with TestClient(app) as client:
        labs = client.get("/labs")
        southwest = client.get("/labs/southwest")
    assert labs.status_code == 200
    assert 'href="/labs/southwest"' not in labs.text
    assert southwest.status_code == 404


def test_labs_uses_the_classic_job_keys_and_shared_upload_endpoint():
    with TestClient(app) as client:
        script = client.get("/static/labs.js")

    assert script.status_code == 200
    assert "crewbidiqLatestJob" in script.text
    assert "crewbidiqActiveJob" in script.text
    assert "No bid package loaded" in script.text
    assert "Upload Bid Package" in script.text
    assert "Replace Bid Package" in script.text
    assert "Use Current Package" in script.text
    assert "new FormData" in script.text
    assert "fetch('/api/jobs'" in script.text
    assert "data.append('context', 'labs')" in script.text
    assert script.text.count("${uploadPanel()}") >= 4
    assert "/navblue-plan" in script.text
    assert "NAVBLUE PBS REQUEST PLAN" in script.text


def test_pdf_can_be_uploaded_directly_from_labs_and_is_shared_with_classic(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    payload = _pdf_bytes("MASTER PAIRINGS\n#1001\nTOTAL CREDIT 10.00TL\nTAFB 24.00\nCHECK-IN AT 08.00")

    with TestClient(app) as client:
        upload = client.post(
            "/api/jobs",
            data={"airline": "delta", "context": "labs", "profile_json": "{}"},
            files={"file": ("ATL AUG 2026.pdf", payload, "application/pdf")},
        )
        assert upload.status_code == 200
        job_id = upload.json()["job_id"]
        status = client.get(f"/api/jobs/{job_id}")
        classic_results = client.get("/results")
        labs = client.get("/labs")
        classic_script = client.get("/static/app.js").text
        labs_script = client.get("/static/labs.js").text
        with db() as conn:
            row = conn.execute("SELECT context FROM jobs WHERE id=?", (job_id,)).fetchone()

    try:
        assert status.status_code == 200
        assert status.json()["status"] == "complete"
        assert status.json()["filename"] == "ATL AUG 2026.pdf"
        assert status.json()["package"]["bid_month"] == "August 2026"
        assert row["context"] == "labs"
        assert "crewbidiqLatestJob" in classic_script
        assert "crewbidiqLatestJob" in labs_script
        assert classic_results.status_code == 200
        assert labs.status_code == 200
    finally:
        _remove_job(job_id)


def test_southwest_zip_can_be_uploaded_directly_from_labs():
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("LAXFOP.TXT", "PAIRING 1234\n")
        archive.writestr("LAXFOL.TXT", "LINE 1 1234\n")

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            data={"airline": "southwest", "context": "labs", "profile_json": "{}"},
            files={"file": ("LAXFOA.ZIP", package.getvalue(), "application/zip")},
        )
        job_id = response.json()["job_id"]
        with db() as conn:
            row = conn.execute("SELECT context,airline,filename FROM jobs WHERE id=?", (job_id,)).fetchone()

    try:
        assert response.status_code == 200
        assert row["context"] == "labs"
        assert row["airline"] == "southwest"
        assert row["filename"] == "LAXFOA.ZIP"
    finally:
        _remove_job(job_id)


def test_labs_replacement_clears_stale_recommendations_and_guards_duplicate_taps():
    with TestClient(app) as client:
        script = client.get("/static/labs.js").text

    assert "Replace the current bid package?" in script
    assert "localStorage.removeItem(latestJobKey)" in script
    assert "navbluePlan = null" in script
    assert "if (labsUploadBusy) return" in script
    assert "button.disabled = busy" in script


def test_browser_quota_failures_are_handled_as_optional_state_in_classic_and_labs():
    classic = Path("app/static/app.js").read_text(encoding="utf-8")
    labs = Path("app/static/labs.js").read_text(encoding="utf-8")
    for script in (classic, labs):
        assert "function isQuotaExceededError(error)" in script
        assert "error.name === 'QuotaExceededError'" in script
        assert "Optional browser state was not saved due to storage quota limits" in script
        assert "return false;" in script
        assert "throw error;" in script


def test_labs_filename_progress_errors_and_post_parse_actions_are_explicit():
    with TestClient(app) as client:
        script = client.get("/static/labs.js").text

    for label in (
        "Uploading file",
        "Detecting airline and package type",
        "Extracting text",
        "Identifying trip records",
        "Parsing details",
        "Building recommendation data",
        "Ready",
        "Describe the Trip You Want",
        "What to Enter in NAVBLUE/PBS",
        "Rank My Lines",
        "Optimize Conflicts",
    ):
        assert label in script
    assert "syncLabsFilename" in script
    assert "label.textContent = file.name" in script
    assert "exceeds the 100 MB upload limit" in script
    assert "select the airline manually" in script


def test_job_progress_reports_pages_stage_and_elapsed_time():
    job_id = "labs-progress-test"
    with TestClient(app) as client:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id,filename,status,progress,message,airline,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (job_id, "AA LAX AUG.pdf", "processing", 39, "Extracting PDF page 84 of 216", "american", "2026-07-16T12:00:00", "2026-07-16T12:00:05"),
            )
        response = client.get(f"/api/jobs/{job_id}")
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    body = response.json()
    assert body["stage"] == "extracting_text"
    assert body["stage_label"] == "Extracting text"
    assert body["pages_processed"] == 84
    assert body["pages_total"] == 216
    assert body["elapsed_seconds"] >= 0


def test_labs_mobile_upload_layout_keeps_actions_above_bottom_navigation():
    with TestClient(app) as client:
        css = client.get("/static/app.css").text
        script = client.get("/static/labs.js").text

    assert ".labs-file-target{min-height:118px}" in css
    assert ".labs-main{padding-bottom:100px}" in css
    assert ".labs-upload-actions{display:grid" in css
    assert "Files app, iCloud Drive, or this device" in script


def test_package_metadata_labels_and_values_are_stacked_and_wrap_safely():
    with TestClient(app) as client:
        css = client.get("/static/app.css").text
        script = client.get("/static/labs.js").text

    assert script.count('class="package-meta-item"') == 4
    for label in ("Base", "Fleet / category", "Parsed", "Last parsed"):
        assert f"<span>{label}</span><strong>" in script
    assert ".package-meta-item strong{display:block" in css
    assert "overflow-wrap:anywhere" in css
    assert "@media(max-width:440px){.package-meta-grid{grid-template-columns:1fr}}" in css


def test_labs_builder_saves_current_fields_and_rescores_before_recommendations():
    with TestClient(app) as client:
        script = client.get("/static/labs.js").text

    assert 'id="openLabsRecommendations"' in script
    assert "addEventListener('click', () => saveCurrentDraft(false))" in script
    assert "loadRefinedRecommendations(jobId)" in script
    assert "fetch(`/api/jobs/${jobId}/rescore`" in script
    assert "Applying your saved trip preferences" in script
    assert "parsed trip${request.matching_trip_count" not in script
    assert "trip${request.matching_trip_count === 1 ? '' : 's'} associated with this request" in script


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
