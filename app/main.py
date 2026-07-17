
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
import gzip
import hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from app.airlines import airline_terminology_payload, get_airline_terminology
from app.airports import coterminal_group_for_airport, coterminal_payload, expand_airports
from app.canonical import attach_canonical_trip, canonical_presentation_record, public_canonical_trip
from app.destinations import is_transcontinental, taxonomy_payload
from app.fatigue import build_fatigue_index
from app.labs import labs_enabled, router as labs_router
from app.month_planner import build_month_plan
from app.navblue import build_navblue_layers
from app.pay import pay_minutes_per_duty_day, pay_priority_value, tfp_per_day_away, tfp_ratio
from app.parsers import select_parser
from app.recommendations import (
    evaluate_recommendation,
    length_priority,
    length_score_contribution,
    recommendation_pipeline,
)
from app.reporting import build_bid_report
from app.seniority import build_seniority_context, estimate_hold_outlook
from app.southwest_planning import optimize_schedule_conflicts, rank_southwest_line
from app.southwest_time import public_local_leg
from app.trip_intent import interpret_trip_intent, trip_intent_profile

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "pairingiq.db"
PARSER_CACHE_VERSION = "2026-07-17.2"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_PARSE_SECONDS = int(os.environ.get("MAX_PARSE_SECONDS", "600"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pairingiq")

app = FastAPI(title="CrewBidIQ")
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
app.include_router(labs_router)
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
                source_json TEXT,
                package_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS parse_cache (
                cache_key TEXT PRIMARY KEY,
                airline TEXT NOT NULL,
                parser_name TEXT NOT NULL,
                pairings_gzip BLOB NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT DEFAULT CURRENT_TIMESTAMP,
                hit_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for name in ("airline", "profile_json", "uploads_json", "source_json", "package_id"):
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


def parser_cache_key(path: Path, airline: str) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return f"{PARSER_CACHE_VERSION}:{airline.lower()}:{digest.hexdigest()}"


def load_cached_pairings(cache_key: str) -> tuple[list[dict[str, Any]], str] | None:
    with job_lock, db() as conn:
        row = conn.execute("SELECT parser_name,pairings_gzip FROM parse_cache WHERE cache_key=?", (cache_key,)).fetchone()
        if row:
            conn.execute(
                "UPDATE parse_cache SET last_used_at=CURRENT_TIMESTAMP,hit_count=hit_count+1 WHERE cache_key=?",
                (cache_key,),
            )
    if not row:
        return None
    try:
        pairings = json.loads(gzip.decompress(row["pairings_gzip"]).decode("utf-8"))
        return pairings, row["parser_name"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        log.warning("Removing unreadable parser cache entry %s", cache_key)
        with job_lock, db() as conn:
            conn.execute("DELETE FROM parse_cache WHERE cache_key=?", (cache_key,))
        return None


def store_cached_pairings(cache_key: str, airline: str, parser_name: str, pairings: list[dict[str, Any]]) -> None:
    raw = json.dumps(pairings, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=5)
    with job_lock, db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO parse_cache
               (cache_key,airline,parser_name,pairings_gzip,created_at,last_used_at,hit_count)
               VALUES(?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,0)""",
            (cache_key, airline, parser_name, compressed),
        )


def source_pairings(source: dict[str, Any]) -> list[dict[str, Any]]:
    if source.get("pairings") is not None:
        return source.get("pairings") or []
    cache_key = source.get("cache_key")
    cached = load_cached_pairings(cache_key) if cache_key else None
    return cached[0] if cached else []


def row_package_id(row: sqlite3.Row) -> str:
    """Return the upload identity, with job id as the legacy migration identity."""
    return str(row["package_id"] or row["id"])


def bind_pairings_to_package(pairings: list[dict[str, Any]], package_id: str) -> list[dict[str, Any]]:
    """Clone cached/parser records into exactly one upload package namespace."""
    bound: list[dict[str, Any]] = []
    for original in pairings:
        pairing = dict(original)
        rotation = str(pairing.get("rotation_number") or pairing.get("id") or "").upper()
        if "bidable_inventory_confirmed" not in pairing:
            page_classification = str(pairing.get("page_classification") or "").upper()
            source_context = " ".join(
                str(pairing.get(key) or "")
                for key in ("source_section", "fleet_section", "block")
            )
            instructional = page_classification in {
                "COVER", "CONTENTS", "INSTRUCTIONS", "REFERENCE", "EXAMPLE", "HOTEL_LIST", "APPENDIX",
            } or bool(re.search(
                r"\b(?:EXAMPLE|SAMPLE|ILLUSTRATION|TRAINING EXAMPLE|NOT FOR BIDDING|FOR REFERENCE)\b",
                source_context,
                re.I,
            ))
            # Legacy caches predate explicit provenance. Normalize them once at
            # this boundary, while failing closed on instructional context.
            pairing["bidable_inventory_confirmed"] = not instructional
        pairing["package_id"] = package_id
        pairing["inventory_key"] = f"{package_id}:{rotation}"
        bound.append(attach_canonical_trip(pairing, package_id))
    return bound


def package_records(records: list[dict[str, Any]], package_id: str) -> list[dict[str, Any]]:
    """Fail closed if any package-dependent record crosses an upload boundary."""
    mismatches = [record for record in records if str(record.get("package_id") or "") != package_id]
    if mismatches:
        raise HTTPException(409, "Package isolation check rejected records from another bid package.")
    return records


def require_active_package(row: sqlite3.Row, supplied_package_id: str | None) -> str:
    expected = row_package_id(row)
    if row["package_id"] and not supplied_package_id:
        raise HTTPException(400, "active package_id is required")
    if supplied_package_id and supplied_package_id != expected:
        raise HTTPException(409, "The requested package is no longer active. Reload the current bid package.")
    return expected


INDEX_HTML = r"""
<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#071525">
  <title>CrewBidIQ</title>
  <link rel="stylesheet" href="/static/app.css?v=0424">
</head>
<body data-classic-page="__CLASSIC_PAGE__">
<div class="app-shell">
  <aside class="desktop-sidebar">
    <div class="side-brand"><span class="wing">✈</span><strong>CrewBid<span>IQ</span></strong></div>
    <nav>
      <a href="/" class="nav-link __HOME_ACTIVE__">⌂ <span>Home</span></a>
      <a href="/#upload" class="nav-link">⇧ <span>Upload</span></a>
      <a href="/results" class="nav-link __RESULTS_ACTIVE__">▥ <span>Results</span></a>
      <a href="/#preferences" class="nav-link">⚙ <span>Preferences</span></a>
      <button id="guideBtn" class="nav-link nav-button"><span>User Guide</span></button>
    </nav>
    <div class="side-footer">CrewBidIQ v0.2.4 test</div>
  </aside>

  <div class="app-main">
    <header class="mobile-header">
      <div class="header-identity">
        <a class="brand-word" href="/">CrewBid<span>IQ</span></a>
        __LABS_SWITCH__
      </div>
      <div class="header-controls">
        <button id="mobileGuideBtn" class="round-button guide-button" aria-label="Open user guide">Guide</button>
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
          <div class="airline-step"><div class="upload-step-heading"><span>1</span><div><strong>Select your airline</strong><small>This determines which parser CrewBidIQ uses.</small></div></div><label class="select-field">Airline<select id="airlineChoice" required><option value="" selected disabled>Select an airline</option><option value="delta">Delta Air Lines</option><option value="southwest">Southwest Airlines</option><option value="american">American Airlines</option><option value="generic">Other airline / generic PDF</option></select></label></div>
          <div id="uploadLocked" class="drop-zone upload-locked"><span class="locked-step">2</span><strong>Select an airline to unlock upload</strong><span>This prevents the wrong airline parser from reading your package.</span></div>
          <div id="pdfUploads" class="drop-zone hidden"><span class="drop-step">2 · Upload package</span><div class="upload-icon">⇧</div><strong>Choose bid-package PDF</strong><span id="pdfFileName">No file selected</span><label class="file-picker" for="pdfFile">Browse files</label><input id="pdfFile" class="native-file-input" type="file" accept=".pdf,application/pdf"></div>
          <div id="southwestUploads" class="drop-zone hidden"><span class="drop-step">2 · Upload package</span><div class="upload-icon">⇧</div><strong>Southwest bid package</strong><span id="southwestZipName" data-empty-text="Upload the airline ZIP, or individual TXT files">Upload the airline ZIP, or individual TXT files</span><label class="file-picker" for="southwestZip">Choose ZIP</label><input id="southwestZip" class="native-file-input" type="file" accept=".zip,application/zip"><div class="or">OR</div><div class="sw-files"><label>Pairings TXT<input id="southwestPairingsFile" type="file" accept=".txt,text/plain"></label><label>Lines TXT<input id="southwestLinesFile" type="file" accept=".txt,text/plain"></label><label>Seniority TXT<input id="southwestSeniorityFile" type="file" accept=".txt,text/plain"></label><label>Cover TXT<input id="southwestCoverFile" type="file" accept=".txt,text/plain"></label></div></div>
        </div>
        <div class="primary-actions"><button id="analyzeBtn" class="primary" disabled>Analyze bid package</button><button id="demoBtn" class="secondary">View sample results</button></div>
        <div id="jobPanel" class="job-panel hidden"><div class="job-row"><strong id="jobStatus">Preparing…</strong><span id="jobPercent">0%</span></div><div class="progress"><div id="progressFill"></div></div><div id="jobMessage" class="muted"></div></div>
        <div id="errorBox" class="error hidden"></div>
      </section>

      <section class="surface" id="preferences">
        <div class="surface-title"><div><span class="section-number">2</span><h2>Your preferences</h2></div><div class="preference-actions"><button id="saveProfileBtn" class="text-button">Save on this device</button><button id="runPreferencesBtn" class="primary" disabled>Run preferences</button></div></div>
        <div class="preference-grid">
          <label>Highest-priority layovers<input id="eliteCities" placeholder="SAN, HNL, BOS"></label>
          <label>Preferred layovers<input id="secondaryCities" placeholder="SEA, PDX, MIA"></label>
          <label>Avoid layovers<input id="penaltyCities" placeholder="DFW, IAH"></label>
          <label>Trip length priority (best to least)<input id="preferredTripLengths" placeholder="6+, 5, 4, 3, 2, 1"><small>Order matters. This is a preference, not an automatic hard filter.</small></label>
          <label>Earliest report<input id="earliestReport" type="time"></label>
          <label>Latest release<input id="latestRelease" type="time"></label>
          <label>Base / co-terminal group<input id="baseAirport" placeholder="ATL or NYC"></label>
          <label id="bidFleetField" class="hidden">American bid fleet<input id="bidFleets" placeholder="320, 737"></label>
          <label id="payPriorityField" class="hidden">Pay ranking priority<select id="payPriority"></select></label>
          <label>Preferred start airports<input id="preferredStartAirports" placeholder="JFK, LGA"></label>
          <label>Avoid start airports<input id="avoidStartAirports" placeholder="EWR"></label>
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
            <label>Required days off<textarea id="requiredDaysOff" placeholder="8/11, 8/18"></textarea></label>
            <label>Preferred days off<textarea id="preferredDaysOff" placeholder="8/12, 8/19"></textarea></label>
            <label>Holidays / special dates<textarea id="holidayDates" placeholder="8/31, 9/7"></textarea></label>
            <label>Preferred weekdays off<input id="preferredWeekdays" placeholder="SAT,SUN"></label>
            <label>Preferred aircraft codes<input id="preferredAircraft" placeholder="NEO,321"></label>
            <label>Maximum transfers<input id="maxTransfers" type="number" min="0" placeholder="0"></label>
          </div>
        </details>
        <input id="smallCities" type="hidden"><input id="maxConsecutiveWorkDays" type="hidden"><input id="minConsecutiveDaysOff" type="hidden"><input id="avoidReserve" type="hidden"><input id="preferOperate" type="hidden">
        <div class="hidden-weight-fields"><input id="wElite" type="hidden" value="150"><input id="wSecondary" type="hidden" value="12"><input id="wSmall" type="hidden" value="6"><input id="wPenalty" type="hidden" value="18"><input id="wAircraft" type="hidden" value="20"><input id="wPure" type="hidden" value="65"><input id="wTransfer" type="hidden" value="32"><input id="wDeadhead" type="hidden" value="18"><input id="wStartPreferred" type="hidden" value="18"><input id="wStartAvoid" type="hidden" value="35"><input id="wRequiredConflict" type="hidden" value="500"><input id="wPreferredConflict" type="hidden" value="35"><input id="wHolidayConflict" type="hidden" value="60"><input id="wEarlyReport" type="hidden" value="20"><input id="wLateRelease" type="hidden" value="20"></div>
      </section>

      <section id="synopsisPanel" class="surface synopsis-panel hidden">
        <div class="surface-title"><div><span class="section-number">3</span><div><h2>Bid package synopsis</h2><p>A quick picture of what the airline is offering before preferences change the ranking.</p></div></div></div>
        <div id="synopsisMetrics" class="synopsis-metrics"></div>
        <div class="synopsis-breakdowns">
          <article><h3>Trip lengths</h3><div id="synopsisLengths" class="breakdown-list"></div></article>
          <article><h3>Start airports</h3><div id="synopsisStarts" class="breakdown-list"></div></article>
          <article><h3>Fleet categories</h3><div id="synopsisFleets" class="breakdown-list"></div></article>
          <article><h3>Top overnight cities</h3><div id="synopsisLayovers" class="breakdown-list"></div></article>
        </div>
      </section>

      <section class="results-section" id="resultsPanel">
        <div class="results-header">
          <div><span class="kicker">YOUR RESULTS</span><h2 id="resultsTitle">Recommended rotations</h2><p id="summary">Load sample results or analyze a bid package.</p></div>
          <div class="results-actions"><select id="resultLimit"><option value="25">Top 25</option><option value="50">Top 50</option><option value="100">Top 100</option><option value="all">All</option></select><a id="csvLink" class="secondary button disabled" href="#">PDF report</a>__CONTINUE_LABS__</div>
        </div>
        <div class="snapshot" id="snapshot">
          <div><span>Top match</span><strong id="snapshotMatch">—</strong></div>
          <div><span id="snapshotPayLabel">Credit</span><strong id="snapshotCredit">—</strong></div>
          <div><span>Trip length</span><strong id="snapshotLength">—</strong></div>
          <div><span>Fatigue</span><strong id="snapshotFatigue">—</strong></div>
        </div>
        <div id="results" class="ranked-list"><div class="empty-state"><span>✈</span><strong>No results yet</strong><p>Your ranked rotations will appear here.</p></div></div>
        <section id="nearMatchesPanel" class="near-matches hidden">
          <div class="near-matches-heading"><div><span class="kicker">CLOSEST AVAILABLE</span><h3>Near Matches</h3><p>These trips miss at least one requirement. CrewBidIQ shows exactly what must be relaxed.</p></div></div>
          <div id="nearResults" class="ranked-list"></div>
        </section>
      </section>

      <section id="guide" class="surface guide-panel hidden">
        <div class="surface-title"><div><h2>Complete User Guide</h2></div><button id="closeGuideBtn" class="text-button">Close</button></div>
        <p class="guide-intro">CrewBidIQ helps answer “What trips fit my life?” Upload the airline bid package once, set the preferences that matter to you, and use <strong>Run preferences</strong> whenever you want to rerank the same parsed package. You do not need to upload it again.</p>

        <div class="guide-grid">
          <article class="guide-card">
            <h3>1. Upload and analyze</h3>
            <ul class="guide-list">
              <li><strong>Airline:</strong> choose the airline before selecting the file so the correct parser and terminology are used.</li>
              <li><strong>PDF bid packages:</strong> use the PDF picker for Delta, American, and other supported PDF formats.</li>
              <li><strong>Southwest packages:</strong> upload one ZIP, or the Pairings and Lines text files with optional cover and seniority files.</li>
              <li><strong>Analyze bid package:</strong> extracts the trips, identifies operating legs and true overnights, and creates the first ranked list.</li>
              <li><strong>Interrupted uploads:</strong> CrewBidIQ keeps the active job and offers Resume analysis when the connection returns.</li>
            </ul>
          </article>

          <article class="guide-card">
            <h3>2. Airline terminology</h3>
            <p>The labels follow the airline automatically. Delta uses <strong>Rotation</strong>, American uses <strong>Sequence</strong>, Southwest uses <strong>Line</strong>, and other airlines may use <strong>Pairing</strong> or another configured term.</p>
            <p>American equipment codes are decoded only when the meaning is confirmed. Unconfirmed codes remain visible exactly as printed instead of being guessed.</p>
          </article>
        </div>

        <h3>Preference guide</h3>
        <p>Comma-separated fields accept entries such as <strong>SAN, HNL, BOS</strong>. Dates can use simple month/day entries such as <strong>8/11, 8/18</strong>; CrewBidIQ uses the bid package year. Full YYYY-MM-DD dates remain supported. Empty report and release times add no restriction. If left blank, CrewBidIQ uses one allowed deadhead and zero allowed airport transfers.</p>
        <div class="guide-grid">
          <article class="guide-card">
            <h4>Layovers and trip shape</h4>
            <ul class="guide-list">
              <li><strong>Highest-priority layovers:</strong> gives a dominant positive preference to sequences that overnight in those cities. A required-day conflict can still place one lower.</li>
              <li><strong>Preferred layovers:</strong> gives a smaller positive preference to desirable overnight cities.</li>
              <li><strong>Avoid layovers:</strong> lowers sequences that overnight in those cities. Airports merely touched while operating are not treated as layovers.</li>
              <li><strong>Trip length priority:</strong> ranks trip lengths from best to least, such as 6+, 5, 4, 3, 2, 1. It remains a soft preference unless you explicitly make a length required in Labs.</li>
              <li><strong>Minimum layover hours:</strong> lowers a result for each overnight shorter than the entered minimum.</li>
            </ul>
          </article>

          <article class="guide-card">
            <h4>Report, release, and calendar</h4>
            <ul class="guide-list">
              <li><strong>Earliest report:</strong> lowers trips that report before your preferred starting time.</li>
              <li><strong>Latest release:</strong> lowers trips that release after your preferred ending time.</li>
              <li><strong>Required days off:</strong> enter month/day without a year, such as 8/11. This is a hard conflict; any overlap receives the largest penalty and a Low rating.</li>
              <li><strong>Preferred days off:</strong> month/day entries work here too. This is a soft conflict; overlaps reduce the rank but do not automatically make the result Low.</li>
              <li><strong>Holidays / special dates:</strong> dates to check when Avoid holidays is enabled.</li>
              <li><strong>Avoid holidays:</strong> lowers trips touching a listed holiday. <strong>Work holidays</strong> removes that avoidance preference.</li>
              <li><strong>Weekends off and preferred weekdays off:</strong> are saved as planning context; separate weekday weighting is not yet applied to ranking.</li>
            </ul>
          </article>

          <article class="guide-card">
            <h4>Duty workload</h4>
            <ul class="guide-list">
              <li><strong>Maximum legs any duty day:</strong> lowers trips when a working duty exceeds the limit.</li>
              <li><strong>Maximum first-day legs:</strong> focuses that limit on day one.</li>
              <li><strong>Maximum last-day legs:</strong> helps protect the trip home from a heavy final day.</li>
              <li><strong>Maximum deadheads:</strong> lowers trips with more deadhead segments than allowed. A flight-number D suffix in American packages is shown as a provisional deadhead until airline documentation confirms it.</li>
              <li><strong>Maximum transfers:</strong> lowers known same-city airport transfers, such as JFK–LGA or DCA–IAD, above your limit.</li>
            </ul>
          </article>

          <article class="guide-card">
            <h4>Redeyes and aircraft</h4>
            <ul class="guide-list">
              <li><strong>Allow mid-rotation redeyes:</strong> reduces the penalty when a parsed flight leg departs during the Window of Circadian Low (WOCL), 02:00 through 05:59 local departure time.</li>
              <li><strong>Allow redeye starts, Avoid final redeyes, and Maximum legs after redeye rest:</strong> are saved for planning; phase-specific WOCL weighting will become active as airline duty classification expands.</li>
              <li><strong>Preferred aircraft codes:</strong> favors matching codes found in the package. Use the airline’s printed code, such as 321E, rather than relying only on a marketing aircraft name.</li>
              <li><strong>Bid fleet categories:</strong> is a hard filter when the package contains fleet metadata. American examples include 320, 737, 777, and 787.</li>
              <li><strong>Preferred and avoid start airports:</strong> adjust rank from the first departure airport. Avoid takes priority if an airport appears in both lists. Delta NYC expands to JFK, LGA, and EWR.</li>
            </ul>
          </article>
        </div>

        <h3>How to read your results</h3>
        <div class="guide-grid">
          <article class="guide-card">
            <h4>Rating and rank</h4>
            <p><strong>Exact Match, Strong Match, and Partial Match</strong> describe eligible trips. <strong>Near Match</strong> is shown separately when a trip misses a hard requirement.</p>
            <p><strong>Why it matched</strong> separates matched preferences, compromises, and neutral trip facts. Near Matches list the exact requirement that would need to be relaxed.</p>
          </article>

          <article class="guide-card">
            <h4>Snapshot and core metrics</h4>
            <ul class="guide-list">
              <li><strong>Top match:</strong> rating of the first recommendation.</li>
              <li><strong>Southwest TFP:</strong> Trips for Pay is shown as Pairing TFP or Line TFP. Carry-out TFP and TFP efficiency remain separate.</li>
              <li><strong>Delta Total Pay:</strong> Trip Credit plus confidently parsed EDP, HOL, and SIT. Missing or unsupported components are never assumed to be zero.</li>
              <li><strong>American Total Pay:</strong> the bottom total printed in the package's TPAY column. The original TPAY value remains preserved with the sequence.</li>
              <li><strong>Other-airline credit:</strong> airline-provided trip or sequence credit when available. Total Pay appears only after that airline's pay rules are defined.</li>
              <li><strong>TAFB:</strong> total time away from base.</li>
              <li><strong>Trip length:</strong> elapsed trip days represented by the airline package. Flying duty periods are shown separately.</li>
              <li><strong>Fatigue risk:</strong> flags flight legs departing during WOCL (02:00 through 05:59 local).</li>
              <li><strong>Legs by duty day:</strong> working flight segments in each RPT-to-RLS duty period; deadheads are counted separately.</li>
            </ul>
          </article>

          <article class="guide-card">
            <h4>Layovers, timeline, and source</h4>
            <ul class="guide-list">
              <li><strong>Overnights:</strong> only contractual rest or hotel cities, not every city the trip touches.</li>
              <li><strong>All operating cities:</strong> expandable list of every departure and arrival airport.</li>
              <li><strong>Timeline and duty legs:</strong> flight order, local departure and arrival times, flight number, operating/deadhead status, and equipment.</li>
              <li><strong>View original sequence/rotation:</strong> the preserved airline-formatted source block for comparison with the bid package.</li>
            </ul>
          </article>

          <article class="guide-card">
            <h4>Conflicts, airline pay, and report</h4>
            <ul class="guide-list">
              <li><strong>Conflicts:</strong> lists required-day, preferred-day, and holiday overlaps. Time and workload mismatches appear under Why it matched.</li>
              <li><strong>Pay ranking priority:</strong> optionally ranks otherwise eligible trips by an airline-supported pay or efficiency measure. Required days off still take priority.</li>
              <li><strong>Delta Additional Pay:</strong> itemizes only EDP, HOL, and SIT values that were actually found in the source pairing.</li>
              <li><strong>PDF report:</strong> creates a printable package containing your preferences, top recommendations, detail pages, original airline formatting, and definitions.</li>
            </ul>
          </article>
        </div>

        <div class="guide-note"><strong>Important:</strong> CrewBidIQ is a planning aid. Always verify dates, legality, pay, hotels, transportation, deadheads, and equipment against the original airline bid package before submitting a bid. Preferences are saved only in this browser.</div>
      </section>
    </main>

    <div id="diagnosticModal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="diagnosticTitle">
      <div class="diagnostic-modal">
        <div class="modal-title"><div><span class="kicker">PARSER FEEDBACK</span><h2 id="diagnosticTitle">Report a result problem</h2></div><button id="closeDiagnosticBtn" class="text-button">Close</button></div>
        <p>This creates a diagnostic JSON file for you to attach in Codex or send to support. It includes this result, nearby source blocks, and duplicate parser candidates—not the complete bid package. Preferences are included only for a wrong-ranking report.</p>
        <label>What is wrong?<select id="diagnosticCategory"><option value="missing_data">Missing or incomplete data</option><option value="wrong_layover">Wrong layover</option><option value="wrong_ranking">Wrong ranking</option><option value="wrong_times">Wrong times or duty</option><option value="other">Other</option></select></label>
        <label>What should CrewBidIQ have shown?<textarea id="diagnosticNotes" maxlength="2000" placeholder="Example: Rotation 4461 should have 3 duty days and an overnight in MCO."></textarea></label>
        <div class="modal-actions"><button id="cancelDiagnosticBtn" class="secondary">Cancel</button><button id="downloadDiagnosticBtn" class="primary">Create diagnostic file</button></div>
      </div>
    </div>

    __MOBILE_NAV__
  </div>
</div>
<script src="/static/app.js?v=0423"></script>
<script>document.getElementById('mobileGuideBtn').addEventListener('click',()=>document.getElementById('guideBtn').click());</script>
</body></html>
"""


def classic_html(page: str) -> str:
    enabled = labs_enabled()
    home_active = "active" if page == "home" else ""
    results_active = "active" if page == "results" else ""
    labs_switch = (
        '<nav class="experience-switch" aria-label="CrewBidIQ experience">'
        '<a href="/" class="active">Classic</a><a href="/labs">Labs <small>Beta</small></a></nav>'
        if enabled
        else ""
    )
    continue_labs = (
        '<a id="continueLabs" class="labs-button button hidden" href="/labs">Continue in Labs</a>'
        if enabled
        else ""
    )
    if enabled:
        mobile_nav = (
            '<nav class="bottom-nav three" aria-label="Primary navigation">'
            f'<a href="/" class="{home_active}"><span>A</span>Analyze</a>'
            f'<a href="/results" class="{results_active}"><span>R</span>Results</a>'
            '<a href="/labs"><span>L</span>Labs</a></nav>'
        )
    else:
        mobile_nav = (
            '<nav class="bottom-nav">'
            f'<a href="/" class="{home_active}"><span>⌂</span>Home</a>'
            '<a href="/#upload"><span>⇧</span>Upload</a>'
            f'<a href="/results" class="{results_active}"><span>▥</span>Results</a>'
            '<a href="/#preferences"><span>⚙</span>Preferences</a></nav>'
        )
    replacements = {
        "__CLASSIC_PAGE__": page,
        "__HOME_ACTIVE__": home_active,
        "__RESULTS_ACTIVE__": results_active,
        "__LABS_SWITCH__": labs_switch,
        "__CONTINUE_LABS__": continue_labs,
        "__MOBILE_NAV__": mobile_nav,
    }
    html = INDEX_HTML
    for marker, value in replacements.items():
        html = html.replace(marker, value)
    return html


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(classic_html("home"))


@app.get("/results", response_class=HTMLResponse)
def classic_results() -> HTMLResponse:
    return HTMLResponse(classic_html("results"))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "CrewBidIQ"}


@app.get("/api/airlines/terminology")
def airline_terminology() -> dict[str, dict[str, str]]:
    return airline_terminology_payload()


@app.get("/api/airlines/coterminals")
def airline_coterminals() -> dict[str, dict[str, list[str]]]:
    return coterminal_payload()


@app.get("/api/destinations")
def destinations() -> dict[str, Any]:
    return {"groups": taxonomy_payload()}


@app.post("/api/trip-intent")
def parse_trip_intent(payload: dict[str, Any]) -> dict[str, Any]:
    intent = interpret_trip_intent(str(payload.get("text") or ""))
    return {"intent": intent, "profile": trip_intent_profile(intent)}


@app.post("/api/seniority-context")
def seniority_context(payload: dict[str, Any]) -> dict[str, Any]:
    context = build_seniority_context(payload)
    if context is None:
        raise HTTPException(400, "Enter a valid category position and category population.")
    return context


def extract_text(
    path: Path,
    suffix: str,
    job_id: str,
    *,
    sort_pdf_text: bool = True,
    deadline: float | None = None,
) -> str:
    if suffix == ".pdf":
        doc = fitz.open(path)
        parts: list[str] = []
        has_readable_text = False
        last_progress_update = time.monotonic()
        try:
            for i, page in enumerate(doc):
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Parsing exceeded the configured time limit")
                page_text = page.get_text("text", sort=sort_pdf_text)
                has_readable_text = has_readable_text or bool(page_text.strip())
                parts.append(f"<<<CREWBIDIQ_PAGE:{i + 1}>>>\n" + page_text)
                now = time.monotonic()
                if now - last_progress_update >= 0.75 or i + 1 == len(doc):
                    update_job(
                        job_id,
                        progress=15 + int((i + 1) / max(len(doc), 1) * 45),
                        message=f"Extracting PDF page {i + 1} of {len(doc)}",
                    )
                    last_progress_update = now
        finally:
            doc.close()
        if not has_readable_text:
            raise RuntimeError("The PDF contains no readable text. Upload a text-based airline bid package rather than a scanned image.")
        return "\n".join(parts)

    raw = path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".html", ".htm"}:
        raw = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
        raw = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
        raw = re.sub(r"<[^>]+>", " ", raw)
    return raw


def sort_pdf_text_for_airline(airline: str) -> bool:
    """Keep native column order for airline packages that use side-by-side records."""
    return str(airline or "").lower() not in {"american", "delta", "auto"}


def pairing_record_quality(pairing: dict[str, Any]) -> tuple[int, int, int, int, int]:
    legs = pairing.get("legs") or []
    return (
        int(bool(legs)),
        len(legs),
        int(bool(pairing.get("credit"))),
        int(bool(pairing.get("tafb"))),
        len(str(pairing.get("block") or "")),
    )


def consolidate_pairings(pairings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one package-scoped trip per repeated ID and retain operating dates."""
    order: list[str] = []
    candidates: dict[str, list[dict[str, Any]]] = {}
    for pairing in pairings:
        pairing_id = str(pairing.get("id") or "").strip().upper()
        if not pairing_id:
            continue
        package_id = str(pairing.get("package_id") or "legacy-package").strip()
        inventory_key = str(pairing.get("inventory_key") or f"{package_id}:{pairing_id}")
        pairing.setdefault("package_id", package_id)
        pairing.setdefault("rotation_number", pairing_id)
        pairing.setdefault("inventory_key", inventory_key)
        pairing.setdefault("source_page", pairing.get("source_pdf_page"))
        pairing.setdefault("source_section", pairing.get("fleet_section") or "bidable_inventory")
        pairing.setdefault("page_classification", "BIDABLE_INVENTORY")
        pairing.setdefault("package_base", pairing.get("base"))
        pairing.setdefault("package_fleet", pairing.get("fleet"))
        pairing.setdefault("parser_confidence", pairing.get("confidence", 0.0))
        non_bidable_page = str(pairing.get("page_classification") or "").upper() in {
            "COVER", "CONTENTS", "INSTRUCTIONS", "REFERENCE", "EXAMPLE", "HOTEL_LIST", "APPENDIX",
        }
        pairing.setdefault("bidable_inventory_confirmed", not non_bidable_page)
        if inventory_key not in candidates:
            order.append(inventory_key)
            candidates[inventory_key] = []
        candidates[inventory_key].append(pairing)

    consolidated: list[dict[str, Any]] = []
    for inventory_key in order:
        records = candidates[inventory_key]
        selected = dict(max(records, key=pairing_record_quality))
        operating_dates: list[str] = []
        for record in records:
            value = record.get("effective")
            values = value if isinstance(value, list) else re.split(r"\s*,\s*", str(value or ""))
            for operating_date in values:
                token = str(operating_date).strip()
                if token and token not in operating_dates:
                    operating_dates.append(token)
        if operating_dates:
            selected["effective"] = ", ".join(operating_dates)
            selected["operating_dates"] = operating_dates
        if len(records) > 1:
            selected["parser_candidates"] = [
                {
                    "legs": len(record.get("legs") or []),
                    "credit": record.get("credit"),
                    "tafb": record.get("tafb"),
                    "confidence": record.get("confidence"),
                    "block": record.get("block") or "",
                }
                for record in records
            ]
        consolidated.append(attach_canonical_trip(selected, str(selected.get("package_id") or "legacy-package")))
    return consolidated


def parse_pairings(text: str, job_id: str, parser_choice: str = "auto") -> tuple[list[dict[str, Any]], str]:
    update_job(job_id, progress=65, message="Detecting airline format")
    try:
        module, parser_name = select_parser(text, parser_choice)
    except ValueError as exc:
        raise RuntimeError("Airline detection failed. Select the airline manually and try again.") from exc
    update_job(job_id, progress=67, message=f"Identifying trip records with the {parser_name} parser")
    pairings = consolidate_pairings(module.parse(text))
    if not pairings:
        raise RuntimeError(
            f"No pairing identifiers detected with the {parser_name} parser. "
            "Try another parser selection or provide a sample package for a custom adapter."
        )
    update_job(job_id, progress=70, message=f"Parsing details for {len(pairings)} identified trip records")
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


MONTH_NUMBERS = {name: index for index, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
)}


def date_parts(value: str) -> tuple[int | None, int, int] | None:
    """Read full or year-free pilot dates without guessing a package year."""
    token = str(value or "").strip().upper()
    match = re.fullmatch(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", token)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    match = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})(?:[-/](20\d{2}))?", token)
    if match:
        return (int(match.group(3)) if match.group(3) else None, int(match.group(1)), int(match.group(2)))
    match = re.fullmatch(r"(\d{1,2})([A-Z]{3})(20\d{2})?", token)
    if match and match.group(2) in MONTH_NUMBERS:
        return (int(match.group(3)) if match.group(3) else None, MONTH_NUMBERS[match.group(2)], int(match.group(1)))
    return None


def matching_dates(dates: list[str], preferences: set[str]) -> list[str]:
    """Match MM/DD preferences to the year carried by each parsed trip date."""
    parsed_preferences = [parts for value in preferences if (parts := date_parts(value))]
    matches: list[str] = []
    for value in dates:
        parsed_date = date_parts(value)
        if not parsed_date:
            continue
        date_year, month, day = parsed_date
        if any(
            month == pref_month and day == pref_day and (
                pref_year is None or date_year is None or pref_year == date_year
            )
            for pref_year, pref_month, pref_day in parsed_preferences
        ):
            matches.append(value)
    return matches


def detect_time_values(block: str) -> list[int]:
    values = []
    for hhmm in re.findall(r"\b(?:[01]\d|2[0-3])[0-5]\d\b", block):
        values.append(int(hhmm[:2]) * 60 + int(hhmm[2:]))
    return values


def airline_for_pairing(pairing: dict[str, Any]) -> str:
    parser_id = str(pairing.get("parser") or "")
    return pairing.get("airline") or (
        "delta" if parser_id.startswith("delta") else (
            "american" if parser_id.startswith("american") else (
                "southwest" if parser_id.startswith("southwest") else "generic"
            )
        )
    )


def detect_start_airport(pairing: dict[str, Any]) -> str | None:
    # A leading deadhead positions the crew to the airport where the operating
    # rotation starts. Prefer that first operating origin for pilot-facing use.
    for leg in pairing.get("legs", []) or []:
        if leg.get("deadhead"):
            continue
        departure = str(leg.get("departure") or "").strip().upper()
        if departure:
            return departure
    for leg in pairing.get("legs", []) or []:
        departure = str(leg.get("departure") or "").strip().upper()
        if departure:
            return departure
    return None


WOCL_START_MINUTES = 2 * 60
WOCL_END_MINUTES = 6 * 60


def clock_minutes(value: Any) -> int | None:
    """Parse a structured local clock value without mistaking durations for times."""
    token = str(value or "").strip().replace(":", "")
    if not re.fullmatch(r"\d{3,4}", token):
        return None
    token = token.zfill(4)
    hours, minutes = int(token[:2]), int(token[2:])
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def is_wocl_departure(value: Any) -> bool:
    minutes = clock_minutes(value)
    return minutes is not None and WOCL_START_MINUTES <= minutes < WOCL_END_MINUTES


def wocl_departures(pairing: dict[str, Any]) -> list[dict[str, Any]]:
    """Return flight legs departing from 02:00 through 05:59 local time."""
    matches: list[dict[str, Any]] = []
    for index, leg in enumerate(pairing.get("legs", []) or [], 1):
        if not is_wocl_departure(leg.get("departure_time")):
            continue
        matches.append({
            "leg": index,
            "day": leg.get("day"),
            "flight": leg.get("flight"),
            "departure": leg.get("departure"),
            "departure_time": leg.get("departure_time"),
            "arrival": leg.get("arrival"),
            "arrival_time": leg.get("arrival_time"),
            "deadhead": bool(leg.get("deadhead")),
        })
    return matches


def classify_redeye(pairing: dict[str, Any]) -> str:
    return "WOCL departure" if wocl_departures(pairing) else "none"


def pairing_duty_count(pairing: dict[str, Any]) -> int:
    canonical = pairing.get("canonical_trip") or {}
    if _canonical_count := canonical.get("duty_period_count"):
        return int(_canonical_count)
    duty_labels: list[str] = []
    for leg in pairing.get("legs", []) or []:
        if leg.get("deadhead"):
            continue
        day = str(leg.get("day") or "1")
        if day not in duty_labels:
            duty_labels.append(day)
    return len(duty_labels)


def pairing_trip_length(pairing: dict[str, Any]) -> int:
    """Return elapsed trip days, which may exceed the number of flying duties."""
    canonical = pairing.get("canonical_trip") or {}
    normalized_length = canonical.get("trip_length_days") or pairing.get("trip_length_days") or pairing.get("sequence_days") or pairing.get("trip_days")
    if str(normalized_length or "").isdigit() and int(normalized_length) > 0:
        return int(normalized_length)
    duty_labels: list[str] = []
    for leg in pairing.get("legs", []) or []:
        label = str(leg.get("day") or "1").strip().upper()
        if label not in duty_labels:
            duty_labels.append(label)
    if not duty_labels:
        return 0
    if airline_for_pairing(pairing) == "delta" and all(re.fullmatch(r"[A-Z]", label) for label in duty_labels):
        offsets = [ord(label) - ord("A") + 1 for label in duty_labels]
        return max(offsets) - min(offsets) + 1
    return len(duty_labels)


def filter_pairings_for_profile(pairings: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    # The normalization boundary must explicitly confirm inventory provenance.
    # Missing and false values fail closed so instructional/example candidates
    # can never enter the recommendation pipeline.
    pairings = [pairing for pairing in pairings if pairing.get("bidable_inventory_confirmed") is True]
    fleets = set(list_field(profile.get("bid_fleets")))
    if not fleets:
        return pairings
    return [pairing for pairing in pairings if str(pairing.get("fleet") or "").upper() in fleets]


def _breakdown(counter: Counter[str], total: int, label: str) -> list[dict[str, Any]]:
    return [
        {label: value, "count": count, "percent": round(count / total * 100, 1) if total else 0.0}
        for value, count in counter.most_common()
    ]


def build_bid_synopsis(pairings: list[dict[str, Any]]) -> dict[str, Any]:
    pairings = filter_pairings_for_profile(consolidate_pairings(pairings), {})
    total = len(pairings)
    complete = sum(bool(pairing.get("legs")) for pairing in pairings)
    redeyes = sum(classify_redeye(pairing) != "none" for pairing in pairings)
    deadheads = sum(any(leg.get("deadhead") for leg in pairing.get("legs", []) or []) for pairing in pairings)
    starts = Counter(filter(None, (detect_start_airport(pairing) for pairing in pairings)))
    lengths = Counter(str(length) for pairing in pairings if (length := pairing_trip_length(pairing)))
    layovers = Counter(city for pairing in pairings for city in detect_layover_cities(pairing))
    fleets = Counter(str(pairing.get("fleet")) for pairing in pairings if pairing.get("fleet"))
    return {
        "total": total,
        "count_basis": "unique_trip_id",
        "complete": complete,
        "incomplete": total - complete,
        "redeye": {"count": redeyes, "percent": round(redeyes / total * 100, 1) if total else 0.0},
        "deadhead": {"count": deadheads, "percent": round(deadheads / total * 100, 1) if total else 0.0},
        "overnight_city_count": len(layovers),
        "trip_lengths": _breakdown(lengths, total, "days"),
        "start_airports": _breakdown(starts, total, "airport"),
        "layover_cities": _breakdown(layovers, total, "city")[:10],
        "fleets": _breakdown(fleets, total, "fleet"),
    }


def sort_results(results: list[dict[str, Any]], active_package_id: str | None = None) -> None:
    """Apply the explicit two-stage recommendation output order in place."""
    results[:] = recommendation_pipeline(results, active_package_id)


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


def _southwest_local_display(pairing: dict[str, Any]) -> str:
    rows = ["Local schedule"]
    for leg in pairing.get("legs", []) or []:
        departure_time = leg.get("departure_time")
        arrival_time = leg.get("arrival_time")
        if not departure_time or not arrival_time:
            continue
        departure_zone = leg.get("departure_local_event_timezone") or "local"
        arrival_zone = leg.get("arrival_local_event_timezone") or "local"
        rows.append(
            f"{leg.get('event_date') or ''} {leg.get('departure')} {departure_time} ({departure_zone}) "
            f"→ {leg.get('arrival')} {arrival_time} ({arrival_zone})"
        )
    return "\n".join(rows)


def score_pairing(pairing: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    pairing = canonical_presentation_record(pairing)
    canonical_trip = public_canonical_trip(pairing["canonical_trip"])
    block = pairing["block"]
    upper = block.upper()
    airline = airline_for_pairing(pairing)
    touched_cities = detect_airports(block, pairing)
    cities = detect_layover_cities(pairing)
    if airline == "southwest":
        dates = list(dict.fromkeys(str(leg.get("event_date")) for leg in pairing.get("legs", []) if leg.get("event_date")))
    elif "operating_dates" in pairing:
        dates = [str(value) for value in pairing.get("operating_dates") or []]
    elif pairing.get("effective"):
        dates = list_field(pairing.get("effective"))
    else:
        dates = [] if airline == "delta" else detect_dates(block)
    start_airport = detect_start_airport(pairing)

    elite = set(list_field(profile.get("elite_cities")))
    secondary = set(list_field(profile.get("secondary_cities")))
    small = set(list_field(profile.get("small_cities")))
    penalty = set(list_field(profile.get("penalty_cities")))
    aircraft = list_field(profile.get("preferred_aircraft"))
    base = str(profile.get("base_airport", "")).upper().strip()
    base_airports = set(expand_airports(airline, [base]))
    preferred_starts = set(expand_airports(airline, list_field(profile.get("preferred_start_airports"))))
    avoided_starts = set(expand_airports(airline, list_field(profile.get("avoid_start_airports"))))

    required_days = set(list_field(profile.get("required_days_off")))
    preferred_days = set(list_field(profile.get("preferred_days_off")))
    holiday_dates = set(list_field(profile.get("holiday_dates")))

    w = profile.get("weights", {})
    score = 0.0
    reasons, calendar_conflicts = [], []
    data_issues: list[str] = []
    if not pairing.get("legs"):
        score -= float(w.get("incomplete") or 1000)
        data_issues.append("No duty legs were parsed")
        reasons.append("Incomplete source data: no duty legs were parsed")

    if start_airport in avoided_starts:
        score -= float(w.get("start_avoid") or 35)
        reasons.append(f"Starts at {start_airport}, which you prefer to avoid")
    elif start_airport in preferred_starts:
        score += float(w.get("start_preferred") or 18)
        reasons.append(f"Starts at your preferred airport {start_airport}")

    for city in cities:
        if city in elite:
            score += float(w.get("elite") or 150); reasons.append(f"{city} is a highest-priority overnight")
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

    redeye_legs = wocl_departures(pairing)
    redeye = "WOCL departure" if redeye_legs else "none"

    if redeye != "none":
        if profile.get("allow_productive_redeye", True):
            score -= 18
        else:
            score -= 55
        departures = ", ".join(
            f"{leg.get('departure') or 'Unknown'} {leg.get('departure_time')}"
            for leg in redeye_legs[:3]
        )
        reasons.append(f"Departs during WOCL (02:00–05:59 local): {departures}")

    req_hits = sorted(matching_dates(dates, required_days))
    pref_hits = sorted(matching_dates(dates, preferred_days))
    holiday_hits = sorted(matching_dates(dates, holiday_dates))

    if req_hits:
        score -= len(req_hits) * float(w.get("required_conflict") or 500)
        calendar_conflicts.append("Required off: " + ", ".join(req_hits))
    if pref_hits:
        score -= len(pref_hits) * float(w.get("preferred_conflict") or 35)
        calendar_conflicts.append("Preferred off: " + ", ".join(pref_hits))
    if holiday_hits and profile.get("avoid_holidays", False):
        score -= len(holiday_hits) * float(w.get("holiday_conflict") or 60)
        calendar_conflicts.append("Holiday: " + ", ".join(holiday_hits))

    times = (
        [
            value
            for leg in pairing.get("legs", [])
            for value in (clock_minutes(leg.get("departure_time")), clock_minutes(leg.get("arrival_time")))
            if value is not None
        ]
        if airline == "southwest"
        else detect_time_values(block)
    )
    if times:
        earliest_report = profile.get("earliest_report_minutes")
        latest_release = profile.get("latest_release_minutes")
        if earliest_report is not None and min(times) < int(earliest_report):
            score -= float(w.get("early_report") or 20)
            reasons.append("Reports earlier than your preferred start time")
        if latest_release is not None and max(times) > int(latest_release):
            score -= float(w.get("late_release") or 20)
            reasons.append("Releases later than your preferred end time")

    elite_non_base = [c for c in cities if c in elite and c not in base_airports]
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

    trip_length = pairing_trip_length(pairing)
    ranked_lengths = length_priority(profile)
    length_points, length_rank, length_reason = length_score_contribution(trip_length, profile)
    score += length_points
    if length_rank is not None:
        if profile.get("trip_length_priority"):
            reasons.append(length_reason)
        else:
            reasons.append(f"Matches your preferred {trip_length}-day trip length")
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
    terminology = get_airline_terminology(airline)
    result_legs = [
        {**(public_local_leg(leg) if airline == "southwest" else leg), "wocl_departure": is_wocl_departure(leg.get("departure_time"))}
        for leg in pairing.get("legs", []) or []
    ]
    result = {
        "id": canonical_trip["id"],
        "pairing": canonical_trip["source_trip_number"],
        "source_trip_number": canonical_trip["source_trip_number"],
        "canonical_trip_id": canonical_trip["id"],
        "canonical_trip": canonical_trip,
        "terminology": canonical_trip["terminology"],
        "ordered_events": canonical_trip["ordered_events"],
        "ordered_legs": canonical_trip["ordered_legs"],
        "duty_days": canonical_trip["duty_days"],
        "hotels": canonical_trip["hotels"],
        "pay_breakdown": canonical_trip["pay_breakdown"],
        "tfp": canonical_trip["tfp"],
        "score": round(score, 1),
        "dates": dates,
        "cities": cities,
        "touched_cities": touched_cities,
        "start_airport": start_airport,
        "coterminal_group": coterminal_group_for_airport(airline, start_airport),
        "preferred_aircraft": aircraft_hits,
        "equipment_codes": list(dict.fromkeys(pairing.get("equipment_codes") or parsed_equipment)),
        "equipment_mapping_status": pairing.get("equipment_mapping_status"),
        "redeye": redeye,
        "redeye_legs": redeye_legs,
        "deadheads": deadheads,
        "transfers": transfers,
        "calendar_conflicts": calendar_conflicts,
        "reasons": reasons,
        "parser": pairing.get("parser", "generic"),
        "parser_confidence": pairing.get("parser_confidence", pairing.get("confidence", 0)),
        "package_id": canonical_trip["package_id"],
        "inventory_key": canonical_trip["id"],
        "source_page": canonical_trip["source_page"],
        "source_section": canonical_trip["source_section"],
        "page_classification": pairing.get("page_classification"),
        "package_base": pairing.get("package_base"),
        "package_fleet": pairing.get("package_fleet"),
        "rotation_number": pairing.get("rotation_number") or pairing.get("id"),
        "bidable_inventory_confirmed": canonical_trip["bidable_inventory_confirmed"],
        "data_quality": "incomplete" if data_issues else "complete",
        "data_issues": data_issues,
        "credit": pairing.get("credit"),
        "tafb": canonical_trip["tafb"],
        "checkin": pairing.get("checkin"),
        "release": pairing.get("release"),
        "layovers": pairing.get("layovers", []),
        "legs": result_legs,
        "duty_legs": duty_counts,
        "trip_length": canonical_trip["trip_length_days"],
        "trip_length_days": canonical_trip["trip_length_days"],
        "trip_length_preference_active": bool(ranked_lengths),
        "trip_length_match": bool(ranked_lengths and length_rank is not None),
        "trip_length_priority": ranked_lengths,
        "length_priority_rank": length_rank,
        "preferred_trip_lengths": ranked_lengths,
        "first_day_legs": duty_counts[0] if duty_counts else 0,
        "last_day_legs": duty_counts[-1] if duty_counts else 0,
        "item_type": "pairing",
        "match_level": level,
        "display_label": terminology.singular,
        "original_display": _southwest_local_display(pairing) if airline == "southwest" else block,
        "operations": pairing.get("operations"),
        "sequence_days": pairing.get("sequence_days"),
        "duty_period_count": pairing.get("duty_period_count", len(duty_counts)),
        "overnight_count": pairing.get("overnight_count", len(pairing.get("layovers", []))),
        "calendar_span_days": pairing.get("calendar_span_days"),
        "first_report": pairing.get("first_report") or pairing.get("checkin"),
        "final_release": pairing.get("final_release") or pairing.get("release"),
        "operating_dates": pairing.get("operating_dates") or dates,
        "operating_dates_status": pairing.get("operating_dates_status") or ("validated" if dates else "unavailable"),
        "positions": pairing.get("positions", []),
        "fleet": pairing.get("fleet"),
        "satellite": pairing.get("satellite"),
        "operation_qualifiers": pairing.get("operation_qualifiers", []),
        "airline": airline,
        "source_terminology": pairing.get("source_terminology"),
        "fleet_section": pairing.get("fleet_section"),
        "source_pdf_page": pairing.get("source_pdf_page"),
        "total_flight_segments": pairing.get("total_flight_segments", len(pairing.get("legs", []))),
        "aircraft_display_names": pairing.get("aircraft_display_names", []),
        "duty_periods": pairing.get("duty_periods", []),
        "transcontinental": is_transcontinental(result_legs),
        "long_haul": any(
            (float(str(leg.get("block") or "0").replace(":", ".")) >= 6.0)
            for leg in result_legs
            if re.fullmatch(r"\d{1,2}[.:]\d{2}", str(leg.get("block") or ""))
        ),
    }
    if airline == "southwest":
        result.update({
            "raw_trip_credit_label": pairing.get("raw_trip_credit_label"),
            "pairing_tfp": pairing.get("pairing_tfp"),
            "tfp_per_duty_period": pairing.get("tfp_per_duty_period"),
            "tfp_per_day_away": pairing.get("tfp_per_day_away"),
        })
    elif airline == "delta":
        result.update({
            "trip_credit": pairing.get("trip_credit") or pairing.get("credit"),
            "pay_components": pairing.get("pay_components"),
            "additional_pay": pairing.get("additional_pay"),
            "total_pay": pairing.get("total_pay"),
            "raw_total_pay": pairing.get("raw_total_pay"),
            "unknown_pay_components": pairing.get("unknown_pay_components"),
            "edp": pairing.get("edp"),
            "hol": pairing.get("hol"),
            "sit": pairing.get("sit"),
            "raw_pay_tokens": pairing.get("raw_pay_tokens", []),
            "unresolved_pay_tokens": pairing.get("unresolved_pay_tokens", []),
            "credit_per_duty_day": pay_minutes_per_duty_day(pairing.get("trip_credit") or pairing.get("credit"), len(duty_counts)),
            "total_pay_per_duty_day": pay_minutes_per_duty_day(pairing.get("total_pay"), len(duty_counts)),
        })
    elif airline == "american" and pairing.get("total_pay") is not None:
        result.update({
            "total_pay": pairing.get("total_pay"),
            "raw_total_pay": pairing.get("raw_total_pay"),
            "source_total_pay_label": pairing.get("source_total_pay_label"),
            "total_pay_per_duty_day": pay_minutes_per_duty_day(pairing.get("total_pay"), len(duty_counts)),
        })

    preference = str(profile.get("pay_priority") or "")
    result["pay_priority"] = preference or None
    result["pay_priority_value"] = pay_priority_value(result, preference)
    if preference and result["pay_priority_value"] is not None:
        labels = {
            "pairing_tfp": "Pairing TFP",
            "monthly_tfp": "Monthly TFP",
            "tfp_per_duty_period": "TFP per duty period",
            "tfp_per_day_away": "TFP efficiency",
            "trip_credit": "Trip Credit",
            "total_pay": "Total Pay",
            "additional_pay": "Additional Pay",
            "credit_per_duty_day": "Credit per duty day",
            "total_pay_per_duty_day": "Total pay per duty day",
        }
        result["pay_explanation"] = f"{labels.get(preference, preference)}: {result.get(preference)}"
    else:
        result["pay_explanation"] = None
    result["fatigue_index"] = build_fatigue_index(result)
    result["seniority_context"] = build_seniority_context(profile.get("seniority_context"))
    result["hold_outlook"] = estimate_hold_outlook(result, result["seniority_context"])
    result.update(evaluate_recommendation(result, profile))
    if os.environ.get("RECOMMENDATION_DEBUG_ENABLED", "false").lower() == "true":
        result["recommendation_debug"] = {
            "parser": result.get("parser"),
            "parser_confidence": result.get("parser_confidence"),
            "normalization": pairing.get("normalization_diagnostics"),
            "eligibility_result": result.get("eligibility_result"),
            "eligibility_violations": result.get("eligibility_violations"),
            "eligibility_stage": result.get("eligibility_stage"),
            "ranking_stage": result.get("ranking_stage"),
            "ranking_score": result.get("ranking_score"),
            "ranking_components": result.get("ranking_components"),
            "trip_length": trip_length,
            "trip_length_priority": ranked_lengths,
            "length_priority_rank": length_rank,
            "length_score_contribution": length_points,
            "sequence": {
                "sequence_id": pairing.get("id"),
                "parsed_sequence_days": pairing.get("sequence_days"),
                "calendar_span_days": pairing.get("calendar_span_days"),
                "duty_period_count": pairing.get("duty_period_count"),
                "overnight_count": pairing.get("overnight_count"),
                "first_report": pairing.get("first_report"),
                "final_release": pairing.get("final_release"),
                "eligibility": result.get("eligibility_result"),
                "rejection_reason": "; ".join(result.get("eligibility_violations") or []) or None,
            } if airline == "american" else None,
            "southwest_time_normalization": [
                {
                    "departure": leg.get("departure_time_provenance"),
                    "arrival": leg.get("arrival_time_provenance"),
                }
                for leg in pairing.get("legs", [])
            ] if airline == "southwest" else None,
        }
    return result


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
    aliases: dict[str, str] = {}
    for pairing_id in pairing_ids:
        normalized_id = pairing_id.upper()
        aliases[normalized_id] = normalized_id
        if normalized_id.startswith("X") and len(normalized_id) > 1:
            aliases[normalized_id[1:]] = normalized_id

    # Southwest LAXFOL rows begin with: Line 1    TFP 90.18 ...
    headers = list(re.finditer(r"(?im)^\s*LINE\s*#?\s*([A-Z0-9-]+)\s+TFP\s+([0-9]+\.[0-9]+)[^\n]*$", normalized))
    for i, match in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(normalized)
        block = normalized[match.start():end]
        refs = []
        for token in re.findall(r"\b[A-Z0-9]{3,6}\b", block.upper()):
            canonical = aliases.get(token)
            if canonical and canonical not in refs:
                refs.append(canonical)
        if refs:
            tafb = re.search(r"\bTAFB\s+([0-9]+:[0-9]{2})", block, re.IGNORECASE)
            duty_periods = re.search(r"\bNo\.\s*DPs\s+(\d+)", block, re.IGNORECASE)
            carry_out = re.search(r"\bC/O\s+TFP\s+([0-9]+\.[0-9]+)", block, re.IGNORECASE)
            monthly_tfp = match.group(2)
            dp_count = int(duty_periods.group(1)) if duty_periods else None
            tafb_value = tafb.group(1) if tafb else None
            lines.append({
                "id": match.group(1).upper(),
                "pairing_ids": refs,
                "block": block,
                "raw_trip_credit_label": "TFP",
                "monthly_tfp": monthly_tfp,
                "line_tfp": monthly_tfp,
                "carry_out_tfp": carry_out.group(1) if carry_out else None,
                "duty_period_count": dp_count,
                "tafb": tafb_value,
                "tfp_per_duty_period": tfp_ratio(monthly_tfp, dp_count),
                "tfp_per_day_away": tfp_per_day_away(monthly_tfp, tafb_value),
            })
    if not lines:
        # Fallback: one record per row containing recognizable pairing IDs.
        for row_no, row in enumerate(normalized.splitlines(), 1):
            refs = [aliases[token] for token in re.findall(r"\b[A-Z0-9]{3,6}\b", row.upper()) if token in aliases]
            refs = list(dict.fromkeys(refs))
            if refs:
                first = re.match(r"\s*([A-Z0-9-]+)", row)
                lines.append({"id": first.group(1) if first else f"LINE-{row_no}", "pairing_ids": refs, "block": row})
    # De-duplicate identical line id / pairing combinations.
    unique = {}
    for line in lines:
        unique[(line["id"], tuple(line["pairing_ids"]))] = line
    return list(unique.values())


def score_southwest_line(line: dict[str, Any], pairing_scores: dict[str, dict[str, Any]], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    members = [pairing_scores[p] for p in line["pairing_ids"] if p in pairing_scores]
    if not members:
        raise RuntimeError(f"No pairing details found for Southwest line {line['id']}")
    package_ids = {str(item.get("package_id") or "") for item in members}
    line_package_id = str(line.get("package_id") or (next(iter(package_ids)) if len(package_ids) == 1 else "") or "legacy-package")
    if package_ids != {""} and (len(package_ids) != 1 or line_package_id not in package_ids):
        raise RuntimeError("Package isolation check rejected a Southwest line with mixed pairing sources.")
    canonical_trips = [item["canonical_trip"] for item in members if item.get("canonical_trip")]
    cities = list(dict.fromkeys(
        str(layover.get("airport") or layover.get("city"))
        for trip in canonical_trips
        for layover in trip.get("layovers", [])
        if layover.get("airport") or layover.get("city")
    ))
    layovers = []
    for trip in canonical_trips:
        for layover in trip.get("layovers", []):
            key = (layover.get("airport"), layover.get("duration"), layover.get("after_duty_day"))
            if key not in [(x.get("airport"), x.get("duration"), x.get("after_duty_day")) for x in layovers]:
                layovers.append(layover)
    reasons = []
    for item in members:
        reasons.extend(item.get("reasons", []))
    profile = profile or {}
    conflicts = sorted(set(c for x in members for c in x.get("calendar_conflicts", [])))
    touched = list(dict.fromkeys(c for item in members for c in item.get("touched_cities", [])))
    duty_legs = [count for item in members for count in item.get("duty_legs", [])]
    redeye_legs = [
        {"pairing": item.get("pairing"), **leg}
        for item in members
        for leg in item.get("redeye_legs", [])
    ]
    result = {
        "id": f"{line_package_id}:LINE:{line['id']}",
        "pairing": line["id"], "item_type": "line", "score": 0.0,
        "package_id": line_package_id,
        "canonical_trip_ids": [trip["id"] for trip in canonical_trips],
        "canonical_trips": canonical_trips,
        "ordered_events": [event for trip in canonical_trips for event in trip.get("ordered_events", [])],
        "ordered_legs": [leg for trip in canonical_trips for leg in trip.get("ordered_legs", [])],
        "duty_days": [day for trip in canonical_trips for day in trip.get("duty_days", [])],
        "hotels": [hotel for trip in canonical_trips for hotel in trip.get("hotels", [])],
        "dates": line.get("work_dates") or list(dict.fromkeys(d for item in members for d in item.get("dates", []))), "cities": cities, "touched_cities": touched, "preferred_aircraft": sorted(set(a for item in members for a in item.get("preferred_aircraft", []))),
        "redeye": "WOCL departure" if redeye_legs else "none", "redeye_legs": redeye_legs,
        "deadheads": sum(x.get("deadheads", 0) for x in members), "transfers": sorted(set(t for x in members for t in x.get("transfers", []))),
        "calendar_conflicts": conflicts,
        "reasons": [f"Contains pairings: {', '.join(line['pairing_ids'])}"] + list(dict.fromkeys(reasons))[:12],
        "parser": "southwest_lines", "parser_confidence": min(x.get("parser_confidence", 0) for x in members),
        "credit": line.get("monthly_tfp"), "tafb": line.get("tafb"), "checkin": None, "release": None,
        "raw_trip_credit_label": line.get("raw_trip_credit_label"),
        "monthly_tfp": line.get("monthly_tfp"), "line_tfp": line.get("line_tfp"),
        "carry_out_tfp": line.get("carry_out_tfp"),
        "tfp_per_duty_period": line.get("tfp_per_duty_period"),
        "tfp_per_day_away": line.get("tfp_per_day_away"),
        "layovers": layovers, "legs": [leg for item in members for leg in item.get("legs", [])], "pairing_ids": line["pairing_ids"],
        "duty_legs": duty_legs, "first_day_legs": duty_legs[0] if duty_legs else 0, "last_day_legs": duty_legs[-1] if duty_legs else 0,
        "match_level": "fair", "display_label": get_airline_terminology("southwest").singular,
        "original_display": "\n\n".join(item.get("original_display", "") for item in members if item.get("original_display")),
        "airline": "southwest", "data_quality": "complete",
    }
    result.update(rank_southwest_line(line, members, profile))
    result["match_level"] = match_level(result["score"], conflicts)
    result["fatigue_index"] = build_fatigue_index(result)
    result["seniority_context"] = build_seniority_context(profile.get("seniority_context"))
    result["hold_outlook"] = estimate_hold_outlook(result, result["seniority_context"])
    conflict_events = profile.get("fixed_events") or []
    if conflict_events:
        result["schedule_conflict_analysis"] = optimize_schedule_conflicts(
            result,
            conflict_events,
            str(profile.get("conflict_mode") or "protect"),
        )
    preference = str(profile.get("pay_priority") or "")
    result["pay_priority"] = preference or None
    result["pay_priority_value"] = pay_priority_value(result, preference)
    labels = {"monthly_tfp": "Monthly TFP", "tfp_per_duty_period": "TFP per duty period", "tfp_per_day_away": "TFP efficiency"}
    result["pay_explanation"] = (
        f"{labels.get(preference, preference)}: {result.get(preference)}"
        if preference and result["pay_priority_value"] is not None else None
    )
    return result


def validate_uploaded_path(path: Path, expected: str) -> None:
    if path.stat().st_size == 0:
        raise HTTPException(400, "The selected file is empty.")
    with path.open("rb") as uploaded:
        signature = uploaded.read(8)
    if expected == ".pdf" and not signature.startswith(b"%PDF-"):
        raise HTTPException(400, "The selected file is not a valid PDF.")
    if expected == ".pdf":
        try:
            document = fitz.open(path)
        except Exception as exc:
            raise HTTPException(400, "The PDF is corrupted or unreadable. Choose a fresh copy of the airline package.") from exc
        try:
            if document.needs_pass:
                raise HTTPException(400, "Password-protected PDFs are not supported. Upload an unlocked copy.")
            if document.page_count < 1:
                raise HTTPException(400, "The PDF does not contain any pages.")
        finally:
            document.close()
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
    package_id = job_id
    work_dir = UPLOAD_DIR / f"{job_id}_work"
    cache_key: str | None = None
    cache_hit = False
    deadline = time.monotonic() + MAX_PARSE_SECONDS
    try:
        update_job(job_id, status="processing", progress=5, message="Detecting airline and package type")
        if airline == "southwest":
            if len(paths) == 1 and paths[0].suffix.lower() == ".zip":
                with zipfile.ZipFile(paths[0]) as archive:
                    members = [m for m in archive.infolist() if not m.is_dir() and ".." not in Path(m.filename).parts]
                    pairing_chunks, line_chunks = [], []
                    for i, member in enumerate(members, 1):
                        if time.monotonic() > deadline:
                            raise TimeoutError("Parsing exceeded the configured time limit")
                        name = Path(member.filename).name.lower()
                        if Path(name).suffix.lower() not in {".txt", ".csv", ".html", ".htm"}:
                            continue
                        update_job(
                            job_id,
                            progress=10 + int(i / max(len(members), 1) * 45),
                            message=f"Reading Southwest file {i} of {len(members)}",
                        )
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
                pairings_text = extract_text(paths[0], paths[0].suffix.lower(), job_id, deadline=deadline)
                lines_text = extract_text(paths[1], paths[1].suffix.lower(), job_id, deadline=deadline)
            pairings, parser_name = parse_pairings(pairings_text, job_id, "southwest")
            pairings = filter_pairings_for_profile(bind_pairings_to_package(pairings, package_id), {})
            if not pairings:
                raise RuntimeError("No confirmed bidable Southwest pairings were available for recommendation input.")
            update_job(job_id, progress=72, message=f"Matching {len(pairings)} pairings to offered lines")
            scored_pairings = {p["id"]: score_pairing(p, profile) for p in pairings}
            lines = parse_southwest_lines(lines_text, set(scored_pairings))
            if not lines:
                raise RuntimeError("No Southwest lines could be matched to the pairing IDs. Confirm that the correct Pairings and Lines ZIP files were uploaded.")
            lines = [{**line, "package_id": package_id} for line in lines]
            results = [score_southwest_line(line, scored_pairings, profile) for line in lines]
            item_label = "lines"
        else:
            cache_key = parser_cache_key(paths[0], airline)
            cached = load_cached_pairings(cache_key)
            if cached:
                pairings, parser_name = cached
                cache_hit = True
                update_job(
                    job_id,
                    progress=70,
                    message=f"Reusing the parsed {parser_name} bid package ({len(pairings)} pairings)",
                )
            else:
                text = extract_text(
                    paths[0],
                    paths[0].suffix.lower(),
                    job_id,
                    sort_pdf_text=sort_pdf_text_for_airline(airline),
                    deadline=deadline,
                )
                pairings, parser_name = parse_pairings(
                    text,
                    job_id,
                    airline if airline in {"delta", "american", "generic"} else "auto",
                )
                store_cached_pairings(cache_key, airline, parser_name, pairings)
            pairings = bind_pairings_to_package(pairings, package_id)
            if airline == "auto" and pairings:
                detected_airline = airline_for_pairing(pairings[0])
                airline = detected_airline
                update_job(job_id, airline=airline, message=f"Detected {airline.title()} bid package")
            eligible_pairings = filter_pairings_for_profile(pairings, profile)
            if profile.get("bid_fleets") and not eligible_pairings:
                raise RuntimeError("No pairings matched the selected bid fleet. Check the fleet code and run the package again.")
            update_job(job_id, progress=75, message=f"Scoring {len(eligible_pairings)} pairings")
            results = [score_pairing(pairing, profile) for pairing in eligible_pairings]
            item_label = "pairings"
        sort_results(results, package_id)
        package_records(results, package_id)
        synopsis = build_bid_synopsis(pairings)
        diagnostics = {
            "active_package_id": package_id,
            "airline": airline,
            "base": next((item.get("airport") for item in synopsis.get("start_airports", []) if item.get("airport")), None),
            "fleet": next((item.get("fleet") for item in synopsis.get("fleets", []) if item.get("fleet")), None),
            "month": infer_bid_month(get_job(job_id)["filename"]),
            "parsed_candidate_count": len(pairings),
            "accepted_inventory_count": len(filter_pairings_for_profile(pairings, {})),
            "recommendation_input_count": len(eligible_pairings) if airline != "southwest" else len(lines),
            "recommendation_output_count": len(results),
            "eligible_recommendation_count": sum(result.get("eligible") is True for result in results),
            "near_match_count": sum(result.get("eligible") is not True for result in results),
            "result_package_ids": [result.get("package_id") for result in results],
        }
        source = (
            {"kind": "southwest", "package_id": package_id, "pairings": pairings, "lines": lines, "parser_name": parser_name, "synopsis": synopsis, "package_diagnostics": diagnostics}
            if airline == "southwest"
            else {
                "kind": "pairings",
                "package_id": package_id,
                "cache_key": cache_key,
                "parser_name": parser_name,
                "cache_hit": cache_hit,
                "synopsis": synopsis,
                "package_diagnostics": diagnostics,
            }
        )
        update_job(job_id, status="complete", progress=100, message=f"Complete: {len(results)} {item_label} ranked", results_json=json.dumps(results), source_json=json.dumps(source), profile_json=json.dumps(profile))
    except Exception as exc:
        log.exception("Job %s failed", job_id)
        error = (
            "Parsing timed out before the package was complete. Try again or upload a smaller airline package."
            if isinstance(exc, TimeoutError)
            else str(exc)
        )
        update_job(job_id, status="failed", progress=100, error=error, message="Analysis failed")
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
    elif airline in {"delta", "american", "generic", "auto"}:
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
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "The selected file exceeds the 100 MB upload limit.")
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
        conn.execute("INSERT INTO jobs(id,filename,context,status,progress,message,airline,profile_json,uploads_json,package_id) VALUES(?,?,?,?,?,?,?,?,?,?)", (job_id, filenames, context, "queued", 1, "Upload received", airline, json.dumps(profile), json.dumps([str(path) for path in paths]), job_id))
    background_tasks.add_task(process_job, job_id, paths, profile, airline)
    return {"job_id": job_id, "package_id": job_id, "status": "queued", "filename": filenames, "airline": airline, "maximum_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024)}


def infer_bid_month(filename: str) -> str | None:
    months = {
        "JAN": "January", "FEB": "February", "MAR": "March", "APR": "April",
        "MAY": "May", "JUN": "June", "JUL": "July", "AUG": "August",
        "SEP": "September", "OCT": "October", "NOV": "November", "DEC": "December",
    }
    upper = str(filename or "").upper()
    token = next((abbr for abbr in months if re.search(rf"(?:^|[^A-Z]){abbr}(?:[^A-Z]|$)", upper)), None)
    year = re.search(r"\b(20\d{2})\b", upper)
    if not token:
        return year.group(1) if year else None
    return f"{months[token]}{f' {year.group(1)}' if year else ''}"


def job_progress_payload(row: sqlite3.Row) -> dict[str, Any]:
    status = str(row["status"] or "")
    message = str(row["message"] or "")
    lowered = message.lower()
    if status == "complete":
        stage, label = "ready", "Ready"
    elif status == "failed":
        stage, label = "failed", "Failed"
    elif "extracting pdf" in lowered or "reading southwest" in lowered:
        stage, label = "extracting_text", "Extracting text"
    elif "identifying trip records" in lowered or "reusing the parsed" in lowered:
        stage, label = "identifying_records", "Identifying trip records"
    elif "parsing details" in lowered or "matching" in lowered:
        stage, label = "parsing_details", "Parsing details"
    elif "scoring" in lowered:
        stage, label = "building_recommendations", "Building recommendation data"
    else:
        stage, label = "detecting_package", "Detecting airline and package type"

    detail: dict[str, int] = {}
    page = re.search(r"page\s+(\d+)\s+of\s+(\d+)", message, re.IGNORECASE)
    files = re.search(r"file\s+(\d+)\s+of\s+(\d+)", message, re.IGNORECASE)
    if page:
        detail = {"pages_processed": int(page.group(1)), "pages_total": int(page.group(2))}
    elif files:
        detail = {"files_processed": int(files.group(1)), "files_total": int(files.group(2))}

    created = datetime.fromisoformat(str(row["created_at"]).replace(" ", "T"))
    end_value = row["updated_at"] if status in {"complete", "failed"} else datetime.utcnow().isoformat(timespec="seconds")
    ended = datetime.fromisoformat(str(end_value).replace(" ", "T"))
    return {"stage": stage, "stage_label": label, "elapsed_seconds": max(0, int((ended - created).total_seconds())), **detail}


def job_package_metadata(row: sqlite3.Row, results: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any]:
    synopsis = source.get("synopsis") or {}
    starts = synopsis.get("start_airports") or []
    fleets = [str(item.get("fleet")) for item in (synopsis.get("fleets") or []) if item.get("fleet")]
    is_southwest = source.get("kind") == "southwest" or row["airline"] == "southwest"
    return {
        "package_id": row_package_id(row),
        "filename": row["filename"],
        "airline": row["airline"],
        "base": starts[0].get("airport") if starts else None,
        "fleet_category": ", ".join(fleets) if fleets else None,
        "fleets": fleets,
        "bid_month": infer_bid_month(row["filename"] or ""),
        "parsed_count": len(results),
        "record_label": "lines" if is_southwest else get_airline_terminology(row["airline"] or "generic").plural.lower(),
        "package_type": source.get("parser_name") or source.get("kind"),
        "last_parsed_at": row["updated_at"] if row["status"] == "complete" else None,
    }


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    row = get_job(job_id)
    if not row:
        raise HTTPException(404, "Job not found")
    payload = {
        "job_id": row["id"],
        "package_id": row_package_id(row),
        "filename": row["filename"],
        "airline": row["airline"],
        "status": row["status"],
        "progress": row["progress"],
        "message": row["message"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        **job_progress_payload(row),
    }
    if row["status"] == "complete":
        payload["results"] = json.loads(row["results_json"] or "[]")
        source = json.loads(row["source_json"] or "{}")
        if row["package_id"]:
            package_records(payload["results"], row_package_id(row))
        payload["synopsis"] = source.get("synopsis") or build_bid_synopsis(source_pairings(source))
        payload["package"] = job_package_metadata(row, payload["results"], source)
        if os.environ.get("PACKAGE_DEBUG_ENABLED", "false").lower() == "true":
            payload["package_diagnostics"] = source.get("package_diagnostics") or {}
    return payload


@app.post("/api/jobs/{job_id}/rescore")
def rescore_job(job_id: str, profile_json: str = Form(...), package_id: str | None = Form(None)):
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    active_package_id = require_active_package(row, package_id)
    try:
        profile = json.loads(profile_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid preference profile") from exc
    source = json.loads(row["source_json"] or "null")
    if not source:
        raise HTTPException(409, "This analysis was created before preference reruns were available. Upload the bid package one more time.")
    pairings = bind_pairings_to_package(source_pairings(source), active_package_id)
    if source.get("cache_key") and not pairings:
        raise HTTPException(409, "The parsed bid-package cache is unavailable. Upload this bid package one more time.")
    if source.get("kind") == "southwest":
        pairings = filter_pairings_for_profile(pairings, {})
        if not pairings:
            raise HTTPException(409, "No confirmed bidable Southwest pairings are available. Upload the package again.")
        scored_pairings = {pairing["id"]: score_pairing(pairing, profile) for pairing in pairings}
        lines = [{**line, "package_id": active_package_id} for line in source.get("lines") or []]
        results = [score_southwest_line(line, scored_pairings, profile) for line in lines]
    else:
        eligible_pairings = filter_pairings_for_profile(pairings, profile)
        if profile.get("bid_fleets") and not eligible_pairings:
            raise HTTPException(400, "No pairings matched the selected bid fleet. Check the fleet code.")
        results = [score_pairing(pairing, profile) for pairing in eligible_pairings]
    sort_results(results, active_package_id)
    package_records(results, active_package_id)
    update_job(job_id, results_json=json.dumps(results), profile_json=json.dumps(profile), message=f"Preferences updated: {len(results)} recommendations reranked")
    synopsis = source.get("synopsis") or build_bid_synopsis(pairings)
    return {"job_id": job_id, "package_id": active_package_id, "status": "complete", "results": results, "synopsis": synopsis, "message": f"Reranked {len(results)} recommendations without parsing the bid package again"}


@app.post("/api/jobs/{job_id}/navblue-plan")
def navblue_plan(job_id: str, profile: dict[str, Any]):
    if not labs_enabled():
        raise HTTPException(404, "CrewBidIQ Labs is not enabled")
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    active_package_id = require_active_package(row, str(profile.pop("package_id", "")) or None)
    if row["airline"] == "southwest":
        raise HTTPException(400, "Southwest line bidding is not a NAVBLUE/PBS workflow.")
    results = json.loads(row["results_json"] or "[]")
    if row["package_id"]:
        package_records(results, active_package_id)
    stored_profile = json.loads(row["profile_json"] or "{}")
    merged_profile = {**stored_profile, **(profile or {}), "airline": row["airline"]}
    return {**build_navblue_layers(merged_profile, results, row["filename"] or ""), "package_id": active_package_id}


@app.post("/api/jobs/{job_id}/month-plan")
def month_plan(job_id: str, intent: dict[str, Any]):
    if not labs_enabled():
        raise HTTPException(404, "CrewBidIQ Labs is not enabled")
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    active_package_id = require_active_package(row, str(intent.pop("package_id", "")) or None)
    results = json.loads(row["results_json"] or "[]")
    if row["package_id"]:
        package_records(results, active_package_id)
    stored_profile = json.loads(row["profile_json"] or "{}")
    return {**build_month_plan({**stored_profile, **(intent or {})}, results), "package_id": active_package_id}


@app.post("/api/jobs/{job_id}/diagnostic.json")
def result_diagnostic(
    job_id: str,
    pairing_id: str = Form(...),
    category: str = Form(...),
    notes: str = Form(""),
    package_id: str | None = Form(None),
):
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    active_package_id = require_active_package(row, package_id)
    allowed = {"missing_data", "wrong_layover", "wrong_ranking", "wrong_times", "other"}
    if category not in allowed:
        raise HTTPException(400, "Unknown diagnostic category")
    if len(notes) > 2000:
        raise HTTPException(400, "Diagnostic note is too long")

    source = json.loads(row["source_json"] or "{}")
    pairings = bind_pairings_to_package(source_pairings(source), active_package_id)
    if source.get("cache_key") and not pairings:
        raise HTTPException(409, "The parsed bid-package cache is unavailable. Upload this bid package one more time.")
    selected_id = pairing_id.strip().upper()
    selected_index = next((index for index, pairing in enumerate(pairings) if str(pairing.get("id") or "").upper() == selected_id), None)
    target = pairings[selected_index] if selected_index is not None else None
    results = package_records(json.loads(row["results_json"] or "[]"), active_package_id) if row["package_id"] else json.loads(row["results_json"] or "[]")
    result = next((item for item in results if str(item.get("pairing") or "").upper() == selected_id), None)
    if target is None and result is None:
        raise HTTPException(404, "Result not found")

    context: dict[str, Any] = {}
    if selected_index is not None:
        for label, index in (("previous", selected_index - 1), ("selected", selected_index), ("next", selected_index + 1)):
            if 0 <= index < len(pairings):
                pairing = pairings[index]
                context[label] = {"id": pairing.get("id"), "block": pairing.get("block") or ""}

    bundle = {
        "schema": "crewbidiq.parser-diagnostic.v1",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "job": {"airline": row["airline"], "filename": row["filename"], "package_id": active_package_id},
        "report": {"pairing_id": selected_id, "category": category, "notes": notes.strip()},
        "parsed_result": result,
        "source_record": target,
        "neighboring_source_context": context,
        "parser_candidates": (target or {}).get("parser_candidates", []),
        "preferences": json.loads(row["profile_json"] or "{}") if category == "wrong_ranking" else None,
        "privacy": "Contains the selected result and nearby source blocks, not the complete bid package.",
    }
    content = json.dumps(bundle, indent=2, ensure_ascii=False).encode("utf-8")
    safe_id = re.sub(r"[^A-Z0-9_-]", "", selected_id) or "result"
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="crewbidiq-diagnostic-{safe_id}.json"'},
    )


@app.get("/api/jobs/{job_id}/report.pdf")
@app.get("/api/jobs/{job_id}/csv", include_in_schema=False)
def job_report(job_id: str, package_id: str | None = None):
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    active_package_id = require_active_package(row, package_id)
    results = package_records(json.loads(row["results_json"] or "[]"), active_package_id) if row["package_id"] else json.loads(row["results_json"] or "[]")
    pdf = build_bid_report(results, json.loads(row["profile_json"] or "{}"), row["airline"] or row["context"] or "airline", row["filename"])
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="crewbidiq_{job_id}.pdf"'})
