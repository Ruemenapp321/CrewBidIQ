from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AirlineTerminology:
    singular: str
    plural: str
    recommended: str
    details: str
    view_original: str
    analyzed: str


@dataclass(frozen=True)
class EquipmentDefinition:
    raw_code: str
    aircraft: str
    variant: str | None = None
    subfleet: str | None = None
    notes: str | None = None
    known: bool = True


@dataclass(frozen=True)
class AirlineKnowledge:
    code: str
    name: str
    terminology: AirlineTerminology
    equipment: dict[str, EquipmentDefinition]


def _equipment(code: str, aircraft: str, **details: str) -> EquipmentDefinition:
    return EquipmentDefinition(raw_code=code, aircraft=aircraft, **details)


AMERICAN_EQUIPMENT = {
    "H319": _equipment("H319", "Airbus A319 CEO", notes="AA code is shared by certain A319 configurations"),
    "319W": _equipment("319W", "Airbus A319 CEO", variant="CFM enhanced, non-sharklet"),
    "319S": _equipment("319S", "Airbus A319 CEO", variant="CFM enhanced, sharklet"),
    "A320": _equipment("A320", "Airbus A320 CEO", variant="CFM"),
    "H205": _equipment("H205", "Airbus A320 CEO", variant="IAE"),
    "321K": _equipment("321K", "Airbus A321 CEO", variant="CFM basic, non-sharklet"),
    "321T": _equipment("321T", "Airbus A321 CEO", variant="CFM enhanced, non-sharklet"),
    "321R": _equipment("321R", "Airbus A321 CEO", variant="IAE enhanced, sharklet"),
    "321N": _equipment("321N", "Airbus A321neo", subfleet="A321NA"),
    "321E": _equipment("321E", "Airbus A321neo", subfleet="A321NX"),
    "321X": _equipment("321X", "Airbus A321neo", subfleet="A321NY"),
}


AIRLINES = {
    "generic": AirlineKnowledge(
        code="generic",
        name="Airline",
        terminology=AirlineTerminology("Pairing", "Pairings", "Recommended pairings", "Pairing details", "View original pairing", "Pairings analyzed"),
        equipment={},
    ),
    "delta": AirlineKnowledge(
        code="delta",
        name="Delta Air Lines",
        terminology=AirlineTerminology("Rotation", "Rotations", "Recommended rotations", "Rotation details", "View original rotation", "Rotations analyzed"),
        equipment={},
    ),
    "american": AirlineKnowledge(
        code="american",
        name="American Airlines",
        terminology=AirlineTerminology("Sequence", "Sequences", "Recommended sequences", "Sequence details", "View original sequence", "Sequences analyzed"),
        equipment=AMERICAN_EQUIPMENT,
    ),
    "southwest": AirlineKnowledge(
        code="southwest",
        name="Southwest Airlines",
        terminology=AirlineTerminology("Line", "Lines", "Recommended lines", "Line details", "View original line", "Lines analyzed"),
        equipment={},
    ),
}


def get_airline_knowledge(airline: str) -> AirlineKnowledge:
    return AIRLINES.get((airline or "").lower(), AIRLINES["generic"])


def get_airline_terminology(airline: str) -> AirlineTerminology:
    return get_airline_knowledge(airline).terminology


def decode_equipment(airline: str, raw_code: str) -> EquipmentDefinition:
    code = str(raw_code or "").strip().upper()
    definition = get_airline_knowledge(airline).equipment.get(code)
    if definition:
        return definition
    return EquipmentDefinition(raw_code=code, aircraft=code or "Unknown", known=False)


def get_aircraft_display_name(airline: str, raw_code: str) -> str:
    definition = decode_equipment(airline, raw_code)
    if not definition.known:
        return definition.raw_code or "Unknown equipment"
    return f"{definition.aircraft} ({definition.raw_code})"


def airline_terminology_payload() -> dict[str, dict[str, str]]:
    return {code: asdict(knowledge.terminology) for code, knowledge in AIRLINES.items()}
