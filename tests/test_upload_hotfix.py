import io
import json
import zipfile

from fastapi.testclient import TestClient

from app.main import app


def test_upload_starts_locked_until_an_airline_is_selected():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert '<option value="" selected disabled>Select an airline</option>' in response.text
    assert 'id="uploadLocked"' in response.text
    assert 'id="pdfUploads" class="drop-zone hidden"' in response.text
    assert 'id="analyzeBtn" class="primary" disabled' in response.text


def test_pdf_upload_creates_job():
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PAIRING 1234")
    payload = doc.tobytes()
    doc.close()
    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            data={"airline": "delta", "context": "delta", "profile_json": json.dumps({})},
            files={"file": ("test.pdf", payload, "application/pdf")},
        )
    assert response.status_code == 200
    assert response.json().get("job_id")


def test_southwest_compact_zip_names_are_accepted():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("LAXFOP.TXT", "PAIRING 1234\n")
        archive.writestr("LAXFOL.TXT", "LINE 1 1234\n")
        archive.writestr("LAXFOS.TXT", "SENIORITY\n")
        archive.writestr("LAXFOC.TXT", "COVER\n")
    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            data={"airline": "southwest", "context": "southwest", "profile_json": json.dumps({})},
            files={"file": ("LAXFOA.ZIP", data.getvalue(), "application/zip")},
        )
    assert response.status_code == 200
    assert response.json().get("job_id")


def test_rejects_file_with_pdf_extension_but_invalid_signature():
    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            data={"airline": "delta", "context": "delta", "profile_json": json.dumps({})},
            files={"file": ("not-really.pdf", b"plain text", "application/pdf")},
        )
    assert response.status_code == 400
    assert "valid PDF" in response.json()["detail"]


def test_rejects_damaged_zip():
    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            data={"airline": "southwest", "context": "southwest", "profile_json": json.dumps({})},
            files={"file": ("package.zip", b"PK-not-a-zip", "application/zip")},
        )
    assert response.status_code == 400
    assert "valid ZIP" in response.json()["detail"]
