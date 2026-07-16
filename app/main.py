
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
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from app.airlines import airline_terminology_payload, get_airline_terminology
from app.airports import coterminal_group_for_airport, coterminal_payload, expand_airports
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
                source_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for name in ("airline", "profile_json", "uploads_json", "source_json"):
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
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#071525">
  <title>CrewBidIQ</title>
  <link rel="stylesheet" href="/static/app.css?v=0417">
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
      <button id="guideBtn" class="nav-link nav-button"><span>User Guide</span></button>
    </nav>
    <div class="side-footer">CrewBidIQ v0.2.4 test</div>
  </aside>

  <div class="app-main">
    <header class="mobile-header">
      <div class="brand-word">CrewBid<span>IQ</span></div>
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
          <label>Preferred trip lengths<input id="preferredTripLengths" placeholder="2, 3, 4"></label>
          <label>Earliest report<input id="earliestReport" type="time"></label>
          <label>Latest release<input id="latestRelease" type="time"></label>
          <label>Base / co-terminal group<input id="baseAirport" placeholder="ATL or NYC"></label>
          <label id="bidFleetField" class="hidden">American bid fleet<input id="bidFleets" placeholder="320, 737"></label>
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
              <li><strong>Preferred trip lengths:</strong> favors the listed number of duty days, such as 2, 3, or 4.</li>
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
              <li><strong>Allow mid-rotation redeyes:</strong> reduces the general redeye penalty when overnight flying is detected.</li>
              <li><strong>Allow redeye starts, Avoid final redeyes, and Maximum legs after redeye rest:</strong> are saved for planning; phase-specific redeye weighting will become active as airline duty classification expands.</li>
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
            <p><strong>★★★★★ Excellent, ★★★★ Strong, ★★★ Good, ★★ Fair, and ★ Low</strong> summarize how closely each result follows your preferences. The internal score orders the list; the stars are the pilot-facing summary. A required-day conflict always produces Low.</p>
            <p><strong>Why it matched</strong> names the preference signals, workload limits, and conflicts that affected the recommendation so you can understand the rank.</p>
          </article>

          <article class="guide-card">
            <h4>Snapshot and core metrics</h4>
            <ul class="guide-list">
              <li><strong>Top match:</strong> rating of the first recommendation.</li>
              <li><strong>Credit:</strong> airline-provided trip or sequence credit when available.</li>
              <li><strong>TAFB:</strong> total time away from base.</li>
              <li><strong>Trip length:</strong> number of parsed duty periods.</li>
              <li><strong>Recovery:</strong> a quick description of workload around detected redeye flying.</li>
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
            <h4>Conflicts, soft credit, and report</h4>
            <ul class="guide-list">
              <li><strong>Conflicts:</strong> lists required-day, preferred-day, and holiday overlaps. Time and workload mismatches appear under Why it matched.</li>
              <li><strong>Soft credit:</strong> shows airline-specific pay signals when supported. Delta may show EDP, HOL, and SIT; unsupported airline rules display N/A.</li>
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

    <nav class="bottom-nav">
      <a href="#upload"><span>⌂</span>Home</a><a href="#upload"><span>⇧</span>Upload</a><a href="#resultsPanel" class="active"><span>▥</span>Results</a><a href="#preferences"><span>⚙</span>Preferences</a>
    </nav>
  </div>
</div>
<script src="/static/app.js?v=0417"></script>
<script>document.getElementById('mobileGuideBtn').addEventListener('click',()=>document.getElementById('guideBtn').click());</script>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "CrewBidIQ"}


@app.get("/api/airlines/terminology")
def airline_terminology() -> dict[str, dict[str, str]]:
    return airline_terminology_payload()


@app.get("/api/airlines/coterminals")
def airline_coterminals() -> dict[str, dict[str, list[str]]]:
    return coterminal_payload()


def extract_text(path: Path, suffix: str, job_id: str) -> str:
    if suffix == ".pdf":
        doc = fitz.open(path)
        parts = []
        for i, page in enumerate(doc):
            parts.append(f"<<<CREWBIDIQ_PAGE:{i + 1}>>>\n" + page.get_text("text", sort=True))
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
    """Keep the richest record for repeated IDs and retain bounded diagnostics."""
    order: list[str] = []
    candidates: dict[str, list[dict[str, Any]]] = {}
    for pairing in pairings:
        pairing_id = str(pairing.get("id") or "").strip().upper()
        if not pairing_id:
            continue
        if pairing_id not in candidates:
            order.append(pairing_id)
            candidates[pairing_id] = []
        candidates[pairing_id].append(pairing)

    consolidated: list[dict[str, Any]] = []
    for pairing_id in order:
        records = candidates[pairing_id]
        selected = dict(max(records, key=pairing_record_quality))
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
        consolidated.append(selected)
    return consolidated


def parse_pairings(text: str, job_id: str, parser_choice: str = "auto") -> tuple[list[dict[str, Any]], str]:
    update_job(job_id, progress=65, message="Detecting airline format")
    module, parser_name = select_parser(text, parser_choice)
    pairings = consolidate_pairings(module.parse(text))
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
        "delta" if parser_id.startswith("delta") else ("american" if parser_id.startswith("american") else "generic")
    )


def detect_start_airport(pairing: dict[str, Any]) -> str | None:
    for leg in pairing.get("legs", []) or []:
        departure = str(leg.get("departure") or "").strip().upper()
        if departure:
            return departure
    return None


def classify_redeye(pairing: dict[str, Any]) -> str:
    upper = str(pairing.get("block") or "").upper()
    if "REDEYE" in upper:
        return "flagged"
    if len(re.findall(r"\b(?:2[1-3]|0[0-6])\d{2}\b", upper)) >= 2:
        return "possible"
    return "none"


def pairing_duty_count(pairing: dict[str, Any]) -> int:
    duty_labels: list[str] = []
    for leg in pairing.get("legs", []) or []:
        if leg.get("deadhead"):
            continue
        day = str(leg.get("day") or "1")
        if day not in duty_labels:
            duty_labels.append(day)
    return len(duty_labels)


def filter_pairings_for_profile(pairings: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
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
    total = len(pairings)
    complete = sum(bool(pairing.get("legs")) for pairing in pairings)
    redeyes = sum(classify_redeye(pairing) != "none" for pairing in pairings)
    deadheads = sum(any(leg.get("deadhead") for leg in pairing.get("legs", []) or []) for pairing in pairings)
    starts = Counter(filter(None, (detect_start_airport(pairing) for pairing in pairings)))
    lengths = Counter(str(length) for pairing in pairings if (length := pairing_duty_count(pairing)))
    layovers = Counter(city for pairing in pairings for city in detect_layover_cities(pairing))
    fleets = Counter(str(pairing.get("fleet")) for pairing in pairings if pairing.get("fleet"))
    return {
        "total": total,
        "complete": complete,
        "incomplete": total - complete,
        "redeye": {"count": redeyes, "percent": round(redeyes / total * 100, 1) if total else 0.0},
        "deadhead": {"count": deadheads, "percent": round(deadheads / total * 100, 1) if total else 0.0},
        "trip_lengths": _breakdown(lengths, total, "days"),
        "start_airports": _breakdown(starts, total, "airport"),
        "layover_cities": _breakdown(layovers, total, "city")[:10],
        "fleets": _breakdown(fleets, total, "fleet"),
    }


def sort_results(results: list[dict[str, Any]]) -> None:
    results.sort(key=lambda item: (item.get("data_quality") != "incomplete", item.get("score", 0)), reverse=True)


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
    airline = airline_for_pairing(pairing)
    touched_cities = detect_airports(block, pairing)
    cities = detect_layover_cities(pairing)
    dates = list_field(pairing.get("effective")) if pairing.get("effective") else detect_dates(block)
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

    redeye = classify_redeye(pairing)

    if redeye != "none":
        if profile.get("allow_productive_redeye", True):
            score -= 18
        else:
            score -= 55
        reasons.append(f"{redeye} redeye signal")

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

    times = detect_time_values(block)
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
    terminology = get_airline_terminology(airline)
    return {
        "pairing": pairing["id"],
        "score": round(score, 1),
        "dates": dates,
        "cities": cities,
        "touched_cities": touched_cities,
        "start_airport": start_airport,
        "coterminal_group": coterminal_group_for_airport(airline, start_airport),
        "preferred_aircraft": aircraft_hits,
        "equipment_codes": pairing.get("equipment_codes", parsed_equipment),
        "equipment_mapping_status": pairing.get("equipment_mapping_status"),
        "redeye": redeye,
        "deadheads": deadheads,
        "transfers": transfers,
        "calendar_conflicts": calendar_conflicts,
        "reasons": reasons,
        "parser": pairing.get("parser", "generic"),
        "parser_confidence": pairing.get("confidence", 0),
        "data_quality": "incomplete" if data_issues else "complete",
        "data_issues": data_issues,
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
        "display_label": terminology.singular,
        "original_display": block,
        "operations": pairing.get("operations"),
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
        "match_level": match_level(score, conflicts), "display_label": get_airline_terminology("southwest").singular, "original_display": line.get("block", ""),
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
            eligible_pairings = filter_pairings_for_profile(pairings, profile)
            if profile.get("bid_fleets") and not eligible_pairings:
                raise RuntimeError("No pairings matched the selected bid fleet. Check the fleet code and run the package again.")
            update_job(job_id, progress=75, message=f"Scoring {len(eligible_pairings)} pairings")
            results = [score_pairing(pairing, profile) for pairing in eligible_pairings]
            item_label = "pairings"
        sort_results(results)
        synopsis = build_bid_synopsis(pairings)
        source = {"kind": "southwest", "pairings": pairings, "lines": lines, "synopsis": synopsis} if airline == "southwest" else {"kind": "pairings", "pairings": pairings, "synopsis": synopsis}
        update_job(job_id, status="complete", progress=100, message=f"Complete: {len(results)} {item_label} ranked", results_json=json.dumps(results), source_json=json.dumps(source), profile_json=json.dumps(profile))
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
        source = json.loads(row["source_json"] or "{}")
        payload["synopsis"] = source.get("synopsis") or build_bid_synopsis(source.get("pairings") or [])
    return payload


@app.post("/api/jobs/{job_id}/rescore")
def rescore_job(job_id: str, profile_json: str = Form(...)):
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    try:
        profile = json.loads(profile_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid preference profile") from exc
    source = json.loads(row["source_json"] or "null")
    if not source:
        raise HTTPException(409, "This analysis was created before preference reruns were available. Upload the bid package one more time.")
    pairings = source.get("pairings") or []
    if source.get("kind") == "southwest":
        scored_pairings = {pairing["id"]: score_pairing(pairing, profile) for pairing in pairings}
        results = [score_southwest_line(line, scored_pairings) for line in source.get("lines") or []]
    else:
        eligible_pairings = filter_pairings_for_profile(pairings, profile)
        if profile.get("bid_fleets") and not eligible_pairings:
            raise HTTPException(400, "No pairings matched the selected bid fleet. Check the fleet code.")
        results = [score_pairing(pairing, profile) for pairing in eligible_pairings]
    sort_results(results)
    update_job(job_id, results_json=json.dumps(results), profile_json=json.dumps(profile), message=f"Preferences updated: {len(results)} recommendations reranked")
    synopsis = source.get("synopsis") or build_bid_synopsis(pairings)
    return {"job_id": job_id, "status": "complete", "results": results, "synopsis": synopsis, "message": f"Reranked {len(results)} recommendations without parsing the bid package again"}


@app.post("/api/jobs/{job_id}/diagnostic.json")
def result_diagnostic(
    job_id: str,
    pairing_id: str = Form(...),
    category: str = Form(...),
    notes: str = Form(""),
):
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    allowed = {"missing_data", "wrong_layover", "wrong_ranking", "wrong_times", "other"}
    if category not in allowed:
        raise HTTPException(400, "Unknown diagnostic category")
    if len(notes) > 2000:
        raise HTTPException(400, "Diagnostic note is too long")

    source = json.loads(row["source_json"] or "{}")
    pairings = source.get("pairings") or []
    selected_id = pairing_id.strip().upper()
    selected_index = next((index for index, pairing in enumerate(pairings) if str(pairing.get("id") or "").upper() == selected_id), None)
    target = pairings[selected_index] if selected_index is not None else None
    results = json.loads(row["results_json"] or "[]")
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
        "job": {"airline": row["airline"], "filename": row["filename"]},
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
def job_report(job_id: str):
    row = get_job(job_id)
    if not row or row["status"] != "complete":
        raise HTTPException(404, "Completed analysis not found")
    results = json.loads(row["results_json"] or "[]")
    pdf = build_bid_report(results, json.loads(row["profile_json"] or "{}"), row["airline"] or row["context"] or "airline", row["filename"])
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="crewbidiq_{job_id}.pdf"'})
