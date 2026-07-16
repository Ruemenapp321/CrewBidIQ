
from __future__ import annotations
import re
from .base import Pairing

def detect(text: str) -> float:
    return .1

def parse(text: str) -> list[dict]:
    normalized=text.replace("\r","\n")
    patterns=[
        re.compile(r"(?mi)^\s*#([A-Z]?\d{3,6})\b"),
        re.compile(r"(?mi)^\s*(?:PAIRING|TRIP|SEQ|SEQUENCE)\s*[:#]?\s*([A-Z]?\d{3,6})\b"),
    ]
    matches=[]
    for p in patterns:
        matches.extend((m.start(),m.group(1).upper()) for m in p.finditer(normalized))
    matches.sort()
    if not matches:
        ids=[]
        for token in re.findall(r"\b[A-Z]?\d{4,6}\b",normalized):
            if token not in ids: ids.append(token)
        return [Pairing(x,normalized[:160000],[],[],parser="generic_fallback",confidence=.15).to_dict() for x in ids[:1000]]
    out=[];seen=set()
    for i,(start,pid) in enumerate(matches):
        if pid in seen: continue
        end=matches[i+1][0] if i+1<len(matches) else len(normalized)
        out.append(Pairing(pid,normalized[start:end],[],[],parser="generic_fallback",confidence=.3).to_dict())
        seen.add(pid)
    return out
