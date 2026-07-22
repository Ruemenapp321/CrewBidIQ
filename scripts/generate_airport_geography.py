"""Generate CrewBidIQ's local airport geography snapshot.

Input is the public-domain OurAirports airports.csv file. The generated file
contains only airport codes CrewBidIQ validates, so it remains small and is
safe to load for every bid package without a runtime network request.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.airports import SUPPORTED_IATA_AIRPORTS


TYPE_PRIORITY = {
    "large_airport": 4,
    "medium_airport": 3,
    "small_airport": 2,
    "seaplane_base": 1,
    "heliport": 0,
}


def generate(source: Path, destination: Path, countries: Path | None = None) -> None:
    country_names: dict[str, str] = {}
    if countries:
        with countries.open(newline="", encoding="utf-8-sig") as handle:
            country_names = {
                str(row.get("code") or "").strip().upper(): str(row.get("name") or "").strip()
                for row in csv.DictReader(handle)
            }
    selected: dict[str, dict[str, object]] = {}
    with source.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("iata_code") or "").strip().upper()
            if code not in SUPPORTED_IATA_AIRPORTS:
                continue
            candidate = {
                "airport": code,
                "name": str(row.get("name") or code).strip(),
                "city": str(row.get("municipality") or code).strip(),
                "country_code": str(row.get("iso_country") or "").strip().upper(),
                "country_name": country_names.get(str(row.get("iso_country") or "").strip().upper()),
                "continent": str(row.get("continent") or "").strip().upper(),
                "latitude": float(row["latitude_deg"]),
                "longitude": float(row["longitude_deg"]),
                "type": str(row.get("type") or "").strip(),
            }
            current = selected.get(code)
            if current and TYPE_PRIORITY.get(str(current["type"]), -1) >= TYPE_PRIORITY.get(str(candidate["type"]), -1):
                continue
            selected[code] = candidate
    missing = sorted(SUPPORTED_IATA_AIRPORTS - selected.keys())
    if missing:
        raise SystemExit(f"OurAirports snapshot did not contain supported codes: {', '.join(missing)}")
    payload = {
        "source": "OurAirports airports.csv (Public Domain)",
        "source_url": "https://ourairports.com/data/",
        "generated_on": "2026-07-21",
        "airports": {code: selected[code] for code in sorted(selected)},
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--countries", type=Path, help="Optional OurAirports countries.csv for searchable country names")
    args = parser.parse_args()
    generate(args.source, args.destination, args.countries)
