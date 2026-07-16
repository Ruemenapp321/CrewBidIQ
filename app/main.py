
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
import zipfile
import shutil
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from app.parsers import select_parser
from app.reporting import build_bid_report

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "pairingiq.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pairingiq")

app = FastAPI(title="CrewBidIQ")
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
                airline TEXT,
                profile_json TEXT,
                uploads_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for name in ("airline", "profile_json", "uploads_json"):
            if name not in columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT")


@app.on_event("startup")
def startup() -> None:
    init_db()
    with db() as conn:
        pending = conn.execute("SELECT * FROM jobs WHERE status IN ('queued','processing')").fetchall()
    for row in pending:
        paths = [Path(p) for p in json.loads(row["uploads_json"] or "[]")]
        if paths and all(path.exists() for path in paths) and row["airline"]:
            profile = json.loads(row["profile_json"] or "{}")
            threading.Thread(target=process_job, args=(row["id"], paths, profile, row["airline"]), daemon=True).start()
        else:
            update_job(row["id"], status="failed", progress=100, error="Analysis was interrupted before it completed. Please upload the package again.", message="Analysis interrupted")


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
<html lang="en" data-theme="system">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#071525">
  <title>CrewBidIQ</title>
  <link rel="stylesheet" href="/static/app.css?v=0401">
</head>
<body>
<div class="app-shell">
  <aside class="desktop-sidebar">
    <div class="side-brand"><span class="wing">✈</span><strong>CrewBid<span>IQ</span></strong></div>
    <nav>
      <a href="#upload" class="nav-link active">⌂ <span>Home</span></a>
      <a href="#upload" class="nav-link">⇧ <span>Upload</span></a>
      <a href="#resultsPanel" class="nav-link">▥ <span>Results</span></a>
      <a href="#preferences" class="nav-link">⚙ <span>Preferences</span></a>
      <button id="guideBtn" class="nav-link nav-button">? <span>User Guide</span></button>
    </nav>
    <div class="side-footer">CrewBidIQ v0.2.4 test</div>
  </aside>

  <div class="app-main">
    <header class="mobile-header">
      <div class="brand-word">CrewBid<span>IQ</span></div>
      <div class="header-controls">
        <select id="themeChoice" aria-label="Appearance"><option value="system">System</option><option value="dark">Dark</option><option value="light">Light</option></select>
        <button id="mobileGuideBtn" class="round-button" aria-label="Open guide">?</button>
      </div>
    </header>

    <main>
      <section class="welcome" id="upload">
        <div>
          <span class="kicker">CLASSIC ANALYZER</span>
          <h1>Find the rotations that fit your life.</h1>
          <p>Upload your bid package, choose what matters, and see a clean ranked list built around your preferences.</p>
        </div>
        <div class="welcome-badge"><span>✦</span><strong>Mobile-first</strong><small>Built for quick scanning</small></div>
      </section>

      <section class="surface upload-surface">
        <div class="surface-title"><div><span class="section-number">1</span><h2>Upload bid package</h2></div><p>PDF for most airlines. Southwest accepts one ZIP or two TXT files.</p></div>
        <div class="upload-layout">
          <label class="select-field">Airline<select id="airlineChoice"><option value="delta">Delta Air Lines</option><option value="southwest">Southwest Airlines</option><option value="american">American Airlines</option><option value="generic">Other airline / generic PDF</option></select></label>
          <div id="pdfUploads" class="drop-zone"><div class="upload-icon">⇧</div><strong>Choose bid-package PDF</strong><span id="pdfFileName">No file selected</span><label class="file-picker" for="pdfFile">Browse files</label><input id="pdfFile" class="native-file-input" type="file" accept=".pdf,application/pdf"></div>
          <div id="southwestUploads" class="drop-zone hidden"><div class="upload-icon">⇧</div><strong>Southwest bid package</strong><span id="southwestZipName">Upload the airline ZIP, or individual TXT files</span><label class="file-picker" for="southwestZip">Choose ZIP</label><input id="southwestZip" class="native-file-input" type="file" accept=".zip,application/zip"><div class="or">OR</div><div class="sw-files"><label>Pairings TXT<input id="southwestPairingsFile" type="file" accept=".txt,text/plain"></label><label>Lines TXT<input id="southwestLinesFile" type="file" accept=".txt,text/plain"></label><label>Seniority TXT<input id="southwestSeniorityFile" type="file" accept=".txt,text/plain"></label><label>Cover TXT<input id="southwestCoverFile" type="file" accept=".txt,text/plain"></label></div></div>
        </div>
        <div class="primary-actions"><button id="analyzeBtn" class="primary">Analyze bid package</button><button id="demoBtn" class="secondary">View sample results</button></div>
        <div id="jobPanel" class="job-panel hidden"><div class="job-row"><strong id="jobStatus">Preparing…</strong><span id="jobPercent">0%</span></div><div class="progress"><div id="progressFill"></div></div><div id="jobMessage" class="muted"></div></div>
        <div id="errorBox" class="error hidden"></div>
      </section>

      <section class="surface" id="preferences">
        <div class="surface-title"><div><span class="section-number">2</span><h2>Your preferences</h2></div><button id="saveProfileBtn" class="text-button">Save on this device</button></div>
        <div class="preference-grid">
          <label>Highest-priority layovers<input id="eliteCities" placeholder="SAN, HNL, BOS"></label>
          <label>Preferred layovers<input id="secondaryCities" placeholder="SEA, PDX, MIA"></label>
          <label>Avoid layovers<input id="penaltyCities" placeholder="DFW, IAH"></label>
          <label>Preferred trip lengths<input id="preferredTripLengths" placeholder="2, 3, 4"></label>
          <label>Earliest report<input id="earliestReport" type="time"></label>
          <label>Latest release<input id="latestRelease" type="time"></label>
        </div>
        <div class="preference-chips">
          <label><input id="preferWeekendsOff" type="checkbox"><span>Weekends off</span></label>
          <label><input id="avoidHolidays" type="checkbox"><span>Avoid holidays</span></label>
          <label><input id="workHolidays" type="checkbox"><span>Work holidays</span></label>
          <label><input id="allowRedeyeStart" type="checkbox"><span>Allow redeye starts</span></label>
          <label><input id="allowMidRotationRedeye" type="checkbox"><span>Allow mid-rotation redeyes</span></label>
          <label><input id="avoidFinalRedeye" type="checkbox"><span>Avoid final redeyes</span></label>
        </div>
        <details class="advanced"><summary>Advanced duty preferences</summary>
          <div class="preference-grid">
            <label>Maximum legs any duty day<input id="maxLegsPerDay" type="number" min="1" placeholder="3"></label>
            <label>Maximum first-day legs<input id="maxFirstDayLegs" type="number" min="1" placeholder="2"></label>
            <label>Maximum last-day legs<input id="maxLastDayLegs" type="number" min="1" placeholder="2"></label>
            <label>Maximum legs after redeye rest<input id="maxLegsAfterRedeye" type="number" min="0" placeholder="2"></label>
            <label>Minimum layover hours<input id="minLayoverHours" type="number" min="0" placeholder="12"></label>
            <label>Maximum deadheads<input id="maxDeadheads" type="number" min="0" placeholder="1"></label>
            <label>Required days off<textarea id="requiredDaysOff" placeholder="YYYY-MM-DD, separated by commas"></textarea></label>
            <label>Preferred days off<textarea id="preferredDaysOff" placeholder="YYYY-MM-DD, separated by commas"></textarea></label>
            <label>Holidays / special dates<textarea id="holidayDates" placeholder="YYYY-MM-DD, separated by commas"></textarea></label>
            <label>Preferred weekdays off<input id="preferredWeekdays" placeholder="SAT,SUN"></label>
            <label>Preferred aircraft codes<input id="preferredAircraft" placeholder="NEO,321"></label>
            <label>Maximum transfers<input id="maxTransfers" type="number" min="0" placeholder="0"></label>
          </div>
        </details>
        <input id="smallCities" type="hidden"><input id="maxConsecutiveWorkDays" type="hidden"><input id="minConsecutiveDaysOff" type="hidden"><input id="avoidReserve" type="hidden"><input id="preferOperate" type="hidden">
        <div class="hidden-weight-fields"><input id="wElite" type="hidden" value="28"><input id="wSecondary" type="hidden" value="12"><input id="wSmall" type="hidden" value="6"><input id="wPenalty" type="hidden" value="18"><input id="wAircraft" type="hidden" value="20"><input id="wPure" type="hidden" value="65"><input id="wTransfer" type="hidden" value="32"><input id="wDeadhead" type="hidden" value="18"><input id="wRequiredConflict" type="hidden" value="500"><input id="wPreferredConflict" type="hidden" value="35"><input id="wHolidayConflict" type="hidden" value="60"><input id="wEarlyReport" type="hidden" value="20"><input id="wLateRelease" type="hidden" value="20"></div>
      </section>

      <section class="results-section" id="resultsPanel">
        <div class="results-header">
          <div><span class="kicker">YOUR RESULTS</span><h2 id="resultsTitle">Recommended rotations</h2><p id="summary">Load sample results or analyze a bid package.</p></div>
          <div class="results-actions"><select id="resultLimit"><option value="25">Top 25</option><option value="50">Top 50</option><option value="100">Top 100</option><option value="all">All</option></select><a id="csvLink" class="secondary button disabled" href="#">PDF report</a></div>
        </div>
        <div class="snapshot" id="snapshot">
          <div><span>Top match</span><strong id="snapshotMatch">—</strong></div>
          <div><span>Credit</span><strong id="snapshotCredit">—</strong></div>
          <div><span>Trip length</span><strong id="snapshotLength">—</strong></div>
          <div><span>Recovery</span><strong id="snapshotRecovery">—</strong></div>
        </div>
        <div id="results" class="ranked-list"><div class="empty-state"><span>✈</span><strong>No results yet</strong><p>Your ranked rotations will appear here.</p></div></div>
      </section>

      <section id="guide" class="surface guide-panel hidden">
        <div class="surface-title"><div><span class="section-number">?</span><h2>Complete User Guide</h2></div><button id="closeGuideBtn" class="text-button">Close</button></div>
        <h3>Airline terminology</h3><p><strong>Rotation:</strong> Delta’s term for a complete trip sequence. <strong>Pairing:</strong> the term used by many other airlines. CrewBidIQ changes labels automatically.</p>
        <h3>Match ratings</h3><p><strong>Excellent, Strong, Good, Fair, and Low Match</strong> describe how closely a rotation follows your selected preferences. The hidden numerical score only determines rank.</p>
        <h3>Duty legs</h3><p><strong>Legs by day</strong> shows working flight segments in each duty period. <strong>First-day legs</strong>, <strong>last-day legs</strong>, and <strong>legs after redeye rest</strong> identify demanding duty patterns.</p>
        <h3>Redeyes</h3><p><strong>Redeye start</strong> begins the trip overnight. <strong>Mid-rotation redeye</strong> occurs before the final duty period. Recovery describes the duty workload after required rest.</p>
        <h3>Conflicts</h3><p>A conflict is any required day off, preferred day off, holiday, time limit, or duty limit that the rotation violates.</p>
        <h3>Soft credit</h3><p>Delta displays EDP, HOL, and SIT when detected. Other airlines display N/A until airline-specific rules are defined.</p>
        <h3>Storage</h3><p>Preferences are saved only in this browser. Uploaded files are processed by the server and should always be verified against the original airline bid package.</p>
      </section>
    </main>

    <nav class="bottom-nav">
      <a href="#upload"><span>⌂</span>Home</a><a href="#upload"><span>⇧</span>Upload</a><a href="#resultsPanel" class="active"><span>▥</span>Results</a><a href="#preferences"><span>⚙</span>Preferences</a>
    </nav>
  </div>
</div>
<script src="/static/app.js?v=0401"></script>
<script>document.getElementById('mobileGuideBtn').addEventListener('click',()=>document.getElementById('guideBtn').click());</script>
</body></html>
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


def detect_layover_cities(pairing: dict[str, Any]) -> list[str]:
    """Return true overnight/rest cities only, never every airport touched.

    Prefer parser-provided layovers. If a parser did not provide them, infer
    overnight cities from the final arrival of each duty period except the
    final duty period.
    """
    out: list[str] = []
    for layover in pairing.get("layovers", []) or []:
        city = str(layover.get("city") or "").strip().upper()
        if city and city not in out:
            out.append(city)
    if out:
        return out

    legs = pairing.get("legs", []) or []
    if not legs:
        return out

    duty_order: list[str] = []
    last_arrival_by_duty: dict[str, str] = {}
    for index, leg in enumerate(legs):
        duty = str(leg.get("day") or "1")
        if duty not in duty_order:
            duty_order.append(duty)
        arrival = str(leg.get("arrival") or "").strip().upper()
        if arrival:
            last_arrival_by_duty[duty] = arrival

    for duty in duty_order[:-1]:
        city = last_arrival_by_duty.get(duty)
        if city and city not in out:
            out.append(city)
    return out


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


def match_level(score: float, conflicts: list[str]) -> str:
    if conflicts and any(value.startswith("Required off:") for value in conflicts):
        return "low"
    if score >= 60:
        return "excellent"
    if score >= 30:
        return "strong"
    if score >= 10:
        return "good"
    if score >= 0:
        return "fair"
    return "low"


def score_pairing(pairing: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    block = pairing["block"]
    upper = block.upper()
    touched_cities = detect_airports(block, pairing)
    cities = detect_layover_cities(pairing)
    dates = list_field(pairing.get("effective")) if pairing.get("effective") else detect_dates(block)

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
            score += float(w.get("elite") or 28); reasons.append(f"{city} is a highest-priority overnight")
        elif city in secondary:
            score += float(w.get("secondary") or 12); reasons.append(f"{city} is a preferred overnight")
        elif city in small:
            score += float(w.get("small") or 6); reasons.append(f"{city} matches your interesting-city list")
        if city in penalty:
            score -= float(w.get("penalty") or 18); reasons.append(f"{city} is an overnight you prefer to avoid")

    parsed_equipment = [leg.get("aircraft") for leg in pairing.get("legs", []) if leg.get("aircraft")]
    aircraft_hits = sorted(set([x for x in aircraft if x and (x in upper or x in parsed_equipment)]))
    score += len(aircraft_hits) * float(w.get("aircraft") or 20)
    if aircraft_hits:
        reasons.append("Includes preferred aircraft: " + ", ".join(aircraft_hits))

    deadheads = sum(1 for leg in pairing.get("legs", []) if leg.get("deadhead")) if pairing.get("legs") else len(re.findall(r"\bDH\b", upper))
    max_dh = int(profile.get("max_deadheads") if profile.get("max_deadheads") is not None else 1)
    if deadheads == 0 and profile.get("prefer_operate", True):
        score += 10; reasons.append("all-operated signal")
    elif deadheads > max_dh:
        cost = (deadheads - max_dh) * float(w.get("deadhead") or 18)
        score -= cost; reasons.append(f"Has {deadheads} deadheads, above your limit of {max_dh}")

    transfer_pairs = [
        ("SFO", "SJC"), ("JFK", "LGA"), ("JFK", "EWR"),
        ("LGA", "EWR"), ("DCA", "IAD"), ("DCA", "BWI"),
    ]
    transfers = [f"{a}→{b}" for a, b in transfer_pairs if a in touched_cities and b in touched_cities]
    max_transfers = int(profile.get("max_transfers") if profile.get("max_transfers") is not None else 0)
    if len(transfers) > max_transfers:
        score -= (len(transfers) - max_transfers) * float(w.get("transfer") or 32)
        reasons.append("Requires an airport transfer")

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
        score -= len(req_hits) * float(w.get("required_conflict") or 500)
        calendar_conflicts.append("Required off: " + ", ".join(req_hits))
    if pref_hits:
        score -= len(pref_hits) * float(w.get("preferred_conflict") or 35)
        calendar_conflicts.append("Preferred off: " + ", ".join(pref_hits))
    if holiday_hits and profile.get("avoid_holidays", False):
        score -= len(holiday_hits) * float(w.get("holiday_conflict") or 60)
        calendar_conflicts.append("Holiday: " + ", ".join(holiday_hits))

    times = detect_time_values(block)
    if times:
        earliest_report = profile.get("earliest_report_minutes", 360)
        latest_release = profile.get("latest_release_minutes", 1320)
        if min(times) < earliest_report:
            score -= float(w.get("early_report") or 20)
            reasons.append("Reports earlier than your preferred start time")
        if max(times) > latest_release:
            score -= float(w.get("late_release") or 20)
            reasons.append("Releases later than your preferred end time")

    elite_non_base = [c for c in cities if c in elite and c != base]
    if base and len(elite_non_base) == 1 and len(cities) <= 5:
        score += float(w.get("pure") or 65)
        reasons.append("simple base-to-preferred-city pattern")

    if profile.get("avoid_reserve", True) and re.search(r"\b(RES|RSV|STBY|STANDBY)\b", upper):
        score -= 250
        reasons.append("reserve / standby penalty")

    working_legs = [leg for leg in pairing.get("legs", []) if not leg.get("deadhead")]
    duty_counts = []
    duty_labels = []
    for leg in working_legs:
        day = leg.get("day") or "1"
        if day not in duty_labels:
            duty_labels.append(day)
            duty_counts.append(0)
        duty_counts[duty_labels.index(day)] += 1

    preferred_lengths = {int(x) for x in list_field(profile.get("preferred_trip_lengths")) if x.isdigit()}
    if duty_counts and len(duty_counts) in preferred_lengths:
        score += 18
        reasons.append(f"Matches your preferred {len(duty_counts)}-day trip length")
    limits = (("max_legs_per_day", max(duty_counts, default=0), "maximum legs in a duty day"), ("max_first_day_legs", duty_counts[0] if duty_counts else 0, "first-day legs"), ("max_last_day_legs", duty_counts[-1] if duty_counts else 0, "last-day legs"))
    for key, actual, label in limits:
        limit = profile.get(key)
        if limit is not None and actual > int(limit):
            score -= 20 * (actual - int(limit))
            reasons.append(f"Has {actual} {label}, above your limit of {limit}")
    min_layover = profile.get("min_layover_hours")
    if min_layover is not None:
        short = []
        for layover in pairing.get("layovers", []) or []:
            duration = str(layover.get("duration") or "").replace(".", ":")
            if re.fullmatch(r"\d{1,2}:\d{2}", duration):
                hours, minutes = map(int, duration.split(":"))
                if hours + minutes / 60 < float(min_layover):
                    short.append(layover.get("city"))
        if short:
            score -= 25 * len(short)
            reasons.append("Shorter-than-preferred overnight in " + ", ".join(str(x) for x in short))

    level = match_level(score, calendar_conflicts)
    return {
        "pairing": pairing["id"],
        "score": round(score, 1),
        "dates": dates,
        "cities": cities,
        "touched_cities": touched_cities,
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
        "duty_legs": duty_counts,
        "first_day_legs": duty_counts[0] if duty_counts else 0,
        "last_day_legs": duty_counts[-1] if duty_counts else 0,
        "soft_credit": " ".join(re.findall(r"\b(?:\d{1,3})?(?:EDP|HOL|SIT)\b", upper)) or None,
        "item_type": "pairing",
        "match_level": level,
        "display_label": "Rotation" if pairing.get("parser", "").startswith("delta") else "Pairing",
        "original_display": block,
    }


def extract_archive_text(zip_path: Path, target_dir: Path, job_id: str, label: str) -> str:
    target_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        safe_members = [m for m in archive.infolist() if not m.is_dir() and ".." not in Path(m.filename).parts]
        if not safe_members:
            raise RuntimeError(f"The {label} ZIP does not contain any files.")
        for index, member in enumerate(safe_members, 1):
            suffix = Path(member.filename).suffix.lower()
            if suffix not in {".pdf", ".html", ".htm", ".txt", ".csv"}:
                continue
            extracted = target_dir / f"{index}_{Path(member.filename).name}"
            with archive.open(member) as src, extracted.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            chunks.append(extract_text(extracted, suffix, job_id))
            update_job(job_id, progress=min(60, 10 + int(index / max(len(safe_members), 1) * 45)), message=f"Reading {label} file {index} of {len(safe_members)}")
    if not chunks:
        raise RuntimeError(f"The {label} ZIP contains no supported PDF, HTML, TXT, or CSV files.")
    return "\n\n".join(chunks)


def parse_southwest_lines(text: str, pairing_ids: set[str]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    normalized = text.replace("\r", "\n")
    # Common line identifiers: LINE 123, L123, or a leading numeric/alphanumeric token.
    headers = list(re.finditer(r"(?im)^\s*(?:LINE\s*#?\s*)?([A-Z]{0,2}\d{1,5})\b[^\n]*$", normalized))
    for i, match in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(normalized)
        block = normalized[match.start():end]
        refs = []
        for token in re.findall(r"\b[A-Z0-9]{4,6}\b", block.upper()):
            if token in pairing_ids and token not in refs:
                refs.append(token)
        if refs:
            lines.append({"id": match.group(1).upper(), "pairing_ids": refs, "block": block})
    if not lines:
        # Fallback: one record per row containing recognizable pairing IDs.
        for row_no, row in enumerate(normalized.splitlines(), 1):
            refs = [token for token in re.findall(r"\b[A-Z0-9]{4,6}\b", row.upper()) if token in pairing_ids]
            refs = list(dict.fromkeys(refs))
            if refs:
                first = re.match(r"\s*([A-Z0-9-]+)", row)
                lines.append({"id": first.group(1) if first else f"LINE-{row_no}", "pairing_ids": refs, "block": row})
    # De-duplicate identical line id / pairing combinations.
    unique = {}
    for line in lines:
        unique[(line["id"], tuple(line["pairing_ids"]))] = line
    return list(unique.values())


def score_southwest_line(line: dict[str, Any], pairing_scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    members = [pairing_scores[p] for p in line["pairing_ids"] if p in pairing_scores]
    if not members:
        raise RuntimeError(f"No pairing details found for Southwest line {line['id']}")
    cities = list(dict.fromkeys(c for item in members for c in item.get("cities", [])))
    layovers = []
    for item in members:
        for layover in item.get("layovers", []):
            key = (layover.get("city"), layover.get("duration"))
            if key not in [(x.get("city"), x.get("duration")) for x in layovers]:
                layovers.append(layover)
    reasons = []
    for item in members:
        reasons.extend(item.get("reasons", []))
    credits = [item.get("credit") for item in members if item.get("credit")]
    score = round(sum(x["score"] for x in members) / len(members), 1)
    conflicts = sorted(set(c for x in members for c in x.get("calendar_conflicts", [])))
    touched = list(dict.fromkeys(c for item in members for c in item.get("touched_cities", [])))
    duty_legs = [count for item in members for count in item.get("duty_legs", [])]
    return {
        "pairing": line["id"], "item_type": "line", "score": score,
        "dates": [], "cities": cities, "touched_cities": touched, "preferred_aircraft": sorted(set(a for item in members for a in item.get("preferred_aircraft", []))),
        "redeye": "flagged" if any(x.get("redeye") != "none" for x in members) else "none",
        "deadheads": sum(x.get("deadheads", 0) for x in members), "transfers": sorted(set(t for x in members for t in x.get("transfers", []))),
        "calendar_conflicts": conflicts,
        "reasons": [f"Contains pairings: {', '.join(line['pairing_ids'])}"] + list(dict.fromkeys(reasons))[:12],
        "parser": "southwest_lines", "parser_confidence": min(x.get("parser_confidence", 0) for x in members),
        "credit": " + ".join(credits) if credits else None, "tafb": None, "checkin": None, "release": None,
        "layovers": layovers, "legs": [leg for item in members for leg in item.get("legs", [])], "soft_credit": None, "pairing_ids": line["pairing_ids"],
        "duty_legs": duty_legs, "first_day_legs": duty_legs[0] if duty_legs else 0, "last_day_legs": duty_legs[-1] if duty_legs else 0,
        "match_level": match_level(score, conflicts), "display_label": "Line", "original_display": line.get("block", ""),
    }


def validate_uploaded_path(path: Path, expected: str) -> None:
    if path.stat().st_size == 0:
        raise HTTPException(400, "The selected file is empty.")
    signature = path.read_bytes()[:8]
    if expected == ".pdf" and not signature.startswith(b"%PDF-"):
        raise HTTPException(400, "The selected file is not a valid PDF.")
    if expected == ".zip":
        if not zipfile.is_zipfile(path):
            raise HTTPException(400, "The selected file is not a valid ZIP archive.")
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) > 100:
                raise HTTPException(400, "The ZIP contains too many files.")
            if any(".." in Path(m.filename).parts or Path(m.filename).is_absolute() for m in members):
                raise HTTPException(400, "The ZIP contains an unsafe filename.")
            if sum(m.file_size for m in members) > 250 * 1024 * 1024:
                raise HTTPException(413, "The expanded ZIP exceeds 250 MB.")
            if archive.testzip() is not None:
                raise HTTPException(400, "The ZIP archive is damaged.")


def process_job(job_id: str, paths: list[Path], profile: dict[str, Any], airline: str) -> None:
    work_dir = UPLOAD_DIR / f"{job_id}_work"
    try:
        update_job(job_id, status="processing", progress=5, message="Opening uploaded file(s)")
        if airline == "southwest":
            if len(paths) == 1 and paths[0].suffix.lower() == ".zip":
                with zipfile.ZipFile(paths[0]) as archive:
                    members = [m for m in archive.infolist() if not m.is_dir() and ".." not in Path(m.filename).parts]
                    pairing_chunks, line_chunks = [], []
                    for i, member in enumerate(members, 1):
                        name = Path(member.filename).name.lower()
                        if Path(name).suffix.lower() not in {".txt", ".csv", ".html", ".htm"}:
                            continue
                        raw = archive.read(member).decode("utf-8", errors="ignore")
                        stem = Path(name).stem.upper()
                        # Southwest airline packages commonly use compact names such as
                        # LAXFOP.TXT (pairings), LAXFOL.TXT (lines), LAXFOS.TXT
                        # (seniority), and LAXFOC.TXT (cover).
                        if "PAIR" in stem or stem.endswith("P"):
                            pairing_chunks.append(raw)
                        elif "LINE" in stem or stem.endswith("L"):
                            line_chunks.append(raw)
                    if not pairing_chunks or not line_chunks:
                        raise RuntimeError("The Southwest ZIP must contain both a Pairings file and a Lines file.")
                    pairings_text = "\n\n".join(pairing_chunks)
                    lines_text = "\n\n".join(line_chunks)
            else:
                pairings_text = extract_text(paths[0], paths[0].suffix.lower(), job_id)
                lines_text = extract_text(paths[1], paths[1].suffix.lower(), job_id)
            pairings, parser_name = parse_pairings(pairings_text, job_id, "southwest")
            update_job(job_id, progress=72, message=f"Matching {len(pairings)} pairings to offered lines")
            scored_pairings = {p["id"]: score_pairing(p, profile) for p in pairings}
            lines = parse_southwest_lines(lines_text, set(scored_pairings))
            if not lines:
                raise RuntimeError("No Southwest lines could be matched to the pairing IDs. Confirm that the correct Pairings and Lines ZIP files were uploaded.")
            results = [score_southwest_line(line, scored_pairings) for line in lines]
            item_label = "lines"
        else:
            text = extract_text(paths[0], paths[0].suffix.lower(), job_id)
            pairings, parser_name = parse_pairings(text, job_id, airline if airline in {"delta", "american"} else "auto")
            update_job(job_id, progress=75, message=f"Scoring {len(pairings)} pairings")
            results = [score_pairing(pairing, profile) for pairing in pairings]
            item_label = "pairings"
        results.sort(key=lambda item: item["score"], reverse=True)
        update_job(job_id, status="complete", progress=100, message=f"Complete: {len(results)} {item_label} ranked", results_json=json.dumps(results))
    except Exception as exc:
        log.exception("Job %s failed", job_id)
        update_job(job_id, status="failed", progress=100, error=str(exc), message="Analysis failed")
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    airline: str = Form(...),
    profile_json: str = Form(...),
    context: str = Form(""),
    file: UploadFile | None = File(None),
    pairings_file: UploadFile | None = File(None),
    lines_file: UploadFile | None = File(None),
    seniority_file: UploadFile | None = File(None),
    cover_file: UploadFile | None = File(None),
):
    try:
        profile = json.loads(profile_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid preference profile") from exc
    airline = airline.lower().strip()
    uploads: list[UploadFile]
    if airline == "southwest":
        if file:
            if Path(file.filename or "").suffix.lower() != ".zip":
                raise HTTPException(400, "Southwest combined upload must be one ZIP containing Lines and Pairings.")
            uploads = [file]
        elif pairings_file and lines_file:
            optional_uploads = [x for x in (seniority_file, cover_file) if x is not None]
            if any(Path(x.filename or "").suffix.lower() != ".txt" for x in (pairings_file, lines_file, *optional_uploads)):
                raise HTTPException(400, "Southwest individual uploads must be text files.")
            uploads = [pairings_file, lines_file, *optional_uploads]
        else:
            raise HTTPException(400, "Upload one Southwest ZIP, or at least the Pairings and Lines text files.")
    elif airline in {"delta", "american", "generic"}:
        if not file:
            raise HTTPException(400, "Choose a bid-package PDF.")
        if Path(file.filename or "").suffix.lower() != ".pdf":
            raise HTTPException(400, "This airline requires one PDF bid package.")
        uploads = [file]
    else:
        raise HTTPException(400, "That airline is not supported yet.")

    job_id = uuid.uuid4().hex
    paths: list[Path] = []
    try:
        for index, upload in enumerate(uploads):
            suffix = Path(upload.filename or "").suffix.lower()
            path = UPLOAD_DIR / f"{job_id}_{index}{suffix}"
            total = 0
            with path.open("wb") as out:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise HTTPException(413, "A file exceeds 100 MB")
                    out.write(chunk)
            validate_uploaded_path(path, suffix)
            paths.append(path)
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        candidate = locals().get("path")
        if isinstance(candidate, Path):
            candidate.unlink(missing_ok=True)
        raise
    filenames = " + ".join(x.filename or "upload" for x in uploads)
    with db() as conn:
        conn.execute("INSERT INTO jobs(id,filename,context,status,progress,message,airline,profile_json,uploads_json) VALUES(?,?,?,?,?,?,?,?,?)", (job_id, filenames, context, "queued", 1, "Upload received", airline, json.dumps(profile), json.dumps([str(path) for path in paths])))
    background_tasks.add_task(process_job, job_id, paths, profile, airline)
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


@app.get("/api/jobs/{job_id}/report.pdf")
@app.get("/api/jobs/{job_id}/csv", include_in_schema=False)
def job_report(job_id: str):
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    results = json.loads(row["results_json"] or "[]")
    pdf = build_bid_report(results, json.loads(row["profile_json"] or "{}"), row["airline"] or row["context"] or "airline", row["filename"])
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="crewbidiq_{job_id}.pdf"'})
