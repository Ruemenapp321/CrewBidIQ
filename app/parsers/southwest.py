from __future__ import annotations
import re
from .base import Leg, Layover, Pairing
from app.pay import southwest_pairing_pay_fields

HEADER = re.compile(
    r"(?m)^([A-Z0-9]{4})\s+([A-Z0-9 ]+?)\s+PILOTS\s+REPORT AT\s+([0-9]{1,2}:[0-9]{2})\s+EFFECTIVE\s+([^\n]+)$"
)
LEG = re.compile(
    r"^\s*([A-Z]{2}|\d)?\s+(DH\s+)?([A-Z0-9]{1,4})\s+([A-Z0-9]{3})\s+"
    r"([A-Z]{3})\s+(\d{4})\s+([A-Z]{3})\s+(\d{4})\*?\s+"
    r"(\d{1,2}:\d{2}|:\d{2})\s+\s*([0-9.]+)?\s*(?:([A-Z]{3})\s+(\d{1,2}:\d{2}))?.*$"
)
TRIP_SUMMARY = re.compile(
    r"Trip Credit\s+([0-9]+\.[0-9]+)[A-Z]?\s+BLK HRS\s+([0-9]+:[0-9]{2})\s+"
    r"No\. Legs\s+(\d+)\s+TAFB\s+([0-9]+:[0-9]{2})",
    re.I,
)
REPORT = re.compile(r"REPORT AT\s+([0-9]{1,2}:[0-9]{2})", re.I)


def detect(text: str) -> float:
    up = text.upper()
    score = 0.0
    if "TRIP CREDIT" in up and ("TFP" in up or "BLK HRS" in up):
        score += .35
    if "PILOTS      REPORT AT" in up:
        score += .30
    if "SCHEDULE PERIOD:" in up and "POSITION: FO" in up:
        score += .10
    if len(HEADER.findall(text)) >= 10:
        score += .25
    return min(score, 1.0)


def parse(text: str) -> list[dict]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(HEADER.finditer(normalized))
    results = []
    for i, header in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(normalized)
        block = normalized[header.start():end]
        legs: list[Leg] = []
        layovers: list[Layover] = []
        current_day = None
        for line in block.splitlines():
            lm = LEG.match(line)
            if not lm:
                continue
            day, dh, flight, eq, dep, dtime, arr, atime, block_time, _cr, lay_city, lay_duration = lm.groups()
            if day:
                current_day = day
            legs.append(Leg(
                day=current_day,
                deadhead=bool(dh),
                flight=flight,
                departure=dep,
                departure_time=dtime,
                arrival=arr,
                arrival_time=atime,
                block=block_time,
                aircraft=eq,
            ))
            if lay_city and lay_duration:
                layovers.append(Layover(city=lay_city, duration=lay_duration, hotel=None))

        summary = TRIP_SUMMARY.search(block)
        reports = REPORT.findall(block)
        release = legs[-1].arrival_time if legs else None
        confidence = .96 if legs and summary else (.78 if legs else .45)
        result = Pairing(
            pairing_id=header.group(1),
            raw=block,
            legs=legs,
            layovers=layovers,
            credit=summary.group(1) if summary else None,
            tafb=summary.group(4) if summary else None,
            checkin=reports[0] if reports else header.group(3),
            release=release,
            effective=header.group(4).strip(),
            parser="southwest_pairing_text",
            confidence=confidence,
        ).to_dict()
        duty_periods = len({leg.day or "1" for leg in legs if not leg.deadhead})
        result.update(southwest_pairing_pay_fields(summary.group(1) if summary else None, result["tafb"], duty_periods))
        result["airline"] = "southwest"
        results.append(result)
    return results
