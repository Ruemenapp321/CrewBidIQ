from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class DestinationGroup:
    code: str
    label: str
    level: str
    airports: tuple[str, ...]


GROUPS = {
    "HAWAII": DestinationGroup("HAWAII", "Any Hawaii", "region", ("HNL", "OGG", "LIH", "KOA", "ITO")),
    "JAPAN": DestinationGroup("JAPAN", "Any Japan", "country", ("HND", "NRT", "KIX", "NGO")),
    "ASIA": DestinationGroup("ASIA", "Any Asia", "region", ("HND", "NRT", "KIX", "NGO", "ICN", "PVG", "PEK", "HKG", "TPE", "SIN", "BKK", "MNL")),
    "EUROPE": DestinationGroup("EUROPE", "Any Europe", "region", ("AMS", "ATH", "BCN", "CDG", "DUB", "FCO", "FRA", "LHR", "LIS", "MAD", "MUC", "MXP", "ZRH")),
    "CARIBBEAN": DestinationGroup("CARIBBEAN", "Any Caribbean", "region", ("AUA", "BGI", "CUR", "GCM", "MBJ", "NAS", "PLS", "PUJ", "SJU", "STT", "SXM")),
    "TRANSCON": DestinationGroup("TRANSCON", "Transcontinental", "region", ("BOS", "EWR", "IAD", "JFK", "LGA", "MIA", "PHL", "DCA", "LAX", "OAK", "PDX", "SAN", "SEA", "SFO", "SJC")),
}

ALIASES = {
    "ANY HAWAII": "HAWAII", "HAWAII": "HAWAII",
    "ANY JAPAN": "JAPAN", "JAPAN": "JAPAN",
    "ANY ASIA": "ASIA", "ASIA": "ASIA",
    "ANY EUROPE": "EUROPE", "EUROPE": "EUROPE",
    "ANY CARIBBEAN": "CARIBBEAN", "CARIBBEAN": "CARIBBEAN",
    "TRANSCONTINENTAL": "TRANSCON", "TRANSCON": "TRANSCON",
}

EAST_COAST = {"BOS", "EWR", "IAD", "JFK", "LGA", "MIA", "PHL", "DCA", "BWI"}
WEST_COAST = {"LAX", "OAK", "PDX", "SAN", "SEA", "SFO", "SJC"}


def resolve_destination(value: str) -> dict[str, object]:
    token = str(value or "").strip().upper()
    group_code = ALIASES.get(token)
    if group_code:
        return asdict(GROUPS[group_code])
    if len(token) == 3 and token.isalpha():
        return {"code": token, "label": token, "level": "airport", "airports": (token,)}
    return {"code": token, "label": token.title(), "level": "unknown", "airports": ()}


def destination_matches(values: Iterable[str], preference: str) -> bool:
    airports = {str(value or "").strip().upper() for value in values}
    resolved = resolve_destination(preference)
    return bool(airports.intersection(resolved["airports"]))


def is_transcontinental(legs: Iterable[dict[str, object]]) -> bool:
    for leg in legs:
        departure = str(leg.get("departure") or "").upper()
        arrival = str(leg.get("arrival") or "").upper()
        if (departure in EAST_COAST and arrival in WEST_COAST) or (departure in WEST_COAST and arrival in EAST_COAST):
            return True
    return False


def taxonomy_payload() -> list[dict[str, object]]:
    return [asdict(group) for group in GROUPS.values()]
