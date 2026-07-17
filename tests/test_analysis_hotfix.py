import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main


def _insert_completed_job(job_id: str, package_id: str) -> None:
    main.init_db()
    results = [
        {
            "id": f"{package_id}:1001",
            "package_id": package_id,
            "pairing": "1001",
            "rank": 1,
            "match_class": "exact",
            "match_label": "Exact Match",
            "qualification_reasons": ["Met all hard requirements"],
            "trip_length": 4,
            "simplified_route": "ATL-SAN-ATL",
            "checkin": "0800",
            "release": "1745",
            "tafb": "72:00",
            "trip_credit": "20:00",
            "total_pay": "22:15",
            "layovers": [{"city": "SAN", "duration": "16:00"}],
            "fatigue_index": {"level": "Low"},
            "hold_outlook": {"likelihood": "Low"},
            "eligible": True,
            "ordered_events": [{"kind": "report"}],
            "original_display": "LARGE PAYLOAD FIELD",
        },
        {
            "id": f"{package_id}:1002",
            "package_id": package_id,
            "pairing": "1002",
            "rank": 2,
            "match_class": "near",
            "match_label": "Near Match",
            "qualification_reasons": ["Missed one hard requirement"],
            "trip_length": 3,
            "simplified_route": "ATL-BOS-ATL",
            "checkin": "0900",
            "release": "1800",
            "tafb": "48:00",
            "trip_credit": "15:00",
            "layovers": [{"city": "BOS", "duration": "12:00"}],
            "fatigue_index": {"level": "Moderate"},
            "hold_outlook": {"likelihood": "Moderate"},
            "eligible": False,
            "ordered_events": [{"kind": "report"}],
            "original_display": "LARGE PAYLOAD FIELD",
        },
    ]
    with main.db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO packages(id,filename,context,airline,profile_json,uploads_json,persisted,recoverable,session_id_hash,request_id,expires_at,created_at,updated_at)
               VALUES(?,?,?,?,?,?,1,1,NULL,'test',NULL,datetime('now'),datetime('now'))""",
            (package_id, "test.pdf", "classic", "delta", "{}", "[]"),
        )
        conn.execute(
            """INSERT OR REPLACE INTO jobs(id,filename,context,status,progress,message,airline,profile_json,uploads_json,source_json,results_json,summaries_json,package_id,state,current_stage,retry_count,recoverable,user_message,request_id,session_id_hash,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (
                job_id,
                "test.pdf",
                "classic",
                "complete",
                100,
                "Complete",
                "delta",
                "{}",
                "[]",
                json.dumps({"kind": "pairings", "package_id": package_id, "synopsis": {}}),
                json.dumps(results),
                json.dumps([main.recommendation_summary(result) for result in results]),
                package_id,
                "completed",
                "ready",
                0,
                1,
                "Complete",
                "test",
                None,
            ),
        )


def test_recommendation_endpoint_is_paginated_and_lightweight():
    job_id = "hotfix-summary-job"
    package_id = "hotfix-summary-package"
    _insert_completed_job(job_id, package_id)
    with TestClient(main.app) as client:
        response = client.get(f"/api/jobs/{job_id}/recommendations", params={"package_id": package_id, "limit": 1, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 2
    assert body["limit"] == 1
    assert body["next_offset"] == 1
    assert len(body["results"]) == 1
    assert "ordered_events" not in body["results"][0]
    assert body["counts"]["exact"] == 1
    assert body["counts"]["near"] == 1


def test_cancel_endpoint_is_idempotent_for_active_job():
    job_id = "hotfix-cancel-job"
    package_id = "hotfix-cancel-package"
    with main.db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO jobs(id,filename,status,progress,message,airline,profile_json,uploads_json,package_id,state,current_stage,recoverable,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (job_id, "test.pdf", "processing", 42, "Scoring", "delta", "{}", "[]", package_id, "ranking", "building_recommendations", 1),
        )
    with TestClient(main.app) as client:
        first = client.post(f"/api/jobs/{job_id}/cancel", data={"package_id": package_id})
        second = client.post(f"/api/jobs/{job_id}/cancel", data={"package_id": package_id})
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cancelled"] is True
    assert second.json()["already_cancelled"] is True


def test_reset_endpoint_clears_package_and_jobs():
    job_id = "hotfix-reset-job"
    package_id = "hotfix-reset-package"
    with main.db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO packages(id,filename,context,airline,profile_json,uploads_json,persisted,recoverable,created_at,updated_at)
               VALUES(?,?,?,?,?,?,1,1,datetime('now'),datetime('now'))""",
            (package_id, "test.pdf", "classic", "delta", "{}", "[]"),
        )
        conn.execute(
            """INSERT OR REPLACE INTO jobs(id,filename,status,progress,message,airline,profile_json,uploads_json,package_id,state,current_stage,recoverable,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (job_id, "test.pdf", "processing", 12, "Extracting", "delta", "{}", "[]", package_id, "parsing", "extracting_text", 1),
        )
    with TestClient(main.app) as client:
        response = client.post(f"/api/packages/{package_id}/reset")
    assert response.status_code == 200
    assert response.json()["reset"] is True
    with main.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE package_id=?", (package_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM packages WHERE id=?", (package_id,)).fetchone()[0] == 0


def _sample_pairing(pairing_id: str) -> dict:
    return {
        "id": pairing_id,
        "block": f"#{pairing_id}\nATL 0800 BOS 1000",
        "legs": [
            {
                "day": "A",
                "deadhead": False,
                "departure": "ATL",
                "departure_time": "0800",
                "arrival": "BOS",
                "arrival_time": "1000",
                "aircraft": "320",
            },
            {
                "day": "B",
                "deadhead": False,
                "departure": "BOS",
                "departure_time": "0800",
                "arrival": "ATL",
                "arrival_time": "1000",
                "aircraft": "320",
            },
        ],
        "layovers": [{"city": "BOS", "duration": "16:00", "hotel": None}],
        "credit": "10:00",
        "tafb": "26:00",
        "parser": "delta_test",
        "confidence": 1.0,
    }


def _insert_processing_job(job_id: str, upload_path: Path, *, package_id: str) -> None:
    with main.db() as conn:
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.execute(
            """INSERT INTO jobs(id,filename,status,progress,message,airline,profile_json,uploads_json,package_id,state,current_stage,recoverable,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (
                job_id,
                upload_path.name,
                "processing",
                5,
                "Detecting airline and package type",
                "delta",
                "{}",
                json.dumps([str(upload_path)]),
                package_id,
                "parsing",
                "detecting_package",
                1,
            ),
        )


def _prepare_cached_delta_job(job_id: str, tmp_path: Path, *, pairings: list[dict], package_id: str) -> tuple[Path, str]:
    upload_path = tmp_path / f"{job_id}.pdf"
    upload_path.write_bytes(b"%PDF-1.4\ncached")
    cache_key = main.parser_cache_key(upload_path, "delta")
    main.store_cached_pairings(cache_key, "delta", "delta_v1", pairings)
    _insert_processing_job(job_id, upload_path, package_id=package_id)
    return upload_path, cache_key


def test_cancel_during_active_parsing_stops_before_parser_execution(monkeypatch, tmp_path):
    job_id = "hotfix-cancel-during-parse"
    package_id = "hotfix-cancel-during-parse-package"
    upload_path = tmp_path / "parse-target.pdf"
    upload_path.write_bytes(b"%PDF-1.4\nparse")
    _insert_processing_job(job_id, upload_path, package_id=package_id)

    def cancel_while_extracting(*_args, **_kwargs):
        main.update_job(
            job_id,
            status="failed",
            state="cancelled",
            current_stage="cancelled",
            recoverable=0,
            message="Analysis cancelled",
            user_message="Analysis cancelled",
        )
        return "MASTER PAIRINGS\n#1001"

    monkeypatch.setattr(main, "extract_text", cancel_while_extracting)
    monkeypatch.setattr(
        main,
        "parse_pairings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cancelled parsing must not reach parse_pairings")),
    )

    main.process_job(job_id, [upload_path], {}, "delta")
    row = main.get_job(job_id)

    assert row["state"] == "cancelled"
    assert row["status"] == "failed"
    assert row["current_stage"] == "cancelled"
    assert row["results_json"] is None


def test_cancel_during_active_scoring_stops_before_publish(monkeypatch, tmp_path):
    job_id = "hotfix-cancel-during-scoring"
    package_id = "hotfix-cancel-during-scoring-package"
    pairings = [_sample_pairing("1001"), _sample_pairing("1002"), _sample_pairing("1003")]
    upload_path, cache_key = _prepare_cached_delta_job(job_id, tmp_path, pairings=pairings, package_id=package_id)
    original_score_pairing = main.score_pairing
    cancelled = {"value": False}

    def score_and_cancel(pairing, profile):
        if not cancelled["value"]:
            cancelled["value"] = True
            main.update_job(
                job_id,
                status="failed",
                state="cancelled",
                current_stage="cancelled",
                recoverable=0,
                message="Analysis cancelled",
                user_message="Analysis cancelled",
            )
        return original_score_pairing(pairing, profile)

    monkeypatch.setattr(main, "score_pairing", score_and_cancel)

    try:
        main.process_job(job_id, [upload_path], {}, "delta")
        row = main.get_job(job_id)
        assert row["state"] == "cancelled"
        assert row["status"] == "failed"
        assert row["current_stage"] == "cancelled"
    finally:
        with main.db() as conn:
            conn.execute("DELETE FROM parse_cache WHERE cache_key=?", (cache_key,))


def test_one_malformed_trip_does_not_block_remaining_results(monkeypatch, tmp_path):
    job_id = "hotfix-malformed-trip"
    package_id = "hotfix-malformed-trip-package"
    pairings = [_sample_pairing("1001"), _sample_pairing("1002"), _sample_pairing("1003")]
    upload_path, cache_key = _prepare_cached_delta_job(job_id, tmp_path, pairings=pairings, package_id=package_id)
    original_score_pairing = main.score_pairing

    def score_with_one_malformed(pairing, profile):
        if str(pairing.get("id")) == "1002":
            raise RuntimeError("Malformed record")
        return original_score_pairing(pairing, profile)

    monkeypatch.setattr(main, "score_pairing", score_with_one_malformed)

    try:
        main.process_job(job_id, [upload_path], {}, "delta")
        row = main.get_job(job_id)
        results = json.loads(row["results_json"] or "[]")
        assert row["status"] == "complete"
        assert row["state"] == "completed"
        assert int(row["records_failed"] or 0) == 1
        assert len(results) == 2
        assert all(result.get("pairing") != "1002" for result in results)
    finally:
        with main.db() as conn:
            conn.execute("DELETE FROM parse_cache WHERE cache_key=?", (cache_key,))
