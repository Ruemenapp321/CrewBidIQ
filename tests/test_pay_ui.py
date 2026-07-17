from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_classic_cards_show_delta_total_pay_and_southwest_tfp():
    script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    pay_section = script.split("function payPresentation", 1)[1].split("function metricStrip", 1)[0]
    assert "line ? 'Line TFP' : 'Pairing TFP'" in pay_section
    assert "'Carry-out TFP'" in pay_section
    assert "snapshotLabel: 'Total Pay'" in pay_section
    assert "['Total Pay', item.total_pay]" in pay_section
    assert "['Additional Pay', item.additional_pay]" in pay_section
    assert "['EDP', 'HOL', 'SIT']" in pay_section
    assert "if (airline === 'american')" in pay_section
    assert "['Total Pay', item.total_pay]" in pay_section
    assert "Soft credit" not in script
    assert '<details class="timeline-details"><summary>Timeline and duty legs</summary>' in script


def test_labs_uses_airline_specific_pay_language_and_navblue_plan():
    script = (ROOT / "app" / "static" / "labs.js").read_text(encoding="utf-8")
    assert "TFP and efficiency" in script
    assert "Total Pay and efficiency" in script
    assert "airline === 'delta' || airline === 'american'" in script
    assert "Credit and efficiency" not in script
    assert "NAVBLUE PBS REQUEST PLAN" in script
    assert "/navblue-plan" in script
    assert "results.slice(0, 10)" not in script
