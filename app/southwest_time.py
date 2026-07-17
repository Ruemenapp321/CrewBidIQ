from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

HERB_TIMEZONE = "America/Chicago"

# Fail closed for an unknown station instead of displaying a Herb clock as local.
AIRPORT_TIMEZONES = {
    "ABQ": "America/Denver", "ALB": "America/New_York", "AMA": "America/Chicago",
    "ATL": "America/New_York", "AUA": "America/Aruba", "AUS": "America/Chicago",
    "BDL": "America/New_York", "BHM": "America/Chicago", "BLI": "America/Los_Angeles",
    "BOI": "America/Boise", "BZE": "America/Belize", "BZN": "America/Denver",
    "BNA": "America/Chicago", "BOS": "America/New_York", "BWI": "America/New_York",
    "BUF": "America/New_York", "BUR": "America/Los_Angeles", "CHS": "America/New_York",
    "CLE": "America/New_York", "CLT": "America/New_York", "COS": "America/Denver",
    "CMH": "America/New_York", "DAL": "America/Chicago", "DCA": "America/New_York",
    "DEN": "America/Denver", "DSM": "America/Chicago", "DTW": "America/New_York",
    "ECP": "America/Chicago", "ELP": "America/Denver", "EUG": "America/Los_Angeles",
    "FAT": "America/Los_Angeles", "FLL": "America/New_York", "GEG": "America/Los_Angeles",
    "GCM": "America/Cayman", "GRR": "America/Detroit", "GSP": "America/New_York",
    "HDN": "America/Denver", "HNL": "Pacific/Honolulu", "HOU": "America/Chicago",
    "IAD": "America/New_York", "IND": "America/Indiana/Indianapolis", "JAX": "America/New_York",
    "ICT": "America/Chicago", "ISP": "America/New_York", "ITO": "Pacific/Honolulu",
    "JAN": "America/Chicago", "JFK": "America/New_York", "KOA": "Pacific/Honolulu",
    "LAS": "America/Los_Angeles", "LAX": "America/Los_Angeles", "LBB": "America/Chicago",
    "LIR": "America/Costa_Rica", "LIH": "Pacific/Honolulu", "LIT": "America/Chicago",
    "LGA": "America/New_York", "LGB": "America/Los_Angeles", "MCI": "America/Chicago",
    "MBJ": "America/Jamaica", "MCO": "America/New_York", "MDW": "America/Chicago", "MEM": "America/Chicago",
    "MIA": "America/New_York", "MKE": "America/Chicago", "MSP": "America/Chicago",
    "MSY": "America/Chicago", "MYR": "America/New_York", "NAS": "America/Nassau",
    "OAK": "America/Los_Angeles", "OGG": "Pacific/Honolulu", "OKC": "America/Chicago",
    "OMA": "America/Chicago", "ONT": "America/Los_Angeles", "ORD": "America/Chicago",
    "ORF": "America/New_York", "PBI": "America/New_York", "PNS": "America/Chicago",
    "PDX": "America/Los_Angeles", "PHL": "America/New_York",
    "PHX": "America/Phoenix", "PIT": "America/New_York", "PVD": "America/New_York",
    "PVR": "America/Mexico_City", "PUJ": "America/Santo_Domingo", "PWM": "America/New_York",
    "RDU": "America/New_York", "RIC": "America/New_York", "RNO": "America/Los_Angeles",
    "ROC": "America/New_York", "RSW": "America/New_York",
    "SAN": "America/Los_Angeles", "SAT": "America/Chicago", "SDF": "America/Kentucky/Louisville",
    "SAV": "America/New_York", "SEA": "America/Los_Angeles", "SFO": "America/Los_Angeles",
    "SJC": "America/Los_Angeles", "SJD": "America/Mazatlan", "SJO": "America/Costa_Rica",
    "SLC": "America/Denver", "SMF": "America/Los_Angeles", "SNA": "America/Los_Angeles",
    "SRQ": "America/New_York", "STL": "America/Chicago", "SYR": "America/New_York",
    "TPA": "America/New_York", "TUL": "America/Chicago", "TUS": "America/Phoenix",
    "VPS": "America/Chicago",
}

DAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
MONTHS = {name: number for number, name in enumerate(
    ("", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
) if name}


def parse_source_clock(value: str | None) -> tuple[int, int] | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) not in {3, 4}:
        return None
    hour, minute = int(digits[:-2]), int(digits[-2:])
    return (hour, minute) if hour < 24 and minute < 60 else None


def normalize_herb_event(source_clock: str, event_date: date, airport: str) -> dict[str, Any] | None:
    clock = parse_source_clock(source_clock)
    timezone_name = AIRPORT_TIMEZONES.get(str(airport or "").upper())
    if clock is None or timezone_name is None:
        return None
    herb = datetime(event_date.year, event_date.month, event_date.day, *clock, tzinfo=ZoneInfo(HERB_TIMEZONE))
    utc = herb.astimezone(ZoneInfo("UTC"))
    local = utc.astimezone(ZoneInfo(timezone_name))
    return {
        "source_time_herb": source_clock,
        "source_timezone": HERB_TIMEZONE,
        "normalized_utc_timestamp": utc.isoformat(),
        "local_event_timestamp": local.isoformat(),
        "local_event_timezone": timezone_name,
        "local_clock": local.strftime("%H%M"),
    }


def effective_start(value: str, text: str) -> date | None:
    match = re.search(r"\b([A-Z]{3})\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?", value.upper())
    if not match or match.group(1) not in MONTHS:
        return None
    year = int(match.group(3)) if match.group(3) else None
    if year is None:
        schedule = re.search(r"SCHEDULE\s+PERIOD\s*:\s*[A-Z]{3}\s+\d{1,2}\s*,?\s*(\d{4})", text, re.I)
        year = int(schedule.group(1)) if schedule else None
    if year is None:
        return None
    try:
        return date(year, MONTHS[match.group(1)], int(match.group(2)))
    except ValueError:
        return None


def first_day_on_or_after(start: date, day_code: str) -> date:
    return start + timedelta(days=(DAY_CODES[day_code] - start.weekday()) % 7)


def next_day_code(previous: date, day_code: str) -> date:
    offset = (DAY_CODES[day_code] - previous.weekday()) % 7
    return previous + timedelta(days=offset or 7)


def public_local_leg(leg: dict[str, Any]) -> dict[str, Any]:
    hidden = {
        "source_time_herb", "source_timezone", "source_departure_time_herb", "source_arrival_time_herb",
        "departure_time_provenance", "arrival_time_provenance",
    }
    return {key: value for key, value in leg.items() if key not in hidden}
