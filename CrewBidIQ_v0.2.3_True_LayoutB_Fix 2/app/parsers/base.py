
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

@dataclass
class Leg:
    day: str | None
    deadhead: bool
    flight: str | None
    departure: str
    departure_time: str
    arrival: str
    arrival_time: str
    block: str | None = None
    aircraft: str | None = None

@dataclass
class Layover:
    city: str
    duration: str | None = None
    hotel: str | None = None

@dataclass
class Pairing:
    pairing_id: str
    raw: str
    legs: list[Leg]
    layovers: list[Layover]
    credit: str | None = None
    tafb: str | None = None
    checkin: str | None = None
    release: str | None = None
    effective: str | None = None
    parser: str = "generic"
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.pairing_id,
            "block": self.raw,
            "legs": [asdict(x) for x in self.legs],
            "layovers": [asdict(x) for x in self.layovers],
            "credit": self.credit,
            "tafb": self.tafb,
            "checkin": self.checkin,
            "release": self.release,
            "effective": self.effective,
            "parser": self.parser,
            "confidence": self.confidence,
        }
