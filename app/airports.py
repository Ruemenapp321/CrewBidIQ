from __future__ import annotations

from collections.abc import Iterable

from app.southwest_time import AIRPORT_TIMEZONES


# Add groups only when the airline's co-terminal definition is confirmed.
COTERMINAL_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "delta": {
        "NYC": ("JFK", "LGA", "EWR"),
    },
}


# This extends the station metadata already used for Southwest time conversion
# with airports exercised by the other supported airline adapters. Membership
# is deliberately independent from source-token meaning: POS and DAY, for
# example, are real airports, but a parser may accept them only in a structural
# flight-leg origin or destination position.
ADDITIONAL_SUPPORTED_IATA_AIRPORTS = frozenset("""
ABE ABI ABY ACK ACT AEX AGS ALW ANC ASE ATH AVL AZO BDL BET BFL BGM BGR BHM BIL BIS BJI BLK BMI BQK
BTR BTV CAE CAK CHA CHO CID CIU CKB CMI CSG CVG CWA DAB DAY DFW DHN DLH DRO DVL ELM ERI ESC EYW
FAI FAR FAY FCA FNT FSD FSM FWA GFK GNV GPT GRB GSO GTR GUC HHH HLN HOB HPN HSV IAH IDA ILM IMT INL JAC JAN
JMS JNU JNB LAN LCH LEX LFT LNK MBS MDT MGM MHK MOB MQT OAJ OMA OTZ PAY POS PWA RAP RDM ROA
RST SAF SBA SBN SGF SHV SIT SPI STS SUN TLH TRI TYS VLD XNA YUL YVR YYC YYZ
AMS AUA BCN BDA BGI BOG BON BZE CDG CUR DUB EZE FCO FRA GCM GIG GRU HKG HND ICN KEF
KIN KIX LHR LIM LIS MAD MBJ MEX MNL MUC MXP NAS NGO NRT PEK PLS PUJ PVG SCL SIN
SJD SJO SJU SDU STT SXM TPE ZRH ORY LGW LCY LTN STN
""".split())

SUPPORTED_IATA_AIRPORTS = frozenset(AIRPORT_TIMEZONES) | ADDITIONAL_SUPPORTED_IATA_AIRPORTS | frozenset(
    airport
    for groups in COTERMINAL_GROUPS.values()
    for airports in groups.values()
    for airport in airports
)

INTERNATIONAL_IATA_AIRPORTS = frozenset("""
AMS ATH AUA BCN BDA BGI BLK BOG BON BZE CDG CUR DUB EZE FCO FRA GCM GRU HKG HND ICN
JNB KEF KIN KIX LHR LIM LIR LIS MAD MBJ MEX MNL MUC MXP NAS NGO NRT PEK PLS POS
PAY PUJ PVG PVR SCL SIN SJD SJO SJU STT SXM TPE YUL YVR YYC YYZ ZRH
""".split())


def is_valid_airport_code(token: object) -> bool:
    """Return whether a token is a supported IATA airport identifier.

    This validates code metadata only. Callers must separately prove that the
    occurrence occupies an origin/destination field in a parsed flight row.
    """
    code = str(token or "").strip().upper()
    return len(code) == 3 and code.isalpha() and code in SUPPORTED_IATA_AIRPORTS


def is_international_airport(token: object) -> bool:
    return str(token or "").strip().upper() in INTERNATIONAL_IATA_AIRPORTS


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
