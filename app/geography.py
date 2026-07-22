from __future__ import annotations

from collections import Counter
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Iterable


DATA_PATH = Path(__file__).with_name("airport-geography.json")

EUROPE = frozenset("""
AD AL AT AX BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GG GI GR HR HU IE IM IS IT
JE LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS RU SE SI SK SM UA VA
""".split())
SOUTH_AMERICA = frozenset("AR BO BR CL CO EC FK GF GY PE PY SR UY VE".split())
LATIN_AMERICA_CARIBBEAN = frozenset("""
AG AI AW BB BL BM BQ BS BZ CR CU CW DM DO GD GP GT HN HT JM KN KY LC MF MQ MS NI PA PR
SV SX TC TT VC VG VI
""".split())
AFRICA = frozenset("""
AO BF BI BJ BW CD CF CG CI CM CV DJ DZ EG EH ER ET GA GH GM GN GQ GW KE KM LR LS LY MA
MG ML MR MU MW MZ NA NE NG RE RW SC SD SH SL SN SO SS ST SZ TD TG TN TZ UG YT ZA ZM ZW
""".split())
MIDDLE_EAST = frozenset("AE BH IL IQ IR JO KW LB OM PS QA SA SY TR YE".split())
PACIFIC = frozenset("""
AS AU BN CC CK CN CX FJ FM GU HK ID JP KH KI KP KR LA MH MM MN MO MP MY NC NF NR NU NZ PF
PG PH PN PW SB SG TH TL TO TV TW VN VU WF WS
""".split())

THEATER_LABELS = {
    "NORTH_AMERICA": "North America",
    "EUROPE": "Europe",
    "LATIN_AMERICA_CARIBBEAN": "Latin America & Caribbean",
    "SOUTH_AMERICA": "South America",
    "PACIFIC": "Pacific",
    "AFRICA": "Africa",
    "MIDDLE_EAST": "Middle East",
    "UNKNOWN": "Other / unclassified",
}

MARKET_ALIASES = {
    "PARIS": ("CDG", "ORY"),
    "LONDON": ("LHR", "LGW", "LCY", "LTN", "STN"),
    "LON": ("LHR", "LGW", "LCY", "LTN", "STN"),
    "RIO": ("GIG", "SDU"),
    "RIO DE JANEIRO": ("GIG", "SDU"),
    "NEW YORK": ("JFK", "LGA", "EWR"),
    "NYC": ("JFK", "LGA", "EWR"),
}

MARKET_NAMES = {
    "CDG": "Paris", "ORY": "Paris", "LHR": "London", "LGW": "London", "LCY": "London",
    "LTN": "London", "STN": "London", "GIG": "Rio de Janeiro", "SDU": "Rio de Janeiro",
    "JFK": "New York", "LGA": "New York", "EWR": "New York",
}


def aliases_for_airport(airport: object) -> list[str]:
    code = str(airport or "").strip().upper()
    return [alias for alias, members in MARKET_ALIASES.items() if code in members]


@lru_cache(maxsize=1)
def airport_geography() -> dict[str, dict[str, Any]]:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return payload["airports"]


def theater_for_country(country_code: str | None) -> str:
    code = str(country_code or "").strip().upper()
    if code in EUROPE:
        return "EUROPE"
    if code in SOUTH_AMERICA:
        return "SOUTH_AMERICA"
    if code in LATIN_AMERICA_CARIBBEAN:
        return "LATIN_AMERICA_CARIBBEAN"
    if code in AFRICA:
        return "AFRICA"
    if code in MIDDLE_EAST:
        return "MIDDLE_EAST"
    if code in PACIFIC:
        return "PACIFIC"
    if code in {"US", "CA", "MX", "GL", "PM"}:
        return "NORTH_AMERICA"
    return "UNKNOWN"


def geography_for_airport(airport: object) -> dict[str, Any]:
    code = str(airport or "").strip().upper()
    source = airport_geography().get(code, {})
    country_code = str(source.get("country_code") or "").upper() or None
    return {
        "airport": code or None,
        "city": MARKET_NAMES.get(code) or source.get("city") or code or None,
        "country_code": country_code,
        "country_name": source.get("country_name") or country_code,
        "theater": theater_for_country(country_code),
        "name": source.get("name") or code or None,
        "aliases": aliases_for_airport(code),
        "latitude": source.get("latitude"),
        "longitude": source.get("longitude"),
    }


def available_layover_options(pairings: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for pairing in pairings:
        pairing_codes: set[str] = set()
        for value in pairing.get("layovers", []) or []:
            code = str(value.get("arrival_airport") or value.get("airport") or value.get("city") or "").strip().upper()
            if code:
                pairing_codes.add(code)
        counts.update(pairing_codes)
    airports = []
    for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        metadata = geography_for_airport(code)
        airports.append({**metadata, "count": count, "layover_market": code})
    groups = []
    for theater, label in THEATER_LABELS.items():
        children = [value for value in airports if value["theater"] == theater]
        if children:
            groups.append({"code": theater, "label": label, "count": sum(int(value["count"]) for value in children), "airports": children})
    return {"airports": airports, "theaters": groups, "count_basis": "published_trip_records"}


def resolve_layover_preference(value: object, available_airports: Iterable[str]) -> dict[str, Any]:
    token = str(value or "").strip().upper()
    available = list(dict.fromkeys(str(code or "").strip().upper() for code in available_airports if str(code or "").strip()))
    if token in THEATER_LABELS:
        matches = [code for code in available if geography_for_airport(code)["theater"] == token]
        return {"token": token, "label": THEATER_LABELS[token], "level": "theater", "airports": matches}
    aliases = MARKET_ALIASES.get(token)
    if aliases:
        matches = [code for code in aliases if code in available]
        return {"token": token, "label": token.title(), "level": "market", "airports": matches}
    if len(token) == 3 and token.isalpha():
        return {"token": token, "label": geography_for_airport(token)["city"], "level": "airport", "airports": [token] if token in available else []}
    city_matches = [code for code in available if str(geography_for_airport(code)["city"] or "").upper() == token]
    return {"token": token, "label": token.title(), "level": "market" if city_matches else "unknown", "airports": city_matches}
