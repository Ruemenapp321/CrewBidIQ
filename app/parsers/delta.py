from __future__ import annotations

import hashlib
import os
import re
from calendar import monthrange
from typing import Any

from .base import Leg, Layover, Pairing
from app.airports import is_valid_airport_code
from app.pay import parse_delta_pay


PAGE_MARKER = re.compile(r"(?m)^<<<CREWBIDIQ_PAGE:(\d+)>>>\s*$")
HEADER = re.compile(r"(?m)^\s*#([A-Z]?\d{3,5})\b")
LEG = re.compile(
    r"^\s*([A-Z])?\s*(DH\s+)?(\d{1,4})?\s+([A-Z]{3})\s+(\d{4})\s+"
    r"([A-Z]{3})\s+(\d{4})\*?\s+(\d{1,2}\.\d{2})(.*)$"
)
LAYOVER = re.compile(r"(?m)^\s*([A-Z]{3})\s+(\d{1,2}\.\d{2})/([^\n]+?)\s+\d+\.\d{2}/")
CREDIT = re.compile(r"TOTAL CREDIT\s+(\d{1,2}\.\d{2})TL")
TAFB = re.compile(r"TAFB\s+(\d{1,3}\.\d{2})")
CHECKIN = re.compile(r"CHECK-IN AT\s+(\d{1,2}\.\d{2})")
EFFECTIVE = re.compile(r"EFFECTIVE\s+([^\r\n]*?)(?:CHECK-IN|$)", re.I)

MONTHS = {name: index for index, name in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
KNOWN_PAY_CODES = {"CRD", "DHD", "MCD", "TRP", "DPA", "ADG", "EDP", "SIT", "HOL"}
PAY_TOKEN = re.compile(r"^(?:\d{1,3}[.:]?\d{0,2})?(?:CRD|DHD|MCD|TRP|DPA|ADG|EDP|SIT|HOL)$", re.I)
BID_MONTH_YEAR = re.compile(
    r"\b(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|SEP(?:TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)\s+(20\d{2})\b",
    re.I,
)

EXAMPLE_SIGNALS = re.compile(
    r"\b(?:below is an example|training example|not for bidding|example|sample|illustration|for reference|published in the 350 bid package)\b",
    re.I,
)
INSTRUCTION_SIGNALS = re.compile(r"\b(?:instructions?|how to|four pilot operations|frms|bid guide)\b", re.I)
INVENTORY_HEADING = re.compile(r"\b(?:MASTER\s+PAIRINGS|PAIRING\s+INVENTORY|ROTATION\s+INVENTORY)\b", re.I)
END_SECTION = re.compile(r"\b(?:HOTEL\s+LIST|APPENDIX|TABLE\s+OF\s+CONTENTS|CONTENTS)\b", re.I)
PRODUCTION_COLUMNS = re.compile(r"\bDAY\s+FLIGHT\b.*\bDEPARTS\b.*\bARRIVES\b", re.I)

LAST_DIAGNOSTICS: list[dict[str, Any]] = []


def detect(text: str) -> float:
    score = 0.0
    up = text.upper()
    if "MASTER PAIRINGS" in up:
        score += .35
    if "TOTAL CREDIT" in up and "TAFB" in up:
        score += .25
    if "CHECK-IN AT" in up:
        score += .15
    if len(HEADER.findall(text)) >= 10:
        score += .25
    return min(score, 1.0)


def _pages(text: str) -> list[tuple[int, str]]:
    matches = list(PAGE_MARKER.finditer(text))
    if not matches:
        return [(1, text)]
    return [
        (int(match.group(1)), text[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(text)])
        for index, match in enumerate(matches)
    ]


def _heading(page: str) -> str:
    return next((line.strip() for line in page.splitlines() if line.strip()), "")[:160]


def _classify_page(page: str) -> str:
    up = page.upper()
    heading = _heading(page)
    if re.search(r"\bTABLE OF CONTENTS\b|^\s*CONTENTS\s*$", up, re.M):
        return "CONTENTS"
    if re.search(r"\bHOTEL LIST\b", up):
        return "HOTEL_LIST"
    if re.search(r"\bAPPENDIX\b", up):
        return "APPENDIX"
    if EXAMPLE_SIGNALS.search(page) or re.search(r"\b350 FOUR PILOT OPERATIONS\s*&\s*FRMS\b", heading, re.I):
        return "EXAMPLE"
    if INSTRUCTION_SIGNALS.search(heading):
        return "INSTRUCTIONS"
    if INVENTORY_HEADING.search(page) or (PRODUCTION_COLUMNS.search(page) and len(HEADER.findall(page)) >= 2):
        return "BIDABLE_INVENTORY"
    if re.search(r"\b(?:REFERENCE|GLOSSARY)\b", heading, re.I):
        return "REFERENCE"
    if not HEADER.search(page) and re.search(r"\b(?:DELTA|BID PACKAGE)\b", up):
        return "COVER"
    return "UNKNOWN"


def _package_context(text: str) -> tuple[str | None, str | None]:
    up = text.upper()
    combined = re.search(
        r"\b(ATL|BOS|DTW|JFK|LAX|LGA|MSP|SEA|SLC)\s*[-_/ ]?\s*(?:BASE\s*)?(A?3(?:19|20|21|30|50)|7[3-7][A-Z0-9]*|B\d)\b",
        up,
    )
    if combined:
        return combined.group(1), combined.group(2)
    fleet_match = re.search(r"\b(A?3(?:19|20|21|30|50)|7[3-7][A-Z0-9]*|B\d)\b", up)
    return None, fleet_match.group(1) if fleet_match else None


def _bid_month_context(text: str) -> tuple[int | None, int | None]:
    match = BID_MONTH_YEAR.search(text)
    if match:
        return MONTHS[match.group(1)[:3].upper()], int(match.group(2))
    effective_months = {
        MONTHS[token.upper()]
        for segment in EFFECTIVE.findall(text)
        for token in re.findall(r"\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?=\d{1,2}\b)", segment, re.I)
    }
    return (next(iter(effective_months)), None) if len(effective_months) == 1 else (None, None)


def _operating_date_parts(token: str, bid_month_context: tuple[int | None, int | None]) -> tuple[int | None, int, int] | None:
    value = str(token or "").strip().upper().strip(",;()")
    if not value or any(value.endswith(code) for code in KNOWN_PAY_CODES) or PAY_TOKEN.fullmatch(value):
        return None
    context_month, context_year = bid_month_context
    match = re.fullmatch(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", value)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(20\d{2}))?", value)
        if match:
            month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3)) if match.group(3) else context_year
        else:
            match = re.fullmatch(r"([A-Z]{3})(\d{1,2})(?:[-/](20\d{2}))?", value)
            if match and match.group(1) in MONTHS:
                month, day, year = MONTHS[match.group(1)], int(match.group(2)), int(match.group(3)) if match.group(3) else context_year
            else:
                match = re.fullmatch(r"(\d{1,2})([A-Z]{3})(20\d{2})?", value)
                if match and match.group(2) in MONTHS:
                    month, day, year = MONTHS[match.group(2)], int(match.group(1)), int(match.group(3)) if match.group(3) else context_year
                else:
                    match = re.fullmatch(r"(\d{1,2})", value)
                    if not match or context_month is None:
                        return None
                    month, day, year = context_month, int(match.group(1)), context_year
    if context_month is not None and month != context_month:
        return None
    if context_year is not None and year is not None and year != context_year:
        return None
    if not 1 <= month <= 12:
        return None
    max_day = monthrange(year or 2024, month)[1]
    return (year, month, day) if 1 <= day <= max_day else None


def is_valid_operating_date_token(token: str, bid_month_context: tuple[int | None, int | None]) -> bool:
    return _operating_date_parts(token, bid_month_context) is not None


def _operating_dates(block: str, bid_month_context: tuple[int | None, int | None]) -> list[str]:
    match = EFFECTIVE.search(block)
    if not match:
        return []
    candidates = re.findall(
        r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}/\d{1,2}(?:/20\d{2})?|(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{1,2}(?:[-/]20\d{2})?|\d{1,2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?:20\d{2})?|\b\d{1,2}\b|\b\d{2,3}(?:CRD|DHD|MCD|TRP|DPA|ADG|EDP|SIT|HOL)\b|\b(?:CRD|DHD|MCD|TRP|DPA|ADG|EDP|SIT|HOL)\b",
        match.group(1).upper(),
    )
    dates: list[str] = []
    for candidate in candidates:
        parts = _operating_date_parts(candidate, bid_month_context)
        if not parts:
            continue
        year, month, day = parts
        normalized = f"{year:04d}-{month:02d}-{day:02d}" if year else f"{day}{next(name for name, number in MONTHS.items() if number == month)}"
        if normalized not in dates:
            dates.append(normalized)
    return dates


def _diagnose(rotation: str, page: int, heading: str, classification: str, accepted: bool, reason: str, confidence: float) -> None:
    if os.environ.get("PARSER_DEBUG_ENABLED", "false").lower() != "true":
        return
    LAST_DIAGNOSTICS.append({
        "candidate_rotation": rotation,
        "source_page": page,
        "source_heading": heading,
        "page_classification": classification,
        "result": "ACCEPTED" if accepted else "REJECTED",
        "rejection_reason": None if accepted else reason,
        "confidence": confidence,
    })


def get_diagnostics() -> list[dict[str, Any]]:
    return list(LAST_DIAGNOSTICS)


def _valid_clock(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[01]\d|2[0-3])[0-5]\d", value))


def _duty_day_index(day: str | None) -> int | None:
    return ord(day) - ord("A") + 1 if day and re.fullmatch(r"[A-Z]", day) else None


def _parse_leg_rows(block: str, page_number: int) -> tuple[list[Leg], list[dict[str, Any]]]:
    """Parse Delta production rows without scanning arbitrary row tokens."""
    legs: list[Leg] = []
    provenance: list[dict[str, Any]] = []
    current_day: str | None = None
    for source_line, line in enumerate(block.splitlines(), 1):
        leg_match = LEG.match(line)
        if not leg_match:
            continue
        if leg_match.group(1):
            current_day = leg_match.group(1)
        flight = leg_match.group(3)
        origin, destination = leg_match.group(4), leg_match.group(6)
        departure_time, arrival_time = leg_match.group(5), leg_match.group(7)
        continuation = flight is None
        structurally_contiguous = bool(legs and legs[-1].arrival == origin)
        if (
            current_day is None
            or (continuation and not structurally_contiguous)
            or not _valid_clock(departure_time)
            or not _valid_clock(arrival_time)
            or not is_valid_airport_code(origin)
            or not is_valid_airport_code(destination)
        ):
            continue
        rest = leg_match.group(9)
        equipment = re.search(r"\b(3NE|3N1|3NP|321|320|319|75D|73R|73J|221|223|330|350)\b", rest)
        leg_index = len(legs) + 1
        duty_index = _duty_day_index(current_day)
        legs.append(Leg(
            day=current_day,
            deadhead=bool(leg_match.group(2)),
            flight=flight,
            departure=origin,
            departure_time=departure_time,
            arrival=destination,
            arrival_time=arrival_time,
            block=leg_match.group(8),
            aircraft=equipment.group(1) if equipment else None,
        ))
        for role, token in (("origin", origin), ("destination", destination)):
            provenance.append({
                "token": token,
                "source_page": page_number,
                "source_line": source_line,
                "source_row": line.rstrip(),
                "leg_index": leg_index,
                "duty_day_index": duty_index,
                "role": role,
                "validation_result": "accepted",
                "validation_reason": "validated_flight_leg_position_and_iata_metadata",
            })
    return legs, provenance


def _validated_layovers(block: str, legs: list[Leg]) -> list[Layover]:
    duty_order: list[str] = []
    last_destination: dict[str, str] = {}
    for leg in legs:
        day = leg.day or ""
        if day not in duty_order:
            duty_order.append(day)
        last_destination[day] = leg.arrival
    rest_boundaries = {last_destination[day] for day in duty_order[:-1] if last_destination.get(day)}
    return [
        Layover(city=match.group(1), duration=match.group(2), hotel=match.group(3).strip())
        for match in LAYOVER.finditer(block)
        if match.group(1) in rest_boundaries and is_valid_airport_code(match.group(1))
    ]


def _diagnose_airport_tokens(
    rotation: str,
    block: str,
    page_number: int,
    provenance: list[dict[str, Any]],
) -> None:
    if os.environ.get("PARSER_DEBUG_ENABLED", "false").lower() != "true":
        return
    accepted = {(event["source_line"], event["token"], event["role"]): event for event in provenance}
    for source_line, line in enumerate(block.splitlines(), 1):
        for token_match in re.finditer(r"\b[A-Z]{3}\b", line.upper()):
            token = token_match.group(0)
            events = [event for (line_no, value, _), event in accepted.items() if line_no == source_line and value == token]
            if events:
                for event in events:
                    LAST_DIAGNOSTICS.append({
                        "token": token,
                        "rotation": rotation,
                        "source_page": page_number,
                        "source_line": source_line,
                        "context": f'parsed leg {event["role"]}',
                        "result": "ACCEPTED",
                        "reason": "validated_flight_leg_position_and_iata_metadata",
                        "leg_index": event["leg_index"],
                        "duty_day_index": event["duty_day_index"],
                        "role": event["role"],
                    })
                continue
            upper_line = line.upper()
            if "TOTAL PAY" in upper_line:
                context = "pay heading"
            elif PRODUCTION_COLUMNS.search(line):
                duty_columns = upper_line.find("BLK/MAX")
                departure_column = upper_line.find("DEPARTS")
                if duty_columns >= 0 and token_match.start() >= duty_columns:
                    context = "duty-limit field"
                elif departure_column >= 0 and token_match.start() < departure_column:
                    context = "header field"
                else:
                    context = "column heading"
            else:
                context = "non-flight source row"
            LAST_DIAGNOSTICS.append({
                "token": token,
                "rotation": rotation,
                "source_page": page_number,
                "source_line": source_line,
                "context": context,
                "result": "REJECTED",
                "reason": "not_in_validated_flight_leg_origin_destination_position",
                "leg_index": None,
                "duty_day_index": None,
                "role": None,
            })


def parse(text: str) -> list[dict]:
    normalized = text.replace("\r", "\n")
    LAST_DIAGNOSTICS.clear()
    package_base, package_fleet = _package_context(normalized)
    bid_month_context = _bid_month_context(normalized)
    package_id = "delta:" + hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]
    results: list[dict[str, Any]] = []
    inventory_open = False

    for page_number, page in _pages(normalized):
        classification = _classify_page(page)
        heading = _heading(page)
        if END_SECTION.search(heading) or classification in {"CONTENTS", "INSTRUCTIONS", "REFERENCE", "EXAMPLE", "HOTEL_LIST", "APPENDIX"}:
            inventory_open = False
        if INVENTORY_HEADING.search(page) and classification == "BIDABLE_INVENTORY":
            inventory_open = True

        matches = list(HEADER.finditer(page))
        standalone_production = (
            len(_pages(normalized)) == 1
            and bool(CREDIT.search(page) and TAFB.search(page))
            and (bool(PRODUCTION_COLUMNS.search(page)) or any(LEG.match(line) for line in page.splitlines()))
            and not EXAMPLE_SIGNALS.search(page)
        )
        page_inventory = classification == "BIDABLE_INVENTORY" or (inventory_open and classification == "UNKNOWN") or standalone_production
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(page)
            block = page[match.start():end]
            rotation = match.group(1).upper()
            has_detail_row = any(LEG.match(line) for line in block.splitlines())
            production_layout = bool(
                CREDIT.search(block) and TAFB.search(block)
                and (PRODUCTION_COLUMNS.search(block) or has_detail_row or (INVENTORY_HEADING.search(page) and CHECKIN.search(block)))
            )
            instructional = bool(EXAMPLE_SIGNALS.search(page) or classification in {"EXAMPLE", "INSTRUCTIONS"})
            confidence = .35 + (.3 if production_layout else 0) + (.2 if page_inventory else 0) + (.1 if package_base else 0) + (.05 if package_fleet else 0)
            confidence = min(confidence, 1.0)
            accepted = page_inventory and production_layout and not instructional and confidence >= .75
            reason = "accepted_confirmed_bidable_inventory"
            if instructional and not page_inventory:
                reason = "instructional_example_outside_bidable_inventory"
            elif not page_inventory:
                reason = "outside_bidable_inventory"
            elif instructional:
                reason = "instructional_or_example_language"
            elif not production_layout:
                reason = "rotation_candidate_does_not_match_production_layout"
            elif confidence < .75:
                reason = "insufficient_package_context_confidence"
            _diagnose(rotation, page_number, heading, classification, accepted, reason, confidence)
            if not accepted:
                continue

            legs, airport_event_provenance = _parse_leg_rows(block, page_number)
            layovers = _validated_layovers(block, legs)
            credit, tafb, checkin = CREDIT.search(block), TAFB.search(block), CHECKIN.search(block)
            operating_dates = _operating_dates(block, bid_month_context)
            result = Pairing(
                pairing_id=rotation, raw=block, legs=legs, layovers=layovers,
                credit=credit.group(1) if credit else None, tafb=tafb.group(1) if tafb else None,
                checkin=checkin.group(1) if checkin else None,
                effective=", ".join(operating_dates) or None,
                parser="delta_master_pairing", confidence=confidence,
            ).to_dict()
            for leg_index, leg in enumerate(result["legs"], 1):
                events = [event for event in airport_event_provenance if event["leg_index"] == leg_index]
                origin = next((event for event in events if event["role"] == "origin"), None)
                destination = next((event for event in events if event["role"] == "destination"), None)
                leg.update({
                    "source_page": page_number,
                    "source_line": origin["source_line"] if origin else None,
                    "source_row": origin["source_row"] if origin else None,
                    "leg_index": leg_index,
                    "duty_day_index": origin["duty_day_index"] if origin else None,
                    "origin_validation": origin,
                    "destination_validation": destination,
                })
            for layover in result["layovers"]:
                layover["validated"] = True
            result.update(parse_delta_pay(block, result["credit"]))
            result.update({
                "airline": "delta", "package_id": package_id, "source_page": page_number,
                "source_pdf_page": page_number, "source_section": heading or "MASTER PAIRINGS",
                "page_classification": "BIDABLE_INVENTORY", "package_base": package_base,
                "package_fleet": package_fleet, "fleet": package_fleet, "rotation_number": rotation,
                "parser_confidence": confidence, "bidable_inventory_confirmed": True,
                "inventory_key": f"{package_id}:{rotation}",
                "operating_dates": operating_dates,
                "operating_dates_status": "validated" if operating_dates else "unavailable",
                "bid_month": bid_month_context[0], "bid_year": bid_month_context[1],
                "airport_event_provenance": airport_event_provenance,
            })
            _diagnose_airport_tokens(rotation, block, page_number, airport_event_provenance)
            results.append(result)
    return results
