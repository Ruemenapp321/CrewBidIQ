from __future__ import annotations

from collections.abc import Iterable


# Add groups only when the airline's co-terminal definition is confirmed.
COTERMINAL_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "delta": {
        "NYC": ("JFK", "LGA", "EWR"),
    },
}


def expand_airports(airline: str, values: Iterable[str]) -> list[str]:
    groups = COTERMINAL_GROUPS.get((airline or "").lower(), {})
    expanded: list[str] = []
    for value in values:
        code = str(value or "").strip().upper()
        for airport in groups.get(code, (code,) if code else ()):
            if airport not in expanded:
                expanded.append(airport)
    return expanded


def coterminal_group_for_airport(airline: str, airport: str | None) -> str | None:
    code = str(airport or "").strip().upper()
    for group, airports in COTERMINAL_GROUPS.get((airline or "").lower(), {}).items():
        if code in airports:
            return group
    return None


def coterminal_payload() -> dict[str, dict[str, list[str]]]:
    return {
        airline: {group: list(airports) for group, airports in groups.items()}
        for airline, groups in COTERMINAL_GROUPS.items()
    }
