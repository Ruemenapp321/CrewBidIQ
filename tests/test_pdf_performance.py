import gzip
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main


def sample_pairing(pairing_id: str = "1001") -> dict:
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


def test_parser_cache_key_is_scoped_to_file_airline_and_parser_version(tmp_path):
    package = tmp_path / "package.pdf"
    package.write_bytes(b"%PDF-package-one")
    first_delta = main.parser_cache_key(package, "delta")

    assert first_delta != main.parser_cache_key(package, "american")
    assert first_delta.startswith(f"{main.PARSER_CACHE_VERSION}:delta:")

    package.write_bytes(b"%PDF-package-two")
    assert first_delta != main.parser_cache_key(package, "delta")


def test_parser_cache_round_trip_uses_compressed_storage(tmp_path):
    main.init_db()
    package = tmp_path / "package.pdf"
    package.write_bytes(b"%PDF-cache-round-trip")
    cache_key = main.parser_cache_key(package, "delta")
    pairings = [sample_pairing(), {**sample_pairing("1002"), "block": "X" * 20_000}]

    try:
        main.store_cached_pairings(cache_key, "delta", "delta_v1", pairings)
        cached = main.load_cached_pairings(cache_key)
        with main.db() as conn:
            row = conn.execute(
                "SELECT pairings_gzip,hit_count FROM parse_cache WHERE cache_key=?",
                (cache_key,),
            ).fetchone()

        assert cached == (pairings, "delta_v1")
        assert row["hit_count"] == 1
        assert json.loads(gzip.decompress(row["pairings_gzip"])) == pairings
        assert len(row["pairings_gzip"]) < len(json.dumps(pairings).encode("utf-8"))
    finally:
        with main.db() as conn:
            conn.execute("DELETE FROM parse_cache WHERE cache_key=?", (cache_key,))


def test_pdf_progress_updates_are_throttled_and_sort_mode_is_explicit(monkeypatch, tmp_path):
    sort_values = []
    updates = []

    class Page:
        def get_text(self, mode, *, sort):
            assert mode == "text"
            sort_values.append(sort)
            return "page text"

    class Document(list):
        def close(self):
            pass

    ticks = iter((0.0, 0.1, 0.2, 0.3))
    monkeypatch.setattr(main.fitz, "open", lambda path: Document([Page(), Page(), Page()]))
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    monkeypatch.setattr(main, "update_job", lambda *args, **kwargs: updates.append((args, kwargs)))

    text = main.extract_text(tmp_path / "package.pdf", ".pdf", "job-id", sort_pdf_text=False)

    assert sort_values == [False, False, False]
    assert len(updates) == 1
    assert updates[0][1]["message"] == "Extracting PDF page 3 of 3"
    assert text.count("<<<CREWBIDIQ_PAGE:") == 3


def test_cached_pdf_job_skips_extraction_and_stores_a_small_source_record(monkeypatch, tmp_path):
    main.init_db()
    job_id = "cached-pdf-performance-test"
    package = tmp_path / "package.pdf"
    package.write_bytes(b"%PDF-identical-airline-package")
    cache_key = main.parser_cache_key(package, "delta")
    pairings = [sample_pairing()]

    with main.db() as conn:
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.execute("DELETE FROM parse_cache WHERE cache_key=?", (cache_key,))
        conn.execute(
            "INSERT INTO jobs(id,filename,status,progress,airline,profile_json) VALUES(?,?,?,?,?,?)",
            (job_id, "ATL320 AUG.pdf", "queued", 1, "delta", "{}"),
        )
    main.store_cached_pairings(cache_key, "delta", "delta_v1", pairings)
    monkeypatch.setattr(
        main,
        "extract_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cached jobs must not extract the PDF")),
    )

    try:
        main.process_job(job_id, [package], {}, "delta")
        row = main.get_job(job_id)
        source = json.loads(row["source_json"])

        assert row["status"] == "complete"
        assert source["cache_key"] == cache_key
        assert source["cache_hit"] is True
        assert "pairings" not in source
        assert len(source["synopsis"]) > 0
        assert not package.exists()
    finally:
        with main.db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            conn.execute("DELETE FROM parse_cache WHERE cache_key=?", (cache_key,))


def test_large_api_responses_are_gzip_compressed():
    with TestClient(main.app) as client:
        response = client.get("/", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
