from pathlib import Path
import fitz
from app.parsers import delta


def test_delta_august_package():
    path = Path("/mnt/data/DTW320 AUG 2026.pdf")
    if not path.exists():
        return
    doc = fitz.open(path)
    text = "\n".join(page.get_text("text", sort=True) for page in doc)
    parsed = delta.parse(text)
    ids = {p["id"] for p in parsed}
    assert "4913" in ids
    assert len(parsed) > 200
    assert sum(bool(p["legs"]) for p in parsed) > 150
