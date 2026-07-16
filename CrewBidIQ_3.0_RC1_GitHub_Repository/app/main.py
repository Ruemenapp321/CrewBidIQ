
from __future__ import annotations

import csv
import io
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from app.parsers import select_parser

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "pairingiq.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pairingiq")

app = FastAPI(title="CrewBidIQ 3.0", version="3.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
job_lock = threading.Lock()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                context TEXT,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                error TEXT,
                results_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


def update_job(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = datetime.utcnow().isoformat(timespec="seconds")
    clause = ", ".join(f"{k}=?" for k in fields)
    with job_lock, db() as conn:
        conn.execute(f"UPDATE jobs SET {clause} WHERE id=?", [*fields.values(), job_id])


def get_job(job_id: str):
    with db() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


INDEX_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#154e78">
<title>CrewBidIQ</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<header>
  <div class="brand">
    <div><h1>CrewBidIQ</h1><p>Build the month you actually want.</p></div>
    <span class="version">v3.0</span>
  </div>
</header>

<main>
<section class="card">
  <h2>1. Upload bid package</h2>
  <div class="grid">
    <label>Bid package<input id="file" type="file" accept=".pdf,.html,.htm,.txt,.csv"></label>
    <label>Airline<input id="airline" placeholder="Example: Delta, American, United"></label>
    <label>Base<input id="baseAirport" placeholder="Example: ATL"></label>
    <label>Fleet / category<input id="fleet" placeholder="Example: A320 Captain"></label>
    <label>Bid month<input id="bidMonth" type="month"></label>
    <label>Package format
      <select id="parserChoice">
        <option value="auto">Auto-detect</option>
        <option value="delta">Delta master pairing package</option>
        <option value="american">American sequence package (beta)</option>
        <option value="southwest">Southwest pairing text package</option>
        <option value="generic">Generic fallback</option>
      </select>
    </label>
  </div>
  <div class="actions">
    <button id="analyzeBtn">Upload and analyze</button>
    <button id="demoBtn" class="secondary">Load demo</button>
  </div>
  <div id="jobPanel" class="job-panel hidden">
    <div class="job-row"><strong id="jobStatus">Preparing…</strong><span id="jobPercent">0%</span></div>
    <div class="progress"><div id="progressFill"></div></div>
    <div id="jobMessage" class="muted small"></div>
  </div>
  <div id="errorBox" class="error hidden"></div>
</section>

<section class="card">
  <div class="section-head">
    <div><h2>2. Trip preferences</h2><p class="muted">These settings work for any fleet or airline.</p></div>
    <button id="saveProfileBtn" class="ghost">Save profile</button>
  </div>

  <div class="grid">
    <label>Elite layover cities<input id="eliteCities" value="BOS,LAX,SFO,SAN,SEA,PDX"></label>
    <label>Secondary cities<input id="secondaryCities" value="DCA,BWI,MIA,FLL,PBI,MCO,TPA,RSW"></label>
    <label>Interesting cities<input id="smallCities" value="TVC,BNA,SAV,CHS,BTV"></label>
    <label>Penalty cities<input id="penaltyCities" value="AUS,SAT,DFW,IAH,HOU,SDQ,STI"></label>
    <label>Preferred aircraft / subtype codes<input id="preferredAircraft" placeholder="Example: NEO, 3NE, 321XLR"></label>
    <label>Maximum deadheads<input id="maxDeadheads" type="number" min="0" value="1"></label>
    <label>Maximum airport transfers<input id="maxTransfers" type="number" min="0" value="0"></label>
    <label>Preferred trip lengths (days)<input id="preferredTripLengths" value="2,3,4"></label>
    <label>Maximum legs per duty day<input id="maxLegsPerDay" type="number" min="1" value="3"></label>
    <label>Minimum layover hours<input id="minLayoverHours" type="number" min="0" value="12"></label>
  </div>
</section>

<section class="card">
  <h2>3. Calendar and quality-of-life preferences</h2>
  <div class="grid">
    <label>Required days off
      <textarea id="requiredDaysOff" placeholder="YYYY-MM-DD, one or more dates separated by commas"></textarea>
    </label>
    <label>Preferred days off
      <textarea id="preferredDaysOff" placeholder="YYYY-MM-DD, one or more dates separated by commas"></textarea>
    </label>
    <label>Holidays / special dates
      <textarea id="holidayDates" placeholder="Example: 2026-12-25,2027-01-01"></textarea>
    </label>
    <label>Preferred weekdays off
      <input id="preferredWeekdays" placeholder="Example: SAT,SUN">
    </label>
    <label>Maximum consecutive work days
      <input id="maxConsecutiveWorkDays" type="number" min="1" value="5">
    </label>
    <label>Minimum consecutive days off
      <input id="minConsecutiveDaysOff" type="number" min="1" value="2">
    </label>
    <label>Earliest report time
      <input id="earliestReport" type="time" value="06:00">
    </label>
    <label>Latest release time
      <input id="latestRelease" type="time" value="22:00">
    </label>
    <label>Commuter: latest acceptable check-in
      <input id="commuterLatestCheckin" type="time" value="08:30">
    </label>
    <label>Commuter: earliest acceptable release
      <input id="commuterEarliestRelease" type="time" value="21:00">
    </label>
  </div>

  <div class="checks">
    <label><input id="preferWeekendsOff" type="checkbox"> Prefer weekends off</label>
    <label><input id="avoidHolidays" type="checkbox"> Avoid working listed holidays</label>
    <label><input id="allowProductiveRedeye" type="checkbox" checked> Allow productive redeyes</label>
    <label><input id="avoidFinalRedeye" type="checkbox"> Avoid final-day redeyes</label>
    <label><input id="avoidReserve" type="checkbox" checked> Avoid reserve / standby lines</label>
    <label><input id="preferOperate" type="checkbox" checked> Prefer operating over deadheading</label>
  </div>
</section>

<section class="card">
  <h2>4. Scoring weights</h2>
  <div class="grid compact">
    <label>Elite city<input id="wElite" type="number" value="28"></label>
    <label>Secondary city<input id="wSecondary" type="number" value="12"></label>
    <label>Interesting city<input id="wSmall" type="number" value="6"></label>
    <label>Penalty city<input id="wPenalty" type="number" value="18"></label>
    <label>Preferred aircraft<input id="wAircraft" type="number" value="20"></label>
    <label>Pure/simple trip<input id="wPure" type="number" value="65"></label>
    <label>Airport transfer<input id="wTransfer" type="number" value="32"></label>
    <label>Extra deadhead<input id="wDeadhead" type="number" value="18"></label>
    <label>Required day conflict<input id="wRequiredConflict" type="number" value="500"></label>
    <label>Preferred day conflict<input id="wPreferredConflict" type="number" value="35"></label>
    <label>Holiday conflict<input id="wHolidayConflict" type="number" value="60"></label>
    <label>Early report<input id="wEarlyReport" type="number" value="20"></label>
    <label>Late release<input id="wLateRelease" type="number" value="20"></label>
  </div>
</section>

<section class="card">
  <div class="section-head">
    <div><h2>5. Ranked pairings</h2><p id="summary" class="muted">No analysis yet.</p></div>
    <div class="actions tight">
      <a id="csvLink" class="button secondary disabled" href="#">Export CSV</a>
      <button id="printBtn" class="ghost">Print</button>
    </div>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Rank</th><th>Pairing</th><th>Score</th><th>Parser</th><th>Credit</th><th>TAFB</th><th>Dates</th><th>Cities</th>
          <th>Layovers</th><th>Aircraft</th><th>Redeye</th><th>DH</th><th>Transfers</th><th>Calendar conflicts</th><th>Why</th>
        </tr>
      </thead>
      <tbody id="results"></tbody>
    </table>
  </div>
</section>

<section class="card">
  <h2>What “fleet generic” means</h2>
  <p class="muted">No airline, aircraft, base, city, holiday, or schedule rule is hardcoded. Pilots can save different profiles for narrowbody, widebody, captain, first officer, commuter, reserve, or different bases. Airline-specific parsers can be added later without changing the preference engine.</p>
</section>
</main>
<script src="/static/app.js"></script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "CrewBidIQ"}


def extract_text(path: Path, suffix: str, job_id: str) -> str:
    if suffix == ".pdf":
        doc = fitz.open(path)
        parts = []
        for i, page in enumerate(doc):
            parts.append(page.get_text("text", sort=True))
            update_job(
                job_id,
                progress=15 + int((i + 1) / max(len(doc), 1) * 45),
                message=f"Extracting PDF page {i + 1} of {len(doc)}",
            )
        doc.close()
        return "\n".join(parts)

    raw = path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".html", ".htm"}:
        raw = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
        raw = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
        raw = re.sub(r"<[^>]+>", " ", raw)
    return raw


def parse_pairings(text: str, job_id: str, parser_choice: str = "auto") -> tuple[list[dict[str, Any]], str]:
    update_job(job_id, progress=65, message="Detecting airline format")
    module, parser_name = select_parser(text, parser_choice)
    pairings = module.parse(text)
    if not pairings:
        raise RuntimeError(
            f"No pairing identifiers detected with the {parser_name} parser. "
            "Try another parser selection or provide a sample package for a custom adapter."
        )
    update_job(job_id, progress=70, message=f"Using {parser_name} parser; found {len(pairings)} pairings")
    return pairings, parser_name

def list_field(value: Any) -> list[str]:
    if isinstance(value, list):
        source = value
    else:
        source = str(value or "").split(",")
    return [str(x).strip().upper() for x in source if str(x).strip()]


def detect_airports(block: str, pairing: dict[str, Any] | None = None) -> list[str]:
    if pairing and pairing.get("legs"):
        out=[]
        for leg in pairing["legs"]:
            for code in (leg.get("departure"), leg.get("arrival")):
                if code and code not in out: out.append(code)
        return out
    excluded = {
        "TOTAL", "CREDIT", "CHECK", "PAGE", "PILOT", "PAIR", "TRIP",
        "FDP", "TAFB", "MAX", "DAY", "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
    }
    out = []
    for code in re.findall(r"\b[A-Z]{3}\b", block.upper()):
        if code not in excluded and code not in out:
            out.append(code)
    return out[:50]


def detect_dates(block: str) -> list[str]:
    patterns = [
        r"\b20\d{2}-\d{2}-\d{2}\b",
        r"\b\d{1,2}[A-Z]{3}20\d{2}\b",
        r"\b\d{1,2}[A-Z]{3}\b",
    ]
    dates = []
    for pattern in patterns:
        for value in re.findall(pattern, block.upper()):
            if value not in dates:
                dates.append(value)
    return dates[:20]


def detect_time_values(block: str) -> list[int]:
    values = []
    for hhmm in re.findall(r"\b(?:[01]\d|2[0-3])[0-5]\d\b", block):
        values.append(int(hhmm[:2]) * 60 + int(hhmm[2:]))
    return values


def score_pairing(pairing: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    block = pairing["block"]
    upper = block.upper()
    cities = detect_airports(block, pairing)
    dates = detect_dates(block)

    elite = set(list_field(profile.get("elite_cities")))
    secondary = set(list_field(profile.get("secondary_cities")))
    small = set(list_field(profile.get("small_cities")))
    penalty = set(list_field(profile.get("penalty_cities")))
    aircraft = list_field(profile.get("preferred_aircraft"))
    base = str(profile.get("base_airport", "")).upper().strip()

    required_days = set(list_field(profile.get("required_days_off")))
    preferred_days = set(list_field(profile.get("preferred_days_off")))
    holiday_dates = set(list_field(profile.get("holiday_dates")))

    w = profile.get("weights", {})
    score = 0.0
    reasons, calendar_conflicts = [], []

    for city in cities:
        if city in elite:
            score += float(w.get("elite", 28)); reasons.append(f"{city}: elite")
        elif city in secondary:
            score += float(w.get("secondary", 12)); reasons.append(f"{city}: secondary")
        elif city in small:
            score += float(w.get("small", 6)); reasons.append(f"{city}: interesting")
        if city in penalty:
            score -= float(w.get("penalty", 18)); reasons.append(f"{city}: penalty")

    parsed_equipment = [leg.get("aircraft") for leg in pairing.get("legs", []) if leg.get("aircraft")]
    aircraft_hits = sorted(set([x for x in aircraft if x and (x in upper or x in parsed_equipment)]))
    score += len(aircraft_hits) * float(w.get("aircraft", 20))
    if aircraft_hits:
        reasons.append(f"{len(aircraft_hits)} preferred-aircraft signal(s)")

    deadheads = sum(1 for leg in pairing.get("legs", []) if leg.get("deadhead")) if pairing.get("legs") else len(re.findall(r"\bDH\b", upper))
    max_dh = int(profile.get("max_deadheads", 1))
    if deadheads == 0 and profile.get("prefer_operate", True):
        score += 10; reasons.append("all-operated signal")
    elif deadheads > max_dh:
        cost = (deadheads - max_dh) * float(w.get("deadhead", 18))
        score -= cost; reasons.append(f"{deadheads} deadheads")

    transfer_pairs = [
        ("SFO", "SJC"), ("JFK", "LGA"), ("JFK", "EWR"),
        ("LGA", "EWR"), ("DCA", "IAD"), ("DCA", "BWI"),
    ]
    transfers = [f"{a}→{b}" for a, b in transfer_pairs if a in cities and b in cities]
    max_transfers = int(profile.get("max_transfers", 0))
    if len(transfers) > max_transfers:
        score -= (len(transfers) - max_transfers) * float(w.get("transfer", 32))
        reasons.append("airport-transfer signal")

    redeye = "none"
    if "REDEYE" in upper:
        redeye = "flagged"
    elif len(re.findall(r"\b(?:2[1-3]|0[0-6])\d{2}\b", upper)) >= 2:
        redeye = "possible"

    if redeye != "none":
        if profile.get("allow_productive_redeye", True):
            score -= 18
        else:
            score -= 55
        reasons.append(f"{redeye} redeye signal")

    date_set = set(dates)
    req_hits = sorted(date_set & required_days)
    pref_hits = sorted(date_set & preferred_days)
    holiday_hits = sorted(date_set & holiday_dates)

    if req_hits:
        score -= len(req_hits) * float(w.get("required_conflict", 500))
        calendar_conflicts.append("Required off: " + ", ".join(req_hits))
    if pref_hits:
        score -= len(pref_hits) * float(w.get("preferred_conflict", 35))
        calendar_conflicts.append("Preferred off: " + ", ".join(pref_hits))
    if holiday_hits and profile.get("avoid_holidays", False):
        score -= len(holiday_hits) * float(w.get("holiday_conflict", 60))
        calendar_conflicts.append("Holiday: " + ", ".join(holiday_hits))

    times = detect_time_values(block)
    if times:
        earliest_report = profile.get("earliest_report_minutes", 360)
        latest_release = profile.get("latest_release_minutes", 1320)
        if min(times) < earliest_report:
            score -= float(w.get("early_report", 20))
            reasons.append("early-time signal")
        if max(times) > latest_release:
            score -= float(w.get("late_release", 20))
            reasons.append("late-time signal")

    elite_non_base = [c for c in cities if c in elite and c != base]
    if base and len(elite_non_base) == 1 and len(cities) <= 5:
        score += float(w.get("pure", 65))
        reasons.append("simple base-to-preferred-city pattern")

    if profile.get("avoid_reserve", True) and re.search(r"\b(RES|RSV|STBY|STANDBY)\b", upper):
        score -= 250
        reasons.append("reserve / standby penalty")

    return {
        "pairing": pairing["id"],
        "score": round(score, 1),
        "dates": dates,
        "cities": cities,
        "preferred_aircraft": aircraft_hits,
        "redeye": redeye,
        "deadheads": deadheads,
        "transfers": transfers,
        "calendar_conflicts": calendar_conflicts,
        "reasons": reasons,
        "parser": pairing.get("parser", "generic"),
        "parser_confidence": pairing.get("confidence", 0),
        "credit": pairing.get("credit"),
        "tafb": pairing.get("tafb"),
        "checkin": pairing.get("checkin"),
        "release": pairing.get("release"),
        "layovers": pairing.get("layovers", []),
        "legs": pairing.get("legs", []),
    }


def process_job(job_id: str, path: Path, suffix: str, profile: dict[str, Any], parser_choice: str) -> None:
    try:
        update_job(job_id, status="processing", progress=5, message="Opening uploaded file")
        text = extract_text(path, suffix, job_id)
        pairings, parser_name = parse_pairings(text, job_id, parser_choice)
        update_job(job_id, progress=75, message=f"Scoring {len(pairings)} pairings")
        results = []
        for i, pairing in enumerate(pairings):
            results.append(score_pairing(pairing, profile))
            if i % 25 == 0:
                update_job(
                    job_id,
                    progress=75 + int((i + 1) / max(len(pairings), 1) * 20),
                    message=f"Scoring pairing {i + 1} of {len(pairings)}",
                )
        results.sort(key=lambda item: item["score"], reverse=True)
        update_job(
            job_id,
            status="complete",
            progress=100,
            message=f"Complete: {len(results)} pairings ranked with {parser_name} parser",
            results_json=json.dumps(results[:1000]),
        )
    except Exception as exc:
        log.exception("Job %s failed", job_id)
        update_job(job_id, status="failed", progress=100, error=str(exc), message="Analysis failed")
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    context: str = Form(""),
    profile_json: str = Form(...),
    parser_choice: str = Form("auto"),
):
    try:
        profile = json.loads(profile_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid preference profile") from exc

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".html", ".htm", ".txt", ".csv"}:
        raise HTTPException(400, "Supported formats: PDF, HTML, HTM, TXT, CSV")

    job_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{job_id}{suffix}"
    total = 0
    with path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > 100 * 1024 * 1024:
                path.unlink(missing_ok=True)
                raise HTTPException(413, "File exceeds 100 MB")
            out.write(chunk)

    with db() as conn:
        conn.execute(
            "INSERT INTO jobs(id,filename,context,status,progress,message) VALUES(?,?,?,?,?,?)",
            (job_id, file.filename or "upload", context, "queued", 1, "Upload received"),
        )

    background_tasks.add_task(process_job, job_id, path, suffix, profile, parser_choice)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    row = get_job(job_id)
    if not row:
        raise HTTPException(404, "Job not found")
    payload = {
        "job_id": row["id"],
        "filename": row["filename"],
        "status": row["status"],
        "progress": row["progress"],
        "message": row["message"],
        "error": row["error"],
    }
    if row["status"] == "complete":
        payload["results"] = json.loads(row["results_json"] or "[]")
    return payload


@app.get("/api/jobs/{job_id}/csv")
def job_csv(job_id: str):
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    results = json.loads(row["results_json"] or "[]")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Rank", "Pairing", "Score", "Dates", "Cities", "Preferred Aircraft",
        "Redeye", "Deadheads", "Transfers", "Calendar Conflicts", "Reasons",
    ])
    for i, item in enumerate(results, 1):
        writer.writerow([
            i, item["pairing"], item["score"], " ".join(item["dates"]),
            " ".join(item["cities"]), " ".join(item["preferred_aircraft"]),
            item["redeye"], item["deadheads"], " ".join(item["transfers"]),
            "; ".join(item["calendar_conflicts"]), "; ".join(item["reasons"]),
        ])
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="pairingiq_{job_id}.csv"'},
    )
