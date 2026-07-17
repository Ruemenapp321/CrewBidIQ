from __future__ import annotations

import io
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

from app.airlines import get_airline_terminology
from app.canonical import canonical_value, model_from_item


LABELS = {"excellent": "★★★★★ Excellent", "strong": "★★★★ Strong", "good": "★★★ Good", "fair": "★★ Fair", "low": "★ Low"}


def _match_label(item: dict[str, Any]) -> str:
    return str(item.get("match_label") or LABELS.get(item.get("match_level", "fair"), "★★ Fair"))


def _text(value: Any) -> str:
    return str(value if value not in (None, "") else "—")


def _cell(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_text(value)), style)


def _pay_rows(item: dict[str, Any], airline: str) -> list[list[Any]]:
    model = model_from_item(item)
    legacy_components = item.get("pay_components") or {}
    pay = {
        "trip_credit": item.get("trip_credit") or item.get("credit"),
        "edp": item.get("edp", legacy_components.get("EDP")),
        "hol": item.get("hol", legacy_components.get("HOL")),
        "sit": item.get("sit", legacy_components.get("SIT")),
        "additional_pay": item.get("additional_pay"),
        "total_pay": item.get("total_pay"),
        "unresolved_pay_tokens": item.get("unresolved_pay_tokens") or [],
        **(model.get("pay_breakdown") or item.get("pay_breakdown") or {}),
    }
    tfp = {
        key: item.get(key)
        for key in ("pairing_tfp", "line_tfp", "monthly_tfp", "carry_out_tfp", "tfp_per_duty_period", "tfp_per_day_away")
    } | (model.get("tfp") or item.get("tfp") or {})
    if airline == "southwest":
        label = "Line TFP" if item.get("item_type") == "line" else "Pairing TFP"
        value = item.get("line_tfp") if item.get("item_type") == "line" else tfp.get("pairing_tfp", item.get("pairing_tfp"))
        return [
            [label, value],
            ["Carry-out TFP", item.get("carry_out_tfp")],
            ["TFP per duty period", tfp.get("tfp_per_duty_period", item.get("tfp_per_duty_period"))],
            ["TFP per day away", tfp.get("tfp_per_day_away", item.get("tfp_per_day_away"))],
        ]
    if airline == "delta":
        rows = [["Trip Credit", pay.get("trip_credit")], ["Additional Pay", pay.get("additional_pay")]]
        components = {"EDP": pay.get("edp"), "HOL": pay.get("hol"), "SIT": pay.get("sit")}
        rows.extend([[label, components[label]] for label in ("EDP", "HOL", "SIT") if components[label] is not None])
        rows.append(["Total Pay", pay.get("total_pay")])
        unresolved = pay.get("unresolved_pay_tokens") or []
        unknown = item.get("unknown_pay_components") or {}
        if unresolved or unknown:
            rows.append(["Unmapped source pay", ", ".join(unresolved) or ", ".join(f"{label} {value}" for label, value in unknown.items())])
        return rows
    if airline == "american":
        return [["Total Pay", pay.get("total_pay", item.get("total_pay"))]]
    return [["Credit", pay.get("trip_credit", item.get("credit"))]]


def build_bid_report(results: list[dict[str, Any]], profile: dict[str, Any], airline: str, filename: str) -> bytes:
    out = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Cover", parent=styles["Title"], fontSize=30, leading=36, textColor=colors.HexColor("#087cff"), alignment=TA_CENTER, spaceAfter=18))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#52657a")))
    styles.add(ParagraphStyle(name="Raw", fontName="Courier", fontSize=6.5, leading=8, backColor=colors.HexColor("#f3f6fa"), borderPadding=8))
    terminology = get_airline_terminology(airline)
    doc = SimpleDocTemplate(out, pagesize=letter, rightMargin=.55*inch, leftMargin=.55*inch, topMargin=.55*inch, bottomMargin=.55*inch, title="CrewBidIQ Bid Analysis")
    story: list[Any] = [Spacer(1, 1.5*inch), Paragraph("CrewBidIQ", styles["Cover"]), Paragraph("Monthly Bid Analysis", styles["Title"]), Spacer(1, .25*inch), Paragraph(f"{airline.title()} • {_text(filename)}", styles["Heading2"]), Spacer(1, 2*inch), Paragraph(f"Find the {terminology.plural.lower()} that fit your life.", styles["Heading2"]), PageBreak()]

    story += [Paragraph("Your preferences", styles["Heading1"])]
    pref_rows = [[_cell(key.replace("_", " ").title(), styles["Small"]), _cell(", ".join(map(str, value)) if isinstance(value, list) else value, styles["Small"])] for key, value in profile.items() if key != "weights" and value not in (None, "", [], False)]
    if pref_rows:
        table = Table(pref_rows, colWidths=[2.15*inch, 4.2*inch])
        table.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .25, colors.HexColor("#dbe4ef")), ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#f3f6fa")), ("VALIGN", (0,0), (-1,-1), "TOP"), ("FONT", (0,0), (-1,-1), "Helvetica", 8), ("PADDING", (0,0), (-1,-1), 5)]))
        story.append(table)
    else:
        story.append(Paragraph("No preferences were supplied.", styles["BodyText"]))

    eligible_results = [item for item in results if item.get("eligible") is True]
    near_results = [item for item in results if item.get("eligible") is False]
    story += [PageBreak(), Paragraph(terminology.recommended, styles["Heading1"])]
    for index, item in enumerate(eligible_results[:10], 1):
        rating = _match_label(item)
        explanation = list(item.get("qualification_reasons") or ["No hard requirement was violated."])
        explanation.extend(item.get("matched_preferences") or [])
        story += [Paragraph(f"{index}. {item.get('display_label', terminology.singular)} {item.get('pairing')} — {rating}", styles["Heading2"]), Paragraph("; ".join(dict.fromkeys(explanation)), styles["BodyText"]), Spacer(1, 8)]
    if near_results:
        story += [Spacer(1, 12), Paragraph("Near Matches", styles["Heading1"]), Paragraph("These options miss at least one hard requirement and are not part of the eligible recommendation list.", styles["BodyText"])]
        for index, item in enumerate(near_results[:5], 1):
            story += [Paragraph(f"{index}. {item.get('display_label', terminology.singular)} {item.get('pairing')} — Near Match", styles["Heading2"]), Paragraph("; ".join(item.get("eligibility_violations") or ["Review required criteria."]), styles["BodyText"]), Spacer(1, 8)]

    for index, item in enumerate(results[:25], 1):
        model = model_from_item(item)
        item = {
            **item,
            "trip_length": model.get("trip_length_days", item.get("trip_length")),
            "tafb": model.get("tafb", item.get("tafb")),
            "layovers": canonical_value(item, "layovers", []),
            "operating_dates": canonical_value(item, "operating_dates", []),
        }
        item_terminology = get_airline_terminology(item.get("airline") or airline)
        story += [PageBreak(), Paragraph(f"{item.get('display_label', item_terminology.singular)} {item.get('pairing')}", styles["Heading1"]), Paragraph(_match_label(item), styles["Heading2"])]
        equipment = ", ".join(item.get("aircraft_display_names") or item.get("equipment_codes", [])) or "—"
        wocl_legs = ", ".join(
            f"{leg.get('departure', 'Unknown')} {leg.get('departure_time', '')}".strip()
            for leg in item.get("redeye_legs", [])
        ) or "None"
        item_airline = item.get("airline") or airline
        fatigue = item.get("fatigue_index") or {}
        hold = item.get("hold_outlook") or {}
        row_values = _pay_rows(item, item_airline) + [["Trip length", f"{item.get('trip_length')} days" if item.get("trip_length") else "N/A"], ["TAFB", item.get("tafb")], ["Layovers", ", ".join(x.get("city", "") for x in item.get("layovers", [])) or "None"], ["Equipment", equipment], ["Legs by duty day", " • ".join(map(str, item.get("duty_legs", []))) or "—"], ["WOCL departures", wocl_legs], ["Fatigue Index", f"{fatigue.get('level')} ({fatigue.get('confidence')} confidence)" if fatigue else "Insufficient Data"], ["Fatigue factors", "; ".join(fatigue.get("contributing_factors", [])) or "None identified"], ["Fatigue mitigations", "; ".join(fatigue.get("mitigating_factors", [])) or "None identified"], ["Hold outlook", f"{hold.get('outlook')} ({hold.get('confidence')} confidence) — {hold.get('estimate_basis')}" if hold else "Insufficient data"], ["Operating dates", ", ".join(item.get("operating_dates", [])) or "Not available"], ["Why it qualified", "; ".join(item.get("qualification_reasons", [])) or ("Near Match only" if item.get("eligible") is False else "No hard requirement was violated")], ["Matched preferences", "; ".join(item.get("matched_preferences", [])) or "None"], ["Compromises", "; ".join(item.get("compromises", [])) or "None"], ["Requirements not met", "; ".join(item.get("eligibility_violations", [])) or "None"], ["Trip facts", "; ".join(item.get("neutral_attributes", [])) or "Not available"]]
        if not item.get("operating_dates"):
            row_values = [row for row in row_values if row[0] != "Operating dates"]
        rows = [[_cell(label, styles["Small"]), _cell(value, styles["Small"])] for label, value in row_values]
        table = Table(rows, colWidths=[1.35*inch, 5.05*inch])
        table.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .25, colors.HexColor("#dbe4ef")), ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#f3f6fa")), ("VALIGN", (0,0), (-1,-1), "TOP"), ("FONT", (0,0), (-1,-1), "Helvetica", 8), ("PADDING", (0,0), (-1,-1), 5)]))
        story += [table, Spacer(1, 12), Paragraph(item_terminology.view_original, styles["Heading2"]), Preformatted(item.get("original_display") or "Not available", styles["Raw"])]

    pay_definition = "Southwest TFP means Trips for Pay; Line TFP, carry-out TFP, and efficiency remain distinct. " if airline == "southwest" else ("Delta Total Pay is Trip Credit plus confidently parsed EDP, HOL, and SIT; absent components are not assumed to be zero. " if airline == "delta" else "")
    story += [PageBreak(), Paragraph("Definitions", styles["Heading1"]), Paragraph(f"{pay_definition}Layover: an overnight or contractual rest location, not every airport operated through. Duty legs: working flight segments within each duty period. TAFB: total time away from base. Redeye: a parsed flight leg departing during the Window of Circadian Low (WOCL), 02:00 through 05:59 local departure time. Fatigue Index: a schedule-based planning signal that is separate from FAR 117 legality. Hold Outlook: an inventory-based category, not an award probability, unless validated historical award data is explicitly available. Match labels summarize eligibility and preference fit for each {terminology.singular.lower()}.", styles["BodyText"])]
    doc.build(story)
    return out.getvalue()
