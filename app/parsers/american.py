from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.airlines import decode_equipment, get_aircraft_display_name
from app.pay import format_pay_minutes, parse_clock_minutes

from .base import Leg, Pairing


SECTION = re.compile(r"^(?P<base>[A-Z]{3})(?:\s+(?P<satellite>ONT|SAN|SNA))?\s+(?P<fleet>777|787|320|737)$")
SEQUENCE = re.compile(
    r"^SEQ\s+(?P<id>\d{3,5})\W*(?P<operations>\d+)\s+OPS\s+POSN\s+(?P<positions>.+?)"
    r"(?:\s+MO\s+TU\s+WE\s+TH\s+FR\s+SA\s+SU)?$",
    re.I,
)
LEG = re.compile(
    r"^(?P<duty>\d+)\s+(?P<departure_day>\d+)/(?P<arrival_day>\d+)\s+"
    r"(?P<equipment>[A-Z0-9]{2,4})\s+(?P<flight>\d+[A-Z]?)\s+"
    r"(?P<departure>[A-Z]{3})\s+(?P<departure_local>\d{4})/(?P<departure_home>\d{4})\s+"
    r"(?:(?P<meal>[A-Z])\s+)?(?P<arrival>[A-Z]{3})\s+"
    r"(?P<arrival_local>\d{4})/(?P<arrival_home>\d{4})(?:\s+(?P<tail>.*))?$"
)
REPORT = re.compile(r"^RPT\s+(\d{4})/(\d{4})")
RELEASE = re.compile(r"^RLS\s+(\d{4})/(\d{4})")
TOTAL = re.compile(r"^TTL\s+(.+)$")
CALENDAR = re.compile(r"(?:^|\s)((?:--|\d{1,2})(?:\s+(?:--|\d{1,2})){6})$")
MONTH_YEAR = re.compile(
    r"\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(20\d{2})\b",
    re.I,
)
MONTHS = {
    name: index
    for index, name in enumerate(
        ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"),
        1,
    )
}
MONTH_ABBR = {name[:3]: index for name, index in MONTHS.items()}
QUALIFIER = re.compile(r"\b(?:SPANISH|JAPANESE|ITALIAN)\s+OPERATION\b|\bREPLACES\s+PRIOR\s+MONTH\b", re.I)
PAGE_MARKER = re.compile(r"^<<<CREWBIDIQ_PAGE:(\d+)>>>$")


def _normalize_line(line: str) -> str:
    line = "".join(char for char in line if unicodedata.category(char) != "Cf")
    line = line.translate(str.maketrans({"−": "-", "–": "-", "—": "-", " ": " ", "•": "", "‧": ""}))
    return re.sub(r"[ \t]+", " ", line).strip()


def _normalize(text: str) -> str:
    return "\n".join(_normalize_line(line) for line in text.replace("\r", "\n").splitlines())


def detect(text: str) -> float:
    normalized = _normalize(text).upper()
    score = 0.0
    if "DLCL/DHBT" in normalized and "ALCL/AHBT" in normalized:
        score += 0.45
    if "SYNTH" in normalized and "TPAY" in normalized and "TAFB" in normalized:
        score += 0.25
    if len(re.findall(r"(?m)^SEQ\s+\d{3,5}\b", normalized)) >= 2:
        score += 0.25
    if "POSN CA FO" in normalized or "POSN FB" in normalized:
        score += 0.05
    return min(score, 1.0)


def _month_context(text: str) -> tuple[int | None, int | None]:
    match = MONTH_YEAR.search(text)
    if not match:
        return None, None
    return MONTHS[match.group(1).upper()], int(match.group(2))


def _calendar_dates(lines: list[str], month: int | None, year: int | None) -> list[str]:
    days: list[int] = []
    for line in lines:
        match = CALENDAR.search(line)
        calendar_text = match.group(1) if match else ""
        report = re.match(r"^RPT\s+\d{4}/\d{4}(?:\s+(.*))?$", line)
        if report and report.group(1):
            calendar_text += " " + report.group(1)
        for value in re.findall(r"\b\d{1,2}\b", calendar_text):
            day = int(value)
            if 1 <= day <= 31 and day not in days:
                days.append(day)
    days.sort()
    replacement_dates: list[str] = []
    if year:
        for line in lines:
            for day, month_name in re.findall(r"\bSEQUENCE\s+\d+/(\d{1,2})([A-Z]{3})\b", line.upper()):
                replacement_dates.append(f"{year:04d}-{MONTH_ABBR[month_name]:02d}-{int(day):02d}")
    if replacement_dates:
        return list(dict.fromkeys(replacement_dates))
    if month and year:
        return [f"{year:04d}-{month:02d}-{day:02d}" for day in days]
    return [str(day) for day in days]


def _positions(value: str) -> tuple[list[str], list[str], str]:
    value = re.sub(r"\bFO\s+C\b", "FO FC", value, flags=re.I)
    qualifiers = [re.sub(r"\s+", " ", item.upper()) for item in QUALIFIER.findall(value)]
    clean = QUALIFIER.sub(" ", value)
    positions = list(dict.fromkeys(re.findall(r"\b(?:CA|FO|FB|FC)\b", clean.upper())))
    return positions, qualifiers, re.sub(r"\s+", " ", value).strip()


def _hotel(line: str) -> dict[str, str | None] | None:
    match = re.match(r"^([A-Z]{3})\s+(.+)$", line)
    if not match:
        return None
    duration = re.search(r"\b(\d{1,3}\.\d{2})\b", match.group(2))
    if not duration:
        return None
    before_duration = match.group(2)[: duration.start()].strip()
    phone_match = re.search(r"(?:^|\s)(\+?\d[\d() -]{5,})$", before_duration)
    phone = phone_match.group(1).strip() if phone_match else None
    hotel = before_duration[: phone_match.start()].strip() if phone_match else before_duration
    return {"city": match.group(1), "duration": duration.group(1), "hotel": hotel or before_duration, "hotel_phone": phone}


def _transport(line: str) -> dict[str, str | None] | None:
    upper = line.upper()
    if not (upper.startswith("SHUTTLE") or "TRANS INFO" in upper or upper.startswith("TRANSPORT")):
        return None
    phone_match = re.search(r"(?:^|\s)(\+?\d[\d() -]{5,})$", line)
    phone = phone_match.group(1).strip() if phone_match else None
    provider = line[: phone_match.start()].strip() if phone_match else line.strip()
    return {"transportation_provider": provider or None, "transportation_phone": phone}


def _totals(line: str) -> tuple[str | None, str | None, str | None, str | None]:
    values = re.findall(r"\b\d{1,3}\.\d{2}\b", line)
    if len(values) < 3:
        return None, None, None, None
    block = values[0]
    synthetic = values[1] if len(values) >= 2 else None
    trip_pay = values[2] if len(values) >= 3 else None
    tafb = values[3] if len(values) >= 4 else None
    return block, synthetic, trip_pay, tafb


def _duty_values(line: str) -> dict[str, str | None]:
    values = re.findall(r"\b\d{1,3}\.\d{2}\b", line)
    names = ("block", "synthetic", "raw_tpay", "duty", "fdp")
    parsed = {name: values[index] if index < len(values) else None for index, name in enumerate(names)}
    parsed["total_pay"] = format_pay_minutes(parse_clock_minutes(parsed["raw_tpay"]))
    parsed["trip_pay"] = parsed["raw_tpay"]  # Backward-compatible raw source alias.
    return parsed


def _clock_minutes(value: str | None) -> int | None:
    token = str(value or "").strip()
    if not re.fullmatch(r"\d{4}", token):
        return None
    hours, minutes = int(token[:2]), int(token[2:])
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def _annotate_duty_calendar_days(duty_periods: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    """Infer report/release day offsets from AA D/A markers and local clocks."""
    first_report_day: int | None = None
    final_release_day: int | None = None
    for duty in duty_periods:
        legs = duty.get("legs") or []
        if not legs:
            continue
        first_leg, last_leg = legs[0], legs[-1]
        report_day = int(first_leg["departure_day"])
        report_minutes = _clock_minutes(duty.get("report_local"))
        departure_minutes = _clock_minutes(first_leg.get("departure_time"))
        if report_minutes is not None and departure_minutes is not None and report_minutes > departure_minutes:
            report_day -= 1

        release_day = int(last_leg["arrival_day"])
        release_minutes = _clock_minutes(duty.get("release_local"))
        arrival_minutes = _clock_minutes(last_leg.get("arrival_time"))
        if release_minutes is not None and arrival_minutes is not None and release_minutes < arrival_minutes:
            release_day += 1

        duty["report_day"] = report_day
        duty["release_day"] = release_day
        first_report_day = report_day if first_report_day is None else min(first_report_day, report_day)
        final_release_day = release_day if final_release_day is None else max(final_release_day, release_day)
    return first_report_day, final_release_day


def _build_sequence(current: dict[str, Any], month: int | None, year: int | None) -> dict[str, Any]:
    lines: list[str] = current["lines"]
    legs: list[Leg] = []
    leg_details: list[dict[str, Any]] = []
    layovers: list[dict[str, Any]] = []
    reports: list[dict[str, str]] = []
    releases: list[dict[str, str]] = []
    duty_periods: list[dict[str, Any]] = []
    active_duty: dict[str, Any] | None = None
    pending_layover: dict[str, Any] | None = None
    awaiting_layover = False
    block_total = synthetic_total = raw_total_pay = tafb = None

    for line in lines[1:]:
        report = REPORT.match(line)
        if report:
            report_pair = {"local": report.group(1), "home_base": report.group(2)}
            reports.append(report_pair)
            active_duty = {
                "number": len(duty_periods) + 1,
                "report_local": report.group(1),
                "report_home_base": report.group(2),
                "legs": [],
            }
            duty_periods.append(active_duty)
            awaiting_layover = False
            pending_layover = None
            continue
        release = RELEASE.match(line)
        if release:
            release_pair = {"local": release.group(1), "home_base": release.group(2)}
            releases.append(release_pair)
            if active_duty is None:
                active_duty = {"number": len(duty_periods) + 1, "legs": []}
                duty_periods.append(active_duty)
            active_duty.update(
                {
                    "release_local": release.group(1),
                    "release_home_base": release.group(2),
                    **_duty_values(line),
                    "leg_count": len(active_duty["legs"]),
                    "working_leg_count": sum(not leg["deadhead"] for leg in active_duty["legs"]),
                    "deadhead_leg_count": sum(leg["deadhead"] for leg in active_duty["legs"]),
                }
            )
            awaiting_layover = True
            continue
        total = TOTAL.match(line)
        if total:
            block_total, synthetic_total, raw_total_pay, tafb = _totals(total.group(1))
            awaiting_layover = False
            continue
        leg = LEG.match(line)
        if leg:
            raw_flight = leg.group("flight").upper()
            deadhead = raw_flight.endswith("D")
            parsed_leg = Leg(
                day=leg.group("duty"),
                deadhead=deadhead,
                flight=raw_flight[:-1] if deadhead else raw_flight,
                departure=leg.group("departure"),
                departure_time=leg.group("departure_local"),
                arrival=leg.group("arrival"),
                arrival_time=leg.group("arrival_local"),
                block=None if deadhead else (re.search(r"\b\d{1,2}\.\d{2}\b", leg.group("tail") or "").group(0) if re.search(r"\b\d{1,2}\.\d{2}\b", leg.group("tail") or "") else None),
                aircraft=leg.group("equipment"),
            )
            legs.append(parsed_leg)
            detail = parsed_leg.__dict__.copy()
            equipment = decode_equipment("american", leg.group("equipment"))
            detail.update(
                {
                    "raw_flight": raw_flight,
                    "equipment_code": leg.group("equipment"),
                    "departure_day": int(leg.group("departure_day")),
                    "arrival_day": int(leg.group("arrival_day")),
                    "departure_home_time": leg.group("departure_home"),
                    "arrival_home_time": leg.group("arrival_home"),
                    "pay_values_raw": leg.group("tail") or "",
                    "deadhead_provisional": deadhead,
                    "aircraft_name": equipment.aircraft,
                    "aircraft_display_name": get_aircraft_display_name("american", leg.group("equipment")),
                    "equipment_known": equipment.known,
                }
            )
            leg_details.append(detail)
            if active_duty is None:
                active_duty = {"number": len(duty_periods) + 1, "legs": []}
                duty_periods.append(active_duty)
            active_duty["legs"].append(detail)
            awaiting_layover = False
            pending_layover = None
            continue
        if awaiting_layover:
            hotel = _hotel(line)
            if hotel:
                hotel.update({"transportation_provider": None, "transportation_phone": None})
                layovers.append(hotel)
                pending_layover = hotel
                if active_duty is not None:
                    active_duty["layover_after"] = hotel
                awaiting_layover = False
                continue
        if pending_layover:
            transport = _transport(line)
            if transport:
                pending_layover.update(transport)

    dates = _calendar_dates(lines, month, year)
    # RPT text can repeat in page furniture or malformed extraction. A duty
    # period is production-valid only when it owns legs and has both endpoints.
    validated_duty_periods = [
        duty for duty in duty_periods
        if duty.get("legs") and duty.get("report_local") and duty.get("release_local")
    ]
    first_report_day, final_release_day = _annotate_duty_calendar_days(validated_duty_periods)
    leg_days = [
        day
        for leg in leg_details
        for day in (int(leg["departure_day"]), int(leg["arrival_day"]))
    ]
    span_start_candidates = leg_days + ([first_report_day] if first_report_day is not None else [])
    span_end_candidates = leg_days + ([final_release_day] if final_release_day is not None else [])
    calendar_span_days = (
        max(span_end_candidates) - min(span_start_candidates) + 1
        if span_start_candidates and span_end_candidates else len(validated_duty_periods)
    )
    sequence_days = max(calendar_span_days, 1) if legs else 0
    positions, qualifiers, position_text = _positions(current["position_text"])
    raw = "\n".join(current.get("raw_lines") or lines).strip()
    total_pay = format_pay_minutes(parse_clock_minutes(raw_total_pay))
    confidence = 0.98 if legs and total_pay and len(dates) == current["operations"] else (0.92 if legs else 0.55)
    pairing = Pairing(
        pairing_id=current["id"],
        raw=raw,
        legs=legs,
        layovers=[],
        credit=raw_total_pay,  # Legacy API alias; pilot-facing AA output uses total_pay.
        tafb=tafb,
        checkin=validated_duty_periods[0]["report_local"] if validated_duty_periods else None,
        release=validated_duty_periods[-1]["release_local"] if validated_duty_periods else None,
        effective=", ".join(dates) or None,
        parser="american_cockpit_sequence",
        confidence=confidence,
    ).to_dict()
    pairing["legs"] = leg_details
    pairing["layovers"] = layovers
    equipment_codes = list(dict.fromkeys(leg["equipment_code"] for leg in leg_details))
    known_equipment = [decode_equipment("american", code).known for code in equipment_codes]
    mapping_status = "mapped" if known_equipment and all(known_equipment) else ("partially_mapped" if any(known_equipment) else "raw_unmapped")
    section_parts = [current["section"].get(key) for key in ("base", "satellite", "fleet")]
    pairing.update(
        {
            "airline": "american",
            "source_terminology": "sequence",
            "operations": current["operations"],
            "positions": positions,
            "position_text": position_text,
            "operation_qualifiers": qualifiers,
            "base": current["section"].get("base"),
            "satellite": current["section"].get("satellite"),
            "fleet": current["section"].get("fleet"),
            "fleet_section": " ".join(part for part in section_parts if part),
            "start_dates": dates,
            "source_pdf_page": current.get("source_pdf_page"),
            "block_total": block_total,
            "synthetic_total": synthetic_total,
            "trip_pay_total": raw_total_pay,
            "raw_total_pay": raw_total_pay,
            "total_pay": total_pay,
            "source_total_pay_label": "TPAY" if raw_total_pay else None,
            "equipment_codes": equipment_codes,
            "aircraft_display_names": [get_aircraft_display_name("american", code) for code in equipment_codes],
            "equipment_mapping_status": mapping_status,
            "duty_reports": reports,
            "duty_releases": releases,
            "duty_periods": validated_duty_periods,
            "sequence_days": sequence_days,
            "duty_period_count": len(validated_duty_periods),
            "overnight_count": len(layovers),
            "calendar_span_days": calendar_span_days,
            "first_report": validated_duty_periods[0]["report_local"] if validated_duty_periods else None,
            "first_report_day": first_report_day,
            "final_release": validated_duty_periods[-1]["release_local"] if validated_duty_periods else None,
            "final_release_day": final_release_day,
            "total_flight_segments": len(leg_details),
            "normalization_diagnostics": {
                "raw_sequence_id": current["id"],
                "parsed_sequence_days": sequence_days,
                "calendar_span_days": calendar_span_days,
                "duty_period_count": len(validated_duty_periods),
                "overnight_count": len(layovers),
                "first_report": validated_duty_periods[0]["report_local"] if validated_duty_periods else None,
                "final_release": validated_duty_periods[-1]["release_local"] if validated_duty_periods else None,
                "length_basis": "report_to_release_calendar_span",
            },
        }
    )
    return pairing


def _parse_cockpit_package(text: str) -> list[dict[str, Any]]:
    normalized = _normalize(text)
    month, year = _month_context(normalized)
    section: dict[str, str | None] = {"base": None, "satellite": None, "fleet": None}
    current: dict[str, Any] | None = None
    current_page: int | None = None
    results: list[dict[str, Any]] = []

    for raw_line in text.replace("\r", "\n").splitlines():
        line = _normalize_line(raw_line)
        page_marker = PAGE_MARKER.match(line)
        if page_marker:
            current_page = int(page_marker.group(1))
            continue
        section_match = SECTION.match(line)
        if section_match:
            section = section_match.groupdict()
            continue
        header = SEQUENCE.match(line)
        if header:
            if current:
                results.append(_build_sequence(current, month, year))
            current = {
                "id": header.group("id"),
                "operations": int(header.group("operations")),
                "position_text": header.group("positions"),
                "section": section.copy(),
                "source_pdf_page": current_page,
                "lines": [line],
                "raw_lines": [raw_line.rstrip()],
            }
            continue
        if current:
            current["lines"].append(line)
            current["raw_lines"].append(raw_line.rstrip())
    if current:
        results.append(_build_sequence(current, month, year))
    return results


LEGACY_HEADERS = [
    re.compile(r"(?mi)^\s*(?:SEQ|SEQUENCE|TRIP|PAIRING)\s*[:#]?\s*([A-Z]?\d{3,6})\b"),
    re.compile(r"(?mi)^\s*([A-Z]?\d{4,6})\s+(?:\d{1,2}D|\d+\s*DAY)\b"),
]


def _legacy_parse(text: str) -> list[dict[str, Any]]:
    headers: list[tuple[int, str]] = []
    for pattern in LEGACY_HEADERS:
        headers.extend((match.start(), match.group(1).upper()) for match in pattern.finditer(text))
    headers.sort()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, (start, pairing_id) in enumerate(headers):
        if pairing_id in seen:
            continue
        end = headers[index + 1][0] if index + 1 < len(headers) else len(text)
        results.append(Pairing(pairing_id, text[start:end], [], [], parser="american_sequence_fallback", confidence=0.35).to_dict())
        seen.add(pairing_id)
    return results


def parse(text: str) -> list[dict[str, Any]]:
    if "DLCL/DHBT" in text.upper() and "ALCL/AHBT" in text.upper():
        parsed = _parse_cockpit_package(text)
        if parsed:
            return parsed
    return _legacy_parse(_normalize(text))
