from __future__ import annotations
import re
from datetime import date, timedelta
from .base import Leg, Layover, Pairing
from app.pay import southwest_pairing_pay_fields
from app.southwest_time import effective_start, first_day_on_or_after, next_day_code, normalize_herb_event

HEADER = re.compile(
    r"(?m)^([A-Z0-9]{4})\s+([A-Z0-9 ]+?)\s+PILOTS\s+REPORT AT\s+([0-9]{1,2}:[0-9]{2})\s+EFFECTIVE\s+([^\n]+)$"
)
LEG = re.compile(
    r"^\s*([A-Z]{2}|\d)?\s+(DH\s+)?([A-Z0-9]{1,4})\s+([A-Z0-9]{3})\s+"
    r"([A-Z]{3})\s+(\d{4})\s+([A-Z]{3})\s+(\d{4})(\*)?\s+"
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
        normalized_legs: list[dict] = []
        layovers: list[Layover] = []
        current_day = None
        current_date = None
        start_date = effective_start(header.group(4), normalized)
        for line in block.splitlines():
            lm = LEG.match(line)
            if not lm:
                continue
            day, dh, flight, eq, dep, dtime, arr, atime, next_day, block_time, _cr, lay_city, lay_duration = lm.groups()
            if day:
                if start_date and day != current_day and day in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}:
                    current_date = first_day_on_or_after(start_date, day) if current_date is None else next_day_code(current_date, day)
                current_day = day
            leg = Leg(
                day=current_day,
                deadhead=bool(dh),
                flight=flight,
                departure=dep,
                departure_time=dtime,
                arrival=arr,
                arrival_time=atime,
                block=block_time,
                aircraft=eq,
            )
            legs.append(leg)
            leg_record = leg.__dict__.copy()
            if current_date:
                departure = normalize_herb_event(dtime, current_date, dep)
                departure_clock = int(re.sub(r"\D", "", dtime) or 0)
                arrival_clock = int(re.sub(r"\D", "", atime) or 0)
                arrival_date = current_date + timedelta(days=1 if next_day or arrival_clock < departure_clock else 0)
                arrival = normalize_herb_event(atime, arrival_date, arr)
                leg_record.update({
                    "event_date": current_date.isoformat(),
                    "source_departure_time_herb": dtime,
                    "source_arrival_time_herb": atime,
                    "source_timezone": "America/Chicago",
                    "departure_time": departure["local_clock"] if departure else None,
                    "arrival_time": arrival["local_clock"] if arrival else None,
                    "departure_normalized_utc_timestamp": departure["normalized_utc_timestamp"] if departure else None,
                    "arrival_normalized_utc_timestamp": arrival["normalized_utc_timestamp"] if arrival else None,
                    "departure_local_event_timestamp": departure["local_event_timestamp"] if departure else None,
                    "arrival_local_event_timestamp": arrival["local_event_timestamp"] if arrival else None,
                    "departure_local_event_timezone": departure["local_event_timezone"] if departure else None,
                    "arrival_local_event_timezone": arrival["local_event_timezone"] if arrival else None,
                    "departure_time_provenance": {key: value for key, value in departure.items() if key != "local_clock"} if departure else None,
                    "arrival_time_provenance": {key: value for key, value in arrival.items() if key != "local_clock"} if arrival else None,
                })
            else:
                leg_record.update({
                    "source_departure_time_herb": dtime,
                    "source_arrival_time_herb": atime,
                    "source_timezone": "America/Chicago",
                    "departure_time": None,
                    "arrival_time": None,
                    "time_normalization_status": "unavailable_missing_event_date",
                })
            normalized_legs.append(leg_record)
            if lay_city and lay_duration:
                layovers.append(Layover(city=lay_city, duration=lay_duration, hotel=None))

        summary = TRIP_SUMMARY.search(block)
        reports = REPORT.findall(block)
        release = normalized_legs[-1].get("arrival_time") if normalized_legs else None
        first_leg_date = normalized_legs[0].get("event_date") if normalized_legs else None
        report_normalized = normalize_herb_event(
            reports[0] if reports else header.group(3),
            date.fromisoformat(first_leg_date),
            normalized_legs[0].get("departure"),
        ) if first_leg_date and normalized_legs else None
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
        result["legs"] = normalized_legs
        result["checkin"] = report_normalized["local_clock"] if report_normalized else None
        result["first_report"] = result["checkin"]
        result["final_release"] = release
        result["report_time_provenance"] = {key: value for key, value in report_normalized.items() if key != "local_clock"} if report_normalized else None
        result["time_normalization_status"] = "normalized" if normalized_legs and all(leg.get("departure_time") and leg.get("arrival_time") for leg in normalized_legs) else "unavailable"
        duty_periods = len({leg.day or "1" for leg in legs if not leg.deadhead})
        result.update(southwest_pairing_pay_fields(summary.group(1) if summary else None, result["tafb"], duty_periods))
        result["airline"] = "southwest"
        results.append(result)
    return results
