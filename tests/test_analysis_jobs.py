import json
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

import app.main as main


SESSION = "analysis-regression-session"
HEADERS = {"X-CrewBidIQ-Session": SESSION}


def pdf_bytes(text: str = "MASTER PAIRINGS\n#1001\nTOTAL CREDIT 10.00TL\nTAFB 24.00") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def create_saved_job(client: TestClient, monkeypatch, session: str = SESSION) -> dict:
    monkeypatch.setattr(main, "run_job_once", lambda *_args, **_kwargs: None)
    response = client.post(
        "/api/jobs",
        data={"airline": "delta", "context": "classic", "profile_json": "{}", "session_id": session},
        files={"file": ("ATL320 AUG 2026.pdf", pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 200
    return response.json()


def cleanup_package(package_id: str) -> None:
    with main.db() as conn:
        package = conn.execute("SELECT uploads_json FROM packages WHERE id=?", (package_id,)).fetchone()
        conn.execute("DELETE FROM jobs WHERE package_id=?", (package_id,))
        conn.execute("DELETE FROM packages WHERE id=?", (package_id,))
    if package:
        for value in json.loads(package["uploads_json"] or "[]"):
            Path(value).unlink(missing_ok=True)


def test_upload_persists_one_package_and_one_distinct_job_before_polling(monkeypatch):
    with TestClient(main.app) as client:
        created = create_saved_job(client, monkeypatch)
        try:
            assert created["package_id"] != created["job_id"]
            assert created["package_persisted"] is True
            assert created["state"] == "queued"
            required = {
                "package_id", "job_id", "current_stage", "progress_percent", "created_at", "updated_at",
                "last_successful_poll_at", "retry_count", "recoverable", "error_code", "user_message",
            }
            assert required <= created.keys()
            with main.db() as conn:
                packages = conn.execute("SELECT COUNT(*) FROM packages WHERE id=?", (created["package_id"],)).fetchone()[0]
                jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE id=? AND package_id=?", (created["job_id"], created["package_id"])).fetchone()[0]
            assert packages == jobs == 1
        finally:
            cleanup_package(created["package_id"])


def test_polling_requires_the_matching_package_and_preserves_confirmed_progress(monkeypatch):
    with TestClient(main.app) as client:
        created = create_saved_job(client, monkeypatch)
        try:
            main.update_job(created["job_id"], status="processing", state="parsing", current_stage="extracting_text", progress=43, message="Extracting PDF page 43 of 100")
            response = client.get(
                f'/api/jobs/{created["job_id"]}', params={"package_id": created["package_id"]}, headers=HEADERS,
            )
            assert response.status_code == 200
            assert response.json()["progress_percent"] == 43
            assert response.json()["last_successful_poll_at"]
            mismatch = client.get(
                f'/api/jobs/{created["job_id"]}', params={"package_id": "another-package"}, headers=HEADERS,
            )
            assert mismatch.status_code == 409
            assert mismatch.json()["detail"]["error_code"] == "JOB_PACKAGE_MISMATCH"
        finally:
            cleanup_package(created["package_id"])


def test_resume_reuses_an_active_job_and_repeated_taps_do_not_duplicate(monkeypatch):
    with TestClient(main.app) as client:
        created = create_saved_job(client, monkeypatch)
        try:
            first = client.post(
                f'/api/packages/{created["package_id"]}/analysis-jobs', data={"session_id": SESSION}, headers=HEADERS,
            )
            second = client.post(
                f'/api/packages/{created["package_id"]}/analysis-jobs', data={"session_id": SESSION}, headers=HEADERS,
            )
            assert first.status_code == second.status_code == 200
            assert first.json()["job_id"] == second.json()["job_id"] == created["job_id"]
            assert first.json()["replacement_created"] is False
            with main.db() as conn:
                count = conn.execute("SELECT COUNT(*) FROM jobs WHERE package_id=?", (created["package_id"],)).fetchone()[0]
            assert count == 1
        finally:
            cleanup_package(created["package_id"])


def test_expired_job_returns_410_and_resume_creates_one_replacement(monkeypatch):
    with TestClient(main.app) as client:
        created = create_saved_job(client, monkeypatch)
        try:
            main.update_job(
                created["job_id"], status="failed", state="expired", current_stage="expired", progress=43,
                error_code="JOB_EXPIRED", error=main.ANALYSIS_ERROR_MESSAGES["JOB_EXPIRED"],
                message=main.ANALYSIS_ERROR_MESSAGES["JOB_EXPIRED"],
            )
            expired = client.get(
                f'/api/jobs/{created["job_id"]}', params={"package_id": created["package_id"]}, headers=HEADERS,
            )
            assert expired.status_code == 410
            assert expired.json()["detail"]["progress_percent"] == 43
            replacement = client.post(
                f'/api/packages/{created["package_id"]}/analysis-jobs', data={"session_id": SESSION}, headers=HEADERS,
            )
            repeated = client.post(
                f'/api/packages/{created["package_id"]}/analysis-jobs', data={"session_id": SESSION}, headers=HEADERS,
            )
            assert replacement.status_code == repeated.status_code == 200
            assert replacement.json()["replacement_created"] is True
            assert replacement.json()["job_id"] != created["job_id"]
            assert repeated.json()["job_id"] == replacement.json()["job_id"]
        finally:
            cleanup_package(created["package_id"])


def test_missing_job_recovers_from_package_but_missing_package_requests_reupload(monkeypatch):
    with TestClient(main.app) as client:
        created = create_saved_job(client, monkeypatch)
        try:
            with main.db() as conn:
                conn.execute("DELETE FROM jobs WHERE id=?", (created["job_id"],))
            missing = client.get(
                f'/api/jobs/{created["job_id"]}', params={"package_id": created["package_id"]}, headers=HEADERS,
            )
            assert missing.status_code == 404
            assert missing.json()["detail"]["error_code"] == "JOB_NOT_FOUND"
            replacement = client.post(
                f'/api/packages/{created["package_id"]}/analysis-jobs', data={"session_id": SESSION}, headers=HEADERS,
            )
            assert replacement.status_code == 200
            assert replacement.json()["replacement_created"] is True
        finally:
            cleanup_package(created["package_id"])
        unavailable = client.post("/api/packages/not-saved/analysis-jobs", data={"session_id": SESSION})
        assert unavailable.status_code == 404
        assert unavailable.json()["detail"]["error_code"] == "PACKAGE_NOT_PERSISTED"


def test_parser_failure_is_actionable_and_recoverable_from_the_saved_upload(monkeypatch):
    with TestClient(main.app) as client:
        created = create_saved_job(client, monkeypatch)
        try:
            main.update_job(
                created["job_id"], status="failed", state="failed", current_stage="failed", progress=67,
                error_code="PARSER_FAILED", error="No production rotation rows were found.",
                message=main.ANALYSIS_ERROR_MESSAGES["PARSER_FAILED"], recoverable=1,
            )
            response = client.get(
                f'/api/jobs/{created["job_id"]}', params={"package_id": created["package_id"]}, headers=HEADERS,
            )
            assert response.status_code == 200
            assert response.json()["error_code"] == "PARSER_FAILED"
            assert response.json()["progress_percent"] == 67
            assert response.json()["recoverable"] is True
            assert "could not be parsed" in response.json()["user_message"]
        finally:
            cleanup_package(created["package_id"])


def test_package_replacement_cancels_the_prior_session_job(monkeypatch):
    with TestClient(main.app) as client:
        first = create_saved_job(client, monkeypatch)
        second = create_saved_job(client, monkeypatch)
        try:
            with main.db() as conn:
                previous = conn.execute("SELECT state,recoverable FROM jobs WHERE id=?", (first["job_id"],)).fetchone()
            assert previous["state"] == "cancelled"
            assert previous["recoverable"] == 0
            assert first["package_id"] != second["package_id"]
        finally:
            cleanup_package(first["package_id"])
            cleanup_package(second["package_id"])


def test_client_recovery_is_bounded_refreshable_and_safari_aware():
    classic = Path("app/static/app.js").read_text(encoding="utf-8")
    labs = Path("app/static/labs.js").read_text(encoding="utf-8")
    css = Path("app/static/app.css").read_text(encoding="utf-8")

    for script in (classic, labs):
        assert "crewbidiqAnalysisJob" in script
        assert "Last confirmed progress" in script
        assert "visibilitychange" in script
        assert "pageshow" in script
        assert "addEventListener('online'" in script
        assert "analysis-jobs" in script
        assert "POLLING_NETWORK_ERROR" in script
    assert "MAX_POLL_RETRIES = 6" in classic
    assert "setInterval(pollJob" not in classic
    assert "Your upload is safe" not in classic
    assert "if (resumeInFlight) return" in classic
    assert "if (sessionResumeInFlight) return" in labs
    assert "analysis-debug" in css


def test_completed_status_can_omit_large_results_but_keep_package_summary():
    job_id = "lightweight-status-test"
    source = {
        "kind": "pairings",
        "parser_name": "delta",
        "synopsis": {"total": 2, "start_airports": [{"airport": "ATL", "count": 2, "percent": 100}]},
        "package_diagnostics": {"recommendation_output_count": 2},
    }
    results = [{"pairing": "1001"}, {"pairing": "1002"}]
    with TestClient(main.app) as client:
        with main.db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO jobs
                   (id,filename,status,progress,results_json,airline,source_json,state,current_stage)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (job_id, "ATL AUG 2026.pdf", "complete", 100, json.dumps(results), "delta", json.dumps(source), "completed", "ready"),
            )
        response = client.get(f"/api/jobs/{job_id}", params={"include_results": "false"})
        with main.db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    assert response.status_code == 200
    body = response.json()
    assert "results" not in body
    assert body["synopsis"] == source["synopsis"]
    assert body["package"]["parsed_count"] == 2


def test_clients_persist_only_lightweight_state_and_release_bfcache_resources():
    classic = Path("app/static/app.js").read_text(encoding="utf-8")
    labs = Path("app/static/labs.js").read_text(encoding="utf-8")
    flight_deck = Path("app/static/flight-deck.js").read_text(encoding="utf-8")

    assert "lightweightAnalysisState" in classic
    assert "lightweightAnalysis" in labs
    assert "include_results=false" in labs
    for script in (classic, labs, flight_deck):
        assert "addEventListener('pagehide'" in script
        assert "event.persisted" in script
        assert "AbortController" in script
    assert "flightDeckMap.remove()" in flight_deck


def test_classic_labs_and_demo_remain_isolated_on_the_shared_job_model():
    classic = Path("app/static/app.js").read_text(encoding="utf-8")
    labs = Path("app/static/labs.js").read_text(encoding="utf-8")
    assert "crewbidiqActivePackage" in classic and "crewbidiqActivePackage" in labs
    assert "demo:explicit" in classic
    assert "clearActiveAnalysis()" in classic
    assert "acceptPackageResponse(sessionJob)" in labs


def test_reconnect_to_85_percent_job_preserves_elapsed_time_and_stage_truth():
    job_id = "reconnect-85-test"
    package_id = "reconnect-85-package"
    with TestClient(main.app) as client:
        with main.db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO jobs(id,filename,status,progress,message,airline,profile_json,uploads_json,package_id,state,current_stage,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    "ATL320 AUG.pdf",
                    "processing",
                    85,
                    "Building summaries: batch 2 of 8",
                    "delta",
                    "{}",
                    "[]",
                    package_id,
                    "ranking",
                    "building_recommendations",
                    "2026-07-16T00:00:00",
                    main.utc_now(),
                ),
            )
        response = client.get(f"/api/jobs/{job_id}", params={"package_id": package_id}, headers=HEADERS)
        with main.db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    body = response.json()
    assert response.status_code == 200
    assert body["progress_percent"] == 85
    assert body["stage"] == "building_recommendations"
    assert body["stage_label"] == "Building recommendation data"
    assert body["elapsed_seconds"] >= 60 * 60


def test_reconnect_stage_and_elapsed_restore_logic_is_monotonic_in_classic_and_labs_clients():
    classic = Path("app/static/app.js").read_text(encoding="utf-8")
    labs = Path("app/static/labs.js").read_text(encoding="utf-8")

    assert "current_stage: analysisState.current_stage || inferredStageFromProgress(lastConfirmedProgress)" in classic
    assert "stage_label: analysisState.stage_label || analysisState.message || 'Building recommendation data'" in classic
    assert "state: 'reconnecting'" in classic

    assert "const incomingIndex = processingStageIndex.get(incomingStage) ?? -1;" in labs
    assert "const previousIndex = processingStageIndex.get(previousStage) ?? -1;" in labs
    assert "const stage = incomingIndex >= previousIndex ? incomingStage : previousStage;" in labs
    assert "elapsed_seconds: Math.max(Number(body.elapsed_seconds || 0), elapsedSeconds(previous))" in labs
    assert "elapsed_seconds: elapsedSeconds(previous)" in labs


def test_startup_skips_cancelled_jobs_even_if_legacy_status_is_processing(monkeypatch, tmp_path):
    cancelled_job = "startup-cancelled-job"
    active_job = "startup-active-job"
    cancelled_upload = tmp_path / "cancelled.pdf"
    active_upload = tmp_path / "active.pdf"
    cancelled_upload.write_bytes(b"%PDF-cancelled")
    active_upload.write_bytes(b"%PDF-active")

    with main.db() as conn:
        conn.execute("DELETE FROM jobs WHERE id IN (?,?)", (cancelled_job, active_job))
        conn.execute(
            """INSERT INTO jobs(id,filename,status,progress,message,airline,profile_json,uploads_json,state,current_stage,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cancelled_job,
                "cancelled.pdf",
                "processing",
                44,
                "Scoring recommendations: 4 of 20",
                "delta",
                "{}",
                json.dumps([str(cancelled_upload)]),
                "cancelled",
                "cancelled",
                "2026-07-16T00:00:00",
                "2026-07-16T00:00:30",
            ),
        )
        conn.execute(
            """INSERT INTO jobs(id,filename,status,progress,message,airline,profile_json,uploads_json,state,current_stage,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                active_job,
                "active.pdf",
                "processing",
                25,
                "Extracting PDF page 2 of 4",
                "delta",
                "{}",
                json.dumps([str(active_upload)]),
                "parsing",
                "extracting_text",
                "2026-07-16T00:00:00",
                "2026-07-16T00:00:30",
            ),
        )

    started: list[str] = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def start(self):
            started.append(self.kwargs["args"][0])

    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    main.startup()

    assert active_job in started
    assert cancelled_job not in started

    with main.db() as conn:
        conn.execute("DELETE FROM jobs WHERE id IN (?,?)", (cancelled_job, active_job))
