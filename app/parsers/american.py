from __future__ import annotations

import re
import unicodedata
from typing import Any

from .base import Leg, Layover, Pairing


SECTION = re.compile(r"^(?P<base>[A-Z]{3})(?:\s+(?P<satellite>ONT|SAN|SNA))?\s+(?P<fleet>777|787|320|737)$")
SEQUENCE = re.compile(
    r"^SEQ\s+(?P<id>\d{3,5})\W*(?P<operations>\d+)\s+OPS\s+POSN\s+(?P<positions>.+?)"
    r"(?:\s+MO\s+TU\s+WE\s+TH\s+FR\s+SA\s+SU)?$",
    re.I,
)
LEG = re.compile(
    r"^(?P<duty>\d+)\s+(?P<departure_day>\d+)/(?P<arrival_day>\d+)\s+"
    r"(?P<equipment>[A-Z0-9]{2,3})\s+(?P<flight>\d+[A-Z]?)\s+"
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


def _normalize(text: str) -> str:
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    text = text.translate(str.maketrans({"−": "-", "–": "-", "—": "-", " ": " ", "•": "", "‧": ""}))
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r", "\n").splitlines())


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


def _hotel(line: str) -> tuple[str, str, str] | None:
    match = re.match(r"^([A-Z]{3})\s+(.+)$", line)
    if not match:
        return None
    duration = re.search(r"\b(\d{1,3}\.\d{2})\b", match.group(2))
    if not duration:
        return None
    before_duration = match.group(2)[: duration.start()].strip()
    hotel = re.sub(r"\s+\+?\d[\d() -]{5,}$", "", before_duration).strip()
    return match.group(1), duration.group(1), hotel or before_duration


def _totals(line: str) -> tuple[str | None, str | None, str | None, str | None]:
    values = re.findall(r"\b\d{1,3}\.\d{2}\b", line)
    if len(values) < 3:
        return None, None, None, None
    block = values[0]
    synthetic = values[1] if len(values) >= 2 else None
    trip_pay = values[2] if len(values) >= 3 else None
    tafb = values[3] if len(values) >= 4 else None
    return block, synthetic, trip_pay, tafb


def _build_sequence(current: dict[str, Any], month: int | None, year: int | None) -> dict[str, Any]:
    lines: list[str] = current["lines"]
    legs: list[Leg] = []
    leg_details: list[dict[str, Any]] = []
    layovers: list[Layover] = []
    reports: list[dict[str, str]] = []
    releases: list[dict[str, str]] = []
    awaiting_layover = False
    block_total = synthetic_total = trip_pay_total = tafb = None

    for line in lines[1:]:
        report = REPORT.match(line)
        if report:
            reports.append({"local": report.group(1), "home_base": report.group(2)})
            awaiting_layover = False
            continue
        release = RELEASE.match(line)
        if release:
            releases.append({"local": release.group(1), "home_base": release.group(2)})
            awaiting_layover = True
            continue
        total = TOTAL.match(line)
        if total:
            block_total, synthetic_total, trip_pay_total, tafb = _totals(total.group(1))
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
                }
            )
            leg_details.append(detail)
            awaiting_layover = False
            continue
        if awaiting_layover:
            hotel = _hotel(line)
            if hotel:
                layovers.append(Layover(city=hotel[0], duration=hotel[1], hotel=hotel[2]))
                awaiting_layover = False

    dates = _calendar_dates(lines, month, year)
    positions, qualifiers, position_text = _positions(current["position_text"])
    raw = "\n".join(lines).strip()
    confidence = 0.98 if legs and trip_pay_total and len(dates) == current["operations"] else (0.92 if legs else 0.55)
    pairing = Pairing(
        pairing_id=current["id"],
        raw=raw,
        legs=legs,
        layovers=layovers,
        credit=trip_pay_total,
        tafb=tafb,
        checkin=reports[0]["local"] if reports else None,
        release=releases[-1]["local"] if releases else None,
        effective=", ".join(dates) or None,
        parser="american_cockpit_sequence",
        confidence=confidence,
    ).to_dict()
    pairing["legs"] = leg_details
    pairing.update(
        {
            "operations": current["operations"],
            "positions": positions,
            "position_text": position_text,
            "operation_qualifiers": qualifiers,
            "base": current["section"].get("base"),
            "satellite": current["section"].get("satellite"),
            "fleet": current["section"].get("fleet"),
            "start_dates": dates,
            "block_total": block_total,
            "synthetic_total": synthetic_total,
            "trip_pay_total": trip_pay_total,
            "equipment_codes": list(dict.fromkeys(leg["equipment_code"] for leg in leg_details)),
            "equipment_mapping_status": "raw_unmapped",
            "duty_reports": reports,
            "duty_releases": releases,
        }
    )
    return pairing


def _parse_cockpit_package(text: str) -> list[dict[str, Any]]:
    normalized = _normalize(text)
    month, year = _month_context(normalized)
    section: dict[str, str | None] = {"base": None, "satellite": None, "fleet": None}
    current: dict[str, Any] | None = None
    results: list[dict[str, Any]] = []

    for line in normalized.splitlines():
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
                "lines": [line],
            }
            continue
        if current:
            current["lines"].append(line)
            if TOTAL.match(line):
                # Keep the calendar fields on the total row, then close the sequence.
                results.append(_build_sequence(current, month, year))
                current = None
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
