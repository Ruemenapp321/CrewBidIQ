from app.parsers import select_parser
import pytest


def test_auto_selects_southwest():
    text = "\n".join([f"XA{i:02d}  MO          PILOTS      REPORT AT 7:05            EFFECTIVE Aug 10" for i in range(10)])
    text += "\nTrip Credit 13.20 TFP BLK HRS 11:30 No. Legs 6 TAFB 32:00"
    _module, name = select_parser(text, "auto")
    assert name == "southwest"


def test_uncertain_auto_detection_requests_manual_selection():
    with pytest.raises(ValueError, match="uncertain"):
        select_parser("PAIRING 1234", "auto")


def test_manual_generic_selection_remains_available():
    _module, name = select_parser("PAIRING 1234", "generic")
    assert name == "generic"
