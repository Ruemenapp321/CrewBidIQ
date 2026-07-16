from app.parsers import select_parser


def test_auto_selects_southwest():
    text = "\n".join([f"XA{i:02d}  MO          PILOTS      REPORT AT 7:05            EFFECTIVE Aug 10" for i in range(10)])
    text += "\nTrip Credit 13.20 TFP BLK HRS 11:30 No. Legs 6 TAFB 32:00"
    _module, name = select_parser(text, "auto")
    assert name == "southwest"
