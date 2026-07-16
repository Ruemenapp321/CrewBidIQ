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
