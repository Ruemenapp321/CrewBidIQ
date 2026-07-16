
from __future__ import annotations
import re
from .base import Leg, Layover, Pairing
from app.pay import parse_delta_pay

HEADER = re.compile(r"(?m)^\s*#([A-Z]?\d{3,5})\b")
LEG = re.compile(
    r"^\s*([A-Z])?\s*(DH\s+)?(\d{3,4})?\s+([A-Z]{3})\s+(\d{4})\s+"
    r"([A-Z]{3})\s+(\d{4})\*?\s+(\d\.\d{2})(.*)$"
)
LAYOVER = re.compile(r"(?m)^\s*([A-Z]{3})\s+(\d{1,2}\.\d{2})/([^\n]+?)\s+\d+\.\d{2}/")
CREDIT = re.compile(r"TOTAL CREDIT\s+(\d{1,2}\.\d{2})TL")
TAFB = re.compile(r"TAFB\s+(\d{1,3}\.\d{2})")
CHECKIN = re.compile(r"CHECK-IN AT\s+(\d{1,2}\.\d{2})")
EFFECTIVE = re.compile(r"EFFECTIVE\s+([A-Z0-9,\-\s]+?)(?:CHECK-IN|$)")

def detect(text: str) -> float:
    score = 0.0
    up = text.upper()
    if "MASTER PAIRINGS" in up: score += .35
    if "TOTAL CREDIT" in up and "TAFB" in up: score += .25
    if "CHECK-IN AT" in up: score += .15
    if len(HEADER.findall(text)) >= 10: score += .25
    return min(score, 1.0)

def parse(text: str) -> list[dict]:
    normalized = text.replace("\r", "\n")
    matches = list(HEADER.finditer(normalized))
    results = []
    for i, m in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(normalized)
        block = normalized[m.start():end]
        pairing_id = m.group(1).upper()
        legs, current_day = [], None
        for line in block.splitlines():
            lm = LEG.match(line)
            if not lm:
                continue
            if lm.group(1):
                current_day = lm.group(1)
            rest = lm.group(9)
            eq = re.search(r"\b(3NE|3N1|3NP|321|320|319|75D|73R|73J|221|223)\b", rest)
            legs.append(Leg(
                day=current_day,
                deadhead=bool(lm.group(2)),
                flight=lm.group(3),
                departure=lm.group(4),
                departure_time=lm.group(5),
                arrival=lm.group(6),
                arrival_time=lm.group(7),
                block=lm.group(8),
                aircraft=eq.group(1) if eq else None,
            ))
        layovers = [
            Layover(city=x.group(1), duration=x.group(2), hotel=x.group(3).strip())
            for x in LAYOVER.finditer(block)
        ]
        c, t, ci, eff = CREDIT.search(block), TAFB.search(block), CHECKIN.search(block), EFFECTIVE.search(block)
        confidence = .95 if legs else .6
        result = Pairing(
            pairing_id=pairing_id, raw=block, legs=legs, layovers=layovers,
            credit=c.group(1) if c else None, tafb=t.group(1) if t else None,
            checkin=ci.group(1) if ci else None,
            effective=eff.group(1).strip() if eff else None,
            parser="delta_master_pairing", confidence=confidence,
        ).to_dict()
        result.update(parse_delta_pay(block, result["credit"]))
        result["airline"] = "delta"
        results.append(result)
    return results
