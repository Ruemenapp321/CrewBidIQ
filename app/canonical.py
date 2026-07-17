from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import re
from typing import Any


@dataclass(frozen=True)
class PayBreakdown:
    trip_credit: str | None
    edp: str | None
    hol: str | None
    sit: str | None
    additional_pay: str | None
    total_pay: str | None
    raw_pay_tokens: list[str]
    unresolved_pay_tokens: list[str]


@dataclass(frozen=True)
class TripLeg:
    sequence_index: int
    duty_day_index: int
    origin: str | None
    destination: str | None
    operating_or_deadhead: str
    flight_number: str | None
    equipment: str | None
    source_departure_time: str | None
    source_arrival_time: str | None
    utc_departure_time: str | None
    utc_arrival_time: str | None
    local_departure_time: str | None
    local_arrival_time: str | None
    origin_timezone: str | None
    destination_timezone: str | None


@dataclass(frozen=True)
class TripEvent:
    sequence_index: int
    duty_day_index: int
    event_type: str
    airport: str | None
    source_time: str | None
    utc_time: str | None
    local_time: str | None
    local_timezone: str | None
    leg_sequence_index: int | None = None
    operating_or_deadhead: str | None = None


@dataclass(frozen=True)
class Layover:
    after_duty_day: int
    airport: str | None
    city: str | None
    hotel: str | None
    transportation: str | None
    start_local: str | None
    end_local: str | None
    duration: str | None
    validated: bool


@dataclass(frozen=True)
class DutyDay:
    day_index: int
    calendar_date: str | None
    report_event: TripEvent | None
    ordered_legs: list[TripLeg]
    release_event: TripEvent | None
    layover_after_duty: Layover | None


@dataclass(frozen=True)
class CanonicalTrip:
    id: str
    package_id: str
    airline: str
    terminology: str
    base: str | None
    fleet: str | None
    seat: str | None
    bid_month: str | None
    source_trip_number: str
    trip_length_days: int
    calendar_span_days: int
    duty_period_count: int
    tafb: str | None
    pay_breakdown: PayBreakdown
    tfp: dict[str, Any] | None
    ordered_events: list[TripEvent]
    ordered_legs: list[TripLeg]
    duty_days: list[DutyDay]
    layovers: list[Layover]
    hotels: list[dict[str, Any]]
    report: TripEvent | None
    release: TripEvent | None
    operating_dates: list[str]
    source_text: str
    source_page: int | None
    source_section: str | None
    raw_source_fields: dict[str, Any]
    bidable_inventory_confirmed: bool
    parser_confidence: float


TERMINOLOGY = {
    "delta": "rotation",
    "american": "sequence",
    "southwest": "pairing",
    "generic": "pairing",
}

CANONICAL_ALIAS_FIELDS = {
    "canonical_trip",
    "canonical_trip_id",
    "trip_length_days",
    "ordered_events",
    "ordered_legs",
    "duty_days",
    "hotels",
    "pay_breakdown",
    "tfp",
}


def _airline(record: dict[str, Any]) -> str:
    explicit = str(record.get("airline") or "").strip().lower()
    if explicit:
        return explicit
    parser = str(record.get("parser") or "").lower()
    for airline in ("delta", "american", "southwest"):
        if parser.startswith(airline):
            return airline
    return "generic"


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _values(value: Any) -> list[str]:
    source = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    return [str(item).strip() for item in source if str(item).strip()]


def _operating_dates(record: dict[str, Any], airline: str) -> list[str]:
    # An explicitly empty operating_dates field is authoritative. In particular,
    # Delta must never fall back to unvalidated tokens from source text.
    if "operating_dates" in record:
        return list(dict.fromkeys(_values(record.get("operating_dates"))))
    if record.get("start_dates"):
        return list(dict.fromkeys(_values(record.get("start_dates"))))
    if airline == "southwest":
        return list(dict.fromkeys(
            str(leg.get("event_date"))
            for leg in record.get("legs", []) or []
            if leg.get("event_date")
        ))
    effective = str(record.get("effective") or "").strip()
    if not effective:
        return []
    return list(dict.fromkeys(
        token for token in re.split(r"\s*,\s*", effective)
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", token)
    ))


def _bid_month(record: dict[str, Any], operating_dates: list[str]) -> str | None:
    month = record.get("bid_month")
    year = record.get("bid_year")
    if _positive_int(month) and _positive_int(year):
        return f"{int(year):04d}-{int(month):02d}"
    if isinstance(month, str) and month.strip():
        return month.strip()
    for value in operating_dates:
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
            return value[:7]
    return None


def _pay_breakdown(record: dict[str, Any], airline: str) -> PayBreakdown:
    components = record.get("pay_components") or {}
    trip_credit = record.get("trip_credit")
    if trip_credit is None and airline in {"delta", "generic"}:
        trip_credit = record.get("credit")
    return PayBreakdown(
        trip_credit=str(trip_credit) if trip_credit not in (None, "") else None,
        edp=str(record.get("edp") if record.get("edp") is not None else components.get("EDP")) if (record.get("edp") is not None or components.get("EDP") is not None) else None,
        hol=str(record.get("hol") if record.get("hol") is not None else components.get("HOL")) if (record.get("hol") is not None or components.get("HOL") is not None) else None,
        sit=str(record.get("sit") if record.get("sit") is not None else components.get("SIT")) if (record.get("sit") is not None or components.get("SIT") is not None) else None,
        additional_pay=str(record.get("additional_pay")) if record.get("additional_pay") not in (None, "") else None,
        total_pay=str(record.get("total_pay")) if record.get("total_pay") not in (None, "") else None,
        raw_pay_tokens=_values(record.get("raw_pay_tokens")),
        unresolved_pay_tokens=_values(record.get("unresolved_pay_tokens")),
    )


def _tfp(record: dict[str, Any], airline: str) -> dict[str, Any] | None:
    if airline != "southwest":
        return None
    fields = {
        key: record.get(key)
        for key in (
            "pairing_tfp", "line_tfp", "monthly_tfp", "carry_out_tfp",
            "tfp_per_duty_period", "tfp_per_day_away",
        )
        if record.get(key) is not None
    }
    return fields or None


def _leg_day_indices(legs: list[dict[str, Any]]) -> list[int]:
    raw_labels = [str(leg.get("duty_day_index") or leg.get("day") or "1").strip().upper() for leg in legs]
    if raw_labels and all(re.fullmatch(r"[A-Z]", label) for label in raw_labels):
        return [ord(label) - ord("A") + 1 for label in raw_labels]
    if raw_labels and all(label.isdigit() and int(label) > 0 for label in raw_labels):
        return [int(label) for label in raw_labels]
    labels: list[str] = []
    indices: list[int] = []
    for label in raw_labels:
        if label not in labels:
            labels.append(label)
        indices.append(labels.index(label) + 1)
    return indices


def _trip_legs(record: dict[str, Any]) -> list[TripLeg]:
    source = record.get("legs", []) or []
    day_indices = _leg_day_indices(source)
    legs: list[TripLeg] = []
    for sequence_index, (leg, day_index) in enumerate(zip(source, day_indices), 1):
        legs.append(TripLeg(
            sequence_index=sequence_index,
            duty_day_index=day_index,
            origin=str(leg.get("departure") or leg.get("origin") or "").upper() or None,
            destination=str(leg.get("arrival") or leg.get("destination") or "").upper() or None,
            operating_or_deadhead="deadhead" if bool(leg.get("deadhead")) else "operating",
            flight_number=str(leg.get("flight") or leg.get("flight_number") or "") or None,
            equipment=str(leg.get("aircraft") or leg.get("equipment") or leg.get("equipment_code") or "") or None,
            source_departure_time=str(leg.get("source_departure_time_herb") or leg.get("source_departure_time") or leg.get("departure_time") or "") or None,
            source_arrival_time=str(leg.get("source_arrival_time_herb") or leg.get("source_arrival_time") or leg.get("arrival_time") or "") or None,
            utc_departure_time=str(leg.get("departure_normalized_utc_timestamp") or leg.get("utc_departure_time") or "") or None,
            utc_arrival_time=str(leg.get("arrival_normalized_utc_timestamp") or leg.get("utc_arrival_time") or "") or None,
            local_departure_time=str(leg.get("departure_time") or leg.get("local_departure_time") or "") or None,
            local_arrival_time=str(leg.get("arrival_time") or leg.get("local_arrival_time") or "") or None,
            origin_timezone=str(leg.get("departure_local_event_timezone") or leg.get("origin_timezone") or "") or None,
            destination_timezone=str(leg.get("arrival_local_event_timezone") or leg.get("destination_timezone") or "") or None,
        ))
    return legs


def _layovers(record: dict[str, Any], legs: list[TripLeg]) -> list[Layover]:
    source = record.get("layovers", []) or []
    last_destination = {
        day: next((leg.destination for leg in reversed(legs) if leg.duty_day_index == day), None)
        for day in sorted({leg.duty_day_index for leg in legs})
    }
    unused_days = [day for day in sorted(last_destination)[:-1]]
    if not source:
        # Preserve the existing generic-parser behavior at one centralized
        # boundary: a duty boundary can imply an overnight, while an airport
        # between two legs in the same duty can never become a layover.
        return [
            Layover(day, last_destination[day], last_destination[day], None, None, None, None, None, False)
            for day in unused_days
            if last_destination.get(day)
        ]
    layovers: list[Layover] = []
    for index, value in enumerate(source):
        airport = str(value.get("airport") or value.get("city") or "").strip().upper() or None
        explicit_day = _positive_int(value.get("after_duty_day"))
        matching_day = next((day for day in unused_days if last_destination.get(day) == airport), None)
        after_day = explicit_day or matching_day or (unused_days[0] if unused_days else index + 1)
        if after_day in unused_days:
            unused_days.remove(after_day)
        duration = str(value.get("duration") or "").strip() or None
        hotel = str(value.get("hotel") or "").strip() or None
        layovers.append(Layover(
            after_duty_day=after_day,
            airport=airport,
            city=str(value.get("city") or airport or "").strip() or None,
            hotel=hotel,
            transportation=str(value.get("transportation") or value.get("transportation_provider") or "").strip() or None,
            start_local=str(value.get("start_local") or "").strip() or None,
            end_local=str(value.get("end_local") or "").strip() or None,
            duration=duration,
            validated=bool(value.get("validated", bool(airport and (duration or hotel)))),
        ))
    return layovers


def _report_release_event(
    event_type: str,
    duty_index: int,
    airport: str | None,
    local_time: Any,
    provenance: dict[str, Any] | None = None,
) -> TripEvent | None:
    if local_time in (None, ""):
        return None
    provenance = provenance or {}
    return TripEvent(
        sequence_index=0,
        duty_day_index=duty_index,
        event_type=event_type,
        airport=airport,
        source_time=str(provenance.get("source_time_herb") or local_time),
        utc_time=str(provenance.get("normalized_utc_timestamp") or "") or None,
        local_time=str(provenance.get("local_event_timestamp") or local_time),
        local_timezone=str(provenance.get("local_event_timezone") or "") or None,
    )


def _duty_days(record: dict[str, Any], legs: list[TripLeg], layovers: list[Layover]) -> tuple[list[DutyDay], list[TripEvent]]:
    duty_indices = sorted({leg.duty_day_index for leg in legs})
    source_legs = record.get("legs", []) or []
    source_duties = record.get("duty_periods", []) or []
    duty_days: list[DutyDay] = []
    ordered_events: list[TripEvent] = []

    for duty_ordinal, duty_index in enumerate(duty_indices):
        duty_legs = [leg for leg in legs if leg.duty_day_index == duty_index]
        first_leg = duty_legs[0]
        last_leg = duty_legs[-1]
        duty_source = source_duties[duty_ordinal] if duty_ordinal < len(source_duties) else {}
        report_time = duty_source.get("report_local")
        release_time = duty_source.get("release_local")
        if duty_index == duty_indices[0]:
            report_time = report_time or record.get("first_report") or record.get("checkin")
        if duty_index == duty_indices[-1]:
            release_time = release_time or record.get("final_release") or record.get("release")
        report_provenance = record.get("report_time_provenance") if duty_index == duty_indices[0] else None
        report = _report_release_event("report", duty_index, first_leg.origin, report_time, report_provenance)
        release = _report_release_event("release", duty_index, last_leg.destination, release_time)
        source_leg = source_legs[first_leg.sequence_index - 1] if first_leg.sequence_index <= len(source_legs) else {}
        calendar_date = str(source_leg.get("event_date") or "") or None
        layover = next((item for item in layovers if item.after_duty_day == duty_index), None)
        duty_days.append(DutyDay(duty_index, calendar_date, report, duty_legs, release, layover))

        if report:
            ordered_events.append(report)
        for leg in duty_legs:
            ordered_events.extend([
                TripEvent(
                    sequence_index=0,
                    duty_day_index=duty_index,
                    event_type="departure",
                    airport=leg.origin,
                    source_time=leg.source_departure_time,
                    utc_time=leg.utc_departure_time,
                    local_time=leg.local_departure_time,
                    local_timezone=leg.origin_timezone,
                    leg_sequence_index=leg.sequence_index,
                    operating_or_deadhead=leg.operating_or_deadhead,
                ),
                TripEvent(
                    sequence_index=0,
                    duty_day_index=duty_index,
                    event_type="arrival",
                    airport=leg.destination,
                    source_time=leg.source_arrival_time,
                    utc_time=leg.utc_arrival_time,
                    local_time=leg.local_arrival_time,
                    local_timezone=leg.destination_timezone,
                    leg_sequence_index=leg.sequence_index,
                    operating_or_deadhead=leg.operating_or_deadhead,
                ),
            ])
        if release:
            ordered_events.append(release)

    ordered_events = [
        TripEvent(**{**asdict(event), "sequence_index": index})
        for index, event in enumerate(ordered_events, 1)
    ]
    event_by_key = {
        (event.event_type, event.duty_day_index): event
        for event in ordered_events
        if event.event_type in {"report", "release"}
    }
    duty_days = [
        DutyDay(
            day.day_index,
            day.calendar_date,
            event_by_key.get(("report", day.day_index)),
            day.ordered_legs,
            event_by_key.get(("release", day.day_index)),
            day.layover_after_duty,
        )
        for day in duty_days
    ]
    return duty_days, ordered_events


def _raw_source_fields(record: dict[str, Any]) -> dict[str, Any]:
    if isinstance(record.get("raw_source_fields"), dict):
        return dict(record["raw_source_fields"])
    excluded = CANONICAL_ALIAS_FIELDS | {"block", "raw", "legs", "layovers"}
    return {key: value for key, value in record.items() if key not in excluded}


def canonical_trip_from_record(record: dict[str, Any], package_id: str | None = None) -> CanonicalTrip:
    airline = _airline(record)
    package = str(package_id or record.get("package_id") or "legacy-package").strip()
    source_number = str(record.get("source_trip_number") or record.get("rotation_number") or record.get("id") or "").strip().upper()
    source_text = str(record.get("block") or record.get("source_text") or record.get("raw") or "")
    if not source_number:
        source_number = "TRIP-" + sha256(source_text.encode("utf-8", errors="ignore")).hexdigest()[:12].upper()
    canonical_id = f"{package}:{source_number}"
    legs = _trip_legs(record)
    layovers = _layovers(record, legs)
    duty_days, ordered_events = _duty_days(record, legs, layovers)
    trip_length = next((value for value in (
        _positive_int(record.get("trip_length_days")),
        _positive_int(record.get("sequence_days")),
        _positive_int(record.get("trip_days")),
        _positive_int(record.get("calendar_span_days")),
    ) if value is not None), (max((day.day_index for day in duty_days), default=0) - min((day.day_index for day in duty_days), default=1) + 1 if duty_days else 0))
    calendar_span = _positive_int(record.get("calendar_span_days")) or trip_length
    duty_count = _positive_int(record.get("duty_period_count")) or len(duty_days)
    dates = _operating_dates(record, airline)
    hotels: list[dict[str, Any]] = []
    for layover in layovers:
        if not layover.hotel:
            continue
        hotel = {
            "airport": layover.airport,
            "city": layover.city,
            "name": layover.hotel,
            "transportation": layover.transportation,
            "validated": layover.validated,
        }
        if hotel not in hotels:
            hotels.append(hotel)
    first_operating = next((leg for leg in legs if leg.operating_or_deadhead == "operating"), None)
    report = duty_days[0].report_event if duty_days else None
    release = duty_days[-1].release_event if duty_days else None
    return CanonicalTrip(
        id=canonical_id,
        package_id=package,
        airline=airline,
        terminology=TERMINOLOGY.get(airline, "pairing"),
        base=str(record.get("package_base") or record.get("base") or (first_operating.origin if first_operating else "") or "").upper() or None,
        fleet=str(record.get("package_fleet") or record.get("fleet") or "").upper() or None,
        seat=str(record.get("seat") or record.get("position") or "").upper() or None,
        bid_month=_bid_month(record, dates),
        source_trip_number=source_number,
        trip_length_days=trip_length,
        calendar_span_days=calendar_span,
        duty_period_count=duty_count,
        tafb=str(record.get("tafb")) if record.get("tafb") not in (None, "") else None,
        pay_breakdown=_pay_breakdown(record, airline),
        tfp=_tfp(record, airline),
        ordered_events=ordered_events,
        ordered_legs=legs,
        duty_days=duty_days,
        layovers=layovers,
        hotels=hotels,
        report=report,
        release=release,
        operating_dates=dates,
        source_text=source_text,
        source_page=record.get("source_page") or record.get("source_pdf_page"),
        source_section=str(record.get("source_section") or record.get("fleet_section") or "").strip() or None,
        raw_source_fields=_raw_source_fields(record),
        bidable_inventory_confirmed=record.get("bidable_inventory_confirmed") is not False,
        parser_confidence=float(record.get("parser_confidence", record.get("confidence", 0.0)) or 0.0),
    )


def canonical_trip_payload(record: dict[str, Any], package_id: str | None = None) -> dict[str, Any]:
    return asdict(canonical_trip_from_record(record, package_id))


def attach_canonical_trip(record: dict[str, Any], package_id: str | None = None) -> dict[str, Any]:
    """Attach the shared presentation model while retaining legacy parser aliases."""
    output = dict(record)
    canonical = canonical_trip_payload(output, package_id)
    output.update({
        "package_id": canonical["package_id"],
        "inventory_key": canonical["id"],
        "canonical_trip_id": canonical["id"],
        "source_trip_number": canonical["source_trip_number"],
        "trip_length_days": canonical["trip_length_days"],
        "calendar_span_days": canonical["calendar_span_days"],
        "duty_period_count": canonical["duty_period_count"],
        "ordered_events": canonical["ordered_events"],
        "ordered_legs": canonical["ordered_legs"],
        "duty_days": canonical["duty_days"],
        "hotels": canonical["hotels"],
        "pay_breakdown": canonical["pay_breakdown"],
        "tfp": canonical["tfp"],
        "operating_dates": canonical["operating_dates"],
        "canonical_trip": canonical,
    })
    return output


def canonical_presentation_record(record: dict[str, Any], package_id: str | None = None) -> dict[str, Any]:
    """Project canonical facts into the legacy shape consumed by Classic scoring."""
    output = attach_canonical_trip(record, package_id)
    canonical = output["canonical_trip"]
    source_legs = record.get("legs", []) or []
    legacy_legs: list[dict[str, Any]] = []
    for index, leg in enumerate(canonical["ordered_legs"]):
        legacy = dict(source_legs[index]) if index < len(source_legs) else {}
        legacy.update({
            "sequence_index": leg["sequence_index"],
            "duty_day_index": leg["duty_day_index"],
            "departure": leg["origin"],
            "arrival": leg["destination"],
            "departure_time": leg["local_departure_time"],
            "arrival_time": leg["local_arrival_time"],
            "flight": leg["flight_number"],
            "aircraft": leg["equipment"],
            "deadhead": leg["operating_or_deadhead"] == "deadhead",
        })
        legacy_legs.append(legacy)
    output["legs"] = legacy_legs
    output["layovers"] = canonical["layovers"]
    output["block"] = canonical["source_text"]
    return output


def public_canonical_trip(canonical: dict[str, Any]) -> dict[str, Any]:
    """Remove parser-only Southwest Herb provenance from normal API/UI payloads."""
    if canonical.get("airline") != "southwest":
        return canonical
    output = {**canonical, "raw_source_fields": {}, "source_text": "Local schedule available in original_display"}
    output["ordered_legs"] = [
        {**leg, "source_departure_time": None, "source_arrival_time": None}
        for leg in canonical.get("ordered_legs", [])
    ]
    output["ordered_events"] = [
        {**event, "source_time": None}
        for event in canonical.get("ordered_events", [])
    ]
    output["duty_days"] = [
        {
            **day,
            "report_event": ({**day["report_event"], "source_time": None} if day.get("report_event") else None),
            "ordered_legs": [
                {**leg, "source_departure_time": None, "source_arrival_time": None}
                for leg in day.get("ordered_legs", [])
            ],
            "release_event": ({**day["release_event"], "source_time": None} if day.get("release_event") else None),
        }
        for day in canonical.get("duty_days", [])
    ]
    output["report"] = {**canonical["report"], "source_time": None} if canonical.get("report") else None
    output["release"] = {**canonical["release"], "source_time": None} if canonical.get("release") else None
    return output


def model_from_item(item: dict[str, Any]) -> dict[str, Any]:
    model = item.get("canonical_trip")
    return model if isinstance(model, dict) else item


def canonical_value(item: dict[str, Any], key: str, default: Any = None) -> Any:
    """Read a canonical field without treating an authoritative empty value as missing."""
    model = item.get("canonical_trip")
    return model.get(key, default) if isinstance(model, dict) else item.get(key, default)
