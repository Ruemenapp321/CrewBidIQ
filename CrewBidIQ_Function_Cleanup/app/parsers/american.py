
from __future__ import annotations
import re
from .base import Leg, Layover, Pairing

# Beta adapter for common AA sequence-sheet / trip-sheet text layouts.
HEADERS = [
    re.compile(r"(?mi)^\s*(?:SEQ|SEQUENCE|TRIP|PAIRING)\s*[:#]?\s*([A-Z]?\d{3,6})\b"),
    re.compile(r"(?mi)^\s*([A-Z]?\d{4,6})\s+(?:\d{1,2}D|\d+\s*DAY)\b"),
]
LEG_PATTERNS = [
    re.compile(
        r"^\s*(\d)?\s*(DH\s+)?(?:AA)?(\d{1,4})?\s+([A-Z]{3})\s+(\d{4})\s+"
        r"([A-Z]{3})\s+(\d{4})\s*(?:([A-Z0-9]{2,5}))?.*$"
    ),
    re.compile(
        r"^\s*(\d)?\s*(DH\s+)?([A-Z0-9]{2,6})?\s+([A-Z]{3})[-\s]+([A-Z]{3})\s+"
        r"(\d{4})[-\s]+(\d{4})\s*(?:([A-Z0-9]{2,5}))?.*$"
    ),
]
LAYOVER_PATTERNS = [
    re.compile(r"(?mi)^\s*(?:LAYOVER|RON|OVERNIGHT)\s*[:\-]?\s*([A-Z]{3})(?:\s+(\d{1,2}[:.]\d{2}))?(?:\s+(.+))?$"),
    re.compile(r"(?mi)^\s*([A-Z]{3})\s+(?:LAYOVER|RON)\s+(\d{1,2}[:.]\d{2})(?:\s+(.+))?$"),
]
CREDIT = re.compile(r"(?i)(?:CREDIT|CR)\s*[:\-]?\s*(\d{1,3}[:.]\d{2})")
TAFB = re.compile(r"(?i)(?:TAFB|TIME\s+AWAY)\s*[:\-]?\s*(\d{1,3}[:.]\d{2})")
REPORT = re.compile(r"(?i)(?:REPORT|RPT|SIGN[- ]?IN)\s*[:\-]?\s*(\d{4})")
RELEASE = re.compile(r"(?i)(?:RELEASE|RLS|SIGN[- ]?OUT)\s*[:\-]?\s*(\d{4})")

def detect(text: str) -> float:
    up = text.upper()
    score = 0.0
    if "SEQUENCE" in up: score += .3
    if re.search(r"\bAA\s*\d{1,4}\b", up): score += .2
    if "TAFB" in up or "TIME AWAY" in up: score += .15
    if "RPT" in up or "REPORT" in up: score += .1
    if any(len(p.findall(text)) >= 5 for p in HEADERS): score += .25
    return min(score, 1.0)

def _headers(text):
    found = []
    for pat in HEADERS:
        found.extend((m.start(), m.group(1).upper()) for m in pat.finditer(text))
    found.sort()
    dedup=[]
    seen=set()
    for item in found:
        if item[1] not in seen:
            dedup.append(item); seen.add(item[1])
    return dedup

def parse(text: str) -> list[dict]:
    normalized=text.replace("\r","\n")
    headers=_headers(normalized)
    results=[]
    for i,(start,pid) in enumerate(headers):
        end=headers[i+1][0] if i+1<len(headers) else len(normalized)
        block=normalized[start:end]
        legs=[]; current_day=None
        for line in block.splitlines():
            matched=None
            for idx,pat in enumerate(LEG_PATTERNS):
                lm=pat.match(line)
                if lm:
                    matched=(idx,lm); break
            if not matched: continue
            idx,lm=matched
            if lm.group(1): current_day=lm.group(1)
            if idx==0:
                dep,dtime,arr,atime=lm.group(4),lm.group(5),lm.group(6),lm.group(7)
                flight,eq=lm.group(3),lm.group(8)
            else:
                dep,arr,dtime,atime=lm.group(4),lm.group(5),lm.group(6),lm.group(7)
                flight,eq=lm.group(3),lm.group(8)
            legs.append(Leg(current_day,bool(lm.group(2)),flight,dep,dtime,arr,atime,None,eq))
        layovers=[]
        for pat in LAYOVER_PATTERNS:
            for lm in pat.finditer(block):
                layovers.append(Layover(lm.group(1), lm.group(2), (lm.group(3) or "").strip() or None))
        c,t,r,rl=CREDIT.search(block),TAFB.search(block),REPORT.search(block),RELEASE.search(block)
        results.append(Pairing(
            pairing_id=pid,raw=block,legs=legs,layovers=layovers,
            credit=c.group(1) if c else None,tafb=t.group(1) if t else None,
            checkin=r.group(1) if r else None,release=rl.group(1) if rl else None,
            parser="american_sequence_beta",confidence=.8 if legs else .45
        ).to_dict())
    return results
