from app.main import parse_southwest_lines, score_pairing, score_southwest_line
from app.parsers import southwest
from tests.test_southwest_parser import SAMPLE


LINE_SAMPLE = """Line 1    TFP    90.18
TAFB 252:10
No. DPs 13
C/O TFP 0.00   A28=1100/1945(13.20)
Line 11    TFP    103.96
TAFB 312:50
No. DPs 15
C/O TFP 8.30   A28=1100/1945(13.20)
"""


def test_laxfol_monthly_and_carry_out_tfp_parse():
    lines = parse_southwest_lines(LINE_SAMPLE, {"XA28"})
    assert len(lines) == 2
    assert lines[0]["pairing_ids"] == ["XA28"]
    assert lines[0]["monthly_tfp"] == "90.18"
    assert lines[0]["carry_out_tfp"] == "0.00"
    assert lines[0]["tfp_per_duty_period"] == "6.94"
    assert lines[1]["monthly_tfp"] == "103.96"
    assert lines[1]["carry_out_tfp"] == "8.30"


def test_southwest_line_result_uses_tfp_fields():
    pairing = southwest.parse(SAMPLE)[0]
    scored = score_pairing(pairing, {})
    line = parse_southwest_lines(LINE_SAMPLE, {"XA28"})[0]
    result = score_southwest_line(line, {"XA28": scored}, {"pay_priority": "monthly_tfp"})
    assert result["airline"] == "southwest"
    assert result["line_tfp"] == "90.18"
    assert result["carry_out_tfp"] == "0.00"
    assert result["pay_priority_value"] == 90.18
